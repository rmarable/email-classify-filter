"""Outbound reminders, `ecf outbound report|snooze|dismiss` (SPEC §9.8; V1.5 step 6).

Outbound is off by default, so ecf reminds you what it held back. 7 days after an address first
went live, while outbound is off: `[ecf-alert] Operator Input Needed: outbound off (<address>)`
on the desktop, by Slack DM and in the summary channel, with the suppressed counts and
`ecf outbound report <address>`; then weekly, 5 in all; then one summary-channel line a month.

No reminder while the address is paused, snoozed (`ecf outbound snooze`, 1-90 days) or dismissed
(`ecf outbound dismiss`, for good), and none once outbound has been turned on: turning it off again
later is a choice, not something to be reminded of. A computer that slept past a reminder sends
one when it wakes, not the ones it missed; the next counts from then. Snooze and dismiss are
audited (`outbound.reminders_snoozed`, `outbound.reminders_dismissed`).

`report` is what you'd look at before turning sending on: the track record (`high` addresses need
20 reviewed suppressed sends, 95% correct), the latest suppressed proposals and their reviews, the
send limits, recent sends, and the reminder schedule.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from ecf.errors import ConflictError, InvalidInputError
from ecf_server import addresses, alerts, cards, outbound, send_limits, slack_admin, slack_out
from ecf_server.chat import Card, RouteRef
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier

FIRST = timedelta(days=7)  # after the address first went live
WEEKLY, FULL = timedelta(days=7), 5  # day 7, then weekly: 5 full reminders
MONTHLY = timedelta(days=30)  # then a summary-channel line
SNOOZE_DAYS = (1, 90)
REPORT_ROWS = 20


def live_since(conn: sqlite3.Connection, aid: str) -> str | None:
    """When the address first went live; None if it never has."""
    row = conn.execute(
        "SELECT min(ts) FROM audit WHERE address_id = ? AND event = 'stage.changed'"
        " AND json_extract(data, '$.to') = 'live'", (aid,)).fetchone()  # fmt: skip
    if row[0]:
        return str(row[0])
    a = conn.execute("SELECT stage, created_at FROM addresses WHERE address_id = ?",
                     (aid,)).fetchone()  # fmt: skip
    return str(a["created_at"]) if a is not None and a["stage"] == "live" else None


def _ever_enabled(conn: sqlite3.Connection, aid: str) -> bool:
    return conn.execute("SELECT 1 FROM audit WHERE address_id = ? AND event = 'outbound.enabled'"
                        " LIMIT 1", (aid,)).fetchone() is not None  # fmt: skip


def schedule(conn: sqlite3.Connection, aid: str) -> dict[str, Any]:
    """The address's reminder state, with `next_at` (None when no reminder is coming)."""
    a = conn.execute(
        "SELECT outbound, outbound_reminders, outbound_reminded_at, outbound_snoozed_until,"
        " outbound_dismissed_at FROM addresses WHERE address_id = ?",
        (aid,),
    ).fetchone()
    since = live_since(conn, aid)
    out: dict[str, Any] = {
        "live_since": since,
        "sent": int(a["outbound_reminders"]),
        "last_at": a["outbound_reminded_at"],
        "snoozed_until": a["outbound_snoozed_until"],
        "dismissed_at": a["outbound_dismissed_at"],
        "next_at": None,
    }
    if a["outbound"] or a["outbound_dismissed_at"] or since is None or _ever_enabled(conn, aid):
        return out
    n, last = out["sent"], out["last_at"]
    due = (from_ts(since) + FIRST if n == 0 or last is None
           else from_ts(last) + (WEEKLY if n < FULL else MONTHLY))  # fmt: skip
    if a["outbound_snoozed_until"]:
        due = max(due, from_ts(a["outbound_snoozed_until"]))
    out["next_at"] = to_ts(due)
    return out


def remind(conn: sqlite3.Connection, clock: Clock, notifier: Notifier) -> int:
    """Each tick: send the reminders that are due. Returns how many went."""
    now = clock.now()
    rows = conn.execute("SELECT address_id, email FROM addresses WHERE removed_at IS NULL"
                        " AND paused = 0 AND outbound = 0 AND outbound_dismissed_at IS NULL"
                        " ORDER BY address_id").fetchall()  # fmt: skip
    sent = 0
    for r in rows:
        s = schedule(conn, r["address_id"])
        if s["next_at"] is None or from_ts(s["next_at"]) > now:
            continue
        _send(conn, clock, notifier, r["address_id"], r["email"], s, now)
        sent += 1
    return sent


def _send(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, aid: str, email: str,
          s: dict[str, Any], now: datetime) -> None:  # fmt: skip
    rec = outbound.track_record(conn, aid)
    n = s["sent"] + 1
    slack = "slack" in alerts.routes(conn, "operator")
    summary = slack_admin.setting(conn, slack_admin.SUMMARY_CHANNEL) if slack else ""
    key = f"outbound_reminder:{aid}:{n}"
    if n <= FULL:
        days = (now - from_ts(str(s["live_since"]))).days
        head = alerts.title("operator_input", "outbound off") + f" ({aid})"
        text = (f"{email} has been live {days} days with outbound off: {rec['suppressed']}"
                f" template reply or forward proposal(s) held back ({rec['reviewed']} reviewed,"
                f" {rec['correct']} marked correct). See `ecf outbound report {aid}`;"
                f" `ecf outbound enable {aid}` turns sending on (each send still needs your"
                f" approval and step-up). Reminder {n} of {FULL}; stop them with"
                f" `ecf outbound snooze {aid}` or `ecf outbound dismiss {aid}`.")  # fmt: skip
        notifier.notify(head, text)
        ident = slack_admin.identity(conn) if slack else None
        if ident is not None and ident.member:
            slack_out.enqueue_post(conn, clock, key=f"{key}:dm", route=RouteRef(ident.member),
                                   card=Card(head, text=text))  # fmt: skip
    else:
        new = _suppressed_since(conn, aid, s["last_at"])
        head = f"Outbound still off ({aid})"
        text = (f"{email}: {new} send proposal(s) held back this month, {rec['suppressed']} in"
                f" all. `ecf outbound report {aid}`")  # fmt: skip
    if summary:
        slack_out.enqueue_post(conn, clock, key=f"{key}:summary", route=RouteRef(summary),
                               card=Card(head, text=text))  # fmt: skip
    with write_tx(conn):
        conn.execute("UPDATE addresses SET outbound_reminders = ?, outbound_reminded_at = ?,"
                     " outbound_snoozed_until = NULL WHERE address_id = ?",
                     (n, to_ts(now), aid))  # fmt: skip
        _audit(conn, now, aid, "outbound.reminded", "service",
               {"n": n, "suppressed": rec["suppressed"]})  # fmt: skip


def _suppressed_since(conn: sqlite3.Connection, aid: str, since: str | None) -> int:
    return int(conn.execute("SELECT count(*) FROM items WHERE address_id = ? AND"
                            " suppressed_action IS NOT NULL AND created_at >= ?",
                            (aid, since or "")).fetchone()[0])  # fmt: skip


def snooze(conn: sqlite3.Connection, clock: Clock, ref: str, days: int, *,
           actor: str) -> dict[str, Any]:  # fmt: skip
    lo, hi = SNOOZE_DAYS
    if not lo <= days <= hi:
        raise InvalidInputError(f"snooze for {lo}-{hi} days")
    a = _off(conn, ref)
    until = to_ts(clock.now() + timedelta(days=days))
    with write_tx(conn):
        conn.execute("UPDATE addresses SET outbound_snoozed_until = ? WHERE address_id = ?",
                     (until, a["address_id"]))  # fmt: skip
        _audit(conn, clock.now(), a["address_id"], "outbound.reminders_snoozed", actor,
               {"days": days, "until": until})  # fmt: skip
    return {"address_id": a["address_id"], "email": a["email"]} | schedule(conn, a["address_id"])


def dismiss(conn: sqlite3.Connection, clock: Clock, ref: str, *, actor: str) -> dict[str, Any]:
    a = _off(conn, ref)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET outbound_dismissed_at = coalesce(outbound_dismissed_at,"
                     " ?) WHERE address_id = ?", (to_ts(clock.now()), a["address_id"]))  # fmt: skip
        _audit(conn, clock.now(), a["address_id"], "outbound.reminders_dismissed", actor, {})
    return {"address_id": a["address_id"], "email": a["email"]} | schedule(conn, a["address_id"])


def _off(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    a = addresses.get_address(conn, ref)
    if a["outbound"]:
        raise ConflictError(f"outbound is on for {a['email']}; there are no reminders to stop")
    return a


def report(conn: sqlite3.Connection, clock: Clock, ref: str) -> dict[str, Any]:
    """`ecf outbound report <address>`."""
    a = addresses.get_address(conn, ref)
    aid = a["address_id"]
    rec = outbound.track_record(conn, aid)
    rows = conn.execute("SELECT * FROM items WHERE address_id = ? AND suppressed_action IS NOT"
                        " NULL ORDER BY created_at DESC, stable_id LIMIT ?",
                        (aid, REPORT_ROWS)).fetchall()  # fmt: skip
    suppressed = [_suppressed_row(r) for r in rows]
    sends = conn.execute(
        "SELECT stable_id, kind, status, sent_at FROM sent WHERE address_id = ?"
        " AND kind IN ('reply', 'forward') ORDER BY sent_at DESC LIMIT ?",
        (aid, REPORT_ROWS)).fetchall()  # fmt: skip
    return {
        "address_id": aid,
        "email": a["email"],
        "stage": a["stage"],
        "sensitivity": a["sensitivity"],
        "outbound": a["outbound"],
        "record": rec,
        "high_gate": outbound.high_ready(rec) if a["sensitivity"] == "high" else None,
        "suppressed": suppressed,
        "limits": send_limits.limits(conn, aid),
        "counts": send_limits.counts(conn, clock, aid),
        "tripped": send_limits.tripped(conn, aid),
        "sends": [
            {
                "short_id": (r["stable_id"] or "")[: cards.SHORT_ID],
                "kind": r["kind"],
                "status": r["status"],
                "at": r["sent_at"],
            }
            for r in sends
        ],
        "reminders": schedule(conn, aid),
    }


def _suppressed_row(r: sqlite3.Row) -> dict[str, Any]:
    facts: dict[str, Any] = json.loads(r["facts"] or "{}")
    review: dict[str, Any] = json.loads(r["review"] or "{}")
    verdict = review.get("verdict")
    return {
        "short_id": r["stable_id"][: cards.SHORT_ID],
        "created_at": r["created_at"],
        "action": r["suppressed_action"],
        "sender": cards.sender_line(r, facts),
        "subject": cards.subject_line(r),
        "review": verdict if verdict in outbound.REVIEWED else None,
    }


def _audit(conn: sqlite3.Connection, now: datetime, aid: str, event: str, actor: str,
           data: dict[str, Any]) -> None:  # fmt: skip
    conn.execute(
        "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
        " VALUES (?, ?, ?, ?, 'ok', ?)",
        (to_ts(now), aid, event, actor, json.dumps(data)),
    )
