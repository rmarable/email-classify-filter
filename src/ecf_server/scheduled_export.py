"""Scheduled export (SPEC §11.9; OD-102, OD-343, OD-345, OD-347; V1.5 step 8b).

**When** (OD-343): `export_schedule` (`ecf config apply`; `daily` by default, `weekly`, `off`) is
due once the last good export is a day (a week) old. The timer checks each tick and runs the export
in its own thread, on battery too. Without a backup key or `export_dir` nothing runs and nothing
counts as failed; the daily summary says backups aren't set up. A failure is retried after an
hour; after 2 in a row a System Error opens (`export_failed`), and the next success resolves it.
`ecf export now` runs one at once, as the schedule would (no step-up, like the schedule).

**What**: export_bundle.py builds the bundle from a backup-API snapshot; it is signed with the
seed from the secret store (checked against the key in settings) and encrypted to the stored age
recipient, so this install can't read it. It is written to a 0600 temp file inside `export_dir`,
synced, then renamed. **Pruning** keeps the newest `export_keep` (14) scheduled bundles: only files
whose signature verifies with this install's current or an earlier key and whose signed header
names this install, so ecf never deletes a file it didn't write. Manual bundles (step 9) are never
pruned.

**Reporting** (OD-347): success is a daily-summary line only; failures raise the alert (desktop,
Slack, and email when routed). Audit: `export.completed` (seq, size, SHA-256, counts, duration,
key generation) and `export.failed` (the reason).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import structlog
from nacl.signing import VerifyKey

from ecf.errors import ConflictError, EcfError
from ecf_server import _age, backup_key, export_bundle, export_keys, health, install_identity
from ecf_server import settings as settings_mod
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier
from ecf_server.secretstore import SecretStore
from ecf_server.stepup import person

log = structlog.get_logger("ecf.export")

SCHEDULE_KEY = "export_schedule"  # a config section (config.py)
DEFAULT_SCHEDULE = "daily"
PERIOD = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}
RETRY = timedelta(hours=1)
ALERT_AFTER = 2
FAIL_ALERT = "export_failed"
LAST_OK, FAILURES, LAST_ERROR = "export.last_ok", "export.failures", "export.last_error"
NEXT_TRY, SEQ = "export.next_try_at", "export.seq"

Spawn = Callable[[Callable[[], None]], None]


class ExportFailedError(Exception):
    """Why an export didn't happen, in words safe for a notification (no email content)."""


# ---- settings -----------------------------------------------------------------------------------


def schedule(conn: sqlite3.Connection) -> str:
    return str(_get(conn, SCHEDULE_KEY) or DEFAULT_SCHEDULE)


def set_up(conn: sqlite3.Connection) -> bool:
    return export_keys.current(conn) is not None and export_keys.export_dir(conn) is not None


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "schedule": schedule(conn),
        "keep": settings_mod.get(conn, "export_keep"),
        "last_ok": _get(conn, LAST_OK),
        "failures": int(_get(conn, FAILURES) or 0),
        "last_error": _get(conn, LAST_ERROR),
        "set_up": set_up(conn),
    }


def due(conn: sqlite3.Connection, clock: Clock) -> bool:
    sched = schedule(conn)
    if sched == "off" or not set_up(conn):
        return False
    now = clock.now()
    nxt = _get(conn, NEXT_TRY)
    if nxt is not None and from_ts(str(nxt)) > now:
        return False
    last = _get(conn, LAST_OK)
    return last is None or now - from_ts(last["at"]) >= PERIOD[sched]


# ---- running ------------------------------------------------------------------------------------

_RUNNING = threading.Lock()


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="ecf-export", daemon=True).start()


def start(connect: Callable[[], sqlite3.Connection], clock: Clock, notifier: Notifier,
          store: SecretStore | None, data_dir: Path, install: str, *,
          spawn: Spawn = _thread) -> bool:  # fmt: skip
    """Run one export in its own thread; False when one is already running."""
    if not _RUNNING.acquire(blocking=False):
        return False

    def work() -> None:
        try:
            conn = connect()
            try:
                run(conn, clock, notifier, store, data_dir, install)
            finally:
                conn.close()
        except Exception as exc:  # run records its own failures; anything else is logged
            log.error("export.thread_failed", error_type=type(exc).__name__)
        finally:
            _RUNNING.release()

    spawn(work)
    return True


def now(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, store: SecretStore | None,
        data_dir: Path, install: str) -> dict[str, Any]:  # fmt: skip
    """`ecf export now`: one export at once, in the caller's thread."""
    if not set_up(conn):
        raise ConflictError("backups aren't set up: ecf export keys rotate, then ecf export dir"
                            " set <directory>")  # fmt: skip
    if not _RUNNING.acquire(blocking=False):
        raise ConflictError("an export is running now; try again in a minute")
    try:
        return run(conn, clock, notifier, store, data_dir, install)
    finally:
        _RUNNING.release()


def run(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, store: SecretStore | None,
        data_dir: Path, install: str) -> dict[str, Any]:  # fmt: skip
    """One scheduled export, recorded either way; returns what happened."""
    started = time.monotonic()
    try:
        done = write(conn, clock, store, data_dir, install)
    except (ExportFailedError, EcfError, OSError, export_bundle.BundleError, _age.AgeError) as exc:
        why = _why(exc)
        _failed(conn, clock, notifier, why)
        return {"ok": False, "error": why}
    done["pruned"] = prune(
        conn, Path(done["dir"]), install, int(settings_mod.get(conn, "export_keep"))
    )
    _succeeded(conn, clock, notifier, done, time.monotonic() - started)
    return {"ok": True} | done


def write(conn: sqlite3.Connection, clock: Clock, store: SecretStore | None, data_dir: Path,
          install: str) -> dict[str, Any]:  # fmt: skip
    key = export_keys.current(conn)
    where_s = export_keys.export_dir(conn)
    if key is None or where_s is None:
        raise ExportFailedError("backups aren't set up")
    seed = signing_seed(key, store)
    where = export_keys.check_dir(where_s, data_dir)
    _clear_partials(where, install)
    seq = next_seq(conn, clock)
    created = to_ts(clock.now())
    c = export_bundle.contents(conn, data_dir, install=install, kind="scheduled",
                               created_at=created, seq=seq)  # fmt: skip
    header = c.header | {"key_generation": key["generation"],
                         "key_fingerprint": key["fingerprint"]}  # fmt: skip
    data = export_bundle.seal(_age.encrypt(c.plaintext, key["recipient"]), header, seed)
    name = export_bundle.file_name(install, created, seq)
    export_bundle.write_atomic(where, name, data)
    return {"dir": str(where), "file": name, "seq": seq, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "at": created, "counts": c.counts,
            "key_generation": key["generation"]}  # fmt: skip


def status_seq(conn: sqlite3.Connection) -> int:
    """The newest `export.seq` this install has used (0 before any export)."""
    return int(_get(conn, SEQ) or 0)


def next_seq(conn: sqlite3.Connection, clock: Clock) -> int:
    """`export.seq`, shared by scheduled and manual bundles; restore warns on an older one."""
    with write_tx(conn):
        seq = int(_get(conn, SEQ) or 0) + 1
        _put(conn, SEQ, seq, to_ts(clock.now()))
    return seq


def prune(conn: sqlite3.Connection, where: Path, install: str, keep: int) -> list[str]:
    """Delete scheduled bundles past the newest `keep`, only ones this install signed."""
    keys = _verify_keys(conn)
    me = install_identity.install_id(conn)
    found: list[tuple[str, int, Path]] = []
    for p in where.glob(f"ecf-{install}-*{export_bundle.SUFFIX}"):
        if p.is_symlink() or not p.is_file():
            continue
        try:
            header, _ = export_bundle.verify(p.read_bytes(), keys)
        except (OSError, export_bundle.BundleError):
            continue  # not ours, or unreadable: never deleted
        if header.get("install_id") != me or header.get("kind") != "scheduled":
            continue
        found.append((str(header.get("created_at")), int(header.get("seq") or 0), p))
    found.sort(reverse=True)
    gone: list[str] = []
    for _at, _seq, p in found[keep:]:
        try:
            p.unlink()
            gone.append(p.name)
        except OSError as exc:
            log.warning("export.prune_failed", error_type=type(exc).__name__)
    return gone


def daily_line(conn: sqlite3.Connection) -> str:
    """The daily summary's backup line (OD-347)."""
    sched = schedule(conn)
    if sched == "off":
        return "Backups: off (export_schedule: off)."
    if not set_up(conn):
        return ("Backups: not set up (ecf export keys rotate, then ecf export dir set"
                " <directory>).")  # fmt: skip
    last = _get(conn, LAST_OK)
    failures = int(_get(conn, FAILURES) or 0)
    text = ("Backups: none yet." if last is None else
            f"Last backup {last['at'][:16].replace('T', ' ')} UTC to {last['dir']}.")  # fmt: skip
    if failures:
        err: dict[str, Any] = _get(conn, LAST_ERROR) or {}
        text += f" The last {failures} tr{'ies' if failures > 1 else 'y'} failed: {err.get('why')}."
    return text


# ---- helpers ------------------------------------------------------------------------------------


def signing_seed(key: dict[str, Any], store: SecretStore | None) -> bytes:
    if store is None:
        raise ExportFailedError("no secret store to read the signing key from")
    raw = store.get(export_keys.SEED_NAME)  # a locked store raises SecretStoreNeedsYouError
    if raw is None:
        raise ExportFailedError("the signing key is missing from the secret store; make a new key"
                                " with ecf export keys rotate")  # fmt: skip
    seed = bytes.fromhex(raw)
    if backup_key.public_from_seed(seed, key["recipient"]).verify_key != key["verify_key"]:
        raise ExportFailedError("the signing key in the secret store doesn't match the backup key;"
                                " make a new key with ecf export keys rotate")  # fmt: skip
    return seed


def _verify_keys(conn: sqlite3.Connection) -> list[VerifyKey]:
    entries = [*(export_keys.previous(conn)), export_keys.current(conn)]
    return [VerifyKey(bytes.fromhex(e["verify_key"])) for e in entries if e is not None]


def _clear_partials(where: Path, install: str) -> None:
    """A crash mid-write leaves a temp file; only one export runs at a time, so any left is old."""
    for p in where.glob(f".ecf-{install}-*{export_bundle.SUFFIX}.partial"):
        p.unlink(missing_ok=True)


def _why(exc: BaseException) -> str:
    if isinstance(exc, EcfError):
        return exc.detail
    if isinstance(exc, OSError):
        return f"couldn't write the backup: {exc.strerror or type(exc).__name__}"
    return str(exc) or type(exc).__name__


def _succeeded(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, done: dict[str, Any],
               seconds: float) -> None:  # fmt: skip
    now = to_ts(clock.now())
    with write_tx(conn):
        _put(conn, LAST_OK, {k: done[k] for k in ("at", "dir", "file", "seq", "bytes")}, now)
        _put(conn, FAILURES, 0, now)
        conn.execute("DELETE FROM settings WHERE key IN (?, ?)", (NEXT_TRY, LAST_ERROR))
        _audit(conn, now, "export.completed", "ok", {
            "kind": "scheduled", "person": person(), "seq": done["seq"], "bytes": done["bytes"],
            "sha256": done["sha256"], "counts": done["counts"],
            "data_format": export_bundle.DATA_FORMAT, "key_generation": done["key_generation"],
            "pruned": len(done["pruned"]), "duration_s": round(seconds, 1)})  # fmt: skip
    health.resolve_alert(conn, clock, notifier, FAIL_ALERT, None)


def _failed(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, why: str) -> None:
    now = clock.now()
    with write_tx(conn):
        failures = int(_get(conn, FAILURES) or 0) + 1
        ts = to_ts(now)
        _put(conn, FAILURES, failures, ts)
        _put(conn, LAST_ERROR, {"at": ts, "why": why}, ts)
        _put(conn, NEXT_TRY, to_ts(now + RETRY), ts)
        _audit(conn, ts, "export.failed", "error", {"kind": "scheduled", "why": why,
                                                    "failures": failures})  # fmt: skip
    log.warning("export.failed", failures=failures)
    if failures >= ALERT_AFTER:
        last = _get(conn, LAST_OK)
        since = "never" if last is None else last["at"][:16].replace("T", " ") + " UTC"
        health.open_alert(conn, clock, notifier, FAIL_ALERT, None,
                          f"Backups are failing ({failures} tries): {why}. Last good backup:"
                          f" {since}. Details: ecf export status")  # fmt: skip


def _get(conn: sqlite3.Connection, key: str) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else json.loads(row[0])


def _put(conn: sqlite3.Connection, key: str, value: Any, now: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'service')"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (key, json.dumps(value), now),
    )


def _audit(conn: sqlite3.Connection, now: str, event: str, outcome: str,
           data: dict[str, Any]) -> None:  # fmt: skip
    conn.execute("INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?, ?, 'service', ?,"
                 " ?)", (now, event, outcome, json.dumps(data)))  # fmt: skip
