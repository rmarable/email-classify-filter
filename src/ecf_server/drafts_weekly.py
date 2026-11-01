"""The weekly drafts post (M8 plan §7): counts only, for the ISO week just ended, in the summary
channel, on the first tick of a new week. A week with no drafts posts nothing."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from ecf_server import draft_stats, slack_admin, slack_out, slack_routes
from ecf_server.chat import Card
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx

LAST = "drafts_weekly_on"  # the last ISO week handled


def week_of(when: datetime) -> str:
    year, week, _day = when.isocalendar()
    return f"{year}-W{week:02d}"


def _bounds(now: datetime) -> tuple[datetime, datetime]:
    """Monday 00:00 UTC of last week, and of this one."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    this = day - timedelta(days=now.weekday())
    return this - timedelta(days=7), this


def run(conn: sqlite3.Connection, clock: Clock) -> bool:
    route = slack_routes.summary_route(conn)
    now = clock.now()
    start, end = _bounds(now)
    week = week_of(start)
    if route is None or slack_admin.setting(conn, LAST) == week:
        return False
    counts = draft_stats.week_counts(conn, start, end)
    if counts["drafts"]:
        slack_out.enqueue_post(conn, clock, key=f"drafts-weekly:{week}", route=route,
                               card=card(week, counts))  # fmt: skip
    with write_tx(conn):
        slack_admin.put_setting(conn, LAST, week, to_ts(now), actor="service")
    return bool(counts["drafts"])


def card(week: str, c: dict[str, int]) -> Card:
    return Card(
        title=f"Drafts in {week}",
        fields=(("Written", str(c["drafts"])), ("Refused by the checker", str(c["refused"])),
                ("Saved", str(c["saved"])), ("Sent unchanged", str(c["sent_unchanged"])),
                ("Sent edited", str(c["sent_edited"])), ("Rejected", str(c["rejected"])),
                ("Expired", str(c["expired"])), ("👍 / 👎", f"{c['up']} / {c['down']}")),
        note="More: ecf drafts stats",
    )  # fmt: skip
