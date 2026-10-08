import re
import sqlite3
import subprocess
from pathlib import Path

import pytest

from ecf.paths import Paths, default_root, paths_for
from ecf_server.__main__ import dev_home_refusal
from ecf_server.chat import Card, FakeChat, RouteRef

from .conftest import spawn, stop, uds_client, wait_answering


def start_dev(home: Path | None) -> subprocess.Popen[bytes]:
    args = ["dev", "--tick-seconds", "0.2"]
    if home is not None:
        args += ["--home", str(home)]
    return spawn(args)


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
        assert stop(proc) == 0


def test_dev_routes_absent_in_normal_service(running: Paths) -> None:
    with uds_client(running) as c:
        r = c.get("/v1/dev/clock", headers=auth(running))
        assert r.status_code == 404 and r.json()["code"] == "not_found"
        assert c.get("/v1/status", headers=auth(running)).json()["mode"] == "local"


def test_dev_temp_folder_is_removed() -> None:
    proc = start_dev(None)
    try:
        assert proc.stderr is not None
        line = proc.stderr.readline().decode()  # the first line is printed before anything else
        m = re.search(r"data in (\S+)", line)
        assert m, line
        data_dir = Path(m.group(1))
        wait_answering(Paths("dev", data_dir.parent), proc)
    finally:
        code = stop(proc)
    assert code == 0
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
    ref = chat.post(RouteRef("summary"), Card("hello"))
    assert [p["card"]["title"] for p in chat.posts] == ["hello"] and ref.route.channel == "summary"
    chat.clear()
    assert chat.posts == []


def test_dev_clock_rejects_non_numbers(home: Path) -> None:
    p = Paths("dev", home)
    proc = start_dev(home)
    try:
        wait_answering(p, proc)
        with uds_client(p) as c:
            r = c.post("/v1/dev/clock", params={"advance": "soon"}, headers=auth(p))
            assert r.status_code == 400 and r.json()["code"] == "invalid_input"
    finally:
        stop(proc)


def test_dev_refuses_a_home_inside_the_default_root(tmp_path: Path) -> None:
    """R183, R206 (OD-468): a dev service approves every step-up, so never over real data."""
    inside = default_root() / "dev-test"
    assert "data root" in (dev_home_refusal(inside, Paths("dev", inside)) or "")
    assert dev_home_refusal(tmp_path, Paths("dev", tmp_path)) is None  # nothing there yet


def test_dev_refuses_a_home_holding_an_initialised_install(tmp_path: Path) -> None:
    p = Paths("dev", tmp_path)
    p.data_dir.mkdir(parents=True)
    conn = sqlite3.connect(p.db)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    assert dev_home_refusal(tmp_path, p) is None  # a dev database: no role
    conn.execute("INSERT INTO settings VALUES ('install_role', '\"test\"')")
    conn.commit()
    conn.close()
    assert "ecf init" in (dev_home_refusal(tmp_path, p) or "")
    p.db.write_bytes(b"not a database")
    assert "can't read" in (dev_home_refusal(tmp_path, p) or "")  # fail closed
