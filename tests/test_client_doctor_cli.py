import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import ecf
from ecf.cli import app
from ecf.client import NOT_RUNNING, LocalClient
from ecf.doctor import (
    Level,
    check_data_dir,
    check_database,
    check_disk_encryption,
    check_unit,
    judge_status,
    run_checks,
)
from ecf.errors import ServiceUnavailableError, UnauthorizedError
from ecf.paths import Paths
from ecf.service_unit import UnitStatus


class FakeManager:
    def __init__(self, status: UnitStatus) -> None:
        self._status = status
        self.unit_path = Path("/nowhere")

    def status(self) -> UnitStatus:
        return self._status

    def install(self) -> None: ...
    def uninstall(self) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def restart(self) -> None: ...


def completed(out: str, rc: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], rc, out, "")


# ---- client


def test_client_status(running: Paths) -> None:
    with LocalClient(running) as c:
        assert c.get("/v1/health", auth=False) == {"ok": True}
        assert c.get("/v1/status")["install"] == "t"


def test_client_wrong_token(running: Paths) -> None:
    running.token.write_text("wrong")
    with LocalClient(running) as c, pytest.raises(UnauthorizedError):
        c.get("/v1/status")


def test_client_no_service(tmp_path: Path) -> None:
    with LocalClient(Paths("t", tmp_path)) as c, pytest.raises(ServiceUnavailableError) as e:
        c.get("/v1/status")
    assert e.value.detail == NOT_RUNNING


# ---- doctor checks


@pytest.mark.parametrize(
    ("platform", "reply", "level"),
    [
        ("darwin", completed("FileVault is On.\n"), Level.OK),
        ("darwin", completed("FileVault is Off.\n"), Level.FAIL),
        ("linux", completed("disk\npart\ncrypt\n"), Level.OK),
        ("linux", completed("disk\npart\n"), Level.WARN),
        ("linux", completed("", 127), Level.WARN),
    ],
)
def test_disk_encryption(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    reply: subprocess.CompletedProcess[str],
    level: Level,
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    assert check_disk_encryption(lambda _args: reply).level is level


@pytest.mark.parametrize(
    ("status", "level"),
    [
        (UnitStatus(False, False), Level.WARN),
        (UnitStatus(True, False, detail="stopped"), Level.FAIL),
        (UnitStatus(True, True, pid=5), Level.OK),
    ],
)
def test_unit_check(status: UnitStatus, level: Level) -> None:
    assert check_unit(FakeManager(status)).level is level


def test_data_dir_permissions(home: Path) -> None:
    p = Paths("t", home)  # short /tmp root, so the socket path check passes
    assert check_data_dir(p)[0].level is Level.WARN  # doesn't exist yet
    p.data_dir.mkdir(mode=0o755)
    p.data_dir.chmod(0o755)
    first = check_data_dir(p)[0]
    assert first.level is Level.FAIL and "chmod 700" in first.fix
    p.data_dir.chmod(0o700)
    assert [c.level for c in check_data_dir(p)] == [Level.OK, Level.OK]


def test_database_check(running: Paths) -> None:
    checks = check_database(running)
    assert checks[0].level is Level.OK and "quick_check: ok" in checks[0].detail
    assert check_database(Paths("x", running.root))[0].level is Level.WARN


def test_run_checks_against_a_running_service(running: Paths) -> None:
    checks = {
        c.name: c
        for c in run_checks(
            running,
            manager=FakeManager(UnitStatus(True, True, 1)),
            run=lambda _a: completed("FileVault is On.\n"),
        )
    }
    for name in (
        "python",
        "sqlite",
        "data folder",
        "socket path",
        "service unit",
        "service",
        "versions",
        "timer",
        "crash breaker",
        "database",
        "disk space",
    ):
        assert checks[name].level is Level.OK, (name, checks[name])
    if sys.platform == "darwin":
        assert checks["secret store"].level is Level.OK
        assert checks["secret store"].detail == "keychain"
    assert stat.S_IMODE(running.data_dir.stat().st_mode) == 0o700


# ---- CLI


def test_cli_status_without_service(home: Path) -> None:
    result = CliRunner().invoke(app, ["--install", "t", "status"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ServiceUnavailableError)


def test_cli_status_and_doctor(running: Paths) -> None:
    r = CliRunner().invoke(app, ["--install", "t", "status"])
    assert r.exit_code == 0, r.output
    assert "service:" in r.output and "breaker:   ok" in r.output
    d = CliRunner().invoke(app, ["--install", "t", "doctor"])
    assert "versions" in d.output and "database" in d.output


def test_cli_rejects_bad_install_name() -> None:
    r = CliRunner().invoke(app, ["--install", "Bad Name", "version"])
    assert r.exit_code == 2


# ---- judge_status branches

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
GOOD_STATUS: dict[str, object] = {
    "pid": 1,
    "version": ecf.__version__,
    "last_tick_at": "2026-10-01T11:59:30.000000Z",
    "breaker": {"recent_crashes": 0, "tripped": False},
    "secret_store": {"backend": "keychain", "interpreter_changed": False},
}


def levels(st: dict[str, object]) -> dict[str, Level]:
    return {c.name: c.level for c in judge_status(st, NOW)}


def test_judge_status_all_good() -> None:
    assert set(levels(GOOD_STATUS).values()) == {Level.OK}


@pytest.mark.parametrize(
    ("change", "name", "level"),
    [
        ({"version": "9.9.9"}, "versions", Level.FAIL),
        ({"last_tick_at": None}, "timer", Level.WARN),
        ({"last_tick_at": "2026-10-01T11:50:00.000000Z"}, "timer", Level.FAIL),
        ({"breaker": {"recent_crashes": 5, "tripped": True}}, "crash breaker", Level.FAIL),
        ({"secret_store": {"backend": None, "detail": "none"}}, "secret store", Level.FAIL),
        (
            {"secret_store": {"backend": "keychain", "interpreter_changed": True}},
            "secret store",
            Level.WARN,
        ),
    ],
)
def test_judge_status_branches(change: dict[str, object], name: str, level: Level) -> None:
    assert levels({**GOOD_STATUS, **change})[name] is level


def test_doctor_exit_code_matches_failures(running: Paths) -> None:
    d = CliRunner().invoke(app, ["--install", "t", "doctor"])
    failed = any(line.lstrip().startswith("FAIL") for line in d.output.splitlines())
    assert d.exit_code == (3 if failed else 0), d.output


def test_sizes_print_in_ecf_megabytes() -> None:
    from ecf import cli  # noqa: PLC0415

    assert cli._mb(51_200_000) == "48.8 MB"  # pyright: ignore[reportPrivateUsage]
    assert cli._mb(64 * 1_048_576) == "64.0 MB"  # pyright: ignore[reportPrivateUsage]
