"""Applying an import (V1.5 step 9c; OD-355, OD-357 to OD-361): an empty target, --replace, what
changes on the way in, settings and config, the single transaction, step-up, the routes and the
CLI."""

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
    bundle_reader,
    config,
    db,
    export_bundle,
    importer,
    install_identity,
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

KEY_TEXT = __import__("ecf_server.backup_key", fromlist=["key_text"]).key_text(ROOT)
LABEL = json.dumps({"actions": [{"name": "label", "target": "invoice"}]})


@pytest.fixture
def target(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "target" / "ecf.db")
    db.migrate(c)
    yield c
    c.close()


def _source(conn: sqlite3.Connection) -> None:
    with write_tx(conn), db.items_writer("create"):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, stage, outbound,"
                     " preset, created_at) VALUES ('ap', 'ap@acme.example', 'standard', 'live', 1,"
                     " 'A', 't')")  # fmt: skip
        rows = [("00aa00bb", "awaiting_approval", LABEL), ("11aa00bb", "delayed", LABEL),
                ("22aa00bb", "executing", LABEL), ("33aa00bb", "executed", LABEL),
                ("44aa00bb", "awaiting_approval", None)]  # fmt: skip
        for i, (sid, status, proposal) in enumerate(rows):
            conn.execute("INSERT INTO items (stable_id, address_id, uid, uidvalidity, status,"
                         " content_hash, proposal, created_at, updated_at) VALUES (?, 'ap', ?, 1,"
                         " ?, 'h', ?, 't', 't')", (sid, i + 1, status, proposal))  # fmt: skip
        conn.execute("INSERT INTO routes (address_id, surface, route_ref) VALUES ('ap', 'slack',"
                     " 'C1')")  # fmt: skip
        conn.execute("INSERT INTO alerts (key, kind, address_id, detail, opened_at) VALUES"
                     " ('login_rejected:ap', 'login_rejected', 'ap', 'x', 't')")  # fmt: skip
        conn.execute("INSERT INTO audit (ts, event, actor, outcome, data) VALUES ('t',"
                     " 'test.event', 'service', 'ok', '{}')")  # fmt: skip
    _set(conn, "org_domains", ["acme.example"])
    _set(conn, "export_schedule", "weekly")
    _set(conn, "alerts.routes", ["slack", "email"])
    _set(conn, "slack_member_id", "U1")
    _set(conn, "stale_item_days", 45)


def _bundle(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path) -> Path:
    _source(conn)
    store = MemorySecretStore()
    out = tmp_path / "bundles"
    out.mkdir(exist_ok=True)
    _set_up(conn, store, out)
    r = scheduled_export.run(conn, clock, FakeNotifier(), store, conn_dir(conn), "t")
    assert r["ok"], r
    return out / r["file"]


def conn_dir(conn: sqlite3.Connection) -> Path:
    return Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent


def _import(target: sqlite3.Connection, clock: FakeClock, path: Path, *, replace: bool = False,
            typed: str | None = None, n: FakeNotifier | None = None) -> dict[str, Any]:  # fmt: skip
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)

    def go(nonce: str | None) -> dict[str, Any]:
        return importer.apply(target, clock, n or FakeNotifier(), conn_dir(target), "t", parsed,
                              str(path), replace=replace, typed_install=typed,
                              nonce=nonce)  # fmt: skip

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


# ---- table lists --------------------------------------------------------------------------------


def test_every_exported_table_is_copied_or_handled_apart() -> None:
    assert set(importer.COPY) | set(importer.NOT_COPIED) == set(export_bundle.INCLUDED)
    assert not set(importer.COPY) & set(importer.NOT_COPIED)


# ---- an import into an empty install ------------------------------------------------------------


def test_import_into_an_empty_install(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path,
                                      target: sqlite3.Connection) -> None:  # fmt: skip
    path = _bundle(conn, clock, tmp_path)
    source = install_identity.install_id(conn)
    n = FakeNotifier()
    r = _import(target, clock, path, n=n)
    assert r["counts"]["addresses"] == 1 and r["counts"]["items"] == 5
    assert r["counts"]["reposted"] == 2
    a = target.execute("SELECT * FROM addresses").fetchone()
    assert (a["paused"], a["outbound"], a["stage"]) == (1, 0, "assist")
    status = dict(target.execute("SELECT stable_id, status FROM items").fetchall())
    assert status == {"00aa00bb": "awaiting_approval", "11aa00bb": "awaiting_approval",
                      "22aa00bb": "failed_unknown", "33aa00bb": "executed",
                      "44aa00bb": "needs_human"}  # fmt: skip
    grants = target.execute("SELECT stable_id FROM grants WHERE status = 'issued'"
                            " ORDER BY stable_id").fetchall()  # fmt: skip
    assert [g[0] for g in grants] == ["00aa00bb", "11aa00bb"]
    assert _one(target, "SELECT count(*) FROM items WHERE origin = ?", source) == 5
    assert _one(target, "SELECT count(*) FROM audit WHERE origin = ? AND event = 'test.event'",
                source) == 1  # fmt: skip
    assert _one(target, "SELECT count(*) FROM routes") == 0
    assert _one(target, "SELECT count(*) FROM alerts") == 0
    assert _one(target, "SELECT count(*) FROM eval_runs WHERE path != ''") == 0
    # settings: the allow-list
    assert install_identity.install_id(target) != source  # the target keeps its own identity
    settings_now = {r[0]: json.loads(r[1]) for r in target.execute("SELECT key, value FROM"
                                                                   " settings")}  # fmt: skip
    assert settings_now["org_domains"] == ["acme.example"]
    assert settings_now["export_schedule"] == "weekly"
    assert settings_now["alerts.routes"] == ["slack"]
    assert settings_now["stale_item_days"] == 45
    assert "slack_member_id" not in settings_now and "export.key" not in settings_now
    assert config.current(target)["org_domains"] == ["acme.example"]
    # audit and notice
    data = json.loads(_one(target, "SELECT data FROM audit WHERE event = 'import.completed'"))
    assert data["source_install"] == source and data["signer"] == "typed_key"
    assert data["replace"] is False
    notice = next(t for h, t in n.sent if "Security Notice" in h)
    assert f"Data from install {source} was imported" in notice
    assert "org_domains" in notice and "export_schedule" in notice
    # the staging copy is gone
    assert not list(conn_dir(target).glob(".import-*"))


def test_a_non_empty_target_needs_replace_and_the_name(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, target: sqlite3.Connection
) -> None:
    path = _bundle(conn, clock, tmp_path)
    _import(target, clock, path)
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)
    with pytest.raises(ConflictError, match="--replace"):
        importer.apply(target, clock, FakeNotifier(), conn_dir(target), "t", parsed, str(path),
                       replace=False, typed_install=None, nonce=None)  # fmt: skip
    with pytest.raises(InvalidInputError, match="name typed"):
        importer.apply(target, clock, FakeNotifier(), conn_dir(target), "t", parsed, str(path),
                       replace=True, typed_install="other", nonce=None)  # fmt: skip
    with write_tx(target):
        target.execute("INSERT INTO addresses (address_id, email, sensitivity, preset,"
                       " created_at) VALUES ('old', 'old@acme.example', 'standard', 'A',"
                       " 't')")  # fmt: skip
    r = _import(target, clock, path, replace=True, typed="t")
    assert r["replace"] is True
    ids = [x[0] for x in target.execute("SELECT address_id FROM addresses")]
    assert ids == ["ap"]
    assert _one(target, "SELECT count(*) FROM items") == 5
    assert _one(target, "SELECT count(*) FROM grants WHERE status = 'issued'") == 2


def test_the_step_up_is_bound_to_the_bundle(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, target: sqlite3.Connection
) -> None:
    path = _bundle(conn, clock, tmp_path)
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)
    other = {"path": str(path), "sha256": "0" * 64, "replace": False}
    issued = stepup.issue(target, clock, FakeStepper(), "import", other)
    assert issued.prompt.startswith(f"ecf: import the bundle {path} (SHA-256 000000000000)")
    stepup.verify(target, clock, FakeStepper(), issued.nonce_id)
    with pytest.raises(StepupRequiredError):
        importer.apply(target, clock, FakeNotifier(), conn_dir(target), "t", parsed, str(path),
                       replace=False, typed_install=None, nonce=issued.nonce_id)  # fmt: skip
    assert _one(target, "SELECT count(*) FROM addresses") == 0


def test_a_bad_row_rolls_everything_back(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path, target: sqlite3.Connection
) -> None:
    path = _bundle(conn, clock, tmp_path)
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)
    parsed.tables["addresses"][0]["sensitivity"] = "extreme"  # breaks a CHECK constraint

    def go(nonce: str | None) -> dict[str, Any]:
        return importer.apply(target, clock, FakeNotifier(), conn_dir(target), "t", parsed,
                              str(path), replace=False, typed_install=None,
                              nonce=nonce)  # fmt: skip

    with pytest.raises(bundle_reader.BadBundleError, match="breaks a rule"):
        _with_step_up(target, clock, go)
    assert _one(target, "SELECT count(*) FROM addresses") == 0
    assert _one(target, "SELECT count(*) FROM settings WHERE key = 'org_domains'") == 0


def _setting(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": value, "updated_at": "t", "updated_by": "t"}


INVALID: dict[str, tuple[Callable[[bundle_reader.Parsed], object], str]] = {
    "address_id": (lambda p: p.tables["addresses"][0].update(address_id="Bad_ID"),
                   r"address ID|breaks"),
    "column": (lambda p: p.tables["addresses"][0].update(new_column=1), "column this schema"),
    "setting": (lambda p: p.tables["settings"].append(_setting("stale_item_days", "2")),
                "stale_item_days"),
    "config": (lambda p: p.tables["settings"].append(_setting("org_domains", '["gmail.com"]')),
               "config fails its checks"),
}  # fmt: skip


@pytest.mark.parametrize("case", sorted(INVALID))
def test_validation(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path,
                    target: sqlite3.Connection, case: str) -> None:  # fmt: skip
    change, why = INVALID[case]
    path = _bundle(conn, clock, tmp_path)
    parsed = bundle_reader.read(target, str(path), KEY_TEXT)
    change(parsed)

    def go(nonce: str | None) -> dict[str, Any]:
        return importer.apply(target, clock, FakeNotifier(), conn_dir(target), "t", parsed,
                              str(path), replace=False, typed_install=None,
                              nonce=nonce)  # fmt: skip

    with pytest.raises(bundle_reader.BadBundleError, match=why):
        _with_step_up(target, clock, go)
    assert _one(target, "SELECT count(*) FROM addresses") == 0


# ---- routes and CLI -----------------------------------------------------------------------------


def test_route_and_cli(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path,
                       monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    path = _bundle(conn, clock, tmp_path)
    tdb = tmp_path / "t2" / "ecf.db"
    c = db.connect(tdb)
    db.migrate(c)
    c.close()
    st = make_state(tdb, None)
    st.stepper = FakeStepper()
    st.notifier = FakeNotifier()
    r = call(st, "POST", "/v1/import", {"path": str(path), "secret": KEY_TEXT}, TOKEN)
    assert r.status_code == 403 and r.json()["code"] == "stepup_required"

    def client(_paths: Paths) -> ApiClient:
        return ApiClient(st)

    monkeypatch.setattr(ecf.cli_import, "LocalClient", client)

    def typed(_prompt: str) -> str:
        return KEY_TEXT

    monkeypatch.setattr(ecf.cli_import, "hidden", typed)
    runner = CliRunner()
    r = runner.invoke(app, ["--install", "t", "import", str(path)], input="n\n")
    assert r.exit_code == 1 and "arrives paused" in r.output
    r = runner.invoke(app, ["--install", "t", "import", str(path)], input="y\n")
    assert r.exit_code == 0, r.output
    assert "imported 1 address(es) and 5 emails; 2 approval(s) to decide again" in r.output
    r = runner.invoke(app, ["--install", "t", "import", str(path)], input="y\n")
    assert r.exit_code == 1 and "add --replace" in r.output
    r = runner.invoke(app, ["--install", "t", "import", str(path), "--replace"], input="y\nt\n")
    assert r.exit_code == 0, r.output
