"""The dead-man's switch (SPEC §11; V1.2 step 8a).

A Slack message is always scheduled (`chat.scheduleMessage`) in the summary channel a few check
intervals ahead: "ecf hasn't checked in since <time>". While ecf runs it is replaced before it is
due, so it never posts; if the service stops without a clean stop, the breaker trips or the
computer dies or sleeps, it posts, and you hear it in Slack even though ecf can't tell you.

- Business hours only by default (`deadman_offhours` false; operator decision 2026-09-29, OD-219):
  a message that would post outside business hours is moved to the next business-hours start plus
  3 workday intervals, so a laptop asleep overnight isn't reported unless it stays off into the
  workday. With `deadman_offhours` true it posts whenever it falls due.
- Scheduled `AHEAD` (3) check intervals ahead (the install's workday or off-hours interval, now);
  replaced once less than `RENEW` (2) intervals are left, by scheduling the new one first and then
  deleting the old one, always long before Slack's 60-second limit on deleting a message due to
  post (chat.deleteScheduledMessage, verified 2026-09-29, docs.slack.dev). About one API call per
  check interval.
- A clean stop deletes it. After a crash, the one left scheduled is kept or replaced by the same
  rule when the service starts again.
- The message carries no email content: the computer's name and a time.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ecf_server import schedule, slack_admin, slack_out
from ecf_server._slack import SlackError
from ecf_server.chat import Card, RouteRef
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.slack_chat import WebLike
from ecf_server.slack_render import fallback

AHEAD = 3
RENEW = 2
ID, CHANNEL, POST_AT = "slack_deadman_id", "slack_deadman_channel", "slack_deadman_post_at"
OFFHOURS = "deadman_offhours"  # False: post only in business hours (operator decision, OD-219)


def _settings(conn: sqlite3.Connection) -> dict[str, Any]:
    """The install's schedule settings and `deadman_offhours`, without address overrides."""
    s: dict[str, Any] = dict(schedule.DEFAULTS) | {OFFHOURS: False}
    for row in conn.execute(
        "SELECT key, value FROM settings WHERE key IN (SELECT value FROM json_each(?))",
        (json.dumps(list(s)),),
    ):
        s[row["key"]] = json.loads(row["value"])
    return s


def interval(conn: sqlite3.Connection, now: datetime) -> timedelta:
    """The install's current check interval (workday or off-hours), without address overrides."""
    return schedule.interval(now, _settings(conn))


def post_time(now: datetime, s: dict[str, Any]) -> datetime:
    """`AHEAD` intervals from now; unless `deadman_offhours` is on, a time outside business hours
    moves to the next business-hours start plus `AHEAD` workday intervals (08:30 with the
    defaults), so it posts only if ecf still isn't checking once the workday has begun."""
    t = now + schedule.interval(now, s) * AHEAD
    bh: dict[str, Any] = s["business_hours"]
    if s.get(OFFHOURS) is True or schedule.in_business_hours(t, bh):  # only a JSON true
        return t
    grace = timedelta(minutes=min(120, max(5, int(s["mail_fetch_interval_workday"])))) * AHEAD
    return next_business_start(t, bh) + grace


def next_business_start(t: datetime, bh: dict[str, Any]) -> datetime:
    """The first business-hours start after `t`."""
    tz = ZoneInfo(bh["tz"])
    local = t.astimezone(tz)
    start = time.fromisoformat(bh["start"])
    for days in range(8):
        day = local.date() + timedelta(days=days)
        candidate = datetime.combine(day, start, tzinfo=tz)
        if candidate > local and candidate.weekday() in bh["days"]:
            return candidate.astimezone(t.tzinfo)
    raise ValueError("business_hours has no working days")


def keep_armed(conn: sqlite3.Connection, clock: Clock, web: WebLike, *, computer: str) -> bool:
    """Make sure a message is scheduled far enough ahead; True if one was (re)scheduled."""
    channel = slack_admin.setting(conn, "slack_summary_channel")
    if not channel:
        return False
    now = clock.now()
    s = _settings(conn)
    step = schedule.interval(now, s)
    old_id, old_channel = slack_admin.setting(conn, ID), slack_admin.setting(conn, CHANNEL)
    old_at = slack_admin.setting(conn, POST_AT)
    if old_id and old_channel == channel and old_at and from_ts(old_at) - now >= step * RENEW:
        return False
    if old_id and old_at and from_ts(old_at) <= now:  # it fired while ecf was down: say it's back
        slack_out.enqueue_post(conn, clock, key=f"deadman-back:{to_ts(now)}",
                               route=RouteRef(channel),
                               card=Card("ecf is back", text=f"Checking again on {computer} since"
                                         f" {now.strftime('%Y-%m-%d %H:%M UTC')}; it had been"
                                         f" silent since before {old_at[:16]} UTC."))  # fmt: skip
    post_at = post_time(now, s)
    text = fallback(f"ecf hasn't checked in since {now.strftime('%Y-%m-%d %H:%M UTC')} "
                    f"({computer}). If the computer is on, run `ecf status` there.")  # fmt: skip
    r = web.call("chat.scheduleMessage", channel=channel, post_at=int(post_at.timestamp()),
                 text=text, unfurl_links=False, unfurl_media=False)  # fmt: skip
    new_id = str(r.get("scheduled_message_id", ""))
    now_ts = to_ts(now)
    with write_tx(conn):
        slack_admin.put_setting(conn, ID, new_id, now_ts, actor="service")
        slack_admin.put_setting(conn, CHANNEL, channel, now_ts, actor="service")
        slack_admin.put_setting(conn, POST_AT, to_ts(post_at), now_ts, actor="service")
    if old_id:
        _delete(web, old_channel, old_id)
    return True


def disarm(conn: sqlite3.Connection, web: WebLike) -> None:
    """`ecf service stop` or `uninstall`: delete the scheduled message. Not for a shutdown, logout
    or crash, when silence is what it should report (operator decision 2026-09-30, OD-222)."""
    old_id, channel = slack_admin.setting(conn, ID), slack_admin.setting(conn, CHANNEL)
    if old_id and channel:
        _delete(web, channel, old_id)
    with write_tx(conn):
        for key in (ID, CHANNEL, POST_AT):
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def _delete(web: WebLike, channel: str, scheduled_id: str) -> None:
    try:
        web.call("chat.deleteScheduledMessage", channel=channel,
                 scheduled_message_id=scheduled_id)  # fmt: skip
    except SlackError as exc:  # already posted (the computer slept) or gone: nothing to undo
        log.info("slack.deadman_delete_skipped", code=exc.code)
