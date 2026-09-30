"""Pause and resume, the pinned "Needs you", stale items and the dead-man's switch (V1.2 step
8a)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from ecf.errors import NotFoundError, StepupRequiredError
from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import approvals, db, deadman, execute, items, needs_you, pause, slack_admin, stepup
from ecf_server._slack import Envelope, SlackError
from ecf_server.actions import Planned
from ecf_server.clock import Clock, FakeClock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.slack_in import Inbound, SlackReceiver
from ecf_server.state_machine import TransitionContext
from ecf_server.stepper import FakeStepper

ME = "U0ME1"
FRAUD = {"triggers": {"fraud": ["bank details from an unconfirmed sender"]}}


def _setup(conn: sqlite3.Connection, clock: FakeClock, *, summary: bool = True) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        for aid, sens in (("ap", "high"), ("billing", "standard")):
            conn.execute("INSERT INTO addresses (address_id, email, sensitivity, stage, preset,"
                         " created_at) VALUES (?, ?, ?, 'live', 'A', ?)",
                         (aid, f"{aid}@acme.example", sens, now))  # fmt: skip
            conn.execute("INSERT INTO routes (address_id, surface, route_ref, name)"
                         " VALUES (?, 'slack', ?, ?)",
                         (aid, f"C{aid.upper()}", f"ecf-default-{aid}"))  # fmt: skip
        keys = [("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", ME)]
        if summary:
            keys.append(("slack_summary_channel", "CSUM"))
        for k, v in keys:
            slack_admin.put_setting(conn, k, v, now, actor="test")


def _item(conn: sqlite3.Connection, clock: FakeClock, sid: str, aid: str = "ap",
          *, escalated: bool = True) -> str:  # fmt: skip
    def also(c: sqlite3.Connection) -> None:
        if escalated:
            c.execute("INSERT INTO escalations (stable_id, address_id, state, created_at)"
                      " VALUES (?, ?, 'posted', ?)", (sid, aid, to_ts(clock.now())))  # fmt: skip

    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId(aid),
                      content_hash="h", facts=json.dumps(FRAUD), subject=f"Invoice {sid[:4]}",
                      sender="billing@vendor-a.example", also=also)  # fmt: skip
    return sid


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")
    return [json.loads(r[0]) for r in rows]


def _status(conn: sqlite3.Connection, sid: str) -> str:
    return str(conn.execute("SELECT status FROM items WHERE stable_id = ?", (sid,)).fetchone()[0])


# ---- pause ----------------------------------------------------------------------------------


def test_pause_one_all_and_back(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    assert pause.set_paused(conn, clock, "ap@acme.example", True, actor="os_user") == ["ap"]
    assert pause.set_paused(conn, clock, "ap", True, actor="os_user") == []  # already
    assert pause.set_paused(conn, clock, pause.ALL, True, actor="os_user") == ["billing"]
    assert pause.paused_addresses(conn) == ["ap", "billing"]
    assert pause.set_paused(conn, clock, pause.ALL, False, actor="os_user") == ["ap", "billing"]
    with pytest.raises(NotFoundError):
        pause.set_paused(conn, clock, "nobody", True, actor="os_user")
    events = [r[0] for r in conn.execute("SELECT event FROM audit ORDER BY id")]
    assert events == ["address.paused"] * 2 + ["address.resumed"] * 2
    assert "fraud" in pause.describe(["ap"], True)


class RecordingExecutor:
    def __init__(self) -> None:
        self.ran: list[str] = []

    def __call__(self, conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row,
                 actions: list[Planned]) -> list[str]:  # fmt: skip
        self.ran.append(item["stable_id"])
        return ["done"]


def _approved(conn: sqlite3.Connection, clock: FakeClock, sid: str, aid: str,
              actions: list[Planned]) -> None:  # fmt: skip
    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId(aid),
                      content_hash="h", facts="{}")  # fmt: skip
    for to in (Status.CLASSIFIED, Status.PROPOSED):
        items.transition(conn, clock, StableId(sid), to, TransitionContext(), actor="test")
    approvals.request(conn, clock, sid, actions)
    try:
        approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")
    except StepupRequiredError as exc:
        issued = stepup.issue(conn, clock, FakeStepper(), exc.extra["purpose"], exc.extra["target"])
        stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
        approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user", nonce=issued.nonce_id)


def test_a_paused_address_holds_approved_actions_without_using_attempts(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    _approved(conn, clock, "a" * 64, "billing", [Planned("archive")])
    pause.set_paused(conn, clock, "billing", True, actor="os_user")
    ex = RecordingExecutor()
    for _ in range(5):
        execute.run_once(conn, clock, ex)
        clock.advance(120)
    job = conn.execute("SELECT state, attempts FROM jobs WHERE queue = 'actions'").fetchone()
    assert ex.ran == [] and (job["state"], job["attempts"]) == ("queued", 0)
    pause.set_paused(conn, clock, "billing", False, actor="os_user")
    clock.advance(120)
    assert execute.run_once(conn, clock, ex) and ex.ran == ["a" * 64]


def test_a_delayed_send_that_runs_out_while_paused_waits_for_resume(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    sid = "a" * 64
    _approved(conn, clock, sid, "ap", [Planned("reply_template", "received")])
    assert _status(conn, sid) == Status.DELAYED
    pause.set_paused(conn, clock, "ap", True, actor="os_user")
    assert approvals.advance_delays(conn, clock, 900, woke=False) == 0
    assert _status(conn, sid) == Status.DELAYED
    pause.set_paused(conn, clock, "ap", False, actor="os_user")
    assert approvals.advance_delays(conn, clock, 0, woke=False) == 1
    assert _status(conn, sid) == Status.EXECUTING


def test_pause_all_from_the_pinned_message(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    _setup(conn, clock)
    inbound = Inbound(slack_admin.identity, clock, lambda: db.connect(db_path),
                      lambda _e: None, lambda _t, _v: None)  # fmt: skip
    payload = {"type": "block_actions", "api_app_id": "A1", "team": {"id": "T1"},
               "user": {"id": ME}, "channel": {"id": "CSUM"},
               "actions": [{"action_id": "pause#0", "value": pause.ALL}]}  # fmt: skip
    inbound.on_envelope(Envelope("e1", "interactive", payload, None, None))
    SlackReceiver(clock).run_once(conn)
    assert pause.paused_addresses(conn) == ["ap", "billing"]
    note = _posts(conn)[-1]
    assert note["op"] == "ephemeral" and note["text"].startswith("Paused ap, billing.")


# ---- Needs you ------------------------------------------------------------------------------


def test_needs_you_lists_what_waits_stale_first(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    assert needs_you.card(conn, computer="mac", last_connected=clock.now()).title == (
        "Needs you: nothing waiting")  # fmt: skip
    _item(conn, clock, "a" * 64)
    _item(conn, clock, "b" * 64, escalated=False)  # nothing for you to do
    pause.set_paused(conn, clock, "billing", True, actor="os_user")
    c = needs_you.card(conn, computer="mac", last_connected=datetime(2026, 10, 1, 12, 0))
    assert c.title == "Needs you: 1 waiting"
    lines = c.text.splitlines()
    assert lines[0] == "aaaaaaaa ap: escalated: billing@vendor-a.example: Invoice aaaa"
    assert (
        "Paused: billing (fraud checks continue)" in lines and lines[-1] == "All of it: ecf inbox"
    )
    assert [b.label for b in c.buttons] == ["Pause all", "Resume all"]
    assert c.note == "Buttons work only while mac is awake. Last connected 2026-10-01 12:00 UTC."


def test_needs_you_is_pinned_once_and_edited_only_on_change(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    assert needs_you.refresh(conn, clock, computer="mac")
    assert _posts(conn)[-1]["pin"] is True and _posts(conn)[-1]["channel"] == "CSUM"
    assert not needs_you.refresh(conn, clock, computer="mac")  # nothing changed
    _item(conn, clock, "a" * 64)
    clock.advance(60)
    assert needs_you.refresh(conn, clock, computer="mac")
    assert _posts(conn)[-1]["card"]["title"] == "Needs you: 1 waiting"
    clock.advance(3601)  # hourly, so "last connected" stays true while awake
    assert needs_you.refresh(conn, clock, computer="mac")
    assert not needs_you.refresh(conn, clock, computer="mac")


def test_no_summary_channel_no_needs_you(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock, summary=False)
    assert not needs_you.refresh(conn, clock) and _posts(conn) == []


def test_stale_items_are_marked_and_announced_once(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    _item(conn, clock, "a" * 64)
    clock.advance(29 * 86400)
    assert needs_you.mark_stale(conn, clock) == []
    clock.advance(2 * 86400)
    assert needs_you.mark_stale(conn, clock) == ["a" * 64]
    assert needs_you.mark_stale(conn, clock) == []
    [post] = [p for p in _posts(conn) if p["key"].startswith("stale:")]
    assert post["card"]["title"] == "1 email(s) have waited 30 days"
    c = needs_you.card(conn, computer="mac", last_connected=clock.now())
    assert c.title == "Needs you: 1 waiting (1 stale)"
    assert "1 stale email(s) waiting 30+ days: ecf inbox --stale" in c.text  # a `new` item


# ---- the dead-man's switch ------------------------------------------------------------------


class ScheduleWeb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.n = 0
        self.fail_delete = False

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        self.calls.append((method, params))
        if method == "chat.scheduleMessage":
            self.n += 1
            return {"ok": True, "scheduled_message_id": f"Q{self.n}"}
        if self.fail_delete:
            raise SlackError(method, "invalid_scheduled_message_id")
        return {"ok": True}


def test_the_dead_mans_message_stays_ahead_and_is_replaced_before_it_is_due(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    web = ScheduleWeb()
    step = deadman.interval(conn, clock.now())
    assert deadman.keep_armed(conn, clock, web, computer="mac")
    method, p = web.calls[0]
    assert method == "chat.scheduleMessage" and p["channel"] == "CSUM"
    assert p["post_at"] == int((clock.now() + 3 * step).timestamp())
    assert "hasn't checked in since" in p["text"] and "(mac)" in p["text"]
    assert not deadman.keep_armed(conn, clock, web, computer="mac")  # still far enough ahead
    clock.advance(step.total_seconds() + 1)  # less than 2 intervals left: replace it
    assert deadman.keep_armed(conn, clock, web, computer="mac")
    # the new one first, then the old one
    assert [m for m, _ in web.calls] == ["chat.scheduleMessage", "chat.scheduleMessage",
                                         "chat.deleteScheduledMessage"]  # fmt: skip
    assert web.calls[2][1]["scheduled_message_id"] == "Q1"
    due = from_ts(slack_admin.setting(conn, deadman.POST_AT))
    assert due - clock.now() == 3 * deadman.interval(conn, clock.now())


def test_a_clean_stop_disarms_it_and_a_posted_one_is_harmless(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    web = ScheduleWeb()
    deadman.keep_armed(conn, clock, web, computer="mac")
    web.fail_delete = True  # it already posted (the computer slept past it)
    deadman.disarm(conn, web)
    assert web.calls[-1][0] == "chat.deleteScheduledMessage"
    assert slack_admin.setting(conn, deadman.ID) == ""
    _setup_no = ScheduleWeb()
    deadman.disarm(conn, _setup_no)  # nothing scheduled: nothing to delete
    assert _setup_no.calls == []


def test_no_summary_channel_no_dead_mans_message(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock, summary=False)
    web = ScheduleWeb()
    assert not deadman.keep_armed(conn, clock, web, computer="mac") and web.calls == []


def test_by_default_it_posts_only_in_business_hours(conn: sqlite3.Connection) -> None:
    """OD-219: a laptop asleep overnight isn't reported unless it stays off into the workday."""
    s = deadman._settings(conn)  # pyright: ignore[reportPrivateUsage]  # the defaults
    ny = ZoneInfo("America/New_York")
    evening = datetime(2026, 10, 1, 19, 0, tzinfo=ny)  # a Thursday
    assert deadman.post_time(evening, s) == datetime(2026, 10, 2, 8, 30, tzinfo=ny)
    friday = datetime(2026, 10, 2, 16, 50, tzinfo=ny)  # 17:20 would be after hours
    assert deadman.post_time(friday, s) == datetime(2026, 10, 5, 8, 30, tzinfo=ny)  # Monday
    morning = datetime(2026, 10, 1, 9, 0, tzinfo=ny)
    assert deadman.post_time(morning, s) == morning + timedelta(minutes=30)
    s[deadman.OFFHOURS] = True
    assert deadman.post_time(evening, s) == evening + timedelta(minutes=90)


def test_an_overnight_message_is_scheduled_once_for_the_morning(
    conn: sqlite3.Connection,
) -> None:
    clock = FakeClock(datetime(2026, 10, 1, 23, 0, tzinfo=UTC))  # 19:00 in New York
    _setup(conn, clock)
    web = ScheduleWeb()
    assert deadman.keep_armed(conn, clock, web, computer="mac")
    posted_at = datetime.fromtimestamp(web.calls[0][1]["post_at"], UTC)
    assert posted_at == datetime(2026, 10, 2, 12, 30, tzinfo=UTC)  # 08:30 in New York
    clock.advance(3 * 3600)  # the evening goes by: it is far enough ahead, nothing to do
    assert not deadman.keep_armed(conn, clock, web, computer="mac")
    with write_tx(conn):  # as `ecf settings set` will store it (a JSON boolean)
        conn.execute("INSERT INTO settings VALUES (?, 'true', ?, 'test')",
                     (deadman.OFFHOURS, to_ts(clock.now())))  # fmt: skip
    s = deadman._settings(conn)  # pyright: ignore[reportPrivateUsage]
    assert deadman.post_time(clock.now(), s) == clock.now() + timedelta(minutes=90)
    s[deadman.OFFHOURS] = "true"  # a string isn't a yes
    assert deadman.post_time(clock.now(), s) == datetime(2026, 10, 2, 12, 30, tzinfo=UTC)


def test_after_the_message_fired_ecf_says_it_is_back(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """A shutdown or logout leaves the switch armed, so its message may have posted (OD-222)."""
    _setup(conn, clock)
    web = ScheduleWeb()
    deadman.keep_armed(conn, clock, web, computer="mac")
    clock.advance(2 * 3600)  # the computer was off past the message's time
    assert deadman.keep_armed(conn, clock, web, computer="mac")
    back = [p for p in _posts(conn) if p["key"].startswith("deadman-back:")]
    assert len(back) == 1 and back[0]["card"]["title"] == "ecf is back"
    clock.advance(60)
    deadman.keep_armed(conn, clock, web, computer="mac")  # far enough ahead again: no second
    assert len([p for p in _posts(conn) if p["key"].startswith("deadman-back:")]) == 1
