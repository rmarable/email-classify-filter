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

import httpx
import pytest

from ecf.paths import Paths
from ecf_server import db
from ecf_server.clock import FakeClock


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


def start_service(home: Path, install: str = "t") -> subprocess.Popen[bytes]:
    env = {**os.environ, "ECF_HOME": str(home)}
    args = ["local", "--install", install, "--tick-seconds", "0.2"]
    return subprocess.Popen(
        [sys.executable, "-m", "ecf_server", *args],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


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
        proc.send_signal(signal.SIGTERM)
        proc.wait(20)
