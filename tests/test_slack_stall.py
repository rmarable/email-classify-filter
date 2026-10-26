"""The Slack stall fix (OD-557): a post Slack refuses gives up at once and isn't posted again by
itself; an outage is held, and given up when other posts get through or after 2 hours; other
errors retry briefly; a give-up is one notice, worded by what was lost, at most a few an hour."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import answers, approvals, items, jobs, slack_admin, slack_out
from ecf_server._slack import SlackError
from ecf_server.actions import Planned
from ecf_server.chat import Card, RouteRef
from ecf_server.clock import FakeClock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.slack_chat import SlackChat
from ecf_server.slack_out import SlackSender
from ecf_server.state_machine import TransitionContext
from tests.test_slack_out import FakeWeb, Timer

ME = "U0ME1"
CAP, C2 = RouteRef("CAP"), RouteRef("C2")
TITLE = "[ecf-alert] Slack Delivery Failed: a post wasn't sent"


class ChannelWeb(FakeWeb):
    """Fails every post and edit to the channels named, with that code, until told otherwise."""

    def __init__(self) -> None:
        super().__init__()
        self.down: dict[str, str] = {}

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        code = self.down.get(str(params.get("channel")))
        if code and method in ("chat.postMessage", "chat.update"):
            self.calls.append((method, params))
            raise SlackError(method, code)
        return super().call(method, **params)


class Notices:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def __call__(self, _conn: sqlite3.Connection, detail: str) -> None:
        self.sent.append(detail)


@pytest.fixture
def setup(conn: sqlite3.Connection, clock: FakeClock) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute(
            "INSERT INTO addresses (address_id, email, sensitivity, stage, preset,"
            " created_at) VALUES ('ap', 'ap@acme.example', 'standard', 'live', 'C', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO routes (address_id, surface, route_ref, name)"
            " VALUES ('ap', 'slack', 'CAP', 'ecf-default-ap')"
        )
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", ME)):
            slack_admin.put_setting(conn, k, v, now, actor="test")


def _sender(
    web: FakeWeb, clock: FakeClock, notices: Notices | None = None, n: FakeNotifier | None = None
) -> SlackSender:
    t = Timer()
    return SlackSender(
        SlackChat(web),
        clock,
        n or FakeNotifier(),
        resolve=lambda _h: True,
        sleep=t.sleep,
        monotonic=t.mono,
        on_lost=notices,
        lost_kind=approvals.lost_post_kind,
    )


def _waiting(conn: sqlite3.Connection, clock: FakeClock, sid: str) -> str:
    """An approval waiting, its card queued for CAP."""
    items.create_item(
        conn,
        clock,
        stable_id=StableId(sid),
        address_id=AddressId("ap"),
        content_hash="h",
        facts="{}",
        subject="Invoice 42",
        sender="billing@vendor-a.example",
    )
    for to in (Status.CLASSIFIED, Status.PROPOSED):
        items.transition(conn, clock, StableId(sid), to, TransitionContext(), actor="test")
    approvals.request(conn, clock, sid, [Planned("archive")])
    return sid


def _states(conn: sqlite3.Connection, key: str) -> list[str]:
    rows = conn.execute(
        "SELECT state FROM jobs WHERE queue = 'slack_out' AND"
        " json_extract(payload, '$.key') = ? ORDER BY rowid",
        (key,),
    )
    return [r[0] for r in rows]


def _job(conn: sqlite3.Connection, key: str) -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM jobs WHERE queue = 'slack_out' AND"
        " json_extract(payload, '$.key') = ? ORDER BY rowid DESC LIMIT 1",
        (key,),
    ).fetchone()


def _audit(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM audit WHERE event = 'slack.post_lost' ORDER BY id"
    ).fetchall()


# A refused post gives up at once, its channel moves on, and it isn't posted again by itself.


def test_a_refused_card_gives_up_at_once_and_the_channel_moves_on(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web, notes = FakeWeb(), Notices()
    s = _sender(web, clock, notes)
    sid = _waiting(conn, clock, "a" * 64)
    slack_out.enqueue_post(conn, clock, key="digest:ap:1", route=CAP, card=Card("next"))
    web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
    assert s.run_once(conn) and s.run_once(conn)  # no 30 s wait: the next post goes at once
    assert _states(conn, f"item:{sid}") == ["dead"] and _states(conn, "digest:ap:1") == ["done"]
    assert _job(conn, f"item:{sid}")["last_error"] == "slack:chat.postMessage:invalid_blocks"
    assert notes.sent == [
        f"Email {sid[:8]}: decide it with ecf approve {sid[:8]} or ecf reject"
        f" {sid[:8]}. Slack refused its card; ecf won't post it again by"
        " itself; ecf slack repost tries again."
    ]
    assert approvals.post_held_cards(conn, clock) == 0  # left for ecf slack repost


def test_one_unknown_failure_is_posted_once_more_and_two_are_not(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web, notes = FakeWeb(), Notices()
    s = _sender(web, clock, notes)
    sid = _waiting(conn, clock, "b" * 64)
    key = f"item:{sid}"
    for round_ in (1, 2):
        for _ in range(jobs.DEFAULT_MAX_ATTEMPTS):
            web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "some_new_error")]
            s.run_once(conn)
            clock.advance(400)
        assert _states(conn, key)[-1] == "dead"
        posted = approvals.post_held_cards(conn, clock)
        assert posted == (1 if round_ == 1 else 0)  # X3 once; then left for ecf slack repost
    assert "ecf posts it once more when it can" in notes.sent[0]
    assert "didn't reach Slack twice" in notes.sent[1]


def test_unknown_codes_retry_after_30_60_120_300_seconds(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web = FakeWeb()
    s = _sender(web, clock, Notices())
    slack_out.enqueue_post(conn, clock, key="digest:ap:1", route=CAP, card=Card("d"))
    waits: list[int] = []
    for _ in range(jobs.DEFAULT_MAX_ATTEMPTS):
        web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "some_new_error")]
        assert s.run_once(conn)
        row = _job(conn, "digest:ap:1")
        if row["state"] == "queued":
            waits.append(round((from_ts(row["visible_at"]) - clock.now()).total_seconds()))
            clock.advance(waits[-1])
    assert waits == [30, 60, 120, 300] and _job(conn, "digest:ap:1")["state"] == "dead"


def test_a_give_up_is_one_event_never_resolved_by_a_later_post(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web, n = FakeWeb(), FakeNotifier()
    s = _sender(web, clock, None, n)  # the default notice: the desktop, same title as alerts.event
    slack_out.enqueue_post(conn, clock, key="daily:2026-10-25", route=CAP, card=Card("d"))
    web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "msg_too_long")]
    s.run_once(conn)
    slack_out.enqueue_post(conn, clock, key="digest:ap:2", route=CAP, card=Card("ok"))
    s.run_once(conn)
    assert n.sent == [(TITLE, "A post didn't reach Slack; nothing to do unless it repeats.")]
    assert conn.execute("SELECT count(*) FROM alerts").fetchone()[0] == 0  # no open/resolve alert


def test_a_failed_edit_doesnt_hold_back_a_later_post(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web = FakeWeb()
    s = _sender(web, clock, Notices())
    sid = _waiting(conn, clock, "c" * 64)
    key = f"item:{sid}"
    s.run_once(conn)  # posted
    approvals.edit_card(conn, clock, sid, "Archived")
    web.fail["chat.update"] = [SlackError("chat.update", "block_mismatch")]
    s.run_once(conn)  # the edit refused: dead at once, the message record kept
    assert _states(conn, key)[-1] == "dead" and slack_out.message_ref(conn, key) is not None
    assert slack_out.post_deaths(conn, key) == 0 and key not in slack_out.refused_keys(conn)
    with write_tx(conn):  # then its channel went
        conn.execute("DELETE FROM slack_messages WHERE key = ?", (key,))
    assert approvals.post_held_cards(conn, clock) == 1


# An outage: held without counting; given up when other posts get through, or after 2 hours.


def test_an_outage_answer_holds_without_counting_an_attempt(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web = ChannelWeb()
    web.down["CAP"] = "internal_error"
    s = _sender(web, clock, Notices())
    slack_out.enqueue_post(conn, clock, key="digest:ap:1", route=CAP, card=Card("d"))
    for _ in range(3):
        s.run_once(conn)
        clock.advance(slack_out.OUTAGE_HOLD_S)
    row = _job(conn, "digest:ap:1")
    assert row["state"] == "queued" and row["attempts"] == 0


def test_with_other_posts_going_through_the_16th_outage_answer_gives_up(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web, notes = ChannelWeb(), Notices()
    web.down["CAP"] = "internal_error"
    s = _sender(web, clock, notes)
    sid = _waiting(conn, clock, "d" * 64)
    key = f"item:{sid}"
    slack_out.enqueue_post(conn, clock, key="digest:ap:2", route=CAP, card=Card("behind it"))
    s.run_once(conn)  # the first outage answer
    slack_out.enqueue_post(conn, clock, key="digest:other", route=C2, card=Card("fine"))
    s.run_once(conn)  # another channel posts fine
    for _ in range(slack_out.OUTAGE_CAP_HOLDS):
        clock.advance(slack_out.OUTAGE_HOLD_S)
        s.run_once(conn)
    assert _states(conn, key) == ["dead"]
    assert "Slack couldn't take its card while other posts went through" in notes.sent[0]
    assert "tries again" in notes.sent[0]
    web.down.clear()
    s.run_once(conn)  # the next job on the channel is claimed
    assert _states(conn, "digest:ap:2") == ["done"]
    assert approvals.post_held_cards(conn, clock) == 0  # an outage give-up isn't posted again


def test_with_nothing_going_through_the_post_is_held_then_given_up_after_2_hours(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web, notes, n = ChannelWeb(), Notices(), FakeNotifier()
    web.down["CAP"] = "service_unavailable"
    s = _sender(web, clock, notes, n)
    sid = _waiting(conn, clock, "e" * 64)
    key = f"item:{sid}"
    for i in range(slack_out.OUTAGE_MAX_HOLDS):
        s.run_once(conn)
        clock.advance(slack_out.OUTAGE_HOLD_S)
        if i == 30:
            assert _states(conn, key) == ["queued"]  # still held after its 30th outage answer
    [alert] = conn.execute("SELECT detail FROM alerts WHERE resolved_at IS NULL").fetchall()
    assert "Slack has answered service_unavailable to every post for" in alert[0]
    assert "at most 2 hours" in alert[0]
    assert _states(conn, key) == ["queued"] and not notes.sent
    s.run_once(conn)  # the 121st
    assert _states(conn, key) == ["dead"]
    assert "Slack didn't take its card for 2 hours" in notes.sent[0]
    web.down.clear()
    slack_out.enqueue_post(conn, clock, key="digest:ap:3", route=CAP, card=Card("ok"))
    s.run_once(conn)
    assert conn.execute("SELECT count(*) FROM alerts WHERE resolved_at IS NULL").fetchone()[0] == 0


def test_completions_without_a_post_dont_count_as_slack_working(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web, notes = ChannelWeb(), Notices()
    web.down["CAP"] = "internal_error"
    s = _sender(web, clock, notes)
    sid = _waiting(conn, clock, "f" * 64)
    s.run_once(conn)
    slack_out.enqueue_post(conn, clock, key="digest:gone", route=RouteRef("CGONE"), card=Card("x"))
    web.down["CGONE"] = "channel_not_found"  # completes on a gone channel: not a success
    s.run_once(conn)
    for _ in range(slack_out.OUTAGE_CAP_HOLDS + 2):
        clock.advance(slack_out.OUTAGE_HOLD_S)
        s.run_once(conn)
    assert _states(conn, f"item:{sid}") == ["queued"] and not notes.sent


# The notice: worded by what was lost; a few an hour, then a summary.


def test_the_notice_window_keeps_cards_with_an_action_and_sums_up_the_rest(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web, notes = FakeWeb(), Notices()
    s = _sender(web, clock, notes)
    for i in range(slack_out.NOTICES_PER_WINDOW + 1):
        slack_out.enqueue_post(conn, clock, key=f"digest:ap:{i}", route=CAP, card=Card("d"))
        web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
        s.run_once(conn)
    sid = _waiting(conn, clock, "1" * 64)
    web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
    s.run_once(conn)
    assert len(notes.sent) == slack_out.NOTICES_PER_WINDOW + 1  # the sixth digest held back
    assert notes.sent[-1].startswith(f"Email {sid[:8]}: decide it")
    clock.advance(int(slack_out.NOTICE_WINDOW.total_seconds()))
    assert not s.run_once(conn)  # a pass with no job still sends the summary
    assert notes.sent[-1] == (
        "1 more posts didn't reach Slack, 0 of them cards of emails waiting"
        " for you; ecf doctor lists them; ecf slack repost puts approval"
        " cards back"
    )


def test_the_summary_counts_an_escalation_but_not_a_decided_items_card(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web, notes = FakeWeb(), Notices()
    s = _sender(web, clock, notes)
    for i in range(slack_out.NOTICES_PER_WINDOW):
        slack_out.enqueue_post(conn, clock, key=f"digest:ap:{i}", route=CAP, card=Card("d"))
        web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
        s.run_once(conn)
    for i in range(slack_out.ITEM_NOTICES_PER_WINDOW):  # escalations share the 20 (N8)
        slack_out.enqueue_post(conn, clock, key=f"burst:ap:{i}", route=CAP, card=Card("burst"))
        web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
        s.run_once(conn)
    assert len(notes.sent) == slack_out.NOTICES_PER_WINDOW + slack_out.ITEM_NOTICES_PER_WINDOW
    slack_out.enqueue_post(conn, clock, key="burst:ap:x", route=CAP, card=Card("burst"))
    decided = _waiting(conn, clock, "2" * 64)
    approvals.reject(conn, clock, decided, actor="test")
    for _ in range(3):  # the burst, the decided item's card and its edit
        web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
        web.fail["chat.update"] = [SlackError("chat.update", "invalid_blocks")]
        s.run_once(conn)
    clock.advance(int(slack_out.NOTICE_WINDOW.total_seconds()))
    s.run_once(conn)
    assert notes.sent[-1].startswith("3 more posts didn't reach Slack, 1 of them cards")


def test_a_notice_after_a_quiet_window_opens_a_fresh_one(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web, notes = FakeWeb(), Notices()
    s = _sender(web, clock, notes)
    slack_out.enqueue_post(conn, clock, key="digest:ap:a", route=CAP, card=Card("d"))
    web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
    s.run_once(conn)
    clock.advance(int(slack_out.NOTICE_WINDOW.total_seconds()) + 1)
    for i in range(slack_out.NOTICES_PER_WINDOW):
        slack_out.enqueue_post(conn, clock, key=f"digest:ap:{i}", route=CAP, card=Card("d"))
        web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
        s.run_once(conn)
    assert len(notes.sent) == 1 + slack_out.NOTICES_PER_WINDOW  # nothing owed, nothing held


@pytest.mark.parametrize(
    ("kind", "method", "reason", "flavour", "deaths", "starts"),
    [
        ("approval", "chat.postMessage", "refused", "", 1, "Email abcd1234: decide it with"),
        ("approval", "chat.postMessage", "outage", "cap", 1, "Email abcd1234: decide it with"),
        ("approval", "chat.postMessage", "outage", "quiet", 1, "Email abcd1234: decide it with"),
        ("approval", "chat.postMessage", "unknown", "", 1, "Email abcd1234: its card didn't"),
        ("approval", "chat.postMessage", "unknown", "", 2, "Email abcd1234: decide it with"),
        ("approval", "chat.update", "refused", "", 0, "Email abcd1234: decide it with"),
        ("approval_stepup", "chat.update", "refused", "", 0, "Email abcd1234 is waiting for"),
        ("question", "chat.postMessage", "refused", "", 1, "Email abcd1234: answer it with"),
        ("question", "chat.update", "refused", "", 0, "Email abcd1234: answer it with"),
        ("delayed", "chat.update", "unknown", "", 0, "Email abcd1234 is sending in a few"),
        ("escalation", "chat.postMessage", "refused", "", 1, "An escalation card didn't"),
        ("other_item", "chat.update", "refused", "", 0, "Email abcd1234: its card in Slack"),
        ("digest", "chat.postMessage", "unknown", "", 1, "A digest for abcd1234 didn't reach"),
        ("needs_you", "chat.postMessage", "refused", "", 1, "The Needs you list didn't reach"),
        ("none", "chat.postMessage", "refused", "", 1, "A post didn't reach Slack; nothing"),
    ],
)
def test_each_notice_leads_with_what_to_do(
    kind: str, method: str, reason: str, flavour: str, deaths: int, starts: str
) -> None:
    text = slack_out.notice_text(kind, "abcd1234", method, reason, flavour, deaths)
    assert text.startswith(starts)
    # what to do (or, for a first failure, what ecf will do) comes first, within what a desktop
    # notification shows
    first = (
        "ecf posts it once more"
        if (reason, deaths) == ("unknown", 1) and kind == "approval"
        else next(
            (
                c
                for c in ("ecf approve", "ecf answer", "ecf cancel", "ecf digest", "ecf inbox")
                if c in text
            ),
            "",
        )
    )
    assert kind == "none" or text.index(first) < 90


def test_what_a_lost_post_was(conn: sqlite3.Connection, clock: FakeClock, setup: None) -> None:
    sid = _waiting(conn, clock, "3" * 64)
    q = "4" * 64
    items.create_item(
        conn, clock, stable_id=StableId(q), address_id=AddressId("ap"), content_hash="h", facts="{}"
    )
    for to in (Status.CLASSIFIED, Status.PROPOSED):
        items.transition(conn, clock, StableId(q), to, TransitionContext(), actor="test")
    answers.ask(conn, clock, q, "Which invoice?", member=ME)
    kind = approvals.lost_post_kind
    assert kind(conn, f"item:{sid}") == ("approval", sid[:8])
    assert kind(conn, f"item:{q}") == ("question", q[:8])
    assert kind(conn, "item:" + "5" * 64) == ("none", "")  # no longer kept
    assert kind(conn, "burst:ap:x") == ("escalation", "")
    assert kind(conn, "rollup:ap:2026-10-25T10") == ("escalation", "")
    assert kind(conn, "needs-you") == ("needs_you", "")
    assert kind(conn, "digest:ap:2026-10-25T10:00:00.000000Z") == ("digest", "ap")
    assert kind(conn, "daily:2026-10-25") == ("none", "")
    assert kind(conn, "archive") == ("none", "")


def test_a_delayed_send_is_its_own_kind(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    sid = _waiting(conn, clock, "6" * 64)
    ctx = TransitionContext(reversible=True)
    items.transition(conn, clock, StableId(sid), Status.APPROVED, ctx, actor="test")
    items.transition(
        conn,
        clock,
        StableId(sid),
        Status.DELAYED,
        TransitionContext(send_on_high=True),
        actor="test",
    )
    assert approvals.lost_post_kind(conn, f"item:{sid}") == ("delayed", sid[:8])


# Audit, failures of the notice path, and the dead-post bookkeeping.


def test_the_audit_row_has_the_code_and_method_only(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web = FakeWeb()
    s = _sender(web, clock, Notices())
    sid = _waiting(conn, clock, "7" * 64)
    slack_out.enqueue_post(conn, clock, key="burst:ap:" + "8" * 64, route=CAP, card=Card("b"))
    slack_out.enqueue_post(conn, clock, key="digest:ap:1", route=CAP, card=Card("d"))
    for _ in range(3):
        web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
        s.run_once(conn)
    rows = _audit(conn)
    assert [(r["stable_id"], r["address_id"]) for r in rows] == [(sid, "ap"), (None, "ap")]
    assert json.loads(rows[0]["data"]) == {
        "code": "invalid_blocks",
        "method": "chat.postMessage",
        "reason": "refused",
    }


def test_a_failing_lookup_or_notice_still_leaves_the_audit_row(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    def broken(_c: sqlite3.Connection, _k: str) -> tuple[str, str]:
        raise RuntimeError("lookup")

    def broken_notice(_c: sqlite3.Connection, _d: str) -> None:
        raise RuntimeError("notice")

    web = FakeWeb()
    t = Timer()
    for lost_kind, on_lost in ((broken, None), (approvals.lost_post_kind, broken_notice)):
        s = SlackSender(
            SlackChat(web),
            clock,
            FakeNotifier(),
            sleep=t.sleep,
            monotonic=t.mono,
            lost_kind=lost_kind,
            on_lost=on_lost,
        )
        sid = _waiting(conn, clock, ("9" if on_lost else "0") * 64)
        slack_out.enqueue_post(conn, clock, key="digest:ap:next", route=CAP, card=Card("n"))
        web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
        assert s.run_once(conn) and s.run_once(conn)  # the pass goes on to the next job
        assert any(r["stable_id"] == sid for r in _audit(conn))


def test_a_failing_audit_write_still_sends_the_notice(
    conn: sqlite3.Connection, clock: FakeClock, setup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_a: Any) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(slack_out, "_audit_lost", broken)
    web, notes = FakeWeb(), Notices()
    s = _sender(web, clock, notes)
    _waiting(conn, clock, "a1" * 32)
    web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
    s.run_once(conn)
    assert len(notes.sent) == 1


def test_a_failing_summary_closes_the_window_and_the_pass_goes_on(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    calls: list[str] = []

    def on_lost(_c: sqlite3.Connection, detail: str) -> None:
        calls.append(detail)
        if detail.startswith("1 more"):
            raise RuntimeError("email down")

    web, t = FakeWeb(), Timer()
    s = SlackSender(
        SlackChat(web), clock, FakeNotifier(), sleep=t.sleep, monotonic=t.mono, on_lost=on_lost
    )
    for i in range(slack_out.NOTICES_PER_WINDOW + 1):
        slack_out.enqueue_post(conn, clock, key=f"digest:ap:{i}", route=CAP, card=Card("d"))
        web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
        s.run_once(conn)
    clock.advance(int(slack_out.NOTICE_WINDOW.total_seconds()))
    assert not s.run_once(conn)  # the summary raised; logged
    assert not s.run_once(conn) and calls.count(calls[-1]) == 1  # not sent again


def test_dead_post_bookkeeping(conn: sqlite3.Connection, clock: FakeClock, setup: None) -> None:
    rows = [
        ("item:new-refused", "slack:chat.postMessage:invalid_blocks", False),
        ("item:new-outage", "slack:chat.postMessage:internal_error", False),
        ("item:new-unknown", "slack:chat.postMessage:some_new_error", False),
        ("item:new-edit", "slack:chat.update:block_mismatch", True),
        ("item:old-form", "slack:invalid_blocks", False),
        ("item:old-unknown", "slack:some_new_error", False),
        ("item:expired-claim", "claim expired", False),
        ("item:null", None, False),
        ("item:null-with-message", None, True),
    ]
    now = to_ts(clock.now())
    for key, error, message in rows:
        slack_out.enqueue_post(conn, clock, key=key, route=CAP, card=Card("x"))
        with write_tx(conn):
            conn.execute(
                "UPDATE jobs SET state = 'dead', last_error = ? WHERE"
                " json_extract(payload, '$.key') = ?",
                (error, key),
            )
            if message:
                conn.execute(
                    "INSERT INTO slack_messages (key, channel, ts, created_at,"
                    " updated_at) VALUES (?, 'CAP', '1.0', ?, ?)",
                    (key, now, now),
                )
    got = slack_out._dead_posts(conn)  # pyright: ignore[reportPrivateUsage]
    assert got == {
        "item:new-refused": (1, True),
        "item:new-outage": (1, True),
        "item:new-unknown": (1, False),
        "item:old-form": (1, True),
        "item:old-unknown": (1, False),
        "item:expired-claim": (1, False),
        "item:null": (1, False),
    }
    assert slack_out.refused_keys(conn) == {"item:new-refused", "item:new-outage", "item:old-form"}
    assert slack_out.post_deaths(conn, "item:new-edit") == 0


def test_after_a_channel_gone_a_refused_card_gets_one_more_try_then_is_held(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web = ChannelWeb()
    s = _sender(web, clock, Notices())
    sid = _waiting(conn, clock, "b1" * 32)
    key = f"item:{sid}"
    web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
    s.run_once(conn)
    assert key in slack_out.refused_keys(conn)
    slack_out.enqueue_post(conn, clock, key=key, route=CAP, card=Card("again"))  # by hand
    web.down["CAP"] = "channel_not_found"
    s.run_once(conn)  # completes on the gone channel: supersedes the refused death
    web.down.clear()
    with write_tx(conn):
        conn.execute(
            "INSERT INTO routes (address_id, surface, route_ref, name)"
            " VALUES ('ap', 'slack', 'CNEW', 'ecf-default-ap')"
        )
    assert approvals.post_held_cards(conn, clock) == 1  # exactly one more automatic post
    web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
    s.run_once(conn)
    assert approvals.post_held_cards(conn, clock) == 0  # then held


def test_a_refused_card_that_later_posted_and_was_deleted_comes_back(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    """A guard (passes before OD-557 too, where nothing held cards back)."""
    web = FakeWeb()
    s = _sender(web, clock, Notices())
    sid = _waiting(conn, clock, "c1" * 32)
    key = f"item:{sid}"
    web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
    s.run_once(conn)
    slack_out.enqueue_post(conn, clock, key=key, route=CAP, card=Card("fine now"))
    s.run_once(conn)  # posted fine (say, at grant expiry)
    with write_tx(conn):  # then deleted in Slack, as `_forget` does after message_not_found
        conn.execute("DELETE FROM slack_messages WHERE key = ?", (key,))
    assert approvals.post_held_cards(conn, clock) == 1


def test_the_notice_never_goes_through_slack(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    """A guard: the event's class keeps it off Slack, so a refused notice can't loop."""
    from ecf_server import alerts  # noqa: PLC0415

    alerts.event(conn, clock, FakeNotifier(), slack_out.ALERT, "x", condition=slack_out.LOST)
    assert conn.execute("SELECT count(*) FROM jobs WHERE queue = 'slack_out'").fetchone()[0] == 0


# Diff review fixes (N1-N8).


class TickingWeb(FakeWeb):
    """Moves the clock on by `step` seconds during each post, as a slow Slack call would."""

    def __init__(self, clock: FakeClock) -> None:
        super().__init__()
        self.clock, self.step = clock, 0

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        self.clock.advance(self.step)
        return super().call(method, **params)


def test_a_notice_decided_after_the_window_ends_mid_pass_is_sent_and_the_summary_too(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    """N1: the window ends between the top-of-pass flush and the notice."""
    web, notes = TickingWeb(clock), Notices()
    s = _sender(web, clock, notes)
    for i in range(slack_out.NOTICES_PER_WINDOW + 1):  # five sent, one held for the summary
        slack_out.enqueue_post(conn, clock, key=f"daily:{i}", route=CAP, card=Card("d"))
        web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
        s.run_once(conn)
    clock.advance(int(slack_out.NOTICE_WINDOW.total_seconds()) - 1)
    web.step = 2  # the hour ends during the call
    slack_out.enqueue_post(conn, clock, key="daily:late", route=CAP, card=Card("d"))
    web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
    s.run_once(conn)
    assert notes.sent[-2].startswith("1 more posts didn't reach Slack")
    assert notes.sent[-1] == "A post didn't reach Slack; nothing to do unless it repeats."


def test_a_failing_slack_calls_row_still_gives_the_audit_row_and_notice(
    conn: sqlite3.Connection, clock: FakeClock, setup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N2: `_record` raising (a busy database) after the job died."""

    def broken(*_a: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(SlackSender, "_record", broken)
    web, notes = FakeWeb(), Notices()
    s = _sender(web, clock, notes)
    sid = _waiting(conn, clock, "a2" * 32)
    web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
    s.run_once(conn)
    assert _states(conn, f"item:{sid}") == ["dead"]
    assert [r["stable_id"] for r in _audit(conn)] == [sid] and len(notes.sent) == 1


def test_a_failing_outage_alert_still_gives_the_notice(
    conn: sqlite3.Connection, clock: FakeClock, setup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N2: the alert write after an outage give-up raising doesn't lose the notice."""

    def broken(*_a: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(SlackSender, "_outage_failed", broken)
    web, notes = ChannelWeb(), Notices()
    web.down["CAP"] = "internal_error"
    s = _sender(web, clock, notes)
    sid = _waiting(conn, clock, "a3" * 32)
    for _ in range(slack_out.OUTAGE_MAX_HOLDS + 1):
        with pytest.raises(sqlite3.OperationalError):
            s.run_once(conn)
        clock.advance(slack_out.OUTAGE_HOLD_S)
    assert _states(conn, f"item:{sid}") == ["dead"]
    assert len(notes.sent) == 1 and "Slack didn't take its card for 2 hours" in notes.sent[0]


def test_a_short_reply_that_goes_through_doesnt_count_as_slack_working(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    """N4: only posts count, so a click's reply can't make an outage look like this card's."""
    web, notes = ChannelWeb(), Notices()
    web.down["CAP"] = "internal_error"
    s = _sender(web, clock, notes)
    sid = _waiting(conn, clock, "a4" * 32)
    s.run_once(conn)
    slack_out.enqueue_ephemeral(conn, clock, route=C2, user=ME, text="Approved")
    s.run_once(conn)
    assert _states(conn, f"item:{sid}") == ["queued"]
    for _ in range(slack_out.OUTAGE_CAP_HOLDS + 2):
        clock.advance(slack_out.OUTAGE_HOLD_S)
        s.run_once(conn)
    assert _states(conn, f"item:{sid}") == ["queued"] and not notes.sent


def test_held_cards_read_the_dead_posts_only_when_a_card_is_missing(
    conn: sqlite3.Connection, clock: FakeClock, setup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N5: every waiting card has one, so the dead-post scan never runs."""

    def boom(_c: sqlite3.Connection) -> set[str]:
        raise AssertionError("scanned")

    _waiting(conn, clock, "a5" * 32)  # its card is queued
    monkeypatch.setattr(slack_out, "refused_keys", boom)
    assert approvals.post_held_cards(conn, clock) == 0


def test_a_sender_being_dropped_sends_the_summary_it_owes(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    """N6: a Slack reload or `ecf slack remove` mid-window."""
    web, notes = FakeWeb(), Notices()
    s = _sender(web, clock, notes)
    for i in range(slack_out.NOTICES_PER_WINDOW + 1):
        slack_out.enqueue_post(conn, clock, key=f"daily:{i}", route=CAP, card=Card("d"))
        web.fail["chat.postMessage"] = [SlackError("chat.postMessage", "invalid_blocks")]
        s.run_once(conn)
    s.flush_notices(conn)
    assert notes.sent[-1].startswith("1 more posts didn't reach Slack")
    s.flush_notices(conn)
    assert len(notes.sent) == slack_out.NOTICES_PER_WINDOW + 1  # owed once


# Commit D: a short reply or a channel's closing to a channel that's gone completes at once.


def _ops(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = conn.execute("SELECT kind, outcome FROM slack_calls ORDER BY id").fetchall()
    return [(r[0], r[1]) for r in rows]


def _archive(conn: sqlite3.Connection, clock: FakeClock, channel: str) -> None:
    jobs.enqueue(
        conn,
        clock,
        jobs.Queue.SLACK_OUT,
        AddressId(channel),
        {"op": "archive", "channel": channel},
        timeout_s=slack_out.TIMEOUT_S,
    )


def test_an_ephemeral_or_archive_to_a_gone_channel_completes_at_once(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web, notes = FakeWeb(), Notices()
    s = _sender(web, clock, notes)
    slack_out.enqueue_ephemeral(conn, clock, route=CAP, user=ME, text="the excerpt")
    _archive(conn, clock, "COLD")
    web.fail["chat.postEphemeral"] = [SlackError("chat.postEphemeral", "channel_not_found")]
    web.fail["conversations.archive"] = [SlackError("conversations.archive", "channel_not_found")]
    assert s.run_once(conn) and s.run_once(conn)
    assert conn.execute("SELECT count(*) FROM jobs WHERE state != 'done'").fetchone()[0] == 0
    assert _ops(conn) == [("ephemeral", "channel_gone"), ("archive", "channel_gone")]
    assert conn.execute("SELECT route_ref FROM routes").fetchone()[0] == "CAP"  # left alone
    assert not notes.sent


def test_an_ephemeral_slack_refuses_is_dropped_without_a_notice(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web, notes = FakeWeb(), Notices()
    s = _sender(web, clock, notes)
    slack_out.enqueue_ephemeral(conn, clock, route=CAP, user=ME, text="x" * 5000)
    web.fail["chat.postEphemeral"] = [SlackError("chat.postEphemeral", "msg_too_long")]
    s.run_once(conn)
    [state] = conn.execute("SELECT state FROM jobs").fetchall()
    assert state[0] == "done" and _ops(conn) == [("ephemeral", "dead")] and not notes.sent


def test_an_ephemeral_dropped_on_an_outage_doesnt_count_as_slack_working(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web, notes = ChannelWeb(), Notices()
    web.down["CAP"] = "internal_error"
    s = _sender(web, clock, notes)
    sid = _waiting(conn, clock, "d1" * 32)
    s.run_once(conn)
    slack_out.enqueue_ephemeral(conn, clock, route=C2, user=ME, text="hi")
    web.fail["chat.postEphemeral"] = [SlackError("chat.postEphemeral", "internal_error")]
    s.run_once(conn)  # dropped, best effort: not a success
    for _ in range(slack_out.OUTAGE_CAP_HOLDS + 2):
        clock.advance(slack_out.OUTAGE_HOLD_S)
        s.run_once(conn)
    assert _states(conn, f"item:{sid}") == ["queued"] and not notes.sent


# E15: a post whose claim kept expiring until it dead-lettered is a lost post like any other.


def _expire_until_dead(conn: sqlite3.Connection, clock: FakeClock) -> None:
    """Each claim taken and never finished (the pass failed after claiming), until the limit."""
    for _ in range(jobs.DEFAULT_MAX_ATTEMPTS):
        assert jobs.claim(conn, clock, jobs.Queue.SLACK_OUT, slack_out.WORKER) is not None
        clock.advance(slack_out.TIMEOUT_S * jobs.CLAIM_FACTOR + 1)


def test_a_post_whose_claim_kept_expiring_gives_the_notice_and_one_more_try(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web, notes = FakeWeb(), Notices()
    s = _sender(web, clock, notes)
    sid = _waiting(conn, clock, "e1" * 32)
    _expire_until_dead(conn, clock)
    assert not s.run_once(conn)  # nothing else to send; the expired claim is reported
    assert _states(conn, f"item:{sid}") == ["dead"]
    assert notes.sent == [
        f"Email {sid[:8]}: its card didn't reach Slack; ecf posts it once more when it can."
        f" If that fails too, decide it with ecf approve {sid[:8]} or ecf reject {sid[:8]}."
    ]
    [row] = _audit(conn)
    assert json.loads(row["data"])["code"] == "claim_expired"
    assert approvals.post_held_cards(conn, clock) == 1


def test_an_edit_whose_claim_kept_expiring_says_the_card_wasnt_updated(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web, notes = FakeWeb(), Notices()
    s = _sender(web, clock, notes)
    sid = _waiting(conn, clock, "e2" * 32)
    s.run_once(conn)  # posted
    slack_out.enqueue_post(conn, clock, key=f"item:{sid}", route=CAP, card=Card("an edit"))
    _expire_until_dead(conn, clock)
    s.run_once(conn)
    assert "Its card in Slack wasn't updated" in notes.sent[0]


def test_a_short_reply_whose_claim_kept_expiring_sends_nothing(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    web, notes = FakeWeb(), Notices()
    s = _sender(web, clock, notes)
    slack_out.enqueue_ephemeral(conn, clock, route=CAP, user=ME, text="Approved")
    _expire_until_dead(conn, clock)
    s.run_once(conn)
    assert not notes.sent and not _audit(conn)


def test_a_failing_report_of_an_expired_claim_doesnt_stop_the_pass(
    conn: sqlite3.Connection, clock: FakeClock, setup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A busy database while reporting: logged; the pass goes on to the next job."""

    def busy(*_a: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    web, notes = FakeWeb(), Notices()
    s = _sender(web, clock, notes)
    sid = _waiting(conn, clock, "e3" * 32)
    _expire_until_dead(conn, clock)
    slack_out.enqueue_post(conn, clock, key="daily:x", route=CAP, card=Card("next"))
    monkeypatch.setattr(SlackSender, "_lost", busy)
    assert s.run_once(conn)  # the next job is sent
    assert _states(conn, f"item:{sid}") == ["dead"] and _states(conn, "daily:x") == ["done"]


def test_an_expired_claim_whose_record_cant_be_read_is_reported_as_a_post(
    conn: sqlite3.Connection, clock: FakeClock, setup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def busy(*_a: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    web, notes = FakeWeb(), Notices()
    s = _sender(web, clock, notes)
    sid = _waiting(conn, clock, "e4" * 32)
    _expire_until_dead(conn, clock)
    monkeypatch.setattr(slack_out, "message_ref", busy)
    s.run_once(conn)
    assert notes.sent[0].startswith(f"Email {sid[:8]}: its card didn't reach Slack")
