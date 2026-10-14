"""Migration 0038, local models removed (v3.0.0; ADR 0031, OD-506; plan REMOVE-LOCAL-MODELS R1b):
the no-FK migrate path (P1), the conversion of A and B addresses to C, paused (P3, P8), the
dropped columns and table, the in-flight items (P12), the alert and settings rows (P11), the
announcement at start and after a restore (P3, P12), and older bundles (P2, P8)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ecf_server import bundle_reader, db, export_bundle, import_plan, scheduled_export, stages
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore.memory import MemorySecretStore
from tests.test_importer import KEY_TEXT, _import, conn_dir  # pyright: ignore[reportPrivateUsage]
from tests.test_restore import _restore  # pyright: ignore[reportPrivateUsage]
from tests.test_scheduled_export import _set_up  # pyright: ignore[reportPrivateUsage]

OLD = "2026-10-01T00:00:00.000000Z"
REMOVED_KINDS = ("local_model", "local_model_unsafe", "model_failures", "local_model_server")
DEAD_SETTINGS = ("resident", "ollama_log_max_mb", "ollama_log_rotate_days",
                 "daily.model_backlog", "daily.battery_seconds")  # fmt: skip
# every table with a foreign key to `addresses` or `items`, besides those two
CHILDREN = ("check_state", "cursors", "downloads", "leases", "probe", "processing", "routes",
            "grants", "escalations", "excerpts", "delays")  # fmt: skip


def _before(db_path: Path, monkeypatch: pytest.MonkeyPatch,
            version: int = 38) -> sqlite3.Connection:  # fmt: skip
    """A database migrated to just before `version`; the next `db.migrate` runs the rest."""
    every = db._migration_files()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(db, "_migration_files", lambda: [m for m in every if m[0] < version])
    c = db.connect(db_path)
    db.migrate(c)
    monkeypatch.setattr(db, "_migration_files", lambda: every)
    return c


def _address(c: sqlite3.Connection, aid: str, preset: str, *, stage: str = "shadow",
             removed: bool = False) -> None:  # fmt: skip
    c.execute("INSERT INTO addresses (address_id, email, sensitivity, stage, preset, created_at,"
              " removed_at, fallback_enabled, claude_queue_timeout_h, fallback_since) VALUES"
              " (?, ?, 'standard', ?, ?, ?, ?, ?, ?, ?)",
              (aid, f"{aid}@acme.example", stage, preset, OLD, OLD if removed else None,
               int(preset == "C"), 6 if preset == "C" else None,
               OLD if preset == "C" else None))  # fmt: skip


def _item(c: sqlite3.Connection, sid: str, aid: str, status: str, **cols: Any) -> None:
    names = ["stable_id", "address_id", "uid", "uidvalidity", "status", "content_hash",
             "created_at", "updated_at", *cols]  # fmt: skip
    values = [sid, aid, abs(hash(sid)) % 10_000, 1, status, "h", OLD, OLD, *cols.values()]
    with db.items_writer("create"):
        c.execute(f"INSERT INTO items ({', '.join(names)}) VALUES"  # noqa: S608 - test names
                  f" ({', '.join('?' * len(names))})", values)  # fmt: skip


def _populate(c: sqlite3.Connection) -> None:
    """A, B and C addresses (a removed A too), items in flight, and a row in every child table."""
    with write_tx(c):
        _address(c, "a1", "A", stage="live")
        _address(c, "b1", "B", stage="assist")
        _address(c, "c1", "C", stage="live")
        _address(c, "old", "A", removed=True)
        _item(c, "aa000001", "a1", "new", model_attempts=2, model_failed=1, model_failed_at=OLD)
        _item(c, "aa000002", "a1", "awaiting_approval")
        _item(c, "cc000001", "c1", "awaiting_claude", claude_since=OLD, fallback_at=OLD)
        _item(c, "cc000002", "c1", "awaiting_claude", claude_since=OLD)
        _item(c, "cc000003", "c1", "executed", fallback_at=OLD)
        _item(c, "dd000001", "old", "executed")
        for aid in ("a1", "b1", "c1"):
            c.execute("INSERT INTO check_state (address_id) VALUES (?)", (aid,))
            c.execute("INSERT INTO cursors (address_id, last_uid) VALUES (?, 9)", (aid,))
            c.execute("INSERT INTO downloads (address_id, hour, bytes) VALUES (?, 'h', 5)", (aid,))
            c.execute("INSERT INTO probe (address_id, probed_at) VALUES (?, ?)", (aid, OLD))
            c.execute("INSERT INTO processing (address_id, uidvalidity, uid, started_at) VALUES"
                      " (?, 1, 1, ?)", (aid, OLD))  # fmt: skip
            c.execute("INSERT INTO routes (address_id, surface, route_ref) VALUES (?, 'slack',"
                      " 'C1')", (aid,))  # fmt: skip
        c.execute("INSERT INTO leases (address_id, holder, fencing_token, expires_at) VALUES"
                  " ('a1', 'w', 1, ?)", (OLD,))  # fmt: skip
        c.execute("INSERT INTO grants (grant_id, stable_id, action_hash, content_hash, principal,"
                  " status, expires_at) VALUES ('g1', 'aa000002', 'a', 'h', 'p', 'issued', ?)",
                  (OLD,))  # fmt: skip
        c.execute("INSERT INTO escalations (stable_id, address_id, state, created_at) VALUES"
                  " ('aa000001', 'a1', 'pending', ?)", (OLD,))  # fmt: skip
        c.execute("INSERT INTO excerpts (stable_id, classifier_text) VALUES ('aa000001', 'x')")
        c.execute("INSERT INTO delays (stable_id, grant_id, remaining_s, created_at) VALUES"
                  " ('aa000002', 'g1', 5, ?)", (OLD,))  # fmt: skip
        c.execute("INSERT INTO fallback_shadow (stable_id, address_id, digest, outcome,"
                  " created_at) VALUES ('cc000002', 'c1', 'd', 'ok', ?)", (OLD,))  # fmt: skip
        for aid, key, digest, ids in (
            ("a1", "local/A", "d" * 64, None),
            ("b1", "local/B", None, '{"actor": "x"}'),
            ("c1", "claude/claude", None, '{"classifier": "y"}'),
        ):
            c.execute("INSERT INTO gate (address_id, pair_key, reviewed, pinned_ids,"
                      " ollama_digest) VALUES (?, ?, 3, ?, ?)",
                      (aid, key, ids, digest))  # fmt: skip
        for kind in (*REMOVED_KINDS, "models_missing", "login_rejected"):
            c.execute("INSERT INTO alerts (key, kind, address_id, detail, opened_at) VALUES"
                      " (?, ?, NULL, 'x', ?)", (f"{kind}:x", kind, OLD))  # fmt: skip
        for key in (*DEAD_SETTINGS, "stale_item_days"):
            c.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                      " (?, '1', ?, 't')", (key, OLD))  # fmt: skip


def _counts(c: sqlite3.Connection) -> dict[str, int]:
    return {t: c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]  # noqa: S608
            for t in ("addresses", "items", "gate", *CHILDREN)}  # fmt: skip


def _columns(c: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in c.execute(f'PRAGMA table_info("{table}")')}


@pytest.fixture
def fresh(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "new-computer" / "ecf.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def target(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "target" / "ecf.db")
    db.migrate(c)
    yield c
    c.close()


# ---- the migration ------------------------------------------------------------------------------


def test_0038_runs_without_foreign_keys_and_checkpoints() -> None:
    assert 38 in db.NO_FK and 38 in db.CHECKPOINT_AFTER


def test_0038_converts_a_and_b_and_keeps_every_row(
        db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    c = _before(db_path, monkeypatch)
    _populate(c)
    before = _counts(c)
    assert db.migrate(c) == ["0038_local_models_removed.sql"]
    assert _counts(c) == before  # the rebuild lost nothing, and no child was cascaded away
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # back on
    rows = {r["address_id"]: r for r in c.execute("SELECT * FROM addresses")}
    assert {a: (r["preset"], r["paused"], r["stage"]) for a, r in rows.items()} == {
        "a1": ("C", 1, "live"),
        "b1": ("C", 1, "assist"),
        "c1": ("C", 0, "live"),
        "old": ("C", 1, "shadow"),
    }  # the stage moves at the next start (announce_converted)
    assert rows["old"]["removed_at"] == OLD
    gone = {"fallback_enabled", "claude_queue_timeout_h", "fallback_since"}
    assert not gone & _columns(c, "addresses")
    assert {"smtp_host", "outbound_dismissed_at", "overrides"} <= _columns(c, "addresses")
    assert not {"fallback_at", "model_attempts", "model_failed",
                "model_failed_at"} & _columns(c, "items")  # fmt: skip
    assert "ollama_digest" not in _columns(c, "gate")
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
    assert "fallback_shadow" not in names and "items_model_waiting" not in names
    assert "items_open" in names
    since = dict(c.execute("SELECT stable_id, claude_since FROM items WHERE address_id = 'c1'"))
    assert since["cc000001"] > OLD  # handed off to the fallback, still waiting: from now (P12)
    assert since["cc000002"] == OLD and since["cc000003"] is None
    kinds = {r[0] for r in c.execute("SELECT kind FROM alerts")}
    assert kinds == {"models_missing", "login_rejected"}  # models_missing: the Claude pins'
    keys = {r[0] for r in c.execute("SELECT key FROM settings")}
    assert not set(DEAD_SETTINGS) & keys and "stale_item_days" in keys
    marker = c.execute("SELECT value FROM settings WHERE key = ?", (stages.CONVERTED,)).fetchone()
    assert json.loads(marker[0]) == {"a1": "A", "b1": "B"}  # not the removed one
    assert c.execute("SELECT count(*) FROM gate").fetchone()[0] == 3  # kept (R-D8, P13)
    with pytest.raises(sqlite3.IntegrityError):  # CHECK (preset IN ('C', 'D'))
        c.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                  " VALUES ('x', 'x@acme.example', 'standard', 'A', 't')")  # fmt: skip
    with pytest.raises(sqlite3.IntegrityError):  # foreign keys are enforced again
        _item(c, "ee000001", "ghost", "new")
    c.close()


def test_0038_with_no_a_or_b_address_writes_no_marker(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT 1 FROM settings WHERE key = ?", (stages.CONVERTED,)).fetchone()
    assert row is None and stages.converted(conn) == []


def test_a_failing_foreign_key_check_rolls_the_migration_back(
        db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    c = _before(db_path, monkeypatch)
    with write_tx(c):
        _address(c, "a1", "A")
    c.execute("PRAGMA foreign_keys=OFF")
    with write_tx(c):
        _item(c, "ee000001", "ghost", "new")  # a row whose address is missing
    c.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(db.MigrationError, match="parent is missing"):
        db.migrate(c)
    assert c.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 37
    assert "fallback_enabled" in _columns(c, "addresses")
    assert c.execute("SELECT preset FROM addresses").fetchone()[0] == "A"
    assert c.execute("SELECT count(*) FROM fallback_shadow").fetchone()[0] == 0
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1 and not c.in_transaction
    c.close()


def test_a_no_fk_migration_refuses_inside_a_transaction(
        db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    c = _before(db_path, monkeypatch)
    c.execute("BEGIN")
    with pytest.raises(db.MigrationError, match="inside a transaction"):
        db.migrate(c)
    c.execute("ROLLBACK")
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.migrate(c) == ["0038_local_models_removed.sql"]
    c.close()


# ---- the announcement ---------------------------------------------------------------------------


def _audit(c: sqlite3.Connection, event: str) -> list[dict[str, Any]]:
    return [json.loads(r[0]) for r in c.execute("SELECT data FROM audit WHERE event = ?"
                                                " ORDER BY id", (event,))]  # fmt: skip


def test_converted_addresses_are_announced_once(db_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                clock: FakeClock) -> None:  # fmt: skip
    c = _before(db_path, monkeypatch)
    _populate(c)
    db.migrate(c)
    assert stages.converted(c) == [
        {"address_id": "a1", "from": "A", "paused": True, "consented": False, "waiting": 1},
        {"address_id": "b1", "from": "B", "paused": True, "consented": False, "waiting": 0},
    ]
    n = FakeNotifier()
    assert stages.announce_converted(c, clock, n) == ["a1", "b1"]
    stage = dict(c.execute("SELECT address_id, stage FROM addresses"))
    assert stage["a1"] == "assist" and stage["b1"] == "assist" and stage["c1"] == "live"
    moved = _audit(c, "stage.changed")
    assert moved == [{"from": "live", "to": "assist", "reason": stages.CONVERTED_REASON}]
    assert [d["from"] for d in _audit(c, "address.converted")] == ["A", "B"]
    notices = [body for title, body in n.sent if title == "[ecf-alert] Security Notice"]
    assert len(notices) == 2
    assert "a1" in notices[0] and "OD-427" in notices[0] and "1 email of it wait" in notices[0]
    assert "ecf resume a1" in notices[0] and "ecf address remove a1" in notices[0]
    assert stages.announce_converted(c, clock, FakeNotifier()) == []  # once
    c.close()


def test_the_start_step_runs_at_service_start() -> None:
    """`_open_state` calls it, before a stored extension's activation (its own reason first)."""
    import inspect  # noqa: PLC0415

    from ecf_server import service  # noqa: PLC0415

    src = inspect.getsource(service.Service._open_state)  # pyright: ignore[reportPrivateUsage]
    assert src.index("stages.announce_converted(") < src.index("config.activate_stored(")


# ---- older bundles ------------------------------------------------------------------------------


def _old_bundle(db_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock, tmp_path: Path,
                *, version: int, data_format: int) -> Path:  # fmt: skip
    """A bundle from a database at schema `version - 1` with an A and a B address."""
    c = _before(db_path, monkeypatch, version)
    every = db._migration_files()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(db, "_migration_files", lambda: [m for m in every if m[0] < version])
    with write_tx(c), db.items_writer("create"):
        for aid, preset in (("a1", "A"), ("b1", "B")):
            c.execute("INSERT INTO addresses (address_id, email, sensitivity, stage, preset,"
                      " created_at) VALUES (?, ?, 'standard', 'live', ?, ?)",
                      (aid, f"{aid}@acme.example", preset, OLD))  # fmt: skip
        extra = ", model_attempts, model_failed" if version > 19 else ""
        c.execute(f"INSERT INTO items (stable_id, address_id, uid, uidvalidity, status,"  # noqa: S608
                  f" content_hash, created_at, updated_at{extra}) VALUES ('aa000001', 'a1', 1, 1,"
                  f" 'new', 'h', ?, ?{', 2, 1' if extra else ''})", (OLD, OLD))  # fmt: skip
    store = MemorySecretStore()
    out = tmp_path / "bundles"
    out.mkdir(exist_ok=True)
    _set_up(c, store, out)
    monkeypatch.setattr(export_bundle, "DATA_FORMAT", data_format)
    r = scheduled_export.run(c, clock, FakeNotifier(), store, conn_dir(c), "t")
    assert r["ok"], r
    monkeypatch.setattr(export_bundle, "DATA_FORMAT", 4)
    monkeypatch.setattr(db, "_migration_files", lambda: every)
    c.close()
    return out / r["file"]


def test_a_v2_2_3_bundle_restores_converted_and_announces_again(
        db_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock, tmp_path: Path,
        fresh: sqlite3.Connection) -> None:  # fmt: skip
    """v2.2.3's shape: schema 35, data format 3 (P2). Each restore of it announces again (P12)."""
    path = _old_bundle(db_path, monkeypatch, clock, tmp_path, version=36, data_format=3)
    m = bundle_reader.read(fresh, str(path), KEY_TEXT).manifest
    assert (m["data_format"], m["schema_version"]) == (3, 35)
    n = FakeNotifier()
    _restore(fresh, clock, path, n=n)
    rows = {r["address_id"]: (r["preset"], r["paused"])
            for r in fresh.execute("SELECT * FROM addresses")}  # fmt: skip
    assert rows == {"a1": ("C", 1), "b1": ("C", 1)}
    assert fresh.execute("PRAGMA foreign_key_check").fetchall() == []
    assert [c["address_id"] for c in stages.converted(fresh)] == ["a1", "b1"]
    said = [b for _t, b in n.sent if "now preset C" in b]
    assert len(said) == 2
    assert dict(fresh.execute("SELECT address_id, stage FROM addresses")) == {
        "a1": "assist",
        "b1": "assist",
    }
    again = FakeNotifier()
    _restore(fresh, clock, path, n=again)  # the bundle's settings replace the record
    assert len([b for _t, b in again.sent if "now preset C" in b]) == 2


def test_a_format_4_bundle_with_a_and_b_imports_as_c_paused(
        db_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock, tmp_path: Path,
        target: sqlite3.Connection) -> None:  # fmt: skip
    """P8: schema 37, format 4, A and B addresses and the model queue's columns set."""
    path = _old_bundle(db_path, monkeypatch, clock, tmp_path, version=38, data_format=4)
    m = bundle_reader.read(target, str(path), KEY_TEXT).manifest
    assert (m["data_format"], m["schema_version"]) == (4, 37)
    preview = import_plan.preview(target, bundle_reader.read(target, str(path), KEY_TEXT))
    assert preview["converted"] == ["a1", "b1"]
    assert all("converted to C, paused" in a["note"] for a in preview["addresses"])
    n = FakeNotifier()
    r = _import(target, clock, path, n=n)
    assert r["converted"] == ["a1", "b1"]
    rows = {a["address_id"]: (a["preset"], a["paused"], a["stage"])
            for a in target.execute("SELECT * FROM addresses")}  # fmt: skip
    assert rows == {"a1": ("C", 1, "assist"), "b1": ("C", 1, "assist")}
    assert "model_failed" not in _columns(target, "items")
    bodies = [b for _t, b in n.sent]
    assert any("Converted to C, paused" in b and "a1, b1" in b for b in bodies)
    assert len([b for b in bodies if "now preset C" in b]) == 2
    assert [c["waiting"] for c in stages.converted(target)] == [1, 0]
