"""The Claude review reminder (V1.4 step 9; SPEC §13.3, §14.1; OD-115): one Operator Input Needed
per address while items wait for `/ecf-review` longer than `claude_review_reminder_hours`, and the
daily summary's lines."""

from __future__ import annotations

import sqlite3

import pytest

from ecf.errors import InvalidInputError
from ecf_server import claude_queue, claude_review, daily, health, settings
from ecf_server.checks import CheckReport
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from tests.test_claude_review import add, queue, waiting_item

HOUR = 3600


def _open(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["address_id"]: r["detail"] for r in conn.execute(
        "SELECT address_id, detail FROM alerts WHERE kind = 'claude_review'"
        " AND resolved_at IS NULL")}  # fmt: skip


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
    with write_tx(conn):  # it leaves the queue (here: the local fallback takes it)
        conn.execute("UPDATE items SET fallback_at = '2026-10-02T00:00:00Z' WHERE stable_id = ?",
                     (sid,))  # fmt: skip
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


def test_a_claimed_item_still_counts_and_a_handed_off_one_does_not(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    a, b = waiting_item(conn, clock, "c"), waiting_item(conn, clock, "c", 1)
    queue(conn, clock)  # a session holds both: they still wait
    with write_tx(conn):
        conn.execute("UPDATE items SET fallback_at = '2026-10-02T00:00:00Z' WHERE stable_id = ?",
                     (b,))  # the local fallback took it  # fmt: skip
    clock.advance(25 * HOUR)
    [o] = claude_queue.overdue(conn, clock.now())
    assert (o.address_id, o.count) == ("c", 1) and a


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
