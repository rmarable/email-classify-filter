"""The local model in the service (V1.3 step 1b): the check and its alerts, `ecf models install`
and status, the routes, doctor's judgement and the daily-summary line (SPEC §4.3, §7.5, §13.2;
OD-240, OD-242, OD-245)."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from ecf.doctor import Level, judge_models
from ecf.errors import ConflictError
from ecf_server import alerts, daily, models, ollama, slack_admin
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.ollama import Client

PIN = ollama.load_pin()
ME = "U0ME1"
LSOF_OK = "p4242\nn127.0.0.1:11434\n"
PS_ENV = ("/opt/homebrew/bin/ollama serve HOME=/Users/me PATH=/usr/bin OLLAMA_NUM_PARALLEL=1"
          " OLLAMA_NO_CLOUD=1")  # fmt: skip


def _run(lsof: str | Exception = LSOF_OK, ps: str = PS_ENV) -> Callable[[list[str]], str]:
    def run(cmd: list[str]) -> str:
        out = lsof if "lsof" in cmd[0] else ps
        if isinstance(out, Exception):
            raise out
        return out

    return run


def check_kw(**kw: Any) -> dict[str, Any]:
    return {"run": _run(**kw), "platform": "darwin"}


class FakeOllama:
    """Enough of Ollama's API for these tests: tags, version, pull and copy."""

    def __init__(self, *, installed: bool = True, upstream: str = PIN.digest) -> None:
        self.models: dict[str, str] = {PIN.tag: upstream}
        if installed:
            self.models[PIN.ecf_tag] = PIN.digest
        self.upstream = upstream

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.35.0"})
        if path == "/api/tags":
            listed = [{"name": k, "digest": v} for k, v in self.models.items()]
            return httpx.Response(200, json={"models": listed})
        if path == "/api/pull":
            self.models[PIN.tag] = self.upstream
            lines = [{"status": "pulling manifest"},
                     {"status": "pulling bb72", "total": 100, "completed": 50},
                     {"status": "success"}]  # fmt: skip
            return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
        if path == "/api/copy":
            body = json.loads(req.content)
            self.models[body["destination"]] = self.models[body["source"]]
            return httpx.Response(200)
        return httpx.Response(404, json={"error": "not found"})

    def client(self) -> Client:
        return Client(transport=httpx.MockTransport(self.handler))


def _slack(conn: sqlite3.Connection, clock: FakeClock) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", ME),
                     ("slack_summary_channel", "CSUM")):  # fmt: skip
            slack_admin.put_setting(conn, k, v, now, actor="test")


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")
    return [json.loads(r[0]) for r in rows]


def _open(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT kind FROM alerts WHERE resolved_at IS NULL")]


# ---- the check and its alerts ---------------------------------------------------------------


def test_ollama_not_running_opens_one_system_error_then_resolves(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    n = FakeNotifier()
    nobody = subprocess.CalledProcessError(1, ["lsof"], output="")
    assert models.check(conn, clock, n, FakeOllama().client(), **check_kw(lsof=nobody)) is None
    assert models.check(conn, clock, n, FakeOllama().client(), **check_kw(lsof=nobody)) is None
    assert _open(conn) == ["local_model"]
    assert n.sent == [("[ecf-alert] System Error",
                       "Ollama isn't running (nothing listens on port 11434). Model work is"
                       " stopped until it's fixed: start Ollama.")]  # fmt: skip
    assert models.check(conn, clock, n, FakeOllama().client(), **check_kw()) is not None
    assert _open(conn) == []
    assert n.sent[-1][0] == "[ecf-alert] Resolved: System Error"


@pytest.mark.parametrize(
    ("kw", "words"),
    [
        ({"lsof": "p1\nn*:11434\n"}, "listens beyond this computer"),
        ({"lsof": FileNotFoundError("lsof")}, "can't confirm Ollama is safe"),
        ({"ps": PS_ENV + " OLLAMA_DEBUG_LOG_REQUESTS=1"}, "email text included, to disk"),
    ],
)
def test_unsafe_or_unconfirmed_is_loud_and_mentions_you(
    conn: sqlite3.Connection, clock: FakeClock, kw: dict[str, Any], words: str
) -> None:
    _slack(conn, clock)
    n = FakeNotifier()
    assert models.check(conn, clock, n, FakeOllama().client(), **check_kw(**kw)) is None
    assert _open(conn) == ["local_model_unsafe"]
    assert words in n.sent[0][1]
    assert alerts.sweep(conn, clock) == 1
    [post] = _posts(conn)
    assert post["card"]["title"] == "[ecf-alert] System Error"
    assert post["card"]["mention"] == ME and words in post["card"]["text"]


def test_a_changed_model_is_loud(conn: sqlite3.Connection, clock: FakeClock) -> None:
    fake = FakeOllama()
    fake.models[PIN.ecf_tag] = "b" * 64
    assert models.check(conn, clock, FakeNotifier(), fake.client(), **check_kw()) is None
    assert _open(conn) == ["local_model_unsafe"]


def test_a_quiet_alert_is_not_mentioned(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _slack(conn, clock)
    models.check(conn, clock, FakeNotifier(), FakeOllama(installed=False).client(), **check_kw())
    assert _open(conn) == ["local_model"]
    alerts.sweep(conn, clock)
    assert _posts(conn)[0]["card"]["mention"] == ""


def test_switching_cause_keeps_one_alert_open(conn: sqlite3.Connection, clock: FakeClock) -> None:
    n = FakeNotifier()
    models.check(conn, clock, n, FakeOllama(installed=False).client(), **check_kw())
    models.check(conn, clock, n, FakeOllama().client(), **check_kw(lsof="p1\nn*:11434\n"))
    assert _open(conn) == ["local_model_unsafe"]


# ---- install and status -----------------------------------------------------------------------


def _inline(work: Callable[[], None]) -> None:
    work()


@pytest.fixture(autouse=True)
def _idle() -> None:
    models.INSTALLS.set(state="idle", status="", error="", completed=0, total=0)


def test_install_pulls_checks_and_copies(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    from ecf_server import db  # noqa: PLC0415

    fake = FakeOllama(installed=False)
    got = models.start_install(lambda: db.connect(db_path), clock, fake.client, spawn=_inline)
    assert got["state"] == "done", got  # inline: the work ran before the snapshot
    assert fake.models[PIN.ecf_tag] == PIN.digest
    assert models.installed(conn)
    [row] = conn.execute("SELECT data FROM audit WHERE event = 'models.installed'").fetchall()
    assert json.loads(row[0]) == {"tag": PIN.ecf_tag, "digest": PIN.digest}


def test_install_refuses_a_moved_upstream_tag(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    from ecf_server import db  # noqa: PLC0415

    fake = FakeOllama(installed=False, upstream="c" * 64)
    models.start_install(lambda: db.connect(db_path), clock, fake.client, spawn=_inline)
    snap = models.INSTALLS.snapshot()
    assert snap["state"] == "failed" and "no ecf release moves the pin" in snap["error"]
    assert PIN.ecf_tag not in fake.models


def test_one_install_at_a_time(conn: sqlite3.Connection, db_path: Path, clock: FakeClock) -> None:
    from ecf_server import db  # noqa: PLC0415

    models.start_install(lambda: db.connect(db_path), clock, FakeOllama().client,
                         spawn=lambda _work: None)  # fmt: skip
    with pytest.raises(ConflictError):
        models.start_install(lambda: db.connect(db_path), clock, FakeOllama().client,
                             spawn=lambda _work: None)  # fmt: skip


def test_status_ready_and_not(conn: sqlite3.Connection) -> None:
    st = models.status(conn, FakeOllama().client(), **check_kw())
    assert st["ready"] and st["env"] == {"OLLAMA_NUM_PARALLEL": "1", "OLLAMA_NO_CLOUD": "1"}
    st = models.status(conn, FakeOllama(installed=False).client(), **check_kw())
    assert not st["ready"] and st["fault"]["cause"] == "model_missing"
    assert st["fault"]["fix"] == "run `ecf models install`"


def test_the_routes(conn: sqlite3.Connection, db_path: Path, clock: FakeClock) -> None:
    from ecf_server.api import ServiceState, create_app  # noqa: PLC0415

    fake = FakeOllama()
    state = ServiceState(install="t", token="tok", started_at="2026-10-01T12:00:00.000000Z",
                         clock=clock, db_path=db_path, model_client=fake.client,
                         model_check=check_kw())  # fmt: skip

    async def call(method: str, path: str) -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.request(method, path, headers={"Authorization": "Bearer tok"},
                                   json={} if method == "POST" else None)  # fmt: skip

    r = anyio.run(call, "GET", "/v1/models")
    assert r.status_code == 200 and r.json()["ready"] is True


# ---- doctor (pure) ---------------------------------------------------------------------------


def _st(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"pin": {"tag": PIN.tag, "digest": PIN.digest, "ecf_tag": PIN.ecf_tag},
                            "installed_at": "2026-09-30T17:00:00Z", "ready": True,
                            "version": "0.35.0", "listener": ["127.0.0.1:11434"],
                            "env": {"OLLAMA_NUM_PARALLEL": "1",
                                    "OLLAMA_NO_CLOUD": "1"}}  # fmt: skip
    return base | kw


def test_doctor_ok_with_ecfs_settings() -> None:
    [c] = judge_models(_st())
    assert c.level is Level.OK and "listening on 127.0.0.1:11434 only" in c.detail


def test_doctor_reports_settings_that_differ() -> None:
    env = {"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_ORIGINS": "*"}
    details = [c.detail for c in judge_models(_st(env=env))[1:]]
    assert any("OLLAMA_NUM_PARALLEL is unset" in d for d in details)
    assert any("cloud feature is on" in d for d in details)
    assert any("OLLAMA_FLASH_ATTENTION is set" in d for d in details)
    assert any("any web page" in d for d in details)


def test_doctor_fails_on_a_fault_but_only_warns_before_the_first_install() -> None:
    fault = {"cause": "model_missing", "detail": "", "fix": "run `ecf models install`",
             "text": "the local model isn't installed. ...",
             "summary": "the local model isn't installed."}  # fmt: skip
    [c] = judge_models(_st(ready=False, fault=fault, installed_at=None))
    assert c.level is Level.WARN
    [c] = judge_models(_st(ready=False, fault=fault))
    assert c.level is Level.FAIL
    loud = fault | {"cause": "logs_requests"}
    [c] = judge_models(_st(ready=False, fault=loud, installed_at=None))
    assert c.level is Level.FAIL


# ---- daily summary --------------------------------------------------------------------------


def test_the_daily_summary_says_items_wait_for_the_local_model(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    models.check(conn, clock, FakeNotifier(), FakeOllama(installed=False).client(), **check_kw())
    card = daily.card(conn, clock.now(), "2026-10-01")
    assert "items waiting for the local model since" in card.text


def test_install_wide_alerts_stay_listed_after_an_address_is_removed(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    from ecf_server import health  # noqa: PLC0415

    now = to_ts(clock.now())
    with write_tx(conn):  # a removed address made `NULL NOT IN (...)` hide install-wide alerts
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at,"
                     " removed_at) VALUES ('old', 'old@acme.example', 'standard', 'A', ?, ?)",
                     (now, now))  # fmt: skip
    models.check(conn, clock, FakeNotifier(), FakeOllama(installed=False).client(), **check_kw())
    assert [a["kind"] for a in health.open_alerts(conn)] == ["local_model"]
