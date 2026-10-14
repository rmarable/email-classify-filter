"""The local model in the service (V1.3 step 1b): `ecf models install` and status, the routes and
doctor's judgement (SPEC §4.3, §7.5, §13.2). Its check, alerts and daily-summary line went with
the model queue in v3.0.0 (ADR 0031)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from ecf.doctor import Level, judge_models
from ecf.errors import ConflictError
from ecf_server import models
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from tests.helpers import (  # pyright: ignore[reportPrivateUsage]
    PIN,
    FakeOllama,
    check_kw,
)

ME = "U0ME1"


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


def test_install_keeps_ecfs_copies_for_other_releases(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    """Another install, or a rollback (§11.10), may still run an earlier release's copy."""
    from ecf_server import db  # noqa: PLC0415

    fake = FakeOllama(installed=False)
    old = f"{PIN.ecf_name}:1.0.0"
    fake.models[old] = PIN.digest  # an earlier release's copy
    fake.models["other/model:1"] = "d" * 64  # not ecf's
    models.start_install(lambda: db.connect(db_path), clock, fake.client, spawn=_inline)
    assert set(fake.models) == {PIN.tag, PIN.ecf_tag, old, "other/model:1"}
    assert models.stale_tags(fake.client()) == [old]


def test_prune_removes_only_ecfs_copies_for_other_releases(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    fake = FakeOllama()
    # an earlier release's copy, and every copy of the retired decision model (OD-494)
    this_release = PIN.ecf_tag.split(":")[1]
    stale = [f"{PIN.ecf_name}:1.0.0", "ecf/tev1-4b:2.0.0", f"ecf/tev1-4b:{this_release}"]
    for name in stale:
        fake.models[name] = PIN.digest
    fake.models["other/model:1"] = "d" * 64
    fake.models["ecf/someone-elses:1.0.0"] = "e" * 64  # an ecf/ name no pin owns
    fake.models["tev1:4b"] = "f" * 64  # the upstream tag: not ecf's, left alone
    keep = {PIN.tag, PIN.ecf_tag, "other/model:1", "ecf/someone-elses:1.0.0", "tev1:4b"}
    assert models.stale_tags(fake.client()) == [f"{PIN.ecf_name}:1.0.0"]
    assert models.retired_tags(fake.client()) == sorted(stale[1:])
    assert sorted(models.prune(conn, clock, fake.client())) == sorted(stale)
    assert set(fake.models) == keep
    [row] = conn.execute("SELECT data FROM audit WHERE event = 'models.pruned'").fetchall()
    assert sorted(json.loads(row[0])["tags"]) == sorted(stale)
    assert models.prune(conn, clock, fake.client()) == []  # nothing left: no audit row
    n = conn.execute("SELECT count(*) FROM audit WHERE event = 'models.pruned'").fetchone()[0]
    assert n == 1


def test_status_lists_the_kept_copies(conn: sqlite3.Connection) -> None:
    fake = FakeOllama()
    assert models.status(conn, fake.client(), **check_kw())["stale_tags"] == []
    fake.models[f"{PIN.ecf_name}:1.0.0"] = PIN.digest
    st = models.status(conn, fake.client(), **check_kw())
    assert st["ready"] and st["stale_tags"] == [f"{PIN.ecf_name}:1.0.0"]
    assert st["retired_tags"] == []
    fake.models["ecf/tev1-4b:2.2.3"] = "c" * 64  # the retired decision model (OD-494)
    st = models.status(conn, fake.client(), **check_kw())
    assert st["stale_tags"] == [f"{PIN.ecf_name}:1.0.0"]
    assert st["retired_tags"] == ["ecf/tev1-4b:2.2.3"]


def test_install_copies_a_local_copy_of_the_pinned_model_without_pulling(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    """v2.2.3: Ollama 0.40 on Apple silicon pulled an MLX build over the bare `gemma4:12b`, so the
    pinned model may survive only under an earlier release's ecf copy; a pull could replace it."""
    from ecf_server import db  # noqa: PLC0415

    fake = FakeOllama(installed=False, upstream="c" * 64)
    fake.models[PIN.tag] = "c" * 64  # the pinned tag now names another build
    fake.models[f"{PIN.ecf_name}:2.2.1"] = PIN.digest
    got = models.start_install(lambda: db.connect(db_path), clock, fake.client, spawn=_inline)
    assert got["state"] == "done", got
    assert fake.models[PIN.ecf_tag] == PIN.digest and fake.pulls == 0
    assert fake.models[PIN.tag] == "c" * 64  # the user's tag is left alone


def test_local_source_prefers_the_pinned_tag_then_ecf_copies() -> None:
    d = PIN.digest
    assert models.local_source({PIN.tag: d, f"{PIN.ecf_name}:1": d}, PIN) == PIN.tag
    assert models.local_source({PIN.tag: "c" * 64, "x/y:1": d, f"{PIN.ecf_name}:1": d},
                               PIN) == f"{PIN.ecf_name}:1"  # fmt: skip
    assert models.local_source({"x/y:1": d}, PIN) == "x/y:1"
    assert models.local_source({PIN.tag: "c" * 64}, PIN) is None


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

    async def call(method: str, path: str, body: dict[str, Any] | None = None) -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.request(method, path, headers={"Authorization": "Bearer tok"},
                                   json=(body or {}) if method == "POST" else None)  # fmt: skip

    r = anyio.run(call, "GET", "/v1/models")
    assert r.status_code == 200 and r.json()["ready"] is True
    fake.models[f"{PIN.ecf_name}:1.0.0"] = PIN.digest
    r = anyio.run(call, "POST", "/v1/models/prune")
    assert r.status_code == 200 and r.json() == {"removed": [f"{PIN.ecf_name}:1.0.0"]}
    assert PIN.ecf_tag in fake.models
    # an older client's decision-model install is refused, never run as the local model's (OD-494)
    r = anyio.run(call, "POST", "/v1/models/install", {"decision": "tev1-4b"})
    assert r.status_code == 400 and "OD-494" in r.text
    assert models.INSTALLS.snapshot()["state"] == "idle"


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


def test_doctor_lists_kept_copies_without_failing() -> None:
    old = f"{PIN.ecf_name}:1.0.0"
    [_, c] = judge_models(_st(stale_tags=[old]))
    assert c.level is Level.OK and old in c.detail and "ecf models prune" in c.detail
    fault = {"cause": "not_running", "detail": "", "fix": "x", "text": "t", "summary": "s."}
    rows = judge_models(_st(ready=False, fault=fault, stale_tags=[old]))
    assert [r.name for r in rows] == ["local model", "model copies"]


def test_doctor_lists_retired_copies_without_a_keep_note() -> None:
    [_, c] = judge_models(_st(retired_tags=["ecf/tev1-4b:2.2.3"]))
    assert c.level is Level.OK and "retired, no release runs them" in c.detail
    assert "rollback" not in c.detail and "ecf models prune" in c.detail


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


# ---- alerts and need --------------------------------------------------------------------------


def test_install_wide_alerts_stay_listed_after_an_address_is_removed(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    from ecf_server import health  # noqa: PLC0415

    now = to_ts(clock.now())
    with write_tx(conn):  # a removed address made `NULL NOT IN (...)` hide install-wide alerts
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at,"
                     " removed_at) VALUES ('old', 'old@acme.example', 'standard', 'C', ?, ?)",
                     (now, now))  # fmt: skip
    health.open_alert(conn, clock, FakeNotifier(), "models_api", None, "the key was refused")
    assert [a["kind"] for a in health.open_alerts(conn)] == ["models_api"]
