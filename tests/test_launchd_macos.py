"""Real LaunchAgent round trip (macOS only; run before merging to main). Uses an `ecf-test-*`
install in a short /tmp folder and removes the agent, its plist and the folder afterwards."""

import os
import secrets
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from ecf.paths import Paths
from ecf.service_unit import LaunchdManager

pytestmark = [pytest.mark.macos, pytest.mark.skipif(sys.platform != "darwin", reason="launchd")]


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> Iterator[LaunchdManager]:
    home = Path(tempfile.mkdtemp(prefix="ecf-t", dir="/tmp"))
    monkeypatch.setenv("ECF_HOME", str(home))
    agents = Path.home() / "Library" / "LaunchAgents"
    created_agents_dir = not agents.exists()
    m = LaunchdManager(Paths(f"ecf-test-{secrets.token_hex(3)}", home))
    try:
        yield m
    finally:
        m.uninstall()
        if created_agents_dir and agents.exists() and not any(agents.iterdir()):
            agents.rmdir()
        shutil.rmtree(home, ignore_errors=True)


def answers(p: Paths, timeout: float = 20) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with httpx.Client(
                transport=httpx.HTTPTransport(uds=str(p.socket)), base_url="http://ecf"
            ) as c:
                if c.get("/v1/health").status_code == 200:
                    return True
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    return False


def test_launchagent_round_trip(manager: LaunchdManager) -> None:
    manager.install()
    assert manager.unit_path.exists()
    assert answers(manager.paths)
    s = manager.status()
    assert s.installed and s.running and s.pid
    assert os.environ["ECF_HOME"] in manager.unit_path.read_text()
    manager.stop()
    assert not manager.status().running
    manager.start()
    assert answers(manager.paths)
    manager.uninstall()
    assert not manager.unit_path.exists() and not manager.status().running
