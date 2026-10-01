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

import pytest

from ecf.errors import ConflictError
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
    assert json.loads(text)["summary"]["determinism_diffs"] == 0


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
    # the BEC, and the injected email's archive isn't corroborated, so nothing is hidden
    assert (result["correct"], result["unsafe"], result["gate_passed"]) == (0, [], True)


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
                  evalrun.Options(root), spawn=lambda _w: None)  # fmt: skip
    with pytest.raises(ConflictError, match="already running"):
        evalrun.start(lambda: db.connect(db_path), clock, ChatOllama("{}").client,
                      db_path.parent, evalrun.Options(root), spawn=lambda _w: None)  # fmt: skip
