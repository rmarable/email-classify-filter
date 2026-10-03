"""The backup key and `export_dir` (SPEC §11.9, §9.6; OD-327, OD-339 to OD-342; V1.5 step 8a).

**Key** (`ecf export keys rotate`, which also makes the first one): the service makes a new root
(backup_key.py) and answers with its text once; the text is never stored or logged. The new key
waits here, in memory, for `PENDING_FOR` while you save the text and type its fingerprint; then a
step-up whose dialog names the old and new fingerprints commits it: the signing seed goes to the
secret store, the public halves to settings (`export.key`). A rotation keeps each earlier
generation's public halves (`export.keys_previous`), so pruning still recognizes the bundles ecf
wrote; restoring those needs the old backup key (OD-342).

**`export_dir`** (`ecf export dir set <path>`): an existing, writable directory outside the data
directory; needs a key first, the key's fingerprint typed, and step-up. `same_volume` is true when
it shares a device with the data directory, except under iCloud Drive or a File Provider folder
(`~/Library/Mobile Documents`, `~/Library/CloudStorage`), whose files leave the computer (OD-344).

Both changes are audited and sent as a Security Notice (Slack, and email when it's on).
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ecf.errors import ConflictError, InvalidInputError, NotFoundError
from ecf_server import backup_key, slack_admin, stepup
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier
from ecf_server.secretstore import SecretStore

KEY_KEY = "export.key"  # {generation, recipient, verify_key, fingerprint, created_at}
PREVIOUS_KEY = "export.keys_previous"  # earlier generations' entries, newest last
DIR_KEY = "export_dir"
SEED_NAME = "export-signing-seed"
PENDING_FOR = timedelta(minutes=10)
SHOWN = ("generation", "fingerprint", "created_at")
CLOUD_DIRS = ("Library/Mobile Documents", "Library/CloudStorage")  # relative to the home folder


@dataclass(frozen=True)
class _Pending:
    derived: backup_key.Derived
    expires_at: datetime


_pending: dict[str, _Pending] = {}
_lock = threading.Lock()


def current(conn: sqlite3.Connection) -> dict[str, Any] | None:
    return _setting(conn, KEY_KEY)


def previous(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return list(_setting(conn, PREVIOUS_KEY) or [])


def export_dir(conn: sqlite3.Connection) -> str | None:
    return _setting(conn, DIR_KEY)


def show(conn: sqlite3.Connection, data_dir: Path) -> dict[str, Any]:
    key = current(conn)
    where = export_dir(conn)
    return {
        "key": None if key is None else {k: key[k] for k in SHOWN},
        "previous": len(_setting(conn, PREVIOUS_KEY) or []),
        "dir": where,
        "same_volume": None if where is None else same_volume_or_none(Path(where), data_dir),
    }


# ---- the key ------------------------------------------------------------------------------------


def new_key(conn: sqlite3.Connection, clock: Clock) -> dict[str, Any]:
    """Make a key and hold it until `rotate` (the caller first checks there's a secret store)."""
    root = backup_key.new_root()
    derived = backup_key.derive(root)
    now = clock.now()
    pid = secrets.token_hex(16)
    with _lock:
        for k in [k for k, p in _pending.items() if p.expires_at <= now]:
            del _pending[k]
        _pending[pid] = _Pending(derived, now + PENDING_FOR)
    old = current(conn)
    return {
        "pending_id": pid,
        "key_text": backup_key.key_text(root),
        "fingerprint": derived.public.fingerprint,
        "replaces": None if old is None else old["fingerprint"],
        "expires_at": to_ts(now + PENDING_FOR),
    }


def _pending_key(clock: Clock, pending_id: str) -> backup_key.Derived:
    with _lock:
        p = _pending.get(pending_id)
    if p is None or p.expires_at <= clock.now():
        raise NotFoundError("that new key has expired or the service restarted: run `ecf export"
                            " keys rotate` again and discard the key it showed")  # fmt: skip
    return p.derived


@stepup.purpose("export_key")
def _describe_key(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    with _lock:
        p = _pending.get(str(target.get("pending_id", "")))
    if p is None:
        raise NotFoundError("that new key has expired; run `ecf export keys rotate` again")
    new = p.derived.public.fingerprint
    old = current(conn)
    if old is None:
        return stepup.Bound(stepup.digest("export_key", new, None),
                            f"ecf: create the backup key {new}")  # fmt: skip
    return stepup.Bound(stepup.digest("export_key", new, old["fingerprint"]),
                        f"ecf: replace backup key {old['fingerprint']} with {new}")  # fmt: skip


def rotate(
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    store: SecretStore,
    data_dir: Path,
    pending_id: str,
    typed: str,
    *,
    nonce: str | None,
) -> dict[str, Any]:
    derived = _pending_key(clock, pending_id)
    if not backup_key.same_fingerprint(typed, derived.public.fingerprint):
        raise InvalidInputError("that isn't the new key's fingerprint")
    stepup.consume(conn, clock, "export_key", {"pending_id": pending_id}, nonce)
    old = current(conn)
    store.set(SEED_NAME, derived.signing_seed.hex())  # a locked store leaves the key pending
    now = to_ts(clock.now())
    entry = {"generation": 1 if old is None else int(old["generation"]) + 1,
             "recipient": derived.public.recipient, "verify_key": derived.public.verify_key,
             "fingerprint": derived.public.fingerprint, "created_at": now}  # fmt: skip
    with write_tx(conn):
        if old is not None:
            _put(conn, PREVIOUS_KEY, [*(_setting(conn, PREVIOUS_KEY) or []), old], now)
        _put(conn, KEY_KEY, entry, now)
        _audit(conn, now, "export.key_rotated", {
            "generation": entry["generation"], "fingerprint": entry["fingerprint"],
            "previous": None if old is None else old["fingerprint"]})  # fmt: skip
    with _lock:
        _pending.pop(pending_id, None)
    if old is None:
        text = f"A backup key was created: fingerprint {entry['fingerprint']}."
    else:
        text = (f"The backup key was replaced: {old['fingerprint']} → {entry['fingerprint']}."
                " Bundles made before this need the old key to restore.")  # fmt: skip
    notice(conn, clock, notifier, f"{text} If this wasn't you, check the computer ecf runs on.")
    return show(conn, data_dir)


# ---- export_dir ---------------------------------------------------------------------------------


@stepup.purpose("export_dir")
def _describe_dir(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    path = str(target.get("path", ""))
    key = _need_key(conn)
    fp = key["fingerprint"]
    return stepup.Bound(stepup.digest("export_dir", path, export_dir(conn), fp),
                        f"ecf: write backups to {path} (backup key {fp})")  # fmt: skip


def set_dir(
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    data_dir: Path,
    path: str,
    typed: str,
    *,
    nonce: str | None,
) -> dict[str, Any]:
    key = _need_key(conn)
    where = check_dir(path, data_dir)
    if not backup_key.same_fingerprint(typed, key["fingerprint"]):
        raise InvalidInputError("that isn't the backup key's fingerprint (ecf export keys show)")
    stepup.consume(conn, clock, "export_dir", {"path": str(where)}, nonce)
    old = export_dir(conn)
    now = to_ts(clock.now())
    same = same_volume(where, data_dir)
    with write_tx(conn):
        _put(conn, DIR_KEY, str(where), now)
        _audit(conn, now, "export.dir_set", {"was": old, "same_volume": same})
    text = f"Backups now go to {where}" + (f" (was {old})." if old else ".")
    if same:
        text += " It's on the same disk as ecf's data, so it won't survive that disk failing."
    notice(conn, clock, notifier, f"{text} If this wasn't you, check the computer ecf runs on.")
    return show(conn, data_dir)


def check_dir(path: str, data_dir: Path) -> Path:
    """An absolute path to an existing directory ecf can write to, outside its data directory."""
    if not path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise InvalidInputError("a directory path")
    p = Path(path)
    if not p.is_absolute():
        raise InvalidInputError("give the full path of the directory")
    where = p.resolve()
    if not where.is_dir():
        raise InvalidInputError(f"{where} isn't an existing directory; create it first")
    data = data_dir.resolve()
    if where == data or where.is_relative_to(data):
        raise InvalidInputError("backups can't go inside ecf's own data directory")
    try:
        with tempfile.NamedTemporaryFile(dir=where, prefix=".ecf-write-test-"):
            pass
    except OSError as exc:
        raise InvalidInputError(f"ecf can't write to {where}: {exc.strerror or exc}") from None
    return where


def same_volume(path: Path, data_dir: Path, home: Path | None = None) -> bool:
    """On the data directory's disk, unless it's a folder whose files a cloud service copies off
    the computer (OD-344)."""
    base = (home or Path.home()).resolve()
    where = path.resolve()
    if any(where.is_relative_to(base / d) for d in CLOUD_DIRS):
        return False
    return os.stat(where).st_dev == os.stat(data_dir).st_dev


def same_volume_or_none(path: Path, data_dir: Path) -> bool | None:
    try:
        return same_volume(path, data_dir)
    except OSError:  # gone or unreadable; the export says so when it runs
        return None


# ---- helpers ------------------------------------------------------------------------------------


def _need_key(conn: sqlite3.Connection) -> dict[str, Any]:
    key = current(conn)
    if key is None:
        raise ConflictError("make the backup key first: ecf export keys rotate")
    return key


def _setting(conn: sqlite3.Connection, key: str) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else json.loads(row[0])


def _put(conn: sqlite3.Connection, key: str, value: Any, now: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'os_user')"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (key, json.dumps(value), now),
    )


def _audit(conn: sqlite3.Connection, now: str, event: str, data: dict[str, Any]) -> None:
    conn.execute("INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?, ?, 'os_user',"
                 " 'ok', ?)", (now, event, json.dumps(data)))  # fmt: skip


def notice(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, text: str) -> None:
    ident = slack_admin.identity(conn)
    slack_admin.notice(conn, clock, notifier, text,
                       dms=[ident.member] if ident and ident.member else [])  # fmt: skip
