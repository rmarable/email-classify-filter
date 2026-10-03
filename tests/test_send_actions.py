"""Approved sends carried out (V1.5 step 3a; OD-317, OD-321, OD-322, OD-325): template replies and
internal forwards, the checks at execution, the reply limits, and an unknown outcome settled."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from email import message_from_bytes, policy
from typing import Any

import pytest

from ecf.errors import StepupRequiredError
from ecf_server import actor, approvals, decide, probe, send, send_actions
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.mail.fake import FakeMailSource, FakeSender
from ecf_server.notify import FakeNotifier
from tests.test_approvals import _verified_nonce  # pyright: ignore[reportPrivateUsage]
from tests.test_decide import item_row
from tests.test_mailbox_actions import (
    _address,  # pyright: ignore[reportPrivateUsage]
    _mail_item,  # pyright: ignore[reportPrivateUsage]
    _run,  # pyright: ignore[reportPrivateUsage]
)
from tests.test_outbound_plan import TEMPLATES, _config  # pyright: ignore[reportPrivateUsage]

REPLY: dict[str, Any] = {"category": "invoice", "priority": "normal", "requires_action": True,
         "requires_reply": True, "payment_related": False, "deadline_mentioned": False,
         "sender_type": "vendor", "fraud_risk": "none"}  # fmt: skip


def _facts(**kw: Any) -> dict[str, Any]:
    triggers: dict[str, Any] = {}
    return {"triggers": triggers, "auth_result": "pass", "sender_seen_before": True,
            "from_count": 1, "sender_hash": "s1",
            "thread_root": "<root@vendor-a.example>"} | kw  # fmt: skip


@pytest.fixture
def smtp() -> Iterator[FakeSender]:
    fake = FakeSender()
    send_actions.sender_for = lambda _conn, _aid: fake
    yield fake
    send_actions.sender_for = None


@pytest.fixture
def src(conn: sqlite3.Connection, clock: FakeClock) -> FakeMailSource:
    _address(conn, clock)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET outbound = 1, preset = 'B',"
                     " smtp_host = 'smtp.acme.example', smtp_port = 465")  # fmt: skip
    _config(conn, "templates", TEMPLATES)
    _config(conn, "forward_allow_list", [{"id": "ap_lead", "address": "lead@acme.example"}])
    r = probe.ProbeResult(
        roles={"\\Sent": "Sent", "\\Drafts": "Drafts"},
        custom_keywords=True,
        move=True,
        uidplus=True,
        condstore=True,
        append_limit=None,
        saves_sent=None,
        max_message_bytes=None,
        max_size_source=None,
    )
    with write_tx(conn):
        probe.store(conn, clock, "ap", "imap.acme.example", r)
    return FakeMailSource()


def _approved(conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource, n: int,
              action: str, target: str, facts: dict[str, Any] | None = None) -> str:  # fmt: skip
    sid = _mail_item(conn, clock, src, n, REPLY, facts or _facts(), src.deliver)
    with write_tx(conn):
        conn.execute("UPDATE items SET sender_name = 'Pat', subject = 'Invoice 42'"
                     " WHERE stable_id = ?", (sid,))  # fmt: skip
    item = item_row(conn, sid)
    ctx, p = decide.plan_for(conn, item)
    actor.decide_one(conn, clock, item, ctx, p,
                     {"action": action, "target": target, "reason": "r"}, frozenset(),
                     local=False)  # fmt: skip
    assert item_row(conn, sid)["status"] == "awaiting_approval", item_row(conn, sid)["proposal"]
    with pytest.raises(StepupRequiredError) as ei:
        approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")
    approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user",
                      nonce=_verified_nonce(conn, clock, ei.value))  # fmt: skip
    return sid


def _sent_msg(smtp: FakeSender, i: int = -1) -> Any:
    return message_from_bytes(smtp.sent[i][2], policy=policy.default)


def test_a_template_reply_is_sent_once_with_ecf_headers(
    conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource, smtp: FakeSender
) -> None:
    sid = _approved(conn, clock, src, 0, "reply_template", "ack")
    _run(conn, clock, src)
    assert item_row(conn, sid)["status"] == "executed"
    [(frm, rcpt, _raw)] = smtp.sent
    assert (frm, rcpt) == ("ap@acme.example", ("news@vendor-a.example",))
    m = _sent_msg(smtp)
    assert str(m["Subject"]) == "Re: Invoice 42" and str(m["Auto-Submitted"]) == "auto-replied"
    assert str(m["X-ECF-Install"]).endswith(".0")
    assert str(m["In-Reply-To"]) == "<contract-0@synthetic.acme.example>"
    assert "Hello Pat," in m.get_content()
    row = conn.execute("SELECT * FROM sent").fetchone()
    assert (row["status"], row["kind"], row["stable_id"]) == ("sent", "reply", sid)
    assert len(src.find_in("Sent", str(m["Message-ID"]))) == 1  # ecf's copy (provider unknown)
    done = json.loads(item_row(conn, sid)["proposal"])["done"]
    assert {"name": "reply_template", "target": "ack", "to": "news@vendor-a.example",
            "message_id": str(m["Message-ID"])} in done  # fmt: skip


def test_one_template_reply_per_thread_and_per_sender_a_week(
    conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource, smtp: FakeSender
) -> None:
    _approved(conn, clock, src, 0, "reply_template", "ack")
    _run(conn, clock, src)
    same_sender = _approved(conn, clock, src, 1, "reply_template", "ack",
                            _facts(thread_root="<other@vendor-a.example>"))  # fmt: skip
    _run(conn, clock, src)
    clock.advance(8 * 86400)  # past the sender's week
    same_thread = _approved(conn, clock, src, 2, "reply_template", "ack")
    _run(conn, clock, src)
    other = _approved(conn, clock, src, 3, "reply_template", "ack",
                      _facts(sender_hash="s3", thread_root="<new@vendor-b.example>"))  # fmt: skip
    _run(conn, clock, src)
    whys = [json.loads(r[0])["why"] for r in conn.execute(
        "SELECT data FROM audit WHERE event = 'action.failed' ORDER BY id")]  # fmt: skip
    assert whys == ["this sender got a template reply in the last 7 days",
                    "this thread already got a template reply"]  # fmt: skip
    assert (
        item_row(conn, same_sender)["status"] == item_row(conn, same_thread)["status"] == "failed"
    )
    assert item_row(conn, other)["status"] == "executed" and len(smtp.sent) == 2


def test_a_forward_attaches_the_email_as_it_is(
    conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource, smtp: FakeSender
) -> None:
    sid = _approved(conn, clock, src, 0, "forward_internal", "ap_lead")
    _run(conn, clock, src)
    assert item_row(conn, sid)["status"] == "executed"
    assert smtp.sent[0][1] == ("lead@acme.example",)
    m = _sent_msg(smtp)
    assert str(m["Subject"]) == "Fwd: Invoice 42" and str(m["Auto-Submitted"]) == "auto-generated"
    parts = [p.get_content_type() for p in m.walk()]
    assert parts == ["multipart/mixed", "text/plain", "message/rfc822", "text/plain"]
    original = src.fetch(src.uids_after(0)[0])
    assert original is not None and original in smtp.sent[0][2]  # byte for byte


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ("UPDATE addresses SET outbound = 0", "outbound is off"),
        ("UPDATE addresses SET stage = 'assist'", "sends need live"),
        ("UPDATE addresses SET smtp_host = NULL", "no SMTP server"),
        ("UPDATE items SET facts = json_set(facts, '$.bulk_signal', 1)", "bulk"),
    ],
)
def test_checks_at_execution_refuse(conn: sqlite3.Connection, clock: FakeClock,
                                    src: FakeMailSource, smtp: FakeSender, change: str,
                                    why: str) -> None:  # fmt: skip
    sid = _approved(conn, clock, src, 0, "reply_template", "ack")
    with write_tx(conn):
        conn.execute(change)
    _run(conn, clock, src)
    assert item_row(conn, sid)["status"] == "failed" and smtp.sent == []
    events = [json.loads(r[0]) for r in conn.execute(
        "SELECT data FROM audit WHERE event = 'action.failed'")]  # fmt: skip
    assert any(why in e["why"] for e in events), events


def test_a_reply_to_a_monitored_address_is_refused(
    conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource, smtp: FakeSender
) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('ops', 'news@vendor-a.example', 'standard', 'A', 't')")  # fmt: skip
    sid = _approved(conn, clock, src, 0, "reply_template", "ack")
    _run(conn, clock, src)
    assert item_row(conn, sid)["status"] == "failed" and smtp.sent == []


def test_an_unknown_outcome_waits_then_settles_from_sent(
    conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource, smtp: FakeSender
) -> None:
    sid = _approved(conn, clock, src, 0, "reply_template", "ack")
    smtp.fail = "unknown"
    _run(conn, clock, src)
    assert item_row(conn, sid)["status"] == "failed_unknown" and len(smtp.sent) == 1
    with write_tx(conn):
        conn.execute("UPDATE probe SET saves_sent = 1")
    src.append("Sent", smtp.sent[0][2])  # the provider kept it: it went
    assert send.settle(conn, clock, src, "ap").sent == 1
    assert item_row(conn, sid)["status"] == "executed"


def test_an_unknown_outcome_missing_twice_fails_the_item(
    conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource, smtp: FakeSender
) -> None:
    sid = _approved(conn, clock, src, 0, "reply_template", "ack")
    smtp.fail = "unknown"
    _run(conn, clock, src)
    with write_tx(conn):
        conn.execute("UPDATE probe SET saves_sent = 1")
    send.settle(conn, clock, src, "ap")
    send.settle(conn, clock, src, "ap")
    assert item_row(conn, sid)["status"] == "failed"


def test_a_lost_lease_sends_nothing(conn: sqlite3.Connection, clock: FakeClock,
                                    src: FakeMailSource, smtp: FakeSender) -> None:  # fmt: skip
    sid = _approved(conn, clock, src, 0, "reply_template", "ack")
    _run(conn, clock, src, lost=lambda: True)
    assert smtp.sent == [] and item_row(conn, sid)["status"] == "executing"
