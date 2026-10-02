"""ecf's Ollama login item (V1.3 step 1c; SPEC §7.5, OD-246)."""

from __future__ import annotations

import plistlib
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from ecf import ollama_unit
from ecf.ollama_unit import LaunchdOllama, SystemdOllama

OLLAMA = Path("/opt/homebrew/bin/ollama")


class FakeRunner:
    def __init__(self, loaded: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.loaded = loaded

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if args[:2] == ["launchctl", "print"]:
            rc = 0 if self.loaded else 113
            return subprocess.CompletedProcess(args, rc, "\tstate = running\n", "")
        if args[:2] == ["launchctl", "bootstrap"]:
            self.loaded = True
        if args[:2] == ["launchctl", "bootout"]:
            self.loaded = False
        return subprocess.CompletedProcess(args, 0, "", "")


def test_the_plist_fixes_ecfs_settings(tmp_path: Path) -> None:
    raw = plistlib.loads(ollama_unit.render_plist(OLLAMA, Path("/Users/me"), tmp_path))
    assert raw["ProgramArguments"] == [str(OLLAMA), "serve"]
    env = raw["EnvironmentVariables"]
    assert env == {"HOME": "/Users/me", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                   "OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_NUM_PARALLEL": "1",
                   "OLLAMA_NO_CLOUD": "1"}  # fmt: skip
    for unwanted in ("OLLAMA_FLASH_ATTENTION", "OLLAMA_KV_CACHE_TYPE", "OLLAMA_DEBUG",
                     "OLLAMA_DEBUG_LOG_REQUESTS", "OLLAMA_ORIGINS"):  # fmt: skip
        assert unwanted not in env
    assert raw["ProcessType"] == "Interactive" and raw["Umask"] == 0o077
    assert raw["StandardErrorPath"] == str(tmp_path / "ollama" / "ollama.log")


def test_launchd_install_replaces_a_loaded_item_and_uninstall_removes_it(tmp_path: Path) -> None:
    run = FakeRunner(loaded=True)
    unit = LaunchdOllama(tmp_path / "root", runner=run, ollama=OLLAMA, agents_dir=tmp_path / "la",
                         home=tmp_path)  # fmt: skip
    unit.install()
    verbs = [c[1] for c in run.calls if c[0] == "launchctl"]
    assert verbs == ["print", "bootout", "bootstrap"]
    assert (
        unit.unit_path.exists() and (tmp_path / "root" / "ollama").stat().st_mode & 0o777 == 0o700
    )
    assert unit.status().running
    unit.uninstall()
    assert not unit.unit_path.exists() and not run.loaded


def test_systemd_unit_text(tmp_path: Path) -> None:
    text = ollama_unit.render_systemd(OLLAMA, Path("/home/me"), tmp_path)
    assert f'ExecStart="{OLLAMA}" serve' in text
    assert (
        'Environment="OLLAMA_NUM_PARALLEL=1"' in text and 'Environment="OLLAMA_NO_CLOUD=1"' in text
    )
    assert "UMask=0077" in text


def test_systemd_install_enables_and_restarts(tmp_path: Path) -> None:
    run = FakeRunner()
    unit = SystemdOllama(tmp_path, runner=run, ollama=OLLAMA, units_dir=tmp_path / "u",
                         home=tmp_path)  # fmt: skip
    unit.install()
    assert [c[2] for c in run.calls] == ["daemon-reload", "enable", "restart"]


def test_the_login_item_keeps_homebrews_link_and_says_when_its_program_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cellar = tmp_path / "Cellar" / "ollama" / "0.35.0" / "bin" / "ollama"
    cellar.parent.mkdir(parents=True)
    cellar.write_text("")
    link = tmp_path / "bin" / "ollama"
    link.parent.mkdir()
    link.symlink_to(cellar)

    def which(_name: str) -> str:
        return str(link)

    monkeypatch.setattr(ollama_unit.shutil, "which", which)
    assert ollama_unit.ollama_path() == link  # not the versioned Cellar path an upgrade removes
    run = FakeRunner()
    unit = LaunchdOllama(tmp_path / "root", runner=run, ollama=cellar, agents_dir=tmp_path / "la",
                         home=tmp_path)  # fmt: skip
    unit.install()
    assert unit.program() == str(cellar) and unit.status().detail == "running"
    cellar.unlink()  # `brew upgrade ollama` removed the old version
    assert unit.status().detail.startswith(f"its program {cellar} is gone")
    systemd = SystemdOllama(tmp_path, runner=run, ollama=cellar, units_dir=tmp_path / "u",
                            home=tmp_path)  # fmt: skip
    systemd.install()
    assert systemd.program() == str(cellar)
    assert "ecf models serve install" in systemd.status().detail


def _assert_ecfs_settings() -> None:
    from ecf_server import ollama  # noqa: PLC0415

    deadline = time.monotonic() + 30
    listener = None
    while time.monotonic() < deadline and listener is None:
        listener = ollama.find_listener()
        time.sleep(0.5)
    assert listener is not None and listener.addresses == ("127.0.0.1:11434",)
    assert listener.pid is not None
    env = ollama.server_env(listener.pid)
    assert env["OLLAMA_NUM_PARALLEL"] == "1" and env["OLLAMA_NO_CLOUD"] == "1"
    assert "OLLAMA_DEBUG_LOG_REQUESTS" not in env


def _serving() -> bool:
    try:
        httpx.get("http://127.0.0.1:11434/api/version", timeout=1)
    except httpx.TransportError:
        return False
    return True


@pytest.mark.macos
@pytest.mark.skipif(sys.platform != "darwin" or not OLLAMA.exists(), reason="needs Ollama on macOS")
def test_a_real_login_item_runs_ollama_with_ecfs_settings(tmp_path: Path) -> None:
    """With port 11434 free, a login item under an ecf-test-* label is started, checked and
    removed. When something already serves the port (ecf's own login item on a set-up Mac), that
    server is checked instead: it must be ecf's, with ecf's settings (the merge gate allows no
    skips)."""
    if _serving():
        assert LaunchdOllama(tmp_path).status().running, (
            "port 11434 is served by an Ollama that isn't ecf's login item; stop it"
            " (ecf models serve install replaces it)"
        )
        _assert_ecfs_settings()
        return
    unit = LaunchdOllama(tmp_path / "root", ollama=OLLAMA, agents_dir=tmp_path,
                         label="ecf-test-ollama")  # fmt: skip
    unit.install()
    try:
        _assert_ecfs_settings()
    finally:
        unit.uninstall()
    assert not unit.loaded()
