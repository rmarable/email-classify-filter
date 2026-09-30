"""The pinned "Needs you" message and stale items (SPEC §10.1, §6.5; V1.2 step 8a).

"Needs you" is one message pinned in the summary channel: what's waiting on you (`ecf inbox`), the
top 20 (stale first, then oldest) with counts, which addresses are paused, and Pause all or Resume
all. Its header says buttons work only while the computer is awake and when it was last connected.
It is edited only when its content changes, and at most hourly otherwise so "last connected" stays
true while the computer is on (§10.1; the time can't change while it sleeps, which is the point).

Stale items (§6.5, OD-042): an open item older than 30 days gets `stale`; the summary channel hears
it once, when it happens ("reminded once"), then only the daily summary lists them. Stale `held`,
`new` and `awaiting_claude` items appear as one count line, not one line each.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sqlite3
from datetime import datetime, timedelta

from ecf.status import OPEN, Status
from ecf_server import cards, inbox, pause, slack_admin, slack_out, slack_routes
from ecf_server.chat import Button, Card
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx

KEY = "needs-you"
HASH, AT = "slack_needs_you_hash", "slack_needs_you_at"
TOP = 20
REFRESH = timedelta(hours=1)
STALE_AFTER = timedelta(days=30)  # stale_item_days' default (OD-042); `ecf settings set` changes it
COUNT_ONLY: frozenset[Status] = frozenset({Status.HELD, Status.NEW, Status.AWAITING_CLAUDE})
LABELS = {
    "new": "escalated",
    "held": "held (assist)",
    "awaiting_approval": "waiting for approval",
    "awaiting_stepup": "waiting for step-up at your computer",
    "delayed": "sending soon",
    "failed": "failed",
    "failed_unknown": "send outcome unknown",
    "expired": "approval expired",
    "needs_clarification": "has a question",
    "needs_human": "needs you",
    "undo_failed": "undo failed",
}


def host() -> str:
    return platform.node().split(".")[0] or "this computer"


def card(conn: sqlite3.Connection, *, computer: str, last_connected: datetime) -> Card:
    waiting = inbox.inbox(conn)
    stale_bulk = [i for i in waiting if i["stale"] and Status(i["status"]) in COUNT_ONLY]
    listed = [i for i in waiting if i not in stale_bulk]
    lines = [
        f"{'STALE ' if i['stale'] else ''}{i['short_id']} {i['address_id']}: "
        f"{LABELS.get(i['status'], i['status'])}: {cards.short_sender(i['sender'])}: "
        f"{i['subject'][:60]}"
        for i in listed[:TOP]
    ]
    if len(listed) > TOP:
        lines.append(f"... and {len(listed) - TOP} more")
    if stale_bulk:
        lines.append(f"{len(stale_bulk)} stale email(s) waiting 30+ days: ecf inbox --stale")
    paused = pause.paused_addresses(conn)
    if paused:
        lines.append(f"Paused: {', '.join(paused)} (fraud checks continue)")
    lines.append("All of it: ecf inbox")
    stale = sum(1 for i in waiting if i["stale"])
    title = f"Needs you: {len(waiting)} waiting" + (f" ({stale} stale)" if stale else "")
    buttons = [Button(pause.PAUSE, "Pause all", pause.ALL)]
    if paused:
        buttons.append(Button(pause.RESUME, "Resume all", pause.ALL))
    when = last_connected.strftime("%Y-%m-%d %H:%M UTC")
    return Card(
        title if waiting else "Needs you: nothing waiting",
        text="\n".join(lines),
        buttons=tuple(buttons),
        note=f"Buttons work only while {computer} is awake. Last connected {when}.",
    )


def refresh(conn: sqlite3.Connection, clock: Clock, *, computer: str | None = None) -> bool:
    """Post or edit the pinned message if its content changed (or an hour passed); True if so."""
    route = slack_routes.summary_route(conn)
    if route is None:
        return False
    now = clock.now()
    c = card(conn, computer=computer or host(), last_connected=now)
    content = {"title": c.title, "text": c.text, "buttons": [b.label for b in c.buttons]}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
    last_at = slack_admin.setting(conn, AT)
    fresh = bool(last_at) and now - from_ts(last_at) < REFRESH
    if slack_admin.setting(conn, HASH) == digest and fresh:
        return False
    first = slack_out.message_ref(conn, KEY) is None
    slack_out.enqueue_post(conn, clock, key=KEY, route=route, card=c, pin=first)
    with write_tx(conn):
        slack_admin.put_setting(conn, HASH, digest, to_ts(now), actor="service")
        slack_admin.put_setting(conn, AT, to_ts(now), to_ts(now), actor="service")
    return True


def mark_stale(conn: sqlite3.Connection, clock: Clock) -> list[str]:
    """Mark open items older than 30 days stale; say so once in the summary channel."""
    days = int(conn.execute("SELECT coalesce((SELECT value FROM settings"
                            " WHERE key = 'stale_item_days'), ?)",
                            (STALE_AFTER.days,)).fetchone()[0])  # fmt: skip
    cutoff = to_ts(clock.now() - timedelta(days=days))
    rows = conn.execute(
        "SELECT stable_id, address_id FROM items WHERE stale = 0 AND created_at < ?"
        " AND status IN (SELECT value FROM json_each(?)) ORDER BY stable_id",
        (cutoff, json.dumps(sorted(OPEN))),
    ).fetchall()
    if not rows:
        return []
    now = to_ts(clock.now())
    with write_tx(conn):
        for r in rows:
            conn.execute("UPDATE items SET stale = 1 WHERE stable_id = ?", (r["stable_id"],))
            conn.execute(
                "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
                " VALUES (?, ?, ?, 'item.stale', 'service', 'ok', '{}')",
                (now, r["address_id"], r["stable_id"]),
            )
    route = slack_routes.summary_route(conn)
    if route is not None:  # reminded once; then only the daily summary lists them
        slack_out.enqueue_post(
            conn, clock, key=f"stale:{now}", route=route,
            card=Card(f"{len(rows)} email(s) have waited 30 days",
                      text="They stay open until you decide. List: ecf inbox --stale"),
        )  # fmt: skip
    return [r["stable_id"] for r in rows]
