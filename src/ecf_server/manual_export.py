"""Manual `ecf export --to <file>` (SPEC §11.9; OD-120, OD-326, OD-349 to OD-351; V1.5 step 9a).

The same tables and file layout as a scheduled export (export_bundle.py), with kind `manual`,
encrypted to a passphrase (age's scrypt mode) instead of the backup key, so it can be read
without the key, for example by `ecf import` on another computer. It is signed with the install's
signing key when there is one, else unsigned (`signed: false`); import treats unsigned and foreign
bundles alike. Step-up is bound to the exact path. The service writes the file: an absolute path
ending in `.ecfb`, in an existing folder outside the data directory, never over an existing file,
0600. The passphrase reaches the service over the 0600 socket and is never stored or logged.
Manual bundles share `export.seq` with scheduled ones and are never pruned. Audited as
`export.completed` (kind `manual`, without the path) and announced as a Security Notice, since it
takes all of ecf's data off the computer's control.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from ecf.errors import ConflictError, InvalidInputError
from ecf_server import _age, export_bundle, export_keys, passphrase, scheduled_export, stepup
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier
from ecf_server.secretstore import SecretStore


def check_path(path: str, data_dir: Path) -> Path:
    if not path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise InvalidInputError("a file path")
    p = Path(path)
    if not p.is_absolute():
        raise InvalidInputError("give the full path of the file")
    if p.suffix != export_bundle.SUFFIX:
        raise InvalidInputError(f"the file name must end in {export_bundle.SUFFIX}")
    folder = p.parent.resolve()
    if not folder.is_dir():
        raise InvalidInputError(f"{folder} isn't an existing folder; create it first")
    data = data_dir.resolve()
    if folder == data or folder.is_relative_to(data):
        raise InvalidInputError("an export can't go inside ecf's own data directory")
    where = folder / p.name
    if where.exists() or where.is_symlink():
        raise ConflictError(f"{where} already exists; choose another name")
    return where


@stepup.purpose("export_manual")
def _describe(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    del conn
    path = str(target.get("path", ""))
    text = f"ecf: export all of ecf's data to {path} (passphrase-protected)"
    return stepup.Bound(stepup.digest("export_manual", path), text)


def export(  # noqa: PLR0913 - the service state it needs, then the request
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    store: SecretStore | None,
    data_dir: Path,
    install: str,
    path: str,
    secret: str,
    *,
    nonce: str | None,
) -> dict[str, Any]:
    where = check_path(path, data_dir)
    passphrase.check(secret)
    stepup.consume(conn, clock, "export_manual", {"path": str(where)}, nonce)
    started = time.monotonic()
    key = export_keys.current(conn)
    try:
        seed = None if key is None else scheduled_export.signing_seed(key, store)
    except scheduled_export.ExportFailedError as exc:
        raise ConflictError(f"can't sign the export: {exc}") from None
    seq = scheduled_export.next_seq(conn, clock)
    created = to_ts(clock.now())
    c = export_bundle.contents(conn, data_dir, install=install, kind="manual", created_at=created,
                               seq=seq)  # fmt: skip
    header = dict(c.header)
    if key is not None:
        header |= {"key_generation": key["generation"], "key_fingerprint": key["fingerprint"]}
    data = export_bundle.seal(_age.encrypt_passphrase(c.plaintext, secret), header, seed)
    final = export_bundle.write_atomic(where.parent, where.name, data)
    sha = hashlib.sha256(data).hexdigest()
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute(
            "INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?, 'export.completed',"
            " 'os_user', 'ok', ?)",
            (now, json.dumps({"kind": "manual", "person": stepup.person(), "seq": seq,
                              "bytes": len(data), "sha256": sha, "counts": c.counts,
                              "data_format": export_bundle.DATA_FORMAT, "signed": seed is not None,
                              "duration_s": round(time.monotonic() - started, 1)})),
        )  # fmt: skip
    export_keys.notice(conn, clock, notifier,
                       f"A manual export of all ecf data was written to {final}"
                       " (passphrase-protected). If this wasn't you, check the computer ecf runs"
                       " on.")  # fmt: skip
    return {"path": str(final), "seq": seq, "bytes": len(data), "sha256": sha,
            "signed": seed is not None}  # fmt: skip
