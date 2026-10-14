"""`ecf eval run --classifier-backend jev` and `/ecf-eval --preset D` (v3.0.0 step 2d;
planning-docs/JEV-STEP2.md §6, §8 "Eval (2d)"; OD-503, OD-504): a run on the fake transport,
per-case figures, signals, the refusals, faulted cases scored as failures, incomplete runs, the
stops that fail a run, the inputs `compare` warns on, rescore preservation, result format 2,
preset D's source query and the report lines. Every Jev call goes to an `httpx.MockTransport`;
`tests/conftest.py` makes the real transport raise."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import time
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from ecf.client import LocalClient
from ecf.errors import InvalidInputError
from ecf.eval import labels
from ecf.eval.builder import build_all
from ecf.eval.results import (
    RESULT_FORMAT,
    input_warnings,
    load_result,
)
from ecf.paths import Paths
from ecf_server import claude_eval, claude_pins, db, evaljev, evalrun, jev, schedule, signals
from ecf_server.clock import FakeClock
from ecf_server.secretstore.memory import MemorySecretStore
from tests.test_addresses import call, make_state
from tests.test_claude_review import extend
from tests.test_jev import KEY, PIN, body

SYNTHETIC = Path(__file__).parent / "eval" / "synthetic"
AC = schedule.Power(laptop=True, on_ac=True)
# the five cards (labels.jsonl order) and their subjects, which the fake reads from the state
SUBJECTS = {
    "bec-ceo-urgent-wire": "Urgent and confidential - wire needed today",
    "billing-plan-question": "Question about the new pricing",
    "starter-bec": "Updated remittance details for October",
    "starter-control": "October deals",
    "starter-injection": "Question about my order",
}
CASE_OF = {v: k for k, v in SUBJECTS.items()}
TOKENS = {case: 1000 + 100 * i for i, case in enumerate(SUBJECTS)}  # a distinct count each


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "synthetic"
    (r / "cases").mkdir(parents=True)
    for name in SUBJECTS:
        shutil.copy(SYNTHETIC / "cases" / f"{name}.md", r / "cases" / f"{name}.md")
    assert not build_all(r).findings
    for name in SUBJECTS:
        if name != "starter-control":
            labels.confirm(r, name, date(2026, 10, 1))
    return r


def _clear() -> None:
    evalrun.RUN.set(state="idle", run_id="", done=0, total=0, result=None, detail="")
    evalrun.RUN.stop.clear()


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    _clear()
    yield
    _clear()


class Fake:
    """TypeSafe on a MockTransport: answers each case from its subject (`fail`: a status for
    that case, every try), counts the requests, keeps their states."""

    def __init__(self, fail: dict[str, int] | None = None, **over: Any) -> None:
        self.fail = fail or {}
        self.over = over
        self.states: list[Any] = []
        self.cases: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        sent = json.loads(req.content)
        state = sent["state"]
        self.states.append(state)
        m = re.search(r"^Subject: (.*)$", state, re.MULTILINE) if isinstance(state, str) else None
        if m is None:  # the probe's minimal POST
            return httpx.Response(200, json=body())
        case = CASE_OF[m.group(1)]
        self.cases.append(case)
        if case in self.fail:
            return httpx.Response(self.fail[case], json={})
        usage = {"input_tokens": TOKENS[case], "output_tokens": 13}
        return httpx.Response(200, json=body(usage=usage) | self.over)


def _inline(work: Any) -> None:
    work()


def run_jev(db_path: Path, clock: FakeClock, root: Path, fake: Callable[..., httpx.Response], *,
            key: Callable[[], str | None] = lambda: KEY, budget_s: float = 1e6,
            **opts: Any) -> dict[str, Any]:  # fmt: skip
    """A Jev run inline on the fake; waits advance the fake clock."""

    def wait(s: float) -> bool:
        clock.advance(s)
        return evalrun.RUN.stop.is_set()

    setup = evaljev.Setup(key, httpx.MockTransport(fake), wait=wait, budget_s=budget_s)
    evalrun.start(lambda: db.connect(db_path), clock, db_path.parent,
                  evalrun.Options(root, backend=evalrun.JEV, **opts), power=lambda: AC,
                  battery=lambda: 90, spawn=_inline, thermal=lambda: {"CPU_Speed_Limit": 100},
                  asker=evaljev.factory(setup))  # fmt: skip
    return evalrun.RUN.snapshot()


def _row(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM eval_runs ORDER BY rowid DESC").fetchone()  # the latest


# ---- a run ---------------------------------------------------------------------------------------


def test_a_jev_run_end_to_end_on_the_fake(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    """Five cards classified by Jev, scored by the rules and policy; no actor, no model server;
    the result records what Jev was asked with (P13) and its figures (§5.5), never the key or a
    request ID (D7); it can't pass the gate in step 2."""
    fake = Fake()
    snap = run_jev(db_path, clock, root, fake)
    assert snap["state"] == "done", snap
    s = snap["result"]
    assert s["cases"] == 5 and s["confirmed"] == 4 and s["complete"] is True
    assert s["options"] == {"classifier": True, "actor": False} and s["gate_passed"] is False
    assert s["faulted"] == [] and "incomplete_reason" not in s
    assert s["backend"] == "jev" and s["classifier_digest"] == PIN and s["actor_digest"] is None
    schema = evalrun.load_schema()
    assert s["schema_digest"] == schema.digest == s["jev"]["schema_digest"]  # v3 (P13)
    assert s["prompt_digest"] == jev.prompt_digest(schema, pin=PIN)
    assert s["input_format"] == jev.state_format_id() and s["excerpt_cap"] == jev.excerpt_cap()
    assert s["gate_key"].startswith(f"jev:{PIN}+")
    row = _row(conn)
    assert (row["pair"], row["digest"], row["gate_passed"]) == ("jev/hosted", s["gate_key"], 0)
    j = s["jev"]
    assert j["pin"] == PIN and j["options"] == {"state": "s1", "preamble": "state",
                                                "concurrency": 1}  # fmt: skip
    assert j["latency_ms_by_concurrency"]["1"]["n"] == 10  # 5 cases + 5 determinism repeats
    # every 200's usage, repeats included (P21), and its cost line
    assert j["input_tokens_billed"] == 2 * sum(TOKENS.values())
    assert j["cost"]["usd"] == round(j["input_tokens_billed"] * 0.042 / 1e6, 6)
    assert "per million input tokens" in j["cost"]["basis"]
    assert s["determinism"]["cases"] == sorted(SUBJECTS) and s["determinism_diffs"] == 0
    assert all(v == [1, 1] for v in s["determinism"]["concurrency"].values())  # P23
    assert "calibration" in s  # Jev's probabilities
    text = Path(row["path"]).read_text()
    assert KEY not in text and "req_" not in text.lower() and "request_id" not in text
    assert "Updated remittance" not in text  # no message text (I5)
    result = load_result(Path(row["path"]))
    assert result.format == RESULT_FORMAT == 2
    assert all(c.tries == 1 and c.fault is None and c.confidence for c in result.cases)
    assert len(fake.cases) == 10 and evalrun.RUN.snapshot()["state"] == "done"


def test_per_case_figures_belong_to_their_case(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    del conn
    run_jev(db_path, clock, root, Fake())
    result = load_result(Path(_path(db_path)))
    for c in result.cases:
        assert c.input_tokens == TOKENS[c.id] and c.output_tokens == 13, c.id
        assert c.classifier_ms is not None and c.wait_ms == 0 and c.queue_ms == 0
        assert c.concurrency == 1 and c.probabilities


def _path(db_path: Path) -> str:
    c = db.connect(db_path)
    try:
        return str(_row(c)["path"])
    finally:
        c.close()


def test_signals_are_sent_and_no_signals_leaves_them_out(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    """OD-486: the same signals block the classifiers get; `--no-signals` is allowed for Jev
    (R3) and makes the run not for the gate."""
    del conn
    wording = tuple(signals.WORDING.values())
    on = Fake()
    run_jev(db_path, clock, root, on)
    assert any(w in st for st in on.states if isinstance(st, str) for w in wording)
    _clear()
    off = Fake()
    snap = run_jev(db_path, clock, root, off, signals=False)
    assert not any(w in st for st in off.states if isinstance(st, str) for w in wording)
    assert snap["result"]["options"]["switches_off"] == ["signals"]
    assert snap["result"]["options"]["for_gate"] is False


def test_measurement_options_make_the_run_not_for_the_gate(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    del conn
    fake = Fake()
    snap = run_jev(db_path, clock, root, fake, jev_state="s2", jev_preamble="instructions")
    s = snap["result"]
    assert s["options"]["for_gate"] is False
    assert s["options"]["measuring"] == ["jev_state s2", "jev_preamble instructions"]
    assert s["input_format"] == jev.state_format_id("s2", "instructions")
    assert all(isinstance(st, dict) for st in fake.states if st != jev.KEY_CHECK_STATE)
    _clear()
    with pytest.raises(InvalidInputError, match="not in step 2"):
        run_jev(db_path, clock, root, Fake(), jev_concurrency=2)
    with pytest.raises(InvalidInputError, match="for the jev backend"):
        evalrun.start(lambda: db.connect(db_path), clock, db_path.parent,
                      evalrun.Options(root, jev_state="s2"), spawn=_inline)  # fmt: skip


def test_no_redact_only_with_fraud_only(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    """P2: Jev may run unredacted on the synthetic `--fraud-only` set only."""
    del conn
    with pytest.raises(InvalidInputError, match="--fraud-only"):
        run_jev(db_path, clock, root, Fake(), redact=False)
    snap = run_jev(db_path, clock, root, Fake(), redact=False, fraud_only=True)
    assert snap["state"] == "done" and snap["result"]["redact"] is False
    assert snap["result"]["gate_passed"] is False


def test_a_non_default_schema_is_refused(
    extensions_on: None, conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    """D9: Jev evals use the default schema until step 4 decides user fields on D."""
    del extensions_on
    extend(conn, clock)
    fake = Fake()
    with pytest.raises(InvalidInputError, match="default schema"):
        run_jev(db_path, clock, root, fake)
    assert not fake.states and evalrun.RUN.snapshot()["state"] == "idle"


def test_a_missing_key_is_refused_before_the_run(
    conn: sqlite3.Connection, db_path: Path, root: Path
) -> None:
    """P12: the route reads the key; without one nothing starts."""
    del conn
    r = call(make_state(db_path, MemorySecretStore()), "POST", "/v1/eval/runs",
             {"root": str(root), "backend": "jev"})  # fmt: skip
    assert r.status_code == 400 and "no TypeSafe key" in r.text
    assert evalrun.RUN.snapshot()["state"] == "idle"
    with pytest.raises(InvalidInputError, match="no TypeSafe key"):
        evalrun.start(lambda: db.connect(db_path), FakeClock(), db_path.parent,
                      evalrun.Options(root, backend="jev"), spawn=_inline)  # fmt: skip


def test_the_route_passes_the_key_and_transport_to_the_run(
    conn: sqlite3.Connection, db_path: Path, root: Path
) -> None:
    del conn
    store = MemorySecretStore()
    store.set(jev.KEY_SECRET, KEY)
    state = make_state(db_path, store)
    fake = Fake()
    state.jev_transport = httpx.MockTransport(fake)
    r = call(state, "POST", "/v1/eval/runs", {"root": str(root), "backend": "jev"})
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 30
    while evalrun.RUN.snapshot()["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert evalrun.RUN.snapshot()["state"] == "done" and len(fake.cases) == 10


def test_a_corpus_session_is_refused_through_the_socket(running: Paths, root: Path) -> None:
    """P5: corpus runs are gone (R-D5), and the service refuses a request naming a corpus
    session, Jev's included: real mail reaches Jev only through a preset D address (step 4)."""
    with LocalClient(running) as c:
        with pytest.raises(InvalidInputError, match="preset D address"):
            c.request("POST", "/v1/eval/runs", {"root": str(root), "backend": "jev",
                                                "corpus_session": "s" * 32})  # fmt: skip
        assert c.get("/v1/eval/runs")["current"]["state"] == "idle"


# ---- faults, stops and incomplete runs -----------------------------------------------------------


def test_faulted_cases_are_scored_as_failures_and_listed(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, db_path: Path, clock: FakeClock,
    root: Path,
) -> None:  # fmt: skip
    """P1: a case with no answer after its tries is scored, never skipped: unsafe, listed in
    `faulted`; a request error likewise, with its status. Neither is a safety card here, so the
    run is complete."""
    del conn
    monkeypatch.setattr(jev, "FAULT_SHARE", 0.4)  # floor(0.4 * 5) = 2 may fault
    snap = run_jev(db_path, clock, root, Fake({"billing-plan-question": 500,
                                               "starter-control": 400}))  # fmt: skip
    s = snap["result"]
    assert snap["state"] == "done" and s["complete"] is True
    assert s["faulted"] == ["billing-plan-question", "starter-control"]
    assert "billing-plan-question" in s["unsafe"]  # confirmed: counted as a failure
    assert s["jev"]["faulted_cases"] == {"provider": 1, "request_error": 1}
    cases = {c.id: c for c in load_result(Path(_path(db_path))).cases}
    p, r = cases["billing-plan-question"], cases["starter-control"]
    assert (p.fault, p.tries, p.got.get("rule"), p.correct) == ("provider", jev.TRIES, None, False)
    assert (r.fault, r.status, r.tries) == ("request_error", 400, 1)
    assert p.wait_ms is not None and p.wait_ms > 0 and p.classifier_ms is None


def test_a_faulted_safety_card_marks_the_run_incomplete_and_it_carries_on(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, db_path: Path, clock: FakeClock,
    root: Path,
) -> None:  # fmt: skip
    del conn
    monkeypatch.setattr(jev, "FAULT_SHARE", 0.4)
    snap = run_jev(db_path, clock, root, Fake({"starter-bec": 503}))
    s = snap["result"]
    assert snap["state"] == "done" and s["cases"] == 5
    assert s["complete"] is False and s["incomplete_reason"] == "faulted_safety_card"
    assert s["gate_passed"] is False and "determinism" in s  # the repeats still ran (choice 6)


def test_more_than_the_fault_limit_stops_the_run_and_up_to_it_doesnt(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, db_path: Path, clock: FakeClock,
    root: Path,
) -> None:  # fmt: skip
    """P22: the run stops at the first faulted case past floor(0.02 n) (the 7th of 304): here
    floor(0.4 * 5) = 2, so the 2nd faulted case doesn't stop it and the 3rd does."""
    del conn
    monkeypatch.setattr(jev, "FAULT_SHARE", 0.4)
    assert jev.fault_limit(5) == 2
    two = {"bec-ceo-urgent-wire": 500, "billing-plan-question": 500}
    assert run_jev(db_path, clock, root, Fake(two))["state"] == "done"
    _clear()
    snap = run_jev(db_path, clock, root, Fake(two | {"starter-bec": 500}))
    s = snap["result"]
    assert snap["state"] == "stopped" and "3 cases faulted" in snap["detail"]
    assert s["cases"] == 3 and s["incomplete_reason"] == "faulted" and s["complete"] is False
    assert "determinism" not in s and s["gate_passed"] is False


def test_a_spent_waiting_budget_keeps_a_partial_result(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    """§5.4 (draft 4): like `ecf eval stop`: the cases scored so far, marked `budget`."""
    snap = run_jev(db_path, clock, root, Fake({"starter-control": 500}), budget_s=0.0)
    s = snap["result"]
    assert snap["state"] == "stopped" and snap["detail"].startswith("budget after 3 of 5")
    assert s["cases"] == 3 and s["incomplete_reason"] == "budget" and s["complete"] is False
    assert "determinism" not in s
    assert _row(conn)["gate_passed"] == 0


@pytest.mark.parametrize(("fake", "why"), [
    (Fake({c: 401 for c in SUBJECTS}), "TypeSafe refused the key (HTTP 401)"),
    (Fake(model="jev-9.9.9"), "different Jev version than the pin"),
    (Fake({c: 404 for c in SUBJECTS}), "TypeSafe refused the first request (HTTP 404)"),
])  # fmt: skip
def test_auth_and_pin_stops_fail_the_run_with_no_result(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path, fake: Fake, why: str
) -> None:
    snap = run_jev(db_path, clock, root, fake)
    assert snap["state"] == "failed" and why in snap["detail"] and KEY not in snap["detail"]
    assert conn.execute("SELECT count(*) FROM eval_runs").fetchone()[0] == 0


def test_a_key_removed_mid_run_stops_it(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    """P12: the key is re-read before each case."""
    keys = iter([KEY, KEY])  # cases 1 and 2, then gone
    snap = run_jev(db_path, clock, root, Fake(), key=lambda: next(keys, None))
    assert snap["state"] == "failed" and snap["detail"] == jev.KEY_GONE
    assert conn.execute("SELECT count(*) FROM eval_runs").fetchone()[0] == 0


def test_the_determinism_check_compares_probabilities_too() -> None:
    a = evalrun.Asked({"x": "a"}, {"x": {"a": 0.5, "b": 0.5}})
    assert evalrun.same_answer(a, evalrun.Asked({"x": "a"}, {"x": {"a": 0.5 + 5e-7,
                                                                    "b": 0.5}}))  # fmt: skip
    assert not evalrun.same_answer(a, evalrun.Asked({"x": "a"}, {"x": {"a": 0.51, "b": 0.49}}))
    assert not evalrun.same_answer(a, evalrun.Asked({"x": "b"}, {"x": {"a": 0.5, "b": 0.5}}))
    assert evalrun.same_answer(evalrun.Asked(None), evalrun.Asked(None))


# ---- inputs, rescore, format ---------------------------------------------------------------------


def test_jev_vs_c_prints_different_inputs(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    """P13: top-level `prompt_digest`, `input_format` and `excerpt_cap`; a C run writes
    `input_format: claude-v1`, so the comparison says the inputs differ."""
    del conn
    run_jev(db_path, clock, root, Fake())
    a = load_result(Path(_path(db_path)))
    c_run = a.model_copy(update={"summary": dict(a.summary or {}) | {
        "prompt_digest": "c" * 64, "input_format": claude_eval.CLAUDE_INPUT_FORMAT}})  # fmt: skip
    warnings = input_warnings(a, c_run)
    assert any("different prompt" in w for w in warnings)
    assert any("different inputs (input_format" in w for w in warnings)


def _jev_result(monkeypatch: pytest.MonkeyPatch, db_path: Path, clock: FakeClock, root: Path,
                *, incomplete: bool) -> Path:  # fmt: skip
    """A Jev result from a run on the fake, with a faulted case: a safety card (the run
    incomplete) or not."""
    monkeypatch.setattr(jev, "FAULT_SHARE", 0.4)
    card = "starter-bec" if incomplete else "billing-plan-question"
    snap = run_jev(db_path, clock, root, Fake({card: 500}))
    assert snap["result"]["complete"] is not incomplete
    _clear()
    return Path(_path(db_path))


@pytest.mark.parametrize("incomplete", [False, True])
@pytest.mark.parametrize("how", ["synthetic", "policy"])
def test_a_rescore_keeps_every_jev_field_and_scores_the_same(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, db_path: Path, clock: FakeClock,
    root: Path, incomplete: bool, how: str,
) -> None:  # fmt: skip
    """P3 (draft 4, C): both rescore paths keep the per-case Jev figures and the summary keys
    they don't recompute, `incomplete_reason` and `format` included."""
    del conn
    path = _jev_result(monkeypatch, db_path, clock, root, incomplete=incomplete)
    old = load_result(path)
    rescore = evalrun.rescore_synthetic if how == "synthetic" else evalrun.rescore_through_policy
    new = load_result(Path(rescore(root, path, clock)["path"]))
    assert new.format == 2
    for a, b in zip(old.cases, new.cases, strict=True):
        for k in (*evalrun.KEPT_CASE_FIELDS, "probabilities", "classifier_ms"):
            assert getattr(a, k) == getattr(b, k), (a.id, k)
        assert (a.correct, a.safety, a.fields) == (b.correct, b.safety, b.fields), a.id
    s0, s1 = cast(dict[str, Any], old.summary), cast(dict[str, Any], new.summary)
    for k in ("jev", "gate_key", "prompt_digest", "input_format", "excerpt_cap", "faulted",
              "determinism", "complete", "unsafe", "correct"):  # fmt: skip
        assert s1[k] == s0[k], k
    assert s1.get("incomplete_reason") == s0.get("incomplete_reason")
    assert s1.get("incomplete_reason") == ("faulted_safety_card" if incomplete else None)


def test_format_2_and_the_old_client_message(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, db_path: Path, clock: FakeClock,
    root: Path, tmp_path: Path,
) -> None:  # fmt: skip
    """P16: files without `format` read as 1; this ecf writes 2; a client that knows only 1
    refuses a format-2 file with a reason, not a validation dump."""
    del conn
    path = _jev_result(monkeypatch, db_path, clock, root, incomplete=False)
    assert json.loads(path.read_text())["format"] == 2
    raw = json.loads(path.read_text())
    del raw["format"]
    for c in raw["cases"]:
        for k in evalrun.KEPT_CASE_FIELDS:
            c.pop(k)
    older = tmp_path / "older.json"
    older.write_text(json.dumps(raw))
    assert load_result(older).format == 1
    from ecf.eval import results  # noqa: PLC0415

    monkeypatch.setattr(results, "RESULT_FORMAT", 1)  # an older client
    with pytest.raises(InvalidInputError, match=r"written by a newer ecf \(format 2\); run `uv"):
        load_result(path)


# ---- /ecf-eval --preset D ------------------------------------------------------------------------


def _start_d(db_path: Path, clock: FakeClock, root: Path, **kw: Any) -> dict[str, Any]:
    opts = claude_eval.Options(root, preset="D", **kw)
    return claude_eval.start(lambda: db.connect(db_path), clock, db_path.parent, opts,
                             spawn=_inline)  # fmt: skip


def test_preset_d_takes_jevs_classifications_from_its_source_run(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, db_path: Path, clock: FakeClock,
    root: Path,
) -> None:  # fmt: skip
    """D5, P15: the latest complete Jev run on the pin, its prompt digest, the default schema and
    this set; a faulted source case keeps its failure (P1); comparison only (D isn't in
    `claude_pins.PAIR` until step 4); its inputs are the source run's."""
    monkeypatch.setattr(jev, "FAULT_SHARE", 0.4)
    run_jev(db_path, clock, root, Fake({"billing-plan-question": 500}))
    source = _row(conn)
    _clear()
    r = _start_d(db_path, clock, root)
    assert r["preset"] == "D" and r["pinned"] is False
    assert set(r["models"]) == {"actor", "actor_high"}
    run = claude_eval.current()
    assert run is not None and run.source_run == source["run_id"]
    assert run.key.startswith("eval-pins-")
    works = {w.case.id: w for w in run.works}
    failed = works["billing-plan-question"]
    assert failed.classification is None and failed.result is not None
    assert failed.result.correct is False and failed.result.safety is False
    assert all(w.stage in ("act", "done") for w in run.works)  # nothing to classify
    assert "D" not in claude_pins.PAIR
    # no case went to the actor on these answers: the run ended as soon as it was prepared
    assert run.state == "done"
    saved = _row(conn)
    assert saved["pair"] == claude_eval.D_PAIR and saved["digest"] == run.key
    m = json.loads(saved["metrics"])
    src = json.loads(source["metrics"])
    for k in claude_eval.SOURCE_KEYS:
        assert m[k] == src[k], k
    assert m["claude"]["source_run"] == source["run_id"] and m["claude"]["preset"] == "D"


def test_preset_d_needs_a_complete_source_on_the_pin(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, db_path: Path, clock: FakeClock,
    root: Path,
) -> None:  # fmt: skip
    with pytest.raises(InvalidInputError, match="no classifier model"):
        _start_d(db_path, clock, root, classifier_model="claude-opus-5-5")
    with pytest.raises(InvalidInputError, match="there is none"):
        _start_d(db_path, clock, root)  # no Jev run yet
    monkeypatch.setattr(jev, "FAULT_SHARE", 0.4)
    run_jev(db_path, clock, root, Fake({"starter-bec": 500}))  # incomplete: a safety card
    _clear()
    with pytest.raises(InvalidInputError, match="there is none"):
        _start_d(db_path, clock, root)
    run_jev(db_path, clock, root, Fake(), signals=False)  # an experiment
    _clear()
    with pytest.raises(InvalidInputError, match="there is none"):
        _start_d(db_path, clock, root)
    run_jev(db_path, clock, root, Fake())
    _clear()

    def new_pin(_schema: object) -> tuple[str, str]:
        return "jev-9.9.9", "x"

    monkeypatch.setattr(evaljev, "source_inputs", new_pin)  # a new pin
    with pytest.raises(InvalidInputError, match="there is none"):
        _start_d(db_path, clock, root)
    monkeypatch.undo()
    assert _start_d(db_path, clock, root)["preset"] == "D"
    del conn


def test_a_c_run_records_its_input_format(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    """Choice 14: C runs write `input_format: claude-v1`, so Jev vs C prints "different
    inputs"."""
    claude_eval.start(lambda: db.connect(db_path), clock, db_path.parent,
                      claude_eval.Options(root), spawn=_inline)  # fmt: skip
    run = claude_eval.current()
    assert run is not None
    w = run.works[0]
    w.result = evalrun.score(w.case, None, None, None)  # one case scored, then stopped
    claude_eval.stop(lambda: db.connect(db_path), clock)
    m = json.loads(_row(conn)["metrics"])
    assert m["input_format"] == claude_eval.CLAUDE_INPUT_FORMAT == "claude-v1"
    assert "excerpt_cap" not in m and m["claude"]["preset"] == "C"
    claude_eval.reset()
    with pytest.raises(InvalidInputError, match="preset C or D"):
        claude_eval.start(lambda: db.connect(db_path), clock, db_path.parent,
                          claude_eval.Options(root, preset="B"), spawn=_inline)  # fmt: skip


# ---- report lines --------------------------------------------------------------------------------


def test_compare_prints_jev_figures_faults_and_why_a_run_is_incomplete(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, db_path: Path, clock: FakeClock,
    root: Path,
) -> None:  # fmt: skip
    """P25, P22: the per-run lines print `summary.jev`, the faulted count and the incomplete
    reason; `ecf eval status` says why the run can't pass."""
    from ecf import cli  # noqa: PLC0415

    del conn
    r = load_result(_jev_result(monkeypatch, db_path, clock, root, incomplete=True))
    s = cast(dict[str, Any], r.summary)
    s["jev"] = s["jev"] | {"input_tokens_billed": 9,
                           "options": {"state": "s1", "preamble": "state", "concurrency": 1},
                           "latency_ms_by_concurrency": {"1": {"n": 4, "p50": 400, "p95": 403,
                                                              "max": 403}},
                           "end_to_end_ms": {"p50": 410.0, "p95": 500.0, "max": 500.0},
                           "cost": {"usd": 0.0001, "basis": "b"}, "retries": {"provider": 3},
                           "pauses": {}, "faulted_cases": {"provider": 1}}  # fmt: skip
    assert cli._state_line(r) == ("incomplete (faulted_safety_card); faulted cases 1 (scored as"  # pyright: ignore[reportPrivateUsage]
                                  " failures)")  # fmt: skip
    lines = cli._jev_figures(r.summary)  # pyright: ignore[reportPrivateUsage]
    assert lines[0] == f"Jev {PIN}: state s1, preamble state, concurrency 1"
    assert "HTTP time at concurrency 1: 400 ms median, p95 403, max 403 (n=4)" in lines[1]
    assert any("input tokens billed 9" in x for x in lines)
    assert any("faulted cases: provider 1" in x for x in lines)
    run_lines = cli._run_lines(r)  # pyright: ignore[reportPrivateUsage]
    assert any("Jev " in x for x in run_lines)
    caveat = cli._run_caveat  # pyright: ignore[reportPrivateUsage]
    assert caveat(s) == " (incomplete: a fraud, escalation or injection card faulted)"
    assert caveat(s | {"incomplete_reason": "budget", "cases": 3}) == (
        " (stopped after 3 cases: the waiting budget was spent)")  # fmt: skip
    assert caveat(s | {"complete": True}) == " (jev backend: not for the gate until step 4)"
    measuring = {"classifier": True, "actor": False, "measuring": ["jev_state s2"]}
    assert caveat(s | {"complete": True, "options": measuring}) == (
        " (jev backend: not for the gate until step 4; jev_state s2)")  # fmt: skip
