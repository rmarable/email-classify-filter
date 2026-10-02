import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from ecf.paths import Paths
from ecf_server import db
from ecf_server.clock import FakeClock


@pytest.fixture(scope="session")
def dovecot_server() -> Iterator[Any]:
    """One Dovecot container for the whole run (tests/dovecot.py); skipped without Docker."""
    from tests import dovecot  # noqa: PLC0415 - only IMAP tests pay for the import

    yield from dovecot.start()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "ecf.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(db_path)
    db.migrate(c)
    yield c
    c.close()


# ---- real service processes (short /tmp folder: socket path limits) --------------------------


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    d = Path(tempfile.mkdtemp(prefix="ecf-t", dir="/tmp"))
    monkeypatch.setenv("ECF_HOME", str(d))
    yield d
    shutil.rmtree(d, ignore_errors=True)


_STARTED: list[subprocess.Popen[bytes]] = []


def spawn(args: list[str], env: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
    """Start an ecf_server process that the session-end check will make sure is gone."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "ecf_server", *args],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _STARTED.append(proc)
    return proc


def stop(proc: subprocess.Popen[bytes], timeout: float = 30) -> int:
    """SIGTERM, wait, then SIGKILL if needed. Returns the exit code."""
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
    try:
        return proc.wait(timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.wait(10)


@pytest.fixture(autouse=True)
def _fresh_gate_inputs() -> None:
    """The stage tick remembers each address's gate inputs in-process; tests start clean."""
    from ecf_server import stages  # noqa: PLC0415

    stages.SEEN.clear()


@pytest.fixture(scope="session", autouse=True)
def no_leaked_services() -> Iterator[None]:
    yield
    alive = [p for p in _STARTED if p.poll() is None]
    for p in alive:
        p.kill()
        p.wait(10)
    assert not alive, f"{len(alive)} ecf_server process(es) outlived their test"


def start_service(home: Path, install: str = "t") -> subprocess.Popen[bytes]:
    env = {**os.environ, "ECF_HOME": str(home)}
    return spawn(["local", "--install", install, "--tick-seconds", "0.2"], env)


def uds_client(p: Paths) -> httpx.Client:
    return httpx.Client(transport=httpx.HTTPTransport(uds=str(p.socket)), base_url="http://ecf")


def wait_answering(p: Paths, proc: subprocess.Popen[bytes], timeout: float = 15) -> None:
    """Wait until the service answers (a crashed run can leave a stale socket file behind)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(proc.stderr.read().decode() if proc.stderr else "exited")
        if p.socket.exists():
            try:
                with uds_client(p) as c:
                    if c.get("/v1/health").status_code == 200:
                        return
            except httpx.TransportError:
                pass
        time.sleep(0.05)
    raise AssertionError("service never answered")


@pytest.fixture
def running(home: Path) -> Iterator[Paths]:
    p = Paths("t", home)
    proc = start_service(home)
    try:
        wait_answering(p, proc)
        time.sleep(0.5)  # let the timer tick
        yield p
    finally:
        stop(proc)
