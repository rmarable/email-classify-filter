"""What `ecf init` has done (SPEC §13.1; V1.2 step 11c), and the install role.

`ecf init` runs on the client and calls the ordinary routes (Slack install, address add); this
module only reports which steps are done, from the service's own state, so `init status` and
`init --resume` never trust the client's memory. The install role (`prod` or `test`, OD-106) is
set once, by `ecf init`, and never changed.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ecf.errors import ConflictError, InvalidInputError
from ecf_server import addresses, fallback, models, slack_admin
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx

ROLE_KEY = "install_role"
ROLES = ("prod", "test")
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


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    s = slack_admin.status(conn)
    return {
        "install_role": role(conn),
        "slack_installed": s["app_id"] is not None,
        "slack_member": s["member"],
        "slack_pending_app": s["pending_app_id"],
        "org_domains": addresses.get_org_domains(conn),
        "addresses": [a["address_id"] for a in addresses.list_addresses(conn)],
        "models": {
            "needed": any(a["preset"] in LOCAL_PRESETS for a in addresses.list_addresses(conn))
            or fallback.needs_ollama(conn),
            "installed": models.installed(conn),
        },
        "fallback_off": fallback.reminders(conn),  # B and C addresses with it off (§4.3)
    }
