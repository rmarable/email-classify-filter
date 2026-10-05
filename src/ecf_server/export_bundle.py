"""Export bundles (SPEC §11.9; OD-315, OD-345, OD-346; V1.5 step 8b): what goes in, and the file.

**Contents** (OD-346): a consistent snapshot through SQLite's backup API, read table by table into
JSON lines. Only the tables in `INCLUDED` are exported, `settings` without the interpreter hash;
`EXCLUDED` names every other table, so a new table fails a test until it is put in one of them.
The raw database file and the data directory's other files (secrets, tokens, logs, Claude's
config and transcripts, crash state) are never exported.

**File** (OD-345): `MAGIC`, a 4-byte big-endian header length, the header (canonical JSON), the
age ciphertext, and a 64-byte Ed25519 signature over everything before it. The header (format,
install ID and generation, kind, created_at, seq, data format, schema version, key generation and
fingerprint) is readable without the key and is verified before anything is decrypted. Inside the
encryption is a tar.gz: `manifest.json` (install ID, mode, versions, creator, counts, each file's
SHA-256) and `tables/<name>.jsonl`. Encryption works on whole buffers (pyrage), so building or
reading a bundle needs memory a few times its size (a stated limit, §12.2).
"""

from __future__ import annotations

import errno
import gzip
import hashlib
import io
import json
import os
import sqlite3
import struct
import tarfile
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from ecf import __version__
from ecf_server import install_identity
from ecf_server.secretstore.select import INTERPRETER_KEY
from ecf_server.stepup import person

MAGIC = b"ECFB\x01"
FORMAT = 1
# the tables' JSON-lines layout; import accepts this and the one before (§11.9). 2 (V1.6): the
# settings carry `config.org_addresses`, which a version on 1 would drop silently (OD-442)
DATA_FORMAT = 2
SIG_BYTES = 64
MAX_HEADER = 16 * 1024
SUFFIX = ".ecfb"

INCLUDED = (
    "addresses", "alerts", "audit", "claude_calls", "claude_sessions", "cursors",
    "escalations", "eval_results", "eval_runs", "excerpts", "gate", "items", "model_calls",
    "probe", "routes", "schema_migrations", "senders", "sent", "settings", "threads",
)  # fmt: skip
EXCLUDED = (
    "alert_outbox", "check_state", "claim_batches", "claims", "delays", "dns_cache", "downloads",
    "fallback_shadow", "grants", "heartbeats", "jobs", "leases", "nonces", "processing", "rate",
    "slack_dedupe", "slack_messages",
)  # fmt: skip
SETTINGS_LEFT_OUT = (INTERPRETER_KEY,)  # this computer's interpreter; restore resets it


class BundleError(Exception):
    """A bundle that can't be trusted or read; the text never includes its contents."""


# ---- contents -----------------------------------------------------------------------------------


def snapshot_tables(conn: sqlite3.Connection, scratch_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """The allowed tables from a backup-API copy (one consistent moment), made in a 0600 temp file
    inside the data directory and deleted at once."""
    with tempfile.NamedTemporaryFile(dir=scratch_dir, prefix=".export-", suffix=".db") as tmp:
        dst = sqlite3.connect(tmp.name)
        try:
            conn.backup(dst)
            dst.row_factory = sqlite3.Row
            return {t: _rows(dst, t) for t in INCLUDED}
        finally:
            dst.close()


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = [dict(r) for r in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]  # noqa: S608 - fixed names
    if table == "settings":
        rows = [r for r in rows if r["key"] not in SETTINGS_LEFT_OUT]
    return rows


def manifest(tables: dict[str, list[dict[str, Any]]], files: dict[str, bytes],
             meta: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
    return meta | {
        "product_version": __version__,
        "data_format": DATA_FORMAT,
        "counts": {t: len(rows) for t, rows in tables.items()},
        "files": {name: hashlib.sha256(b).hexdigest() for name, b in sorted(files.items())},
    }


def pack(tables: dict[str, list[dict[str, Any]]], meta: dict[str, Any]) -> bytes:
    """The plaintext: a tar.gz of the manifest and one JSON-lines file per table."""
    files = {f"tables/{t}.jsonl": _jsonl(rows) for t, rows in tables.items()}
    files = {"manifest.json": _canonical(manifest(tables, files, meta)) + b"\n"} | files
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o600, 0
            tar.addfile(info, io.BytesIO(data))
    return gzip.compress(raw.getvalue(), mtime=0)


def _jsonl(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(_canonical(r) + b"\n" for r in rows)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


# ---- the file -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Contents:
    plaintext: bytes  # the tar.gz
    header: dict[str, Any]  # the outer header's fields, before `format` and `signed`
    counts: dict[str, int]


def contents(conn: sqlite3.Connection, data_dir: Path, *, install: str, kind: str,
             created_at: str, seq: int) -> Contents:  # fmt: skip
    """The plaintext and header of a `scheduled` or `manual` bundle (the same tables either way)."""
    ident = {"install_id": install_identity.install_id(conn),
             "generation": install_identity.generation(conn)}  # fmt: skip
    schema = int(conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0])
    tables = snapshot_tables(conn, data_dir)
    meta = ident | {"install": install, "mode": "local", "kind": kind, "created_at": created_at,
                    "seq": seq, "creator": person(), "schema_version": schema}  # fmt: skip
    header = ident | {"kind": kind, "created_at": created_at, "seq": seq,
                      "data_format": DATA_FORMAT, "schema_version": schema}  # fmt: skip
    return Contents(pack(tables, meta), header, {t: len(r) for t, r in tables.items()})


def seal(ciphertext: bytes, header: dict[str, Any], seed: bytes | None) -> bytes:
    """The file: signed with `seed`, or, with none (a manual export before any backup key),
    unsigned: `signed: false` in the header and 64 zero bytes where the signature goes."""
    head = _canonical(header | {"format": FORMAT, "signed": seed is not None})
    if len(head) > MAX_HEADER:
        raise BundleError("bundle header too large")
    body = MAGIC + struct.pack(">I", len(head)) + head + ciphertext
    sig = bytes(SIG_BYTES) if seed is None else SigningKey(seed).sign(body).signature
    return body + sig


def read_header(data: bytes) -> dict[str, Any]:
    """The header, unverified: only to choose the key to verify with."""
    return _split(data)[0]


def verify(data: bytes, keys: Iterable[VerifyKey]) -> tuple[dict[str, Any], bytes]:
    """The header and ciphertext when one of `keys` signed the file; else BundleError."""
    header, ciphertext = _split(data)
    if header.get("signed") is False:
        raise BundleError("the bundle isn't signed")
    signed, sig = data[:-SIG_BYTES], data[-SIG_BYTES:]
    for key in keys:
        try:
            key.verify(signed, sig)
        except BadSignatureError:
            continue
        return header, ciphertext
    raise BundleError("the bundle isn't signed by this install's backup key")


def _split(data: bytes) -> tuple[dict[str, Any], bytes]:
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 4 + SIG_BYTES:
        raise BundleError("not an ecf bundle")
    (n,) = struct.unpack(">I", data[len(MAGIC) : len(MAGIC) + 4])
    start = len(MAGIC) + 4
    if n > MAX_HEADER or start + n > len(data) - SIG_BYTES:
        raise BundleError("damaged bundle header")
    try:
        parsed: Any = json.loads(data[start : start + n])
    except ValueError:
        raise BundleError("damaged bundle header") from None
    if not isinstance(parsed, dict):
        raise BundleError("damaged bundle header")
    header = cast("dict[str, Any]", parsed)
    if header.get("format") != FORMAT:
        raise BundleError("unknown bundle format")
    return header, data[start + n : -SIG_BYTES]


def unpack(plaintext: bytes) -> dict[str, bytes]:
    """The files of a decrypted bundle (tests now; import in step 9 adds its own limits)."""
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(plaintext)), mode="r") as tar:
        for m in tar.getmembers():
            f = tar.extractfile(m)
            if f is not None:
                out[m.name] = f.read()
    return out


def write_atomic(where: Path, name: str, data: bytes) -> Path:
    """A 0600 temp file in `where`, synced, then renamed to `name`; never over an existing file."""
    final = where / name
    if final.exists():
        raise FileExistsError(errno.EEXIST, "already exists", str(final))
    tmp = where / f".{name}.partial"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, final)  # fails if `name` appeared meanwhile; rename would replace it
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.unlink()
    try:
        dfd = os.open(where, os.O_RDONLY)
    except OSError:
        return final  # some synced folders can't be opened for a directory sync
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)
    return final


def file_name(install: str, created_at: str, seq: int) -> str:
    stamp = (
        created_at[:19].replace("-", "").replace(":", "")
    )  # 2026-10-03T04:05:06 → 20261003T040506
    return f"ecf-{install}-{stamp}Z-{seq:06d}{SUFFIX}"
