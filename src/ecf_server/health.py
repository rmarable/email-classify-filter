"""Mail-health alerts (SPEC §13.3). V1.1 step 13b: desktop notifications, `ecf status` and
`ecf doctor` (OD-190). From V1.2 the Slack thread also posts them to the summary channel
(`alerts.sweep`), except Slack Delivery Failed; email arrives in V1.5.

- **Mail Provider Unreachable:** after 15 minutes of consecutive mail errors while the network is
  up (OD confirmed 2026-09-27). "Up" means the provider's host name resolves through the system
  resolver, so an offline laptop never raises it and no third party is contacted.
- **Mailbox Login Rejected:** after 3 rejected logins in a row; checks then retry once an hour
  (schedule.py) so the provider doesn't lock the account; `ecf address retry` forces one, and a new
  app password (`ecf address set --app-password`) clears the count.

An alert is notified once when it opens and once more when a later check succeeds ("Resolved").
Notifications carry the address ID and host name only, never message content.
"""

from __future__ import annotations

import socket
import sqlite3
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from ecf_server.checks import CheckReport
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier

UNREACHABLE_AFTER = timedelta(minutes=15)
LOGIN_REJECTIONS = 3
TITLES = {
    "mail_unreachable": "Mail Provider Unreachable",
    "login_rejected": "Mailbox Login Rejected",
    "slack_delivery_failed": "Slack Delivery Failed",
}
Resolver = Callable[[str], bool]


def resolves(host: str) -> bool:
    """True if the host name resolves through the system resolver."""
    try:
        socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        return False
    return True


def after_check(
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    report: CheckReport,
    *,
    resolve: Resolver = resolves,
) -> None:
    """Update the failure counts from a check's outcome and open or resolve alerts."""
    now = clock.now()
    aid = report.address_id
    if report.status == "busy":
        return
    probe_row = conn.execute("SELECT host FROM probe WHERE address_id = ?", (aid,)).fetchone()
    host = probe_row["host"] if probe_row and probe_row["host"] else ""
    row = conn.execute(
        "SELECT failing_since, login_failures FROM check_state WHERE address_id = ?", (aid,)
    ).fetchone()
    since = from_ts(row["failing_since"]) if row and row["failing_since"] else None
    failures = row["login_failures"] if row else 0
    if report.status in ("ok", "first_run", "reset_recovered"):
        since, failures = None, 0
        for kind in TITLES:
            resolve_alert(conn, clock, notifier, kind, aid)
    elif report.status == "login_rejected":
        failures += 1
        if failures >= LOGIN_REJECTIONS:
            open_alert(
                conn,
                clock,
                notifier,
                "login_rejected",
                aid,
                f"{aid}: the provider rejected the app password {failures} times; retrying "
                f"hourly. Fix: ecf address set {aid} --app-password",
            )
    elif report.status == "error":
        since = since or now
        if now - since >= UNREACHABLE_AFTER and host and resolve(host):
            minutes = int((now - since).total_seconds() // 60)
            open_alert(
                conn,
                clock,
                notifier,
                "mail_unreachable",
                aid,
                f"{aid}: can't reach {host} for {minutes} minutes while the network is up",
            )
    with write_tx(conn):
        conn.execute(
            "UPDATE check_state SET failing_since = ?, login_failures = ? WHERE address_id = ?",
            (to_ts(since) if since else None, failures, aid),
        )


def login_backoff(conn: sqlite3.Connection, address_id: str) -> bool:
    """True when logins keep being rejected, so checks slow to hourly (schedule.py)."""
    row = conn.execute(
        "SELECT login_failures FROM check_state WHERE address_id = ?", (address_id,)
    ).fetchone()
    return row is not None and row["login_failures"] >= LOGIN_REJECTIONS


def open_alerts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT kind, address_id, detail, opened_at FROM alerts WHERE resolved_at IS NULL"
        " AND address_id NOT IN (SELECT address_id FROM addresses WHERE removed_at IS NOT NULL)"
        " ORDER BY opened_at"
    ).fetchall()
    return [dict(r) | {"title": TITLES.get(r["kind"], r["kind"])} for r in rows]


def open_alert(
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    kind: str,
    aid: str | None,
    detail: str,
) -> None:
    """Open an alert once and notify; `aid` None for install-wide alerts (Slack)."""
    key = f"{kind}:{aid or 'install'}"
    with write_tx(conn):
        already = conn.execute(
            "SELECT 1 FROM alerts WHERE key = ? AND resolved_at IS NULL", (key,)
        ).fetchone()
        if already:
            conn.execute("UPDATE alerts SET detail = ? WHERE key = ?", (detail, key))
            return
        conn.execute(
            "INSERT INTO alerts (key, kind, address_id, detail, opened_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (key) DO UPDATE SET detail = excluded.detail,"
            " opened_at = excluded.opened_at, resolved_at = NULL, slack_opened_at = NULL,"
            " slack_resolved_at = NULL",
            (key, kind, aid, detail, to_ts(clock.now())),
        )
        _audit(conn, clock, aid, "alert.opened", kind)
    notifier.notify(f"[ecf-alert] {TITLES[kind]}", detail)


def resolve_alert(
    conn: sqlite3.Connection, clock: Clock, notifier: Notifier, kind: str, aid: str | None
) -> None:
    key = f"{kind}:{aid or 'install'}"
    with write_tx(conn):
        done = conn.execute(
            "UPDATE alerts SET resolved_at = ? WHERE key = ? AND resolved_at IS NULL",
            (to_ts(clock.now()), key),
        ).rowcount
        if done:
            _audit(conn, clock, aid, "alert.resolved", kind)
    if done:
        notifier.notify(f"[ecf-alert] Resolved: {TITLES[kind]}", f"{aid or 'ecf'}: working again")


def _audit(conn: sqlite3.Connection, clock: Clock, aid: str | None, event: str, kind: str) -> None:
    conn.execute(
        "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
        " VALUES (?, ?, ?, 'service', 'ok', json_object('kind', ?))",
        (to_ts(clock.now()), aid, event, kind),
    )
