"""What `ecf init` has done (SPEC §13.1; V1.2 step 11c), and the install role.

`ecf init` runs on the client and calls the ordinary routes (Slack install, address add); this
module only reports which steps are done, from the service's own state, so `init status` and
`init --resume` never trust the client's memory. The install role (`prod` or `test`, OD-106) is
set once, by `ecf init`, and never changed.

V1.5 step 13b (OD-400 to OD-403): the alert-email and backup steps' state, and the markers a
skipped step leaves (`init.skipped.<step>`, audited `init.step_skipped`), so `init --resume`
doesn't ask it again and `init status` shows when it was skipped.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ecf.errors import ConflictError, InvalidInputError
from ecf_server import (
    addresses,
    alert_mail,
    claude_pins,
    export_keys,
    fallback,
    internal,
    models,
    scheduled_export,
    slack_admin,
)
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.facts import PUBLIC_DOMAINS

ROLE_KEY = "install_role"
ROLES = ("prod", "test")
SKIPPABLE = ("email", "export")  # the questions `init` asks that you may decline (OD-402)
SKIP_PREFIX = "init.skipped."
LOCAL_PRESETS = ("A", "B")  # the presets that run Ollama (§4); C too with the local fallback on


def role(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (ROLE_KEY,)).fetchone()
    return str(json.loads(row[0])) if row else None


def set_role(conn: sqlite3.Connection, clock: Clock, value: str) -> dict[str, Any]:
    if value not in ROLES:
        raise InvalidInputError("the install role is prod or test")
    was = role(conn)
    if was == value:
        return {"install_role": value, "changed": False}
    if was is not None:
        raise ConflictError(f"this install is {was}; the role is fixed when the install is created")
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'os_user')",
            (ROLE_KEY, json.dumps(value), now),
        )
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, NULL, 'init.role_set', 'os_user', 'ok', ?)",
            (now, json.dumps({"install_role": value})),
        )
    return {"install_role": value, "changed": True}


def skip(conn: sqlite3.Connection, clock: Clock, step: str) -> dict[str, Any]:
    """Record that a step was declined (OD-402); declining again moves the date."""
    if step not in SKIPPABLE:
        raise InvalidInputError(f"steps you can skip: {', '.join(SKIPPABLE)}")
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'os_user')"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (SKIP_PREFIX + step, json.dumps(now), now),
        )
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, NULL, 'init.step_skipped', 'os_user', 'ok', ?)",
            (now, json.dumps({"step": step})),
        )
    return {"skipped": skipped(conn)}


def skipped(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'init.skipped.%'")
    return {str(k).removeprefix(SKIP_PREFIX): str(json.loads(v)) for k, v in rows}


def _alert_email(conn: sqlite3.Connection) -> dict[str, str] | None:
    cfg = alert_mail.config(conn)
    return None if cfg is None else {"from": cfg.address_id, "from_email": cfg.email,
                                     "to": cfg.destination}  # fmt: skip


def _export(conn: sqlite3.Connection) -> dict[str, Any]:
    key = export_keys.current(conn)
    last = scheduled_export.status(conn)["last_ok"]
    return {
        "key": None if key is None else key["fingerprint"],
        "dir": export_keys.export_dir(conn),
        "schedule": scheduled_export.schedule(conn),
        "last_ok": None if last is None else last["at"],
    }


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    s = slack_admin.status(conn)
    listed = addresses.list_addresses(conn)
    return {
        "install_role": role(conn),
        "slack_installed": s["app_id"] is not None,
        "slack_member": s["member"],
        "slack_pending_app": s["pending_app_id"],
        "org_domains": addresses.get_org_domains(conn),
        "org_addresses": len(internal.org_addresses(conn)),  # V1.6 (OD-431)
        # every watched address is at a public provider: no org domain is needed (OD-441)
        "public_only": bool(listed)
        and all(a["email"].rsplit("@", 1)[-1].lower() in PUBLIC_DOMAINS for a in listed),
        "addresses": [a["address_id"] for a in listed],
        "smtp_addresses": [a["address_id"] for a in listed if a["smtp_host"]],
        "alert_email": _alert_email(conn),
        "export": _export(conn),
        "skipped": skipped(conn),
        "models": {
            "needed": any(a["preset"] in LOCAL_PRESETS for a in listed)
            or fallback.needs_ollama(conn),
            "installed": models.installed(conn),
        },
        "fallback_off": fallback.reminders(conn),  # B and C addresses with it off (§4.3)
        "claude_needed": claude_pins.in_use(conn),  # init offers ecf's Claude login (V1.4 step 11)
    }
