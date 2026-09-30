"""Alerts in Slack (SPEC §13.3; V1.2 step 9).

- **Titles:** every alert reads `[ecf-alert] <Title>` (OD-112), on the desktop and in Slack; a
  recovery reads `[ecf-alert] Resolved: <Title>`. Operator Input Needed adds `: <condition>`.
- **Routes:** `alerts.routes` (default `slack`) and per-class `alerts.<class>.routes`, changed
  with `ecf alerts set [<class>] --to …` (step-up, Security Notice). Desktop notifications always
  go too (unless `notifications: off`). Email routes are refused until V1.5, when ecf can send
  (OD-206).
- **Classes:** `mail` (Mail Provider Unreachable, Mailbox Login Rejected), `system` (System
  Error), `operator` (Operator Input Needed) and `slack` (Slack Delivery Failed). Slack Delivery
  Failed never goes through Slack: desktop only in V1.2, email too from V1.5. Security Notices go
  to every enabled route whatever the class settings (`slack_admin.notice`).
- **Delivery to Slack:** open and resolved alerts in the `alerts` table are posted by the Slack
  thread (`sweep`), so the code that raises them needs no Slack; events without an open/resolved
  life (a restart after a crash, jobs that gave up) are posted at once (`event`).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ecf.errors import InvalidInputError
from ecf_server import _slack, slack_admin, slack_out, slack_routes, stepup
from ecf_server.chat import Card, RouteRef
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier
from ecf_server.secretstore import SecretStore
from ecf_server.slack_chat import SlackChat

PREFIX = "[ecf-alert]"
TITLES = {
    "mail_unreachable": "Mail Provider Unreachable",
    "login_rejected": "Mailbox Login Rejected",
    "slack_delivery_failed": "Slack Delivery Failed",
    "slack_connection": "Slack Delivery Failed",
    "system_error": "System Error",
    "local_model": "System Error",
    "local_model_unsafe": "System Error",
    "operator_input": "Operator Input Needed",
    "security_notice": "Security Notice",
}
CLASS_OF = {
    "mail_unreachable": "mail",
    "login_rejected": "mail",
    "system_error": "system",
    "local_model": "system",
    "local_model_unsafe": "system",
    "operator_input": "operator",
    "slack_delivery_failed": "slack",
    "slack_connection": "slack",
    "security_notice": "security",
}
CLASSES = ("mail", "system", "operator", "slack")
ROUTES = frozenset({"slack", "email"})
DEFAULT = ["slack"]
LOUD_KINDS = frozenset({"local_model_unsafe"})  # posts mention you (OD-242, OD-245)
DEAD_MARK = "alerts_dead_job_mark"  # the last dead-lettered job already reported


def title(kind: str, condition: str = "") -> str:
    return f"{PREFIX} {TITLES[kind]}" + (f": {condition}" if condition else "")


# ---- routes -------------------------------------------------------------------------------------


def _key(cls: str | None) -> str:
    return f"alerts.{cls}.routes" if cls else "alerts.routes"


def _get(conn: sqlite3.Connection, key: str) -> list[str] | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return [str(r) for r in json.loads(row[0])] if row else None


def routes(conn: sqlite3.Connection, cls: str) -> list[str]:
    """Where a class goes: its own setting, else the install's, else Slack. Slack-health alerts
    never go through Slack."""
    got = _get(conn, _key(cls)) or _get(conn, _key(None)) or DEFAULT
    return [r for r in got if not (cls == "slack" and r == "slack")]


def show(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "default": _get(conn, _key(None)) or DEFAULT,
        "classes": {c: routes(conn, c) for c in CLASSES},
        "desktop": "on" if _desktop_on(conn) else "off",
    }


def _desktop_on(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key = 'notifications'").fetchone()
    return not (row and json.loads(row[0]) == "off")


def validate(cls: str | None, to: list[str]) -> list[str]:
    if cls is not None and cls not in CLASSES:
        raise InvalidInputError(f"alert classes: {', '.join(CLASSES)}")
    wanted = sorted({t.strip().lower() for t in to if t.strip()})
    if not wanted:
        raise InvalidInputError("at least one route is required: slack")
    unknown = set(wanted) - ROUTES
    if unknown:
        raise InvalidInputError(f"routes are slack and email, not {', '.join(sorted(unknown))}")
    if "email" in wanted:
        raise InvalidInputError("email alerts arrive in V1.5, when ecf can send email (OD-206)")
    if cls == "slack":
        raise InvalidInputError(
            "Slack Delivery Failed can't be delivered through Slack; it goes to the desktop"
            " (and email from V1.5)"
        )
    return wanted


@stepup.purpose("alerts_set")
def _describe_set(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    cls = target.get("class") or None
    to = [str(t) for t in target.get("routes", [])]
    current = _get(conn, _key(cls))
    what = f"{cls} alerts" if cls else "alerts"
    return stepup.Bound(stepup.digest("alerts_set", cls, to, current),
                        f"ecf: send {what} to {', '.join(to)}")  # fmt: skip


def set_routes(
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    cls: str | None,
    to: list[str],
    *,
    nonce: str | None,
) -> dict[str, Any]:
    wanted = validate(cls, to)
    stepup.consume(conn, clock, "alerts_set", {"class": cls or "", "routes": wanted}, nonce)
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'os_user')"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (_key(cls), json.dumps(wanted), now),
        )
        conn.execute(
            "INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?, 'alerts.set',"
            " 'os_user', 'ok', ?)",
            (now, json.dumps({"class": cls, "routes": wanted})),
        )
    ident = slack_admin.identity(conn)
    slack_admin.notice(conn, clock, notifier,
                       f"Alert routing changed: {cls or 'all'} alerts now go to "
                       f"{', '.join(wanted)} (and the desktop).",
                       dms=[ident.member] if ident and ident.member else [])  # fmt: skip
    return show(conn)


# ---- delivery -----------------------------------------------------------------------------------


def event(
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    kind: str,
    detail: str,
    *,
    condition: str = "",
) -> None:
    """An alert that happens once (no open/resolved life): desktop, and Slack if routed."""
    head = title(kind, condition)
    notifier.notify(head, detail)
    if "slack" in routes(conn, CLASS_OF[kind]):
        _post(conn, clock, f"alert:{kind}:{to_ts(clock.now())}", head, detail)


def sweep(conn: sqlite3.Connection, clock: Clock) -> int:
    """Post open and resolved alerts to the summary channel (the Slack thread calls this)."""
    route = slack_routes.summary_route(conn)
    if route is None:
        return 0
    rows = conn.execute(
        "SELECT * FROM alerts WHERE kind != 'slack_delivery_failed' AND (slack_opened_at IS NULL"
        " OR (resolved_at IS NOT NULL AND slack_resolved_at IS NULL)) ORDER BY opened_at, key"
    ).fetchall()
    now = to_ts(clock.now())
    for r in rows:
        routed = "slack" in routes(conn, CLASS_OF.get(r["kind"], "system"))
        who = f" ({r['address_id']})" if r["address_id"] else ""
        if r["slack_opened_at"] is None and routed:
            _post(conn, clock, f"alert:{r['key']}:{r['opened_at']}",
                  f"{title(r['kind'])}{who}", str(r["detail"] or ""),
                  mention=r["kind"] in LOUD_KINDS)  # fmt: skip
        if r["resolved_at"] is not None and routed:
            _post(conn, clock, f"alert:{r['key']}:{r['opened_at']}:resolved",
                  f"{PREFIX} Resolved: {TITLES[r['kind']]}{who}", "Working again.")  # fmt: skip
        with write_tx(conn):
            conn.execute(
                "UPDATE alerts SET slack_opened_at = coalesce(slack_opened_at, ?),"
                " slack_resolved_at = CASE WHEN resolved_at IS NOT NULL THEN ? END WHERE key = ?",
                (now, now, r["key"]),
            )
    return len(rows)


def test(conn: sqlite3.Connection, clock: Clock, notifier: Notifier) -> dict[str, Any]:
    """`ecf alerts test`: one test alert on every route."""
    head, text = f"{PREFIX} Test", "A test alert from `ecf alerts test`. Nothing is wrong."
    notifier.notify(head, text)
    sent = ["desktop"] if _desktop_on(conn) else []
    if slack_routes.summary_route(conn) is not None:
        _post(conn, clock, f"alert:test:{to_ts(clock.now())}", head, text)
        sent.append("slack")
    return {"sent": sent}


def dead_jobs(conn: sqlite3.Connection, clock: Clock, notifier: Notifier) -> int:
    """A System Error for background jobs that gave up since the last report (Slack posts are
    Slack Delivery Failed's business, not this)."""
    mark = int(slack_admin.setting(conn, DEAD_MARK) or 0)
    rows = conn.execute(
        "SELECT rowid, queue FROM jobs WHERE state = 'dead' AND queue != 'slack_out'"
        " AND rowid > ? ORDER BY rowid", (mark,)).fetchall()  # fmt: skip
    if not rows:
        return 0
    queues = sorted({r["queue"] for r in rows})
    event(conn, clock, notifier, "system_error",
          f"{len(rows)} background job(s) gave up after retrying ({', '.join(queues)})."
          " Details: ecf logs; failed actions: ecf inbox.")  # fmt: skip
    with write_tx(conn):
        slack_admin.put_setting(conn, DEAD_MARK, str(rows[-1]["rowid"]), to_ts(clock.now()),
                                actor="service")  # fmt: skip
    return len(rows)


def _post(
    conn: sqlite3.Connection,
    clock: Clock,
    key: str,
    head: str,
    detail: str,
    *,
    mention: bool = False,
) -> None:
    route = slack_routes.summary_route(conn)
    if route is None:
        return
    ident = slack_admin.identity(conn) if mention else None
    who = ident.member if ident and ident.member else ""
    card = Card(head, text=detail, mention=who)
    slack_out.enqueue_post(conn, clock, key=key, route=route, card=card)


def post_now(conn: sqlite3.Connection, secrets: SecretStore, head: str, detail: str) -> None:
    """Post straight to the summary channel, bypassing the queue: for the breaker trip, when the
    service exits right after and nothing would send a queued post. Best effort."""
    channel = slack_admin.setting(conn, slack_admin.SUMMARY_CHANNEL)
    token = secrets.get(slack_admin.BOT_SECRET)
    if not channel or not token:
        return
    SlackChat(_slack.Web(token)).post(RouteRef(channel), Card(head, text=detail))
