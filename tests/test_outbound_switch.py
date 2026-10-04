"""The outbound switch (V1.5 step 3b; SPEC §9.8; OD-323): enable with step-up and, on a high
address, a reviewed track record; disable at once, stopping approved sends."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import PolicyDeniedError, StepupRequiredError
from ecf.ids import AddressId, StableId
from ecf_server import approvals, execute, items, outbound, stepup
from ecf_server.actions import Planned
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.stepper import FakeStepper
from tests.test_addresses import TOKEN, call, make_state
from tests.test_approvals import (
    _proposed,  # pyright: ignore[reportPrivateUsage]
    _setup,  # pyright: ignore[reportPrivateUsage]
    _status,  # pyright: ignore[reportPrivateUsage]
    _verified_nonce,  # pyright: ignore[reportPrivateUsage]
)

SEND = [Planned("reply_template", "ack", {"to": "pat@vendor.example", "template": "ack",
                                          "rendered": "x"})]  # fmt: skip


def _enable(conn: sqlite3.Connection, clock: FakeClock, notices: list[str]) -> dict[str, Any]:
    with pytest.raises(StepupRequiredError) as ei:
        outbound.enable(conn, clock, notices.append, "ap", actor="os_user", nonce=None)
    nonce = _verified_nonce(conn, clock, ei.value)
    return outbound.enable(conn, clock, notices.append, "ap", actor="os_user", nonce=nonce)


def test_enable_needs_step_up_and_announces(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    notices: list[str] = []
    with pytest.raises(StepupRequiredError) as ei:
        outbound.enable(conn, clock, notices.append, "ap", actor="os_user", nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), ei.value.extra["purpose"],
                          ei.value.extra["target"])  # fmt: skip
    assert issued.prompt.startswith("ecf: let ap@acme.example send approved template replies")
    a = _enable(conn, clock, notices)
    assert a["outbound"] is True and notices[0].startswith("Outbound is on for ap@acme.example")
    events = [r[0] for r in conn.execute("SELECT event FROM audit WHERE event LIKE 'outbound.%'")]
    assert events == ["outbound.enabled"]


def _suppressed(conn: sqlite3.Connection, clock: FakeClock, n: int, correct: int) -> None:
    for i in range(n):
        sid = f"s{i:03d}".ljust(64, "0")
        items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                          content_hash="h")  # fmt: skip
        verdict = "correct" if i < correct else "fixed"
        with write_tx(conn):
            conn.execute(
                "UPDATE items SET suppressed_action = 'reply_template', review = ?"
                " WHERE stable_id = ?",
                (json.dumps({"verdict": verdict}), sid),
            )


@pytest.mark.parametrize(("n", "correct", "ok"), [(19, 19, False), (20, 18, False),
                                                  (20, 19, True)])  # fmt: skip
def test_a_high_address_needs_a_reviewed_track_record(
    conn: sqlite3.Connection, clock: FakeClock, n: int, correct: int, ok: bool
) -> None:
    _setup(conn, clock, sensitivity="high")
    _suppressed(conn, clock, n, correct)
    if ok:
        assert _enable(conn, clock, [])["outbound"] is True
    else:
        notices: list[str] = []
        with pytest.raises(PolicyDeniedError, match="high address needs"):
            outbound.enable(conn, clock, notices.append, "ap", actor="os_user", nonce=None)


def _approved_send(conn: sqlite3.Connection, clock: FakeClock, sid: str) -> None:
    _proposed(conn, clock, sid)
    approvals.request(conn, clock, sid, SEND)
    with pytest.raises(StepupRequiredError) as ei:
        approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")
    approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user",
                      nonce=_verified_nonce(conn, clock, ei.value))  # fmt: skip


def test_disable_stops_delayed_and_queued_sends(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock, sensitivity="high")
    with write_tx(conn):
        conn.execute("UPDATE addresses SET outbound = 1")
    delayed, queued = "a" * 64, "b" * 64
    _approved_send(conn, clock, delayed)
    assert _status(conn, delayed) == "delayed"
    with write_tx(conn):
        conn.execute("UPDATE addresses SET sensitivity = 'standard'")
    _approved_send(conn, clock, queued)
    assert _status(conn, queued) == "executing"
    a = outbound.disable(conn, clock, "ap", actor="os_user")
    assert a["outbound"] is False and sorted(a["stopped"]) == ["aaaaaaaa", "bbbbbbbb"]
    assert _status(conn, delayed) == "cancelled"
    ran: list[str] = []

    def executor(*_args: Any) -> list[str]:
        ran.append("x")
        return []

    execute.run_once(conn, clock, executor)
    assert _status(conn, queued) == "failed" and ran == []  # its grant is void: never sent


def test_the_route(conn: sqlite3.Connection, db_path: Path, clock: FakeClock) -> None:
    _setup(conn, clock)
    st = make_state(db_path, None)
    r = call(st, "POST", "/v1/addresses/ap/outbound", {"value": "on"}, TOKEN)
    assert r.status_code == 403 and r.json()["code"] == "stepup_required"
    r = call(st, "POST", "/v1/addresses/ap/outbound", {"value": "maybe"}, TOKEN)
    assert r.status_code == 400
    r = call(st, "POST", "/v1/addresses/ap/outbound", {"value": "off"}, TOKEN)
    assert r.status_code == 200 and r.json()["stopped"] == []
