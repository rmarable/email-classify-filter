"""Applying an import: `ecf import <bundle> [--replace]` (SPEC §11.9; OD-355, OD-357 to OD-361;
V1.5 step 9c).

1. The bundle is read and checked (bundle_reader.py). The target must have no addresses and no
   items, unless `--replace` with this install's name typed (OD-357). Step-up is bound to the
   bundle's path, its SHA-256 and whether it replaces.
2. **A temporary database** (0600, in the data directory) is migrated to the bundle's schema
   version, the bundle's rows are loaded into it (STRICT types, CHECK constraints and foreign keys
   check each row; a column the schema doesn't have is refused), then the normal migrations bring
   it up to date (OD-355). There the rules of import_plan.py are applied: addresses paused, stage
   at most `assist`, outbound off; waiting items back to `awaiting_approval`, executing ones to
   `failed_unknown`; Slack routes and open alerts dropped (alerts describe the other computer);
   `eval_runs.path` cleared (the file stayed behind); `origin` set on items and audit rows
   (OD-358, OD-360). Addresses, settings kept by the allow-list and the security-relevant config
   (through the `config apply` checks) are validated (OD-359).
3. **One transaction** on the live database: with `--replace`, the target's address and item data
   is cleared (children first); the temporary database's tables are copied in; kept settings
   overwrite the target's; any failure rolls everything back (OD-361). Then each re-posted item
   gets a fresh grant, as an approval re-offer does (cards post once Slack is reconnected).
   Audit, model-call and Claude-call rows get new row IDs (appended to this install's history).
   Audited as `import.completed` and announced as a Security Notice naming the config changed.
"""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, cast

from ecf.errors import ConflictError, InvalidInputError, SchemaLimitError
from ecf.ids import SLUG_PATTERN
from ecf_server import (
    approvals,
    config,
    db,
    export_keys,
    import_plan,
    items,
    settings,
    stepup,
)
from ecf_server.bundle_reader import BadBundleError, Parsed
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier
from ecf_server.outbound_msg import addr_spec

# parents before children (foreign keys); settings and schema_migrations are handled apart
COPY = ("addresses", "cursors", "probe", "items", "excerpts", "escalations", "senders", "sent",
        "threads", "gate", "eval_runs", "eval_results", "audit", "model_calls", "claude_calls",
        "claude_sessions")  # fmt: skip
NOT_COPIED = ("settings", "schema_migrations", "routes", "alerts")
NEW_IDS = ("audit", "model_calls", "claude_calls")  # INTEGER PRIMARY KEY: appended here
# what --replace clears, children first; jobs and Slack messages of the old items go too
CLEAR = ("grants", "delays", "claim_batches", "claims", "escalations", "excerpts",
         "fallback_shadow", "processing", "leases", "check_state", "routes", "probe", "cursors",
         "items", "alerts", "addresses", "senders", "sent", "threads", "gate", "eval_runs",
         "eval_results", "model_calls", "claude_calls", "claude_sessions")  # fmt: skip


@stepup.purpose("import")
def _describe(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    del conn
    path, sha = str(target.get("path", "")), str(target.get("sha256", ""))
    replace = bool(target.get("replace"))
    text = (f"ecf: import the bundle {path} (SHA-256 {sha[:12]})"
            + (", replacing this install's data" if replace else ""))  # fmt: skip
    return stepup.Bound(stepup.digest("import", path, sha, replace), text)


def apply(  # noqa: PLR0913 - the service state it needs, then the request
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    data_dir: Path,
    install: str,
    parsed: Parsed,
    path: str,
    *,
    replace: bool,
    typed_install: str | None,
    nonce: str | None,
) -> dict[str, Any]:
    if not import_plan.target_empty(conn):
        if not replace:
            raise ConflictError("this install already has addresses or items; to replace them,"
                                " add --replace and type the install name")  # fmt: skip
        if typed_install != install:
            raise InvalidInputError("--replace needs this install's name typed exactly")
    sha = parsed.opened.sha256
    stepup.consume(conn, clock, "import", {"path": path, "sha256": sha, "replace": replace}, nonce)
    started = time.monotonic()
    source = str(parsed.manifest.get("install_id"))
    with tempfile.NamedTemporaryFile(dir=data_dir, prefix=".import-", suffix=".db") as tmp:
        staged = db.connect(Path(tmp.name))
        try:
            try:
                _stage(staged, parsed, source)
                kept = kept_settings(staged)
                config_doc = validate(staged, kept)
            except sqlite3.Error as exc:  # malformed JSON in a row, a value of the wrong type
                raise BadBundleError(f"the bundle's rows don't load ({exc})") from None
            before = config.current(conn)
            counts = _apply(conn, clock, tmp.name, kept, replace=replace)
        finally:
            staged.close()
    changes = config.diff(before, config_doc) if config_doc else []
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute(
            "INSERT INTO audit (ts, event, actor, outcome, data, origin) VALUES (?,"
            " 'import.completed', 'os_user', 'ok', ?, ?)",
            (now, json.dumps({
                "person": stepup.person(), "source_install": source,
                "source_kind": parsed.manifest.get("kind"),
                "exported_at": parsed.manifest.get("created_at"), "sha256": sha,
                "signer": parsed.signer, "replace": replace, "counts": counts,
                "data_format": parsed.manifest.get("data_format"),
                "duration_s": round(time.monotonic() - started, 1)}), source),
        )  # fmt: skip
    how = "replacing" if replace else "into"
    text = (f"Data from install {source} was imported ({counts.get('addresses', 0)} addresses,"
            f" {counts.get('items', 0)} emails; {how} this install). Addresses are paused;"
            " re-enter app passwords and reconnect Slack.")  # fmt: skip
    if changes:
        text += f" Security-relevant config changed: {config.summary(changes, 200)}."
    export_keys.notice(conn, clock, notifier, text + " If this wasn't you, check the computer"
                                                     " ecf runs on.")  # fmt: skip
    return {"source_install": source, "counts": counts, "config_changes": changes,
            "replace": replace}  # fmt: skip


# ---- staging ------------------------------------------------------------------------------------


def load(staged: sqlite3.Connection, parsed: Parsed) -> None:
    """Load the bundle's rows into a temporary database built at its schema version, then bring
    it up to date with the normal migrations (OD-355). Restore uses this too."""
    db.migrate(staged, up_to=int(parsed.manifest["schema_version"]))
    with write_tx(staged), db.items_writer("create"):
        for table in (*COPY, "routes", "alerts"):
            rows = parsed.tables.get(table, [])
            if rows:
                insert(staged, table, rows)
        rows = parsed.tables.get("settings", [])
        if rows:
            insert(staged, "settings", rows, replace=True)
    db.migrate(staged)
    items.map_imported(staged, import_plan.REPOST, import_plan.UNKNOWN)
    with write_tx(staged):
        staged.execute("UPDATE eval_runs SET path = ''")  # the result file stayed behind


def _stage(staged: sqlite3.Connection, parsed: Parsed, source: str) -> None:
    load(staged, parsed)
    with write_tx(staged):
        staged.execute("UPDATE addresses SET paused = 1, outbound = 0, stage ="
                       " CASE WHEN stage = 'live' THEN 'assist' ELSE stage END")  # fmt: skip
        staged.execute("UPDATE items SET origin = ?", (source,))
        staged.execute("UPDATE audit SET origin = ?", (source,))


def insert(c: sqlite3.Connection, table: str, rows: list[dict[str, Any]], *,
           replace: bool = False) -> None:  # fmt: skip
    have = {r[1] for r in c.execute(f'PRAGMA main.table_info("{table}")')}
    for n, row in enumerate(rows, 1):
        extra = set(row) - have
        if extra:
            raise BadBundleError(f"{table} row {n} has a column this schema doesn't:"
                                 f" {sorted(extra)[0][:40]}")  # fmt: skip
    cols = sorted({k for r in rows for k in r})
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    sql = (f'{verb} INTO main."{table}" ({", ".join(f"[{k}]" for k in cols)})'
           f" VALUES ({', '.join('?' for _ in cols)})")  # fmt: skip
    try:
        c.executemany(sql, [[r.get(k) for k in cols] for r in rows])
    except sqlite3.IntegrityError as exc:
        raise BadBundleError(f"{table}: a row breaks a rule of the schema ({exc})") from None


def kept_settings(staged: sqlite3.Connection) -> dict[str, str]:
    """The settings the allow-list keeps, from the staged (migrated) copy (OD-358)."""
    out: dict[str, str] = {}
    for key, value in staged.execute("SELECT key, value FROM settings ORDER BY key"):
        if import_plan.setting_kept(key):
            out[key] = import_plan.kept_setting_value(key, value)
    return out


def validate(staged: sqlite3.Connection, kept: dict[str, str]) -> dict[str, Any]:
    """Re-check what the schema can't: addresses, kept settings, and the config (OD-359)."""
    for a in staged.execute("SELECT address_id, email, overrides FROM addresses"):
        if not re.fullmatch(SLUG_PATTERN, str(a["address_id"])):
            raise BadBundleError("an address ID isn't valid")
        try:
            addr_spec(str(a["email"]))
        except InvalidInputError:
            raise BadBundleError(f"address {a['address_id']}: not a plain email address") from None
        if not isinstance(json.loads(a["overrides"]), dict):
            raise BadBundleError(f"address {a['address_id']}: overrides aren't an object")
    for key, value in kept.items():
        if key in settings.KEYS:
            _check_setting(key, json.loads(value))
    section_of = {v: k for k, v in config.KEY.items()}
    doc = {section_of[k]: json.loads(v) for k, v in kept.items() if k in section_of}
    if doc:
        try:
            return config.validate(staged, doc)
        except (InvalidInputError, SchemaLimitError) as exc:  # an extension over a cap too
            raise BadBundleError(f"the bundle's config fails its checks: {exc.detail}") from None
    return {}


def _check_setting(key: str, value: Any) -> None:
    k = settings.KEYS[key]
    try:
        if key == "business_hours":
            keys = set(cast("dict[str, Any]", value)) if isinstance(value, dict) else set[str]()
            if keys != {"days", "start", "end", "tz"}:
                raise InvalidInputError("business hours")
            return
        k.parse(str(value).lower() if isinstance(value, bool) else str(value))
    except InvalidInputError:
        raise BadBundleError(f"setting {key} has a value ecf can't use") from None


# ---- applying -----------------------------------------------------------------------------------


def _apply(conn: sqlite3.Connection, clock: Clock, staged_path: str, kept: dict[str, str], *,
           replace: bool) -> dict[str, int]:  # fmt: skip
    conn.execute("ATTACH DATABASE ? AS imp", (staged_path,))
    try:
        with write_tx(conn), db.items_writer("create"):
            if replace:
                old = [r[0] for r in conn.execute("SELECT address_id FROM main.addresses")]
                for table in CLEAR:
                    conn.execute(f'DELETE FROM main."{table}"')  # noqa: S608 - fixed names
                conn.execute("DELETE FROM main.jobs WHERE address_id IN (SELECT value FROM"
                             " json_each(?))", (json.dumps(old),))  # fmt: skip
                conn.execute("DELETE FROM main.slack_messages WHERE key LIKE 'item:%'")
            counts: dict[str, int] = {}
            for table in COPY:
                counts[table] = copy(conn, table)
            now = to_ts(clock.now())
            for key, value in kept.items():
                conn.execute(
                    "INSERT INTO main.settings (key, value, updated_at, updated_by)"
                    " VALUES (?, ?, ?, 'import') ON CONFLICT (key) DO UPDATE SET"
                    " value = excluded.value, updated_at = excluded.updated_at,"
                    " updated_by = excluded.updated_by",
                    (key, value, now),
                )  # fmt: skip
        rows = conn.execute("SELECT stable_id FROM main.items WHERE status ="
                            " 'awaiting_approval' ORDER BY stable_id")  # fmt: skip
        reposted = [r[0] for r in rows]
    finally:
        conn.execute("DETACH DATABASE imp")
    for sid in reposted:
        approvals.reissue_after_import(conn, clock, sid)
    counts["reposted"] = len(reposted)
    return counts


def copy(conn: sqlite3.Connection, table: str, *, keep_ids: bool = False) -> int:
    """Copy the attached `imp` table into `main`; audit, model-call and Claude-call rows get new
    row IDs unless `keep_ids` (restore into an emptied table)."""
    cols = [r[1] for r in conn.execute(f'PRAGMA imp.table_info("{table}")')]
    if table in NEW_IDS and not keep_ids:
        cols = [c for c in cols if c != "id"]
    names = ", ".join(f"[{c}]" for c in cols)
    order = " ORDER BY id" if table in NEW_IDS else ""
    try:
        cur = conn.execute(f'INSERT INTO main."{table}" ({names}) SELECT {names}'  # noqa: S608
                           f' FROM imp."{table}"{order}')  # fmt: skip
    except sqlite3.IntegrityError as exc:
        raise ConflictError(f"{table}: the bundle's rows clash with this install's ({exc}); use"
                            " --replace to replace them") from None  # fmt: skip
    return cur.rowcount
