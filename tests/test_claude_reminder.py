"""The Claude review reminder (V1.4 step 9; SPEC §13.3, §14.1; OD-115): one Operator Input Needed
per address while items wait for `/ecf-review` longer than `claude_review_reminder_hours`, the
loud escalation at twice the hours (v3.0.0, ADR 0031, P10), and the daily summary's lines."""

from __future__ import annotations

import json
import sqlite3

import pytest

from ecf.errors import InvalidInputError
from ecf.ids import StableId
from ecf_server import alerts, claude_queue, claude_review, daily, health, items, settings
from ecf_server.checks import CheckReport
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.state_machine import Status, TransitionContext
from tests.test_claude_review import add, queue, waiting_item

HOUR = 3600
ESC = claude_review.ESCALATE_ALERT


def _open(conn: sqlite3.Connection, kind: str = "claude_review") -> dict[str, str]:
    return {r["address_id"]: r["detail"] for r in conn.execute(
        "SELECT address_id, detail FROM alerts WHERE kind = ? AND resolved_at IS NULL",
        (kind,))}  # fmt: skip


def _classified(conn: sqlite3.Connection, clock: FakeClock, sid: str) -> None:
    """Claude classified it: it leaves the queue."""
    items.transition(conn, clock, StableId(sid), Status.CLASSIFIED, TransitionContext(),
                         actor="classifier")  # fmt: skip


def test_the_reminder_opens_after_the_hours_and_resolves_when_the_queue_drains(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    sid = waiting_item(conn, clock, "c")
    n = FakeNotifier()
    clock.advance(23 * HOUR)
    assert claude_review.remind(conn, clock, n) == 0 and _open(conn) == {}
    clock.advance(1 * HOUR)
    assert claude_review.remind(conn, clock, n) == 1
    assert "1 email(s) have waited more than 24 h for /ecf-review" in _open(conn)["c"]
    assert n.sent[-1][0] == "[ecf-alert] Operator Input Needed: Claude review waiting"
    claude_review.remind(conn, clock, n)  # still open: no second notification
    assert len(n.sent) == 1
    health.after_check(conn, clock, n, CheckReport("c", "ok", "t"))  # a mail check leaves it
    assert "c" in _open(conn)
    _classified(conn, clock, sid)  # it leaves the queue
    assert claude_review.remind(conn, clock, n) == 0 and _open(conn) == {}
    assert n.sent[-1][0] == "[ecf-alert] Resolved: Operator Input Needed: Claude review waiting"


def test_the_hours_are_a_setting_and_a_paused_address_gets_no_reminder(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    add(conn, clock, "d", "C")
    waiting_item(conn, clock, "c")
    waiting_item(conn, clock, "d")
    settings.set_value(conn, clock, "claude_review_reminder_hours", "2", address=None,
                       actor="os_user")  # fmt: skip
    for bad in ("0", "169"):
        with pytest.raises(InvalidInputError):
            settings.set_value(conn, clock, "claude_review_reminder_hours", bad, address=None,
                               actor="os_user")  # fmt: skip
    clock.advance(2 * HOUR + 1)
    n = FakeNotifier()
    assert claude_review.remind(conn, clock, n) == 2
    with write_tx(conn):
        conn.execute("UPDATE addresses SET paused = 1 WHERE address_id = 'd'")
    assert claude_review.remind(conn, clock, n) == 1 and set(_open(conn)) == {"c"}


def test_a_claimed_item_still_counts(conn: sqlite3.Connection, clock: FakeClock) -> None:
    add(conn, clock, "c", "C")
    waiting_item(conn, clock, "c")
    waiting_item(conn, clock, "c", 1)
    queue(conn, clock)  # a session holds both: they still wait
    clock.advance(25 * HOUR)
    [o] = claude_queue.overdue(conn, clock.now())
    assert (o.address_id, o.count) == ("c", 2)


# ---- the escalation at twice the hours (v3.0.0, ADR 0031, R-D4, P10) -------------------------


def test_the_escalation_opens_at_twice_the_hours_loud_and_resolves_when_the_queue_drains(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    sid = waiting_item(conn, clock, "c")
    n = FakeNotifier()
    clock.advance(47 * HOUR)
    assert claude_review.remind(conn, clock, n) == 1  # the reminder, not yet the escalation
    assert claude_review.escalate(conn, clock, n) == 0 and _open(conn, ESC) == {}
    clock.advance(1 * HOUR)
    assert claude_review.escalate(conn, clock, n) == 1
    detail = _open(conn, ESC)["c"]
    assert detail.startswith("c: 1 email(s) have waited more than 48 h for /ecf-review (the"
                             " oldest since ")  # fmt: skip
    assert "W-9" not in detail and "ann@" not in detail and "Ann" not in detail  # counts, dates
    assert n.sent[-1][0] == "[ecf-alert] Operator Input Needed: Claude review overdue"
    assert ESC in alerts.LOUD_KINDS and alerts.CLASS_OF[ESC] == "operator"
    claude_review.escalate(conn, clock, n)  # still open: no second notification
    assert len([t for t, _ in n.sent if "overdue" in t]) == 1
    health.after_check(conn, clock, n, CheckReport("c", "ok", "t"))  # a mail check leaves it
    assert "c" in _open(conn, ESC)
    _classified(conn, clock, sid)
    assert claude_review.escalate(conn, clock, n) == 0 and _open(conn, ESC) == {}
    assert n.sent[-1][0] == "[ecf-alert] Resolved: Operator Input Needed: Claude review overdue"


def test_the_escalation_follows_the_hours_and_skips_a_paused_address(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    add(conn, clock, "d", "C")
    waiting_item(conn, clock, "c")
    waiting_item(conn, clock, "d")
    settings.set_value(conn, clock, "claude_review_reminder_hours", "2", address=None,
                       actor="os_user")  # fmt: skip
    clock.advance(4 * HOUR + 1)
    n = FakeNotifier()
    assert claude_review.escalate(conn, clock, n) == 2
    with write_tx(conn):
        conn.execute("UPDATE addresses SET paused = 1 WHERE address_id = 'd'")
    assert claude_review.escalate(conn, clock, n) == 1 and set(_open(conn, ESC)) == {"c"}


def test_the_escalation_mentions_you_in_slack(conn: sqlite3.Connection, clock: FakeClock) -> None:
    from tests.test_digests_daily import slack_setup  # noqa: PLC0415

    slack_setup(conn, clock)
    add(conn, clock, "c", "C")
    waiting_item(conn, clock, "c")
    clock.advance(49 * HOUR)
    claude_review.escalate(conn, clock, FakeNotifier())
    alerts.sweep(conn, clock)
    posts = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM jobs WHERE queue ="
                                                    " 'slack_out'")]  # fmt: skip
    [post] = [p for p in posts if "Claude review overdue" in p["card"]["title"]]
    assert post["card"]["mention"] == "U0ME1"  # LOUD_KINDS: the post mentions you


def test_the_daily_summary_counts_the_claude_queue_and_its_late_items(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    waiting_item(conn, clock, "c")
    clock.advance(25 * HOUR)
    waiting_item(conn, clock, "c", 1)
    c = daily.card(conn, clock.now(), "2026-10-03")
    assert dict(c.fields)["c"] == (
        "0 waiting on you, 2 waiting for /ecf-review; last 24 h: 0 escalated, 0 not fully scanned"
    )
    [line] = [x for x in c.text.splitlines() if x.startswith("Waiting for /ecf-review")]
    assert line.startswith("Waiting for /ecf-review more than 24 h: 1 (c 1; the oldest since ")
    assert line.endswith("Run `ecf claude` and type /ecf-review.")
