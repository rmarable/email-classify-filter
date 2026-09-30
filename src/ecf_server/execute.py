"""Running approved actions (SPEC §6.2, §6.4; V1.2 step 7b): the `actions` job queue.

A job names an item and its approved grant. The runner consumes the grant once (a conditional
update; a retry puts it back), calls the executor, and moves the item to `executed`, or after 3
attempts to `failed` (`ecf item requeue` runs it again).

The executor is a port. V1.2 has no real one for approved actions: hiding and moving mail arrive
with proposals (V1.3) and sends with outbound (V1.5, OD-207), so `unavailable` refuses and the item
fails with that reason. Tests use a fake. V1.3 moves this into the checks worker, which holds the
address lease that mailbox changes need.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ecf.errors import GrantInvalidError
from ecf.ids import StableId
from ecf.status import Status
from ecf_server import approvals, items, jobs
from ecf_server.actions import Planned, action_hash
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.state_machine import TransitionContext

WORKER = "actions"
Executor = Callable[[sqlite3.Connection, Clock, sqlite3.Row, list[Planned]], list[str]]


class ExecutorUnavailableError(Exception):
    pass


def unavailable(_conn: sqlite3.Connection, _clock: Clock, _item: sqlite3.Row,
                actions: list[Planned]) -> list[str]:  # fmt: skip
    names = sorted({a.name for a in actions})
    later = "V1.5" if any(n in approvals.SENDS for n in names) else "V1.3"
    raise ExecutorUnavailableError(f"{', '.join(names)}: not available until {later}")


def run_once(conn: sqlite3.Connection, clock: Clock, executor: Executor) -> bool:
    job = jobs.claim(conn, clock, jobs.Queue.ACTIONS, WORKER)
    if job is None:
        return False
    sid, grant_id = str(job.payload["stable_id"]), str(job.payload["grant_id"])
    item = conn.execute("SELECT * FROM items WHERE stable_id = ?", (sid,)).fetchone()
    if item is None or item["status"] != Status.EXECUTING:
        jobs.complete(conn, job.job_id, WORKER)  # resolved or cancelled meanwhile: nothing to do
        return True
    p: dict[str, Any] = json.loads(item["proposal"] or "{}")
    actions = [Planned(str(a["name"]), a.get("target")) for a in p.get("actions", [])]
    try:
        _consume(conn, clock, grant_id, action_hash(sid, item["content_hash"], actions))
    except GrantInvalidError as exc:
        _failed(conn, clock, job, item, f"grant: {exc}")
        return True
    try:
        done = executor(conn, clock, item, actions)
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
