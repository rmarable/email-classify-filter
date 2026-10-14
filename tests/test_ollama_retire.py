"""Retiring ecf's Ollama login item at settle (v3.0.0; ADR 0031, R-D13, P13): `ollama_unit.retire`
with a fake runner and folders in tmp, and `upgrade_state.settle` calling it only once the
rollback window closes. No real `launchctl` or `systemctl` runs."""

from __future__ import annotations

import inspect
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ecf import __version__, doctor, ollama_unit
from ecf.ollama_unit import FOLDER, SYSTEMD_UNIT, retire
from ecf.paths import Paths
from ecf_server import upgrade_state
from ecf_server.clock import FakeClock
from ecf_server.service import Service

LABEL = "ecf-test-ollama"


class Runner:
    """Records each command; `launchctl print` answers 0 while the item is loaded."""

    def __init__(self, *, loaded: bool) -> None:
        self.loaded = loaded
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if args[:2] == ["launchctl", "print"]:
            return subprocess.CompletedProcess(args, 0 if self.loaded else 113, "", "")
        if args[:2] == ["launchctl", "bootout"]:
            self.loaded = False
        return subprocess.CompletedProcess(args, 0, "", "")


def _set_up(tmp_path: Path) -> tuple[Path, Path, Path]:
    home, root = tmp_path / "home", tmp_path / "root"
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist/>")
    (root / FOLDER).mkdir(parents=True)
    (root / FOLDER / "ollama.log").write_text("log")
    return home, root, plist


def test_retire_unloads_and_removes_the_login_item_and_the_folder(tmp_path: Path) -> None:
    home, root, plist = _set_up(tmp_path)
    run = Runner(loaded=True)
    done = retire(root, runner=run, home=home, platform="darwin", label=LABEL)
    assert [c[:2] for c in run.calls] == [["launchctl", "print"], ["launchctl", "bootout"]]
    assert all(c[-1].endswith(f"/{LABEL}") for c in run.calls)
    assert not plist.exists() and not (root / FOLDER).exists()
    assert len(done) == 3


def test_retire_is_idempotent(tmp_path: Path) -> None:
    home, root, _ = _set_up(tmp_path)
    run = Runner(loaded=True)
    retire(root, runner=run, home=home, platform="darwin", label=LABEL)
    run.calls.clear()
    assert retire(root, runner=run, home=home, platform="darwin", label=LABEL) == []
    assert [c[:2] for c in run.calls] == [["launchctl", "print"]]  # looked, changed nothing


def test_retire_with_nothing_there_is_a_no_op(tmp_path: Path) -> None:
    run = Runner(loaded=False)
    root = tmp_path / "root"
    root.mkdir()
    (root / "default").mkdir()  # an install's folder is never touched
    assert retire(root, runner=run, home=tmp_path / "home", platform="darwin", label=LABEL) == []
    assert [c[1] for c in run.calls] == ["print"] and (root / "default").is_dir()


def test_retire_never_follows_a_symlinked_folder(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep").write_text("x")
    root = tmp_path / "root"
    root.mkdir()
    (root / FOLDER).symlink_to(target)
    retire(root, runner=Runner(loaded=False), home=tmp_path / "home", platform="darwin",
           label=LABEL)  # fmt: skip
    assert not (root / FOLDER).exists() and (target / "keep").exists()


def test_retire_on_linux_removes_the_user_unit(tmp_path: Path) -> None:
    home, root = tmp_path / "home", tmp_path / "root"
    unit = home / ".config" / "systemd" / "user" / SYSTEMD_UNIT
    unit.parent.mkdir(parents=True)
    unit.write_text("[Unit]\n")
    run = Runner(loaded=False)
    assert retire(root, runner=run, home=home, platform="linux") == [str(unit)]
    assert run.calls == [["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT],
                         ["systemctl", "--user", "daemon-reload"]]  # fmt: skip
    assert not unit.exists()
    run.calls.clear()
    assert retire(root, runner=run, home=home, platform="linux") == [] and run.calls == []


def _upgraded(conn: sqlite3.Connection, clock: FakeClock) -> None:
    upgrade_state.finish(conn, clock, {"from": "2.2.3", "to": __version__, "label": "x",
                                       "started_at": "2026-10-14T00:00:00.000000Z"})  # fmt: skip


def test_settle_retires_only_when_the_rollback_window_closes(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    calls: list[int] = []
    assert upgrade_state.settle(conn, clock, on_settled=lambda: calls.append(1)) is False
    assert calls == []  # no upgrade on record: nothing to settle, nothing retired
    _upgraded(conn, clock)
    assert calls == []  # finished, not settled: a rollback still needs the login item
    assert upgrade_state.settle(conn, clock, on_settled=lambda: calls.append(1)) is True
    assert calls == [1]
    assert upgrade_state.settle(conn, clock, on_settled=lambda: calls.append(1)) is False
    assert calls == [1]  # once


def test_a_failed_retire_still_settles(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _upgraded(conn, clock)

    def boom() -> None:
        raise OSError("launchctl went away")

    assert upgrade_state.settle(conn, clock, on_settled=boom) is True
    rec = upgrade_state.current(conn)
    assert rec is not None and rec["settled_at"] is not None


def test_the_service_retires_at_settle_and_a_dev_service_never(
    conn: sqlite3.Connection, db_path: Path, tmp_path: Path, clock: FakeClock,
    _no_ollama_retire: list[Path],
) -> None:  # fmt: skip
    """`Service._settle_upgrade` passes `retire` for the data root (conftest records it instead
    of touching this computer); `ecf-server dev` passes nothing."""
    _upgraded(conn, clock)
    dev = Service(Paths(install="t", root=tmp_path), clock, dev=True)
    dev.state.db_path = db_path
    dev._settle_upgrade()  # pyright: ignore[reportPrivateUsage]
    assert _no_ollama_retire == [] and upgrade_state.current(conn)["settled_at"] is not None  # pyright: ignore[reportOptionalSubscript]
    _upgraded(conn, clock)  # a later upgrade, to settle again
    svc = Service(Paths(install="t", root=tmp_path), clock)
    svc.state.db_path = db_path
    svc._settle_upgrade()  # pyright: ignore[reportPrivateUsage]
    assert _no_ollama_retire == [tmp_path]


def test_each_command_has_a_20_s_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review R9: `retire` runs in the service's timer thread; a hung launchctl can't hold it."""
    seen: list[float] = []

    def fake(args: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append(kw["timeout"])
        raise subprocess.TimeoutExpired(args, kw["timeout"])

    monkeypatch.setattr(ollama_unit.subprocess, "run", fake)
    out = ollama_unit.run_short(["launchctl", "print", "gui/1/x"])
    assert seen == [20] and out.returncode == 124
    assert inspect.signature(retire).parameters["runner"].default is ollama_unit.run_short


def test_doctor_names_a_leftover_login_item(tmp_path: Path) -> None:
    """Review R8: an install upgraded another way keeps the item; doctor names it and the commands
    that remove it, reading files only (nothing is run)."""
    home, _root, plist = _set_up(tmp_path)
    real = home / "Library" / "LaunchAgents" / f"{ollama_unit.LABEL}.plist"
    assert doctor.check_ollama_item(home, "darwin") == []
    plist.rename(real)
    (check,) = doctor.check_ollama_item(home, "darwin")
    assert check.level is doctor.Level.WARN and str(real) in check.detail
    assert f"launchctl bootout gui/$(id -u)/{ollama_unit.LABEL}" in check.fix
    unit = home / ".config" / "systemd" / "user" / SYSTEMD_UNIT
    unit.parent.mkdir(parents=True)
    unit.write_text("[Unit]")
    (check,) = doctor.check_ollama_item(home, "linux")
    assert f"systemctl --user disable --now {SYSTEMD_UNIT}" in check.fix
