"""The audit log files and `ecf logs` (V1.1 step 12)."""

from __future__ import annotations

import json
import re
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx

from ecf.cli import _log_line, _since  # pyright: ignore[reportPrivateUsage]
from ecf.paths import Paths
from ecf_server import audit, checks, db
from ecf_server.api import ServiceState, create_app
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.schedule import Scheduler
from ecf_server.service import Service
from tests.test_checks import Box, secrets
from tests.test_precheck import BEC
from tests.test_schedule import DESKTOP

INSTALL = "default"


def add(
    conn: sqlite3.Connection, clock: FakeClock, event: str, address: str | None = None, **data: Any
) -> None:
    with write_tx(conn):
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, ?, 'service', 'ok', ?)",
            (to_ts(clock.now()), address, event, json.dumps(data)),
        )


def read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines()]


# ---- files ----------------------------------------------------------------------------------


def test_flush_writes_each_row_once_per_address_and_day(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    out = tmp_path / "audit"
    add(conn, clock, "config.applied", key="org_domains")
    add(conn, clock, "check.completed", "ap", created=2)
    assert audit.flush(conn, clock, out, INSTALL) == 2
    assert audit.flush(conn, clock, out, INSTALL) == 0
    clock.advance(86400)
    add(conn, clock, "check.completed", "ap", created=1)
    assert audit.flush(conn, clock, out, INSTALL) == 1
    ap1, ap2 = out / "ap/2026/10/01.jsonl", out / "ap/2026/10/02.jsonl"
    (first,) = read(ap1)
    assert first["event"] == "check.completed" and first["install"] == INSTALL
    assert (
        first["address_id"] == "ap" and first["data"] == {"created": 2} and "stable_id" not in first
    )
    assert (
        len(read(ap2)) == 1
        and read(out / "_install/2026/10/01.jsonl")[0]["event"] == "config.applied"
    )
    assert stat.S_IMODE(ap1.stat().st_mode) == 0o600
    assert stat.S_IMODE((out / "ap").stat().st_mode) == 0o700


def test_flush_in_batches(conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path) -> None:
    for i in range(audit.BATCH + 5):
        add(conn, clock, "check.completed", "ap", n=i)
    assert audit.flush(conn, clock, tmp_path, INSTALL) == audit.BATCH + 5
    ids = [x["id"] for x in read(tmp_path / "ap/2026/10/01.jsonl")]
    assert ids == sorted(set(ids)) and len(ids) == audit.BATCH + 5


# ---- queries --------------------------------------------------------------------------------


def test_query_filters(conn: sqlite3.Connection, clock: FakeClock) -> None:
    add(conn, clock, "check.completed", "ap")
    add(conn, clock, "action.granted", "ap")
    clock.advance(3600)
    add(conn, clock, "check.failed", "billing")
    add(conn, clock, "config.applied")

    def names(rows: list[dict[str, Any]]) -> list[str]:
        return [r["event"] for r in rows]

    assert names(audit.query(conn, INSTALL)) == [
        "check.completed",
        "action.granted",
        "check.failed",
        "config.applied",
    ]
    assert names(audit.query(conn, INSTALL, address_id="ap")) == [
        "check.completed",
        "action.granted",
    ]
    assert names(audit.query(conn, INSTALL, event_prefix="check.")) == [
        "check.completed",
        "check.failed",
    ]
    since = to_ts(clock.now() - timedelta(minutes=5))
    assert names(audit.query(conn, INSTALL, since=since)) == ["check.failed", "config.applied"]
    assert names(audit.query(conn, INSTALL, limit=1)) == ["config.applied"]
    assert names(audit.query(conn, INSTALL, after_id=3)) == ["config.applied"]
    assert audit.query(conn, INSTALL, event_prefix="%") == []  # no LIKE wildcards


# ---- no message content ---------------------------------------------------------------------


def test_audit_lines_carry_no_message_content(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, tmp_path: Path
) -> None:
    conn.execute(
        "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
        " VALUES ('ap', 'ap@acme.example', 'high', 'A', 'now')"
    )
    conn.execute(
        "INSERT INTO probe (address_id, host, probed_at) VALUES ('ap', 'imap.acme.example', 'now')"
    )
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by)"
        " VALUES ('org_domains', '[\"acme.example\"]', 'now', 't')"
    )
    box = Box()
    for _ in range(2):
        checks.run_check(
            conn,
            clock,
            address_id="ap",
            install=INSTALL,
            secrets=secrets(),
            factory=box.factory,
            connect=lambda: db.connect(db_path),
        )
        box.fake.deliver(BEC)
    audit.flush(conn, clock, tmp_path, INSTALL)
    text = "\n".join(p.read_text() for p in tmp_path.rglob("*.jsonl"))
    assert "precheck.decided" in text
    for secret in (
        "Please pay invoice",
        "updated bank details",
        "billing@vendor-a.example",
        "Hello",
    ):
        assert secret not in text, secret


# ---- API, CLI, service ----------------------------------------------------------------------


def test_logs_route(conn: sqlite3.Connection, db_path: Path, clock: FakeClock) -> None:
    add(conn, clock, "check.completed", "ap")
    add(conn, clock, "check.failed", "ap")
    st = ServiceState(install=INSTALL, token="t", started_at="t", clock=clock, db_path=db_path)

    async def get(q: str) -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(st))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.get(f"/v1/logs{q}", headers={"Authorization": "Bearer t"})

    r = anyio.run(get, "?event=check.failed")
    assert [e["event"] for e in r.json()["events"]] == ["check.failed"]
    assert anyio.run(get, "?limit=abc").status_code == 400


def test_cli_since_and_lines() -> None:
    assert _since("2026-09-29T08:00") == "2026-09-29T08:00"
    got = datetime.strptime(_since("2h"), "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    assert abs((datetime.now(UTC) - timedelta(hours=2)) - got) < timedelta(seconds=5)
    line = _log_line(
        {
            "id": 1,
            "ts": "2026-10-01T12:00:00.000000Z",
            "event": "check.failed",
            "address_id": "ap",
            "outcome": "error",
            "data": {"status": "error"},
        }
    )
    assert re.fullmatch(
        r'2026-10-01T12:00:00  ap +check\.failed \[error\]  \{"status": "error"\}', line
    )


def test_service_tick_copies_the_log(
    conn: sqlite3.Connection, db_path: Path, tmp_path: Path, clock: FakeClock
) -> None:
    paths = Paths(install=INSTALL, root=tmp_path)
    svc = Service(paths, clock)
    svc.scheduler = Scheduler(clock, lambda: DESKTOP)
    svc.state.db_path = db_path
    add(conn, clock, "config.applied")
    svc.tick()
    assert read(paths.audit_dir / "_install/2026/10/01.jsonl")[0]["event"] == "config.applied"
