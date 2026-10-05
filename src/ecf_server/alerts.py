"""Alerts in Slack and by email (SPEC §13.3; V1.2 step 9, V1.5 step 7a).

- **Titles:** every alert reads `[ecf-alert] <Title>` (OD-112), on the desktop, in Slack and in
  email; a recovery reads `[ecf-alert] Resolved: <Title>`. Operator Input Needed adds
  `: <condition>`. One table for every kind (the mail-health ones are `health.TITLES`).
- **Routes:** `alerts.routes` (default `slack`) and per-class `alerts.<class>.routes`, changed
  with `ecf alerts set [<class>] --to …` (step-up, Security Notice). Desktop notifications always
  go too (unless `notifications: off`). `email` is a route once alert email is on (`ecf alerts
  email set`, alert_mail.py); turning it on makes the install-wide routes `slack,email` when
  they were the default, and turning it off takes `email` out of every route.
- **Classes:** `mail` (Mail Provider Unreachable, Mailbox Login Rejected), `system` (System
  Error), `operator` (Operator Input Needed) and `slack` (Slack Delivery Failed). Slack Delivery
  Failed never goes through Slack: the desktop, and email when routed. Security Notices go to every
  enabled route whatever the class settings (`slack_admin.notice`).
- **Delivery:** open and resolved alerts in the `alerts` table are posted by the Slack thread
  (`sweep`) and queued for email by the timer (`email_sweep`), so the code that raises them needs
  neither; events without an open/resolved life (a restart after a crash, jobs that gave up) are
  posted and queued at once (`event`).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ecf.errors import ConflictError, InvalidInputError
from ecf_server import (
    _slack,
    addresses,
    alert_mail,
    health,
    slack_admin,
    slack_out,
    slack_routes,
    stepup,
)
from ecf_server.chat import Card, RouteRef
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier
from ecf_server.outbound_msg import addr_spec
from ecf_server.secretstore import SecretStore
from ecf_server.slack_chat import SlackChat

PREFIX = "[ecf-alert]"
TITLES = health.TITLES | {
    "system_error": "System Error",
    "model_retirement": "Model Retirement Scheduled",
    "operator_input": "Operator Input Needed",
    "security_notice": "Security Notice",
}
CLASS_OF = {
    "mail_unreachable": "mail",
    "login_rejected": "mail",
    "system_error": "system",
    "local_model": "system",
    "local_model_unsafe": "system",
    "model_failures": "system",
    "local_model_server": "system",
    "models_api": "system",
    "models_missing": "system",
    "model_retirement": "system",
    "alert_email": "system",
    "export_failed": "system",
    "operator_input": "operator",
    "claude_review": "operator",
    "second_install": "operator",
    "restored_keywords": "operator",
    "send_limit": "operator",
    "download_budget": "operator",
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


def install_routes(conn: sqlite3.Connection) -> list[str]:
    """The install-wide routes, which the mail-content alerts follow (OD-334)."""
    return _get(conn, _key(None)) or DEFAULT


def show(conn: sqlite3.Connection) -> dict[str, Any]:
    cfg = alert_mail.config(conn)
    mail = None if cfg is None else {"from": cfg.address_id, "from_email": cfg.email,
                                     "to": cfg.destination}  # fmt: skip
    return {
        "default": _get(conn, _key(None)) or DEFAULT,
        "classes": {c: routes(conn, c) for c in CLASSES},
        "desktop": "on" if _desktop_on(conn) else "off",
        "email": mail,
    }


def _desktop_on(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key = 'notifications'").fetchone()
    return not (row and json.loads(row[0]) == "off")


def validate(cls: str | None, to: list[str], *, email_on: bool = False) -> list[str]:
    if cls is not None and cls not in CLASSES:
        raise InvalidInputError(f"alert classes: {', '.join(CLASSES)}")
    wanted = sorted({t.strip().lower() for t in to if t.strip()})
    if not wanted:
        raise InvalidInputError("at least one route is required: slack")
    unknown = set(wanted) - ROUTES
    if unknown:
        raise InvalidInputError(f"routes are slack and email, not {', '.join(sorted(unknown))}")
    if "email" in wanted and not email_on:
        raise InvalidInputError("alert email is off; turn it on first with `ecf alerts email set"
                                " --from <address> --to <destination>`")  # fmt: skip
    if cls == "slack" and "slack" in wanted:
        raise InvalidInputError(
            "Slack Delivery Failed can't be delivered through Slack; it goes to the desktop,"
            " and to email when routed (`--to email`)"
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
    wanted = validate(cls, to, email_on=alert_mail.config(conn) is not None)
    stepup.consume(conn, clock, "alerts_set", {"class": cls or "", "routes": wanted}, nonce)
    now = to_ts(clock.now())
    with write_tx(conn):
        _put(conn, _key(cls), wanted, now)
        conn.execute(
            "INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?, 'alerts.set',"
            " 'os_user', 'ok', ?)",
            (now, json.dumps({"class": cls, "routes": wanted})),
        )
    _notice(conn, clock, notifier, f"Alert routing changed: {cls or 'all'} alerts now go to "
                                   f"{', '.join(wanted)} (and the desktop).")  # fmt: skip
    return show(conn)


# ---- alert email (V1.5 step 7a; OD-333) ---------------------------------------------------------


@stepup.purpose("alerts_email")
def _describe_email(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    frm, to = str(target.get("from") or ""), str(target.get("to") or "")
    cfg = alert_mail.config(conn)
    current = None if cfg is None else [cfg.address_id, cfg.destination]
    if not frm:
        return stepup.Bound(stepup.digest("alerts_email", None, current),
                            "ecf: turn alert email off")  # fmt: skip
    a = addresses.get_address(conn, frm)
    return stepup.Bound(stepup.digest("alerts_email", [a["address_id"], addr_spec(to)], current),
                        f"ecf: send alert email from {a['email']} to {addr_spec(to)}")  # fmt: skip


def set_email(
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    frm: str,
    to: str,
    *,
    nonce: str | None,
) -> dict[str, Any]:
    """`ecf alerts email set`: send alerts from a watched address's SMTP login to a destination
    that isn't a watched address. Step-up; a Security Notice (also to the old destination when
    it changes); a test email; routes become `slack,email` when they were the default."""
    a = addresses.get_address(conn, frm)
    try:
        dest = addr_spec(to)
    except InvalidInputError:
        raise InvalidInputError(f"--to {to!r} isn't a plain email address") from None
    watched = {str(r[0]).lower() for r in conn.execute(
        "SELECT email FROM addresses WHERE removed_at IS NULL")}  # fmt: skip
    if dest.lower() in watched:
        raise InvalidInputError("the destination may not be a watched address: ecf would read its"
                                " own alerts; use an address ecf doesn't watch")  # fmt: skip
    if not a.get("smtp_host"):
        raise InvalidInputError(f"{a['address_id']} has no SMTP server set")
    aid = a["address_id"]
    stepup.consume(conn, clock, "alerts_email", {"from": aid, "to": dest}, nonce)
    old = alert_mail.config(conn)
    now = to_ts(clock.now())
    with write_tx(conn):
        for k, v in ((alert_mail.FROM_KEY, aid), (alert_mail.TO_KEY, dest)):
            _put(conn, k, v, now)
        if _get(conn, _key(None)) is None:
            _put(conn, _key(None), ["slack", "email"], now)
        conn.execute("INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
                     " VALUES (?, ?, 'alerts.email_set', 'os_user', 'ok', ?)",
                     (now, aid, json.dumps({"to": dest,
                                            "was": old and old.destination})))  # fmt: skip
    text = f"Alert email now goes from {a['email']} ({aid}) to {dest}."
    if old is not None and old.destination != dest:
        alert_mail.queue(conn, clock, title("security_notice"),
                         f"{text} It no longer comes here. If this wasn't you, check the computer"
                         " ecf runs on.", slack_done=True, to=old)  # fmt: skip
    _notice(conn, clock, notifier, text)
    alert_mail.queue(conn, clock, f"{PREFIX} Test",
                     "A test from `ecf alerts email set`: alert email works.",
                     slack_done=True)  # fmt: skip
    return show(conn)


def email_off(
    conn: sqlite3.Connection, clock: Clock, notifier: Notifier, *, nonce: str | None
) -> dict[str, Any]:
    """`ecf alerts email off`: step-up; `email` leaves every route; a last Security Notice goes to
    the old destination."""
    old = alert_mail.config(conn)
    if old is None:
        raise ConflictError("alert email is already off")
    stepup.consume(conn, clock, "alerts_email", {"from": "", "to": ""}, nonce)
    text = "Alert email was turned off at this computer (`ecf alerts email off`)."
    alert_mail.queue(conn, clock, title("security_notice"),
                     f"{text} If this wasn't you, check the computer ecf runs on.",
                     slack_done=True, to=old)  # fmt: skip
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("DELETE FROM settings WHERE key IN (?, ?)",
                     (alert_mail.FROM_KEY, alert_mail.TO_KEY))  # fmt: skip
        for k in [_key(None), *(_key(c) for c in CLASSES)]:
            got = _get(conn, k)
            if got is None or "email" not in got:
                continue
            kept = [r for r in got if r != "email"]
            if kept:
                _put(conn, k, kept, now)
            else:
                conn.execute("DELETE FROM settings WHERE key = ?", (k,))
        conn.execute("INSERT INTO audit (ts, event, actor, outcome, data) VALUES"
                     " (?, 'alerts.email_off', 'os_user', 'ok', ?)",
                     (now, json.dumps({"was": old.destination})))  # fmt: skip
    health.resolve_alert(conn, clock, notifier, alert_mail.FAIL_ALERT, None)
    _notice(conn, clock, notifier, text)
    return show(conn)


def email(conn: sqlite3.Connection, clock: Clock, kind: str, head: str, text: str) -> None:
    """Queue an alert email for an alert raised outside the `alerts` table, when its class is
    routed to email."""
    got = routes(conn, CLASS_OF.get(kind, "system"))
    if "email" in got:
        alert_mail.queue(conn, clock, head, text, slack_done="slack" in got)


def email_sweep(conn: sqlite3.Connection, clock: Clock) -> int:
    """Queue alert email for open and resolved alerts (the timer calls this). Rows are marked
    whether or not email is a route, as `sweep` does, so turning email on later sends no backlog."""
    rows = conn.execute(
        "SELECT * FROM alerts WHERE email_opened_at IS NULL"
        " OR (resolved_at IS NOT NULL AND email_resolved_at IS NULL) ORDER BY opened_at, key"
    ).fetchall()
    now = to_ts(clock.now())
    for r in rows:
        got = routes(conn, CLASS_OF.get(r["kind"], "system"))
        routed, slack = "email" in got, "slack" in got
        who = f" ({r['address_id']})" if r["address_id"] else ""
        if r["email_opened_at"] is None and routed:
            alert_mail.queue(conn, clock, f"{title(r['kind'])}{who}", str(r["detail"] or ""),
                             slack_done=slack)  # fmt: skip
        if r["resolved_at"] is not None and routed:
            alert_mail.queue(conn, clock, f"{PREFIX} Resolved: {TITLES[r['kind']]}{who}",
                             "Working again.", slack_done=slack)  # fmt: skip
        with write_tx(conn):
            conn.execute(
                "UPDATE alerts SET email_opened_at = coalesce(email_opened_at, ?),"
                " email_resolved_at = CASE WHEN resolved_at IS NOT NULL THEN ? END WHERE key = ?",
                (now, now, r["key"]),
            )
    return len(rows)


def _put(conn: sqlite3.Connection, key: str, value: Any, now: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'os_user')"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (key, json.dumps(value), now),
    )


def _notice(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, text: str) -> None:
    ident = slack_admin.identity(conn)
    slack_admin.notice(conn, clock, notifier, text,
                       dms=[ident.member] if ident and ident.member else [])  # fmt: skip


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
    """An alert that happens once (no open/resolved life): desktop, and Slack and email if
    routed."""
    head = title(kind, condition)
    notifier.notify(head, detail)
    if "slack" in routes(conn, CLASS_OF[kind]):
        _post(conn, clock, f"alert:{kind}:{to_ts(clock.now())}", head, detail)
    email(conn, clock, kind, head, detail)


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
    if alert_mail.queue(conn, clock, head, text, slack_done="slack" in sent) is not None:
        sent.append("email")
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
