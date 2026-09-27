import os
import re
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from ecf.paths import Paths, paths_for
from ecf_server.chat import FakeChat

from .conftest import uds_client, wait_answering


def start_dev(home: Path | None) -> subprocess.Popen[bytes]:
    args = [sys.executable, "-m", "ecf_server", "dev", "--tick-seconds", "0.2"]
    if home is not None:
        args += ["--home", str(home)]
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def auth(p: Paths) -> dict[str, str]:
    return {"Authorization": f"Bearer {p.token.read_text()}"}


def test_dev_service(home: Path) -> None:
    p = Paths("dev", home)
    proc = start_dev(home)
    try:
        wait_answering(p, proc)
        with uds_client(p) as c:
            st = c.get("/v1/status", headers=auth(p)).json()
            assert st["mode"] == "dev" and st["secret_store"]["backend"] == "memory"
            assert c.get("/v1/dev/clock", headers=auth(p)).json()["now"].startswith("2026-10-01")
            ticks = st["ticks"]
            r = c.post("/v1/dev/clock", params={"advance": 14 * 86400}, headers=auth(p))
            assert r.json()["now"].startswith("2026-10-15")
            assert c.get("/v1/status", headers=auth(p)).json()["ticks"] > ticks
            bad = c.post("/v1/dev/clock", params={"advance": -5}, headers=auth(p))
            assert bad.status_code == 400 and bad.json()["code"] == "invalid_input"
            assert c.get("/v1/dev/chat/posts", headers=auth(p)).json() == {"posts": []}
            assert c.delete("/v1/dev/chat/posts", headers=auth(p)).status_code == 200
            work = c.post("/v1/sessions", headers=auth(p)).json()["profile_token"]
            refused = c.get("/v1/dev/clock", headers={"Authorization": f"Bearer {work}"})
            assert refused.status_code == 403
    finally:
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(20) == 0


def test_dev_routes_absent_in_normal_service(running: Paths) -> None:
    with uds_client(running) as c:
        r = c.get("/v1/dev/clock", headers=auth(running))
        assert r.status_code == 404 and r.json()["code"] == "not_found"
        assert c.get("/v1/status", headers=auth(running)).json()["mode"] == "local"


def test_dev_temp_folder_is_removed() -> None:
    proc = start_dev(None)
    assert proc.stderr is not None
    line = proc.stderr.readline().decode()
    m = re.search(r"data in (\S+)", line)
    assert m, line
    data_dir = Path(m.group(1))
    p = Paths("dev", data_dir.parent)
    wait_answering(p, proc)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(20) == 0
    assert not data_dir.parent.exists()


def test_ecf_socket_steers_clients_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECF_SOCKET", "/tmp/elsewhere/ecf.sock")
    monkeypatch.setenv("ECF_HOME", str(tmp_path))
    assert paths_for("x").socket == Path("/tmp/elsewhere/ecf.sock")
    assert paths_for("x").token == Path("/tmp/elsewhere/cli.token")
    assert paths_for("x", for_service=True).socket == tmp_path / "x" / "run" / "ecf.sock"
    monkeypatch.delenv("ECF_SOCKET")
    assert paths_for("x").socket == tmp_path / "x" / "run" / "ecf.sock"


def test_fake_chat_records() -> None:
    chat = FakeChat()
    ref = chat.post("summary", "hello")
    assert chat.posts == [{"ref": ref, "route": "summary", "text": "hello"}]
    chat.clear()
    assert chat.posts == []


def test_env_not_leaking() -> None:
    assert "ECF_SOCKET" not in os.environ
