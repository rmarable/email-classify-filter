"""Runs the real `ecf-server local` process in a short /tmp folder (socket path limits)."""

import os
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

from ecf.paths import Paths
from ecf_server.service import Service

from .conftest import spawn, start_service, stop, uds_client, wait_answering


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
            elif ss["backend"] is None:  # e.g. CI: no desktop session, systemd < 256
                assert "systemd" in ss["detail"] and "Secret Service" in ss["detail"]
        second = start_service(home)
        try:
            assert second.wait(15) == 3
        finally:
            stop(second)
    finally:
        assert stop(proc) == 0
    assert not p.socket.exists() and not p.running_marker.exists()


def test_kill_is_recorded_as_a_crash(home: Path) -> None:
    p = Paths("t", home)
    proc = start_service(home)
    try:
        wait_answering(p, proc)
    finally:
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
        stop(proc)


def test_breaker_trips_and_reset_clears_it(home: Path) -> None:
    p = Paths("t", home)
    for _ in range(5):
        proc = start_service(home)
        try:
            wait_answering(p, proc)
        finally:
            proc.kill()
            proc.wait(10)
    proc = start_service(home)
    try:
        assert proc.wait(15) == 0  # tripped: exits 0 so launchd/systemd don't restart it
    finally:
        stop(proc)
    assert proc.stderr is not None and b"repeated crashes" in proc.stderr.read()
    env = {**os.environ, "ECF_HOME": str(home)}
    subprocess.run(
        [sys.executable, "-m", "ecf_server", "reset-breaker", "--install", "t"], env=env, check=True
    )
    proc = start_service(home)
    try:
        wait_answering(p, proc)
    finally:
        assert stop(proc) == 0


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


def test_rejects_bad_install_names(home: Path) -> None:
    for bad in ("../x", "Bad", "a b"):
        proc = spawn(["local", "--install", bad], {**os.environ, "ECF_HOME": str(home)})
        try:
            assert proc.wait(15) == 2
        finally:
            stop(proc)
    assert not any(home.iterdir())


def test_service_files_are_private(home: Path) -> None:
    p = Paths("t", home)
    old = os.umask(0o022)  # a permissive umask, as launchd gives
    try:
        proc = start_service(home)
    finally:
        os.umask(old)
    try:
        wait_answering(p, proc)
        for f in (p.log, p.crash_state, p.running_marker):
            assert stat.S_IMODE(f.stat().st_mode) == 0o600, f
    finally:
        stop(proc)


def test_two_sigterms_at_once_still_stop_the_service(home: Path) -> None:
    """Found 2026-09-29: the handler set the stop Event, which can deadlock when the signal lands
    while the main thread holds the Event's lock inside `stop.wait`; two SIGTERMs (a `pkill` plus
    the one `uv run` forwards) left a dev service hung."""
    p = Paths("t", home)
    proc = start_service(home)
    wait_answering(p, proc)
    for _ in range(3):
        proc.send_signal(signal.SIGTERM)
    assert proc.wait(30) == 0


def test_the_signal_handler_takes_no_lock(tmp_path: Path) -> None:
    """Deterministic form of the above: the handler runs while the stop Event's lock is held."""
    svc = Service(Paths("t", tmp_path))
    done = threading.Event()

    def handler_while_locked() -> None:
        cond: Any = cast("Any", svc.stop)._cond  # the Event's lock, held inside stop.wait
        with cond:
            svc._on_signal(signal.SIGTERM, None)  # pyright: ignore[reportPrivateUsage]
        done.set()

    t = threading.Thread(target=handler_while_locked, daemon=True)
    t.start()
    assert done.wait(5), "the signal handler blocked on the stop Event's lock"
    assert svc._signalled == signal.SIGTERM  # pyright: ignore[reportPrivateUsage]
    assert not svc.stop.is_set()  # the main loop sets it, outside the handler
