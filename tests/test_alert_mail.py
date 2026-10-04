"""Alert email (V1.5 step 7a; OD-100, OD-316, OD-320, OD-328, OD-333 to OD-336): turning it on and
off, the outbox, the caps and roll-ups, retries and the fallback to Slack, the email sweep of open
and resolved alerts, and the crash-loop email."""

from __future__ import annotations

import json
import sqlite3
from email import message_from_bytes
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import ConflictError, InvalidInputError, StepupRequiredError
from ecf_server import addresses, alert_mail, alerts, health, own_mail, slack_admin, stepup
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.mail.fake import FakeSender
from ecf_server.message import parse
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.stepper import FakeStepper
from tests.test_addresses import TOKEN, call, make_state

ME = "U0ME1"
DEST = "owner@home.example"


def _slack(conn: sqlite3.Connection, clock: FakeClock) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", ME),
                     ("slack_summary_channel", "CSUM")):  # fmt: skip
            slack_admin.put_setting(conn, k, v, now, actor="test")


def _addresses(conn: sqlite3.Connection) -> None:
    with write_tx(conn):
        for aid in ("ap", "ar"):
            conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset,"
                         " created_at, smtp_host, smtp_port) VALUES (?, ?, 'standard', 'A', 't',"
                         " 'smtp.acme.example', 465)", (aid, f"{aid}@acme.example"))  # fmt: skip


def _nonce(conn: sqlite3.Connection, clock: FakeClock, call: Any) -> str:
    with pytest.raises(StepupRequiredError) as ei:
        call(None)
    issued = stepup.issue(conn, clock, FakeStepper(), str(ei.value.extra["purpose"]),
                          ei.value.extra["target"])  # fmt: skip
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    return issued.nonce_id


def _on(conn: sqlite3.Connection, clock: FakeClock, n: FakeNotifier, *, frm: str = "ap",
        to: str = DEST) -> dict[str, Any]:  # fmt: skip
    def call(nonce: str | None) -> dict[str, Any]:
        return alerts.set_email(conn, clock, n, frm, to, nonce=nonce)

    return call(_nonce(conn, clock, call))


def _outbox(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM alert_outbox ORDER BY id").fetchall()


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")
    return [json.loads(r[0]) for r in rows]


def _sent(fake: FakeSender) -> list[Message]:
    return [message_from_bytes(raw) for _frm, _rcpt, raw in fake.sent]


def _drain(conn: sqlite3.Connection, clock: FakeClock, n: FakeNotifier, fake: FakeSender) -> int:
    return alert_mail.drain(conn, clock, n, lambda _c, _aid: fake)


@pytest.fixture
def ready(conn: sqlite3.Connection, clock: FakeClock) -> FakeNotifier:
    _addresses(conn)
    _slack(conn, clock)
    n = FakeNotifier()
    _on(conn, clock, n)
    with write_tx(conn):  # start the cap tests from an empty outbox
        conn.execute("DELETE FROM alert_outbox")
    return n


def test_turning_it_on_needs_step_up_and_sends_a_notice_and_a_test(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _addresses(conn)
    _slack(conn, clock)
    n = FakeNotifier()
    with pytest.raises(StepupRequiredError) as ei:
        alerts.set_email(conn, clock, n, "ap", DEST, nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "alerts_email", ei.value.extra["target"])
    assert issued.prompt.startswith(f"ecf: send alert email from ap@acme.example to {DEST}")
    r = _on(conn, clock, n)
    assert r["email"] == {"from": "ap", "from_email": "ap@acme.example", "to": DEST}
    assert r["default"] == ["slack", "email"]  # the default routes gain email (OD-333)
    assert [row["subject"] for row in _outbox(conn)] == [
        "[ecf-alert] Security Notice", "[ecf-alert] Test"]  # fmt: skip
    fake = FakeSender()
    assert _drain(conn, clock, n, fake) == 2
    notice, test = _sent(fake)
    assert notice["From"] == "ap@acme.example" and notice["To"] == DEST
    assert notice["Auto-Submitted"] == "auto-generated"  # OD-320
    assert notice["X-ECF-Install"] and notice["Message-ID"].endswith("@acme.example>")
    assert "Alert email now goes from ap@acme.example (ap)" in notice.get_payload()
    assert test["Subject"] == "[ecf-alert] Test"
    kinds = {r["kind"]: r["status"] for r in conn.execute("SELECT kind, status FROM sent")}
    assert kinds == {"alert": "sent"}
    assert {r["state"] for r in _outbox(conn)} == {"sent"}


def test_the_destination_may_not_be_a_watched_address(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _addresses(conn)
    n = FakeNotifier()
    with pytest.raises(InvalidInputError, match="watched address"):
        alerts.set_email(conn, clock, n, "ap", "AR@acme.example", nonce=None)
    with pytest.raises(InvalidInputError, match="plain email address"):
        alerts.set_email(conn, clock, n, "ap", "Owner <owner@home.example>", nonce=None)
    with pytest.raises(InvalidInputError, match="alert email is off"):
        alerts.set_routes(conn, clock, n, None, ["email"], nonce=None)


def test_a_new_destination_tells_the_old_one(conn: sqlite3.Connection, clock: FakeClock,
                                             ready: FakeNotifier) -> None:  # fmt: skip
    _on(conn, clock, ready, frm="ar", to="other@home.example")
    old = [r for r in _outbox(conn) if r["destination"] == DEST]
    assert [r["subject"] for r in old] == ["[ecf-alert] Security Notice"]
    assert "It no longer comes here" in old[0]["body"]
    assert alert_mail.config(conn) == alert_mail.EmailConfig("ar", "ar@acme.example",
                                                             "other@home.example")  # fmt: skip


def test_turning_it_off_takes_email_out_of_every_route(
    conn: sqlite3.Connection, clock: FakeClock, ready: FakeNotifier
) -> None:
    def route(nonce: str | None) -> dict[str, Any]:
        return alerts.set_routes(conn, clock, ready, "slack", ["email"], nonce=nonce)

    assert route(_nonce(conn, clock, route))["classes"]["slack"] == ["email"]
    with write_tx(conn):
        conn.execute("DELETE FROM alert_outbox")

    def off(nonce: str | None) -> dict[str, Any]:
        return alerts.email_off(conn, clock, ready, nonce=nonce)

    r = off(_nonce(conn, clock, off))
    assert r["email"] is None and r["default"] == ["slack"] and r["classes"]["slack"] == []
    [last] = _outbox(conn)  # the old destination hears it; the notice after it isn't emailed
    assert last["destination"] == DEST and "turned off" in last["body"]
    with pytest.raises(ConflictError, match="already off"):
        alerts.email_off(conn, clock, ready, nonce=None)


def test_the_sending_address_cant_be_removed(conn: sqlite3.Connection, clock: FakeClock,
                                             ready: FakeNotifier) -> None:  # fmt: skip
    del ready
    with pytest.raises(ConflictError, match="sends ecf's alert email"):
        addresses.remove_address(conn, clock, MemorySecretStore(), "ap", actor="os_user")
    addresses.remove_address(conn, clock, MemorySecretStore(), "ar", actor="os_user")


def test_caps_hold_alerts_for_an_hourly_roll_up(conn: sqlite3.Connection, clock: FakeClock,
                                               ready: FakeNotifier) -> None:  # fmt: skip
    for i in range(12):
        alert_mail.queue(conn, clock, "[ecf-alert] System Error", f"error {i}", slack_done=False)
    states = [r["state"] for r in _outbox(conn)]
    assert states == ["queued"] * 10 + ["rolled_up"] * 2  # OD-100
    held = [p for p in _posts(conn) if p["key"].startswith("alert_mail:")]
    assert [p["card"]["text"] for p in held] == ["error 10", "error 11"]  # Slack has them now
    for head in ("[ecf-alert] Operator Input Needed: x (ap)", "[ecf-alert] Security Notice"):
        for _ in range(10):  # two more types fill the 30-an-hour cap (OD-328)
            alert_mail.queue(conn, clock, head, "x", slack_done=True)
    alert_mail.queue(conn, clock, "[ecf-alert] Test", "t", slack_done=True)
    assert [r["state"] for r in _outbox(conn)][-1] == "rolled_up"
    assert sum(r["state"] == "queued" for r in _outbox(conn)) == 30
    fake = FakeSender()
    while _drain(conn, clock, ready, fake):
        pass
    assert len(fake.sent) == 30
    clock.advance(3600)
    while _drain(conn, clock, ready, fake):
        pass
    rollups = [m for m in _sent(fake) if "more in the last hour" in m["Subject"]]
    assert sorted(m["Subject"] for m in rollups) == [  # outside the caps (OD-336)
        "[ecf-alert] System Error: 2 more in the last hour",
        "[ecf-alert] Test: 1 more in the last hour",
    ]
    assert {r["state"] for r in _outbox(conn) if not r["rollup"]} == {"sent", "in_rollup"}


def test_retries_then_fallback_to_slack_and_a_system_error(
    conn: sqlite3.Connection, clock: FakeClock, ready: FakeNotifier
) -> None:
    fake = FakeSender()
    fake.fail = "temp"
    alert_mail.queue(conn, clock, "[ecf-alert] System Error", "boom", slack_done=False)
    assert _drain(conn, clock, ready, fake) == 1
    assert _outbox(conn)[0]["state"] == "queued"
    clock.advance(29)
    assert _drain(conn, clock, ready, fake) == 0  # the second try waits 30 s
    clock.advance(1)
    assert _drain(conn, clock, ready, fake) == 1
    clock.advance(120)
    assert _drain(conn, clock, ready, fake) == 1  # the third try fails: fallback (OD-335)
    [row] = _outbox(conn)
    assert (row["state"], row["attempts"]) == ("fallback", 3)
    assert any(p["card"]["text"] == "boom" for p in _posts(conn))
    [open_] = health.open_alerts(conn)
    assert open_["kind"] == "alert_email" and "isn't getting through" in open_["detail"]
    fake.fail = None
    alert_mail.queue(conn, clock, "[ecf-alert] Test", "t", slack_done=True)
    _drain(conn, clock, ready, fake)
    assert health.open_alerts(conn) == []  # the next email through resolves it


def test_a_refused_send_falls_back_at_once(conn: sqlite3.Connection, clock: FakeClock,
                                          ready: FakeNotifier) -> None:  # fmt: skip
    fake = FakeSender()
    fake.fail = "refused"
    alert_mail.queue(conn, clock, "[ecf-alert] System Error", "boom", slack_done=True)
    _drain(conn, clock, ready, fake)
    [row] = _outbox(conn)
    assert row["state"] == "fallback" and "550" in row["error"]
    assert not [p for p in _posts(conn) if p["key"].startswith("alert_mail:")]  # Slack had it


def test_nothing_is_tried_while_the_sender_has_a_mail_alert_open(
    conn: sqlite3.Connection, clock: FakeClock, ready: FakeNotifier
) -> None:
    health.open_alert(conn, clock, ready, "login_rejected", "ap", "ap: rejected")
    alerts.email_sweep(conn, clock)
    fake = FakeSender()
    _drain(conn, clock, ready, fake)
    assert fake.connects == 0  # OD-328
    [row] = _outbox(conn)
    assert row["state"] == "fallback" and row["subject"] == (
        "[ecf-alert] Mailbox Login Rejected (ap)")  # fmt: skip
    assert all(a["kind"] != "alert_email" for a in health.open_alerts(conn))
    assert alert_mail.send_now(conn, clock, lambda _c, _a: fake, "s", "b") is False


def test_open_and_resolved_alerts_are_emailed_when_routed(
    conn: sqlite3.Connection, clock: FakeClock, ready: FakeNotifier
) -> None:
    health.open_alert(conn, clock, ready, "send_limit", "ar", "ar: 25 sends in an hour")
    assert alerts.email_sweep(conn, clock) == 1 and alerts.email_sweep(conn, clock) == 0
    health.resolve_alert(conn, clock, ready, "send_limit", "ar")
    alerts.email_sweep(conn, clock)
    assert [(r["subject"], r["body"], r["slack_done"]) for r in _outbox(conn)] == [
        ("[ecf-alert] Operator Input Needed: send limit reached (ar)",
         "ar: 25 sends in an hour", 1),
        ("[ecf-alert] Resolved: Operator Input Needed: send limit reached (ar)",
         "Working again.", 1),
    ]  # fmt: skip
    assert alerts.sweep(conn, clock) == 1  # the Slack sweep knows every kind (was a KeyError)


def test_alerts_routed_to_slack_only_are_marked_not_emailed(
    conn: sqlite3.Connection, clock: FakeClock, ready: FakeNotifier
) -> None:
    def route(nonce: str | None) -> dict[str, Any]:
        return alerts.set_routes(conn, clock, ready, "system", ["slack"], nonce=nonce)

    route(_nonce(conn, clock, route))
    with write_tx(conn):
        conn.execute("DELETE FROM alert_outbox")
    health.open_alert(conn, clock, ready, "models_api", None, "Models API failed")
    assert alerts.email_sweep(conn, clock) == 1
    assert _outbox(conn) == []
    alerts.event(conn, clock, ready, "operator_input", "x", condition="y (ap)")
    assert [r["subject"] for r in _outbox(conn)] == ["[ecf-alert] Operator Input Needed: y (ap)"]


def test_every_alert_kind_has_a_title_and_a_class() -> None:
    assert set(health.TITLES) <= set(alerts.TITLES)
    assert set(alerts.TITLES) == set(alerts.CLASS_OF)


def test_crash_loop_email_goes_at_once(conn: sqlite3.Connection, clock: FakeClock,
                                       ready: FakeNotifier) -> None:  # fmt: skip
    del ready
    fake = FakeSender()
    assert alert_mail.send_now(conn, clock, lambda _c, _a: fake, "[ecf-alert] System Error",
                               "ecf stopped after 5 crashes") is True  # fmt: skip
    [m] = _sent(fake)
    assert m["Subject"] == "[ecf-alert] System Error"
    assert _outbox(conn)[0]["state"] == "sent"


def test_an_alert_coming_back_is_recognized_as_own_mail(
    conn: sqlite3.Connection, clock: FakeClock, ready: FakeNotifier
) -> None:
    fake = FakeSender()
    alert_mail.queue(conn, clock, "[ecf-alert] Test", "t", slack_done=True)
    _drain(conn, clock, ready, fake)
    [(_frm, _rcpt, raw)] = fake.sent
    assert own_mail.classify(conn, parse(raw), "pass") == own_mail.OWN


def test_the_subject_is_cut_at_99_characters(conn: sqlite3.Connection, clock: FakeClock,
                                             ready: FakeNotifier) -> None:  # fmt: skip
    del ready
    alert_mail.queue(conn, clock, "[ecf-alert] Operator Input Needed: " + "x" * 200, "b",
                     slack_done=True)  # fmt: skip
    assert len(_outbox(conn)[0]["subject"]) == 99


def test_the_routes(conn: sqlite3.Connection, db_path: Path, clock: FakeClock) -> None:
    _addresses(conn)
    st = make_state(db_path, None)
    r = call(st, "POST", "/v1/alerts/email", {"from": "ap", "to": DEST}, TOKEN)
    assert r.status_code == 403 and r.json()["code"] == "stepup_required"
    assert r.json()["target"] == {"from": "ap", "to": DEST}, r.text
    r = call(st, "POST", "/v1/alerts/email/off", {}, TOKEN)
    assert r.status_code == 409  # already off
    r = call(st, "GET", "/v1/alerts", None, TOKEN)
    assert r.status_code == 200 and r.json()["email"] is None
