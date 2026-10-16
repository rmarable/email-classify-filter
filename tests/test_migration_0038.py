"""Migration 0038, local models removed (v3.0.0; ADR 0031, OD-506; plan REMOVE-LOCAL-MODELS R1b):
the no-FK migrate path (P1), the conversion of A and B addresses to C, paused (P3, P8), the
dropped columns and table, the in-flight items (P12), the alert and settings rows (P11), the
announcement at start and after a restore (P3, P12), and older bundles (P2, P8)."""

from __future__ import annotations

import inspect
import json
import re
import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from ecf_server import (
    addresses,
    bundle_reader,
    claude_queue,
    db,
    export_bundle,
    import_plan,
    importer,
    pause,
    scheduled_export,
    stages,
)
from ecf_server.bundle_reader import BadBundleError
from ecf_server.clock import FakeClock, to_ts
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


# The migration: it runs without foreign keys and checkpoints, converts A and B addresses keeping
# every row, and rolls back when the foreign-key check fails.


def test_0038_runs_without_foreign_keys_and_checkpoints() -> None:
    assert 38 in db.NO_FK and 38 in db.CHECKPOINT_AFTER


def test_0038_converts_a_and_b_and_keeps_every_row(
        db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    c = _before(db_path, monkeypatch)
    _populate(c)
    before = _counts(c)
    assert db.migrate(c, up_to=38) == ["0038_local_models_removed.sql"]
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
    # kept until v4: a DROP COLUMN rewrites the whole table (review R1)
    assert set(db.UNUSED_COLUMNS["items"]) <= _columns(c, "items")
    assert "ollama_digest" not in _columns(c, "gate")
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
    assert "fallback_shadow" not in names and "items_model_waiting" not in names
    assert {"items_open", "items_claude_waiting"} <= names
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
    with pytest.raises(db.MigrationError, match="parent is missing") as ei:
        db.migrate(c)
    assert "items rowid 1 -> addresses" in str(ei.value)  # named (R4)
    assert "previous version" in str(ei.value)  # and what happens next
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
    assert db.migrate(c, up_to=38) == ["0038_local_models_removed.sql"]
    c.close()


# The announcement: converted addresses are announced once, by a step that runs at service start.


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
    assert "a1 was set up for a local model ecf no longer has" in notices[0]
    assert "1 email waiting" in notices[0] and "OD-427" not in notices[0]
    assert "ecf resume a1" in notices[0] and "ecf address remove a1" in notices[0]
    assert stages.announce_converted(c, clock, FakeNotifier()) == []  # once
    c.close()


def test_the_start_step_runs_at_service_start() -> None:
    """`_open_state` calls it, before a stored extension's activation (its own reason first)."""
    import inspect  # noqa: PLC0415

    from ecf_server import service  # noqa: PLC0415

    src = inspect.getsource(service.Service._open_state)  # pyright: ignore[reportPrivateUsage]
    assert src.index("stages.announce_converted(") < src.index("config.activate_stored(")


# Older bundles: a v2.2.3 bundle restores as converted and is announced again; a format-4 bundle
# with A or B addresses imports as C, paused.


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
    said = [b for _t, b in n.sent if "for a local model ecf no longer has" in b]
    assert len(said) == 2
    assert dict(fresh.execute("SELECT address_id, stage FROM addresses")) == {
        "a1": "assist",
        "b1": "assist",
    }
    again = FakeNotifier()
    _restore(fresh, clock, path, n=again)  # the bundle's settings replace the record
    assert len([b for _t, b in again.sent if "for a local model ecf no longer has" in b]) == 2


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
    row = target.execute("SELECT model_attempts, model_failed FROM items").fetchone()
    assert tuple(row) == (0, 0)  # the bundle's values aren't carried (R1)
    bodies = [b for _t, b in n.sent]
    assert any("Converted to C, paused" in b and "a1, b1" in b for b in bodies)
    assert len([b for b in bodies if "for a local model ecf no longer has" in b]) == 2
    assert [c["waiting"] for c in stages.converted(target)] == [1, 0]


# Fixes from the diff review (R1-R11), one test each: exports, foreign-key checks and rollback, the
# Claude queue index, the announcement's side effects and import edge cases.


def test_the_unused_columns_are_never_exported(conn: sqlite3.Connection, clock: FakeClock) -> None:
    """R1: the four items columns stay in the schema until v4; a bundle never carries them."""
    with write_tx(conn):
        conn.execute(
            "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at) VALUES"
            " ('c1', 'c1@acme.example', 'standard', 'C', ?)", (OLD,))  # fmt: skip
        _item(conn, "cc000001", "c1", "new")
    rows = export_bundle._rows(conn, "items")  # pyright: ignore[reportPrivateUsage]
    assert rows and not set(db.UNUSED_COLUMNS["items"]) & set(rows[0])
    assert "claude_since" in rows[0]


def test_only_the_rebuilt_tables_children_are_checked(
        db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """R4: an orphan that doesn't point at `addresses` (an excerpt whose item is gone) doesn't fail
    0038; one that does is named."""
    c = _before(db_path, monkeypatch)
    with write_tx(c):
        _address(c, "a1", "A")
    c.execute("PRAGMA foreign_keys=OFF")
    with write_tx(c):
        c.execute("INSERT INTO excerpts (stable_id, classifier_text) VALUES ('gone0001', 'x')")
    c.execute("PRAGMA foreign_keys=ON")
    assert db.migrate(c, up_to=38) == ["0038_local_models_removed.sql"]
    assert db.orphans(c, "addresses") == []
    assert ("excerpts", 1, "items") in [tuple(r)[:3] for r in c.execute("PRAGMA foreign_key_check")]
    c.close()


class _FailingRollback(sqlite3.Connection):
    """A connection whose first ROLLBACK fails, as one can after SQLITE_FULL (R10)."""

    fail = False
    seen: list[str]

    def execute(self, sql: str, *args: Any) -> sqlite3.Cursor:  # type: ignore[override]
        self.seen.append(sql)
        if self.fail and sql == "ROLLBACK":
            self.fail = False
            raise sqlite3.OperationalError("disk I/O error")
        return super().execute(sql, *args)


def test_foreign_keys_come_back_after_a_failed_rollback(
        db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """R10: the migration's own transaction is rolled back and the setting restored, best
    effort, even when write_tx's ROLLBACK failed."""
    _before(db_path, monkeypatch).close()
    c = sqlite3.connect(db_path, autocommit=True, factory=_FailingRollback)
    c.seen = []
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=OFF")
    with write_tx(c):
        c.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                  " VALUES ('a1', 'a1@acme.example', 'standard', 'A', ?)", (OLD,))  # fmt: skip
        c.execute("INSERT INTO cursors (address_id, last_uid) VALUES ('ghost', 1)")
    c.execute("PRAGMA foreign_keys=ON")
    c.fail = True
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        db.migrate(c)
    assert c.seen.count("ROLLBACK") == 2  # write_tx's failed; _apply's own went through
    assert not c.in_transaction and c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 37
    c.close()


def test_the_claude_queue_index_serves_the_ticks_queries(conn: sqlite3.Connection) -> None:
    """R2: backlog, overdue, waiting and the review queue read `items_claude_waiting`."""
    from ecf_server import claude_review  # noqa: PLC0415

    in_queue = claude_queue._IN_QUEUE  # pyright: ignore[reportPrivateUsage]
    sources = [
        "SELECT count(*) FROM items i JOIN addresses a USING (address_id) WHERE a.removed_at IS"  # noqa: S608
        f" NULL AND a.paused = 0 AND {in_queue} AND i.status = 'awaiting_claude'",
        "SELECT i.address_id, count(*), min(i.claude_since), sum(i.claude_since < 'x')"
        + claude_queue._WAITING + " AND i.claude_since < 'x' GROUP BY i.address_id",  # pyright: ignore[reportPrivateUsage]
        "SELECT i.address_id, count(*)" + claude_queue._WAITING + " GROUP BY i.address_id",  # pyright: ignore[reportPrivateUsage]
        re.sub(r"\?", "'x'", claude_review._WAITING),  # pyright: ignore[reportPrivateUsage]
    ]  # fmt: skip
    for sql in sources:
        plan = " | ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql))
        assert "items_claude_waiting" in plan, (sql, plan)
    assert "_IN_QUEUE" in inspect.getsource(claude_queue.backlog)  # backlog's own text, too


def test_overdue_twice_is_the_two_overdue_calls(conn: sqlite3.Connection, clock: FakeClock) -> None:
    """R2: one query for the reminder and the escalation."""
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('c1', 'c1@acme.example', 'standard', 'C', ?)", (OLD,))  # fmt: skip
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('c2', 'c2@acme.example', 'standard', 'C', ?)", (OLD,))  # fmt: skip
    now = clock.now()
    for n, (aid, hours) in enumerate((("c1", 30), ("c1", 50), ("c2", 30), ("c2", 2))):
        since = to_ts(now - timedelta(hours=hours))
        _item(conn, f"ee{n:06d}", aid, "awaiting_claude", claude_since=since)
    first, second = claude_queue.overdue_twice(conn, now, 2)
    assert first == claude_queue.overdue(conn, now)
    assert second == claude_queue.overdue(conn, now, 2)
    assert [(o.address_id, o.count) for o in first] == [("c1", 2), ("c2", 1)]
    assert [(o.address_id, o.count) for o in second] == [("c1", 1)]


def _prechecked(c: sqlite3.Connection, sid: str, aid: str, stage: str) -> None:
    facts = json.dumps({"backfill": 1, "precheck": {"stage": stage}})
    _item(c, sid, aid, "new", prechecked=1, facts=facts)


def test_announcing_queues_the_mail_left_at_new(db_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                clock: FakeClock) -> None:  # fmt: skip
    """R3: an `ecf backfill --act` email (pre-checked, the address's stage recorded) that waited
    for the local model goes to the Claude queue at the announcement, its wait starting then; a
    records-only backfill's email (stage `backfill`) and an unchecked one are left; once."""
    c = _before(db_path, monkeypatch)
    with write_tx(c):
        _address(c, "a1", "A", stage="live")
        _prechecked(c, "aa000010", "a1", "live")
        _prechecked(c, "aa000011", "a1", "backfill")
        _item(c, "aa000012", "a1", "new")
    db.migrate(c)
    assert stages.announce_converted(c, clock, FakeNotifier()) == ["a1"]
    rows = {r[0]: (r[1], r[2]) for r in c.execute("SELECT stable_id, status, claude_since"
                                                  " FROM items")}  # fmt: skip
    assert rows["aa000010"] == ("awaiting_claude", to_ts(clock.now()))
    assert rows["aa000011"] == ("new", None) and rows["aa000012"] == ("new", None)
    assert _audit(c, "address.converted")[0]["queued"] == 1
    c.close()


def test_the_marker_is_written_with_each_address_audit_row(
        db_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock) -> None:  # fmt: skip
    """R6: the announced marker is written in each address's audit-row transaction, so a failure
    after it (here b1's notice) never announces that address twice; a consented address isn't
    counted."""
    c = _before(db_path, monkeypatch)
    _populate(c)
    db.migrate(c)
    real = stages.slack_admin.notice
    calls: list[str] = []

    def flaky(*a: Any, **kw: Any) -> None:
        calls.append(str(a[3]))
        if len(calls) == 2:
            raise sqlite3.OperationalError("database is locked")
        real(*a, **kw)

    monkeypatch.setattr(stages.slack_admin, "notice", flaky)
    with pytest.raises(sqlite3.OperationalError):
        stages.announce_converted(c, clock, FakeNotifier())
    assert stages._announced(c) == ["a1", "b1"]  # pyright: ignore[reportPrivateUsage]
    # b1's audit row and marker committed before its notice failed: it isn't announced twice
    assert stages.announce_converted(c, clock, FakeNotifier()) == []
    with write_tx(c):
        c.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, '[\"a1\"]',"
                  " ?, 't')", (pause.CONSENTED, OLD))  # fmt: skip
    rows = {r["address_id"]: r for r in stages.converted(c)}
    assert rows["a1"]["consented"] and rows["a1"]["waiting"] is None
    assert rows["b1"]["waiting"] == 0
    c.close()


def test_import_replace_clears_the_old_converted_record(
        db_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock, tmp_path: Path,
        target: sqlite3.Connection) -> None:  # fmt: skip
    """R7: the old install's converted ids don't outlive its addresses."""
    path = _old_bundle(db_path, monkeypatch, clock, tmp_path, version=38, data_format=4)
    _import(target, clock, path)
    with write_tx(target):
        for key, value in ((addresses.CONVERTED, '{"zz": "A"}'),
                           (addresses.CONSENTED, '["zz"]')):  # fmt: skip
            target.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                           " (?, ?, ?, 't') ON CONFLICT (key) DO UPDATE SET value ="
                           " excluded.value", (key, value, OLD))  # fmt: skip
    _import(target, clock, path, replace=True, typed="t")
    was = json.loads(target.execute("SELECT value FROM settings WHERE key = ?",
                                    (addresses.CONVERTED,)).fetchone()[0])  # fmt: skip
    assert was == {"a1": "A", "b1": "B"}  # this bundle's, nothing of the old install
    consented = target.execute("SELECT value FROM settings WHERE key = ?",
                               (addresses.CONSENTED,)).fetchone()  # fmt: skip
    assert consented is None
    assert stages._announced(target) == ["a1", "b1"]  # pyright: ignore[reportPrivateUsage]


def test_a_bundle_with_a_preset_d_address_loads_from_step_4(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R11, lifted by v3.0.0 step 4: a D row loads while D is on (it is held back from v3.0.0,
    OD-516, and refused then); any other preset is refused."""
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('d1', 'd1@acme.example', 'standard', 'D', ?)", (OLD,))  # fmt: skip
    with pytest.raises(BadBundleError, match=r"isn't available in v3\.0\.0 \(OD-516\)"):
        importer.validate(conn, {})
    monkeypatch.setattr(addresses, "D_AVAILABLE", True)
    importer.validate(conn, {})
    with write_tx(conn):
        conn.execute("PRAGMA ignore_check_constraints = 1")
        conn.execute("UPDATE addresses SET preset = 'E' WHERE address_id = 'd1'")
        conn.execute("PRAGMA ignore_check_constraints = 0")
    with pytest.raises(BadBundleError, match="preset E isn't one this version has"):
        importer.validate(conn, {})
