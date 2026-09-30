"""One check of one address (SPEC §5.1, §5.4): lease, fetch with analysis, pre-check, record.

Used by `ecf check` now and by the scheduler (V1.1 step 11b). The app password is read from the
secret store at each connect (never kept). A busy lease, a mail error, a rejected login or a lost
lease becomes a report, not an exception; the outcome is stored in `check_state` and audited as
`check.completed` or `check.failed` (counts only, never content).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ecf.errors import MailUnavailableError, NotFoundError, ServiceUnavailableError
from ecf.status import CHECK_FAILED
from ecf_server import backfill, leases, precheck
from ecf_server.addresses import MailFactory, secret_name
from ecf_server.analysis import MessageAnalyzer
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.dnscache import DnsCache
from ecf_server.fetch import (
    MAX_PER_CHECK_S,
    AddressConfig,
    LeaseLostError,
    PageResult,
    address_config,
    backfill_page,
    close_gone,
    fetch_page,
    load_cursor,
)
from ecf_server.isolate import Isolator, subprocess_isolator
from ecf_server.log_bridge import log
from ecf_server.mail import MailSource
from ecf_server.mail.imap import MailLoginRejectedError
from ecf_server.secretstore import SecretStore

DNS_CAP_S = 600  # DNS answers are cached for at most the check interval (§7.3)


@dataclass
class CheckReport:
    address_id: str
    status: str  # ok | first_run | busy | reset_recovered | lease_lost | login_rejected | error
    started_at: str
    finished_at: str = ""
    created: int = 0
    duplicates: int = 0
    quarantined: int = 0
    large_done: int = 0
    deferred: int = 0
    remaining: int = 0
    backfill_created: int = 0
    backfill_remaining: int = 0  # older mail an `ecf backfill` still has to read
    escalations: int = 0
    digest: int = 0
    relocated: int = 0  # known messages re-pointed after a mailbox reset
    resolved_by_mailbox: int = 0  # open items whose message left INBOX
    error: str | None = None
    notes: list[str] = field(default_factory=list[str])

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def more(self) -> bool:
        """Another check now would make progress (for `--until-empty`)."""
        progressing = self.status in ("ok", "reset_recovered")
        return progressing and (
            self.remaining > 0 or self.large_done > 0 or self.backfill_remaining > 0
        )


def holder() -> str:
    """Unique per check: a holder may renew or re-take its own lease, so two checks must never
    share a name."""
    return f"service-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def run_check(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    address_id: str,
    install: str,
    secrets: SecretStore,
    factory: MailFactory,
    connect: Callable[[], sqlite3.Connection],
) -> CheckReport:
    row = conn.execute(
        "SELECT a.email, p.host FROM addresses a JOIN probe p USING (address_id)"
        " WHERE a.address_id = ? AND a.removed_at IS NULL",
        (address_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"no address {address_id!r}")
    report = CheckReport(address_id, "ok", to_ts(clock.now()))
    # In-process exclusion first (SPEC §5.1): the lease alone can't tell two checks in this
    # service apart, so a scheduled check and `ecf check` could otherwise both run (found in the
    # V1.1 shadow run, 2026-09-29).
    lock = leases.local_lock(address_id)
    if not lock.acquire(blocking=False):
        report.status = "busy"
        return _finish(conn, clock, report)
    try:
        _locked_check(conn, clock, report, row, install, secrets, factory, connect)
    except Exception as exc:  # a bug, a child crash, SQLite: still recorded, scheduled, visible
        log.error("check.crashed", address_id=address_id, error_type=type(exc).__name__)
        report.status, report.error = "internal_error", f"internal error ({type(exc).__name__})"
    finally:
        lock.release()
    return _finish(conn, clock, report)


def record_failure(
    conn: sqlite3.Connection, clock: Clock, address_id: str, status: str, error: str
) -> CheckReport:
    """A check that couldn't start (e.g. the secret store is unavailable), recorded like one
    that ran, so `ecf status` and the schedule see it (V1.1 review, 2026-09-29)."""
    report = CheckReport(address_id, status, to_ts(clock.now()))
    report.error = error
    return _finish(conn, clock, report)


class SecretUnavailableError(ServiceUnavailableError):
    """The app password couldn't be read: not a mail provider problem (V1.1 review)."""


# Work that needs the address lease and the open mailbox, run in every check after the pre-check.
# Registered by the modules that own it (digests.py: queued Undos), so checks needs no Slack code.
InLease = Callable[[sqlite3.Connection, Clock, MailSource, str, str, int], object]
IN_LEASE: list[InLease] = []


def _locked_check(
    conn: sqlite3.Connection,
    clock: Clock,
    report: CheckReport,
    row: sqlite3.Row,
    install: str,
    secrets: SecretStore,
    factory: MailFactory,
    connect: Callable[[], sqlite3.Connection],
) -> None:
    address_id = report.address_id
    lease = leases.acquire(conn, clock, address_id, holder())
    if lease is None:
        report.status = "busy"
        return
    name = secret_name(address_id)

    def password() -> str:
        pw = secrets.get(name)
        if not pw:
            raise SecretUnavailableError(f"no app password stored for {address_id}")
        return pw

    src: MailSource | None = None
    try:
        src = factory(row["host"], row["email"], password)
        with leases.Renewer(connect, clock, lease) as renewer:
            cfg = address_config(conn, address_id)
            dns = DnsCache(conn, clock, cap_s=DNS_CAP_S)
            analyzer = MessageAnalyzer.for_address(conn, clock, address_id, dns)
            deadline = clock.monotonic() + MAX_PER_CHECK_S
            page = fetch_page(
                conn,
                clock,
                src,
                cfg,
                lease,
                lost=renewer.lost,
                analyzer=analyzer,
                deadline=deadline,
                isolator=_isolator(conn, dns),
            )
            cur = load_cursor(conn, address_id)
            if cur is not None and cur.uidvalidity is not None:
                report.resolved_by_mailbox = close_gone(
                    conn, clock, src, lease, cur.uidvalidity, close_stale=cur.recovering_until == 0
                )
            outcomes = precheck.run(
                conn,
                clock,
                src,
                address_id,
                page.created,
                install=install,
                max_scan_bytes=cfg.max_scan_bytes,
            )
            if page.remaining == 0 and page.stopped != "lease_lost":  # new mail first
                outcomes += _backfill(conn, clock, src, cfg, lease, report, install=install,
                                      analyzer=analyzer, lost=renewer.lost, dns=dns,
                                      deadline=deadline)  # fmt: skip
            for work in IN_LEASE:  # e.g. queued Undos (digests.py)
                work(conn, clock, src, address_id, install, cfg.max_scan_bytes)
        report.status = _page_status(page)
        report.created, report.duplicates = len(page.created), page.duplicates
        report.relocated = page.relocated
        report.quarantined, report.large_done = len(page.quarantined), len(page.large_done)
        report.deferred, report.remaining = len(page.deferred), page.remaining
        report.escalations = sum(o.decision.escalate for o in outcomes)
        report.digest = sum(o.decision.digest for o in outcomes)
        report.notes = [o.skipped for o in outcomes if o.skipped and o.decision.actions]
    except MailLoginRejectedError as exc:
        report.status, report.error = "login_rejected", exc.detail
    except LeaseLostError as exc:
        report.status, report.error = "lease_lost", exc.detail
    except SecretUnavailableError as exc:
        report.status, report.error = "secret_unavailable", exc.detail
    except (MailUnavailableError, ServiceUnavailableError) as exc:
        report.status, report.error = "error", exc.detail
    finally:
        if src is not None:
            src.close()
        leases.release(conn, lease)


def _backfill(  # noqa: PLR0913 - the check's collaborators, passed through
    conn: sqlite3.Connection,
    clock: Clock,
    src: MailSource,
    cfg: AddressConfig,
    lease: leases.Lease,
    report: CheckReport,
    *,
    install: str,
    analyzer: MessageAnalyzer,
    lost: threading.Event,
    dns: DnsCache,
    deadline: float,
) -> list[precheck.Outcome]:
    """A page of an `ecf backfill` in progress, after the new mail (V1.2 step 11b)."""
    bf = backfill.load(conn, cfg.address_id)
    if bf is None:
        return []
    page = backfill_page(conn, clock, src, cfg, lease, bf, lost=lost, analyzer=analyzer,
                         deadline=deadline, isolator=_isolator(conn, dns))  # fmt: skip
    if page.stopped == "reset":
        backfill.finish(conn, clock, cfg.address_id, bf, "mailbox reset")
        return []
    # decide first, then save: plus anything a failed pass left undecided in the range
    todo = [*page.created, *backfill.undecided(conn, cfg.address_id, bf, page.created)]
    outcomes = backfill.decide(conn, clock, src, cfg.address_id, todo, bf,
                               install=install, max_scan_bytes=cfg.max_scan_bytes)  # fmt: skip
    backfill.save(conn, clock, cfg.address_id, bf)
    report.backfill_created, report.backfill_remaining = len(page.created), page.remaining
    if page.remaining == 0 and page.stopped == "done":
        backfill.finish(conn, clock, cfg.address_id, bf, "done")
    return outcomes


def _isolator(conn: sqlite3.Connection, dns: DnsCache) -> Isolator | None:
    """Messages go to a child process that opens the same database file (OD-195, OD-204)."""
    row = conn.execute("PRAGMA database_list").fetchone()
    path = row["file"] if row else ""
    if not path:
        return None
    return subprocess_isolator(Path(path), dns_cap_s=DNS_CAP_S, dns_budget=dns.remaining)


def _finish(conn: sqlite3.Connection, clock: Clock, r: CheckReport) -> CheckReport:
    r.finished_at = to_ts(clock.now())
    failed = r.status in CHECK_FAILED
    with write_tx(conn):
        if r.status != "busy":
            conn.execute(
                "INSERT INTO check_state (address_id, last_started_at, last_finished_at,"
                " last_status, last_result, backlog, deferred, last_error, last_error_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (address_id) DO UPDATE SET"
                " last_started_at = excluded.last_started_at,"
                " last_finished_at = excluded.last_finished_at,"
                " last_status = excluded.last_status, last_result = excluded.last_result,"
                " backlog = excluded.backlog, deferred = excluded.deferred,"
                " last_error = coalesce(excluded.last_error, check_state.last_error),"
                " last_error_at = coalesce(excluded.last_error_at, check_state.last_error_at)",
                (
                    r.address_id,
                    r.started_at,
                    r.finished_at,
                    r.status,
                    json.dumps(r.to_json()),
                    r.remaining,
                    r.deferred,
                    r.error if failed else None,
                    r.finished_at if failed else None,
                ),
            )
        counts = {k: v for k, v in r.to_json().items() if k not in ("notes", "error")}
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, ?, 'service', ?, ?)",
            (
                r.finished_at,
                r.address_id,
                _event(r.status),
                "error" if failed else "ok",
                json.dumps(counts),
            ),
        )
    return r


def _page_status(page: PageResult) -> str:
    if page.first_run:
        return "first_run"
    if page.stopped == "lease_lost":
        return "lease_lost"
    return "reset_recovered" if page.reset_detected else "ok"


def _event(status: str) -> str:
    if status in CHECK_FAILED:
        return "check.failed"
    return "check.lease_skipped" if status == "busy" else "check.completed"


def states(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Per active address: stage, pause and the last check (for `ecf status`)."""
    rows = conn.execute(
        "SELECT a.address_id, a.email, a.stage, a.paused, c.last_finished_at, c.last_status,"
        " c.backlog, c.deferred, c.last_error, c.last_error_at, c.next_due_at FROM addresses a"
        " LEFT JOIN check_state c USING (address_id) WHERE a.removed_at IS NULL"
        " ORDER BY a.address_id"
    ).fetchall()
    return [dict(r) | {"paused": bool(r["paused"])} for r in rows]
