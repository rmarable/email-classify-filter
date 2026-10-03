"""The send circuit breaker (V1.5 step 5; SPEC §8.4, §9.6; OD-059): counted from `sent`, tripped
before a grant is used, held until `ecf outbound resume`, limits changed with step-up."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from ecf.errors import ConflictError, InvalidInputError, StepupRequiredError
from ecf_server import approvals, execute, send_limits
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from tests.test_addresses import TOKEN, call, make_state
from tests.test_approvals import (
    _grants,  # pyright: ignore[reportPrivateUsage]
    _setup,  # pyright: ignore[reportPrivateUsage]
    _status,  # pyright: ignore[reportPrivateUsage]
    _verified_nonce,  # pyright: ignore[reportPrivateUsage]
)
from tests.test_outbound_switch import _approved_send  # pyright: ignore[reportPrivateUsage]


def _sends(conn: sqlite3.Connection, clock: FakeClock, n: int, *, kind: str = "reply",
           ago: timedelta = timedelta(minutes=5), status: str = "sent") -> None:  # fmt: skip
    with write_tx(conn):
        for i in range(n):
            conn.execute("INSERT INTO sent (message_id_hash, address_id, content_hash, kind,"
                         " sent_at, message_id, status) VALUES (?, 'ap', 'h', ?, ?, ?, ?)",
                         (f"{kind}{ago}{status}{i}", kind, to_ts(clock.now() - ago), f"<{i}@x>",
                          status))  # fmt: skip


def test_the_hour_limit_trips_and_stays_tripped(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    _sends(conn, clock, 24)
    _sends(conn, clock, 30, kind="alert")  # alert email doesn't count (§13.3)
    _sends(conn, clock, 10, status="failed")  # neither does mail that never went
    assert send_limits.allow(conn, clock, "ap")
    _sends(conn, clock, 1, ago=timedelta(minutes=1))
    assert not send_limits.allow(conn, clock, "ap")
    assert send_limits.tripped(conn, "ap")
    clock.advance(2 * 3600)  # the hour passed: still stopped until a person looks
    assert not send_limits.allow(conn, clock, "ap")
    [data] = [json.loads(r[0]) for r in conn.execute(
        "SELECT data FROM audit WHERE event = 'outbound.breaker_tripped'")]  # fmt: skip
    assert data["over"] == ["max_sends_per_hour"]


def test_the_day_limit_counts_a_day(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    _sends(conn, clock, 249, ago=timedelta(hours=3))
    _sends(conn, clock, 50, ago=timedelta(hours=25))  # yesterday
    assert send_limits.allow(conn, clock, "ap")
    _sends(conn, clock, 1, ago=timedelta(hours=2))
    assert not send_limits.allow(conn, clock, "ap")


def test_a_held_send_keeps_its_grant_and_runs_after_resume(conn: sqlite3.Connection,
                                                           clock: FakeClock) -> None:  # fmt: skip
    _setup(conn, clock)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET outbound = 1")
    sid = "a" * 64
    _approved_send(conn, clock, sid)
    _sends(conn, clock, 25)
    ran: list[str] = []

    def executor(*_a: object) -> list[str]:
        ran.append("sent")
        return ["sent"]

    assert execute.run_once(conn, clock, executor)
    assert ran == [] and _status(conn, sid) == "executing" and _grants(conn, sid) == ["approved"]
    job = conn.execute("SELECT attempts, last_error FROM jobs WHERE queue = 'actions'").fetchone()
    assert (job["attempts"], job["last_error"]) == (0, "send limit")
    notifier = FakeNotifier()
    send_limits.sweep(conn, clock, notifier)
    assert notifier.sent[0][0] == "[ecf-alert] Operator Input Needed: send limit reached"
    with pytest.raises(StepupRequiredError) as ei:
        send_limits.resume(conn, clock, "ap", actor="os_user", nonce=None)
    r = send_limits.resume(conn, clock, "ap", actor="os_user",
                           nonce=_verified_nonce(conn, clock, ei.value))  # fmt: skip
    assert r["outbound"] is True and r["counts"]["max_sends_per_hour"] == 25
    send_limits.sweep(conn, clock, notifier)
    assert notifier.sent[-1][0].startswith("[ecf-alert] Resolved:")
    with pytest.raises(ConflictError):
        send_limits.resume(conn, clock, "ap", actor="os_user", nonce=None)


def test_a_retry_of_a_recorded_send_doesn_t_count_against_itself(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    _sends(conn, clock, 24)
    with write_tx(conn):
        conn.execute("INSERT INTO sent (message_id_hash, address_id, content_hash, kind, sent_at,"
                     " grant_id, status) VALUES ('g', 'ap', 'h', 'reply', ?, 'G1', 'pending')",
                     (to_ts(clock.now()),))  # fmt: skip
    assert send_limits.allow(conn, clock, "ap", "G1")
    assert not send_limits.allow(conn, clock, "ap", "G2")


def test_changing_the_limits_needs_step_up_and_announces(conn: sqlite3.Connection,
                                                         clock: FakeClock) -> None:  # fmt: skip
    _setup(conn, clock)
    notices: list[str] = []
    with pytest.raises(InvalidInputError):
        send_limits.set_limits(conn, clock, notices.append, "ap", {"max_sends_per_hour": 0},
                               actor="os_user", nonce=None)  # fmt: skip
    new = {"max_sends_per_hour": 2}
    with pytest.raises(StepupRequiredError) as ei:
        send_limits.set_limits(conn, clock, notices.append, "ap", new, actor="os_user",
                               nonce=None)  # fmt: skip
    r = send_limits.set_limits(conn, clock, notices.append, "ap", new, actor="os_user",
                               nonce=_verified_nonce(conn, clock, ei.value))  # fmt: skip
    assert r["limits"] == {"max_sends_per_hour": 2, "max_sends_per_day": 250}
    assert notices == ["Send limits for ap@acme.example changed: max_sends_per_hour 25 -> 2."]
    _sends(conn, clock, 2)
    assert not send_limits.allow(conn, clock, "ap")


def test_the_routes(conn: sqlite3.Connection, db_path: Path, clock: FakeClock) -> None:
    _setup(conn, clock)
    st = make_state(db_path, None)
    r = call(st, "POST", "/v1/addresses/ap", {"max_sends_per_hour": 5}, TOKEN)
    assert r.status_code == 403 and r.json()["code"] == "stepup_required"
    r = call(st, "POST", "/v1/addresses/ap", {"max_sends_per_hour": "5"}, TOKEN)
    assert r.status_code == 400
    r = call(st, "POST", "/v1/addresses/ap/outbound", {"value": "resume"}, TOKEN)
    assert r.status_code == 409  # nothing to resume
    assert approvals.SEND_DELAY_S > 0
