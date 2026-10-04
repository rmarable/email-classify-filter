"""`ecf doctor`'s sending, alert-email and backup checks (SPEC §13.2; V1.5 step 13a; OD-394 to
OD-398).

They run in the service, which writes the backups and sends the mail, and answer rows shaped like
slack_doctor's (`{name, level, detail, fix}`):

- **SMTP**, per address, from what is stored: the server, the SMTP result of the last probe
  (`address add`, `address set --app-password` or `--smtp-host`) and the last accepted send; no
  live login (OD-396). A missing server or a failed check fails once the address has outbound on
  or sends alert email, else warns.
- **Alert email**: off, or sender -> destination with the last delivery and the queue; a warning
  once the oldest queued email has waited 10 minutes (an `alert_email` System Error already
  covers the failure). **Reach**: a warning when alert email is off and desktop notifications
  are off or unavailable, since Slack-health alerts then reach nobody (OD-397).
- **Backups**: set up or not, the schedule, `export_dir` writable (a test file) and off the data
  directory's disk, and the last backup's age: fine to one period plus 2 h, a warning to two
  periods, then a failure; a first backup is "due" for one period plus 2 h after setup (OD-398).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ecf.errors import InvalidInputError
from ecf_server import addresses, alert_mail, alerts, export_keys, scheduled_export
from ecf_server.clock import Clock, from_ts

OK, WARN, FAIL = "ok", "warn", "FAIL"
QUEUE_LATE = timedelta(minutes=10)
GRACE = timedelta(hours=2)


def _c(name: str, level: str, detail: str, fix: str = "") -> dict[str, str]:
    return {"name": name, "level": level, "detail": detail, "fix": fix}


def checks(
    conn: sqlite3.Connection, clock: Clock, data_dir: Path, notifier: str
) -> list[dict[str, str]]:
    now = clock.now()
    return [
        *smtp(conn),
        *alert_email(conn, now),
        *reach(conn, notifier),
        *backups(conn, now, data_dir),
    ]


# ---- SMTP ---------------------------------------------------------------------------------------


def smtp(conn: sqlite3.Connection) -> list[dict[str, str]]:
    cfg = alert_mail.config(conn)
    sender = cfg.address_id if cfg else None
    out: list[dict[str, str]] = []
    for a in addresses.list_addresses(conn):
        aid = a["address_id"]
        uses = [w for w, on in (("outbound is on", a["outbound"]),
                                ("it sends alert email", aid == sender)) if on]  # fmt: skip
        bad = FAIL if uses else WARN
        why = f" ({' and '.join(uses)})" if uses else ""
        name = f"smtp {aid}"
        if not a["smtp_host"]:
            out.append(_c(name, bad, f"no SMTP server set{why}",
                          f"ecf address set {aid} --smtp-host <host>"))  # fmt: skip
            continue
        server = f"{a['smtp_host']}:{a['smtp_port']}"
        probe: dict[str, Any] = a.get("probe") or {}
        result: dict[str, Any] | None = probe.get("smtp")
        last = _last_send(conn, aid)
        probed = probe.get("probed_at")
        if last is not None and (probed is None or last > str(probed)):
            out.append(_c(name, OK, f"{server}; last send accepted {last[:16]}Z"))
        elif result is None:
            out.append(_c(name, bad, f"{server}, not checked yet{why}",
                          f"ecf address set {aid} --app-password (checks it)"))  # fmt: skip
        elif not result.get("ok"):
            out.append(_c(name, bad, f"{server}: {result.get('error') or 'login failed'}{why}",
                          f"fix the server or app password, then ecf address set {aid}"
                          " --smtp-host <host> (or --app-password) to check again"))  # fmt: skip
        else:
            out.append(_c(name, OK, f"{server}; login checked {str(probed)[:16]}Z"))
    return out


def _last_send(conn: sqlite3.Connection, address_id: str) -> str | None:
    row = conn.execute("SELECT max(sent_at) FROM sent WHERE address_id = ? AND status = 'sent'",
                       (address_id,)).fetchone()  # fmt: skip
    return None if row is None or row[0] is None else str(row[0])


# ---- alert email and reach ----------------------------------------------------------------------


def alert_email(conn: sqlite3.Connection, now: datetime) -> list[dict[str, str]]:
    cfg = alert_mail.config(conn)
    if cfg is None:
        return [_c("alert email", OK, "off (alerts go to Slack and the desktop)")]
    head = f"from {cfg.email} ({cfg.address_id}) to {cfg.destination}"
    sent = conn.execute("SELECT max(settled_at) FROM alert_outbox WHERE state = 'sent'"
                        ).fetchone()[0]  # fmt: skip
    queued, oldest = conn.execute("SELECT count(*), min(created_at) FROM alert_outbox"
                                  " WHERE state = 'queued'").fetchone()  # fmt: skip
    last = f"last delivered {str(sent)[:16]}Z" if sent else "none delivered in the last 7 days"
    if queued and now - from_ts(str(oldest)) >= QUEUE_LATE:
        mins = int((now - from_ts(str(oldest))).total_seconds() // 60)
        return [_c("alert email", WARN, f"{head}; {queued} queued, the oldest for {mins} min;"
                   f" {last}", "ecf logs --event alert_mail.; ecf alerts test")]  # fmt: skip
    tail = f"; {queued} queued" if queued else ""
    return [_c("alert email", OK, f"{head}; {last}{tail}")]


def reach(conn: sqlite3.Connection, notifier: str) -> list[dict[str, str]]:
    """§13.2: with Slack down, only email and the desktop can tell you."""
    if alert_mail.config(conn) is not None:
        return []
    desktop_on = alerts.show(conn)["desktop"] == "on"
    if desktop_on and notifier != "none":
        return []
    why = "desktop notifications are off" if not desktop_on else "no desktop notifier here"
    return [_c("alert reach", WARN, f"alert email is off and {why}: a Slack failure would reach"
               " nobody", "ecf alerts email set --from <address> --to <destination>"
               + ("" if desktop_on else ", or ecf settings set notifications on"))]  # fmt: skip


# ---- backups ------------------------------------------------------------------------------------


def backups(conn: sqlite3.Connection, now: datetime, data_dir: Path) -> list[dict[str, str]]:
    key, where = export_keys.current(conn), export_keys.export_dir(conn)
    if key is None or where is None:
        fix = ("ecf export keys rotate, then ecf export dir set <directory>" if key is None
               else "ecf export dir set <directory>")  # fmt: skip
        return [_c("backups", WARN, "not set up: no " + ("backup key" if key is None
                                                          else "backup folder"), fix)]  # fmt: skip
    sched = scheduled_export.schedule(conn)
    out = [_folder(where, data_dir)]
    if sched == "off":
        out.append(_c("backups", WARN, "scheduled backups are off (export_schedule: off)",
                      "ecf config apply with export_schedule: daily"))  # fmt: skip
        return out
    out.append(_age(conn, now, sched))
    return out


def _folder(where: str, data_dir: Path) -> dict[str, str]:
    try:
        export_keys.check_dir(where, data_dir)
    except InvalidInputError as exc:
        return _c("backup folder", FAIL, str(exc.detail), "ecf export dir set <directory>")
    same = export_keys.same_volume_or_none(Path(where), data_dir)
    if same:
        return _c("backup folder", WARN, f"{where}: on the same disk as ecf's data",
                  "ecf export dir set <a folder on another disk or a synced folder>")  # fmt: skip
    return _c("backup folder", OK, f"{where} (writable, another disk or a synced folder)")


def _age(conn: sqlite3.Connection, now: datetime, sched: str) -> dict[str, str]:
    st = scheduled_export.status(conn)
    period = scheduled_export.PERIOD[sched]
    last: dict[str, Any] | None = st["last_ok"]
    failing = (f"; the last {st['failures']} attempt(s) failed: {st['last_error']}"
               if st["failures"] else "")  # fmt: skip
    fix = "ecf export now (it says why it fails); ecf export status"
    if last is None:
        since = _set_up_at(conn)
        if since is None or now - since <= period + GRACE:
            lvl = WARN if failing else OK
            return _c("backups", lvl, f"{sched}; the first backup is due{failing}",
                      fix if failing else "")  # fmt: skip
        age, what = now - since, "no backup yet, set up"
    else:
        age, what = now - from_ts(last["at"]), "last backup"
    text = f"{sched}; {what} {_ago(age)} ago{failing}"
    if age <= period + GRACE:
        return _c("backups", WARN if failing else OK, text, fix if failing else "")
    return _c("backups", WARN if age <= 2 * period else FAIL, text, fix)


def _set_up_at(conn: sqlite3.Connection) -> datetime | None:
    """When backups became set up: the later of the key's and the folder's last change."""
    row = conn.execute("SELECT max(updated_at) FROM settings WHERE key IN (?, ?)",
                       (export_keys.KEY_KEY, export_keys.DIR_KEY)).fetchone()  # fmt: skip
    return None if row is None or row[0] is None else from_ts(str(row[0]))


def _ago(d: timedelta) -> str:
    hours = int(d.total_seconds() // 3600)
    return f"{hours} h" if hours < 48 else f"{hours // 24} days"
