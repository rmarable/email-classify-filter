import json
import re
import sqlite3
import stat
from pathlib import Path

import pytest

from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import db
from ecf_server.clock import FakeClock
from ecf_server.items import create_item


def test_pragmas(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == db.BUSY_TIMEOUT_MS


def test_files_are_private(conn: sqlite3.Connection, db_path: Path) -> None:
    conn.execute("INSERT INTO settings VALUES ('k', '1', 't', 'test')")  # create -wal/-shm
    db.connect(db_path).close()
    for suffix in ("", "-wal", "-shm"):
        p = Path(f"{db_path}{suffix}")
        assert p.exists(), suffix
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, suffix
    assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700


def test_migrate_is_idempotent(conn: sqlite3.Connection) -> None:
    assert db.migrate(conn) == []
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert (
        len(tables - {"schema_migrations"}) == 37
    )  # 21 initial + processing (0003), check_state (0005), alerts (0008), slack_messages (0011),
    # escalations (0015), delays (0016), model_calls (0018), eval_runs (0020), claims and
    # claim_batches (0021), claude_calls and claude_sessions (0022), fallback_shadow (0023),
    # alert_outbox (0029), downloads (0032), corpus_downloads (0033)


def test_strict_rejects_wrong_types(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO rate VALUES ('k', 't', 'not a number')")


def test_status_check_matches_enum(conn: sqlite3.Connection) -> None:
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'items'").fetchone()[0]
    listed = re.search(r"status IN \(([^)]*)\)", sql)
    assert listed is not None
    assert set(re.findall(r"'([a-z_]+)'", listed.group(1))) == {s.value for s in Status}


def test_write_tx_rolls_back(conn: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError), db.write_tx(conn):
        conn.execute("INSERT INTO settings VALUES ('k', '1', 't', 'test')")
        raise RuntimeError("boom")
    assert conn.execute("SELECT count(*) FROM settings WHERE key = 'k'").fetchone()[0] == 0
    assert not conn.in_transaction


def test_version_floor() -> None:
    assert db.MIN_SQLITE >= (3, 37, 0)
    assert db.sqlite_version_ok()


class _FailingCommit(sqlite3.Connection):
    fail = True

    def execute(self, sql: str, *args: object) -> sqlite3.Cursor:  # type: ignore[override]
        if sql == "COMMIT" and _FailingCommit.fail:
            raise sqlite3.OperationalError("disk I/O error (simulated)")
        return super().execute(sql, *args)  # pyright: ignore[reportArgumentType]


def test_failed_commit_does_not_wedge_the_connection(tmp_path: Path) -> None:
    c = sqlite3.connect(tmp_path / "x.db", autocommit=True, factory=_FailingCommit)
    c.execute("CREATE TABLE t (x INTEGER)")
    with pytest.raises(sqlite3.OperationalError), db.write_tx(c):
        c.execute("INSERT INTO t VALUES (1)")
    assert not c.in_transaction
    _FailingCommit.fail = False
    with db.write_tx(c):
        c.execute("INSERT INTO t VALUES (2)")
    assert [r[0] for r in c.execute("SELECT x FROM t")] == [2]
    c.close()


def test_0012_rebuilds_jobs_keeping_rows_and_indexes(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    every = db._migration_files()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(db, "_migration_files", lambda: [m for m in every if m[0] < 12])
    c = db.connect(db_path)
    db.migrate(c)
    c.execute(
        "INSERT INTO jobs (job_id, queue, address_id, payload, timeout_s, visible_at, created_at)"
        " VALUES ('j1', 'slack_out', 'C1', '{\"k\": 1}', 30, 't', 't')"
    )
    with pytest.raises(sqlite3.IntegrityError):  # the old CHECK has no slack_in
        c.execute(
            "INSERT INTO jobs (job_id, queue, address_id, timeout_s, visible_at, created_at)"
            " VALUES ('j0', 'slack_in', 'U1', 60, 't', 't')"
        )
    monkeypatch.setattr(db, "_migration_files", lambda: every)
    assert db.migrate(c)[0] == "0012_slack_in_queue.sql"
    row = c.execute("SELECT queue, payload FROM jobs WHERE job_id = 'j1'").fetchone()
    assert (row["queue"], row["payload"]) == ("slack_out", '{"k": 1}')
    c.execute(
        "INSERT INTO jobs (job_id, queue, address_id, timeout_s, visible_at, created_at)"
        " VALUES ('j2', 'slack_in', 'U1', 60, 't', 't')"
    )
    indexes = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'index'"
                                       " AND tbl_name = 'jobs' AND sql IS NOT NULL")}  # fmt: skip
    assert indexes == {"jobs_ready", "jobs_claimed"}
    c.close()


def test_0015_carries_v11_pending_escalations_over(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    every = db._migration_files()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(db, "_migration_files", lambda: [m for m in every if m[0] < 15])
    c = db.connect(db_path)
    db.migrate(c)
    c.execute(
        "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
        " VALUES ('ap', 'ap@acme.example', 'standard', 'A', 't')"
    )
    for sid, esc in (("s1", "pending Slack (V1.2)"), ("s2", None)):
        facts = json.dumps({"precheck": {"escalation": esc, "at": "2026-09-29T10:00:00.000000Z"}})
        create_item(c, FakeClock(), stable_id=StableId(sid), address_id=AddressId("ap"),
                    content_hash="h", facts=facts)  # fmt: skip
    monkeypatch.setattr(db, "_migration_files", lambda: every)
    db.migrate(c)
    rows = c.execute("SELECT stable_id, state, created_at FROM escalations").fetchall()
    assert [tuple(r) for r in rows] == [("s1", "v1.1", "2026-09-29T10:00:00.000000Z")]
    c.close()


def test_0034_moves_stored_classifications_to_schema_v2(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OD-475: `staff` becomes `team` in classifications, corrections and fallback shadow runs;
    `items.schema_version` goes 1 -> 2, so the mapping runs once (C3)."""
    every = db._migration_files()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(db, "_migration_files", lambda: [m for m in every if m[0] < 34])
    c = db.connect(db_path)
    db.migrate(c)
    c.execute(
        "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
        " VALUES ('ap', 'ap@acme.example', 'standard', 'A', 't')"
    )
    staff = json.dumps({"category": "other", "sender_type": "staff", "fraud_risk": "none"})
    vendor = json.dumps({"category": "invoice", "sender_type": "vendor", "fraud_risk": "low"})
    for sid, cls, fix in (("s1", staff, staff), ("s2", vendor, None), ("s3", None, None)):
        create_item(c, FakeClock(), stable_id=StableId(sid), address_id=AddressId("ap"),
                    content_hash=sid, classification=cls, human_correction=fix)  # fmt: skip
    c.execute("INSERT INTO fallback_shadow (stable_id, address_id, digest, outcome,"
              " classification, created_at) VALUES ('s1', 'ap', 'd', 'ok', ?, 't')",
              (staff,))  # fmt: skip
    monkeypatch.setattr(db, "_migration_files", lambda: every)
    assert db.migrate(c) == ["0034_schema_v2.sql"]
    rows = {r[0]: r for r in c.execute("SELECT stable_id, classification, human_correction,"
                                       " schema_version FROM items")}  # fmt: skip
    assert json.loads(rows["s1"][1])["sender_type"] == "team"
    assert json.loads(rows["s1"][2])["sender_type"] == "team"
    assert json.loads(rows["s2"][1]) == json.loads(vendor)  # nothing else is mapped
    assert rows["s3"][1] is None
    assert {r[3] for r in rows.values()} == {2}
    shadow = c.execute("SELECT classification FROM fallback_shadow").fetchone()[0]
    assert json.loads(shadow)["sender_type"] == "team"
    assert db.migrate(c) == []
    c.close()
