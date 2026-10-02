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
from ecf_server import checks, daily, db, digests, evalrun, items, modelq, pause, slack_admin
from ecf_server._slack import Envelope
from ecf_server.actions import MessageChangedError, Planned
from ecf_server.chat import FakeChat, RouteRef
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.slack_in import Inbound, SlackReceiver

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
                     " created_at) VALUES ('ap', 'ap@acme.example', 'standard', 'assist', 'A', ?)",
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
    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                      content_hash="h", facts=json.dumps(facts), subject=f"Invoice {sid[:3]}",
                      sender="billing@vendor-a.example", **extra)  # fmt: skip
    return sid


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")
    return [json.loads(r[0]) for r in rows]


def _facts(conn: sqlite3.Connection, sid: str) -> dict[str, Any]:
    return json.loads(
        conn.execute("SELECT facts FROM items WHERE stable_id = ?", (sid,)).fetchone()[0]
    )


# ---- digests --------------------------------------------------------------------------------


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
    assert text[2] == "Weak fraud signals (first-time sender asking for payment):"
    assert text[3].startswith("aaaaaaaa billing@vendor-a.example: Invoice aaa")
    assert text[5] == "Payment email from unverified senders:" and text[6].startswith("bbbbbbbb")
    assert text[-1] == "Not fully scanned: 1"
    assert [(b["action"], b["ref"]) for b in card["buttons"]] == [("undo", "b" * 64),
                                                                  ("pause", "ap")]  # fmt: skip
    assert card["note"].startswith("This acts on the email only.")


def test_the_digest_and_daily_summary_say_an_eval_holds_the_model(
    conn: sqlite3.Connection,
) -> None:
    clock = FakeClock(MORNING)
    slack_setup(conn, clock)
    assert digests.run(conn, clock) == 0
    clock.advance(3600)
    _item(conn, clock, "d" * 64, {})
    line = ("Model checks paused for an eval since 2026-10-01 14:00 UTC: new mail waits for the"
            " local model; fraud checks continue (ecf eval status)")  # fmt: skip
    assert modelq.EXCLUSIVE.acquire("eval", to_ts(clock.now()))
    try:
        assert digests.run(conn, clock) == 1
        assert _posts(conn)[0]["card"]["text"].splitlines()[1] == line
        assert line in daily.card(conn, clock.now(), "2026-10-01").text.splitlines()
    finally:
        modelq.EXCLUSIVE.release()
    assert evalrun.slack_line() is None
    assert "eval" not in daily.card(conn, clock.now(), "2026-10-01").text


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


# ---- Undo -----------------------------------------------------------------------------------


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
    assert _posts(conn)[-1]["text"].startswith("Undo queued for bbbbbbbb")
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
    assert "Undone for bbbbbbbb: removed label unverified_sender, flag." in texts
    assert any(t.startswith("Couldn't undo xxxxxxxx") for t in texts)


# ---- the daily summary ----------------------------------------------------------------------


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
