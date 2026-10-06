"""Fetching one page of mail for one address (SPEC §5.1 step 3, §6.4). V1.1 step 6a.

The caller holds the address's check lease. A page is at most 30 messages or 20 seconds
(OD-026). The first run starts from now (`start_from: now`): it records INBOX's position and
fetches nothing. Messages are read with PEEK, parsed in memory (never written to disk), and
created as items at `new` with their excerpts. The cursor advances after each message and is
written only while the lease's fencing token is still current.

Crash safety (§5.1): a marker is written before each message is read and removed with its item.
A marker that is still there after two crashes quarantines the message: an item with
`quarantined` and `content_unscanned` set and nothing parsed, so one crafted email can't keep
tripping the crash-loop breaker.

Messages over the size limit, or over 16 MB, are deferred in `deferred_uids` and read after the
rest of the page; nothing is ever skipped. A UIDVALIDITY change (a mailbox reset) is recovered
here: known messages are re-pointed and the rest fetched again, deduplicated by `stable_id`
(§6.4). `backfill_page` reads older mail for `ecf backfill`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import tracemalloc
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ecf.errors import ConflictError, MailUnavailableError
from ecf.ids import AddressId, StableId
from ecf.status import OPEN, Status
from ecf_server import download_budget, items, keywords, leases, own_mail, probe, triggers
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.isolate import Isolator
from ecf_server.log_bridge import log
from ecf_server.mail import MailSource, MessageMeta
from ecf_server.message import (
    ACTOR_CHARS,
    CLASSIFIER_CHARS,
    ParsedMessage,
    identity_digest,
    parse,
    parse_partial,
    stable_id,
)
from ecf_server.outbound_msg import message_ids
from ecf_server.senderauth import AuthOutcome
from ecf_server.state_machine import TransitionContext

PAGE_MESSAGES = 30
PAGE_S = 20.0
MAX_PARTIAL_TEXTS = 20  # text parts fetched from an oversized message (one IMAP fetch each)
LARGE_BYTES = 16 * 1024 * 1024  # fixed threshold: processed after smaller mail (§5.1)
QUARANTINE_AFTER = 2  # crashes on the same message
SUBJECT_MAX = 500  # kept on the item for its card (metadata; cards cap fields at 2,000)
SENDER_MAX = 320
MAX_PER_CHECK_S = 360.0  # max_per_check default: a check's IMAP and rules work (§5.2)
DEFAULT_RATE = 2 * 1024 * 1024  # bytes/s assumed until a page has measured throughput
HEADER_LIMIT = 256 * 1024  # header block fetched for an oversized message
LARGE_LOCK = threading.Lock()  # large messages are processed one at a time service-wide
MB = 1024 * 1024
DEFAULT_MAX = {"high": 64 * MB, "standard": 16 * MB}  # OD-024 (64 MB kept, OD-195)
DEFAULT_SCAN = 10 * MB  # OD-027


class Analyzer(Protocol):
    """Analysis plugged into fetching (steps 7 to 9): `analyze` reads and returns facts;
    `record` runs inside the item's transaction (sender history)."""

    def analyze(
        self,
        parsed: ParsedMessage,
        raw: bytes,
        auth: AuthOutcome | None = None,
        gmail_labels: frozenset[str] | None = None,
        /,
    ) -> dict[str, Any]: ...
    def record(
        self, conn: sqlite3.Connection, parsed: ParsedMessage, facts: dict[str, Any], /
    ) -> None: ...


class LeaseLostError(ConflictError):
    """The check lease expired or was taken; this holder must stop writing."""


@dataclass
class Cursor:
    uidvalidity: int | None
    last_uid: int
    deferred: list[int]
    version: int
    recovering_until: int = 0  # UIDs up to this are a re-fetch after a mailbox reset (§6.4)
    deferred_since: str | None = None  # earliest INTERNALDATE among `deferred` (ISO), for §6.4


@dataclass(frozen=True)
class AddressConfig:
    address_id: str
    sensitivity: str
    max_message_bytes: int
    max_scan_bytes: int
    gmail: bool = False  # Gmail mode, from the last probe (OD-438)


@dataclass
class PageResult:
    first_run: bool = False
    reset_detected: bool = False
    created: list[str] = field(default_factory=list[str])
    duplicates: int = 0
    quarantined: list[int] = field(default_factory=list[int])
    relocated: int = 0  # known messages re-pointed after a mailbox reset
    deferred: list[int] = field(default_factory=list[int])  # still waiting after this check
    large_done: list[int] = field(default_factory=list[int])  # deferred messages handled now
    remaining: int = 0  # new UIDs not reached in this page
    stopped: str = "done"  # done | page_limit | time | lease_lost | download_budget (Gmail)
    own_skipped: int = 0  # ecf's own mail come back (own_mail; V1.5)
    second_install: bool = False  # mail from another install on this mailbox: address paused
    restored_keywords: bool = False  # ecf's labels on new mail after a restore (OD-372): paused


def address_config(conn: sqlite3.Connection, address_id: str) -> AddressConfig:
    row = conn.execute(
        "SELECT sensitivity, overrides FROM addresses WHERE address_id = ?", (address_id,)
    ).fetchone()
    overrides: dict[str, Any] = json.loads(row["overrides"])
    return AddressConfig(
        address_id=address_id,
        sensitivity=row["sensitivity"],
        max_message_bytes=probe.cap_to_provider(
            conn,
            address_id,
            int(overrides.get("max_message_bytes", DEFAULT_MAX[row["sensitivity"]])),
        ),
        max_scan_bytes=int(overrides.get("max_scan_bytes_per_part", DEFAULT_SCAN)),
        gmail=probe.is_gmail(conn, address_id),
    )


def load_cursor(conn: sqlite3.Connection, address_id: str) -> Cursor | None:
    row = conn.execute("SELECT * FROM cursors WHERE address_id = ?", (address_id,)).fetchone()
    if row is None:
        return None
    return Cursor(
        row["uidvalidity"],
        row["last_uid"],
        json.loads(row["deferred_uids"]),
        row["version"],
        row["recovering_until_uid"],
        row["deferred_since"],
    )


def save_cursor(conn: sqlite3.Connection, clock: Clock, lease: leases.Lease, cur: Cursor) -> Cursor:
    """Write the cursor if its version is unchanged and the lease is still held. The lease check
    and the write share one transaction, which holds SQLite's write lock throughout."""
    deferred = json.dumps(sorted(set(cur.deferred)))
    since = cur.deferred_since if cur.deferred else None
    with write_tx(conn):
        if not leases.held(conn, clock, lease):
            raise LeaseLostError(f"lease on {lease.address_id} lost; cursor not saved")
        if cur.version == 0 and load_cursor(conn, lease.address_id) is None:
            conn.execute(
                "INSERT INTO cursors (address_id, uidvalidity, last_uid, deferred_uids, version,"
                " recovering_until_uid, deferred_since) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (
                    lease.address_id,
                    cur.uidvalidity,
                    cur.last_uid,
                    deferred,
                    cur.recovering_until,
                    since,
                ),
            )
        elif (
            conn.execute(
                "UPDATE cursors SET uidvalidity = ?, last_uid = ?, deferred_uids = ?,"
                " recovering_until_uid = ?, deferred_since = ?, version = version + 1"
                " WHERE address_id = ? AND version = ?",
                (
                    cur.uidvalidity,
                    cur.last_uid,
                    deferred,
                    cur.recovering_until,
                    since,
                    lease.address_id,
                    cur.version,
                ),
            ).rowcount
            != 1
        ):
            raise LeaseLostError(f"cursor for {lease.address_id} moved; not saved")
    return Cursor(
        cur.uidvalidity,
        cur.last_uid,
        sorted(set(cur.deferred)),
        cur.version + 1,
        cur.recovering_until,
        since,
    )


def fetch_page(  # noqa: PLR0913 - keyword-only options after the five collaborators
    conn: sqlite3.Connection,
    clock: Clock,
    src: MailSource,
    cfg: AddressConfig,
    lease: leases.Lease,
    *,
    lost: threading.Event | None = None,
    analyzer: Analyzer | None = None,
    deadline: float | None = None,
    isolator: Isolator | None = None,
    install: str = "",
) -> PageResult:
    """One page of new mail, then deferred large mail while `deadline` (monotonic; default the
    check budget from now) leaves time for it. With `install`, ecf's keywords on the page are
    read (one request) and sorted (keywords.py)."""
    result = PageResult()
    state = src.inbox()
    cur = load_cursor(conn, cfg.address_id)
    if cur is None:  # first run: start from now
        save_cursor(conn, clock, lease, Cursor(state.uidvalidity, state.uidnext - 1, [], 0))
        result.first_run = True
        return result
    if cur.uidvalidity != state.uidvalidity:
        result.reset_detected = True
        cur, result.relocated = _start_recovery(
            conn, clock, src, lease, cur, state.uidvalidity, state.uidnext
        )

    new = src.uids_after(cur.last_uid)
    page = new[:PAGE_MESSAGES]
    metas = src.meta(page)
    pg = _Page(conn, clock, src, cfg, lease, state.uidvalidity, result, analyzer, isolator)
    pg.recovering_until = cur.recovering_until
    pg.install = install
    pg.budget_left = download_budget.left(conn, clock, cfg.address_id, gmail=cfg.gmail)
    if install and page:
        pg.flags = src.flags(page)
    if cfg.gmail and page:  # Gmail's labels, for its own mail to itself (`self_sent`, OD-446)
        pg.labels = src.gmail_labels(page)
    started = clock.monotonic()
    end = deadline if deadline is not None else started + MAX_PER_CHECK_S
    done = 0
    for uid in page:
        if lost is not None and lost.is_set():
            result.stopped = "lease_lost"
            break
        if clock.monotonic() - started > PAGE_S:
            result.stopped = "time"
            break
        meta = metas.get(uid)
        if meta is not None and (meta.size > cfg.max_message_bytes or meta.size > LARGE_BYTES):
            cur.deferred.append(uid)
            _note_deferred(cur, meta.internaldate)
        elif meta is not None and not _within_budget(pg, uid, meta):
            break  # Gmail's download budget: this message and the rest wait for a later check
        cur.last_uid = uid  # vanished messages (no meta) are simply passed
        if cur.recovering_until and cur.last_uid >= cur.recovering_until:
            cur.recovering_until = 0  # recovery done
        cur = save_cursor(conn, clock, lease, cur)
        done += 1
    if result.stopped == "done" and len(new) > len(page):
        result.stopped = "page_limit"
    result.remaining = len(new) - done
    if result.stopped not in ("lease_lost", "download_budget") and cur.deferred:
        cur = _deferred(pg, cur, end, lost)
    result.deferred = list(cur.deferred)
    return result


@dataclass
class Backfill:
    """Progress of `ecf backfill` for one address (OD-216): mail that arrived since `since`, up to
    the cursor's position when it started (`end_uid`); `after_uid` is the last UID handled."""

    since: str  # ISO date
    act: bool
    uidvalidity: int
    end_uid: int
    after_uid: int = 0

    def to_json(self) -> dict[str, Any]:
        return {"since": self.since, "act": self.act, "uidvalidity": self.uidvalidity,
                "end_uid": self.end_uid, "after_uid": self.after_uid}  # fmt: skip


def backfill_page(  # noqa: PLR0913 - keyword-only options after the six collaborators
    conn: sqlite3.Connection,
    clock: Clock,
    src: MailSource,
    cfg: AddressConfig,
    lease: leases.Lease,
    bf: Backfill,
    *,
    lost: threading.Event | None = None,
    analyzer: Analyzer | None = None,
    deadline: float | None = None,
    isolator: Isolator | None = None,
) -> PageResult:
    """One page of older mail, oldest first, within the same limits as a check (30 messages or
    20 s, the check budget). `bf.after_uid` advances past each message handled; the caller saves
    it. Mail ecf already has (by UID) and mail waiting as deferred is left alone. A mailbox reset
    ends the backfill (`stopped = "reset"`)."""
    result = PageResult()
    state = src.inbox()
    if state.uidvalidity != bf.uidvalidity:
        result.stopped = "reset"
        return result
    cur = load_cursor(conn, cfg.address_id)
    waiting = set(cur.deferred) if cur else set[int]()
    known = {
        r[0]
        for r in conn.execute(
            "SELECT json_extract(locator, '$.uid') FROM items WHERE address_id = ?"
            " AND json_extract(locator, '$.uidvalidity') = ?",
            (cfg.address_id, bf.uidvalidity),
        )
    }
    since = datetime.fromisoformat(bf.since).replace(tzinfo=UTC)
    todo = [
        u for u in src.uids_since(since)
        if bf.after_uid < u <= bf.end_uid and u not in known and u not in waiting
    ]  # fmt: skip
    page = todo[:PAGE_MESSAGES]
    metas = src.meta(page)
    pg = _Page(conn, clock, src, cfg, lease, bf.uidvalidity, result, analyzer, isolator)
    pg.budget_left = download_budget.left(conn, clock, cfg.address_id, gmail=cfg.gmail)
    started = clock.monotonic()
    end = deadline if deadline is not None else started + MAX_PER_CHECK_S
    done = 0
    for uid in page:
        if lost is not None and lost.is_set():
            result.stopped = "lease_lost"
            break
        if clock.monotonic() - started > PAGE_S or clock.monotonic() > end:
            result.stopped = "time"
            break
        meta = metas.get(uid)
        if meta is not None and (meta.size > cfg.max_message_bytes or meta.size > LARGE_BYTES):
            oversized = meta.size > cfg.max_message_bytes
            need = HEADER_LIMIT + cfg.max_scan_bytes if oversized else meta.size
            if (pg.over_budget(need) or clock.monotonic() + need / pg.rate() > end
                    or not LARGE_LOCK.acquire(blocking=False)):  # fmt: skip
                # the next backfill job picks it up
                result.stopped = "download_budget" if pg.budget_spent else "time"
                break
            try:
                if oversized:
                    _process_partial(pg, uid, meta.size, meta.internaldate)
                else:
                    _process_large(pg, uid, meta.internaldate)
            finally:
                LARGE_LOCK.release()
        elif meta is not None and not _within_budget(pg, uid, meta):
            break
        bf.after_uid = uid  # vanished messages (no meta) are simply passed
        done += 1
    if result.stopped == "done" and len(todo) > len(page):
        result.stopped = "page_limit"
    result.remaining = len(todo) - done
    return result


def _deferred(pg: _Page, cur: Cursor, end: float, lost: threading.Event | None) -> Cursor:
    """Large and oversized mail after the page (§5.1, OD-030): oldest first, one at a time across
    the service, only when the estimate from size and measured throughput fits the budget."""
    for uid in sorted(cur.deferred):
        if lost is not None and lost.is_set():
            break
        meta = pg.src.meta([uid]).get(uid)
        if meta is None:  # gone from INBOX: nothing left to process
            cur.deferred.remove(uid)
            cur = save_cursor(pg.conn, pg.clock, pg.lease, cur)
            continue
        oversized = meta.size > pg.cfg.max_message_bytes
        need = HEADER_LIMIT + pg.cfg.max_scan_bytes if oversized else meta.size
        if pg.clock.monotonic() + need / pg.rate() > end or pg.over_budget(need):
            break  # next check
        if not LARGE_LOCK.acquire(blocking=False):
            break  # another address is processing a large message
        try:
            if oversized:
                _process_partial(pg, uid, meta.size, meta.internaldate)
            else:
                _process_large(pg, uid, meta.internaldate)
        finally:
            LARGE_LOCK.release()
        cur.deferred.remove(uid)
        cur = save_cursor(pg.conn, pg.clock, pg.lease, cur)
        pg.result.large_done.append(uid)
    return cur


@dataclass
class _Page:
    conn: sqlite3.Connection
    clock: Clock
    src: MailSource
    cfg: AddressConfig
    lease: leases.Lease
    uidvalidity: int
    result: PageResult
    analyzer: Analyzer | None
    isolator: Isolator | None = None  # messages parsed and verified in a child (OD-195, OD-204)
    fetched_bytes: int = 0
    fetch_s: float = 0.0
    recovering_until: int = 0
    install: str = ""  # this install's name, for its keywords (V1.5 step 10b)
    budget_left: int | None = None  # Gmail's download budget still free (OD-440); None: none
    budget_spent: bool = False
    flags: dict[int, frozenset[str]] = field(default_factory=dict[int, frozenset[str]])
    labels: dict[int, frozenset[str]] = field(default_factory=dict[int, frozenset[str]])

    def over_budget(self, need: int) -> bool:
        """Fetching `need` more bytes would pass Gmail's download budget: stop (OD-440)."""
        if self.budget_left is None or need <= self.budget_left:
            return False
        self.result.stopped, self.budget_spent = "download_budget", True
        return True

    def downloaded(self, nbytes: int) -> None:
        if self.budget_left is not None:
            download_budget.record(self.conn, self.clock, self.cfg.address_id, nbytes)
            self.budget_left -= nbytes

    def rate(self) -> float:
        """Measured fetch throughput in bytes/s, or DEFAULT_RATE before there is enough data."""
        if self.fetch_s < 0.5 or self.fetched_bytes == 0:
            return DEFAULT_RATE
        return self.fetched_bytes / self.fetch_s


def _within_budget(pg: _Page, uid: int, meta: MessageMeta) -> bool:
    """Process the message unless it would pass Gmail's download budget (OD-440)."""
    if pg.over_budget(meta.size):
        return False
    _process(pg, uid, meta.internaldate)
    return True


def _process(pg: _Page, uid: int, internaldate: datetime | None = None) -> None:
    conn, clock, cfg, lease, uv = pg.conn, pg.clock, pg.cfg, pg.lease, pg.uidvalidity
    attempts = _mark(conn, clock, cfg.address_id, uv, uid)
    if attempts > QUARANTINE_AFTER:
        _quarantine(conn, clock, lease, uv, uid)
        pg.result.quarantined.append(uid)
        return
    try:
        t0 = clock.monotonic()
        raw = pg.src.fetch(uid)
        if raw is None:  # gone since the search
            _unmark(conn, cfg.address_id, uv, uid)
            return
        pg.fetched_bytes += len(raw)
        pg.fetch_s += clock.monotonic() - t0
        pg.downloaded(len(raw))
        if pg.isolator is not None:  # every message, with a time limit (OD-204)
            parsed, auth = pg.isolator(raw, cfg.max_scan_bytes)
            _store(pg, uid, parsed, raw, {}, internaldate, auth)
        else:
            _store(pg, uid, parse(raw, max_scan_bytes=cfg.max_scan_bytes), raw, {}, internaldate)
    except (LeaseLostError, MailUnavailableError):  # not the message's fault
        _give_back(conn, cfg.address_id, uv, uid)
        raise


def _process_large(pg: _Page, uid: int, internaldate: datetime | None = None) -> None:
    """A message over 16 MB but within the limit: read whole, then parsed and verified in a child
    process when the service gives an isolator (OD-195); this process's peak memory is logged."""
    tracing = not tracemalloc.is_tracing()
    if tracing:
        tracemalloc.start()
    tracemalloc.reset_peak()
    try:
        _process(pg, uid, internaldate)
    finally:
        peak = tracemalloc.get_traced_memory()[1]
        if tracing:
            tracemalloc.stop()
    log.info("fetch.large_message", address_id=pg.cfg.address_id, uid=uid, peak_bytes=peak)


def _process_partial(pg: _Page, uid: int, size: int, internaldate: datetime | None = None) -> None:
    """A message over the address's limit: headers and the start of each text part only;
    unscanned beyond that, and never a DKIM pass (§5.1, §7.2)."""
    conn, clock, cfg, lease, uv = pg.conn, pg.clock, pg.cfg, pg.lease, pg.uidvalidity
    if _mark(conn, clock, cfg.address_id, uv, uid) > QUARANTINE_AFTER:
        _quarantine(conn, clock, lease, uv, uid)
        pg.result.quarantined.append(uid)
        return
    try:
        parts = pg.src.structure(uid)
        header = pg.src.fetch_part(uid, "HEADER", HEADER_LIMIT)
        if parts is None or header is None:
            _unmark(conn, cfg.address_id, uv, uid)
            return
        texts: dict[str, bytes] = {}
        for p in [p for p in parts if p.content_type.startswith("text/")][:MAX_PARTIAL_TEXTS]:
            texts[p.section] = pg.src.fetch_part(uid, p.section, cfg.max_scan_bytes) or b""
        pg.downloaded(len(header) + sum(len(t) for t in texts.values()))
        parsed = parse_partial(header, parts, texts, size=size, max_scan_bytes=cfg.max_scan_bytes)
        _store(
            pg, uid, parsed, header, {"oversized": True, "content_unscanned": True}, internaldate
        )
    except (LeaseLostError, MailUnavailableError):  # not the message's fault
        _give_back(conn, cfg.address_id, uv, uid)
        raise


def _store(
    pg: _Page,
    uid: int,
    parsed: ParsedMessage,
    raw: bytes,
    extra: dict[str, Any],
    internaldate: datetime | None = None,
    auth: AuthOutcome | None = None,
) -> None:
    """Create the item for a read message, re-point a known one after a mailbox reset, or record
    a repeat delivery (§6.3, §6.4)."""
    conn, clock, cfg, lease, uv = pg.conn, pg.clock, pg.cfg, pg.lease, pg.uidvalidity
    sid = stable_id(cfg.address_id, parsed.message_id, parsed.content_hash, uv, uid)
    when = internaldate.isoformat() if internaldate else None
    if pg.recovering_until and uid <= pg.recovering_until:
        known = _known_after_reset(conn, cfg.address_id, sid, parsed, when)
        if known is not None:
            if _relocate(conn, clock, lease, known, uid, uv, when):
                pg.result.relocated += 1
            _unmark(conn, cfg.address_id, uv, uid)
            return
    digest = identity_digest(parsed)
    changed = _identity_changed(conn, sid, digest)
    if changed:  # same body and Message-ID, different sender or subject: a new item, analyzed
        sid = stable_id(
            cfg.address_id, parsed.message_id, f"{parsed.content_hash}|{digest}", uv, uid
        )
    if conn.execute("SELECT 1 FROM items WHERE stable_id = ?", (sid,)).fetchone():
        with write_tx(conn):  # the same message delivered again: recorded, not re-actioned
            _fence(conn, clock, lease)
            conn.execute(
                "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
                " VALUES (?, ?, ?, 'item.duplicate_delivery', 'service', 'ok', ?)",
                (to_ts(clock.now()), cfg.address_id, sid, json.dumps({"uid": uid})),
            )
            _unmark_in(conn, cfg.address_id, uv, uid)
        pg.result.duplicates += 1
        return
    labels = None
    if pg.cfg.gmail:
        if uid not in pg.labels:  # deferred large mail: read here
            pg.labels.update(pg.src.gmail_labels([uid]))
        labels = pg.labels.get(uid, frozenset())
    found = pg.analyzer.analyze(parsed, raw, auth, labels) if pg.analyzer else {}
    facts = _facts(parsed) | extra | found
    facts["identity_digest"] = digest
    if facts.get("ecf_mail") == own_mail.OWN:  # ecf's own mail came back: no item (§8.4)
        with write_tx(conn):
            _fence(conn, clock, lease)
            conn.execute(
                "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
                " VALUES (?, ?, 'mail.own_skipped', 'service', 'ok', ?)",
                (to_ts(clock.now()), cfg.address_id, json.dumps({"uid": uid})),
            )
            _unmark_in(conn, cfg.address_id, uv, uid)
        pg.result.own_skipped += 1
        return
    if facts.get("ecf_mail") == own_mail.SECOND:
        pg.result.second_install |= _pause_second_install(conn, clock, cfg.address_id)
    _keywords(pg, uid, facts)
    reused = message_id_reused(conn, cfg.address_id, parsed.message_id, parsed.content_hash, digest)

    def also(c: sqlite3.Connection) -> None:
        _fence(c, clock, lease)
        c.execute(
            "INSERT INTO excerpts (stable_id, classifier_text, actor_text) VALUES (?, ?, ?)",
            (
                sid,
                parsed.excerpt(CLASSIFIER_CHARS, triggers.redact_injection),
                parsed.excerpt(ACTOR_CHARS, triggers.redact_injection),
            ),
        )
        if pg.analyzer is not None:
            pg.analyzer.record(c, parsed, facts)
        _unmark_in(c, cfg.address_id, uv, uid)

    items.create_item(
        conn,
        clock,
        stable_id=StableId(sid),
        address_id=AddressId(cfg.address_id),
        content_hash=parsed.content_hash,
        also=also,
        uid=uid,
        uidvalidity=uv,
        message_id=parsed.message_id,
        hash_version=parsed.hash_version,
        duplicate_message_id=int(reused),
        facts=json.dumps(facts, sort_keys=True),
        subject=parsed.subject[:SUBJECT_MAX],
        sender=(parsed.from_addr or "")[:SENDER_MAX] or None,
        sender_name=parsed.from_name[:SENDER_MAX] or None,
        locator=json.dumps(
            {"uid": uid, "uidvalidity": uv, "message_id": parsed.message_id, "internaldate": when}
        ),
    )
    pg.result.created.append(sid)


def _start_recovery(
    conn: sqlite3.Connection,
    clock: Clock,
    src: MailSource,
    lease: leases.Lease,
    cur: Cursor,
    uidvalidity: int,
    uidnext: int,
) -> tuple[Cursor, int]:
    """The mailbox reset its UIDs (§6.4): re-fetch from the day before the last message already
    read or the earliest deferred one, re-point open items by Message-ID, and audit it. Known
    messages are recognized while re-fetching."""
    aid = lease.address_id
    since = _recovery_since(conn, aid, cur)
    uids = src.uids_since(since - timedelta(days=1)) if since else []
    start = min(uids) - 1 if uids else uidnext - 1
    relocated = 0
    for row in conn.execute(
        "SELECT stable_id, message_id, locator FROM items WHERE address_id = ?"
        " AND message_id IS NOT NULL AND status IN (SELECT value FROM json_each(?))",
        (aid, json.dumps(sorted(OPEN))),
    ).fetchall():
        loc: dict[str, Any] = json.loads(row["locator"])
        if loc.get("uidvalidity") == uidvalidity:
            continue
        found = src.find_message_id(row["message_id"])
        if found and _relocate(
            conn, clock, lease, row["stable_id"], found[0], uidvalidity, loc.get("internaldate")
        ):
            relocated += 1
    data = {
        "old_uidvalidity": cur.uidvalidity,
        "new_uidvalidity": uidvalidity,
        "refetch": len(uids),
        "open_items_relocated": relocated,
    }
    with write_tx(conn):
        _fence(conn, clock, lease)
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, 'mailbox.reset', 'service', 'ok', ?)",
            (to_ts(clock.now()), aid, json.dumps(data)),
        )
    fresh = Cursor(uidvalidity, start, [], cur.version, max(uids) if uids else 0)
    return save_cursor(conn, clock, lease, fresh), relocated


def _note_deferred(cur: Cursor, arrived: datetime | None) -> None:
    if arrived is None:
        return
    utc = arrived.astimezone(UTC)
    if cur.deferred_since is None or utc < datetime.fromisoformat(cur.deferred_since):
        cur.deferred_since = utc.isoformat()


def _recovery_since(conn: sqlite3.Connection, address_id: str, cur: Cursor) -> datetime | None:
    """How far back to re-read after a reset: the arrival time of the last message read under the
    old UIDVALIDITY, or of the earliest deferred message if that is older. Arrival, not the time
    ecf read it: a backlog read days late must still be found (V1.1 review, 2026-09-29)."""
    row = conn.execute(
        "SELECT json_extract(locator, '$.internaldate') AS arrived, created_at FROM items"
        " WHERE address_id = ? AND json_extract(locator, '$.uidvalidity') = ?"
        " ORDER BY json_extract(locator, '$.uid') DESC LIMIT 1",
        (address_id, cur.uidvalidity),
    ).fetchone()
    found: list[datetime] = []
    if row is not None:
        found.append(
            datetime.fromisoformat(row["arrived"]) if row["arrived"] else from_ts(row["created_at"])
        )
    if cur.deferred_since:
        found.append(datetime.fromisoformat(cur.deferred_since))
    return min(found) if found else None


def _known_after_reset(
    conn: sqlite3.Connection,
    address_id: str,
    sid: str,
    parsed: ParsedMessage,
    when: str | None,
) -> str | None:
    """The item a re-fetched message already has: by stable_id, or without a Message-ID by
    content hash and arrival date (§6.4)."""
    if conn.execute("SELECT 1 FROM items WHERE stable_id = ?", (sid,)).fetchone():
        return sid
    if parsed.message_id is not None or when is None:
        return None
    row = conn.execute(
        "SELECT stable_id FROM items WHERE address_id = ? AND message_id IS NULL"
        " AND content_hash = ? AND json_extract(locator, '$.internaldate') = ?",
        (address_id, parsed.content_hash, when),
    ).fetchone()
    return None if row is None else str(row["stable_id"])


def _relocate(
    conn: sqlite3.Connection,
    clock: Clock,
    lease: leases.Lease,
    stable: str,
    uid: int,
    uidvalidity: int,
    when: str | None,
) -> bool:
    """Point an item at its message's new UID; False if it already pointed there."""
    with write_tx(conn):
        _fence(conn, clock, lease)
        loc: dict[str, Any] = json.loads(
            conn.execute("SELECT locator FROM items WHERE stable_id = ?", (stable,)).fetchone()[0]
        )
        if loc.get("uid") == uid and loc.get("uidvalidity") == uidvalidity:
            return False
        loc |= {"uid": uid, "uidvalidity": uidvalidity}
        if when is not None:
            loc["internaldate"] = when
        conn.execute(
            "UPDATE items SET locator = ?, updated_at = ? WHERE stable_id = ?",
            (json.dumps(loc), to_ts(clock.now()), stable),
        )
    return True


def close_gone(
    conn: sqlite3.Connection,
    clock: Clock,
    src: MailSource,
    lease: leases.Lease,
    uidvalidity: int,
    *,
    close_stale: bool = False,
) -> int:
    """Close open items whose message has left INBOX (moved, archived or deleted by a person) as
    `resolved_by_mailbox` (§6.2). With `close_stale` (the caller knows recovery after a reset is
    done), items still pointing at another UIDVALIDITY weren't found again, so their message is
    gone too (V1.1 review). Returns how many."""
    rows = conn.execute(
        "SELECT stable_id, locator FROM items WHERE address_id = ?"
        " AND status IN (SELECT value FROM json_each(?))",
        (lease.address_id, json.dumps(sorted(OPEN))),
    ).fetchall()
    by_uid: dict[int, str] = {}
    stale: list[str] = []
    for r in rows:
        loc: dict[str, Any] = json.loads(r["locator"])
        if loc.get("uidvalidity") == uidvalidity and "uid" in loc:
            by_uid[int(loc["uid"])] = r["stable_id"]
        elif close_stale and loc.get("uidvalidity") is not None:
            stale.append(r["stable_id"])
    present = src.existing(by_uid) if by_uid else set[int]()
    gone = [sid for uid, sid in by_uid.items() if uid not in present] + stale
    closed = 0
    for sid in gone:
        if not leases.held(conn, clock, lease):
            raise LeaseLostError(f"lease on {lease.address_id} lost")
        items.transition(
            conn,
            clock,
            StableId(sid),
            Status.RESOLVED_BY_MAILBOX,
            TransitionContext(),
            actor="service",
        )
        closed += 1
    return closed


def _identity_changed(conn: sqlite3.Connection, sid: str, digest: str) -> bool:
    """An item with this stable ID exists and was read with other identity headers. Items made
    before the digest was recorded count as unchanged."""
    row = conn.execute(
        "SELECT json_extract(facts, '$.identity_digest') AS d FROM items WHERE stable_id = ?",
        (sid,),
    ).fetchone()
    return row is not None and row["d"] is not None and row["d"] != digest


def message_id_reused(
    conn: sqlite3.Connection,
    address_id: str,
    message_id: str | None,
    content_hash: str,
    digest: str,
) -> bool:
    """Another item has this Message-ID with different content or identity headers (trigger 5,
    §6.3)."""
    if message_id is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM items WHERE address_id = ? AND message_id = ? AND (content_hash != ?"
        " OR coalesce(json_extract(facts, '$.identity_digest'), ?) != ?)",
        (address_id, message_id, content_hash, digest, digest),
    ).fetchone()
    return row is not None


def _keywords(pg: _Page, uid: int, facts: dict[str, Any]) -> None:
    """ecf's keywords on the message (keywords.py; §13.6, OD-372)."""
    if not pg.install:
        return
    if uid not in pg.flags:  # deferred large mail: read here
        pg.flags.update(pg.src.flags([uid]))
    mine, others = keywords.sort(pg.flags.get(uid, frozenset()), pg.install)
    aid = pg.cfg.address_id
    if others:
        _pause(pg.conn, pg.clock, aid, "second_install")
        pg.result.second_install = True
    if mine:
        facts["ecf_keywords"] = mine
        if keywords.in_restore_window(pg.conn, pg.clock, aid, pg.uidvalidity, uid):
            _pause(pg.conn, pg.clock, aid, "restored_keywords")
            pg.result.restored_keywords = True


def _pause_second_install(conn: sqlite3.Connection, clock: Clock, address_id: str) -> bool:
    """Pause the address (as `ecf pause` does) when mail from another install arrives (§13.6);
    True if it wasn't paused yet. The check's report raises Operator Input Needed."""
    return _pause(conn, clock, address_id, "second_install")


def _pause(conn: sqlite3.Connection, clock: Clock, address_id: str, why: str) -> bool:
    with write_tx(conn):
        n = conn.execute("UPDATE addresses SET paused = 1 WHERE address_id = ? AND paused = 0",
                         (address_id,)).rowcount  # fmt: skip
        if n:
            conn.execute(
                "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
                " VALUES (?, ?, 'address.paused', 'service', 'ok', ?)",
                (to_ts(clock.now()), address_id, json.dumps({"why": why})),
            )
    return bool(n)


def _facts(p: ParsedMessage) -> dict[str, Any]:
    refs = message_ids(*p.headers.get("references", ()), *p.headers.get("in-reply-to", ()))
    return {
        "size": p.size,
        "from_count": p.from_count,
        # the thread's first Message-ID, the key of "one template reply per thread" (OD-325)
        "thread_root": refs[0] if refs else None,
        "mime_defects": p.defects,
        "text_truncated": p.any_truncated,
        "attachments": [
            {"name": a.name, "type": a.content_type, "size": a.size, "inline": a.inline}
            for a in p.attachments
        ],
    }


def _mark(conn: sqlite3.Connection, clock: Clock, address_id: str, uv: int, uid: int) -> int:
    with write_tx(conn):
        conn.execute(
            "INSERT INTO processing (address_id, uidvalidity, uid, attempts, started_at)"
            " VALUES (?, ?, ?, 1, ?) ON CONFLICT DO UPDATE SET attempts = attempts + 1,"
            " started_at = excluded.started_at",
            (address_id, uv, uid, to_ts(clock.now())),
        )
        row = conn.execute(
            "SELECT attempts FROM processing WHERE address_id = ? AND uidvalidity = ? AND uid = ?",
            (address_id, uv, uid),
        ).fetchone()
    return int(row["attempts"])


def _give_back(conn: sqlite3.Connection, address_id: str, uv: int, uid: int) -> None:
    """Undo this attempt's count: losing the lease or the connection is never the message's fault,
    so it mustn't move the message toward quarantine (shadow run and V1.1 review, 2026-09-29).
    A message the server can never deliver then fails the check each time, which shows in
    `ecf status` and raises Mail Provider Unreachable, instead of being quarantined unread.
    A decrement, not a delete, keeps any count another holder has added since."""
    with write_tx(conn):
        conn.execute(
            "UPDATE processing SET attempts = attempts - 1"
            " WHERE address_id = ? AND uidvalidity = ? AND uid = ?",
            (address_id, uv, uid),
        )
        conn.execute(
            "DELETE FROM processing WHERE address_id = ? AND uidvalidity = ? AND uid = ?"
            " AND attempts <= 0",
            (address_id, uv, uid),
        )


def _unmark(conn: sqlite3.Connection, address_id: str, uv: int, uid: int) -> None:
    with write_tx(conn):
        _unmark_in(conn, address_id, uv, uid)


def _unmark_in(conn: sqlite3.Connection, address_id: str, uv: int, uid: int) -> None:
    conn.execute(
        "DELETE FROM processing WHERE address_id = ? AND uidvalidity = ? AND uid = ?",
        (address_id, uv, uid),
    )


def _fence(conn: sqlite3.Connection, clock: Clock, lease: leases.Lease) -> None:
    """Inside a write transaction: roll it back if the lease is no longer ours (§6.4)."""
    if not leases.held(conn, clock, lease):
        raise LeaseLostError(f"lease on {lease.address_id} lost; nothing written")


def _quarantine(
    conn: sqlite3.Connection, clock: Clock, lease: leases.Lease, uv: int, uid: int
) -> None:
    """Two crashes on this message: record it for a person without reading it again."""
    address_id = lease.address_id

    def also(c: sqlite3.Connection) -> None:
        _fence(c, clock, lease)
        _unmark_in(c, address_id, uv, uid)

    marker = hashlib.sha256(f"quarantined|{address_id}|{uv}|{uid}".encode()).hexdigest()
    items.create_item(
        conn,
        clock,
        stable_id=StableId(stable_id(address_id, None, marker, uv, uid)),
        address_id=AddressId(address_id),
        content_hash=marker,
        uid=uid,
        uidvalidity=uv,
        facts=json.dumps({"quarantined": True, "content_unscanned": True}),
        locator=json.dumps({"uid": uid, "uidvalidity": uv}),
        also=also,
    )
