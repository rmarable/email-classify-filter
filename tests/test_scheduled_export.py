"""Scheduled export (V1.5 step 8b; OD-315, OD-343, OD-345 to OD-347): what a bundle holds and
leaves out, the signed file, the schedule, failures and the alert, pruning, the daily line,
`export_schedule` and `export_keep`, and the routes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
from pathlib import Path
from typing import Any

import pytest
from nacl.signing import SigningKey
from typer.testing import CliRunner

import ecf.cli_export
from ecf.cli import app
from ecf.errors import InvalidInputError
from ecf.paths import Paths
from ecf_server import (
    _age,
    backup_key,
    config,
    daily,
    export_bundle,
    export_keys,
    health,
    install_identity,
    scheduled_export,
    settings,
)
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.secretstore.select import INTERPRETER_KEY
from tests.test_addresses import TOKEN, call, make_state
from tests.test_export_keys import ApiClient

ROOT = bytes(range(32))
DAY = 86_400


@pytest.fixture
def data_dir(db_path: Path) -> Path:
    return db_path.parent


@pytest.fixture
def out(tmp_path: Path) -> Path:
    p = tmp_path / "backups"
    p.mkdir()
    return p


def _set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, 't',"
                     " 't') ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (key, json.dumps(value)))  # fmt: skip


def _set_up(conn: sqlite3.Connection, store: MemorySecretStore, out: Path,
            root: bytes = ROOT) -> backup_key.Derived:  # fmt: skip
    d = backup_key.derive(root)
    store.set(export_keys.SEED_NAME, d.signing_seed.hex())
    key = {"generation": 1, "recipient": d.public.recipient, "verify_key": d.public.verify_key,
           "fingerprint": d.public.fingerprint, "created_at": "t"}  # fmt: skip
    _set(conn, export_keys.KEY_KEY, key)
    _set(conn, export_keys.DIR_KEY, str(out))
    return d


def _data(conn: sqlite3.Connection) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('ap', 'ap@acme.example', 'standard', 'A', 't')")  # fmt: skip
        conn.execute("INSERT INTO nonces (nonce_id, purpose, bound_hash, person, created_at,"
                     " expires_at, target, code) VALUES ('n1', 'test', 'h', 'me', 't', 't', '{}',"
                     " 'ABCD')")  # fmt: skip
    _set(conn, INTERPRETER_KEY, "abc123")


def _run(conn: sqlite3.Connection, clock: FakeClock, store: MemorySecretStore | None,
         data_dir: Path, n: FakeNotifier | None = None) -> dict[str, Any]:  # fmt: skip
    return scheduled_export.run(conn, clock, n or FakeNotifier(), store, data_dir, "t")


# ---- contents -----------------------------------------------------------------------------------


def test_every_table_is_included_or_excluded(conn: sqlite3.Connection) -> None:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"
                                         " AND name NOT LIKE 'sqlite_%'")}  # fmt: skip
    inc, exc = set(export_bundle.INCLUDED), set(export_bundle.EXCLUDED)
    assert not inc & exc
    assert tables == inc | exc, f"decide for: {sorted(tables ^ (inc | exc))}"
    for secret_bearing in ("grants", "nonces", "jobs", "leases", "claims"):
        assert secret_bearing in exc


def test_a_bundle_round_trips_and_leaves_out_what_it_must(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path
) -> None:
    store = MemorySecretStore()
    d = _set_up(conn, store, out)
    _data(conn)
    r = _run(conn, clock, store, data_dir)
    assert r["ok"], r
    path = out / r["file"]
    assert r["file"].startswith("ecf-t-") and r["file"].endswith(".ecfb") and r["seq"] == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(data_dir.glob(".export-*"))  # the snapshot copy is gone
    raw = path.read_bytes()
    header, ciphertext = export_bundle.verify(raw, [backup_key.verify_key(d.public)])
    assert header["install_id"] == install_identity.install_id(conn)
    assert header["kind"] == "scheduled" and header["seq"] == 1
    assert header["key_fingerprint"] == d.public.fingerprint
    files = export_bundle.unpack(_age.decrypt(ciphertext, d.identity))
    manifest = json.loads(files["manifest.json"])
    assert manifest["data_format"] == export_bundle.DATA_FORMAT and manifest["mode"] == "local"
    for name, sha in manifest["files"].items():
        assert hashlib.sha256(files[name]).hexdigest() == sha
    assert set(files) == {"manifest.json", *(f"tables/{t}.jsonl" for t in export_bundle.INCLUDED)}
    rows = [json.loads(x) for x in files["tables/addresses.jsonl"].splitlines()]
    assert [r["address_id"] for r in rows] == ["ap"]
    keys = {json.loads(x)["key"] for x in files["tables/settings.jsonl"].splitlines()}
    assert INTERPRETER_KEY not in keys and "install.id" in keys
    whole = b"".join(files.values())
    assert b"ABCD" not in whole  # no nonce
    assert d.signing_seed.hex().encode() not in whole


def test_tampered_or_foreign_bundles_fail_verification(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path
) -> None:
    store = MemorySecretStore()
    d = _set_up(conn, store, out)
    raw = (out / _run(conn, clock, store, data_dir)["file"]).read_bytes()
    keys = [backup_key.verify_key(d.public)]
    flipped = bytearray(raw)
    flipped[len(raw) // 2] ^= 1
    with pytest.raises(export_bundle.BundleError):
        export_bundle.verify(bytes(flipped), keys)
    header = export_bundle.read_header(raw)
    forged = raw.replace(json.dumps(header["seq"]).encode(), b"9", 1)
    with pytest.raises(export_bundle.BundleError):
        export_bundle.verify(forged, keys)
    other = SigningKey.generate().verify_key
    with pytest.raises(export_bundle.BundleError, match="isn't signed"):
        export_bundle.verify(raw, [other])
    with pytest.raises(export_bundle.BundleError, match="not an ecf bundle"):
        export_bundle.verify(b"junk" * 40, keys)


# ---- schedule and failures ----------------------------------------------------------------------


def test_due(conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path) -> None:
    store = MemorySecretStore()
    assert not scheduled_export.due(conn, clock)  # no key, no dir: nothing runs
    _set_up(conn, store, out)
    assert scheduled_export.due(conn, clock)
    assert _run(conn, clock, store, data_dir)["ok"]
    assert not scheduled_export.due(conn, clock)
    clock.advance(DAY - 60)
    assert not scheduled_export.due(conn, clock)
    clock.advance(60)
    assert scheduled_export.due(conn, clock)
    _set(conn, "export_schedule", "weekly")
    assert not scheduled_export.due(conn, clock)
    clock.advance(6 * DAY)
    assert scheduled_export.due(conn, clock)
    _set(conn, "export_schedule", "off")
    assert not scheduled_export.due(conn, clock)


def test_failures_retry_hourly_then_alert_and_a_success_resolves(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path
) -> None:
    store, n = MemorySecretStore(), FakeNotifier()
    _set_up(conn, store, out)
    store.delete(export_keys.SEED_NAME)
    r = _run(conn, clock, store, data_dir, n)
    assert not r["ok"] and "missing from the secret store" in r["error"]
    assert not health.open_alerts(conn) and not scheduled_export.due(conn, clock)
    clock.advance(3600)
    assert scheduled_export.due(conn, clock)
    _run(conn, clock, store, data_dir, n)
    alerts = health.open_alerts(conn)
    assert [a["kind"] for a in alerts] == ["export_failed"]
    assert "Backups are failing (2 tries)" in alerts[0]["detail"]
    assert "Last good backup: never" in alerts[0]["detail"]
    assert "failed: the signing key is missing" in scheduled_export.daily_line(conn)
    _set_up(conn, store, out)
    clock.advance(3600)
    assert _run(conn, clock, store, data_dir, n)["ok"]
    assert not health.open_alerts(conn)
    assert scheduled_export.status(conn)["failures"] == 0
    events = [r[0] for r in conn.execute("SELECT event FROM audit WHERE event LIKE 'export.%'")]
    assert events == ["export.failed", "export.failed", "export.completed"]


def test_failure_reasons(conn: sqlite3.Connection, clock: FakeClock, data_dir: Path,
                         out: Path) -> None:  # fmt: skip
    store = MemorySecretStore()
    _set_up(conn, store, out)
    assert "no secret store" in _run(conn, clock, None, data_dir)["error"]
    store.set(export_keys.SEED_NAME, bytes(32).hex())  # someone else's seed
    assert "doesn't match the backup key" in _run(conn, clock, store, data_dir)["error"]
    _set_up(conn, store, out)
    store.locked = True
    assert "secret store" in _run(conn, clock, store, data_dir)["error"]
    store.locked = False
    out.rmdir()
    assert "isn't an existing directory" in _run(conn, clock, store, data_dir)["error"]
    assert not list(data_dir.glob(".export-*"))


def test_start_runs_in_a_thread_once(conn: sqlite3.Connection, clock: FakeClock, data_dir: Path,
                                     db_path: Path, out: Path) -> None:  # fmt: skip
    from ecf_server import db  # noqa: PLC0415

    store = MemorySecretStore()
    _set_up(conn, store, out)
    held: list[Any] = []
    assert scheduled_export.start(lambda: db.connect(db_path), clock, FakeNotifier(), store,
                                  data_dir, "t", spawn=held.append)  # fmt: skip
    assert not scheduled_export.start(lambda: db.connect(db_path), clock, FakeNotifier(), store,
                                      data_dir, "t", spawn=held.append)  # fmt: skip
    held[0]()
    assert len(list(out.glob("*.ecfb"))) == 1
    assert scheduled_export.start(lambda: db.connect(db_path), clock, FakeNotifier(), store,
                                  data_dir, "t", spawn=lambda f: f())  # fmt: skip


# ---- pruning ------------------------------------------------------------------------------------


def test_prune_keeps_the_newest_and_touches_only_this_installs_bundles(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path
) -> None:
    store = MemorySecretStore()
    _set_up(conn, store, out)
    settings.set_value(conn, clock, "export_keep", "2", address=None, actor="t")
    foreign = out / "ecf-t-20200101T000000Z-000001.ecfb"
    foreign.write_bytes(b"not ours")
    other_key = backup_key.derive(bytes([7]) * 32)
    stranger = export_bundle.seal(b"x", {"install_id": install_identity.install_id(conn),
                                         "kind": "scheduled", "created_at": "2000", "seq": 0},
                                  other_key.public.recipient, other_key.signing_seed)  # fmt: skip
    (out / "ecf-t-20000101T000000Z-000000.ecfb").write_bytes(stranger)
    (out / ".ecf-t-x.ecfb.partial").write_bytes(b"crash leftover")
    runs: list[dict[str, Any]] = []
    for _ in range(3):
        runs.append(_run(conn, clock, store, data_dir))
        clock.advance(DAY)
    names = [str(r["file"]) for r in runs]
    assert runs[-1]["pruned"] == [names[0]]
    left = sorted(p.name for p in out.iterdir())
    assert left == sorted([*names[1:], foreign.name, "ecf-t-20000101T000000Z-000000.ecfb"])


def test_bundles_from_an_earlier_key_are_still_pruned(
    conn: sqlite3.Connection, clock: FakeClock, data_dir: Path, out: Path
) -> None:
    store = MemorySecretStore()
    old = _set_up(conn, store, out)
    first = _run(conn, clock, store, data_dir)["file"]
    _set(conn, export_keys.PREVIOUS_KEY, [{"generation": 1, "recipient": old.public.recipient,
                                           "verify_key": old.public.verify_key,
                                           "fingerprint": old.public.fingerprint}])  # fmt: skip
    _set_up(conn, store, out, root=bytes([9]) * 32)
    settings.set_value(conn, clock, "export_keep", "1", address=None, actor="t")
    clock.advance(DAY)
    r = _run(conn, clock, store, data_dir)
    assert r["pruned"] == [first]


# ---- reporting and configuration ----------------------------------------------------------------


def test_daily_line(conn: sqlite3.Connection, clock: FakeClock, data_dir: Path,
                   out: Path) -> None:  # fmt: skip
    assert "not set up" in scheduled_export.daily_line(conn)
    store = MemorySecretStore()
    _set_up(conn, store, out)
    assert scheduled_export.daily_line(conn) == "Backups: none yet."
    _run(conn, clock, store, data_dir)
    line = scheduled_export.daily_line(conn)
    assert line.startswith("Last backup ") and str(out) in line
    card = daily.card(conn, clock.now(), "2026-10-03")
    assert "Last backup " in (card.text or "")
    _set(conn, "export_schedule", "off")
    assert scheduled_export.daily_line(conn) == "Backups: off (export_schedule: off)."


def test_export_schedule_through_config_apply(conn: sqlite3.Connection) -> None:
    doc = config.validate(conn, {"export_schedule": "weekly"})
    assert doc == {"export_schedule": "weekly"}
    changes = config.diff(config.current(conn), doc)
    assert changes == [{"section": "export_schedule", "change": "daily to weekly"}]
    with pytest.raises(InvalidInputError, match="daily, weekly or off"):
        config.validate(conn, {"export_schedule": "hourly"})
    _set(conn, "export_schedule", "off")
    back = config.validate(conn, {"export_schedule": "default"})
    assert config.diff(config.current(conn), back)[0]["change"].startswith("reset to daily")
    assert scheduled_export.schedule(conn) == "off"


def test_export_keep_setting(conn: sqlite3.Connection, clock: FakeClock) -> None:
    assert settings.get(conn, "export_keep") == 14
    settings.set_value(conn, clock, "export_keep", "30", address=None, actor="t")
    assert settings.get(conn, "export_keep") == 30
    with pytest.raises(InvalidInputError):
        settings.set_value(conn, clock, "export_keep", "0", address=None, actor="t")
    with pytest.raises(InvalidInputError, match="config apply"):
        settings.key("export_schedule", None)


def test_routes(conn: sqlite3.Connection, db_path: Path, out: Path) -> None:
    store = MemorySecretStore()
    st = make_state(db_path, store)
    r = call(st, "POST", "/v1/export/now", {}, TOKEN)
    assert r.status_code == 409 and "aren't set up" in r.text
    _set_up(conn, store, out)
    r = call(st, "POST", "/v1/export/now", {}, TOKEN)
    assert r.status_code == 200 and r.json()["ok"], r.text
    got = call(st, "GET", "/v1/export", None, TOKEN).json()
    assert got["schedule"] == "daily" and got["keep"] == 14 and got["set_up"]
    assert got["last_ok"]["file"] == r.json()["file"] and got["failures"] == 0
    assert (out / r.json()["file"]).is_file()


def test_cli_status_and_now(conn: sqlite3.Connection, db_path: Path, out: Path,
                            monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    store = MemorySecretStore()
    st = make_state(db_path, store)

    def client(_paths: Paths) -> ApiClient:
        return ApiClient(st)

    monkeypatch.setattr(ecf.cli_export, "LocalClient", client)
    runner = CliRunner()
    r = runner.invoke(app, ["--install", "t", "export", "status"])
    assert r.exit_code == 0 and "schedule: daily (keeps the newest 14)" in r.output
    assert "not set up" in r.output
    _set_up(conn, store, out)
    r = runner.invoke(app, ["--install", "t", "export", "now"])
    assert r.exit_code == 0 and "backup written: " in r.output and str(out) in r.output
    r = runner.invoke(app, ["--install", "t", "export", "status"])
    assert "last backup: " in r.output and ".ecfb" in r.output
    store.delete(export_keys.SEED_NAME)
    r = runner.invoke(app, ["--install", "t", "export", "now"])
    assert r.exit_code == 1 and "backup failed: the signing key is missing" in r.output
