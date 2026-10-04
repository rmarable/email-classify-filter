"""Reading a bundle (V1.5 step 9b; OD-352 to OD-356): signers, unlocking with the backup key or the
passphrase, the archive checks against hostile bundles, versions, the preview, the routes and
`ecf import --dry-run`."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import sqlite3
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import ecf.cli_import
from ecf.cli import app
from ecf.errors import InvalidInputError
from ecf.paths import Paths
from ecf_server import (
    _age,
    backup_key,
    bundle_reader,
    db,
    export_bundle,
    import_plan,
    install_identity,
    manual_export,
    scheduled_export,
    stepup,
)
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.stepper import FakeStepper
from tests.test_addresses import TOKEN, call, make_state
from tests.test_export_keys import ApiClient
from tests.test_scheduled_export import ROOT, _set, _set_up  # pyright: ignore[reportPrivateUsage]

KEY_TEXT = backup_key.key_text(ROOT)
OTHER = bytes([5]) * 32
PASS = "maple orbit candle river stone"
LABEL = json.dumps({"actions": [{"name": "label", "target": "invoice"}]})


@pytest.fixture
def data_dir(db_path: Path) -> Path:
    return db_path.parent


@pytest.fixture
def out(tmp_path: Path) -> Path:
    p = tmp_path / "bundles"
    p.mkdir()
    return p


def _scheduled(conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path,
               root: bytes = ROOT) -> Path:  # fmt: skip
    store = MemorySecretStore()
    _set_up(conn, store, out, root=root)
    r = scheduled_export.run(conn, clock, FakeNotifier(), store, data_dir, "t")
    assert r["ok"], r
    return out / r["file"]


def _data(conn: sqlite3.Connection) -> None:
    with write_tx(conn), db.items_writer("create"):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, stage, preset,"
                     " created_at) VALUES ('ap', 'ap@acme.example', 'standard', 'live', 'A',"
                     " 't')")  # fmt: skip
        for i, status in enumerate(("awaiting_approval", "delayed", "executing", "executed")):
            conn.execute("INSERT INTO items (stable_id, address_id, uid, uidvalidity, status,"
                         " content_hash, proposal, created_at, updated_at) VALUES (?, 'ap', ?, 1,"
                         " ?, 'h', ?, 't', 't')",
                         (f"s{i}", i + 1, status, LABEL))  # fmt: skip
    _set(conn, "alerts.routes", ["slack", "email"])
    _set(conn, "slack_member_id", "U1")
    _set(conn, "business_hours", {"days": [0], "start": "08:00", "end": "17:00", "tz": "UTC"})


# ---- reading real bundles -----------------------------------------------------------------------


def test_an_own_scheduled_bundle_opens_with_the_backup_key(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path
) -> None:
    _data(conn)
    path = _scheduled(conn, clock, data_dir, out)
    o = bundle_reader.inspect(conn, str(path))
    assert o.own and o.claims_this_install and o.signed
    head = bundle_reader.describe(o)
    assert head["kind"] == "scheduled" and head["install_id"] == install_identity.install_id(conn)
    p = bundle_reader.read(conn, str(path), KEY_TEXT.lower())
    assert p.signer == "own"
    assert [r["address_id"] for r in p.tables["addresses"]] == ["ap"]
    assert len(p.tables["items"]) == 4
    with pytest.raises(bundle_reader.BadBundleError, match="doesn't open"):
        bundle_reader.read(conn, str(path), backup_key.key_text(OTHER))
    with pytest.raises(InvalidInputError, match=r"typo|length|character"):
        bundle_reader.read(conn, str(path), KEY_TEXT[:-1] + ("A" if KEY_TEXT[-1] != "A" else "B"))


def test_a_bundle_from_another_install_is_signed_by_the_typed_key(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path
) -> None:
    path = _scheduled(conn, clock, data_dir, out, root=OTHER)
    _set_up(conn, MemorySecretStore(), out)  # this install's key is now ROOT
    _set(conn, "install.id", "f" * 32)  # and it's another install
    o = bundle_reader.inspect(conn, str(path))
    assert not o.own and not o.claims_this_install
    p = bundle_reader.read(conn, str(path), backup_key.key_text(OTHER))
    assert p.signer == "typed_key"


def test_a_bundle_naming_this_install_with_a_bad_signature(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path
) -> None:
    path = _scheduled(conn, clock, data_dir, out)
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1  # the signature's last byte
    path.write_bytes(bytes(raw))
    o = bundle_reader.inspect(conn, str(path))
    assert o.claims_this_install and not o.own
    assert bundle_reader.read(conn, str(path), KEY_TEXT).signer == "unknown"


def test_a_manual_bundle_opens_with_its_passphrase(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path
) -> None:
    target = {"path": str(out.resolve() / "m.ecfb")}
    issued = stepup.issue(conn, clock, FakeStepper(), "export_manual", target)
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    manual_export.export(conn, clock, FakeNotifier(), None, data_dir, "t", target["path"], PASS,
                         nonce=issued.nonce_id)  # fmt: skip
    o = bundle_reader.inspect(conn, target["path"])
    assert o.header["kind"] == "manual" and not o.signed and not o.own
    assert bundle_reader.read(conn, target["path"], PASS).signer == "unsigned"
    with pytest.raises(bundle_reader.BadBundleError, match="passphrase doesn't open"):
        bundle_reader.read(conn, target["path"], "a wrong passphrase of length")


# ---- hostile archives ---------------------------------------------------------------------------


def _tar(members: list[tarfile.TarInfo | tuple[str, bytes]]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for m in members:
            if isinstance(m, tarfile.TarInfo):
                tar.addfile(m)
            else:
                info = tarfile.TarInfo(m[0])
                info.size = len(m[1])
                tar.addfile(info, io.BytesIO(m[1]))
    return gzip.compress(raw.getvalue())


def _manifest(conn: sqlite3.Connection, files: dict[str, bytes], **over: Any) -> bytes:
    schema = int(conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0])
    m: dict[str, Any] = {
        "install_id": "a" * 32, "kind": "scheduled", "created_at": "2026-10-03T00:00:00.000000Z",
        "seq": 1, "data_format": 1, "schema_version": schema,
        "files": {n: hashlib.sha256(b).hexdigest() for n, b in files.items()},
        "counts": {n.removeprefix("tables/").removesuffix(".jsonl"): len(b.splitlines())
                   for n, b in files.items()},
    } | over  # fmt: skip
    return json.dumps(m).encode()


def _bundle(conn: sqlite3.Connection, out: Path, plaintext: bytes, **header: Any) -> Path:
    d = backup_key.derive(OTHER)
    schema = int(conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0])
    h = {"install_id": "a" * 32, "kind": "scheduled", "created_at": "2026-10-03T00:00:00.000000Z",
         "seq": 1, "data_format": 1, "schema_version": schema} | header  # fmt: skip
    data = export_bundle.seal(_age.encrypt(plaintext, d.public.recipient), h, d.signing_seed)
    path = out / f"crafted-{hashlib.sha256(data).hexdigest()[:8]}.ecfb"
    path.write_bytes(data)
    return path


def _read(conn: sqlite3.Connection, path: Path) -> bundle_reader.Parsed:
    return bundle_reader.read(conn, str(path), backup_key.key_text(OTHER))


ROWS = b'{"address_id":"x","email":"x@acme.example"}\n'


def test_a_well_formed_crafted_bundle_reads(conn: sqlite3.Connection, out: Path) -> None:
    files = {"tables/addresses.jsonl": ROWS}
    p = _read(conn, _bundle(conn, out, _tar([("manifest.json", _manifest(conn, files)),
                                             ("tables/addresses.jsonl", ROWS)])))  # fmt: skip
    assert p.tables == {"addresses": [{"address_id": "x", "email": "x@acme.example"}]}
    assert p.signer == "typed_key"


def _link(name: str, kind: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = "/etc/passwd"
    return info


Member = tarfile.TarInfo | tuple[str, bytes]
HOSTILE: dict[str, tuple[Callable[[bytes], list[Member]], str]] = {
    "manifest_not_first": (
        lambda m: [("tables/addresses.jsonl", ROWS), ("manifest.json", m)], "start with its"),
    "duplicate": (
        lambda m: [("manifest.json", m), ("tables/addresses.jsonl", ROWS),
                   ("tables/addresses.jsonl", ROWS)], "appears twice"),
    "unknown_table": (lambda m: [("manifest.json", m), ("tables/grants.jsonl", ROWS)],
                      "unexpected file"),
    "dotdot": (lambda m: [("manifest.json", m), ("../tables/addresses.jsonl", ROWS)],
               "unsafe file"),
    "absolute": (lambda m: [("manifest.json", m), ("/tables/addresses.jsonl", ROWS)],
                 "unexpected file"),
    "symlink": (lambda m: [("manifest.json", m), _link("tables/addresses.jsonl", tarfile.SYMTYPE)],
                "isn't a regular file"),
    "hardlink": (lambda m: [("manifest.json", m), _link("tables/addresses.jsonl", tarfile.LNKTYPE)],
                 "isn't a regular file"),
    "hash": (lambda m: [("manifest.json", m), ("tables/addresses.jsonl", ROWS + b"{}\n")],
             "doesn't match the manifest"),
    "missing": (lambda m: [("manifest.json", m)], "missing"),
}  # fmt: skip


@pytest.mark.parametrize("case", sorted(HOSTILE))
def test_hostile_archives_are_refused(conn: sqlite3.Connection, out: Path, case: str) -> None:
    members, why = HOSTILE[case]
    files = {"tables/addresses.jsonl": ROWS}
    path = _bundle(conn, out, _tar(members(_manifest(conn, files))))
    with pytest.raises(bundle_reader.BadBundleError, match=why):
        _read(conn, path)


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (b"[1]\n", "isn't an object"),
        (b"not json\n", "isn't JSON"),
        (b'{"a":{"nested":1}}\n', "wrong kind"),
        (b'{"a":[1]}\n', "wrong kind"),
    ],
)
def test_bad_rows_are_refused(conn: sqlite3.Connection, out: Path, body: bytes, why: str) -> None:
    files = {"tables/addresses.jsonl": body}
    path = _bundle(conn, out, _tar([("manifest.json", _manifest(conn, files)),
                                    ("tables/addresses.jsonl", body)]))  # fmt: skip
    with pytest.raises(bundle_reader.BadBundleError, match=why):
        _read(conn, path)


def test_counts_must_match(conn: sqlite3.Connection, out: Path) -> None:
    files = {"tables/addresses.jsonl": ROWS}
    m = _manifest(conn, files, counts={"addresses": 2})
    path = _bundle(conn, out, _tar([("manifest.json", m), ("tables/addresses.jsonl", ROWS)]))
    with pytest.raises(bundle_reader.BadBundleError, match="row count"):
        _read(conn, path)


def test_size_and_count_limits(conn: sqlite3.Connection, out: Path,
                               monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    files = {"tables/addresses.jsonl": ROWS}
    good = _tar([("manifest.json", _manifest(conn, files)), ("tables/addresses.jsonl", ROWS)])
    path = _bundle(conn, out, good)
    monkeypatch.setattr(bundle_reader, "MAX_MEMBER", len(ROWS) - 1)
    with pytest.raises(bundle_reader.BadBundleError, match="larger than 1 GB"):
        _read(conn, path)
    monkeypatch.setattr(bundle_reader, "MAX_MEMBER", 1 << 30)
    monkeypatch.setattr(bundle_reader, "MAX_TOTAL", 10)
    with pytest.raises(bundle_reader.BadBundleError, match="more than 2 GB"):
        _read(conn, path)
    monkeypatch.setattr(bundle_reader, "MAX_TOTAL", 1 << 31)
    monkeypatch.setattr(bundle_reader, "MAX_MEMBERS", 1)
    with pytest.raises(bundle_reader.BadBundleError, match="more than 1 files"):
        _read(conn, path)
    monkeypatch.setattr(bundle_reader, "MAX_BUNDLE", 100)
    with pytest.raises(bundle_reader.BadBundleError, match="larger than 512 MB"):
        bundle_reader.inspect(conn, str(path))


def test_versions(conn: sqlite3.Connection, out: Path) -> None:
    files = {"tables/addresses.jsonl": ROWS}
    schema = int(conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0])
    for over, why in (({"schema_version": schema + 1}, "newer ecf"),
                      ({"data_format": 2}, "data format 2"),
                      ({"seq": 9}, "disagree on seq")):  # fmt: skip
        header = {k: v for k, v in over.items() if k != "seq"}
        m = _manifest(conn, files, **over)
        path = _bundle(conn, out, _tar([("manifest.json", m),
                                        ("tables/addresses.jsonl", ROWS)]), **header)  # fmt: skip
        with pytest.raises(bundle_reader.BadBundleError, match=why):
            _read(conn, path)


def test_file_checks(conn: sqlite3.Connection, out: Path, tmp_path: Path) -> None:
    junk = out / "junk.ecfb"
    junk.write_bytes(b"x" * 100)
    with pytest.raises(bundle_reader.BadBundleError, match="not an ecf bundle"):
        bundle_reader.inspect(conn, str(junk))
    with pytest.raises(InvalidInputError, match="full path"):
        bundle_reader.inspect(conn, "junk.ecfb")
    with pytest.raises(InvalidInputError, match="isn't a regular file"):
        bundle_reader.inspect(conn, str(out))
    link = tmp_path / "link.ecfb"
    link.symlink_to(junk)
    with pytest.raises(InvalidInputError, match="can't open"):
        bundle_reader.inspect(conn, str(link))


# ---- preview, routes and CLI --------------------------------------------------------------------


def test_preview(conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path) -> None:
    _data(conn)
    p = bundle_reader.read(conn, str(_scheduled(conn, clock, data_dir, out)), KEY_TEXT)
    r = import_plan.preview(conn, p)
    assert r["signer"] == "own" and r["source"]["install"] == "t"
    assert r["addresses"] == [{"address_id": "ap", "email": "ap@acme.example", "stage": "assist"}]
    assert r["reposted"] == 2 and r["failed_unknown"] == 1
    assert r["target_empty"] is False
    kept = {row["key"] for row in p.tables["settings"] if import_plan.setting_kept(row["key"])}
    assert {"alerts.routes", "business_hours", "export_schedule"} - {"export_schedule"} <= kept
    assert "slack_member_id" not in kept and "install.id" not in kept
    assert not any(k.startswith("export.") for k in kept)
    assert import_plan.kept_setting_value("alerts.routes", '["slack","email"]') == '["slack"]'
    assert import_plan.kept_setting_value("alerts.routes", '["email"]') == '["slack"]'


def test_routes(conn: sqlite3.Connection, clock: FakeClock, db_path: Path, data_dir: Path,
                out: Path) -> None:  # fmt: skip
    path = _scheduled(conn, clock, data_dir, out)
    st = make_state(db_path, None)
    r = call(st, "POST", "/v1/import/inspect", {"path": str(path)}, TOKEN)
    assert r.status_code == 200 and r.json()["own"] is True
    body = {"path": str(path), "secret": KEY_TEXT, "dry_run": True}
    r = call(st, "POST", "/v1/import", body, TOKEN)
    assert r.status_code == 200 and r.json()["signer"] == "own", r.text
    r = call(st, "POST", "/v1/import", body | {"dry_run": False}, TOKEN)
    assert r.status_code == 403 and r.json()["code"] == "stepup_required"  # applying (9c)


def test_cli_dry_run(conn: sqlite3.Connection, clock: FakeClock, db_path: Path, data_dir: Path,
                     out: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    _data(conn)
    path = _scheduled(conn, clock, data_dir, out)
    st = make_state(db_path, None)

    def client(_paths: Paths) -> ApiClient:
        return ApiClient(st)

    def typed(prompt: str) -> str:
        assert prompt.startswith("Backup key")
        return KEY_TEXT

    monkeypatch.setattr(ecf.cli_import, "LocalClient", client)
    monkeypatch.setattr(ecf.cli_import, "hidden", typed)
    r = CliRunner().invoke(app, ["--install", "t", "import", str(path), "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "scheduled bundle from install" in r.output
    assert "signed by this install's backup key" in r.output
    assert "address ap (ap@acme.example): arrives paused, stage assist, outbound off" in r.output
    assert "re-posted for a fresh decision: 2; marked failed_unknown: 1" in r.output
    assert "this install isn't empty: importing needs --replace" in r.output
