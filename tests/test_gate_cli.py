"""`ecf stage set <address> live` end to end (V1.3 step 6b): the real CLI against a real dev
service over its socket, through the gate screen, the refusal, step-up and the held emails. The dev
service's step-up is a fake authenticator, so no OS dialog appears."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from typer.testing import CliRunner

from ecf.cli import app
from ecf.errors import PolicyDeniedError
from ecf.paths import Paths
from ecf_server import db, decide, evalrun, gate, slack_admin
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.state_machine import Status
from tests.test_decide import KNOWN_BULK, MARKETING, make_address, make_classified
from tests.test_dev_mode import start_dev

from .conftest import stop, wait_answering

CUSTOMER = MARKETING | {"category": "customer_request", "requires_reply": True}


def _seed(conn: sqlite3.Connection, clock: FakeClock) -> str:
    make_address(conn, clock, "assist")
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("INSERT INTO routes (address_id, surface, route_ref, name)"
                     " VALUES ('ap', 'slack', 'CAP', 'ecf-default-ap')")  # fmt: skip
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"),
                     ("slack_member_id", "U0ME1"), ("slack_summary_channel", "CSUM")):  # fmt: skip
            slack_admin.put_setting(conn, k, v, now, actor="test")
    for i in range(100):
        sid = make_classified(conn, clock, CUSTOMER, KNOWN_BULK, sid=f"r{i:04d}x")
        with write_tx(conn):
            conn.execute("UPDATE items SET pinned_models = ?, review = ? WHERE stable_id = ?",
                         (json.dumps({"digest": gate.current_digest()}),
                          json.dumps({"verdict": "correct", "category_ok": True, "bulk": False}),
                          sid))  # fmt: skip
    held = make_classified(conn, clock, MARKETING, KNOWN_BULK, sid="held")
    assert decide.apply(conn, clock, held) is Status.HELD  # archive waits for live
    return held


def _safe_run(conn: sqlite3.Connection, clock: FakeClock, root: Path) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("INSERT INTO eval_runs (run_id, pair, digest, set_version, created_at,"
                     " metrics, gate_passed, path) VALUES ('run1', ?, ?, ?, ?, ?, 1, 'p')",
                     (gate.PAIR, gate.current_digest(), evalrun.set_version(root), now,
                      json.dumps({"confirmed": 150, "unsafe": [], "fraud_cases": 60})))  # fmt: skip
        slack_admin.put_setting(conn, evalrun.EVAL_ROOT, str(root), now, actor="test")


@contextmanager
def _db(p: Paths) -> Generator[sqlite3.Connection]:
    conn = db.connect(p.db)
    try:
        yield conn
    finally:
        conn.close()


def test_stage_set_live_from_the_cli(home: Path, tmp_path: Path) -> None:
    p = Paths("dev", home)
    proc = start_dev(home)
    try:
        wait_answering(p, proc)
        root = tmp_path / "set"
        root.mkdir()
        (root / "labels.jsonl").write_text('{"id": "x"}\n')
        with _db(p) as conn:
            held = _seed(conn, FakeClock())
        cli = CliRunner()

        refused = cli.invoke(app, ["--install", "dev", "stage", "set", "ap", "live"])
        assert refused.exit_code != 0
        assert "go-live gate: NOT met" in refused.output
        assert "  ok   100/100 reviewed" in refused.output
        assert "  FAIL no synthetic-set result for this model" in refused.output
        assert "ecf eval run --fraud-only" in refused.output
        assert isinstance(refused.exception, PolicyDeniedError)  # ecf's main prints it as "ecf: …"
        assert "no override can waive" in str(refused.exception)

        conn = db.connect(p.db)
        try:
            assert (
                conn.execute("SELECT stage FROM addresses").fetchone()[0] == "assist"
            )  # unchanged
            _safe_run(conn, FakeClock(), root)
        finally:
            conn.close()
        status = cli.invoke(app, ["--install", "dev", "stage", "status"])
        assert status.exit_code == 0, status.output
        assert "go-live gate: met" in status.output

        live = cli.invoke(app, ["--install", "dev", "stage", "set", "ap", "live"])
        assert live.exit_code == 0, live.output
        assert "go-live gate: met" in live.output
        assert "held emails: 1 up to 7 days old run when the address goes live; 0 older" \
            in live.output  # fmt: skip
        assert "Step-up: ecf: move ap@acme.example from assist to live (Full)" in live.output
        assert "ap: live" in live.output
        assert "held emails: 1 ran, 0 marked handled by hand, 0 still held, 0 failed" in live.output

        with _db(p) as conn:
            assert conn.execute("SELECT stage FROM addresses").fetchone()[0] == "live"
            assert conn.execute("SELECT status FROM items WHERE stable_id = ?",
                                (held,)).fetchone()[0] != "held"  # fmt: skip
            changed = json.loads(conn.execute(
                "SELECT data FROM audit WHERE event = 'stage.changed' ORDER BY id DESC"
            ).fetchone()[0])  # fmt: skip
            assert changed["to"] == "live" and changed["gate"]["met"] is True
        after = cli.invoke(app, ["--install", "dev", "stage", "status"])
        assert "live" in after.output and "go-live gate" not in after.output
    finally:
        stop(proc)
