"""This install's side of `ecf upgrade`'s checks: `GET /v1/upgrade/state` (SPEC §11.10; OD-375,
OD-379, OD-381; V1.5 step 11a).

The running versions (product, schema actually applied, data format, API), the model pins in
force (the lock file's pins, before any `claude_model_override`, since a release changes the
file) and which addresses use each one (the gate's roles per preset), the install role (`prod`
refuses `--wheel`), and what makes an upgrade wait: executing items, held leases, and open `ecf
claude` sessions (OD-379). From v3.0.0 (ADR 0031) there is no local model pin; the prompt digest
is Claude's (`claude_review.prompt_digest`, P4), and `converted` names the addresses migration
0038 converted from A or B to C and paused, with how many of their emails wait (P3), and
`rules_customised` the applied rules' rules with a fixed id that stopped the v2 conversion (OD-508).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ecf import __version__
from ecf.errors import InvalidInputError
from ecf.schema import load_schema
from ecf_server import (
    claude_pins,
    claude_review,
    config,
    export_bundle,
    initsetup,
    jev_keys,
    stages,
)
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log

CURRENT = "upgrade.current"  # the last upgrade: {from, to, label, started_at, finished_at, ...}


def state(conn: sqlite3.Connection, clock: Clock, *, api_version: int,
          sessions: int) -> dict[str, Any]:  # fmt: skip
    lock = claude_pins.load_lock()
    pins = {f: lock[f] for f in claude_pins.ROLES}
    # preset D's Jev pin and mapping, as the wheel files read them (step 4, S4-D12, P18)
    pins["jev"] = jev_keys.pin()
    if (mapping := jev_keys.mapping_digest()) is not None:
        pins["jev_mapping"] = mapping
    users: dict[str, list[str]] = {}
    for aid, preset in conn.execute("SELECT address_id, preset FROM addresses WHERE removed_at"
                                    " IS NULL ORDER BY address_id"):  # fmt: skip
        for f in claude_pins.GATE_ROLES[preset]:
            users.setdefault(f, []).append(aid)
        if preset == jev_keys.PRESET:
            for f in ("jev", "jev_mapping"):
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
        # the shipped prompt's digest, as release.json has it (Q6; Claude's from v3.0.0, P4); the
        # install's, with its schema extension, apart: an extension carries over an upgrade
        "prompt_digest": claude_review.prompt_digest(load_schema()),
        "effective_prompt_digest": claude_review.prompt_digest(config.current_schema(conn)),
        # a stored extension, in force or not (OD-481): v2.2.0's upgrade check needs it (item 6)
        "extension_stored": config.current(conn)["schema"] is not None,
        # the C addresses a stored extension not yet in use would move to assist at the next
        # start (`config.activate_stored`; S4, P11): the pre-check lists them
        "extension_pending": config.pending_activation(conn),
        "pins": pins, "pin_users": users, "install_role": initsetup.role(conn),
        "busy": {"executing": executing, "leases": leases, "claude_sessions": sessions},
        "upgrade": current(conn),
        # A and B addresses 0038 made C and paused: [{address_id, from, waiting}] (P3)
        "converted": stages.converted(conn),
        # applied rules with a fixed id that stopped the v2 conversion: [{id, renamed_to,
        # renamed_from}] (OD-508); the new CLI's after-upgrade notice names each
        "rules_customised": config.rules_customised(conn),
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


def settle(conn: sqlite3.Connection, clock: Clock, *,
           on_settled: Callable[[], object] | None = None) -> bool:  # fmt: skip
    """The new service's first timer pass whose work all succeeded closes the rollback window
    (OD-377): afterwards `ecf upgrade --to` follows the after-settling rules (step 11c). Then,
    and only then, `on_settled` runs (the service's `ollama_unit.retire`: a rollback to v2.2.x
    needs ecf's Ollama login item until here; v3.0.0, R-D13, P13); a failure there is logged,
    and the window stays closed."""
    rec = current(conn)
    if rec is None or rec.get("settled_at") or rec.get("to") != __version__:
        return False
    now = to_ts(clock.now())
    with write_tx(conn):
        _put(conn, rec | {"settled_at": now}, now)
        conn.execute("INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?,"
                     " 'upgrade.settled', 'service', 'ok', ?)",
                     (now, json.dumps({"from": rec.get("from"), "to": rec.get("to")})))  # fmt: skip
    if on_settled is not None:
        try:
            on_settled()
        except Exception as exc:  # the clean-up is best effort; the upgrade has settled
            log.warning("upgrade.retire_failed", error_type=type(exc).__name__)
    return True


def _put(conn: sqlite3.Connection, rec: dict[str, Any], now: str) -> None:
    conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?,"
                 " 'upgrade') ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
                 " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                 (CURRENT, json.dumps(rec), now))  # fmt: skip
