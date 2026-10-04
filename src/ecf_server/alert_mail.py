"""Alert email (SPEC §13.3; OD-100, OD-316, OD-320, OD-328, OD-333 to OD-336; V1.5 step 7a).

Turned on with `ecf alerts email set --from <address> --to <destination>` (alerts.set_email):
alerts are sent through the SMTP login of `alerts.email.monitored_address` (a watched address) to
`alerts.email.destination_address` (never a watched address). The text is the desktop
notification's (address IDs, counts, item IDs, commands), never a subject, sender or excerpt
(OD-316). Alert email is exempt from the outbound switch and from the send circuit breaker, and
is recorded in `sent` (kind `alert`) with `Auto-Submitted: auto-generated` and `X-ECF-Install`
(OD-320), so its own copy coming back in is recognized (own_mail.py).

- **Outbox:** `queue` writes a row (`alert_outbox`) with the sending address and destination of
  that moment, so a notice queued just before alert email is turned off or moved still reaches the
  old destination. The service's alert-mail thread runs `drain`, so the timer never waits on SMTP.
- **Caps** (OD-100, OD-328, OD-336): at most 10 an hour per alert type (its fixed title) and 30 an
  hour in all. Past either, an alert is `rolled_up`: posted to Slack at once if Slack didn't have
  it, and listed in its type's roll-up email once the oldest waiting one is an hour old. Roll-ups
  don't count toward the caps, so at most 30 plus one per type go out in an hour.
- **Fallback** (OD-335): a send that isn't accepted is retried at 30 s and 2 min; after the third
  try (or at once, when the server refuses it for good or the outcome is unknown) the alert
  `fallback`s: posted to the summary channel if Slack didn't already have it (every alert already
  reached the desktop where it was raised), and `[ecf-alert] System Error` (`alert_email`) opens
  until an alert email goes through. While the sending address has Mail Provider Unreachable or
  Mailbox Login Rejected open, nothing is tried: alerts fall back at once (OD-328).
- **send_now:** the crash-loop breaker's email, sent synchronously and best effort as the service
  exits (§11.1); it skips the queue but is recorded like the rest.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from ecf_server import addresses, health, install_identity, send, slack_out
from ecf_server.chat import Card, RouteRef
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.mail.smtp import Sender, SendNotSentError
from ecf_server.notify import Notifier
from ecf_server.outbound_msg import Built, build_alert, clean, new_message_id

FROM_KEY = addresses.ALERT_SENDER_KEY  # an address ID
TO_KEY = "alerts.email.destination_address"
SUMMARY_CHANNEL = "slack_summary_channel"  # slack_admin.SUMMARY_CHANNEL (which imports this)
PREFIX = "[ecf-alert]"
PER_TYPE_HOUR = 10  # OD-100
PER_INSTALL_HOUR = 30  # OD-328
TRIES = 3  # OD-335
RETRY_S = (30, 120)  # after the first and the second try
SUBJECT_CHARS = 99  # OD-112
ROLLUP_LINES = 30
KEEP = timedelta(days=7)
HOUR = timedelta(hours=1)
FAIL_ALERT = "alert_email"  # System Error while alert email isn't getting through
MAIL_ALERTS = ("mail_unreachable", "login_rejected")
COUNTED = ("queued", "sent", "fallback")
FOOTER = ("\n\n--\nSent by ecf on your computer. Alert email never carries an email's subject,"
          " sender or text. Turn it off: ecf alerts email off")  # fmt: skip

SenderFor = Callable[[sqlite3.Connection, str], Sender]


@dataclass(frozen=True)
class EmailConfig:
    address_id: str
    email: str  # the sending address
    destination: str


def config(conn: sqlite3.Connection) -> EmailConfig | None:
    """Where alert email goes from and to; None while it is off."""
    rows = dict(conn.execute("SELECT key, json_extract(value, '$') FROM settings"
                             " WHERE key IN (?, ?)", (FROM_KEY, TO_KEY)).fetchall())  # fmt: skip
    aid, dest = rows.get(FROM_KEY), rows.get(TO_KEY)
    if not aid or not dest:
        return None
    a = conn.execute("SELECT email FROM addresses WHERE address_id = ? AND removed_at IS NULL",
                     (aid,)).fetchone()  # fmt: skip
    return None if a is None else EmailConfig(str(aid), str(a["email"]), str(dest))


def type_of(title: str) -> str:
    """The cap bucket: an alert's fixed title, without `[ecf-alert]`, `Resolved:` or the
    condition (`Operator Input Needed: approval waiting (a1)` -> `Operator Input Needed`)."""
    t = title.removeprefix(PREFIX).strip().removeprefix("Resolved:").strip()
    return t.split(":", 1)[0].split(" (", 1)[0].strip()


def queue(
    conn: sqlite3.Connection,
    clock: Clock,
    subject: str,
    body: str,
    *,
    slack_done: bool,
    to: EmailConfig | None = None,
) -> int | None:
    """Queue one alert email (to `to`, else the current destination); returns its row, or None
    while email is off. Over a cap it is held for its type's roll-up instead."""
    cfg = to or config(conn)
    if cfg is None:
        return None
    kind, now = type_of(subject), clock.now()
    since = to_ts(now - HOUR)
    with write_tx(conn):
        marks = ", ".join("?" * len(COUNTED))
        typed, total = conn.execute(
            f"SELECT count(*) FILTER (WHERE kind = ?), count(*) FROM alert_outbox"  # noqa: S608
            f" WHERE rollup = 0 AND created_at >= ? AND state IN ({marks})",
            (kind, since, *COUNTED),
        ).fetchone()
        over = typed >= PER_TYPE_HOUR or total >= PER_INSTALL_HOUR
        conn.execute(
            "INSERT INTO alert_outbox (kind, subject, body, address_id, destination, created_at,"
            " state, slack_done, next_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, clean(subject, SUBJECT_CHARS), body, cfg.address_id, cfg.destination,
             to_ts(now), "rolled_up" if over else "queued", int(slack_done), to_ts(now)),
        )  # fmt: skip
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    if over and not slack_done:  # it would otherwise reach no one for up to an hour
        _post(conn, clock, rid, subject, body)
        with write_tx(conn):
            conn.execute("UPDATE alert_outbox SET slack_done = 1 WHERE id = ?", (rid,))
    return int(rid)


def drain(
    conn: sqlite3.Connection, clock: Clock, notifier: Notifier, sender_for: SenderFor,
    *, limit: int = 10,
) -> int:  # fmt: skip
    """Queue due roll-ups, then try the alert emails that are due; returns how many were tried."""
    now = clock.now()
    with write_tx(conn):
        conn.execute("DELETE FROM alert_outbox WHERE settled_at < ?", (to_ts(now - KEEP),))
    _rollups(conn, clock)
    rows = conn.execute("SELECT * FROM alert_outbox WHERE state = 'queued' AND next_at <= ?"
                        " ORDER BY id LIMIT ?", (to_ts(now), limit)).fetchall()  # fmt: skip
    for r in rows:
        _attempt(conn, clock, notifier, sender_for, r)
    return len(rows)


def send_now(
    conn: sqlite3.Connection, clock: Clock, sender_for: SenderFor, subject: str, body: str
) -> bool:
    """One alert email right now, outside the queue (the crash-loop breaker, as the service
    exits). Best effort: False when off, skipped (OD-328) or not accepted."""
    cfg = config(conn)
    if cfg is None or mail_alert_open(conn, cfg.address_id):
        return False
    rid = queue(conn, clock, subject, body, slack_done=True, to=cfg)
    row = conn.execute("SELECT * FROM alert_outbox WHERE id = ?", (rid,)).fetchone()
    if row is None or row["state"] != "queued":  # over a cap: its roll-up follows the next start
        return False
    try:
        _send(conn, clock, sender_for, row)
    except Exception as exc:  # the service is exiting either way
        log.warning("alert_mail.send_now_failed", error_type=type(exc).__name__)
        _settle(conn, clock, row["id"], "fallback", type(exc).__name__)
        return False
    _settle(conn, clock, row["id"], "sent")
    return True


def mail_alert_open(conn: sqlite3.Connection, address_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM alerts WHERE address_id = ? AND resolved_at IS NULL AND kind IN (?, ?)",
        (address_id, *MAIL_ALERTS),
    ).fetchone()
    return row is not None


def counts(conn: sqlite3.Connection, clock: Clock) -> dict[str, int]:
    """Alert email of the last 24 hours by state (`ecf alerts show`)."""
    rows = conn.execute("SELECT state, count(*) FROM alert_outbox WHERE rollup = 0"
                        " AND created_at >= ? GROUP BY state",
                        (to_ts(clock.now() - timedelta(days=1)),)).fetchall()  # fmt: skip
    return {str(r[0]): int(r[1]) for r in rows}


# ---- internals ----------------------------------------------------------------------------------


def _attempt(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, sender_for: SenderFor,
             r: sqlite3.Row) -> None:  # fmt: skip
    if mail_alert_open(conn, r["address_id"]):
        _fallback(conn, clock, notifier, r, "its sending address has a mail alert open",
                  tell=False)  # fmt: skip
        return
    try:
        _send(conn, clock, sender_for, r)
    except SendNotSentError as exc:
        tries = int(r["attempts"]) + 1
        if exc.retryable and tries < TRIES:
            with write_tx(conn):
                conn.execute("UPDATE alert_outbox SET attempts = ?, error = ?, next_at = ?"
                             " WHERE id = ?",
                             (tries, exc.detail[:200],
                              to_ts(clock.now() + timedelta(seconds=RETRY_S[tries - 1])),
                              r["id"]))  # fmt: skip
            return
        _fallback(conn, clock, notifier, r, exc.detail)
    except Exception as exc:  # login, secret store, outcome unknown: fall back
        _fallback(conn, clock, notifier, r, _why(exc))
    else:
        _settle(conn, clock, r["id"], "sent")
        health.resolve_alert(conn, clock, notifier, FAIL_ALERT, None)


def _send(conn: sqlite3.Connection, clock: Clock, sender_for: SenderFor, r: sqlite3.Row) -> None:
    a = conn.execute("SELECT email FROM addresses WHERE address_id = ? AND removed_at IS NULL",
                     (r["address_id"],)).fetchone()  # fmt: skip
    if a is None:
        raise SendNotSentError("the sending address is no longer watched", retryable=False)
    frm = str(a["email"])
    mid = r["message_id"]
    if mid is None:  # chosen once, kept by every retry (send.py)
        mid = new_message_id(frm.rpartition("@")[2])
        with write_tx(conn):
            conn.execute("UPDATE alert_outbox SET message_id = ? WHERE id = ?", (mid, r["id"]))
    built = _build(conn, r, frm, str(mid))
    out = send.Outgoing(r["address_id"], "alert", frm, (str(r["destination"]),), built)
    send.submit(conn, clock, sender_for(conn, r["address_id"]), out)


def _build(conn: sqlite3.Connection, r: sqlite3.Row, frm: str, mid: str) -> Built:
    """The same bytes on every try: the Date is when the alert was queued."""
    return build_alert(from_addr=frm, to_addr=str(r["destination"]), subject=str(r["subject"]),
                       body=str(r["body"]) + FOOTER,
                       install_header=install_identity.header_value(conn),
                       date=from_ts(str(r["created_at"])), message_id=mid)  # fmt: skip


def _fallback(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, r: sqlite3.Row,
              why: str, *, tell: bool = True) -> None:  # fmt: skip
    """Not sent: Slack instead (if it didn't have it), and System Error while this lasts."""
    _settle(conn, clock, r["id"], "fallback", why)
    if not r["slack_done"]:
        _post(conn, clock, r["id"], str(r["subject"]), str(r["body"]))
    with write_tx(conn):
        conn.execute("INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
                     " VALUES (?, ?, 'alert.email_fallback', 'service', 'error',"
                     " json_object('type', ?, 'why', ?))",
                     (to_ts(clock.now()), r["address_id"], r["kind"], why[:200]))  # fmt: skip
    log.warning("alert_mail.fallback", address_id=r["address_id"], kind=r["kind"])
    if tell:
        text = (f"Alert email from {r['address_id']} isn't getting through ({why[:120]}); alerts"
                " go to Slack and the desktop meanwhile. Try: ecf alerts test")  # fmt: skip
        health.open_alert(conn, clock, notifier, FAIL_ALERT, None, text)


def _settle(conn: sqlite3.Connection, clock: Clock, rid: int, state: str,
            error: str | None = None) -> None:  # fmt: skip
    with write_tx(conn):
        conn.execute("UPDATE alert_outbox SET state = ?, settled_at = ?, attempts = attempts + 1,"
                     " error = coalesce(?, error) WHERE id = ?",
                     (state, to_ts(clock.now()), error and error[:200], rid))  # fmt: skip


def _rollups(conn: sqlite3.Connection, clock: Clock) -> None:
    """One roll-up per type once its oldest held alert is an hour old."""
    now = clock.now()
    due = conn.execute("SELECT kind FROM alert_outbox WHERE state = 'rolled_up' GROUP BY kind"
                       " HAVING min(created_at) <= ?", (to_ts(now - HOUR),)).fetchall()  # fmt: skip
    cfg = config(conn)
    for (kind,) in due:
        rows = conn.execute("SELECT id, subject, created_at FROM alert_outbox WHERE kind = ?"
                            " AND state = 'rolled_up' ORDER BY id", (kind,)).fetchall()  # fmt: skip
        with write_tx(conn):
            conn.executemany("UPDATE alert_outbox SET state = 'in_rollup', settled_at = ?"
                             " WHERE id = ?", [(to_ts(now), r["id"]) for r in rows])  # fmt: skip
        if cfg is None:
            continue  # email was turned off meanwhile; Slack already has each one
        lines = [f"{str(r['created_at'])[11:16]} UTC  {r['subject']}" for r in rows[:ROLLUP_LINES]]
        if len(rows) > ROLLUP_LINES:
            lines.append(f"... and {len(rows) - ROLLUP_LINES} more")
        body = (f"{len(rows)} {kind} alert(s) passed the email cap ({PER_TYPE_HOUR} an hour per"
                f" type, {PER_INSTALL_HOUR} in all) and weren't emailed one by one. Each also went"
                " to the desktop, and to Slack.\n\n" + "\n".join(lines))  # fmt: skip
        with write_tx(conn):
            conn.execute(
                "INSERT INTO alert_outbox (kind, subject, body, address_id, destination,"
                " created_at, state, rollup, slack_done, next_at) VALUES (?, ?, ?, ?, ?, ?,"
                " 'queued', 1, 1, ?)",
                (kind, clean(f"{PREFIX} {kind}: {len(rows)} more in the last hour",
                             SUBJECT_CHARS),
                 body, cfg.address_id, cfg.destination, to_ts(now), to_ts(now)),
            )  # fmt: skip


def _post(conn: sqlite3.Connection, clock: Clock, rid: int, subject: str, body: str) -> None:
    row = conn.execute("SELECT json_extract(value, '$') FROM settings WHERE key = ?",
                       (SUMMARY_CHANNEL,)).fetchone()  # fmt: skip
    if row is None or not row[0]:
        return
    slack_out.enqueue_post(conn, clock, key=f"alert_mail:{rid}", route=RouteRef(str(row[0])),
                           card=Card(subject, text=body))  # fmt: skip


def _why(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
