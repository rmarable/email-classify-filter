"""Drafts and sends resolved and bound into the grant (V1.5 step 2a; OD-317): the payload, the
action hash covering it, the check at execution, and suppressed sends."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from ecf.ids import StableId
from ecf_server import actor, decide, outbound_plan
from ecf_server.actions import Planned, action_hash
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from tests.test_decide import item_row, make_address, make_classified

INVOICE: dict[str, Any] = {"category": "invoice", "priority": "normal", "requires_action": True,
           "requires_reply": True, "payment_related": False, "deadline_mentioned": False,
           "sender_type": "vendor", "fraud_risk": "none"}  # fmt: skip
FACTS: dict[str, Any] = {"triggers": {}, "auth_result": "pass", "sender_seen_before": True,
         "from_count": 1}  # fmt: skip
TEMPLATES = {"version": 1, "templates": [
    {"id": "ack", "enabled": True, "subject": "Re: {subject}",
     "body": "Hello {sender_name},\n\nThanks, we got it.\n"},
    {"id": "off", "enabled": False, "subject": "x", "body": "y"},
]}  # fmt: skip


def _config(conn: sqlite3.Connection, key: str, value: Any) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?,"
                     " 't', 'test') ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (f"config.{key}", json.dumps(value)))  # fmt: skip


@pytest.fixture
def live(conn: sqlite3.Connection, clock: FakeClock) -> str:
    make_address(conn, clock, "live")
    with write_tx(conn):
        conn.execute("UPDATE addresses SET outbound = 1, preset = 'B'")
    _config(conn, "templates", TEMPLATES)
    _config(conn, "forward_allow_list", [{"id": "ap_lead", "address": "lead@acme.example"}])
    sid = make_classified(conn, clock, INVOICE, FACTS)
    with write_tx(conn):
        conn.execute("UPDATE items SET sender_name = 'Pat', subject = 'Invoice 42'")
    return sid


def _propose(conn: sqlite3.Connection, clock: FakeClock, sid: str, action: str,
             target: str = "", text: str | None = None) -> dict[str, Any]:  # fmt: skip
    item = item_row(conn, sid)
    ctx, p = decide.plan_for(conn, item)
    got = {"action": action, "target": target, "reason": "r"} | ({"text": text} if text else {})
    actor.decide_one(conn, clock, item, ctx, p, got, frozenset(), local=False)
    return json.loads(item_row(conn, sid)["proposal"])


def test_template_reply_payload_is_in_the_grant(conn: sqlite3.Connection, clock: FakeClock,
                                                 live: str) -> None:  # fmt: skip
    doc = _propose(conn, clock, live, "reply_template", "ack")
    [a] = [a for a in doc["actions"] if a["name"] == "reply_template"]
    assert a["payload"]["to"] == "news@vendor-a.example" and a["payload"]["template"] == "ack"
    grant = conn.execute("SELECT action_hash FROM grants WHERE stable_id = ?", (live,)).fetchone()
    planned = [Planned.from_json(x) for x in doc["actions"]]
    assert grant["action_hash"] == action_hash(live, "h", planned)
    without = [Planned(x.name, x.target) for x in planned]
    assert grant["action_hash"] != action_hash(live, "h", without)  # the payload counts


def test_a_changed_template_no_longer_matches(conn: sqlite3.Connection, clock: FakeClock,
                                              live: str) -> None:  # fmt: skip
    doc = _propose(conn, clock, live, "reply_template", "ack")
    [a] = [a for a in doc["actions"] if a["name"] == "reply_template"]
    item = item_row(conn, live)
    assert outbound_plan.check(conn, item, "reply_template", "ack", a["payload"]) is None
    changed = json.loads(json.dumps(TEMPLATES))
    changed["templates"][0]["body"] = "Send the money to another account.\n"
    _config(conn, "templates", changed)
    assert "changed since the approval" in str(
        outbound_plan.check(conn, item, "reply_template", "ack", a["payload"])
    )


def test_forward_payload_names_the_allow_list_address(conn: sqlite3.Connection, clock: FakeClock,
                                                      live: str) -> None:  # fmt: skip
    doc = _propose(conn, clock, live, "forward_internal", "ap_lead")
    [a] = [a for a in doc["actions"] if a["name"] == "forward_internal"]
    assert a["payload"] == {"entry": "ap_lead", "to": "lead@acme.example"}
    item = item_row(conn, live)
    _config(conn, "forward_allow_list", [{"id": "ap_lead", "address": "other@acme.example"}])
    assert outbound_plan.check(conn, item, "forward_internal", "ap_lead", a["payload"])


def test_draft_text_is_cleaned_and_capped(conn: sqlite3.Connection, clock: FakeClock,
                                          live: str) -> None:  # fmt: skip
    text = "Hello Pat,\r\n\nThanks\u202e\x00 for the invoice.   \n" + "x" * 5000
    doc = _propose(conn, clock, live, "draft_reply", text=text)
    [a] = [a for a in doc["actions"] if a["name"] == "draft_reply"]
    body = a["payload"]["text"]
    assert body.startswith("Hello Pat,\n\nThanks for the invoice.\n") and len(body) == 4000
    assert a["payload"]["to"] == "news@vendor-a.example"


def test_an_empty_draft_is_dropped(conn: sqlite3.Connection, clock: FakeClock, live: str) -> None:
    doc = _propose(conn, clock, live, "draft_reply", text="\x00\u200b ")
    assert not any(a["name"] == "draft_reply" for a in doc["actions"])
    assert any(d["name"] == "draft_reply" and "no text" in d["why"]
               for d in doc["plan"]["dropped"])  # fmt: skip


def test_an_unreplyable_sender_drops_the_reply(conn: sqlite3.Connection, clock: FakeClock,
                                               live: str) -> None:  # fmt: skip
    with write_tx(conn):
        conn.execute("UPDATE items SET sender = 'Boss <ceo@acme.example>'")
    doc = _propose(conn, clock, live, "reply_template", "ack")
    assert any("can't be replied to" in d["why"] for d in doc["plan"]["dropped"])


def test_outbound_off_records_the_suppressed_send(conn: sqlite3.Connection, clock: FakeClock,
                                                  live: str) -> None:  # fmt: skip
    with write_tx(conn):
        conn.execute("UPDATE addresses SET outbound = 0")
    doc = _propose(conn, clock, live, "reply_template", "ack")
    row = item_row(conn, live)
    assert row["suppressed_action"] == "reply_template"
    assert {"name": "flag", "target": None} in doc["actions"]
    assert not any(a["name"] == "reply_template" for a in doc["actions"])


def test_old_grants_keep_their_hash() -> None:
    sid = StableId("a" * 64)
    assert action_hash(sid, "h", [Planned("archive")]) == action_hash(
        sid, "h", [Planned.from_json({"name": "archive", "target": None})]
    )
    assert Planned("archive").to_json() == {"name": "archive", "target": None}
