import plistlib
import subprocess
from pathlib import Path

import pytest

from ecf.errors import ServiceUnavailableError
from ecf.paths import Paths
from ecf.service_unit import (
    LaunchdManager,
    SystemdManager,
    render_launchd_plist,
    render_systemd_unit,
)

SERVER = Path("/opt/ecf/bin/ecf-server")


class FakeRunner:
    def __init__(self, replies: dict[str, subprocess.CompletedProcess[str]] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.replies = replies or {}

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        for key, reply in self.replies.items():
            if key in " ".join(args):
                return reply
        return subprocess.CompletedProcess(args, 0, "", "")


def done(rc: int = 0, out: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], rc, out, "")


def test_plist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ECF_HOME", raising=False)
    p = Paths("default", tmp_path)
    d = plistlib.loads(render_launchd_plist("default", SERVER, p))
    assert d["Label"] == "com.email-classify-filter.default"
    assert d["ProgramArguments"] == [str(SERVER), "local", "--install", "default"]
    assert d["KeepAlive"] == {"SuccessfulExit": False} and d["RunAtLoad"] is True
    assert d["ExitTimeOut"] == 60 and d["ProcessType"] == "Interactive"
    assert "EnvironmentVariables" not in d
    monkeypatch.setenv("ECF_HOME", "/tmp/x")
    d = plistlib.loads(render_launchd_plist("default", SERVER, p))
    assert d["EnvironmentVariables"] == {"ECF_HOME": "/tmp/x"}


def test_systemd_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECF_HOME", raising=False)
    text = render_systemd_unit("default", SERVER, [Path("/d/creds/mailbox.billing.cred")])
    for line in (
        "Restart=on-failure",
        "StartLimitIntervalSec=600",
        "StartLimitBurst=5",
        "TimeoutStopSec=60",
        "UMask=0077",
        f"ExecStart={SERVER} local --install default",
        "LoadCredentialEncrypted=mailbox.billing:/d/creds/mailbox.billing.cred",
        "WantedBy=default.target",
    ):
        assert line in text.splitlines()


def test_launchd_install_start_stop(tmp_path: Path) -> None:
    runner = FakeRunner({"launchctl print": done(113)})  # not loaded yet
    m = LaunchdManager(
        Paths("t", tmp_path / "data"), runner=runner, server=SERVER, agents_dir=tmp_path / "agents"
    )
    m.install()
    assert m.unit_path.exists()
    assert runner.calls[-1][:2] == ["launchctl", "bootstrap"]
    m.start()
    assert [str(SERVER), "reset-breaker", "--install", "t"] in runner.calls
    m.uninstall()
    assert not m.unit_path.exists()


def test_launchd_start_needs_install(tmp_path: Path) -> None:
    m = LaunchdManager(
        Paths("t", tmp_path), runner=FakeRunner(), server=SERVER, agents_dir=tmp_path / "agents"
    )
    with pytest.raises(ServiceUnavailableError, match="isn't installed"):
        m.start()


def test_launchd_status_parsing(tmp_path: Path) -> None:
    out = "gui/501/x = {\n\tstate = running\n\tpid = 4242\n\tlast exit code = 0\n}\n"
    m = LaunchdManager(
        Paths("t", tmp_path),
        runner=FakeRunner({"launchctl print": done(0, out)}),
        server=SERVER,
        agents_dir=tmp_path,
    )
    s = m.status()
    assert (s.running, s.pid, s.last_exit) == (True, 4242, 0)
    m2 = LaunchdManager(
        Paths("t", tmp_path),
        runner=FakeRunner({"launchctl print": done(113)}),
        server=SERVER,
        agents_dir=tmp_path,
    )
    assert not m2.status().running


def test_launchctl_failure_is_reported(tmp_path: Path) -> None:
    runner = FakeRunner({"launchctl print": done(113), "bootstrap": done(5)})
    m = LaunchdManager(Paths("t", tmp_path), runner=runner, server=SERVER, agents_dir=tmp_path)
    with pytest.raises(ServiceUnavailableError, match="bootstrap"):
        m.install()


def test_systemd_sequences_and_status(tmp_path: Path) -> None:
    show = "ActiveState=active\nMainPID=77\nExecMainStatus=0\nLoadState=loaded\n"
    runner = FakeRunner({"show": done(0, show)})
    m = SystemdManager(
        Paths("t", tmp_path / "data"), runner=runner, server=SERVER, units_dir=tmp_path / "units"
    )
    m.install()
    assert ["systemctl", "--user", "enable", "--now", "ecf-t.service"] in runner.calls
    m.start()
    i = runner.calls.index([str(SERVER), "reset-breaker", "--install", "t"])
    assert runner.calls[i + 1] == ["systemctl", "--user", "reset-failed", "ecf-t.service"]
    s = m.status()
    assert (s.installed, s.running, s.pid, s.last_exit) == (True, True, 77, 0)
