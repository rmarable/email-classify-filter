"""Running approved actions (SPEC §6.2, §6.4; V1.2 step 7b): the `actions` job queue.

A job names an item and its approved grant. The runner consumes the grant once (a conditional
update; a retry puts it back), calls the executor, and moves the item to `executed`, or after 3
attempts to `failed` (`ecf item requeue` runs it again).

The executor is a port. From V1.3 the real one (`mailbox_actions.executor_for`) runs inside each
address's check, which holds the lease and has the mailbox open; a refusal at execution (stage,
pause, folders, a changed message) fails the item at once without a retry. Sends arrive in V1.5.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ecf.errors import GrantInvalidError, PolicyDeniedError
from ecf.ids import StableId
from ecf.status import Status
from ecf_server import approvals, items, jobs, pause
from ecf_server.actions import MessageChangedError, Planned, action_hash
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.state_machine import TransitionContext

WORKER = "actions"
PAUSED_RECHECK_S = 60
Executor = Callable[[sqlite3.Connection, Clock, sqlite3.Row, list[Planned]], list[str]]


def run_once(  # noqa: PLR0911 - one return per outcome
    conn: sqlite3.Connection, clock: Clock, executor: Executor, *, address_id: str | None = None
) -> bool:
    job = jobs.claim(conn, clock, jobs.Queue.ACTIONS, WORKER, address_id=address_id)
    if job is None:
        return False
    sid, grant_id = str(job.payload["stable_id"]), str(job.payload["grant_id"])
    item = conn.execute("SELECT * FROM items WHERE stable_id = ?", (sid,)).fetchone()
    if item is None or item["status"] != Status.EXECUTING:
        jobs.complete(conn, job.job_id, WORKER)  # resolved or cancelled meanwhile: nothing to do
        return True
    if pause.is_paused(conn, item["address_id"]):  # waits for resume; not counted as an attempt
        jobs.hold(conn, clock, job.job_id, WORKER, PAUSED_RECHECK_S, "paused")
        return True
    p: dict[str, Any] = json.loads(item["proposal"] or "{}")
    actions = [Planned(str(a["name"]), a.get("target")) for a in p.get("actions", [])]
    try:
        _consume(conn, clock, grant_id, action_hash(sid, item["content_hash"], actions))
    except GrantInvalidError as exc:
        if _grant_status(conn, grant_id) == "consumed":  # it may have run before a crash
            _outcome_unknown(conn, clock, job, item)
        else:
            _failed(conn, clock, job, item, f"grant: {exc}")
        return True
    try:
        done = executor(conn, clock, item, actions)
    except (PolicyDeniedError, MessageChangedError) as exc:  # refused at execution: no retry
        with write_tx(conn):
            conn.execute("UPDATE grants SET status = 'voided' WHERE grant_id = ?", (grant_id,))
        _failed(conn, clock, job, item, str(exc.detail)[:200])
        return True
    except Exception as exc:  # retried with backoff, then failed
        with write_tx(conn):  # the grant is usable again for the retry
            conn.execute("UPDATE grants SET status = 'approved', consumed_at = NULL"
                          " WHERE grant_id = ?", (grant_id,))  # fmt: skip
        state = jobs.fail(conn, clock, job.job_id, WORKER, type(exc).__name__)
        log.warning("action.failed", stable_id=sid, error_type=type(exc).__name__, state=state)
        if state == "dead":
            _failed(conn, clock, job, item, str(exc)[:200], complete=False)
        return True
    items.transition(conn, clock, StableId(sid), Status.EXECUTED, TransitionContext(),
                     actor="service", expected=Status.EXECUTING)  # fmt: skip
    _audit(conn, clock, item, "action.executed", {"grant_id": grant_id, "done": done})
    jobs.complete(conn, job.job_id, WORKER)
    approvals.edit_card(conn, clock, sid, f"Done: {', '.join(done) or 'nothing to do'}")
    return True


def _grant_status(conn: sqlite3.Connection, grant_id: str) -> str | None:
    row = conn.execute("SELECT status FROM grants WHERE grant_id = ?", (grant_id,)).fetchone()
    return None if row is None else str(row[0])


def _outcome_unknown(
    conn: sqlite3.Connection, clock: Clock, job: jobs.Job, item: sqlite3.Row
) -> None:
    """The grant was already used: the action may have run before a crash or an expired claim.
    Never run it again unchecked (§6.2 `failed_unknown`; V1.2 review, 2026-09-30)."""
    items.transition(conn, clock, StableId(item["stable_id"]), Status.FAILED_UNKNOWN,
                     TransitionContext(), actor="service", expected=Status.EXECUTING)  # fmt: skip
    _audit(conn, clock, item, "action.outcome_unknown", {}, outcome="error")
    jobs.complete(conn, job.job_id, WORKER)
    approvals.edit_card(conn, clock, item["stable_id"],
                        "Outcome unknown: it may already have run. Check the mailbox, then"
                        f" ecf item show {item['stable_id'][:8]}")  # fmt: skip


def _consume(conn: sqlite3.Connection, clock: Clock, grant_id: str, expected_hash: str) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        n = conn.execute(
            "UPDATE grants SET status = 'consumed', consumed_at = ? WHERE grant_id = ?"
            " AND status = 'approved' AND action_hash = ? AND expires_at > ?",
            (now, grant_id, expected_hash, now),
        ).rowcount
    if n != 1:
        raise GrantInvalidError(f"grant {grant_id[:8]} isn't approved for this action any more")


def _failed(
    conn: sqlite3.Connection,
    clock: Clock,
    job: jobs.Job,
    item: sqlite3.Row,
    why: str,
    *,
    complete: bool = True,
) -> None:
    items.transition(conn, clock, StableId(item["stable_id"]), Status.FAILED, TransitionContext(),
                     actor="service", expected=Status.EXECUTING)  # fmt: skip
    _audit(conn, clock, item, "action.failed", {"why": why}, outcome="error")
    if complete:
        jobs.complete(conn, job.job_id, WORKER)
    short = item["stable_id"][:8]
    approvals.edit_card(conn, clock, item["stable_id"],
                        f"Failed: {why[:100]}. Retry: ecf item requeue {short}")  # fmt: skip


def _audit(
    conn: sqlite3.Connection,
    clock: Clock,
    item: sqlite3.Row,
    event: str,
    data: dict[str, Any],
    outcome: str = "ok",
) -> None:
    with write_tx(conn):
        conn.execute(
            "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
            " VALUES (?, ?, ?, ?, 'service', ?, ?)",
            (to_ts(clock.now()), item["address_id"], item["stable_id"], event, outcome,
             json.dumps(data)),
        )  # fmt: skip
