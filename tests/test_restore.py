"""`ecf restore` (V1.5 step 10a; OD-318, OD-362 to OD-373): a new computer and the same one, the
bundles it accepts, older bundles, the safety copy, the generation and the backup key, the resume
gate, the preview, step-up, the route and the CLI."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import ecf.cli_import
from ecf.cli import app
from ecf.errors import ConflictError, InvalidInputError, StepupRequiredError
from ecf.paths import Paths
from ecf_server import (
    backup_key,
    bundle_reader,
    db,
    export_keys,
    install_identity,
    manual_export,
    pause,
    restore,
    scheduled_export,
    stepup,
)
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.stepper import FakeStepper
from tests.test_addresses import TOKEN, call, make_state
from tests.test_export_keys import ApiClient
from tests.test_importer import _source, conn_dir  # pyright: ignore[reportPrivateUsage]
from tests.test_scheduled_export import ROOT, _set, _set_up  # pyright: ignore[reportPrivateUsage]

KEY_TEXT = backup_key.key_text(ROOT)
PASS = "maple orbit candle river stone"


@pytest.fixture
def fresh(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "new-computer" / "ecf.db")
    db.migrate(c)
    yield c
    c.close()


def _backup(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, *,
            data: bool = True) -> Path:  # fmt: skip
    if data:
        _source(conn)
    store = MemorySecretStore()
    out = tmp_path / "bundles"
    out.mkdir(exist_ok=True)
    _set_up(conn, store, out)
    r = scheduled_export.run(conn, clock, FakeNotifier(), store, conn_dir(conn), "t")
    assert r["ok"], r
    return out / r["file"]


def _restore(target: sqlite3.Connection, clock: FakeClock, path: Path, *,
             store: MemorySecretStore | None = None, older_ok: bool = False,
             n: FakeNotifier | None = None, secret: str = KEY_TEXT,
             key_text: str | None = None) -> dict[str, Any]:  # fmt: skip
    parsed = bundle_reader.read(target, str(path), secret, key_text)

    def go(nonce: str | None) -> dict[str, Any]:
        return restore.restore(target, clock, n or FakeNotifier(), store or MemorySecretStore(),
                               conn_dir(target), parsed, str(path), key_text or secret,
                               stopped=True, older_ok=older_ok, nonce=nonce)  # fmt: skip

    return _with_step_up(target, clock, go)


def _with_step_up(conn: sqlite3.Connection, clock: FakeClock,
                  fn: Callable[[str | None], dict[str, Any]]) -> dict[str, Any]:  # fmt: skip
    with pytest.raises(StepupRequiredError) as ei:
        fn(None)
    issued = stepup.issue(conn, clock, FakeStepper(), str(ei.value.extra["purpose"]),
                          ei.value.extra["target"])  # fmt: skip
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    return fn(issued.nonce_id)


def _one(c: sqlite3.Connection, sql: str, *args: Any) -> Any:
    return c.execute(sql, args).fetchone()[0]


def _settings(c: sqlite3.Connection) -> dict[str, Any]:
    return {r[0]: json.loads(r[1]) for r in c.execute("SELECT key, value FROM settings")}


# ---- a new computer -----------------------------------------------------------------------------


def test_restore_on_a_new_computer(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path,
                                   fresh: sqlite3.Connection) -> None:  # fmt: skip
    path = _backup(conn, clock, tmp_path)
    source_id = install_identity.install_id(conn)
    store, n = MemorySecretStore(), FakeNotifier()
    r = _restore(fresh, clock, path, store=store, n=n)
    assert r["counts"]["addresses"] == 1 and r["counts"]["items"] == 5
    assert r["safety_copy"] is None and r["generation"] == 1
    assert install_identity.install_id(fresh) == source_id  # the same install, adopted
    a = fresh.execute("SELECT * FROM addresses").fetchone()
    assert (a["paused"], a["stage"], a["outbound"]) == (1, "live", 1)  # stage and outbound kept
    assert _one(fresh, "SELECT count(*) FROM routes") == 1  # Slack routes come back
    assert _one(fresh, "SELECT count(*) FROM alerts") == 1
    assert _one(fresh, "SELECT count(*) FROM items WHERE origin IS NOT NULL") == 0
    status = dict(fresh.execute("SELECT stable_id, status FROM items").fetchall())
    assert sorted(status.values()) == ["awaiting_approval", "awaiting_approval", "executed",
                                       "failed_unknown", "needs_human"]  # fmt: skip
    assert _one(fresh, "SELECT count(*) FROM grants WHERE status = 'issued'") == 2
    s = _settings(fresh)
    assert s["slack_member_id"] == "U1"  # the whole settings table, unlike import
    assert s["alerts.routes"] == ["slack", "email"]
    assert export_keys.DIR_KEY not in s  # this computer's own: none yet
    assert s[restore.AWAITING_KEY] == ["ap"]
    d = backup_key.derive(ROOT)
    assert store.get(export_keys.SEED_NAME) == d.signing_seed.hex()  # backups go on (OD-364)
    assert s[export_keys.KEY_KEY]["verify_key"] == d.public.verify_key
    assert _one(fresh, "SELECT count(*) FROM audit WHERE event = 'test.event'") == 1
    data = json.loads(_one(fresh, "SELECT data FROM audit WHERE event = 'restore.completed'"))
    assert data["generation"] == 1 and data["safety_copy"] is False
    assert any("restored from a backup" in t for h, t in n.sent if "Security Notice" in h)
    assert not list(conn_dir(fresh).glob(".restore-*"))


# ---- the same computer --------------------------------------------------------------------------


def test_restore_on_the_same_computer(conn: sqlite3.Connection, clock: FakeClock,
                                      tmp_path: Path) -> None:  # fmt: skip
    path = _backup(conn, clock, tmp_path)
    clock.advance(3600)
    second = _backup(conn, clock, tmp_path, data=False)  # seq 2
    with write_tx(conn):
        conn.execute("INSERT INTO audit (ts, event, actor, outcome, data) VALUES ('t2',"
                     " 'after.backup', 'service', 'ok', '{}')")  # fmt: skip
    parsed = bundle_reader.read(conn, str(path), KEY_TEXT)
    preview = restore.check(conn, parsed)
    assert preview["older"] and preview["seq"] == 1 and preview["newest_seq_here"] == 2
    with pytest.raises(ConflictError, match="older than"):
        restore.restore(conn, clock, FakeNotifier(), MemorySecretStore(), conn_dir(conn), parsed,
                        str(path), KEY_TEXT, stopped=True, older_ok=False, nonce=None)  # fmt: skip
    gen_before = install_identity.generation(conn)
    r = _restore(conn, clock, path, older_ok=True)
    assert r["safety_copy"] and Path(r["safety_copy"]).is_file()
    assert oct(Path(r["safety_copy"]).stat().st_mode & 0o777) == "0o600"
    assert install_identity.generation(conn) == gen_before + 1
    assert scheduled_export.status_seq(conn) == 2  # never goes backwards
    assert _one(conn, "SELECT count(*) FROM audit WHERE event = 'after.backup'") == 1  # merged
    assert second.exists()


def test_bundles_restore_refuses(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path,
                                 fresh: sqlite3.Connection) -> None:  # fmt: skip
    path = _backup(conn, clock, tmp_path)
    # another install's bundle into an install with data
    _set(fresh, "install.id", "f" * 32)
    with write_tx(fresh):
        fresh.execute("INSERT INTO addresses (address_id, email, sensitivity, preset,"
                      " created_at) VALUES ('x', 'x@acme.example', 'standard', 'A',"
                      " 't')")  # fmt: skip
    parsed = bundle_reader.read(fresh, str(path), KEY_TEXT)
    with pytest.raises(ConflictError, match="another install"):
        restore.check(fresh, parsed)
    # stopped not confirmed
    parsed = bundle_reader.read(conn, str(path), KEY_TEXT)
    with pytest.raises(InvalidInputError, match="stopped"):
        restore.restore(conn, clock, FakeNotifier(), MemorySecretStore(), conn_dir(conn), parsed,
                        str(path), KEY_TEXT, stopped=False, older_ok=True, nonce=None)  # fmt: skip


def test_manual_bundles_need_a_signature_and_the_key(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, fresh: sqlite3.Connection
) -> None:
    _source(conn)
    out = tmp_path / "manual"
    out.mkdir()

    def export(name: str) -> str:
        target = {"path": str(out.resolve() / name)}
        issued = stepup.issue(conn, clock, FakeStepper(), "export_manual", target)
        stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
        manual_export.export(conn, clock, FakeNotifier(), store, conn_dir(conn), "t",
                             target["path"], PASS, nonce=issued.nonce_id)  # fmt: skip
        return target["path"]

    store = MemorySecretStore()
    unsigned = export("unsigned.ecfb")
    parsed = bundle_reader.read(fresh, unsigned, PASS, KEY_TEXT)
    with pytest.raises(InvalidInputError, match="signed by the backup key"):
        restore.check(fresh, parsed)
    _set_up(conn, store, tmp_path)
    signed = export("signed.ecfb")
    assert not restore_verified(fresh, signed, None)
    r = _restore(fresh, clock, Path(signed), secret=PASS, key_text=KEY_TEXT)
    assert r["counts"]["addresses"] == 1


def restore_verified(c: sqlite3.Connection, path: str, key: str | None) -> bool:
    return bundle_reader.read(c, path, PASS, key).typed_verified


def test_the_step_up_is_bound_to_the_bundle(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, fresh: sqlite3.Connection
) -> None:
    path = _backup(conn, clock, tmp_path)
    parsed = bundle_reader.read(fresh, str(path), KEY_TEXT)
    issued = stepup.issue(fresh, clock, FakeStepper(), "restore",
                          {"path": str(path), "sha256": "0" * 64})  # fmt: skip
    assert issued.prompt.startswith(f"ecf: restore this install from {path}")
    stepup.verify(fresh, clock, FakeStepper(), issued.nonce_id)
    with pytest.raises(StepupRequiredError):
        restore.restore(fresh, clock, FakeNotifier(), MemorySecretStore(), conn_dir(fresh),
                        parsed, str(path), KEY_TEXT, stopped=True, older_ok=False,
                        nonce=issued.nonce_id)  # fmt: skip
    assert _one(fresh, "SELECT count(*) FROM addresses") == 0


# ---- resume gate and preview --------------------------------------------------------------------


def test_resume_waits_for_a_passing_check(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, fresh: sqlite3.Connection
) -> None:
    path = _backup(conn, clock, tmp_path)
    _restore(fresh, clock, path)
    with pytest.raises(ConflictError, match="no mail check has passed"):
        pause.set_paused(fresh, clock, "ap", False, actor="t")
    assert pause.set_paused(fresh, clock, pause.ALL, False, actor="t") == []  # held, not resumed
    clock.advance(60)
    with write_tx(fresh):
        fresh.execute("INSERT INTO check_state (address_id, last_finished_at, last_status)"
                      " VALUES ('ap', ?, 'error')", (to_ts(clock.now()),))  # fmt: skip
    with pytest.raises(ConflictError):
        pause.set_paused(fresh, clock, "ap", False, actor="t")
    with write_tx(fresh):
        fresh.execute("UPDATE check_state SET last_status = 'ok'")
    assert pause.set_paused(fresh, clock, "ap", False, actor="t") == ["ap"]
    assert _settings(fresh)[restore.AWAITING_KEY] == []
    pause.set_paused(fresh, clock, "ap", True, actor="t")
    assert pause.set_paused(fresh, clock, "ap", False, actor="t") == ["ap"]  # no gate now


def test_preview_differences(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path) -> None:
    path = _backup(conn, clock, tmp_path)
    _set(conn, "org_domains", ["acme.example", "other.example"])
    with write_tx(conn):
        conn.execute("INSERT INTO senders (address_id, sender_hash, dmarc_pass_count,"
                     " confirmed_category) VALUES ('ap', 'h1', 1, 'invoice')")  # fmt: skip
    r = restore.check(conn, bundle_reader.read(conn, str(path), KEY_TEXT))
    assert [c["section"] for c in r["config_changes"]] == ["org_domains"]
    assert r["senders"] == {"added": 0, "removed": 1, "changed": 0}
    assert r["addresses"] == [{"address_id": "ap", "email": "ap@acme.example", "stage": "live",
                               "outbound": True}]  # fmt: skip


# ---- route and CLI ------------------------------------------------------------------------------


def test_route_and_cli(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path,
                       monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    path = _backup(conn, clock, tmp_path)
    tdb = tmp_path / "t2" / "ecf.db"
    c = db.connect(tdb)
    db.migrate(c)
    c.close()
    store = MemorySecretStore()
    st = make_state(tdb, store)
    st.stepper = FakeStepper()
    st.notifier = FakeNotifier()
    r = call(st, "POST", "/v1/restore", {"path": str(path), "secret": KEY_TEXT, "dry_run": True},
             TOKEN)  # fmt: skip
    assert r.status_code == 200 and r.json()["empty_target"] is True, r.text

    def client(_paths: Paths) -> ApiClient:
        return ApiClient(st)

    def typed(_prompt: str) -> str:
        return KEY_TEXT

    monkeypatch.setattr(ecf.cli_import, "LocalClient", client)
    monkeypatch.setattr(ecf.cli_import, "hidden", typed)
    runner = CliRunner()
    r = runner.invoke(app, ["--install", "t", "restore", str(path)], input="n\n")
    assert r.exit_code == 1 and "paused until resumed, stage live, outbound on" in r.output
    r = runner.invoke(app, ["--install", "t", "restore", str(path)], input="y\ny\n")
    assert r.exit_code == 0, r.output
    assert "restored 1 address(es) and 5 emails; generation 1" in r.output
    assert store.get(export_keys.SEED_NAME) is not None
