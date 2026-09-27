"""Runs the real `ecf-server local` process in a short /tmp folder (socket path limits)."""

import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from ecf.paths import Paths


@pytest.fixture
def home() -> Iterator[Path]:
    d = Path(tempfile.mkdtemp(prefix="ecf-t", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def start(home: Path, install: str = "t") -> subprocess.Popen[bytes]:
    env = {**os.environ, "ECF_HOME": str(home)}
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ecf_server",
            "local",
            "--install",
            install,
            "--tick-seconds",
            "0.2",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def wait_socket(p: Paths, proc: subprocess.Popen[bytes], timeout: float = 15) -> None:
    """Wait until the service answers (a crashed run can leave a stale socket file behind)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(proc.stderr.read().decode() if proc.stderr else "exited")
        if p.socket.exists():
            try:
                with client(p) as c:
                    if c.get("/v1/health").status_code == 200:
                        return
            except httpx.TransportError:
                pass
        time.sleep(0.05)
    raise AssertionError("service never answered")


def client(p: Paths) -> httpx.Client:
    return httpx.Client(transport=httpx.HTTPTransport(uds=str(p.socket)), base_url="http://ecf")


def test_start_serve_stop(home: Path) -> None:
    p = Paths("t", home)
    proc = start(home)
    try:
        wait_socket(p, proc)
        for path, mode in (
            (p.data_dir, 0o700),
            (p.run_dir, 0o700),
            (p.socket, 0o600),
            (p.token, 0o600),
            (p.db, 0o600),
        ):
            assert stat.S_IMODE(path.stat().st_mode) == mode, path
        with client(p) as c:
            assert c.get("/v1/health").json() == {"ok": True}
            assert c.get("/v1/status").status_code == 401
            time.sleep(0.6)
            s = c.get("/v1/status", headers={"Authorization": f"Bearer {p.token.read_text()}"})
            assert s.status_code == 200 and s.json()["ticks"] >= 1
        second = start(home)
        assert second.wait(15) == 3
    finally:
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(20) == 0
    assert not p.socket.exists() and not p.running_marker.exists()


def test_kill_is_recorded_as_a_crash(home: Path) -> None:
    p = Paths("t", home)
    proc = start(home)
    wait_socket(p, proc)
    proc.kill()
    proc.wait(10)
    assert p.running_marker.exists()  # left behind by the crash
    proc = start(home)
    try:
        wait_socket(p, proc)
        with client(p) as c:
            s = c.get("/v1/status", headers={"Authorization": f"Bearer {p.token.read_text()}"})
            assert s.json()["breaker"] == {"recent_crashes": 1, "tripped": False}
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(20)


def test_breaker_trips_and_reset_clears_it(home: Path) -> None:
    p = Paths("t", home)
    for _ in range(5):
        proc = start(home)
        wait_socket(p, proc)
        proc.kill()
        proc.wait(10)
    proc = start(home)
    assert proc.wait(15) == 0  # tripped: exits 0 so launchd/systemd don't restart it
    assert proc.stderr is not None and b"repeated crashes" in proc.stderr.read()
    env = {**os.environ, "ECF_HOME": str(home)}
    subprocess.run(
        [sys.executable, "-m", "ecf_server", "reset-breaker", "--install", "t"], env=env, check=True
    )
    proc = start(home)
    try:
        wait_socket(p, proc)
    finally:
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(20) == 0


def test_migrate_command(home: Path) -> None:
    env = {**os.environ, "ECF_HOME": str(home)}
    out = subprocess.run(
        [sys.executable, "-m", "ecf_server", "migrate", "--install", "t"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "0001_initial.sql" in out.stdout
    out = subprocess.run(
        [sys.executable, "-m", "ecf_server", "migrate", "--install", "t"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "none" in out.stdout
