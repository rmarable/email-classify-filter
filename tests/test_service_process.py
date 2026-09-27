"""Runs the real `ecf-server local` process in a short /tmp folder (socket path limits)."""

import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

from ecf.paths import Paths

from .conftest import start_service, uds_client, wait_answering


def test_start_serve_stop(home: Path) -> None:
    p = Paths("t", home)
    proc = start_service(home)
    try:
        wait_answering(p, proc)
        for path, mode in (
            (p.data_dir, 0o700),
            (p.run_dir, 0o700),
            (p.socket, 0o600),
            (p.token, 0o600),
            (p.db, 0o600),
        ):
            assert stat.S_IMODE(path.stat().st_mode) == mode, path
        with uds_client(p) as c:
            assert c.get("/v1/health").json() == {"ok": True}
            assert c.get("/v1/status").status_code == 401
            time.sleep(0.6)
            s = c.get("/v1/status", headers={"Authorization": f"Bearer {p.token.read_text()}"})
            assert s.status_code == 200 and s.json()["ticks"] >= 1
            ss = s.json()["secret_store"]
            assert "backend" in ss and "interpreter_changed" in ss
            if sys.platform == "darwin":
                assert ss["backend"] == "keychain"
        second = start_service(home)
        assert second.wait(15) == 3
    finally:
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(20) == 0
    assert not p.socket.exists() and not p.running_marker.exists()


def test_kill_is_recorded_as_a_crash(home: Path) -> None:
    p = Paths("t", home)
    proc = start_service(home)
    wait_answering(p, proc)
    proc.kill()
    proc.wait(10)
    assert p.running_marker.exists()  # left behind by the crash
    proc = start_service(home)
    try:
        wait_answering(p, proc)
        with uds_client(p) as c:
            s = c.get("/v1/status", headers={"Authorization": f"Bearer {p.token.read_text()}"})
            assert s.json()["breaker"] == {"recent_crashes": 1, "tripped": False}
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(20)


def test_breaker_trips_and_reset_clears_it(home: Path) -> None:
    p = Paths("t", home)
    for _ in range(5):
        proc = start_service(home)
        wait_answering(p, proc)
        proc.kill()
        proc.wait(10)
    proc = start_service(home)
    assert proc.wait(15) == 0  # tripped: exits 0 so launchd/systemd don't restart it
    assert proc.stderr is not None and b"repeated crashes" in proc.stderr.read()
    env = {**os.environ, "ECF_HOME": str(home)}
    subprocess.run(
        [sys.executable, "-m", "ecf_server", "reset-breaker", "--install", "t"], env=env, check=True
    )
    proc = start_service(home)
    try:
        wait_answering(p, proc)
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
