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

Messages over the size limit, or over 16 MB, are deferred in `deferred_uids` (step 6b adds
their handling); nothing is ever skipped. A UIDVALIDITY change is reported, not handled (step 13).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ecf.errors import ConflictError
from ecf.ids import AddressId, StableId
from ecf_server import items, leases
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.mail import MailSource
from ecf_server.message import (
    ACTOR_CHARS,
    CLASSIFIER_CHARS,
    ParsedMessage,
    parse,
    parse_partial,
    stable_id,
)

PAGE_MESSAGES = 30
PAGE_S = 20.0
LARGE_BYTES = 16 * 1024 * 1024  # fixed threshold: processed after smaller mail (§5.1)
QUARANTINE_AFTER = 2  # crashes on the same message
MAX_PER_CHECK_S = 360.0  # max_per_check default: a check's IMAP and rules work (§5.2)
DEFAULT_RATE = 2 * 1024 * 1024  # bytes/s assumed until a page has measured throughput
HEADER_LIMIT = 256 * 1024  # header block fetched for an oversized message
LARGE_LOCK = threading.Lock()  # large messages are processed one at a time service-wide
MB = 1024 * 1024
DEFAULT_MAX = {"high": 64 * MB, "standard": 16 * MB}  # OD-024
DEFAULT_SCAN = 10 * MB  # OD-027

Analyze = Callable[[ParsedMessage, bytes], dict[str, Any]]


class LeaseLostError(ConflictError):
    """The check lease expired or was taken; this holder must stop writing."""


@dataclass
class Cursor:
    uidvalidity: int | None
    last_uid: int
    deferred: list[int]
    version: int


@dataclass(frozen=True)
class AddressConfig:
    address_id: str
    sensitivity: str
    max_message_bytes: int
    max_scan_bytes: int


@dataclass
class PageResult:
    first_run: bool = False
    reset_detected: bool = False
    created: list[str] = field(default_factory=list[str])
    duplicates: int = 0
    quarantined: list[int] = field(default_factory=list[int])
    deferred: list[int] = field(default_factory=list[int])  # still waiting after this check
    large_done: list[int] = field(default_factory=list[int])  # deferred messages handled now
    remaining: int = 0  # new UIDs not reached in this page
    stopped: str = "done"  # done | page_limit | time | lease_lost


def address_config(conn: sqlite3.Connection, address_id: str) -> AddressConfig:
    row = conn.execute(
        "SELECT sensitivity, overrides FROM addresses WHERE address_id = ?", (address_id,)
    ).fetchone()
    overrides: dict[str, Any] = json.loads(row["overrides"])
    return AddressConfig(
        address_id=address_id,
        sensitivity=row["sensitivity"],
        max_message_bytes=int(overrides.get("max_message_bytes", DEFAULT_MAX[row["sensitivity"]])),
        max_scan_bytes=int(overrides.get("max_scan_bytes_per_part", DEFAULT_SCAN)),
    )


def load_cursor(conn: sqlite3.Connection, address_id: str) -> Cursor | None:
    row = conn.execute("SELECT * FROM cursors WHERE address_id = ?", (address_id,)).fetchone()
    if row is None:
        return None
    return Cursor(
        row["uidvalidity"], row["last_uid"], json.loads(row["deferred_uids"]), row["version"]
    )


def save_cursor(conn: sqlite3.Connection, clock: Clock, lease: leases.Lease, cur: Cursor) -> Cursor:
    """Write the cursor if its version is unchanged and the lease is still held. The lease check
    and the write share one transaction, which holds SQLite's write lock throughout."""
    deferred = json.dumps(sorted(set(cur.deferred)))
    with write_tx(conn):
        if not leases.held(conn, clock, lease):
            raise LeaseLostError(f"lease on {lease.address_id} lost; cursor not saved")
        if cur.version == 0 and load_cursor(conn, lease.address_id) is None:
            conn.execute(
                "INSERT INTO cursors (address_id, uidvalidity, last_uid, deferred_uids, version)"
                " VALUES (?, ?, ?, ?, 1)",
                (lease.address_id, cur.uidvalidity, cur.last_uid, deferred),
            )
        elif (
            conn.execute(
                "UPDATE cursors SET uidvalidity = ?, last_uid = ?, deferred_uids = ?,"
                " version = version + 1 WHERE address_id = ? AND version = ?",
                (cur.uidvalidity, cur.last_uid, deferred, lease.address_id, cur.version),
            ).rowcount
            != 1
        ):
            raise LeaseLostError(f"cursor for {lease.address_id} moved; not saved")
    return Cursor(cur.uidvalidity, cur.last_uid, sorted(set(cur.deferred)), cur.version + 1)


def fetch_page(
    conn: sqlite3.Connection,
    clock: Clock,
    src: MailSource,
    cfg: AddressConfig,
    lease: leases.Lease,
    *,
    lost: threading.Event | None = None,
    analyze: Analyze | None = None,
    deadline: float | None = None,
) -> PageResult:
    """One page of new mail, then deferred large mail while `deadline` (monotonic; default the
    check budget from now) leaves time for it."""
    result = PageResult()
    state = src.inbox()
    cur = load_cursor(conn, cfg.address_id)
    if cur is None:  # first run: start from now
        save_cursor(conn, clock, lease, Cursor(state.uidvalidity, state.uidnext - 1, [], 0))
        result.first_run = True
        return result
    if cur.uidvalidity != state.uidvalidity:
        result.reset_detected = True  # recovery is step 13; nothing is fetched meanwhile
        return result

    new = src.uids_after(cur.last_uid)
    page = new[:PAGE_MESSAGES]
    metas = src.meta(page)
    pg = _Page(conn, clock, src, cfg, lease, state.uidvalidity, result, analyze)
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
        elif meta is not None:
            _process(pg, uid)
        cur.last_uid = uid  # vanished messages (no meta) are simply passed
        cur = save_cursor(conn, clock, lease, cur)
        done += 1
    if result.stopped == "done" and len(new) > len(page):
        result.stopped = "page_limit"
    result.remaining = len(new) - done
    if result.stopped != "lease_lost" and cur.deferred:
        cur = _deferred(pg, cur, end, lost)
    result.deferred = list(cur.deferred)
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
        if pg.clock.monotonic() + need / pg.rate() > end:
            break  # next check
        if not LARGE_LOCK.acquire(blocking=False):
            break  # another address is processing a large message
        try:
            if oversized:
                _process_partial(pg, uid, meta.size)
            else:
                _process_large(pg, uid)
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
    analyze: Analyze | None
    fetched_bytes: int = 0
    fetch_s: float = 0.0

    def rate(self) -> float:
        """Measured fetch throughput in bytes/s, or DEFAULT_RATE before there is enough data."""
        if self.fetch_s < 0.5 or self.fetched_bytes == 0:
            return DEFAULT_RATE
        return self.fetched_bytes / self.fetch_s


def _process(pg: _Page, uid: int) -> None:
    conn, clock, cfg, lease, uv = pg.conn, pg.clock, pg.cfg, pg.lease, pg.uidvalidity
    attempts = _mark(conn, clock, cfg.address_id, uv, uid)
    if attempts > QUARANTINE_AFTER:
        _quarantine(conn, clock, lease, uv, uid)
        pg.result.quarantined.append(uid)
        return
    t0 = clock.monotonic()
    raw = pg.src.fetch(uid)
    if raw is None:  # gone since the search
        _unmark(conn, cfg.address_id, uv, uid)
        return
    pg.fetched_bytes += len(raw)
    pg.fetch_s += clock.monotonic() - t0
    _store(pg, uid, parse(raw, max_scan_bytes=cfg.max_scan_bytes), raw, {})


def _process_large(pg: _Page, uid: int) -> None:
    """A message over 16 MB but within the limit: read whole, peak memory logged (§5.1)."""
    tracing = not tracemalloc.is_tracing()
    if tracing:
        tracemalloc.start()
    tracemalloc.reset_peak()
    try:
        _process(pg, uid)
    finally:
        peak = tracemalloc.get_traced_memory()[1]
        if tracing:
            tracemalloc.stop()
    log.info("fetch.large_message", address_id=pg.cfg.address_id, uid=uid, peak_bytes=peak)


def _process_partial(pg: _Page, uid: int, size: int) -> None:
    """A message over the address's limit: headers and the start of each text part only;
    unscanned beyond that, and never a DKIM pass (§5.1, §7.2)."""
    conn, clock, cfg, lease, uv = pg.conn, pg.clock, pg.cfg, pg.lease, pg.uidvalidity
    if _mark(conn, clock, cfg.address_id, uv, uid) > QUARANTINE_AFTER:
        _quarantine(conn, clock, lease, uv, uid)
        pg.result.quarantined.append(uid)
        return
    parts = pg.src.structure(uid)
    header = pg.src.fetch_part(uid, "HEADER", HEADER_LIMIT)
    if parts is None or header is None:
        _unmark(conn, cfg.address_id, uv, uid)
        return
    texts: dict[str, bytes] = {}
    for p in parts:
        if p.content_type in ("text/plain", "text/html") and p.disposition != "attachment":
            texts[p.section] = pg.src.fetch_part(uid, p.section, cfg.max_scan_bytes) or b""
    parsed = parse_partial(header, parts, texts, size=size, max_scan_bytes=cfg.max_scan_bytes)
    _store(pg, uid, parsed, header, {"oversized": True, "content_unscanned": True})


def _store(pg: _Page, uid: int, parsed: ParsedMessage, raw: bytes, extra: dict[str, Any]) -> None:
    """Create the item for a read message, or record a repeat delivery (§6.3)."""
    conn, clock, cfg, lease, uv = pg.conn, pg.clock, pg.cfg, pg.lease, pg.uidvalidity
    sid = stable_id(cfg.address_id, parsed.message_id, parsed.content_hash, uv, uid)
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
    facts = _facts(parsed) | extra | (pg.analyze(parsed, raw) if pg.analyze else {})
    reused = (
        parsed.message_id is not None
        and conn.execute(
            "SELECT 1 FROM items WHERE address_id = ? AND message_id = ? AND content_hash != ?",
            (cfg.address_id, parsed.message_id, parsed.content_hash),
        ).fetchone()
        is not None
    )

    def also(c: sqlite3.Connection) -> None:
        _fence(c, clock, lease)
        c.execute(
            "INSERT INTO excerpts (stable_id, classifier_text, actor_text) VALUES (?, ?, ?)",
            (sid, parsed.excerpt(CLASSIFIER_CHARS), parsed.excerpt(ACTOR_CHARS)),
        )
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
        locator=json.dumps({"uid": uid, "uidvalidity": uv, "message_id": parsed.message_id}),
    )
    pg.result.created.append(sid)


def _facts(p: ParsedMessage) -> dict[str, Any]:
    return {
        "size": p.size,
        "from_count": p.from_count,
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
