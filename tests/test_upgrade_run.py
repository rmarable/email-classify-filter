"""Carrying out an upgrade (V1.5 step 11b; OD-331, OD-376 to OD-381): phase 1 up to the hand-over,
putting things back when the copy or the install fails, phase 2 with its rollback on a failed
migration or start, the re-grant that doesn't roll back, the service's record and the settled
mark."""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

import ecf.cli_upgrade
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


def test_a_missing_old_wheel_stops_nothing(paths: Paths, wheels: tuple[Path, Path]) -> None:
    wheels[0].unlink()
    tools, m, ran = _tools(paths)
    with pytest.raises(EcfError, match="nothing was stopped"):
        _start(paths, tools, wheels)
    assert m.calls == [] and ran == []


def test_a_failure_after_the_stop_starts_the_old_service_again(
    paths: Paths, wheels: tuple[Path, Path]
) -> None:
    tools, m, ran = _tools(paths)
    real_run = tools.run

    def run(args: list[str]) -> int:
        code = real_run(args)
        if args[1] == "snapshot":
            wheels[0].unlink()  # the wheel vanishes between the check and the copy
        return code

    tools.run = run
    with pytest.raises(EcfError, match=r"FileNotFoundError.*the service is started again"):
        _start(paths, tools, wheels)
    assert m.calls == ["stop", "start"] and len(ran) == 1  # no install was tried
    assert upgrade_run.read_state(paths) is None


def _v1_snapshot(paths: Paths, ran: list[list[str]], receipt: list[Path]
                 ) -> upgrade_run.Run:  # fmt: skip
    """`ecf-server snapshot` as v1.0.0 has it (the folder deleted and made again) and `uv tool
    install`, which records the wheel it installed from in `receipt`."""

    def run(args: list[str]) -> int:
        ran.append(args)
        if args[1] == "snapshot":
            folder = paths.data_dir / "upgrades" / args[-1]
            if folder.exists():
                shutil.rmtree(folder)
            folder.mkdir(parents=True)
            (folder / "ecf.db").write_bytes(paths.db.read_bytes())
        elif args[1:4] == ["tool", "install", "--force"]:
            assert Path(args[4]).is_file(), f"uv installs from a missing file: {args[4]}"
            receipt[0] = Path(args[4])
        return 0

    return run


def test_upgrade_rollback_and_the_same_upgrade_again(
    paths: Paths, wheels: tuple[Path, Path]
) -> None:
    """The v2.0.0-rc2 release gate (2026-10-09): 1.0.0 -> rc2, `--to v1.0.0`, -> rc2 again. The
    rollback left uv's receipt naming the wheel in `upgrades/1.0.0-to-2.0.0rc2/`; the second
    upgrade's snapshot deleted that folder and its copy of the receipt's wheel failed with the
    service stopped. Phase 1 of the second upgrade is v1.0.0's code, so the rollback must leave
    the receipt pointing outside `upgrades/`."""
    old, new = wheels
    receipt = [old]
    tools, m, ran = _tools(paths)
    tools.run = _v1_snapshot(paths, ran, receipt)
    upgrades = paths.data_dir / "upgrades"
    label = upgrades / "0.1.0-to-0.2.0"
    for attempt in (1, 2):
        with pytest.raises(Handover):  # phase 1 hands over; the new version is installed
            upgrade_run.start(paths, tools, old_version="0.1.0", new_version="0.2.0",
                              new_wheel=new, old_wheel=receipt[0], pin_changes=[],
                              affected=[])  # fmt: skip
        assert receipt[0] == new and (label / old.name).read_bytes() == b"old wheel"
        if attempt == 1:
            old.unlink()  # the wheel 0.1.0 was first installed from needn't stay
            upgrade_run.downgrade(paths, tools, version="0.1.0", current="0.2.0",
                                  settled=False)  # fmt: skip
            assert not receipt[0].is_relative_to(upgrades)
            assert receipt[0] == paths.data_dir / "releases" / "v0.1.0" / old.name
            assert receipt[0].read_bytes() == b"old wheel"
    assert m.calls == ["stop", "stop", "start", "stop"]  # never stopped and left down


def test_a_receipt_inside_a_snapshot_folder_is_kept_before_the_snapshot(
    paths: Paths, wheels: tuple[Path, Path]
) -> None:
    """An install rolled back by rc2 or earlier: the receipt names the wheel inside the folder the
    snapshot is about to replace. A v2 phase 1 keeps a copy in `releases/` first."""
    label = paths.data_dir / "upgrades" / "0.1.0-to-0.2.0"
    label.mkdir(parents=True)
    inside = label / wheels[0].name
    inside.write_bytes(b"old wheel")
    receipt = [inside]
    tools, m, ran = _tools(paths, {f"install:{wheels[1].name}": 1})
    real_run = tools.run
    v1 = _v1_snapshot(paths, [], receipt)

    def run(args: list[str]) -> int:
        v1(args)
        return real_run(args)

    tools.run = run
    with pytest.raises(EcfError, match="old one is back"):
        upgrade_run.start(paths, tools, old_version="0.1.0", new_version="0.2.0",
                          new_wheel=wheels[1], old_wheel=inside, pin_changes=[],
                          affected=[])  # fmt: skip
    stable = paths.data_dir / "releases" / "v0.1.0" / wheels[0].name
    assert inside.read_bytes() == b"old wheel" and stable.read_bytes() == b"old wheel"
    assert ran[-1] == ["/usr/bin/uv", "tool", "install", "--force", str(stable)]
    assert receipt[0] == stable and m.calls == ["stop", "start"]


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
    assert ran[-1][-1] == str(paths.data_dir / "releases" / "v0.1.0" / wheels[0].name)
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
            if method == "GET":
                return super().request(method, path)
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


LOCK = {"main_session": "claude-haiku-5-5", "classifier": "claude-haiku-4-5-20251001",
        "classifier_high": "claude-sonnet-5-5", "actor": "claude-sonnet-5-5",
        "actor_high": "claude-opus-5-5"}  # fmt: skip
PINS = LOCK | {"local": "d" * 64}


def _release_wheel(path: Path, version: str, classifier_schema: int | None) -> Path:
    """A release wheel's data files; v1.x's release.json has no classifier_schema."""
    info: dict[str, Any] = {"product": "email-classify-filter", "version": version,
                            "api_version": 1, "data_format": 2, "schema_version": 33,
                            "min_client": "0.1.0"}  # fmt: skip
    if classifier_schema is not None:
        info["classifier_schema"] = classifier_schema
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ecf_server/data/release.json", json.dumps(info))
        z.writestr("ecf_server/data/models.lock", json.dumps(LOCK))
        z.writestr("ecf_server/data/ollama.lock", json.dumps({"digest": "d" * 64}))
    return path


class NewService:
    """The new service as phase 2 sees it: its upgrade state, and the finish it records."""

    def __init__(self, state: dict[str, Any]) -> None:
        self.state, self.finished = state, list[dict[str, Any]]()

    def __call__(self, _paths: Paths) -> NewService:
        return self

    def __enter__(self) -> NewService:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get(self, path: str, **_: Any) -> Any:
        assert path == "/v1/upgrade/state"
        return self.state

    def request(self, method: str, path: str, json: Any = None, **_: Any) -> Any:
        assert (method, path) == ("POST", "/v1/upgrade/finish")
        self.finished.append(json)
        return json


@pytest.mark.parametrize(("old_schema", "readable", "affected", "changes"), [
    (None, True, ["ap", "b"], ["schema"]),  # from v1.0.0: its pre-check saw nothing
    (2, True, [], []),  # nothing changed: no extra addresses
    (None, False, ["ap", "b"], ["schema"]),  # old wheel unreadable: a 1.x had schema 1
])  # fmt: skip
def test_phase_two_names_addresses_the_old_check_could_not_see(
    paths: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], old_schema: int | None, readable: bool,
    affected: list[str], changes: list[str],
) -> None:  # fmt: skip
    """A v1.0.0 CLI doesn't know classifier_schema, so its phase 1 stores affected=[]; the new
    CLI recomputes after the upgrade (SPEC §11.10; operator decision 2026-10-09)."""
    old = _release_wheel(tmp_path / "old-1.0.0.whl", "1.0.0", old_schema)
    if not readable:
        old.write_bytes(b"not a zip")
    new = _release_wheel(tmp_path / "new-2.0.0.whl", "2.0.0", 2)
    tools, _m, _ran = _tools(paths)
    with pytest.raises(Handover):  # phase 1 as v1.0.0 runs it: nothing affected
        upgrade_run.start(paths, tools, old_version="1.0.0", new_version="2.0.0", new_wheel=new,
                          old_wheel=old, pin_changes=[], affected=[])  # fmt: skip
    service = NewService({"version": "2.0.0", "classifier_schema": 2, "pins": PINS,
                          "pin_users": {"local": ["ap"], "actor": ["b"]}})  # fmt: skip
    monkeypatch.setattr(ecf.upgrade_run, "LocalClient", service)
    tools, _m, _ran = _tools(paths)

    def same(_p: Paths) -> upgrade_run.Tools:
        return tools

    monkeypatch.setattr(ecf.cli_upgrade, "_tools", same)
    ecf.cli_upgrade._continue(paths)  # pyright: ignore[reportPrivateUsage]
    out = capsys.readouterr().out
    assert service.finished[0]["affected"] == affected
    assert service.finished[0]["pin_changes"] == changes
    state = upgrade_run.read_state(paths)
    assert state is not None and state["affected"] == affected
    if affected:
        assert "the classifier schema changed: ap, b drop to assist" in out
    else:
        assert "drop to assist" not in out
    assert "ecf 2.0.0 is running (was 1.0.0)" in out


def test_affected_addresses_read_as_one_or_many() -> None:
    assert ecf.cli_upgrade._drop(["ap"]) == "ap drops"  # pyright: ignore[reportPrivateUsage]
    assert ecf.cli_upgrade._drop(["ap", "b"]) == "ap, b drop"  # pyright: ignore[reportPrivateUsage]
    assert ecf.cli_upgrade._their(["ap"]) == "its"  # pyright: ignore[reportPrivateUsage]
    assert ecf.cli_upgrade._their(["ap", "b"]) == "their"  # pyright: ignore[reportPrivateUsage]


def test_phase_two_keeps_phase_one_lists_without_an_answer(
    paths: Paths, wheels: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_paths: Paths) -> Any:
        raise EcfError("no answer")

    monkeypatch.setattr(ecf.upgrade_run, "LocalClient", refuse)
    state = {"from": "1.0.0", "old_wheel": str(wheels[0]), "pin_changes": ["local"],
             "affected": ["ap"]}  # fmt: skip
    assert upgrade_run._recount(paths, state) == state  # pyright: ignore[reportPrivateUsage]


def test_finish_is_for_this_version(conn: sqlite3.Connection, clock: FakeClock) -> None:
    with pytest.raises(InvalidInputError, match="this service is"):
        upgrade_state.finish(conn, clock, {"from": "0.0.1", "to": "9.9.9"})
    rec = upgrade_state.finish(conn, clock, {"from": "0.0.1", "to": __version__})
    row = conn.execute("SELECT value FROM settings WHERE key = 'upgrade.current'").fetchone()
    assert rec["finished_at"] and json.loads(row[0])["to"] == __version__
