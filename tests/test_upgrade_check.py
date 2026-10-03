"""`ecf upgrade`'s checks and snapshots (V1.5 step 11a; OD-374 to OD-382): version order, the
wheel's release information, the install, compatibility, model pins, the service's state, the
snapshot and its retention, and the CLI."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import ecf.cli_upgrade
from ecf import __version__, upgrade_check
from ecf.cli import app
from ecf.errors import InvalidInputError
from ecf.paths import Paths
from ecf_server import db, upgrade_snapshot, upgrade_state
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from tests.test_addresses import make_state
from tests.test_export_keys import ApiClient

LOCK = {"main_session": "claude-haiku-4-5-20251001", "classifier": "claude-haiku-4-5-20251001",
        "classifier_high": "claude-sonnet-5-5", "actor": "claude-sonnet-5-5",
        "actor_high": "claude-opus-5-5"}  # fmt: skip


def test_version_order() -> None:
    order = ["0.1.0.dev0", "0.1.0.dev1", "0.1.0rc1", "0.1.0", "0.1.1.dev0", "0.2.0", "1.0.0"]
    assert sorted(order, key=upgrade_check.version_key) == order
    with pytest.raises(InvalidInputError):
        upgrade_check.version_key("1.0")


def _wheel(tmp: Path, *, version: str = "0.2.0", schema: int = 31, data_format: int = 1,
           min_client: str = "0.1.0.dev0", lock: dict[str, str] | None = None,
           digest: str = "d" * 64, name: str = "w.whl") -> Path:  # fmt: skip
    info = {"product": "email-classify-filter", "version": version, "api_version": 1,
            "data_format": data_format, "schema_version": schema,
            "min_client": min_client}  # fmt: skip
    path = tmp / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ecf_server/data/release.json", json.dumps(info))
        z.writestr("ecf_server/data/models.lock", json.dumps(lock or LOCK))
        z.writestr("ecf_server/data/ollama.lock", json.dumps({"digest": digest}))
    return path


def test_read_wheel(tmp_path: Path) -> None:
    rel = upgrade_check.read_wheel(_wheel(tmp_path))
    assert rel.version == "0.2.0" and rel.pins["local"] == "d" * 64
    assert rel.pins["actor_high"] == "claude-opus-5-5"
    junk = tmp_path / "junk.whl"
    junk.write_bytes(b"not a zip")
    with pytest.raises(InvalidInputError, match="isn't an ecf wheel"):
        upgrade_check.read_wheel(junk)
    with pytest.raises(InvalidInputError, match="isn't a wheel file"):
        upgrade_check.read_wheel(tmp_path / "missing.whl")


def _receipt(prefix: Path, wheel: Path | None, name: str = "email-classify-filter") -> None:
    prefix.mkdir(parents=True, exist_ok=True)
    path = f', path = "{wheel}"' if wheel else ""
    (prefix / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "{name}"{path} }}]\n'
    )


def test_install_detection(tmp_path: Path) -> None:
    env = tmp_path / "env"
    env.mkdir()
    assert "isn't installed with `uv tool`" in str(upgrade_check.install_problem(
        upgrade_check.this_install(env)))  # fmt: skip
    old = _wheel(tmp_path, name="old.whl")
    _receipt(env, old)
    inst = upgrade_check.this_install(env)
    assert upgrade_check.install_problem(inst) is None and inst.wheel == old
    old.unlink()
    assert "is gone" in str(upgrade_check.install_problem(upgrade_check.this_install(env)))
    _receipt(env, None, name="other-tool")
    assert "isn't email-classify-filter's" in str(
        upgrade_check.install_problem(upgrade_check.this_install(env)))  # fmt: skip


def _state(**over: Any) -> dict[str, Any]:
    return {"version": "0.1.0", "schema_version": 31, "data_format": 1, "api_version": 1,
            "pins": LOCK | {"local": "d" * 64}, "pin_users": {"local": ["ap"], "actor": ["b"]},
            "install_role": "test",
            "busy": {"executing": 0, "leases": 0, "claude_sessions": 0}} | over  # fmt: skip


def test_compare(tmp_path: Path) -> None:
    ok = upgrade_check.compare(_state(), upgrade_check.read_wheel(_wheel(tmp_path)), "0.1.0")
    assert ok.problems == [] and ok.pin_changes == [] and ok.affected == []
    cases = [
        (_wheel(tmp_path, version="0.1.0", name="a.whl"), "isn't newer"),
        (_wheel(tmp_path, schema=30, name="b.whl"), "schema (30) is older"),
        (_wheel(tmp_path, data_format=3, name="c.whl"), "data format 3"),
        (_wheel(tmp_path, min_client="0.2.0", name="d.whl"), "needs ecf 0.2.0"),
    ]
    for wheel, why in cases:
        r = upgrade_check.compare(_state(), upgrade_check.read_wheel(wheel), "0.1.0")
        assert any(why in p for p in r.problems), (why, r.problems)
    pins = upgrade_check.compare(
        _state(), upgrade_check.read_wheel(_wheel(tmp_path, digest="e" * 64,
                                                  lock=LOCK | {"actor": "claude-sonnet-5-6"},
                                                  name="e.whl")), "0.1.0")  # fmt: skip
    assert pins.pin_changes == ["actor", "local"] and pins.affected == ["ap", "b"]


def test_service_state(conn: sqlite3.Connection, clock: FakeClock) -> None:
    with write_tx(conn), db.items_writer("create"):
        for aid, preset in (("a", "A"), ("b", "B"), ("c", "C")):
            conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset,"
                         " created_at) VALUES (?, ?, 'standard', ?, 't')",
                         (aid, f"{aid}@acme.example", preset))  # fmt: skip
        conn.execute("INSERT INTO items (stable_id, address_id, uid, uidvalidity, status,"
                     " content_hash, created_at, updated_at) VALUES ('aa00bb11', 'a', 1, 1,"
                     " 'executing', 'h', 't', 't')")  # fmt: skip
        conn.execute("INSERT INTO leases (address_id, holder, fencing_token, expires_at) VALUES"
                     " ('a', 'w', 1, ?)", (to_ts(clock.now().replace(year=2100)),))  # fmt: skip
    s = upgrade_state.state(conn, clock, api_version=1, sessions=1)
    assert s["version"] == __version__ and s["schema_version"] == 31
    assert s["pin_users"]["local"] == ["a", "b"]
    assert s["pin_users"]["actor"] == ["b", "c"] and s["pin_users"]["classifier"] == ["c"]
    assert s["busy"] == {"executing": 1, "leases": 1, "claude_sessions": 1}


def test_snapshot_and_retention(conn: sqlite3.Connection, tmp_path: Path) -> None:
    paths = Paths("t", tmp_path / "home")
    c = db.connect(paths.db)
    db.migrate(c)
    c.close()
    made: list[Path] = []
    for i, label in enumerate(("0.1.0-to-0.1.1", "0.1.1-to-0.1.2", "0.1.2-to-0.2.0")):
        d = upgrade_snapshot.take(paths, label)
        os.utime(d, (time.time() + i, time.time() + i))
        made.append(d)
    left = sorted(p.name for p in upgrade_snapshot.folder(paths).iterdir())
    assert left == ["0.1.1-to-0.1.2", "0.1.2-to-0.2.0"]
    db_file = made[-1] / "ecf.db"
    assert oct(db_file.stat().st_mode & 0o777) == "0o600"
    assert oct(made[-1].stat().st_mode & 0o777) == "0o700"
    copy = sqlite3.connect(db_file)
    assert copy.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 31
    copy.close()
    with pytest.raises(InvalidInputError):
        upgrade_snapshot.take(paths, "../escape")


def test_cli(conn: sqlite3.Connection, db_path: Path, tmp_path: Path,
             monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    del conn
    st = make_state(db_path, None)

    def client(_paths: Paths) -> ApiClient:
        return ApiClient(st)

    env = tmp_path / "env"
    _receipt(env, _wheel(tmp_path, name="installed.whl"))
    monkeypatch.setattr(ecf.cli_upgrade, "LocalClient", client)
    real = upgrade_check.this_install

    def installed(prefix: Path | None = None) -> upgrade_check.Install:
        del prefix
        return real(env)

    monkeypatch.setattr(upgrade_check, "this_install", installed)
    runner = CliRunner()
    r = runner.invoke(app, ["--install", "t", "upgrade"])
    assert r.exit_code == 1 and "no ecf release index" in r.output
    r = runner.invoke(app, ["--install", "t", "upgrade", "--to", "0.1.0"])
    assert r.exit_code == 1 and "11c" in r.output
    new = _wheel(tmp_path, version="9.0.0", name="new.whl")
    r = runner.invoke(app, ["--install", "t", "upgrade", "--wheel", str(new), "--check"])
    assert r.exit_code == 0, r.output
    assert f"ecf {__version__} → 9.0.0" in r.output and "checks passed" in r.output
    r = runner.invoke(app, ["--install", "t", "upgrade", "--wheel", str(new)], input="n\n")
    assert r.exit_code == 1 and "Upgrade to 9.0.0?" in r.output  # asks before stopping anything
    c = db.connect(db_path)
    with write_tx(c):
        c.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                  " ('install_role', '\"prod\"', 't', 't')")  # fmt: skip
    c.close()
    r = runner.invoke(app, ["--install", "t", "upgrade", "--wheel", str(new), "--check"])
    assert r.exit_code == 1 and "prod install upgrades only from published releases" in r.output
