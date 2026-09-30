"""V1.3 step 7: `ecf watch`, `ecf check` running the model, the backlog in `ecf status`, and
approvals batched during a large backlog (SPEC §4.1, §5.3, §10.2)."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from pathlib import Path

import pytest

from ecf import watch
from ecf.doctor import Level, check_watch
from ecf.errors import ConflictError
from ecf.ids import AddressId, StableId
from ecf.paths import Paths
from ecf.service_unit import UnitStatus
from ecf_server import decide, items, modelq, ollama
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.state_machine import Status
from tests.test_classifier import GOOD, ChatOllama
from tests.test_decide import KNOWN_BULK, MARKETING, make_address, make_classified
from tests.test_models import check_kw


class FakeManager:
    def __init__(self, running: bool) -> None:
        self.running = running
        self.calls: list[str] = []
        self.unit_path = Path("/nonexistent")

    def status(self) -> UnitStatus:
        return UnitStatus(True, self.running)

    def stop(self) -> None:
        self.calls.append("stop")
        self.running = False

    def start(self) -> None:
        self.calls.append("start")
        self.running = True

    def install(self) -> None: ...
    def uninstall(self) -> None: ...
    def restart(self) -> None: ...


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths("t", tmp_path, honor_ecf_socket=False)


# ---- ecf watch ----------------------------------------------------------------------------------


def test_the_environment_is_an_allow_list() -> None:
    env = {"HOME": "/h", "PATH": "/bin", "OLLAMA_HOST": "http://10.0.0.9", "HTTP_PROXY": "x",
           "TMPDIR": "/t", "DBUS_SESSION_BUS_ADDRESS": "unix:x",
           "AWS_SECRET_ACCESS_KEY": "s"}  # fmt: skip
    assert watch.environment(env, "darwin") == {"HOME": "/h", "PATH": "/bin", "TMPDIR": "/t"}
    assert watch.environment(env, "linux") == {"HOME": "/h", "PATH": "/bin",
                                               "DBUS_SESSION_BUS_ADDRESS": "unix:x"}  # fmt: skip


def test_watch_stops_the_unit_runs_the_service_and_restores_it(
    paths: Paths, tmp_path: Path
) -> None:
    server = tmp_path / "fake-server"
    seen = tmp_path / "seen"
    server.write_text(f"#!/bin/sh\nenv > '{seen}'\ncat '{watch.marker_path(paths)}' >> '{seen}'\n")
    server.chmod(0o755)
    m = FakeManager(running=True)
    os.environ["OLLAMA_HOST"] = "http://10.0.0.9:11434"
    try:
        assert watch.run(paths, m, server=server) == 0
    finally:
        del os.environ["OLLAMA_HOST"]
    assert m.calls == ["stop", "start"]
    text = seen.read_text()
    assert "OLLAMA_HOST" not in text and '"restore_unit": true' in text
    assert watch.marker(paths) is None


def test_watch_leaves_a_stopped_unit_stopped(paths: Paths, tmp_path: Path) -> None:
    m = FakeManager(running=False)
    assert watch.run(paths, m, server=Path("/usr/bin/true")) == 0
    assert m.calls == []


def test_watch_waits_for_the_lock_and_gives_up(paths: Paths) -> None:
    paths.run_dir.mkdir(parents=True)
    with paths.lock.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        with pytest.raises(ConflictError, match="didn't stop"):
            watch.wait_for_lock(paths, timeout_s=0.3)
    watch.wait_for_lock(paths, timeout_s=0.3)  # released: returns at once


def test_a_marker_left_behind_is_reported(paths: Paths) -> None:
    paths.run_dir.mkdir(parents=True)
    watch.marker_path(paths).write_text(json.dumps(
        {"pid": 999999, "restore_unit": True, "started_at": "2026-09-30T12:00:00Z"}))  # fmt: skip
    m = watch.marker(paths)
    assert m is not None and m["alive"] is False
    [c] = check_watch(paths)
    assert c.level is Level.FAIL and c.fix == "ecf service start"


# ---- ecf check and status --------------------------------------------------------------------


def _waiting_items(conn: sqlite3.Connection, clock: FakeClock, n: int) -> None:
    for i in range(n):
        sid = f"{i:04x}".ljust(64, "c")

        def excerpt(c: sqlite3.Connection, sid: str = sid) -> None:
            c.execute("INSERT INTO excerpts (stable_id, classifier_text) VALUES (?, 'x')", (sid,))

        items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                          content_hash="h", facts="{}", subject="s", sender="a@b.example",
                          also=excerpt)  # fmt: skip


def test_check_runs_a_model_round_and_reports_it(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    from ecf_server.api import (  # noqa: PLC0415
        ServiceState,
        _model_lines,  # pyright: ignore[reportPrivateUsage]
    )

    make_address(conn, clock, "shadow")
    _waiting_items(conn, clock, 2)
    fake = ChatOllama(json.dumps(GOOD))
    state = ServiceState(install="t", token="x", started_at="2026-10-01T12:00:00.000000Z",
                         clock=clock, db_path=db_path, model_client=fake.client,
                         model_check=check_kw())  # fmt: skip
    off = {"status": "off", "detail": "this service doesn't run the local model"}
    assert list(_model_lines(state, False)) == [off]
    from ecf_server import pipeline  # noqa: PLC0415

    state.model_work = pipeline.work
    [line] = list(_model_lines(state, True))
    assert line == {"status": "done", "done": 2, "failed": 0, "waiting": 0}


def test_status_estimates_the_backlog(conn: sqlite3.Connection, clock: FakeClock) -> None:
    make_address(conn, clock, "shadow")
    _waiting_items(conn, clock, 30)
    for _ in range(5):
        ollama.record_call(conn, clock, role="classifier", outcome="ok", digest="d",
                           metrics=ollama.Metrics(1, 1, 1, 1, 1, 1, 3_000_000_000))  # fmt: skip
    got = modelq.status(conn, laptop=True, on_ac=False)
    assert got == {"waiting": 30, "eta_s": 90, "on_battery": True, "eval": False}


@pytest.mark.parametrize(("backlog", "carded"), [(0, True), (decide.BACKLOG_BATCH + 1, False)])
def test_a_large_backlog_batches_approvals_on_digests(
    conn: sqlite3.Connection, clock: FakeClock, backlog: int, carded: bool
) -> None:
    from tests.test_digests_daily import slack_setup  # noqa: PLC0415

    slack_setup(conn, clock)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = 'live'")
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                     " ('config.action_policy', ?, 't', 't')",
                     (json.dumps({"standard": {"archive": "approve"}}),))  # fmt: skip
    _waiting_items(conn, clock, backlog)
    sid = make_classified(conn, clock, MARKETING, KNOWN_BULK, sid="ff")
    assert decide.apply(conn, clock, sid) is Status.AWAITING_APPROVAL
    posts = [json.loads(r[0]) for r in conn.execute(
        "SELECT payload FROM jobs WHERE queue = 'slack_out'")]  # fmt: skip
    approval_cards = [p for p in posts if p["card"]["title"].startswith("Approve?")]
    assert bool(approval_cards) is carded
