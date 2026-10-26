"""`ecf slack repost` (OD-557): approval cards drawn again with their current grant, the rest sent
again as last posted, approval cards' dead posts cleared so they come back, a card recorded in a
channel the item no longer uses moved, and `ecf doctor`'s count of lost cards."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from ecf.cli_slack import repost_lines
from ecf.errors import NotFoundError
from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import answers, approvals, items, slack_admin, slack_doctor, slack_out
from ecf_server.actions import Planned
from ecf_server.chat import Card, RouteRef
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.state_machine import TransitionContext

ME = "U0ME1"
ARCHIVE = [Planned("archive")]


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
        for k, v in (
            ("slack_app_id", "A1"),
            ("slack_team_id", "T1"),
            ("slack_member_id", ME),
            ("slack_summary_channel", "CSUM"),
        ):
            slack_admin.put_setting(conn, k, v, now, actor="test")


def _waiting(conn: sqlite3.Connection, clock: FakeClock, sid: str) -> str:
    """An approval waiting, its card queued."""
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
    approvals.request(conn, clock, sid, ARCHIVE)
    return sid


def _question(conn: sqlite3.Connection, clock: FakeClock, sid: str) -> str:
    """A question waiting for your answer, its card queued."""
    items.create_item(
        conn,
        clock,
        stable_id=StableId(sid),
        address_id=AddressId("ap"),
        content_hash="h",
        facts="{}",
    )
    for to in (Status.CLASSIFIED, Status.PROPOSED):
        items.transition(conn, clock, StableId(sid), to, TransitionContext(), actor="test")
    answers.ask(conn, clock, sid, "Which invoice is this?", member=ME)
    return sid


def _jobs(conn: sqlite3.Connection, key: str) -> list[tuple[str, dict[str, Any]]]:
    rows = conn.execute(
        "SELECT state, payload FROM jobs WHERE queue = 'slack_out'"
        " AND json_extract(payload, '$.key') = ? ORDER BY rowid",
        (key,),
    )
    return [(r[0], json.loads(r[1])) for r in rows]


def _sent(conn: sqlite3.Connection, key: str, channel: str = "CAP") -> None:
    """The key's queued job went through: done, and its message recorded."""
    now = "2026-10-25T00:00:00.000000Z"
    with write_tx(conn):
        conn.execute(
            "UPDATE jobs SET state = 'done' WHERE queue = 'slack_out'"
            " AND json_extract(payload, '$.key') = ?",
            (key,),
        )
        post = json.dumps({"key": key, "channel": channel, "card": {"title": "old"}})
        conn.execute(
            "INSERT INTO slack_messages (key, channel, ts, created_at, updated_at, post)"
            " VALUES (?, ?, '1.0001', ?, ?, ?)",
            (key, channel, now, now, post),
        )


def _died(conn: sqlite3.Connection, key: str, error: str = "slack:invalid_blocks") -> None:
    """The key's queued jobs gave up (set by hand, in the form this commit's code already reads)."""
    with write_tx(conn):
        conn.execute(
            "UPDATE jobs SET state = 'dead', last_error = ? WHERE queue = 'slack_out'"
            " AND state = 'queued' AND json_extract(payload, '$.key') = ?",
            (error, key),
        )


def _grant(conn: sqlite3.Connection, sid: str) -> str:
    row = conn.execute(
        "SELECT grant_id FROM grants WHERE stable_id = ? AND status = 'issued'", (sid,)
    ).fetchone()
    return str(row[0])


def _buttons(p: dict[str, Any]) -> set[str]:
    return {b["ref"] for b in p["card"]["buttons"]}


def _move(conn: sqlite3.Connection, clock: FakeClock, sid: str, *to: Status) -> None:
    for s in to:
        items.transition(conn, clock, StableId(sid), s, TransitionContext(), actor="test")


def _doctor(conn: sqlite3.Connection) -> dict[str, str]:
    rows = slack_doctor._delivery(conn)  # pyright: ignore[reportPrivateUsage]
    return next(r for r in rows if r["name"] == "slack posts")


def test_a_dead_approval_card_is_cleared_and_counted_as_coming_back(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    sid = _waiting(conn, clock, "a" * 64)
    _died(conn, f"item:{sid}")
    assert "1 card(s) of emails waiting for you" in _doctor(conn)["detail"]
    r = approvals.repost_cards(conn, clock)
    assert [s for s, _ in _jobs(conn, f"item:{sid}")] == ["done"]
    assert r["reposting"] == 1 and r["not_recovered"] == 0
    rows = slack_doctor._delivery(conn)  # pyright: ignore[reportPrivateUsage]
    assert not [x for x in rows if x["name"] == "slack posts"]  # cleared: nothing gave up now
    assert approvals.post_held_cards(conn, clock) == 1  # comes back with today's card


def test_a_card_left_with_an_old_grant_is_drawn_again_once_with_the_current_one(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    sid = _waiting(conn, clock, "b" * 64)
    key = f"item:{sid}"
    _sent(conn, key)
    old = _grant(conn, sid)
    with write_tx(conn):  # re-offered under a new grant; that edit was refused
        conn.execute("UPDATE grants SET status = 'voided' WHERE grant_id = ?", (old,))
        conn.execute(
            "INSERT INTO grants SELECT 'g-new', stable_id, action_hash, content_hash,"
            " principal, 'issued', NULL, expires_at, NULL FROM grants"
            " WHERE grant_id = ?",
            (old,),
        )
    slack_out.enqueue_post(conn, clock, key=key, route=RouteRef("CAP"), card=Card("Offered again"))
    _died(conn, key)
    approvals.repost_cards(conn, clock)
    queued = [p for s, p in _jobs(conn, key) if s == "queued"]
    assert len(queued) == 1 and "g-new" in _buttons(queued[0]) and old not in _buttons(queued[0])
    assert [s for s, _ in _jobs(conn, key)][-2:] == ["done", "queued"]  # the dead edit, done


@pytest.mark.parametrize(
    ("expiries", "title"), [(0, "Approve? "), (1, "Expired, decide again"), (2, "Offered again")]
)
def test_a_redrawn_card_keeps_its_title(
    conn: sqlite3.Connection, clock: FakeClock, setup: None, expiries: int, title: str
) -> None:
    sid = _waiting(conn, clock, f"{expiries}" * 64)
    _sent(conn, f"item:{sid}")
    with write_tx(conn):
        conn.execute("UPDATE items SET expiry_count = ? WHERE stable_id = ?", (expiries, sid))
    approvals.repost_cards(conn, clock)
    [(_, p)] = [j for j in _jobs(conn, f"item:{sid}") if j[0] == "queued"]
    assert p["card"]["title"].startswith(title) and not p["card"].get("mention")


def test_a_card_in_a_forgotten_channel_moves_to_the_current_one(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    sid = _waiting(conn, clock, "c" * 64)
    key = f"item:{sid}"
    _sent(conn, key, channel="COLD")  # the address's route is CAP now
    r = approvals.repost_cards(conn, clock)
    assert slack_out.message_ref(conn, key) is None and r["reposting"] == 1
    assert [s for s, _ in _jobs(conn, key)] == ["done"]  # nothing sent to COLD
    assert (
        conn.execute("SELECT route_ref FROM routes WHERE address_id = 'ap'").fetchone()[0] == "CAP"
    )
    assert approvals.post_held_cards(conn, clock) == 1
    assert _jobs(conn, key)[-1][1]["channel"] == "CAP"


def test_with_no_route_yet_the_card_waits_for_one(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    sid = _waiting(conn, clock, "d" * 64)
    key = f"item:{sid}"
    _sent(conn, key, channel="COLD")
    with write_tx(conn):
        conn.execute("DELETE FROM routes")
    approvals.repost_cards(conn, clock)
    assert slack_out.message_ref(conn, key) is None
    assert approvals.post_held_cards(conn, clock) == 1 and len(_jobs(conn, key)) == 1  # no route
    with write_tx(conn):
        conn.execute(
            "INSERT INTO routes (address_id, surface, route_ref, name)"
            " VALUES ('ap', 'slack', 'CNEW', 'ecf-default-ap')"
        )
    assert approvals.post_held_cards(conn, clock) == 1
    assert _jobs(conn, key)[-1][1]["channel"] == "CNEW"


def test_one_card_moved_and_cleared_counts_once(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    sid = _waiting(conn, clock, "e" * 64)
    key = f"item:{sid}"
    _sent(conn, key, channel="COLD")
    slack_out.enqueue_post(conn, clock, key=key, route=RouteRef("COLD"), card=Card("an edit"))
    _died(conn, key)
    assert approvals.repost_cards(conn, clock)["reposting"] == 1


def test_scope_leaves_other_cards_and_posts_alone(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    question = _question(conn, clock, "f" * 64)
    old = _waiting(conn, clock, "9" * 64)
    with write_tx(conn):  # old mail a scan marked
        conn.execute("UPDATE items SET facts = '{\"backfill\": 1}' WHERE stable_id = ?", (old,))
        conn.execute(
            "INSERT INTO escalations (stable_id, address_id, state, thread_key,"
            " created_at) VALUES (?, 'ap', 'posted', ?, 'now')",
            (question, f"item:{question}"),
        )
    slack_out.enqueue_post(
        conn, clock, key="digest:ap:x", route=RouteRef("CAP"), card=Card("a digest")
    )
    for key in (f"item:{question}", f"item:{old}", "digest:ap:x"):
        _died(conn, key)
    slack_out.enqueue_post(
        conn, clock, key="digest:ap:y", route=RouteRef("CAP"), card=Card("queued")
    )
    r = approvals.repost_cards(conn, clock)
    assert r == {"queued": 0, "reposting": 0, "not_recovered": 1, "backlog_held": False}
    states = {
        k: [s for s, _ in _jobs(conn, k)]
        for k in (f"item:{question}", f"item:{old}", "digest:ap:x", "digest:ap:y")
    }
    assert states == {
        f"item:{question}": ["dead"],
        f"item:{old}": ["dead"],
        "digest:ap:x": ["dead"],
        "digest:ap:y": ["queued"],
    }
    assert conn.execute("SELECT state FROM escalations").fetchone()[0] == "posted"


def test_needs_you_outside_the_summary_channel_is_skipped(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    _sent(conn, "needs-you", channel="COLDSUM")
    assert approvals.repost_cards(conn, clock)["queued"] == 0
    with write_tx(conn):
        conn.execute("UPDATE slack_messages SET channel = 'CSUM' WHERE key = 'needs-you'")
    assert approvals.repost_cards(conn, clock)["queued"] == 1


def test_an_item_decided_meanwhile_is_not_drawn_again(
    conn: sqlite3.Connection, clock: FakeClock, setup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    one, two = _waiting(conn, clock, "1" * 64), _waiting(conn, clock, "2" * 64)
    for sid in (one, two):
        _sent(conn, f"item:{sid}")
    real = approvals._card  # pyright: ignore[reportPrivateUsage]
    first = True

    def card(*a: Any, **k: Any) -> None:
        nonlocal first
        if first:  # between the select and the re-read, the other item is decided
            first = False
            other = two if a[2]["stable_id"] == one else one
            approvals.reject(conn, clock, other, actor="test")
        real(*a, **k)

    monkeypatch.setattr(approvals, "_card", card)
    assert approvals.repost_cards(conn, clock)["queued"] == 1


def test_a_high_backlog_is_reported(
    conn: sqlite3.Connection, clock: FakeClock, setup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = _waiting(conn, clock, "7" * 64)
    _died(conn, f"item:{sid}")

    def backlog(_conn: sqlite3.Connection) -> int:
        return 101

    monkeypatch.setattr(approvals.claude_queue, "backlog", backlog)
    r = approvals.repost_cards(conn, clock)
    assert r["backlog_held"] is True
    assert approvals.post_held_cards(conn, clock) == 0  # waits for the backlog
    assert any("once 100 or fewer emails wait for Claude review" in x for x in repost_lines(r))


def test_repost_and_a_held_card_converge_on_one_message(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    sid = _waiting(conn, clock, "8" * 64)
    key = f"item:{sid}"
    _sent(conn, key)
    approvals.repost_cards(conn, clock)
    assert approvals.post_held_cards(conn, clock) == 0  # the card exists: not posted twice
    assert [s for s, _ in _jobs(conn, key)] == ["done", "queued"]


def test_doctor_counts_cards_not_posts(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    sid = _waiting(conn, clock, "6" * 64)
    key = f"item:{sid}"
    _died(conn, key)
    slack_out.enqueue_post(conn, clock, key=key, route=RouteRef("CAP"), card=Card("again"))
    _died(conn, key)
    assert _doctor(conn)["detail"] == (
        "2 Slack post(s) gave up, for 1 card(s) of emails waiting for you"
    )
    assert "ecf slack repost" in _doctor(conn)["fix"]


def test_doctor_counts_an_escalation_and_a_question_but_not_old_mail_or_superseded(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    q = _question(conn, clock, "b" * 64)
    old, ok = (_waiting(conn, clock, c * 64) for c in "cd")
    esc = "a" * 64  # an escalated email still waiting for Claude, its card posted
    items.create_item(
        conn,
        clock,
        stable_id=StableId(esc),
        address_id=AddressId("ap"),
        content_hash="h",
        facts="{}",
    )
    _move(conn, clock, esc, Status.AWAITING_CLAUDE)
    slack_out.enqueue_post(
        conn, clock, key=f"item:{esc}", route=RouteRef("CAP"), card=Card("an escalation")
    )
    with write_tx(conn):
        conn.execute(
            "INSERT INTO escalations (stable_id, address_id, state, thread_key,"
            " created_at) VALUES (?, 'ap', 'posted', ?, 'now')",
            (esc, f"item:{esc}"),
        )
        conn.execute('UPDATE items SET facts = \'{"reprocess": "r1"}\' WHERE stable_id = ?', (old,))
    for sid in (esc, q, old, ok):
        _died(conn, f"item:{sid}")
    slack_out.enqueue_post(conn, clock, key=f"item:{ok}", route=RouteRef("CAP"), card=Card("later"))
    _sent(conn, f"item:{ok}")  # a later post went through: supersedes the dead one
    assert "for 2 card(s)" in _doctor(conn)["detail"]


def test_doctor_with_only_one_off_posts(
    conn: sqlite3.Connection, clock: FakeClock, setup: None
) -> None:
    slack_out.enqueue_post(
        conn, clock, key="digest:ap:x", route=RouteRef("CAP"), card=Card("a digest")
    )
    _died(conn, "digest:ap:x")
    assert _doctor(conn)["detail"] == (
        "1 Slack post(s) gave up: one-off posts; nothing to do unless it repeats"
    )


def test_repost_lines() -> None:
    r = {"queued": 3, "reposting": 2, "not_recovered": 1, "backlog_held": False}
    assert repost_lines(r) == [
        "Queued 3 card(s) to edit or re-post.",
        "2 approval card(s) that aren't in Slack now will be posted again shortly.",
        "1 card(s) of emails waiting for you can't be put back in Slack; ecf inbox lists them.",
    ]
    assert repost_lines(
        {"queued": 0, "reposting": 0, "not_recovered": 0, "backlog_held": True}
    ) == ["Queued 0 card(s) to edit or re-post."]


def test_repost_is_refused_before_slack_is_installed(conn: sqlite3.Connection) -> None:
    """A guard: the refusal `ecf slack reauthorize` already had, under its public name."""
    with pytest.raises(NotFoundError, match="Slack isn't installed"):
        slack_admin.require_installed(conn)
