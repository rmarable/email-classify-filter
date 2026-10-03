"""`ecf restore <bundle>` (SPEC §11.9; OD-121, OD-318, OD-362 to OD-373; V1.5 step 10a).

Restore recovers *this* install, unlike import, which brings in another's data.

- **Which bundles** (OD-362): signed by the backup key you type, checked against the key derived
  from it, never a key inside the bundle: a scheduled bundle, or a signed manual one (its
  passphrase and the backup key). Anything else goes through `ecf import`.
- **Same install** (OD-363): an empty install (a new computer) takes the bundle's install ID; one
  with data must already have it.
- **Older bundles** (OD-365): a bundle older than this install's newest export (`export.seq`)
  needs a second yes.
- **Confirmation** (OD-369): you confirm the old computer's service is stopped; if it isn't, the
  generation bump and the second-install check pause its mail anyway (OD-318).
- **Safety copy** (OD-370): before restoring over data, a backup-API copy of the database goes to
  `<data>/restores/` (0600), kept 7 days.
- **What comes back** (OD-368): every exported table, Slack routes and alerts included, and the
  whole settings table, except this computer's own state, which keeps the target's values
  (`MACHINE_LOCAL`). Addresses keep their stage and outbound setting but arrive paused (OD-366);
  `ecf resume` refuses an address until a mail check has passed since the restore. Items in flight
  are mapped as for import (OD-367). `install.generation` becomes one more than the larger of the
  bundle's and this install's; `export.seq` the larger of the two.
- **The backup key** (OD-364): the signing seed is re-derived from the typed key and stored, so
  scheduled backups go on with the same key.
- **Preview** (OD-373): the security-relevant config and sender-record differences, for
  information.

Step-up is bound to the bundle's path and SHA-256. One transaction on the live database;
`restore.completed` is audited and a Security Notice sent.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from ecf.errors import ConflictError, InvalidInputError
from ecf.status import CHECK_FAILED
from ecf_server import (
    approvals,
    backup_key,
    config,
    db,
    export_keys,
    import_plan,
    importer,
    install_identity,
    scheduled_export,
    stepup,
)
from ecf_server.bundle_reader import BadBundleError, Parsed
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier
from ecf_server.secretstore import SecretStore
from ecf_server.secretstore.select import INTERPRETER_KEY

AT_KEY = "restore.at"
AWAITING_KEY = "restore.awaiting_check"  # address IDs ecf resume holds until a check passes
SAFETY_KEEP = timedelta(days=7)
# this computer's state: the target keeps its own values (OD-368)
MACHINE_LOCAL = frozenset({
    INTERPRETER_KEY, export_keys.DIR_KEY, scheduled_export.LAST_OK, scheduled_export.FAILURES,
    scheduled_export.LAST_ERROR, scheduled_export.NEXT_TRY, "audit.flushed_id",
    "models.installed_at",
})  # fmt: skip
COPIED = (*importer.COPY, "routes", "alerts")


def check(conn: sqlite3.Connection, parsed: Parsed) -> dict[str, Any]:
    """What restore requires of the bundle, and what it would change (the preview, OD-373)."""
    if not parsed.typed_verified:
        raise InvalidInputError("restore needs a bundle signed by the backup key you typed; for"
                                " another install's data, or an unsigned bundle, use ecf"
                                " import")  # fmt: skip
    empty = import_plan.target_empty(conn)
    theirs = str(parsed.manifest.get("install_id"))
    if not empty and theirs != install_identity.install_id(conn):
        raise ConflictError("that bundle is from another install; use ecf import")
    seq = int(parsed.manifest.get("seq") or 0)
    ours = int(scheduled_export.status_seq(conn))
    t = parsed.tables
    bundle_cfg = {s: json.loads(r["value"]) for r in t.get("settings", [])
                  for s, k in config.KEY.items() if r["key"] == k}  # fmt: skip
    current = config.current(conn)
    changes = config.diff(current, {s: v for s, v in bundle_cfg.items() if v is not None})
    return {
        "source": {
            k: parsed.manifest.get(k)
            for k in ("install", "install_id", "generation", "kind", "created_at", "seq")
        },
        "empty_target": empty,
        "older": not empty and seq < ours,
        "seq": seq,
        "newest_seq_here": ours,
        "counts": {name: len(rows) for name, rows in sorted(t.items())},
        "addresses": [
            {
                "address_id": a.get("address_id"),
                "email": a.get("email"),
                "stage": a.get("stage"),
                "outbound": bool(a.get("outbound")),
            }
            for a in t.get("addresses", [])
            if a.get("removed_at") is None
        ],
        "config_changes": changes,
        "senders": _sender_diff(conn, t.get("senders", [])),
    }


@stepup.purpose("restore")
def _describe(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    del conn
    path, sha = str(target.get("path", "")), str(target.get("sha256", ""))
    return stepup.Bound(stepup.digest("restore", path, sha),
                        f"ecf: restore this install from {path} (SHA-256 {sha[:12]})")  # fmt: skip


def restore(  # noqa: PLR0913 - the service state it needs, then the request
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    store: SecretStore,
    data_dir: Path,
    parsed: Parsed,
    path: str,
    key_text: str,
    *,
    stopped: bool,
    older_ok: bool,
    nonce: str | None,
) -> dict[str, Any]:
    preview = check(conn, parsed)
    if not stopped:
        raise InvalidInputError("confirm the old computer's ecf service is stopped first")
    if preview["older"] and not older_ok:
        raise ConflictError(f"that bundle (seq {preview['seq']}) is older than this install's"
                            f" newest backup (seq {preview['newest_seq_here']}); confirm to go"
                            " back to it")  # fmt: skip
    sha = parsed.opened.sha256
    stepup.consume(conn, clock, "restore", {"path": path, "sha256": sha}, nonce)
    started = time.monotonic()
    safety = None if preview["empty_target"] else _safety_copy(conn, clock, data_dir)
    derived = backup_key.derive(backup_key.parse_key_text(key_text))
    with tempfile.NamedTemporaryFile(dir=data_dir, prefix=".restore-", suffix=".db") as tmp:
        staged = db.connect(Path(tmp.name))
        try:
            try:
                importer.load(staged, parsed)
                with write_tx(staged):
                    staged.execute("UPDATE addresses SET paused = 1")
                importer.validate(staged, importer.kept_settings(staged))
            except sqlite3.Error as exc:
                raise BadBundleError(f"the bundle's rows don't load ({exc})") from None
            counts = _apply(conn, clock, tmp.name, derived, empty=preview["empty_target"])
        finally:
            staged.close()
    store.set(export_keys.SEED_NAME, derived.signing_seed.hex())
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute(
            "INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?, 'restore.completed',"
            " 'os_user', 'ok', ?)",
            (now, json.dumps({
                "person": stepup.person(), "sha256": sha, "kind": parsed.manifest.get("kind"),
                "exported_at": parsed.manifest.get("created_at"), "seq": preview["seq"],
                "generation": install_identity.generation(conn), "counts": counts,
                "safety_copy": safety is not None, "older": preview["older"],
                "duration_s": round(time.monotonic() - started, 1)})),
        )  # fmt: skip
    export_keys.notice(conn, clock, notifier,
                       f"This install was restored from a backup made"
                       f" {str(parsed.manifest.get('created_at'))[:16].replace('T', ' ')} UTC"
                       f" (generation now {install_identity.generation(conn)}). Addresses are"
                       " paused until a mail check passes and you resume them. If this wasn't"
                       " you, check the computer ecf runs on.")  # fmt: skip
    return {"counts": counts, "generation": install_identity.generation(conn),
            "safety_copy": safety, "addresses": [a["address_id"] for a in preview["addresses"]],
            "config_changes": preview["config_changes"]}  # fmt: skip


def resume_blocked(conn: sqlite3.Connection, address_id: str) -> str | None:
    """Why `ecf resume` must wait (OD-366): restored, and no mail check has passed since."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (AWAITING_KEY,)).fetchone()
    if row is None or address_id not in json.loads(row[0]):
        return None
    at = conn.execute("SELECT value FROM settings WHERE key = ?", (AT_KEY,)).fetchone()
    c = conn.execute("SELECT last_finished_at, last_status FROM check_state WHERE address_id ="
                     " ?", (address_id,)).fetchone()  # fmt: skip
    if (at is not None and c is not None and c["last_finished_at"]
            and from_ts(c["last_finished_at"]) >= from_ts(json.loads(at[0]))
            and c["last_status"] not in CHECK_FAILED):  # fmt: skip
        return None
    return (f"{address_id} was restored and no mail check has passed since: set its app password"
            " (ecf address set --app-password), run ecf check, then resume")  # fmt: skip


def resumed(conn: sqlite3.Connection, address_id: str) -> None:
    """Drop the address from the restore hold (the caller holds the write transaction)."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (AWAITING_KEY,)).fetchone()
    if row is None:
        return
    left = [a for a in json.loads(row[0]) if a != address_id]
    conn.execute("UPDATE settings SET value = ? WHERE key = ?", (json.dumps(left), AWAITING_KEY))


# ---- helpers ------------------------------------------------------------------------------------


def _apply(conn: sqlite3.Connection, clock: Clock, staged_path: str,
           derived: backup_key.Derived, *, empty: bool) -> dict[str, int]:  # fmt: skip
    gen_here = install_identity.generation(conn)
    seq_here = scheduled_export.status_seq(conn)
    conn.execute("ATTACH DATABASE ? AS imp", (staged_path,))
    try:
        with write_tx(conn), db.items_writer("create"):
            old = [r[0] for r in conn.execute("SELECT address_id FROM main.addresses")]
            for table in importer.CLEAR:
                conn.execute(f'DELETE FROM main."{table}"')  # noqa: S608 - fixed names
            conn.execute("DELETE FROM main.jobs WHERE address_id IN (SELECT value FROM"
                         " json_each(?))", (json.dumps(old),))  # fmt: skip
            conn.execute("DELETE FROM main.slack_messages WHERE key LIKE 'item:%'")
            if empty:  # a new computer: its few rows give way to the install's own history
                conn.execute("DELETE FROM main.audit")
            counts: dict[str, int] = {}
            for table in COPIED:
                if table == "audit" and not empty:
                    counts[table] = _merge_audit(conn)
                else:
                    counts[table] = importer.copy(conn, table, keep_ids=True)
            _settings(conn, clock, derived, gen_here=gen_here, seq_here=seq_here)
        rows = conn.execute("SELECT stable_id FROM main.items WHERE status ="
                            " 'awaiting_approval' ORDER BY stable_id")  # fmt: skip
        reposted = [r[0] for r in rows]
    finally:
        conn.execute("DETACH DATABASE imp")
    for sid in reposted:
        approvals.reissue_after_import(conn, clock, sid)
    counts["reposted"] = len(reposted)
    return counts


def _merge_audit(conn: sqlite3.Connection) -> int:
    """Same install: the bundle's audit rows are this install's own; keep every row here and
    add the bundle's that are missing (same row ID, same event)."""
    cols = [r[1] for r in conn.execute('PRAGMA imp.table_info("audit")')]
    names = ", ".join(f"[{c}]" for c in cols)
    cur = conn.execute(f"INSERT OR IGNORE INTO main.audit ({names}) SELECT {names} FROM"  # noqa: S608
                       " imp.audit ORDER BY id")  # fmt: skip
    return cur.rowcount


def _settings(conn: sqlite3.Connection, clock: Clock, derived: backup_key.Derived, *,
              gen_here: int, seq_here: int) -> None:  # fmt: skip
    local = sorted(MACHINE_LOCAL)
    conn.execute("DELETE FROM main.settings WHERE key NOT IN (SELECT value FROM json_each(?))",
                 (json.dumps(local),))  # fmt: skip
    conn.execute("INSERT OR IGNORE INTO main.settings (key, value, updated_at, updated_by)"
                 " SELECT key, value, updated_at, updated_by FROM imp.settings WHERE key NOT IN"
                 " (SELECT value FROM json_each(?))", (json.dumps(local),))  # fmt: skip
    now = to_ts(clock.now())
    gen_bundle = install_identity.generation(conn)
    restored = [r[0] for r in conn.execute("SELECT address_id FROM main.addresses WHERE"
                                           " removed_at IS NULL")]  # fmt: skip
    seq_bundle = scheduled_export.status_seq(conn)
    key = export_keys.current(conn)
    values: dict[str, Any] = {
        install_identity.GENERATION_KEY: max(gen_here, gen_bundle) + 1,
        scheduled_export.SEQ: max(seq_here, seq_bundle),
        AT_KEY: now,
        AWAITING_KEY: restored,
    }
    if key is None or key.get("verify_key") != derived.public.verify_key:
        # the bundle names an earlier key (or none): the typed key is the one in use now
        prev = export_keys.previous(conn) + ([key] if key else [])
        values[export_keys.PREVIOUS_KEY] = prev
        values[export_keys.KEY_KEY] = {
            "generation": (int(key["generation"]) + 1) if key else 1,
            "recipient": derived.public.recipient, "verify_key": derived.public.verify_key,
            "fingerprint": derived.public.fingerprint, "created_at": now}  # fmt: skip
    for k, v in values.items():
        conn.execute("INSERT INTO main.settings (key, value, updated_at, updated_by) VALUES"
                     " (?, ?, ?, 'restore') ON CONFLICT (key) DO UPDATE SET value ="
                     " excluded.value, updated_at = excluded.updated_at, updated_by ="
                     " excluded.updated_by", (k, json.dumps(v), now))  # fmt: skip


def _safety_copy(conn: sqlite3.Connection, clock: Clock, data_dir: Path) -> str:
    folder = data_dir / "restores"
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    cutoff = clock.now() - SAFETY_KEEP
    for old in folder.glob("before-restore-*.db"):
        if old.stat().st_mtime < cutoff.timestamp():
            old.unlink(missing_ok=True)
    stamp = clock.now().strftime("%Y%m%dT%H%M%SZ")
    target = folder / f"before-restore-{stamp}.db"
    dst = sqlite3.connect(target)
    try:
        conn.backup(dst)
    finally:
        dst.close()
    target.chmod(0o600)
    return str(target)


def _sender_diff(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> dict[str, int]:
    """Counts only (OD-373): sender records added, removed or changed by the restore."""
    fields = ("confirmed_category", "expected_reply_to_domain", "verified_rule1a")
    here = {(r["address_id"], r["sender_hash"]): tuple(r[f] for f in fields)
            for r in conn.execute("SELECT * FROM senders")}  # fmt: skip
    there = {(r.get("address_id"), r.get("sender_hash")): tuple(r.get(f) for f in fields)
             for r in rows}  # fmt: skip
    both = there.keys() & here.keys()
    return {"added": len(there.keys() - here.keys()), "removed": len(here.keys() - there.keys()),
            "changed": sum(1 for k in both if there[k] != here[k])}  # fmt: skip
