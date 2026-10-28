"""Hourly digests, Undo, the daily summary and the channel-member check (V1.2 step 8b)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import ConflictError
from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import checks, daily, db, digests, items, pause, slack_admin, slack_render
from ecf_server._slack import Envelope
from ecf_server.actions import MessageChangedError, Planned
from ecf_server.chat import FakeChat, RouteRef
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.slack_in import Inbound, SlackReceiver
from ecf_server.state_machine import Stage, TransitionContext

ME, BOT = "U0ME1", "U0BOT"
# 2026-10-01 is a Thursday; 12:00 UTC is 08:00 in New York (business hours start)
MORNING = datetime(2026, 10, 1, 13, 0, tzinfo=UTC)
WEAK: dict[str, Any] = {"triggers": {"fraud_weak": ["first-time sender asking for payment"]},
        "payment_keyword": True,
        "precheck": {"stage": "assist", "executed": ["label suspicious", "flag"]}}  # fmt: skip
UNVERIFIED: dict[str, Any] = {"triggers": {"unverified_payment": True}, "payment_keyword": True,
              "precheck": {"stage": "assist",
                           "executed": ["label unverified_sender", "flag"]}}  # fmt: skip
FRAUD: dict[str, Any] = {"triggers": {"fraud": ["bank change"]},
         "precheck": {"stage": "assist",
                      "executed": ["label suspicious", "flag", "escalate"]}}  # fmt: skip


def slack_setup(conn: sqlite3.Connection, clock: FakeClock) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, stage, preset,"
                     " created_at) VALUES ('ap', 'ap@acme.example', 'standard', 'assist', 'C', ?)",
                     (now,))  # fmt: skip
        conn.execute("INSERT INTO routes (address_id, surface, route_ref, name)"
                     " VALUES ('ap', 'slack', 'CAP', 'ecf-default-ap')")  # fmt: skip
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", ME),
                     ("slack_summary_channel", "CSUM"),
                     ("slack_summary_name", "ecf-default-summary"),
                     (slack_admin.BOT_USER, BOT)):  # fmt: skip
            slack_admin.put_setting(conn, k, v, now, actor="test")


def _item(conn: sqlite3.Connection, clock: FakeClock, sid: str, facts: dict[str, Any],
          **extra: Any) -> str:  # fmt: skip
    columns: dict[str, Any] = {"subject": f"Invoice {sid[:3]}",
                               "sender": "billing@vendor-a.example"} | extra  # fmt: skip
    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                      content_hash="h", facts=json.dumps(facts), **columns)  # fmt: skip
    return sid


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")
    return [json.loads(r[0]) for r in rows]


def _facts(conn: sqlite3.Connection, sid: str) -> dict[str, Any]:
    return json.loads(
        conn.execute("SELECT facts FROM items WHERE stable_id = ?", (sid,)).fetchone()[0]
    )


# Digests: their sections and Undo buttons, when they post (business hours, never idle), and a
# digest on demand.


def test_a_digest_lists_its_sections_with_undo_only_where_allowed(conn: sqlite3.Connection) -> None:
    clock = FakeClock(MORNING)
    slack_setup(conn, clock)
    assert digests.run(conn, clock) == 0  # first sight: starts from now
    clock.advance(600)
    _item(conn, clock, "a" * 64, WEAK)
    _item(conn, clock, "b" * 64, UNVERIFIED | {"content_unscanned": True})
    _item(conn, clock, "c" * 64, FRAUD)  # has its own escalation card
    assert digests.run(conn, clock) == 0  # less than an hour
    clock.advance(3000)
    assert digests.run(conn, clock) == 1
    [post] = _posts(conn)
    card = post["card"]
    assert post["channel"] == "CAP" and card["title"] == "Digest: ap"
    text = card["text"].splitlines()
    assert text[0] == "3 new message(s) since 13:00 UTC."
    assert text[2:4] == ["You can undo (1):", text[9]]  # first; the line its section has
    assert text[5] == "Weak fraud signals (flagged, not escalated):"
    assert text[6].startswith("aaaaaaaa billing@vendor-a.example: Invoice aaa")
    assert text[6].endswith(": first-time sender asking for payment")  # why (OD-490)
    assert text[8] == "Payment email from unverified senders:" and text[9].startswith("bbbbbbbb")
    assert text[-1] == "Not fully scanned: 1"
    assert [(b["action"], b["ref"]) for b in card["buttons"]] == [("undo", "b" * 64),
                                                                  ("pause", "ap")]  # fmt: skip
    assert card["note"].startswith("This acts on the email only.")


def test_impersonation_without_money_has_its_own_digest_section(conn: sqlite3.Connection) -> None:
    """V1.6: it was listed as "first-time sender asking for payment"."""
    clock = FakeClock(MORNING)
    slack_setup(conn, clock)
    assert digests.run(conn, clock) == 0
    clock.advance(600)
    why = "display name matches Pat Lee (patlee@gmail.com), one of your org addresses, but ..."
    _item(conn, clock, "e" * 64, WEAK | {"triggers": {"fraud_weak": [why],
                                                      "impersonation": [why]}})  # fmt: skip
    _item(conn, clock, "a" * 64, WEAK)
    clock.advance(3000)
    assert digests.run(conn, clock) == 1
    card = _posts(conn)[0]["card"]
    text = card["text"].splitlines()
    assert text[2:7] == [
        "Pretending to be someone you work with (no payment wording found):",
        text[3],
        "",
        "Weak fraud signals (flagged, not escalated):",
        text[6],
    ]
    assert text[3].startswith("eeeeeeee") and text[6].startswith("aaaaaaaa")
    assert card["note"].startswith("This acts on the email only.")


def test_no_idle_digests_and_none_outside_business_hours(conn: sqlite3.Connection) -> None:
    clock = FakeClock(MORNING)
    slack_setup(conn, clock)
    digests.run(conn, clock)
    clock.advance(3600)
    assert digests.run(conn, clock) == 0 and _posts(conn) == []  # nothing came in
    clock.advance(10 * 3600)  # 19:00 in New York
    _item(conn, clock, "b" * 64, UNVERIFIED)
    assert digests.run(conn, clock) == 0
    clock.advance(13 * 3600)  # the next morning: the evening's mail rolls in
    assert digests.run(conn, clock) == 1
    assert _posts(conn)[-1]["card"]["text"].startswith("Caught up: 1 message(s) since")


def test_a_digest_on_demand_at_any_hour_restarts_the_clock(conn: sqlite3.Connection) -> None:
    clock = FakeClock(MORNING)
    slack_setup(conn, clock)
    digests.run(conn, clock)  # the hourly clock starts
    clock.advance(600)
    _item(conn, clock, "a" * 64, WEAK)
    r = digests.post_now(conn, clock, "ap@acme.example")
    assert r == {"address_id": "ap", "posted": True, "since": to_ts(MORNING)}
    assert _posts(conn)[-1]["card"]["title"] == "Digest: ap"
    clock.advance(3600)
    assert digests.run(conn, clock) == 0  # nothing new since the one just posted
    clock.advance(10 * 3600)  # 20:00 in New York: outside business hours, still posts on demand
    _item(conn, clock, "b" * 64, UNVERIFIED)
    assert digests.post_now(conn, clock, "ap")["posted"] is True
    assert digests.post_now(conn, clock, "ap")["posted"] is False  # nothing new
    assert len(_posts(conn)) == 2


def test_a_digest_on_demand_needs_a_channel(conn: sqlite3.Connection) -> None:
    clock = FakeClock(MORNING)
    slack_setup(conn, clock)
    with write_tx(conn):
        conn.execute("DELETE FROM routes")
    with pytest.raises(ConflictError, match="no Slack channel"):
        digests.post_now(conn, clock, "ap")


# Undo buttons name their email (operator decision 2026-10-27): "Undo: <sender>, <subject>", the
# subject cut to fit, never the address; each email once, listed above the buttons.

ARCHIVED = json.dumps({"actions": [{"name": "archive"}, {"name": "label", "target": "marketing"}],
                       "done": [{"name": "label", "target": "marketing"}]})  # fmt: skip


def _executed(conn: sqlite3.Connection, clock: FakeClock, sid: str, facts: dict[str, Any],
              **extra: Any) -> str:  # fmt: skip
    """An email a model plan acted on, which Undo may take back."""
    _item(conn, clock, sid, facts, decision_source="rule", proposal=ARCHIVED, **extra)
    for status in (Status.CLASSIFIED, Status.PROPOSED, Status.EXECUTING, Status.EXECUTED):
        items.transition(conn, clock, StableId(sid), status, TransitionContext(stage=Stage.LIVE),
                         actor="t")  # fmt: skip
    return sid


def _undo_buttons(card: Any) -> list[tuple[str, str]]:
    return [(b.label, b.ref) for b in card.buttons if b.action == digests.UNDO]


def test_undo_buttons_name_the_sender_and_subject_and_are_listed(
    conn: sqlite3.Connection,
) -> None:
    clock = FakeClock(MORNING)
    slack_setup(conn, clock)
    clock.advance(60)
    _item(conn, clock, "b" * 64, UNVERIFIED)  # the pre-check's labels
    _executed(conn, clock, "d" * 64, {})  # a model plan's archive
    card = digests.build(conn, "ap", MORNING, clock.now())
    assert card is not None
    assert _undo_buttons(card) == [
        ("Undo: billing@vendor-a.example, Invoice ddd", "d" * 64),
        ("Undo: billing@vendor-a.example, Invoice bbb", "b" * 64),
    ]
    text = card.text.splitlines()
    i = text.index("You can undo (2):")
    assert i == 2  # right after the opener
    assert text[i + 1] == ("dddddddd billing@vendor-a.example: Invoice ddd"
                           " (label marketing)")  # fmt: skip  # what was done, not planned
    assert text[i + 2].startswith("bbbbbbbb billing@vendor-a.example: Invoice bbb (")
    assert text[i + 3] == ""  # the list ends


def test_an_email_undoable_both_ways_gets_one_button(conn: sqlite3.Connection) -> None:
    clock = FakeClock(MORNING)
    slack_setup(conn, clock)
    clock.advance(60)
    _executed(conn, clock, "e" * 64, UNVERIFIED)  # pre-check labels and a model plan
    card = digests.build(conn, "ap", MORNING, clock.now())
    assert card is not None
    assert [ref for _l, ref in _undo_buttons(card)] == ["e" * 64]
    lines = card.text.splitlines()
    assert lines[lines.index("You can undo (1):") + 1].endswith("(label marketing)")


def test_an_undo_label_cuts_the_subject_never_the_address(conn: sqlite3.Connection) -> None:
    clock = FakeClock(MORNING)
    slack_setup(conn, clock)
    clock.advance(60)
    head = "Undo: " + "x" * 48 + "@vendor-a.example"  # 71: room for ", ", one character, "…"
    _item(conn, clock, "a" * 64, UNVERIFIED, subject="Quarterly statement " * 6)
    _item(conn, clock, "b" * 64, UNVERIFIED, sender=head.removeprefix("Undo: "))
    _item(conn, clock, "c" * 64, UNVERIFIED, sender="y" * 49 + "@vendor-a.example")  # 72
    _item(conn, clock, "e" * 64, UNVERIFIED, subject="\U0001f381" * 40)  # 2 UTF-16 units each
    _item(conn, clock, "f" * 64, UNVERIFIED, subject="Pay\u202enow https://evil.example")
    _item(conn, clock, "9" * 64, UNVERIFIED, sender_name="billing@vendor-a.example",
          sender="x@evil.test")  # fmt: skip
    card = digests.build(conn, "ap", MORNING, clock.now())
    assert card is not None
    label = {ref[0]: lb for lb, ref in _undo_buttons(card)}
    units = {k: len(v.encode("utf-16-le")) // 2 for k, v in label.items()}
    assert max(units.values()) <= slack_render.LABEL_MAX
    assert label["a"].startswith("Undo: billing@vendor-a.example, Quarterly statement")
    assert label["a"].endswith("…") and units["a"] == slack_render.LABEL_MAX
    assert label["b"] == head + ", I…"  # one character of the subject
    assert label["c"] == "Undo cccccccc"
    assert label["e"].startswith("Undo: billing@vendor-a.example, \U0001f381")
    assert units["e"] == slack_render.LABEL_MAX
    assert label["9"] == "Undo: x@evil.test, Invoice 999"  # the address alone, not the name
    rendered = json.dumps(slack_render.blocks(card), ensure_ascii=False)
    assert "Undo: billing@vendor-a.example, Paynow https[:]//evil.example" in rendered
    assert "\u202e" not in rendered


def test_a_line_break_in_a_subject_cant_add_a_line_to_the_undo_list(
    conn: sqlite3.Connection,
) -> None:
    clock = FakeClock(MORNING)
    slack_setup(conn, clock)
    clock.advance(60)
    _item(conn, clock, "b" * 64, UNVERIFIED, subject="Hi\n7fbf7921 ceo@acme.example: Wire",
          sender_name="Pat\nLee")  # fmt: skip
    card = digests.build(conn, "ap", MORNING, clock.now())
    assert card is not None
    lines = card.text.splitlines()
    i = lines.index("You can undo (1):")
    assert lines[i + 1].startswith("bbbbbbbb Pat Lee <billing@vendor-a.example>: Hi 7fbf7921 ceo")
    assert lines[i + 2] == ""  # no extra line in the list
    assert _undo_buttons(card)[0][0] == ("Undo: billing@vendor-a.example,"
                                          " Hi 7fbf7921 ceo@acme.example: Wire")  # fmt: skip


def test_the_undo_list_shows_ten_and_counts_the_rest(conn: sqlite3.Connection) -> None:
    clock = FakeClock(MORNING)
    slack_setup(conn, clock)
    clock.advance(60)
    for n in range(25):
        _item(conn, clock, f"{n:02d}" + "b" * 62, UNVERIFIED)
    card = digests.build(conn, "ap", MORNING, clock.now())
    assert card is not None
    assert len(_undo_buttons(card)) == digests.UNDO_MAX == 10
    lines = card.text.splitlines()
    i = lines.index("You can undo (25):")
    assert lines[i + 11] == "... and 15 more, not shown"
    assert card.buttons[-1].action == pause.PAUSE  # within Slack's 25 buttons
    assert len(card.buttons) <= slack_render.BUTTONS_MAX


@pytest.mark.parametrize("facts", [FRAUD, {"triggers": {"regulator": ["regulator named"]}},
                                   {"quarantined": True}])  # fmt: skip
def test_a_model_plan_on_fraud_regulator_or_quarantined_mail_offers_no_undo(
    conn: sqlite3.Connection, facts: dict[str, Any]
) -> None:
    clock = FakeClock(MORNING)
    slack_setup(conn, clock)
    clock.advance(60)
    _executed(conn, clock, "d" * 64, facts)
    card = digests.build(conn, "ap", MORNING, clock.now())
    assert card is not None
    assert _undo_buttons(card) == [] and "You can undo" not in card.text


# Undo from a digest: queued for the next check, run inside it, and never on fraud.


def _click(db_path: Path, clock: FakeClock, conn: sqlite3.Connection, ref: str) -> None:
    inbound = Inbound(slack_admin.identity, clock, lambda: db.connect(db_path),
                      lambda _e: None, lambda _t, _v: None)  # fmt: skip
    payload = {"type": "block_actions", "api_app_id": "A1", "team": {"id": "T1"},
               "user": {"id": ME}, "channel": {"id": "CAP"},
               "actions": [{"action_id": "undo#0", "value": ref}]}  # fmt: skip
    inbound.on_envelope(Envelope(f"e-{ref}", "interactive", payload, None, None))
    SlackReceiver(clock).run_once(conn)


def test_undo_is_queued_for_the_next_check_and_refused_on_fraud(
    conn: sqlite3.Connection, db_path: Path
) -> None:
    clock = FakeClock(MORNING)
    slack_setup(conn, clock)
    _item(conn, clock, "b" * 64, UNVERIFIED)
    _item(conn, clock, "a" * 64, WEAK)
    _click(db_path, clock, conn, "b" * 64)
    assert _facts(conn, "b" * 64)["precheck"]["undo"] == "queued"
    due = conn.execute("SELECT next_due_at FROM check_state WHERE address_id = 'ap'").fetchone()
    assert due[0] == to_ts(clock.now())  # checked within a minute
    assert _posts(conn)[-1]["text"].startswith("Undo queued for bbbbbbbb (Invoice bbb): done")
    _click(db_path, clock, conn, "a" * 64)  # a weak fraud signal: its label stays
    assert "undo" not in _facts(conn, "a" * 64)["precheck"]
    assert _posts(conn)[-1]["text"].startswith("Nothing to undo here")


def test_queued_undos_run_inside_the_check_and_never_on_fraud(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    slack_setup(conn, clock)
    ran: list[tuple[str, list[Planned]]] = []

    def fake_undo(_c: Any, _k: Any, _src: Any, item: sqlite3.Row, actions: list[Planned],
                  **_kw: Any) -> list[str]:  # fmt: skip
        if item["stable_id"].startswith("x"):
            raise MessageChangedError("message no longer in INBOX")
        ran.append((item["stable_id"], actions))
        return [f"label {a.target}" if a.target else a.name for a in actions]

    monkeypatch.setattr(digests, "undo", fake_undo)
    queued = {"undo": "queued"}
    _item(conn, clock, "b" * 64, UNVERIFIED | {"precheck": UNVERIFIED["precheck"] | queued})
    _item(conn, clock, "f" * 64, FRAUD | {"precheck": FRAUD["precheck"] | queued})  # forced
    _item(conn, clock, "x" * 64, UNVERIFIED | {"precheck": UNVERIFIED["precheck"] | queued})
    assert digests._undos_in_check in checks.IN_LEASE  # pyright: ignore[reportPrivateUsage]
    assert digests.run_undos(conn, clock, None, "ap", install="t",  # type: ignore[arg-type]
                             max_scan_bytes=1) == 3  # fmt: skip
    assert ran == [("b" * 64, [Planned("label", "unverified_sender"), Planned("flag")])]
    assert _facts(conn, "b" * 64)["precheck"]["undo"] == "done"
    assert _facts(conn, "f" * 64)["precheck"]["undo"] == "done"  # nothing removed
    assert _facts(conn, "x" * 64)["precheck"]["undo"].startswith("failed: message no longer")
    texts = [p["text"] for p in _posts(conn)]
    assert "Undone for bbbbbbbb (Invoice bbb): removed label unverified_sender, flag." in texts
    assert any(t.startswith("Couldn't undo xxxxxxxx (Invoice xxx): ") for t in texts)


def test_a_model_plan_undo_reply_names_the_subject_with_links_defanged(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    slack_setup(conn, clock)
    sid = _executed(conn, clock, "d" * 64, {}, subject="Deals at www.shop.example")
    with write_tx(conn):
        conn.execute("UPDATE items SET proposal = json_set(proposal, '$.undo', 'queued')")

    def fake_undo(*_a: Any, **_k: Any) -> list[str]:
        return ["label marketing"]

    monkeypatch.setattr(digests.mailbox_actions, "undo_item", fake_undo)
    assert digests.run_model_undos(conn, clock, None, "ap", "t", lambda: False) == 1  # type: ignore[arg-type]
    assert _posts(conn)[-1]["text"] == (f"Undone for {sid[:8]} (Deals at www[.]shop[.]example):"
                                        " label marketing.")  # fmt: skip


# The daily summary: once per business day, what waits, and hourly checks of who is in the
# channel.


def test_the_daily_summary_posts_once_per_business_day(conn: sqlite3.Connection) -> None:
    clock = FakeClock(datetime(2026, 10, 1, 11, 0, tzinfo=UTC))  # 07:00 in New York
    slack_setup(conn, clock)
    assert daily.run(conn, clock) is False
    clock.advance(3600)  # 08:00
    assert daily.run(conn, clock) is True and daily.run(conn, clock) is False
    assert _posts(conn)[-1]["card"]["title"] == "Daily summary 2026-10-01"
    clock.advance(2 * 86400)  # Saturday
    assert daily.run(conn, clock) is False
    clock.advance(2 * 86400)  # Monday
    assert daily.run(conn, clock) is True


def test_the_daily_summary_says_what_waits_and_who_else_is_there(
    conn: sqlite3.Connection,
) -> None:
    clock = FakeClock(MORNING)
    slack_setup(conn, clock)
    _item(conn, clock, "c" * 64, FRAUD)
    with write_tx(conn):
        conn.execute("INSERT INTO escalations (stable_id, address_id, state, created_at)"
                     " VALUES (?, 'ap', 'posted', ?)", ("c" * 64, to_ts(clock.now())))  # fmt: skip
        conn.execute("UPDATE items SET stale = 1")
        slack_admin.put_setting(conn, daily.MEMBERS, json.dumps({"ecf-default-ap": ["U0EVE"]}),
                                to_ts(clock.now()), actor="test")  # fmt: skip
    pause.set_paused(conn, clock, "ap", True, actor="os_user")
    _item(conn, clock, "d" * 64, {})  # ordinary mail: waits for the classifier, not for you
    c = daily.card(conn, clock.now(), "2026-10-01")
    assert dict(c.fields)["ap"] == (
        "1 waiting on you, 1 waiting for the classifier (V1.3); last 24 h: 1 escalated,"
        " 0 not fully scanned"
    )
    lines = c.text.splitlines()
    assert lines[0] == "Stale (30+ days): 1" and lines[1].startswith("  cccccccc ap:")
    assert "Paused: ap (fraud checks continue)" in lines
    assert "Others in ecf's channels: ecf-default-ap: U0EVE" in lines


def test_channel_members_are_checked_hourly_and_changes_are_a_security_notice(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    slack_setup(conn, clock)
    chat = FakeChat()
    chat.routes["CSUM"] = {"name": "ecf-default-summary", "members": [ME, BOT], "archived": False}
    chat.routes["CAP"] = {"name": "ecf-default-ap", "members": [ME, BOT], "archived": False}
    n = FakeNotifier()
    assert daily.check_members(conn, clock, chat, n) == {}  # recorded; no notice the first time
    chat.invite(RouteRef("CAP"), "U0EVE")
    assert daily.check_members(conn, clock, chat, n) is None  # not due yet
    clock.advance(3601)
    assert daily.check_members(conn, clock, chat, n) == {"ecf-default-ap": ["U0EVE"]}
    assert (
        n.sent[0][0] == "[ecf-alert] Security Notice"
        and "joined ecf-default-ap: U0EVE" in n.sent[0][1]
    )
    notice = [p for p in _posts(conn) if p["key"].startswith("notice:")]
    assert {p["channel"] for p in notice} == {ME, "CSUM"}
    clock.advance(3601)
    assert daily.check_members(conn, clock, chat, n) == {"ecf-default-ap": ["U0EVE"]}
    assert len(n.sent) == 1  # unchanged: no second notice


def test_the_first_member_check_reports_anyone_already_there(conn: sqlite3.Connection) -> None:
    """A channel ecf adopted may have come with people (V1.2 review, 2026-09-30)."""
    clock = FakeClock(MORNING)
    slack_setup(conn, clock)
    chat = FakeChat()
    chat.routes["CSUM"] = {"name": "ecf-default-summary", "members": [ME, BOT], "archived": False}
    chat.routes["CAP"] = {"name": "ecf-default-ap", "members": [ME, BOT, "U0EVE"],
                          "archived": False}  # fmt: skip
    n = FakeNotifier()
    assert daily.check_members(conn, clock, chat, n) == {"ecf-default-ap": ["U0EVE"]}
    assert n.sent[0][0] == "[ecf-alert] Security Notice" and "U0EVE" in n.sent[0][1]
