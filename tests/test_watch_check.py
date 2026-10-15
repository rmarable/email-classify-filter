"""V1.3 step 7: `ecf watch`, `ecf check`, and approvals batched during a large backlog (SPEC §4.1,
§5.3, §10.2). The backlog is what waits at `awaiting_claude` from v3.0.0 (P9); the model round of
`ecf check`, `--until-empty` and the backlog estimate went with the local model (ADR 0031)."""

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
from ecf_server import decide, items
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.state_machine import Status, TransitionContext
from tests.test_decide import KNOWN_BULK, MARKETING, make_address, make_classified


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


# `ecf watch`: an allow-listed environment, stopping and restoring the unit, the lock, and a
# marker left behind.


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


def test_watch_restarts_the_unit_when_the_service_wont_let_go(
    paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    def stuck(_paths: Paths) -> None:
        raise ConflictError("the service didn't stop within 60 s; try again")

    monkeypatch.setattr(watch, "wait_for_lock", stuck)
    m = FakeManager(running=True)
    with pytest.raises(ConflictError):
        watch.run(paths, m, server=Path("/usr/bin/true"))
    assert m.calls == ["stop", "start"] and m.running


def test_a_marker_left_behind_is_reported(paths: Paths) -> None:
    paths.run_dir.mkdir(parents=True)
    watch.marker_path(paths).write_text(json.dumps(
        {"pid": 999999, "restore_unit": True, "started_at": "2026-09-30T12:00:00Z"}))  # fmt: skip
    m = watch.marker(paths)
    assert m is not None and m["alive"] is False
    [c] = check_watch(paths)
    assert c.level is Level.FAIL and c.fix == "ecf service start"


# `ecf check` and status: a large backlog batches approvals onto digests, held cards post once it
# drops, and a busy check is reported rather than waited for.


def _waiting_items(conn: sqlite3.Connection, clock: FakeClock, n: int) -> None:
    """`n` emails waiting for /ecf-review."""
    for i in range(n):
        sid = f"{i:04x}".ljust(64, "c")
        items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                          content_hash="h", facts="{}", subject="s",
                          sender="a@b.example")  # fmt: skip
        items.transition(conn, clock, StableId(sid), Status.AWAITING_CLAUDE, TransitionContext(),
                         actor="service")  # fmt: skip


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


def test_cards_held_back_during_a_backlog_are_posted_once_it_drops(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    from ecf_server import approvals  # noqa: PLC0415
    from tests.test_digests_daily import slack_setup  # noqa: PLC0415

    slack_setup(conn, clock)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = 'live'")
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                     " ('config.action_policy', ?, 't', 't')",
                     (json.dumps({"standard": {"archive": "approve"}}),))  # fmt: skip
    _waiting_items(conn, clock, decide.BACKLOG_BATCH + 1)
    sid = make_classified(conn, clock, MARKETING, KNOWN_BULK, sid="ff")
    assert decide.apply(conn, clock, sid) is Status.AWAITING_APPROVAL

    def cards() -> int:
        return sum(json.loads(r[0])["card"]["title"].startswith("Approve?") for r in conn.execute(
            "SELECT payload FROM jobs WHERE queue = 'slack_out'"))  # fmt: skip

    assert approvals.post_held_cards(conn, clock) == 0 and cards() == 0  # still a backlog
    for (waiting,) in conn.execute("SELECT stable_id FROM items WHERE status = 'awaiting_claude'"
                                   ).fetchall():  # fmt: skip
        items.transition(
            conn, clock, StableId(waiting), Status.CLASSIFIED, TransitionContext(), actor="service"
        )  # Claude classified them: none waits
    assert approvals.post_held_cards(conn, clock) == 1 and cards() == 1
    assert approvals.post_held_cards(conn, clock) == 0  # once


def test_a_busy_check_is_reported_not_waited_for(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--until-empty` and its wait for the scheduled check went in v3.0.0 (ADR 0031, P17)."""
    from ecf_server import api, checks  # noqa: PLC0415
    from ecf_server.api import ServiceState  # noqa: PLC0415

    make_address(conn, clock, "shadow")
    ran: list[str] = []

    def run_check(*_a: object, **_k: object) -> checks.CheckReport:
        ran.append("busy")
        return checks.CheckReport(address_id="ap", status="busy", started_at="t")

    monkeypatch.setattr(api.checks, "run_check", run_check)

    def nothing(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(api.health, "after_check", nothing)
    st = ServiceState(install="t", token="x", started_at="2026-10-01T12:00:00.000000Z",
                      clock=clock, db_path=db_path)  # fmt: skip
    out = [json.loads(x) for x in api._check_lines(st, ["ap"], None, None)]  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert ran == ["busy"]
    assert [o.get("status") for o in out] == ["busy", None] and out[-1]["done"] is True
