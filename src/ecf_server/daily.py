"""The daily summary and the channel-member check (SPEC §10.1, OD-215; V1.2 step 8b).

**Daily summary:** once a day, at the start of business hours (the install's `business_hours`), in
the summary channel. V1.2 says: what's waiting on you per address, open items, stale items (up to
10), approvals that expired twice (listed only here, §6.2), escalations in the last 24 hours,
emails not fully scanned in the last 24 hours, paused addresses, and who else is in ecf's channels.
Lines for backups, battery and backlog, and newer models arrive with those features (V1.3-V1.5).

**Channel members** (OD-215): anyone in a private channel can invite others, so ecf checks every
recorded channel hourly for members other than you and its own bot. The daily summary lists them;
when the set changes, a Security Notice goes to your DM and the summary channel. The first check
records the set without a notice.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ecf.status import OPEN
from ecf_server import cards, inbox, pause, schedule, slack_admin, slack_out, slack_routes
from ecf_server.chat import Card, ChatSurface, RouteRef
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx

LAST = "slack_daily_on"  # the local date of the last summary
MEMBERS, MEMBERS_AT = "slack_channel_others", "slack_channel_others_at"
MEMBERS_EVERY = timedelta(hours=1)
STALE_LIST = 10
DAY = timedelta(days=1)


def _install_bh(conn: sqlite3.Connection) -> dict[str, Any]:
    bh: dict[str, Any] = dict(schedule.DEFAULTS["business_hours"])
    row = conn.execute("SELECT value FROM settings WHERE key = 'business_hours'").fetchone()
    return json.loads(row[0]) if row else bh


def due(conn: sqlite3.Connection, now: datetime) -> str | None:
    """Today's local date if the summary is due (a business day, at or after the start, and not
    posted yet today); else None."""
    bh = _install_bh(conn)
    local = now.astimezone(ZoneInfo(bh["tz"]))
    if local.weekday() not in bh["days"] or local.time() < time.fromisoformat(bh["start"]):
        return None
    today = local.date().isoformat()
    return None if slack_admin.setting(conn, LAST) == today else today


def run(conn: sqlite3.Connection, clock: Clock) -> bool:
    route = slack_routes.summary_route(conn)
    today = due(conn, clock.now())
    if route is None or today is None:
        return False
    slack_out.enqueue_post(conn, clock, key=f"daily:{today}", route=route,
                           card=card(conn, clock.now(), today))  # fmt: skip
    with write_tx(conn):
        slack_admin.put_setting(conn, LAST, today, to_ts(clock.now()), actor="service")
    return True


def card(conn: sqlite3.Connection, now: datetime, today: str) -> Card:
    since = to_ts(now - DAY)
    waiting = inbox.inbox(conn)
    per_addr: dict[str, list[int]] = {}
    open_ = json.dumps(sorted(OPEN))
    model = ("new", "awaiting_claude", "classified")  # waiting for a model, not for you
    for (aid,) in conn.execute(
        "SELECT address_id FROM addresses WHERE removed_at IS NULL ORDER BY address_id"
    ):
        n_open = _count(conn, "SELECT count(*) FROM items WHERE address_id = ? AND status IN"
                        " (SELECT value FROM json_each(?))", aid, open_)  # fmt: skip
        esc = _count(conn, "SELECT count(*) FROM escalations WHERE address_id = ?"
                     " AND created_at > ?", aid, since)  # fmt: skip
        unscanned = _count(conn, "SELECT count(*) FROM items WHERE address_id = ?"
                           " AND created_at > ? AND json_extract(facts, '$.content_unscanned') = 1",
                           aid, since)  # fmt: skip
        mine = [i for i in waiting if i["address_id"] == aid]
        n_model = _count(conn, "SELECT count(*) FROM items WHERE address_id = ? AND status IN"
                         " (SELECT value FROM json_each(?))", aid, json.dumps(model))  # fmt: skip
        n_model -= sum(1 for i in mine if i["status"] in model)  # escalated ones wait on you
        per_addr[aid] = [len(mine), n_model, n_open - len(mine) - n_model, esc, unscanned]
    fields = tuple((aid, _counts(*n)) for aid, n in per_addr.items())
    lines: list[str] = []
    stale = [i for i in waiting if i["stale"]]
    if stale:
        lines += [f"Stale (30+ days): {len(stale)}", *(
            f"  {i['short_id']} {i['address_id']}: {cards.short_sender(i['sender'])}: "
            f"{i['subject'][:50]}"
            for i in stale[:STALE_LIST])]  # fmt: skip
    twice = conn.execute("SELECT stable_id, address_id FROM items WHERE status = 'expired'"
                         " AND expiry_count >= 2 ORDER BY stable_id").fetchall()  # fmt: skip
    if twice:
        lines.append("Approvals that expired twice (decide with ecf approve or ecf item resolve): "
                     + ", ".join(f"{r[0][:8]} ({r[1]})" for r in twice))  # fmt: skip
    paused = pause.paused_addresses(conn)
    if paused:
        lines.append(f"Paused: {', '.join(paused)} (fraud checks continue)")
    others = recorded_others(conn)
    if others:
        lines.append("Others in ecf's channels: " + "; ".join(
            f"{name}: {', '.join(ids)}" for name, ids in sorted(others.items())))  # fmt: skip
    else:
        lines.append("Nobody else is in ecf's channels.")
    lines.append("All of it: ecf inbox")
    return Card(f"Daily summary {today}", fields=fields, text="\n".join(lines))


def _counts(waiting: int, model: int, other: int, escalated: int, unscanned: int) -> str:
    """Waiting on you apart from mail waiting for the classifier ("40 open" mixed them, V1.2
    shadow run, 2026-09-30)."""
    parts = [f"{waiting} waiting on you"]
    if model:
        parts.append(f"{model} waiting for the classifier (V1.3)")
    if other:
        parts.append(f"{other} in progress")
    return (f"{', '.join(parts)}; last 24 h: {escalated} escalated,"
            f" {unscanned} not fully scanned")  # fmt: skip


def _count(conn: sqlite3.Connection, sql: str, *args: Any) -> int:
    return int(conn.execute(sql, args).fetchone()[0])


# ---- channel members (OD-215) -----------------------------------------------------------------


def recorded_others(conn: sqlite3.Connection) -> dict[str, list[str]]:
    raw = slack_admin.setting(conn, MEMBERS)
    return json.loads(raw) if raw else {}


def check_members(
    conn: sqlite3.Connection, clock: Clock, chat: ChatSurface, notifier: Any, *, force: bool = False
) -> dict[str, list[str]] | None:
    """Hourly: members of every recorded channel other than you and the bot. Returns the set
    when it checked (None when not due); a change sends a Security Notice."""
    now = clock.now()
    at = slack_admin.setting(conn, MEMBERS_AT)
    if not force and at and now - from_ts(at) < MEMBERS_EVERY:
        return None
    ident = slack_admin.identity(conn)
    if ident is None or not ident.member:
        return None
    mine = {ident.member, slack_admin.setting(conn, slack_admin.BOT_USER)}
    others: dict[str, list[str]] = {}
    for ch in slack_routes.list_channels(conn):
        extra = sorted(set(chat.members(RouteRef(ch["channel"]))) - mine)
        if extra:
            others[ch["name"] or ch["channel"]] = extra
    before = slack_admin.setting(conn, MEMBERS)
    with write_tx(conn):
        slack_admin.put_setting(conn, MEMBERS, json.dumps(others, sort_keys=True), to_ts(now),
                                actor="service")  # fmt: skip
        slack_admin.put_setting(conn, MEMBERS_AT, to_ts(now), to_ts(now), actor="service")
    if before and json.loads(before) != others:
        slack_admin.notice(conn, clock, notifier, _change_text(json.loads(before), others),
                           dms=[ident.member])  # fmt: skip
    return others


def _change_text(before: dict[str, list[str]], after: dict[str, list[str]]) -> str:
    parts: list[str] = []
    for name in sorted(set(before) | set(after)):
        added = sorted(set(after.get(name, [])) - set(before.get(name, [])))
        removed = sorted(set(before.get(name, [])) - set(after.get(name, [])))
        if added:
            parts.append(f"joined {name}: {', '.join(added)}")
        if removed:
            parts.append(f"left {name}: {', '.join(removed)}")
    return (
        "People in ecf's private channels changed: " + "; ".join(parts) + ". They can see "
        "subjects, senders and escalations there. If you didn't add them, remove them."
    )
