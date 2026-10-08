"""This install's side of `ecf upgrade`'s checks: `GET /v1/upgrade/state` (SPEC §11.10; OD-375,
OD-379, OD-381; V1.5 step 11a).

The running versions (product, schema actually applied, data format, API), the model pins in
force (the lock files' pins, before any `claude_model_override`, since a release changes the
files) and which addresses use each one (the gate's roles per preset, and the local model for A
and B), the install role (`prod` refuses `--wheel`), and what makes an upgrade wait: executing
items, held leases, and open `ecf claude` sessions (OD-379).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ecf import __version__
from ecf.errors import InvalidInputError
from ecf.schema import load_schema
from ecf_server import claude_pins, export_bundle, initsetup, ollama
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx

LOCAL = "local"  # the Ollama digest's key among the pins
CURRENT = "upgrade.current"  # the last upgrade: {from, to, label, started_at, finished_at, ...}


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
        "classifier_schema": load_schema().version,
        "pins": pins, "pin_users": users, "install_role": initsetup.role(conn),
        "busy": {"executing": executing, "leases": leases, "claude_sessions": sessions},
        "upgrade": current(conn),
    }  # fmt: skip


def current(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (CURRENT,)).fetchone()
    return None if row is None else json.loads(row[0])


def finish(conn: sqlite3.Connection, clock: Clock, body: dict[str, Any]) -> dict[str, Any]:
    """`ecf upgrade --continue` started this version (V1.5 step 11b): recorded, not yet settled."""
    rec = {k: body.get(k) for k in ("from", "to", "label", "started_at", "pin_changes",
                                    "affected")} | {"finished_at": to_ts(clock.now()),
                                                    "settled_at": None}  # fmt: skip
    if rec["to"] != __version__:
        raise InvalidInputError(f"this service is {__version__}, not {rec['to']}")
    now = to_ts(clock.now())
    with write_tx(conn):
        _put(conn, rec, now)
        conn.execute("INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?,"
                     " 'upgrade.completed', 'os_user', 'ok', ?)",
                     (now, json.dumps(rec)))  # fmt: skip
    return rec


def settle(conn: sqlite3.Connection, clock: Clock) -> bool:
    """The new service's first timer pass whose work all succeeded closes the rollback window
    (OD-377): afterwards `ecf upgrade --to` follows the after-settling rules (step 11c)."""
    rec = current(conn)
    if rec is None or rec.get("settled_at") or rec.get("to") != __version__:
        return False
    now = to_ts(clock.now())
    with write_tx(conn):
        _put(conn, rec | {"settled_at": now}, now)
        conn.execute("INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?,"
                     " 'upgrade.settled', 'service', 'ok', ?)",
                     (now, json.dumps({"from": rec.get("from"), "to": rec.get("to")})))  # fmt: skip
    return True


def _put(conn: sqlite3.Connection, rec: dict[str, Any], now: str) -> None:
    conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?,"
                 " 'upgrade') ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
                 " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                 (CURRENT, json.dumps(rec), now))  # fmt: skip
