"""Reading a bundle for import and restore (SPEC §11.9; OD-352 to OD-356; V1.5 step 9b).

Everything here treats the file as hostile until it has been checked, and never writes it out.

1. **Open** (`inspect`): a regular file of at most `MAX_BUNDLE` bytes; the header is parsed
   (format, kind) and the signature is tried against this install's current and earlier keys:
   `own` when one verifies. A bundle naming this install that doesn't verify is reported as such.
2. **Decrypt** (OD-352): a scheduled bundle with the typed backup key (its derived age identity),
   a manual one with its passphrase. When the typed key's public half verifies the signature, the
   signer is `typed_key` (what restore requires, step 10).
3. **Unpack** (OD-354): the plaintext is read as a stream (`r|gz`), never extracted. The first
   member must be `manifest.json`; then only `tables/<name>.jsonl` for a table in
   `export_bundle.INCLUDED`, each a regular file, once, at most `MAX_MEMBER` bytes, at most
   `MAX_TOTAL` in all and `MAX_MEMBERS` files; `tarfile.data_filter` must accept each member too.
   Each file's SHA-256 must match the manifest before it is parsed, and every file the manifest
   names must be there.
4. **Versions** (OD-355): the data format is this one or the one before; a schema newer than this
   install's is refused (no downgrade); header and manifest must agree.
5. **Rows**: each line one JSON object whose values are strings, numbers, booleans or null; the
   counts must match the manifest. Columns are checked against the schema when the rows are loaded
   (step 9c).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import sqlite3
import stat
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, cast

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from ecf.errors import InvalidInputError
from ecf_server import _age, backup_key, export_bundle, export_keys, install_identity

MAX_BUNDLE = 512 * 1024 * 1024
MAX_MEMBER = 1024 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_MEMBERS = 64
CHUNK = 1024 * 1024
FORMATS = (export_bundle.DATA_FORMAT - 1, export_bundle.DATA_FORMAT)


class BadBundleError(InvalidInputError):
    """The bundle can't be used; the message names the check, never the bundle's contents."""


@dataclass
class Opened:
    data: bytes = field(repr=False)
    header: dict[str, Any]
    signed: bool
    own: bool  # signed by this install's current or an earlier key
    claims_this_install: bool

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass
class Parsed:
    opened: Opened
    signer: str  # own, typed_key, unknown, unsigned
    manifest: dict[str, Any]
    tables: dict[str, list[dict[str, Any]]] = field(repr=False)


def inspect(conn: sqlite3.Connection, path: str) -> Opened:
    data = _read(path)
    try:
        header = export_bundle.read_header(data)
    except export_bundle.BundleError as exc:
        raise BadBundleError(str(exc)) from None
    if header.get("kind") not in ("scheduled", "manual"):
        raise BadBundleError("unknown bundle kind")
    signed = header.get("signed") is not False
    own = signed and _verifies(data, _own_keys(conn))
    claims = header.get("install_id") == install_identity.install_id(conn)
    return Opened(data, header, signed, own, claims)


def describe(o: Opened) -> dict[str, Any]:
    """What the header says, for the CLI to choose the prompt (no secrets needed)."""
    h = o.header
    return {"kind": h["kind"], "signed": o.signed, "own": o.own,
            "claims_this_install": o.claims_this_install, "install_id": h.get("install_id"),
            "generation": h.get("generation"), "created_at": h.get("created_at"),
            "seq": h.get("seq"), "key_fingerprint": h.get("key_fingerprint"),
            "schema_version": h.get("schema_version"), "data_format": h.get("data_format"),
            "bytes": len(o.data)}  # fmt: skip


def read(conn: sqlite3.Connection, path: str, secret: str) -> Parsed:
    """Open, decrypt with `secret` (the backup key text or the passphrase), unpack and check."""
    o = inspect(conn, path)
    ciphertext = o.data[_body_start(o.data) : -export_bundle.SIG_BYTES]
    signer = "own" if o.own else ("unknown" if o.signed else "unsigned")
    if o.header["kind"] == "scheduled":
        root = backup_key.parse_key_text(secret)
        derived = backup_key.derive(root)
        if o.signed and not o.own and _verifies(o.data, [backup_key.verify_key(derived.public)]):
            signer = "typed_key"
        try:
            plaintext = _age.decrypt(ciphertext, derived.identity)
        except _age.AgeError:
            raise BadBundleError("that backup key doesn't open this bundle") from None
    else:
        try:
            plaintext = _age.decrypt_passphrase(ciphertext, secret)
        except _age.AgeError:
            raise BadBundleError("that passphrase doesn't open this bundle") from None
    manifest, tables = unpack(plaintext)
    check_versions(conn, o.header, manifest)
    return Parsed(o, signer, manifest, tables)


# ---- unpacking ----------------------------------------------------------------------------------


def unpack(plaintext: bytes) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    allowed = {f"tables/{t}.jsonl": t for t in export_bundle.INCLUDED}
    manifest: dict[str, Any] | None = None
    tables: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    total = 0
    for i, (member, f) in enumerate(_members(plaintext)):
        name = member.name
        if i >= MAX_MEMBERS:
            raise BadBundleError(f"more than {MAX_MEMBERS} files in the bundle")
        if name in seen:
            raise BadBundleError(f"{name!r} appears twice")
        seen.add(name)
        if i == 0 and name != "manifest.json":
            raise BadBundleError("the bundle doesn't start with its manifest")
        if i > 0 and name not in allowed:
            raise BadBundleError(f"unexpected file {name[:80]!r} in the bundle")
        body = _read_member(f, member.size)
        total += len(body)
        if total > MAX_TOTAL:
            raise BadBundleError("the bundle unpacks to more than 2 GB")
        if manifest is None:
            manifest = _manifest(body)
            continue
        want = manifest["files"].get(name)
        if want is None or hashlib.sha256(body).hexdigest() != want:
            raise BadBundleError(f"{name} doesn't match the manifest")
        tables[allowed[name]] = _rows(name, body)
    if manifest is None:
        raise BadBundleError("the bundle has no manifest")
    missing = set(manifest["files"]) - seen
    if missing:
        raise BadBundleError(f"files the manifest names are missing: {', '.join(sorted(missing))}")
    for t, rows in tables.items():
        if manifest["counts"].get(t) != len(rows):
            raise BadBundleError(f"{t}: row count doesn't match the manifest")
    return manifest, tables


def _members(plaintext: bytes) -> Iterator[tuple[tarfile.TarInfo, IO[bytes] | None]]:
    """Each member, checked (`_check_member`) before anything of it is read."""
    try:
        gz = gzip.GzipFile(fileobj=io.BytesIO(plaintext), mode="rb")
        with tarfile.open(fileobj=gz, mode="r|") as tar:
            for member in tar:
                _check_member(member)
                yield member, tar.extractfile(member)
    except (tarfile.TarError, OSError, EOFError, gzip.BadGzipFile) as exc:
        raise BadBundleError(f"the bundle's archive is damaged ({type(exc).__name__})") from None


def _check_member(member: tarfile.TarInfo) -> None:
    if not member.isreg():
        raise BadBundleError(f"{member.name[:80]!r} isn't a regular file")
    if member.size > MAX_MEMBER:
        raise BadBundleError(f"{member.name} is larger than 1 GB")
    try:
        tarfile.data_filter(member, "/nonexistent-ecf-import")
    except tarfile.FilterError as exc:
        raise BadBundleError(f"unsafe file in the bundle ({type(exc).__name__})") from None


def _read_member(f: IO[bytes] | None, size: int) -> bytes:
    if f is None:
        raise BadBundleError("a bundle file can't be read")
    buf = io.BytesIO()
    while chunk := f.read(min(CHUNK, size + 1 - buf.tell())):
        buf.write(chunk)
        if buf.tell() > size:
            raise BadBundleError("a bundle file is longer than its header says")
    return buf.getvalue()


def _manifest(body: bytes) -> dict[str, Any]:
    try:
        m = json.loads(body)
    except ValueError:
        raise BadBundleError("the manifest isn't JSON") from None
    if not isinstance(m, dict):
        raise BadBundleError("the manifest isn't an object")
    manifest = cast("dict[str, Any]", m)
    files, counts = manifest.get("files"), manifest.get("counts")
    if not isinstance(files, dict) or not isinstance(counts, dict):
        raise BadBundleError("the manifest has no file list")
    for k, v in cast("dict[Any, Any]", files).items():
        if not isinstance(k, str) or not isinstance(v, str) or len(v) != 64:
            raise BadBundleError("the manifest's file list is malformed")
    return manifest


def _rows(name: str, body: bytes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n, line in enumerate(body.splitlines(), 1):
        try:
            row = json.loads(line)
        except ValueError:
            raise BadBundleError(f"{name} line {n} isn't JSON") from None
        if not isinstance(row, dict):
            raise BadBundleError(f"{name} line {n} isn't an object")
        obj = cast("dict[Any, Any]", row)
        for k, v in obj.items():
            if not isinstance(k, str) or not (v is None or isinstance(v, str | int | float | bool)):
                raise BadBundleError(f"{name} line {n} has a value of the wrong kind")
        out.append(cast("dict[str, Any]", obj))
    return out


def check_versions(conn: sqlite3.Connection, header: dict[str, Any],
                   manifest: dict[str, Any]) -> None:  # fmt: skip
    for k in ("install_id", "kind", "created_at", "seq", "data_format", "schema_version"):
        if header.get(k) != manifest.get(k):
            raise BadBundleError(f"the bundle's header and manifest disagree on {k}")
    if manifest.get("data_format") not in FORMATS:
        raise BadBundleError(f"data format {manifest.get('data_format')} isn't one this version"
                             f" reads ({' or '.join(map(str, FORMATS))})")  # fmt: skip
    ours = int(conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0])
    theirs = manifest.get("schema_version")
    if not isinstance(theirs, int) or theirs < 1:
        raise BadBundleError("the bundle has no schema version")
    if theirs > ours:
        raise BadBundleError(f"the bundle is from a newer ecf (schema {theirs}; this one has"
                             f" {ours}); upgrade ecf first")  # fmt: skip


# ---- helpers ------------------------------------------------------------------------------------


def _read(path: str) -> bytes:
    p = Path(path)
    if not p.is_absolute():
        raise InvalidInputError("give the full path of the bundle")
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise InvalidInputError(f"can't open {p}: {exc.strerror or type(exc).__name__}") from None
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BUNDLE:
        os.close(fd)
        if not stat.S_ISREG(st.st_mode):
            raise InvalidInputError(f"{p} isn't a regular file")
        raise BadBundleError("the bundle is larger than 512 MB")
    with os.fdopen(fd, "rb") as f:
        data = f.read(MAX_BUNDLE + 1)
    if len(data) > MAX_BUNDLE:
        raise BadBundleError("the bundle is larger than 512 MB")
    return data


def _body_start(data: bytes) -> int:
    n = int.from_bytes(data[len(export_bundle.MAGIC) : len(export_bundle.MAGIC) + 4])
    return len(export_bundle.MAGIC) + 4 + n


def _own_keys(conn: sqlite3.Connection) -> list[VerifyKey]:
    entries = [*export_keys.previous(conn), export_keys.current(conn)]
    return [VerifyKey(bytes.fromhex(e["verify_key"])) for e in entries if e is not None]


def _verifies(data: bytes, keys: list[VerifyKey]) -> bool:
    signed, sig = data[: -export_bundle.SIG_BYTES], data[-export_bundle.SIG_BYTES :]
    for k in keys:
        try:
            k.verify(signed, sig)
        except BadSignatureError:
            continue
        return True
    return False
