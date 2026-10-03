"""This install's side of `ecf upgrade`'s checks: `GET /v1/upgrade/state` (SPEC §11.10; OD-375,
OD-379, OD-381; V1.5 step 11a).

The running versions (product, schema actually applied, data format, API), the model pins in
force (the lock files' pins, before any `claude_model_override`, since a release changes the
files) and which addresses use each one (the gate's roles per preset, and the local model for A
and B), the install role (`prod` refuses `--wheel`), and what makes an upgrade wait: executing
items, held leases, and open `ecf claude` sessions (OD-379).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ecf import __version__
from ecf_server import claude_pins, export_bundle, initsetup, ollama
from ecf_server.clock import Clock, to_ts

LOCAL = "local"  # the Ollama digest's key among the pins


def state(conn: sqlite3.Connection, clock: Clock, *, api_version: int,
          sessions: int) -> dict[str, Any]:  # fmt: skip
    lock = claude_pins.load_lock()
    pins = {f: lock[f] for f in claude_pins.ROLES} | {LOCAL: ollama.load_pin().digest}
    users: dict[str, list[str]] = {}
    for aid, preset in conn.execute("SELECT address_id, preset FROM addresses WHERE removed_at"
                                    " IS NULL ORDER BY address_id"):  # fmt: skip
        fams = list(claude_pins.GATE_ROLES[preset])
        if preset in claude_pins.LOCAL_PRESETS:
            fams.append(LOCAL)
        for f in fams:
            users.setdefault(f, []).append(aid)
    executing = conn.execute("SELECT count(*) FROM items WHERE status IN ('executing',"
                             " 'undoing')").fetchone()[0]  # fmt: skip
    leases = conn.execute("SELECT count(*) FROM leases WHERE expires_at > ?",
                          (to_ts(clock.now()),)).fetchone()[0]  # fmt: skip
    schema = int(conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0])
    return {
        "version": __version__, "schema_version": schema,
        "data_format": export_bundle.DATA_FORMAT, "api_version": api_version,
        "pins": pins, "pin_users": users, "install_role": initsetup.role(conn),
        "busy": {"executing": executing, "leases": leases, "claude_sessions": sessions},
    }  # fmt: skip
