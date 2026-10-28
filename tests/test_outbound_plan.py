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
         "from_count": 1, "language": "en"}  # fmt: skip
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
        conn.execute("UPDATE addresses SET outbound = 1, preset = 'C'")
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
    actor.decide_one(conn, clock, item, ctx, p, got, frozenset())
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


def test_draft_text_is_cleaned(conn: sqlite3.Connection, clock: FakeClock, live: str) -> None:
    text = "Hello Pat,\r\n\nThanks\u202e\x00 for the invoice.   \n" + "x" * 3900
    doc = _propose(conn, clock, live, "draft_reply", text=text)
    [a] = [a for a in doc["actions"] if a["name"] == "draft_reply"]
    body = a["payload"]["text"]
    assert body.startswith("Hello Pat,\n\nThanks for the invoice.\n") and body.endswith("x" * 3900)
    assert a["payload"]["to"] == "news@vendor-a.example"


@pytest.mark.parametrize(("sep", "out"), [
    ("\r\n", "\n"), ("\r", "\n"), ("\x0b", "\n"), ("\x0c", "\n"), ("\x1c", "\n"), ("\x1d", "\n"),
    ("\x1e", "\n"), ("\x85", "\n"), ("\u2028", "\n"), ("\u2029", "\n\n"),
])  # fmt: skip
def test_every_line_separator_becomes_a_line_break(sep: str, out: str) -> None:
    """M8 plan §1 (AE7, AF4): what `str.splitlines` breaks on is a line break in the saved draft,
    so deleting a control can't join two words ("Dana\\x85CFO" was saved "DanaCFO")."""
    assert sep.join(["Dana", "CFO"]).splitlines() == ["Dana", "CFO"]
    assert outbound_plan.clean_draft(f"Dana{sep}CFO") == f"Dana{out}CFO"


@pytest.mark.parametrize("char", ["\ue000", "\U000f0000", "\ud800", "\u0378"])
def test_private_use_surrogate_and_unassigned_characters_are_dropped(char: str) -> None:
    """M8 plan §1 (AG1): Co, Cs and Cn go as Cc and Cf do, as Slack's `_scrub` drops them."""
    assert outbound_plan.clean_draft(f"I w{char}ill pay") == "I will pay"


def test_an_over_long_draft_goes_back_to_the_model() -> None:
    """Claude is told the limit and writes a shorter draft, never silently cut (P4)."""
    long = "x" * (outbound_plan.DRAFT_MAX + 1)
    why = actor.problem("draft_reply", "", "r", frozenset(), frozenset(), ("draft_reply",),
                        text=long)  # fmt: skip
    assert why is not None and "4,000" in why
    ok = actor.problem("draft_reply", "", "r", frozenset(), frozenset(), ("draft_reply",),
                       text="x" * outbound_plan.DRAFT_MAX)  # fmt: skip
    assert ok is None


def test_an_over_long_draft_that_gets_through_is_refused_flagged_and_shown(
    conn: sqlite3.Connection, clock: FakeClock, live: str
) -> None:
    """The backstop: never cut silently, never dropped unseen (P4)."""
    from ecf.cli_items import proposal_lines  # noqa: PLC0415

    doc = _propose(conn, clock, live, "draft_reply", text="x" * 5000)
    assert not any(a["name"] == "draft_reply" for a in doc["actions"])
    assert any(a["name"] == "flag" for a in doc["actions"])
    [d] = [d for d in doc["plan"]["dropped"] if d["name"] == "draft_reply"]
    assert "4,000" in d["why"]
    assert any("not proposed: draft_reply" in ln and "4,000" in ln
               for ln in proposal_lines({"proposal": doc}))  # fmt: skip


def test_a_draft_at_the_limit_still_matches_at_execution(
    conn: sqlite3.Connection, clock: FakeClock, live: str
) -> None:
    """The limit didn't change, so a 4,000-character draft (the most an earlier version bound)
    still matches at execution (P5)."""
    doc = _propose(conn, clock, live, "draft_reply", text="y" * outbound_plan.DRAFT_MAX)
    [a] = [a for a in doc["actions"] if a["name"] == "draft_reply"]
    item = item_row(conn, live)
    assert outbound_plan.check(conn, item, "draft_reply", None, a["payload"]) is None


def test_drafts_never_join_approve_all(conn: sqlite3.Connection, clock: FakeClock,
                                       live: str) -> None:  # fmt: skip
    """A digest's one click must not approve a draft nobody read (P3)."""
    from ecf_server import digest_actions  # noqa: PLC0415

    _propose(conn, clock, live, "draft_reply", text="Thanks, Pat.")
    row = item_row(conn, live)
    assert row["status"] == "awaiting_approval"
    assert not digest_actions.eligible(conn, row)


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
