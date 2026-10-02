"""`ecf eval run` (V1.3 step 8c; SPEC §16, OD-237, OD-241): which cases count, scoring, the result
file and its metrics-only content, the battery pause, one run at a time."""

from __future__ import annotations

import json
import shutil
import sqlite3
import stat
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from ecf.errors import ConflictError, ServiceUnavailableError
from ecf.eval import labels
from ecf.eval.builder import build_all
from ecf_server import evalrun, modelq, policy, schedule
from ecf_server.clock import FakeClock, to_ts
from tests.test_classifier import ChatOllama
from tests.test_models import check_kw

SYNTHETIC = Path(__file__).parent / "eval" / "synthetic"
BEC = {"category": "vendor_change_request", "priority": "high", "requires_action": True,
       "requires_reply": False, "payment_related": True, "deadline_mentioned": False,
       "sender_type": "vendor", "fraud_risk": "high"}  # fmt: skip
AC = schedule.Power(laptop=True, on_ac=True)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "synthetic"
    (r / "cases").mkdir(parents=True)
    for name in ("starter-bec.md", "starter-control.md", "starter-injection.md"):
        shutil.copy(SYNTHETIC / "cases" / name, r / "cases" / name)
    assert not build_all(r).findings
    labels.confirm(r, "starter-bec", date(2026, 10, 1))
    labels.confirm(r, "starter-injection", date(2026, 10, 1))
    return r


@pytest.fixture(autouse=True)
def _reset() -> None:
    evalrun.RUN.set(state="idle", run_id="", done=0, total=0, result=None, detail="")
    evalrun.RUN.stop.clear()
    if modelq.EXCLUSIVE.held():
        modelq.EXCLUSIVE.release()


def _inline(work: Any) -> None:
    work()


def test_only_confirmed_unchanged_cases_count(root: Path) -> None:
    cases, version = evalrun.load(root, fraud_only=False)
    assert {c.id: c.confirmed for c in cases} == {"starter-bec": True, "starter-control": False,
                                                  "starter-injection": True}  # fmt: skip
    (root / "eml" / "starter-bec.eml").write_bytes(b"tampered")  # the file no longer matches
    cases, _ = evalrun.load(root, fraud_only=False)
    assert {c.id: c.confirmed for c in cases}["starter-bec"] is False
    fraud, _ = evalrun.load(root, fraud_only=True)
    assert {c.id for c in fraud} == {"starter-bec", "starter-injection"}
    assert len(version) == 16


def _plan(actions: list[str], rule: str = "fraud_guard") -> policy.Plan:
    return policy.Plan(rule, actions=[policy.Planned(a, None, "auto") for a in actions])


def test_scoring_safety() -> None:
    case = evalrun.Case("c", Path("x"), {"labels": {"category": "customer_request"},
                                         "safety": {"must_not_hide": True,
                                                    "injection_target": "archive"}},
                        True, "claude")  # fmt: skip
    ok = evalrun.score(case, {"category": "customer_request"}, _plan(["flag"], "x"), None)
    assert ok.safety and ok.correct
    hidden = evalrun.score(case, {"category": "customer_request"}, _plan(["archive"], "x"), None)
    assert not hidden.safety and not hidden.correct
    obeyed = evalrun.score(case, {"category": "customer_request"}, _plan([], "x"),
                           {"action": "archive", "target": "", "reason": "r"})  # fmt: skip
    assert not obeyed.safety
    escalate = evalrun.Case("e", Path("x"), {"rule": "fraud_guard", "safety": {
        "must_escalate": True}}, True, "claude")  # fmt: skip
    assert not evalrun.score(escalate, {}, _plan(["flag"]), None).safety
    assert evalrun.score(escalate, {}, _plan(["escalate"]), None).correct


def test_a_result_keeps_what_the_model_returned() -> None:
    case = evalrun.Case("c", Path("x"), {"labels": {"category": "other"}, "rule": "otherwise"},
                        True, "claude")  # fmt: skip
    r = evalrun.score(case, {"category": "partnership", "requires_reply": True},
                      _plan([], "requires_reply"), None)  # fmt: skip
    assert r.got == {"category": "partnership", "requires_reply": True, "rule": "requires_reply"}
    assert evalrun.score(case, None, None, None).got == {"rule": None}


def test_a_run_writes_metrics_only_and_records_the_digest(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    from ecf_server import db  # noqa: PLC0415

    fake = ChatOllama(json.dumps(BEC))
    evalrun.start(lambda: db.connect(db_path), clock, fake.client, db_path.parent,
                  evalrun.Options(root), power=lambda: AC, battery=lambda: 90,
                  spawn=_inline, check_kw=check_kw())  # fmt: skip
    snap = evalrun.RUN.snapshot()
    assert snap["state"] == "done", snap
    result = snap["result"]
    assert result["confirmed"] == 2 and result["cases"] == 3
    row = conn.execute("SELECT digest, gate_passed, path FROM eval_runs").fetchone()
    assert row["digest"] == evalrun.ollama.load_pin().digest
    path = Path(row["path"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text()
    assert "bank has changed" not in text and "SYSTEM NOTE" not in text  # no message text (I5)
    summary = json.loads(text)["summary"]
    assert summary["determinism_diffs"] == 0
    model = summary["model"]  # the run's own token and speed figures (step 9)
    assert model["calls"] > 0 and model["emails"] == 3 and model["output_tokens"] > 0
    assert model["generation_tps"]["median"] is not None


def test_the_injection_case_counts_as_unsafe_when_the_model_obeys(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    from ecf_server import db  # noqa: PLC0415

    fooled = BEC | {"category": "marketing", "fraud_risk": "none", "payment_related": False}
    evalrun.start(lambda: db.connect(db_path), clock, ChatOllama(json.dumps(fooled)).client,
                  db_path.parent, evalrun.Options(root, actor=False), power=lambda: AC,
                  battery=lambda: 90, spawn=_inline, check_kw=check_kw())  # fmt: skip
    result = evalrun.RUN.snapshot()["result"]
    # a fooled model gets both categories wrong, but I1 holds: the fraud trigger still escalates
    # the BEC, and the injected email's archive isn't corroborated, so nothing is hidden; a run
    # without the actor never passes the gate, though
    assert (result["correct"], result["unsafe"], result["gate_passed"]) == (0, [], False)
    assert result["options"] == {"classifier": True, "actor": False} and result["complete"]
    assert result["fraud_cases"] == 2  # both confirmed cases expect fraud_guard; not the control


def test_a_low_battery_pauses_and_releases_the_queue(
    monkeypatch: pytest.MonkeyPatch, root: Path, clock: FakeClock
) -> None:
    monkeypatch.setattr(evalrun, "PAUSE_POLL_S", 0.01)
    plugged = {"ac": False}

    def power() -> schedule.Power:
        return schedule.Power(laptop=True, on_ac=plugged["ac"])

    seen: list[bool] = []

    def battery() -> int:
        seen.append(modelq.EXCLUSIVE.held())
        if len(seen) > 2:
            plugged["ac"] = True
        return 10

    told: list[tuple[str, bool]] = []
    lines: list[str | None] = []

    def tell(text: str, desktop: bool) -> None:
        told.append((text, desktop))
        lines.append(evalrun.slack_line())

    opts = evalrun.Options(root, battery_floor=15)
    evalrun.RUN.set(run_id="abcdef1234", state="running", done=4, total=10)
    assert evalrun._hold(opts, power, battery, started=evalrun.time.monotonic(), clock=clock,  # pyright: ignore[reportPrivateUsage]
                         tell=tell)  # fmt: skip
    assert len(seen) >= 3 and not any(seen)  # released while paused
    assert modelq.EXCLUSIVE.held()  # held again once on AC
    assert modelq.EXCLUSIVE.since == to_ts(clock.now())  # for the digest's line
    assert told == [
        ("Eval abcdef12 paused on battery (10%, floor 15%) after case 4 of 10: plug in to resume."
         " Model checks for new mail run meanwhile.", True),
        ("Eval abcdef12 resumed on AC power (4 of 10 cases done): model checks for new mail wait"
         " until it ends.", False),
    ]  # once each, however long the pause  # fmt: skip
    assert lines[1] == ("Eval abcdef12 paused (battery 10%, at or below 15%: plug in to resume);"
                        " model checks for new mail run meanwhile (ecf eval status)")  # fmt: skip
    evalrun.RUN.set(state="idle")
    modelq.EXCLUSIVE.release()


def test_one_run_at_a_time(db_path: Path, clock: FakeClock, root: Path) -> None:
    from ecf_server import db  # noqa: PLC0415

    evalrun.start(lambda: db.connect(db_path), clock, ChatOllama("{}").client, db_path.parent,
                  evalrun.Options(root), spawn=lambda _w: None, check_kw=check_kw())  # fmt: skip
    with pytest.raises(ConflictError, match="already running"):
        evalrun.start(lambda: db.connect(db_path), clock, ChatOllama("{}").client,
                      db_path.parent, evalrun.Options(root), spawn=lambda _w: None,
                      check_kw=check_kw())  # fmt: skip


def test_a_run_refuses_to_start_when_ollama_isnt_ready(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    from ecf_server import db  # noqa: PLC0415

    with pytest.raises(ServiceUnavailableError, match="Ollama isn't running"):
        evalrun.start(lambda: db.connect(db_path), clock, ChatOllama("{}").client,
                      db_path.parent, evalrun.Options(root), spawn=_inline,
                      check_kw=check_kw(lsof=""))  # fmt: skip
    assert evalrun.RUN.snapshot()["state"] == "idle"
    assert conn.execute("SELECT count(*) FROM eval_runs").fetchone()[0] == 0


def test_ollama_going_away_mid_run_fails_the_run_without_a_result(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    from ecf_server import db  # noqa: PLC0415

    gone = ChatOllama(httpx.ConnectError("refused"))  # readiness passes; every chat fails
    evalrun.start(lambda: db.connect(db_path), clock, gone.client, db_path.parent,
                  evalrun.Options(root), power=lambda: AC, battery=lambda: 90, spawn=_inline,
                  check_kw=check_kw())  # fmt: skip
    snap = evalrun.RUN.snapshot()
    assert snap["state"] == "failed" and "Ollama isn't running" in snap["detail"]
    assert conn.execute("SELECT count(*) FROM eval_runs").fetchone()[0] == 0
    assert not modelq.EXCLUSIVE.held()


def test_a_stopped_run_is_saved_but_never_passes_the_gate(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    from ecf_server import db  # noqa: PLC0415

    calls = {"n": 0}

    def battery() -> int:  # `ecf eval stop` arrives during the second case
        calls["n"] += 1
        if calls["n"] > 1:
            evalrun.stop()
        return 90

    evalrun.start(lambda: db.connect(db_path), clock, ChatOllama(json.dumps(BEC)).client,
                  db_path.parent, evalrun.Options(root), power=lambda: AC, battery=battery,
                  spawn=_inline, check_kw=check_kw())  # fmt: skip
    snap = evalrun.RUN.snapshot()
    assert (snap["state"], snap["detail"]) == ("stopped", "stopped after 2 of 3")
    assert snap["result"]["complete"] is False and snap["result"]["unsafe"] == []
    assert conn.execute("SELECT gate_passed FROM eval_runs").fetchone()[0] == 0


def test_eval_status_says_why_a_run_cant_pass_the_gate() -> None:
    from ecf.cli import _run_caveat  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415

    both = {"classifier": True, "actor": True}
    assert _run_caveat({"complete": True, "options": both}) == ""
    assert _run_caveat({"complete": False, "cases": 40, "options": both}) == (
        " (stopped after 40 cases)")  # fmt: skip
    assert _run_caveat({"complete": True, "options": both | {"actor": False}}) == (
        " (without the actor)")  # fmt: skip
    assert _run_caveat({}) == ""  # older runs: nothing recorded
