"""Approvals (V1.2 step 7b), with fake proposals and a fake executor (OD-207): nothing proposes an
action needing approval before V1.3."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import (
    ConflictError,
    GrantInvalidError,
    InvalidInputError,
    PolicyDeniedError,
    StepupRequiredError,
)
from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import approvals, db, execute, items, slack_admin, stepup
from ecf_server._slack import Envelope
from ecf_server.actions import Planned
from ecf_server.clock import Clock, FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.slack_in import Inbound, SlackReceiver
from ecf_server.state_machine import TransitionContext
from ecf_server.stepper import FakeStepper

ME = "U0ME1"
ARCHIVE = [Planned("archive")]
SEND = [Planned("reply_template", "received")]
FRAUD = {"triggers": {"fraud": ["bank details from an unconfirmed sender"]}}


def _setup(conn: sqlite3.Connection, clock: FakeClock, *, sensitivity: str = "standard") -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute(
            "INSERT INTO addresses (address_id, email, sensitivity, stage, preset, created_at)"
            " VALUES ('ap', 'ap@acme.example', ?, 'live', 'A', ?)",
            (sensitivity, now),
        )
        conn.execute("INSERT INTO routes (address_id, surface, route_ref, name)"
                     " VALUES ('ap', 'slack', 'CAP', 'ecf-default-ap')")  # fmt: skip
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", ME)):
            slack_admin.put_setting(conn, k, v, now, actor="test")


def _proposed(
    conn: sqlite3.Connection, clock: FakeClock, sid: str, facts: dict[str, Any] | None = None
) -> str:
    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                      content_hash="h", facts=json.dumps(facts or {}), subject="Invoice 42",
                      sender="billing@vendor-a.example")  # fmt: skip
    for to in (Status.CLASSIFIED, Status.PROPOSED):
        items.transition(conn, clock, StableId(sid), to, TransitionContext(), actor="test")
    return sid


def _status(conn: sqlite3.Connection, sid: str) -> str:
    return str(conn.execute("SELECT status FROM items WHERE stable_id = ?", (sid,)).fetchone()[0])


def _grants(conn: sqlite3.Connection, sid: str) -> list[str]:
    rows = conn.execute("SELECT status FROM grants WHERE stable_id = ? ORDER BY rowid", (sid,))
    return [r[0] for r in rows]


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")
    return [json.loads(r[0]) for r in rows]


def _click(
    db_path: Path, clock: FakeClock, conn: sqlite3.Connection, action: str, ref: str
) -> None:
    inbound = Inbound(slack_admin.identity, clock, lambda: db.connect(db_path),
                      lambda _e: None, lambda _t, _v: None)  # fmt: skip
    payload = {"type": "block_actions", "api_app_id": "A1", "team": {"id": "T1"},
               "user": {"id": ME}, "channel": {"id": "CAP"},
               "actions": [{"action_id": f"{action}#0", "value": ref}]}  # fmt: skip
    inbound.on_envelope(Envelope(f"e-{action}-{ref}-{clock.now()}", "interactive", payload,
                                 None, None))  # fmt: skip
    SlackReceiver(clock).run_once(conn)


def _verified_nonce(conn: sqlite3.Connection, clock: FakeClock, exc: StepupRequiredError) -> str:
    issued = stepup.issue(conn, clock, FakeStepper(), exc.extra["purpose"], exc.extra["target"])
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    return issued.nonce_id


class FakeExecutor:
    def __init__(self, fail: int = 0) -> None:
        self.ran: list[tuple[str, list[str]]] = []
        self.fail = fail

    def __call__(self, conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row,
                 actions: list[Planned]) -> list[str]:  # fmt: skip
        if self.fail:
            self.fail -= 1
            raise OSError("server said no")
        self.ran.append((item["stable_id"], [a.name for a in actions]))
        return [approvals.describe(actions)]


# ---- requesting -----------------------------------------------------------------------------


def test_a_request_issues_a_grant_and_posts_a_card(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    grant = approvals.request(conn, clock, sid, ARCHIVE, member=ME)
    assert _status(conn, sid) == Status.AWAITING_APPROVAL and _grants(conn, sid) == ["issued"]
    expires = conn.execute("SELECT expires_at FROM grants").fetchone()[0]
    assert expires == to_ts(clock.now() + approvals.TTL_OTHER)
    [card] = _posts(conn)
    buttons = card["card"]["buttons"]
    assert [b["label"] for b in buttons][:2] == ["Approve: archive email", "Reject"]
    assert buttons[0]["ref"] == grant and card["card"]["title"] == "Approve? archive email"


def test_approval_needs_the_address_live(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = 'assist'")
    sid = _proposed(conn, clock, "a" * 64)
    with pytest.raises(PolicyDeniedError):
        approvals.request(conn, clock, sid, ARCHIVE)


def test_what_needs_step_up() -> None:
    assert not approvals.needs_stepup({}, ARCHIVE)
    assert approvals.needs_stepup(FRAUD, ARCHIVE)  # hiding a fraud item (OD-213)
    assert approvals.needs_stepup({}, SEND)
    assert approvals.needs_stepup({}, [Planned("delete")])  # anything irreversible
    assert not approvals.needs_stepup(FRAUD, [Planned("label", "suspicious")])


# ---- deciding -------------------------------------------------------------------------------


def test_a_reversible_approval_is_one_click_then_runs(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    grant = approvals.request(conn, clock, sid, ARCHIVE)
    _click(db_path, clock, conn, "approve", grant)
    assert _status(conn, sid) == Status.EXECUTING and _grants(conn, sid) == ["approved"]
    ex = FakeExecutor()
    assert execute.run_once(conn, clock, ex) and ex.ran == [(sid, ["archive"])]
    assert _status(conn, sid) == Status.EXECUTED and _grants(conn, sid) == ["consumed"]
    assert _posts(conn)[-1]["card"]["title"] == "Done: archive email"
    with pytest.raises(ConflictError):  # the first decision won
        approvals.reject(conn, clock, sid, actor="os_user")


def test_a_send_clicked_in_slack_waits_for_your_computer(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(conn, clock)
    n = FakeNotifier()
    monkeypatch.setattr(approvals, "desktop", n)
    sid = _proposed(conn, clock, "a" * 64)
    grant = approvals.request(conn, clock, sid, SEND)
    assert conn.execute("SELECT expires_at FROM grants").fetchone()[0] == to_ts(
        clock.now() + approvals.TTL_SEND)  # fmt: skip
    _click(db_path, clock, conn, "approve", grant)
    assert _status(conn, sid) == Status.AWAITING_STEPUP and _grants(conn, sid) == ["issued"]
    assert n.sent and "ecf approve aaaaaaaa" in n.sent[0][1]
    assert _posts(conn)[-1]["card"]["title"] == "Queued for your computer (1 waiting)"
    with pytest.raises(StepupRequiredError) as ei:
        approvals.approve(conn, clock, n, "aaaaaaaa", actor="os_user")
    assert ei.value.extra == {"purpose": "approve", "target": {"grant_id": grant}}
    issued = stepup.issue(conn, clock, FakeStepper(), "approve", {"grant_id": grant})
    assert issued.prompt.startswith(
        "ecf: send template 'received': the email from billing@vendor-a.example"
    )
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    r = approvals.approve(conn, clock, n, "aaaaaaaa", actor="os_user", nonce=issued.nonce_id)
    assert r["status"] == Status.EXECUTING  # a standard address: no delay


def test_hiding_a_fraud_item_needs_step_up_even_from_the_cli(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64, FRAUD)
    approvals.request(conn, clock, sid, ARCHIVE)
    with pytest.raises(StepupRequiredError) as ei:
        approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")
    nonce = _verified_nonce(conn, clock, ei.value)
    approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user", nonce=nonce)
    assert _status(conn, sid) == Status.EXECUTING


def test_a_queued_approval_can_still_be_rejected(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    grant = approvals.request(conn, clock, sid, SEND)
    _click(db_path, clock, conn, "approve", grant)
    _click(db_path, clock, conn, "reject", grant)
    assert _status(conn, sid) == Status.REJECTED and _grants(conn, sid) == ["voided"]
    assert _posts(conn)[-1]["card"]["title"] == "Rejected: nothing was done"


def test_a_stale_button_is_refused_and_you_are_told(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    grant = approvals.request(conn, clock, sid, ARCHIVE)
    approvals.reject(conn, clock, sid, actor="os_user")
    _click(db_path, clock, conn, "approve", grant)
    assert _status(conn, sid) == Status.REJECTED
    note = _posts(conn)[-1]
    assert note["op"] == "ephemeral" and note["text"].startswith("Not done:")
    events = [r[0] for r in conn.execute("SELECT event FROM audit")]
    assert "slack.click_failed" in events


# ---- the 10-minute delay --------------------------------------------------------------------


def _delayed_send(conn: sqlite3.Connection, clock: FakeClock) -> str:
    _setup(conn, clock, sensitivity="high")
    sid = _proposed(conn, clock, "a" * 64)
    approvals.request(conn, clock, sid, SEND)
    with pytest.raises(StepupRequiredError) as ei:
        approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")
    nonce = _verified_nonce(conn, clock, ei.value)
    r = approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user", nonce=nonce)
    assert r["status"] == Status.DELAYED
    return sid


def test_a_high_send_waits_ten_minutes_of_awake_time(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _delayed_send(conn, clock)
    assert _posts(conn)[-1]["card"]["title"] == "Sending in 10 minute(s) unless you cancel"
    approvals.advance_delays(conn, clock, 0, woke=False)  # asleep: wall time passes, awake doesn't
    approvals.advance_delays(conn, clock, 300, woke=False)
    assert _status(conn, sid) == Status.DELAYED
    approvals.advance_delays(conn, clock, 0, woke=True)  # after a wake: said again
    assert _posts(conn)[-1]["card"]["title"] == "Sending in 5 minute(s) unless you cancel"
    assert approvals.advance_delays(conn, clock, 301, woke=False) == 1
    assert _status(conn, sid) == Status.EXECUTING
    assert conn.execute("SELECT count(*) FROM delays").fetchone()[0] == 0


def test_cancel_during_the_delay(conn: sqlite3.Connection, db_path: Path, clock: FakeClock) -> None:
    sid = _delayed_send(conn, clock)
    _click(db_path, clock, conn, "cancel", sid)
    assert _status(conn, sid) == Status.CANCELLED and _grants(conn, sid) == ["voided"]
    assert conn.execute("SELECT count(*) FROM delays").fetchone()[0] == 0
    assert approvals.advance_delays(conn, clock, 900, woke=False) == 0
    with pytest.raises(ConflictError):
        approvals.cancel(conn, clock, sid, actor="os_user")


# ---- expiry ---------------------------------------------------------------------------------


def test_an_expired_approval_is_offered_once_more(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    first = approvals.request(conn, clock, sid, ARCHIVE)
    clock.advance(13 * 86400)
    assert approvals.expire(conn, clock) == 0
    clock.advance(2 * 86400)
    assert approvals.expire(conn, clock) == 1
    assert _status(conn, sid) == Status.AWAITING_APPROVAL
    assert _grants(conn, sid) == ["voided", "issued"]
    assert _posts(conn)[-1]["card"]["title"] == "Expired, decide again"
    with pytest.raises(GrantInvalidError):  # the old button's grant is gone
        approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user", grant_id=first)
    clock.advance(15 * 86400)
    approvals.expire(conn, clock)
    assert _status(conn, sid) == Status.EXPIRED  # a second expiry: the daily summary lists it


# ---- approve --pending ----------------------------------------------------------------------


def test_pending_batches_non_sends_under_one_step_up(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    _setup(conn, clock)
    hide = _proposed(conn, clock, "a" * 64, FRAUD)
    send = _proposed(conn, clock, "b" * 64)
    for sid, acts in ((hide, ARCHIVE), (send, SEND)):
        _click(db_path, clock, conn, "approve", approvals.request(conn, clock, sid, acts))
        clock.advance(1)
    p = approvals.pending(conn)
    assert [b["id"] for b in p["batch"]] == [hide] and [s["id"] for s in p["sends"]] == [send]
    with pytest.raises(InvalidInputError, match="send"):
        approvals.approve_pending(conn, clock, FakeNotifier(), [hide, send], nonce=None)
    with pytest.raises(StepupRequiredError) as ei:
        approvals.approve_pending(conn, clock, FakeNotifier(), [hide], nonce=None)
    nonce = _verified_nonce(conn, clock, ei.value)
    [r] = approvals.approve_pending(conn, clock, FakeNotifier(), [hide], nonce=nonce)
    assert r["status"] == Status.EXECUTING and _status(conn, send) == Status.AWAITING_STEPUP


# ---- running, failing, requeue --------------------------------------------------------------


def test_a_failing_action_retries_then_fails_and_can_be_requeued(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    approvals.request(conn, clock, sid, ARCHIVE)
    approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")
    ex = FakeExecutor(fail=3)
    for _ in range(approvals.EXECUTE_ATTEMPTS):
        assert execute.run_once(conn, clock, ex)
        clock.advance(3600)
    assert _status(conn, sid) == Status.FAILED
    assert "Retry: ecf item requeue aaaaaaaa" in _posts(conn)[-1]["card"]["title"]
    approvals.requeue(conn, clock, sid, actor="os_user", nonce=None)
    assert execute.run_once(conn, clock, ex)
    assert _status(conn, sid) == Status.EXECUTED and ex.ran == [(sid, ["archive"])]


def test_v12_has_no_real_executor_for_approvals(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    approvals.request(conn, clock, sid, ARCHIVE)
    approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")
    for _ in range(approvals.EXECUTE_ATTEMPTS):
        execute.run_once(conn, clock, execute.unavailable)
        clock.advance(3600)
    assert _status(conn, sid) == Status.FAILED
    why = conn.execute("SELECT data FROM audit WHERE event = 'action.failed'").fetchone()[0]
    assert "not available until V1.3" in why


def test_requeuing_a_send_needs_step_up(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    approvals.request(conn, clock, sid, SEND)
    with pytest.raises(StepupRequiredError) as ei:
        approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")
    approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user",
                      nonce=_verified_nonce(conn, clock, ei.value))  # fmt: skip
    for _ in range(approvals.EXECUTE_ATTEMPTS):
        execute.run_once(conn, clock, FakeExecutor(fail=1))
        clock.advance(3600)
    with pytest.raises(StepupRequiredError) as ei:
        approvals.requeue(conn, clock, sid, actor="os_user", nonce=None)
    approvals.requeue(conn, clock, sid, actor="os_user",
                      nonce=_verified_nonce(conn, clock, ei.value))  # fmt: skip
    assert _status(conn, sid) == Status.EXECUTING


def test_the_decision_routes(conn: sqlite3.Connection, db_path: Path, clock: FakeClock) -> None:
    import anyio  # noqa: PLC0415
    import httpx  # noqa: PLC0415

    from ecf_server.api import ServiceState, create_app  # noqa: PLC0415

    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    approvals.request(conn, clock, sid, SEND)
    state = ServiceState(install="t", token="tok", started_at="2026-10-01T12:00:00.000000Z",
                         clock=clock, db_path=db_path)  # fmt: skip

    async def call(path: str, body: Any = None) -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.post(path, json=body or {},
                                headers={"Authorization": "Bearer tok"})  # fmt: skip

    r = anyio.run(call, "/v1/items/aaaaaaaa/approve")
    assert r.status_code == 403 and r.json()["code"] == "stepup_required"
    assert r.json()["target"]["grant_id"]
    assert anyio.run(call, "/v1/approvals/pending").json() == {"batch": [], "more": 0, "sends": []}
    r = anyio.run(call, "/v1/items/aaaaaaaa/reject")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert _status(conn, sid) == Status.REJECTED
