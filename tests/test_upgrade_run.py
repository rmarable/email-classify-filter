"""Carrying out an upgrade (V1.5 step 11b; OD-331, OD-376 to OD-381): phase 1 up to the hand-over,
putting things back when the copy or the install fails, phase 2 with its rollback on a failed
migration or start, the re-grant that doesn't roll back, the service's record and the settled
mark."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

import ecf.upgrade_run
from ecf import __version__, upgrade_run
from ecf.errors import EcfError, InvalidInputError
from ecf.paths import Paths
from ecf.service_unit import UnitStatus
from ecf_server import db, upgrade_state
from ecf_server.clock import FakeClock
from tests.test_addresses import make_state
from tests.test_export_keys import ApiClient


class Manager:
    unit_path = Path("/dev/null")

    def __init__(self) -> None:
        self.calls: list[str] = []

    def install(self) -> None: ...
    def uninstall(self) -> None: ...
    def restart(self) -> None: ...

    def start(self) -> None:
        self.calls.append("start")

    def stop(self) -> None:
        self.calls.append("stop")

    def status(self) -> UnitStatus:
        return UnitStatus(installed=True, running=True)


class Handover(Exception):
    pass


def _tools(paths: Paths, codes: dict[str, int] | None = None, *, answering: bool = True
           ) -> tuple[upgrade_run.Tools, Manager, list[list[str]]]:  # fmt: skip
    m, ran = Manager(), list[list[str]]()
    codes = codes or {}

    def run(args: list[str]) -> int:
        ran.append(args)
        if args[1] == "snapshot" and codes.get("snapshot", 0) == 0:
            folder = paths.data_dir / "upgrades" / args[-1]
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "ecf.db").write_bytes(paths.db.read_bytes())
        word = args[1] if args[1] != "tool" else "install:" + Path(args[-1]).name
        return codes.get(word, 0)

    def execv(path: str, argv: list[str]) -> None:
        raise Handover(path, argv)

    tools = upgrade_run.Tools(manager=m, run=run, server=lambda: Path("/env/bin/ecf-server"),
                              uv=lambda: "/usr/bin/uv", execv=execv,
                              answering=lambda _p: answering, sleep=lambda _s: None,
                              echo=lambda _s: None)  # fmt: skip
    return tools, m, ran


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    p = Paths("t", tmp_path / "home")
    c = db.connect(p.db)
    db.migrate(c)
    c.close()
    return p


@pytest.fixture
def wheels(tmp_path: Path) -> tuple[Path, Path]:
    old, new = tmp_path / "old-0.1.0.whl", tmp_path / "new-0.2.0.whl"
    old.write_bytes(b"old wheel")
    new.write_bytes(b"new wheel")
    return old, new


def _start(paths: Paths, tools: upgrade_run.Tools, wheels: tuple[Path, Path]) -> None:
    upgrade_run.start(paths, tools, old_version="0.1.0", new_version="0.2.0", new_wheel=wheels[1],
                      old_wheel=wheels[0], pin_changes=["local"], affected=["ap"])  # fmt: skip


def test_phase_one_hands_over_to_the_new_cli(paths: Paths, wheels: tuple[Path, Path]) -> None:
    tools, m, ran = _tools(paths)
    with pytest.raises(Handover) as ei:
        _start(paths, tools, wheels)
    path, argv = ei.value.args
    assert path == str(Path(sys.prefix) / "bin" / "ecf")
    assert argv[1:] == ["--install", "t", "upgrade", "--continue"]
    assert m.calls == ["stop"]
    assert ran[0] == ["/env/bin/ecf-server", "snapshot", "--install", "t", "--label",
                      "0.1.0-to-0.2.0"]  # fmt: skip
    assert ran[1] == ["/usr/bin/uv", "tool", "install", "--force", str(wheels[1])]
    folder = paths.data_dir / "upgrades" / "0.1.0-to-0.2.0"
    assert (folder / wheels[0].name).read_bytes() == b"old wheel"  # kept for a rollback
    state = upgrade_run.read_state(paths)
    assert state is not None and state["phase"] == "migrating"
    assert state["affected"] == ["ap"] and state["old_wheel"] == str(folder / wheels[0].name)
    assert oct(upgrade_run.state_file(paths).stat().st_mode & 0o777) == "0o600"


def test_a_failed_copy_or_install_puts_things_back(paths: Paths,
                                                   wheels: tuple[Path, Path]) -> None:  # fmt: skip
    tools, m, ran = _tools(paths, {"snapshot": 1})
    with pytest.raises(EcfError, match="database copy failed"):
        _start(paths, tools, wheels)
    assert m.calls == ["stop", "start"] and len(ran) == 1
    tools, m, ran = _tools(paths, {f"install:{wheels[1].name}": 1})
    with pytest.raises(EcfError, match="old one is back"):
        _start(paths, tools, wheels)
    assert ran[-1][-1].endswith(wheels[0].name) and m.calls == ["stop", "start"]
    state = upgrade_run.read_state(paths)
    assert state is not None and state["phase"] == "failed"


def test_no_uv_stops_nothing(paths: Paths, wheels: tuple[Path, Path]) -> None:
    tools, m, _ran = _tools(paths)
    tools.uv = lambda: None
    with pytest.raises(EcfError, match="uv isn't on PATH"):
        _start(paths, tools, wheels)
    assert m.calls == []


def _prepared(paths: Paths, wheels: tuple[Path, Path]) -> None:
    tools, _m, _ran = _tools(paths)
    with pytest.raises(Handover):
        _start(paths, tools, wheels)


def test_a_failed_migration_restores_everything(paths: Paths, wheels: tuple[Path, Path]) -> None:
    _prepared(paths, wheels)
    before = paths.db.read_bytes()
    c = db.connect(paths.db)  # the migration got half-way
    c.execute("CREATE TABLE half_done (x INTEGER)")
    c.close()
    Path(str(paths.db) + "-wal").write_bytes(b"stale")
    tools, m, ran = _tools(paths, {"migrate": 1})
    done = upgrade_run.resume(paths, tools, regrant=False)
    assert done["phase"] == "rolled_back" and done["why"] == "migrate"
    assert paths.db.read_bytes() == before and not Path(str(paths.db) + "-wal").exists()
    assert ran[-1][-1].endswith(wheels[0].name)  # the old wheel again
    assert m.calls == ["stop", "start"]


def test_a_service_that_never_answers_is_rolled_back(
    paths: Paths, wheels: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepared(paths, wheels)
    tools, m, _ran = _tools(paths, answering=False)
    monkeypatch.setattr(upgrade_run, "START_S", 0.0)
    done = upgrade_run.resume(paths, tools, regrant=False)
    assert done["phase"] == "rolled_back" and done["why"] == "start"
    assert m.calls == ["start", "stop", "start"]


def test_success_records_the_upgrade_and_a_refused_regrant_does_not_roll_back(
    paths: Paths, wheels: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepared(paths, wheels)
    st = make_state(paths.db, None)
    finished: list[dict[str, Any]] = []

    class Client(ApiClient):
        def request(self, method: str, path: str, json: Any = None, **_: Any) -> Any:
            finished.append(json)
            return super().request(method, path, json | {"to": __version__})

    def client(_paths: Paths) -> Client:
        return Client(st)

    monkeypatch.setattr(ecf.upgrade_run, "LocalClient", client)
    tools, m, ran = _tools(paths, {"regrant": 1})
    done = upgrade_run.resume(paths, tools, regrant=True)
    assert done["phase"] == "started" and m.calls == ["start"]
    assert [a[1] for a in ran] == ["migrate", "regrant"]
    assert finished[0]["from"] == "0.1.0" and finished[0]["affected"] == ["ap"]
    c = db.connect(paths.db)
    rec = upgrade_state.current(c)
    assert rec is not None and rec["settled_at"] is None
    clock = FakeClock()
    assert upgrade_state.settle(c, clock) is True
    assert upgrade_state.settle(c, clock) is False  # once
    events = [r[0] for r in c.execute("SELECT event FROM audit WHERE event LIKE 'upgrade.%'")]
    assert events == ["upgrade.completed", "upgrade.settled"]
    c.close()
    with pytest.raises(EcfError, match="no upgrade is waiting"):
        upgrade_run.resume(paths, tools, regrant=False)


def test_finish_is_for_this_version(conn: sqlite3.Connection, clock: FakeClock) -> None:
    with pytest.raises(InvalidInputError, match="this service is"):
        upgrade_state.finish(conn, clock, {"from": "0.0.1", "to": "9.9.9"})
    rec = upgrade_state.finish(conn, clock, {"from": "0.0.1", "to": __version__})
    row = conn.execute("SELECT value FROM settings WHERE key = 'upgrade.current'").fetchone()
    assert rec["finished_at"] and json.loads(row[0])["to"] == __version__
