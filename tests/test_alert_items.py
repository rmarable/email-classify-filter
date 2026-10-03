"""Alert email about mail (V1.5 step 7b; OD-114, OD-330, OD-334, OD-337, OD-338): Possible Fraud
Attempt and Regulatory Mail Notice per escalation, the hourly Unverified Payment Sender batch, the
"send scheduled in 10 minutes" email, and bounces, auto-replies and copies of alert email
recognized, labelled `alert_echo` and left."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from ecf.ids import AddressId, StableId
from ecf_server import (
    alert_items,
    alert_mail,
    alerts,
    approvals,
    install_identity,
    items,
    own_mail,
    policy,
    triggers,
)
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.message import parse
from ecf_server.outbound_msg import message_id_hash
from ecf_server.policy import Context
from tests.test_approvals import _delayed_send  # pyright: ignore[reportPrivateUsage]
from tests.test_policy import LABELS, STARTER

ALERT_ID = "<0123abcd.ecf@acme.example>"


def _email_on(conn: sqlite3.Connection, *, routes: list[str] | None = None) -> None:
    with write_tx(conn):
        conn.execute("INSERT OR IGNORE INTO addresses (address_id, email, sensitivity, preset,"
                     " created_at) VALUES ('ap', 'ap@acme.example', 'standard', 'A',"
                     " 't')")  # fmt: skip
        for k, v in ((alert_mail.FROM_KEY, "ap"), (alert_mail.TO_KEY, "owner@home.example"),
                     ("alerts.routes", routes or ["slack", "email"])):  # fmt: skip
            conn.execute("INSERT INTO settings (key, value, updated_at, updated_by)"
                         " VALUES (?, ?, 't', 'test')", (k, json.dumps(v)))  # fmt: skip


def _alert_sent(conn: sqlite3.Connection) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO sent (message_id_hash, address_id, content_hash, kind, sent_at,"
                     " message_id, status) VALUES (?, 'ap', 'h', 'alert', 't', ?, 'sent')",
                     (message_id_hash(ALERT_ID), ALERT_ID))  # fmt: skip


def _outbox(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM alert_outbox ORDER BY id").fetchall()


def _item(conn: sqlite3.Connection, clock: FakeClock, sid: str, facts: dict[str, Any],
          cls: dict[str, Any] | None = None, *, escalate: bool = True,
          address: str = "ap") -> None:  # fmt: skip
    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId(address),
                      content_hash="h", facts=json.dumps(facts))  # fmt: skip
    with write_tx(conn):
        if cls is not None:
            conn.execute("UPDATE items SET classification = ? WHERE stable_id = ?",
                         (json.dumps(cls), sid))  # fmt: skip
        if escalate:
            conn.execute("INSERT INTO escalations (stable_id, address_id, state, created_at)"
                         " VALUES (?, ?, 'pending', ?)",
                         (sid, address, to_ts(clock.now())))  # fmt: skip


# ---- recognizing alert echoes (own_mail.alert_echo) ---------------------------------------------


def _install(conn: sqlite3.Connection) -> str:
    return install_identity.header_value(conn)


def _echo(conn: sqlite3.Connection, raw: str) -> str | None:
    data = raw.replace("\n", "\r\n").encode()
    return own_mail.alert_echo(conn, parse(data), data)


def test_a_copy_of_an_alert_is_recognized(conn: sqlite3.Connection) -> None:
    _alert_sent(conn)
    head = (
        "From: ap@acme.example\nTo: x@home.example\nSubject: [ecf-alert] Test\n"
        f"Message-ID: {ALERT_ID}\n"
    )
    assert _echo(conn, head + f"X-ECF-Install: {_install(conn)}\n\nt\n") == "copy"
    assert _echo(conn, head + f"X-ECF-Install: {'f' * 32}.0\n\nt\n") is None  # not ours


def test_a_bounce_citing_an_alert_in_its_returned_headers(conn: sqlite3.Connection) -> None:
    _alert_sent(conn)
    dsn = ("From: MAILER-DAEMON@mx.home.example\nTo: ap@acme.example\nSubject: Undelivered\n"
           "Message-ID: <dsn1@mx.home.example>\n"
           'Content-Type: multipart/report; report-type=delivery-status; boundary="b"\n\n'
           "--b\nContent-Type: text/plain\n\nNo such user\n"
           "--b\nContent-Type: message/delivery-status\n\nReporting-MTA: dns; mx\n\n"
           "Final-Recipient: rfc822; x@home.example\nAction: failed\nStatus: 5.1.1\n"
           "--b\nContent-Type: text/rfc822-headers\n\n"
           f"From: ap@acme.example\nMessage-ID: {ALERT_ID}\n--b--\n")  # fmt: skip
    assert _echo(conn, dsn) == "bounce"
    assert _echo(conn, dsn.replace(ALERT_ID, "<other@acme.example>")) is None


def test_an_auto_reply_citing_an_alert(conn: sqlite3.Connection) -> None:
    _alert_sent(conn)
    reply = ("From: owner@home.example\nTo: ap@acme.example\nSubject: Out of office\n"
             f"Message-ID: <r1@home.example>\nIn-Reply-To: {ALERT_ID}\n")  # fmt: skip
    assert _echo(conn, reply + "Auto-Submitted: auto-replied\n\nAway\n") == "auto_reply"
    assert _echo(conn, reply + "X-Autoreply: yes\n\nAway\n") == "auto_reply"
    assert _echo(conn, reply + "Auto-Submitted: no\n\nAway\n") is None
    assert _echo(conn, reply + "\nA person's reply\n") is None  # not automatic: a normal item


def test_trigger_9_skips_a_copy_of_an_alert_only(conn: sqlite3.Connection) -> None:
    raw = (f"From: ap@acme.example\nTo: x@home.example\nSubject: s\nMessage-ID: {ALERT_ID}\n"
           f"X-ECF-Install: {_install(conn)}\n\nt\n").replace("\n", "\r\n").encode()  # fmt: skip
    parsed = parse(raw)

    def fired(found: dict[str, Any]) -> bool:
        base: dict[str, Any] = {"from_domain": "acme.example", "from_org_domain": "acme.example",
                "reply_to_mismatch": False, "recipient_mismatch": False,
                "sender_confirmed": False, "sender_seen_before": True, "auth": {}}  # fmt: skip
        t = triggers.evaluate(parsed, triggers.scan(triggers.texts_of(parsed)), base | found,
                              org_domains=[], known_vendors=[],
                              duplicate_message_id=False)  # fmt: skip
        return any("X-ECF-Install" in r for r in t.facts()["triggers"]["fraud"])

    assert fired({"auth_result": "fail"})
    assert not fired({"auth_result": "fail", "alert_echo": "copy"})  # OD-338
    assert fired({"auth_result": "fail", "alert_echo": "bounce"})


# ---- labelled and left (policy) ------------------------------------------------------------------


def _ctx(facts: dict[str, Any], **cls: Any) -> Context:
    base: dict[str, Any] = {"category": "notification", "fraud_risk": "none",
                            "requires_reply": False, "requires_action": False, "automated": True,
                            "payment_related": False}  # fmt: skip
    known: dict[str, Any] = {"triggers": {}, "auth_result": "none", "sender_seen_before": True}
    return Context(classification=base | cls, facts=known | facts, sensitivity="standard",
                   rules=STARTER, action_policy={}, move_folders=frozenset())  # fmt: skip


def test_an_echo_is_labelled_and_left() -> None:
    p = policy.plan(_ctx({"alert_echo": "bounce"}), LABELS)
    assert p.rule_id == "alert_echo" and not p.to_actor
    assert [(a.name, a.target) for a in p.actions] == [("label", "alert_echo"), ("leave", None)]
    p = policy.plan(_ctx({"alert_echo": "auto_reply"}, category="other", requires_reply=True,
                         automated=False), LABELS)  # fmt: skip
    assert p.rule_id == "alert_echo" and not p.to_actor  # no actor, no reply to an auto-reply


def test_an_echo_with_a_fraud_or_regulatory_signal_keeps_its_plan() -> None:
    p = policy.plan(_ctx({"alert_echo": "bounce", "triggers": {"fraud": ["x"]}}), LABELS)
    assert p.rule_id == "fraud_guard" and any(a.name == "escalate" for a in p.actions)
    p = policy.plan(_ctx({"alert_echo": "bounce"}, fraud_risk="high"), LABELS)
    assert p.rule_id == "fraud_guard"
    p = policy.plan(_ctx({"alert_echo": "copy"}, category="regulatory"), LABELS)
    assert p.rule_id == "regulatory"


# ---- Possible Fraud Attempt and Regulatory Mail Notice ------------------------------------------


def test_fraud_and_regulatory_escalations_are_emailed_once(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _email_on(conn)
    _item(conn, clock, "f" * 64, {"triggers": {"fraud": ["bank_change"]}})
    _item(conn, clock, "r" * 64, {"triggers": {"regulator": ["irs"]}})
    _item(conn, clock, "m" * 64, {}, {"fraud_risk": "high", "category": "invoice"})
    _item(conn, clock, "b" * 64, {}, {"category": "bug_report"})  # urgent bug: not emailed
    _item(conn, clock, "e" * 64, {"triggers": {"fraud": ["x"]}, "alert_echo": "bounce"})
    assert alert_items.sweep(conn, clock) == 5 and alert_items.sweep(conn, clock) == 0
    got = [(r["subject"], r["body"].split(" (")[0]) for r in _outbox(conn)]
    assert got == [
        ("[ecf-alert] Possible Fraud Attempt", "ap: possible fraud in item ffffffff"),
        ("[ecf-alert] Regulatory Mail Notice", "ap: regulatory mail in item rrrrrrrr"),
        ("[ecf-alert] Possible Fraud Attempt", "ap: possible fraud in item mmmmmmmm"),
    ]
    assert "a fraud trigger" in _outbox(conn)[0]["body"]
    assert "fraud risk high" in _outbox(conn)[2]["body"]


def test_not_emailed_while_the_install_routes_lack_email(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _email_on(conn, routes=["slack"])
    _item(conn, clock, "f" * 64, {"triggers": {"fraud": ["bank_change"]}})
    assert alert_items.sweep(conn, clock) == 1  # handled, so no backlog when email is routed later
    assert _outbox(conn) == []


def test_more_than_ten_fraud_emails_an_hour_roll_up(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _email_on(conn)
    for i in range(12):
        _item(conn, clock, f"{i:02d}" * 32, {"triggers": {"fraud": ["x"]}})
    alert_items.sweep(conn, clock)
    assert [r["state"] for r in _outbox(conn)].count("rolled_up") == 2  # OD-114: up to 10 an hour


# ---- Unverified Payment Sender ------------------------------------------------------------------


def test_unverified_payment_on_high_addresses_is_batched_hourly(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _email_on(conn)
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('hi', 'hi@acme.example', 'high', 'A', 't')")  # fmt: skip
    unverified = {"triggers": {"unverified_payment": True}}
    _item(conn, clock, "1" * 64, unverified, escalate=False, address="hi")
    _item(conn, clock, "2" * 64, unverified, escalate=False, address="ap")  # standard: no email
    _item(conn, clock, "3" * 64, unverified | {"triggers": {"unverified_payment": True,
                                                            "fraud": ["x"]}},
          address="hi")  # escalated as fraud: its own email  # fmt: skip
    assert alert_items.unverified_batch(conn, clock) == 1
    [row] = _outbox(conn)
    assert row["subject"] == "[ecf-alert] Unverified Payment Sender"
    assert "hi: 1 (items 11111111); ecf inbox --address hi" in row["body"]
    clock.advance(60)
    _item(conn, clock, "4" * 64, unverified, escalate=False, address="hi")
    assert alert_items.unverified_batch(conn, clock) == 0  # at most one an hour
    clock.advance(3600)
    assert alert_items.unverified_batch(conn, clock) == 1
    assert "44444444" in _outbox(conn)[-1]["body"] and "11111111" not in _outbox(conn)[-1]["body"]


# ---- send scheduled in 10 minutes ---------------------------------------------------------------


def test_a_delayed_send_is_emailed_once(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _delayed_send(conn, clock)
    assert _outbox(conn) == []  # alert email off
    with write_tx(conn):
        conn.execute("DELETE FROM delays")
    _email_on(conn)
    with write_tx(conn):
        conn.execute("INSERT INTO delays (stable_id, grant_id, remaining_s, created_at)"
                     " VALUES (?, 'g', 600, 't')", (sid,))  # fmt: skip
    item = conn.execute("SELECT * FROM items WHERE stable_id = ?", (sid,)).fetchone()
    approvals._announce_delay(conn, clock, item, "g")  # pyright: ignore[reportPrivateUsage]
    approvals.advance_delays(conn, clock, 0, woke=True)  # a re-announcement isn't emailed again
    [row] = _outbox(conn)
    assert row["subject"] == alerts.title("operator_input", "send scheduled in 10 minutes (ap)")
    assert f"ecf cancel {sid[:8]}" in row["body"]


@pytest.mark.parametrize("kind", ["fraud_guard", "regulatory"])
def test_starter_rule_ids_the_kinds_rely_on(kind: str) -> None:
    assert kind in {r.id for r in STARTER.rules}
