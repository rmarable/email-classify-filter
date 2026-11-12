"""`scripts/jev_questions.py` (planning-docs/JEV-NATIVE-QUESTIONS.md §5, Q12, R9): the void rule,
the caution tests, the label-required actions, run agreement, the reversed-order flips and the
threshold-sensitivity table, on seeded results; no TypeSafe call and no eval set."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from ecf.eval.results import CaseResult, ResultFile
from ecf.schema import load_schema
from ecf_server import jev_native

SCHEMA = load_schema()
NATIVE = jev_native.load(SCHEMA)


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "jev_questions", Path(__file__).parent.parent / "scripts" / "jev_questions.py")  # fmt: skip
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jev_questions"] = mod  # its dataclasses look their module up
    spec.loader.exec_module(mod)
    return mod


JQ = _script()
FREEZE = "f" * 40
REG: dict[str, Any] = {
    "freeze_commit": FREEZE,
    "set": {"set_version": "v1", "cases": 2, "private_cases": 1, "injection_cards": 1},
    "arms": {"J-claude": {"prompt_digest": "pd", "gate_key": "gk"}},
}
ROUNDS: dict[str, Any] = {"1": {
    "start": {"time": "2026-11-12T10:00:00+00:00",
              "repos": {"worktree": {"head": FREEZE, "status": ""}, "main": {"status": ""}}},
    "end": {"time": "2026-11-12T11:00:00+00:00",
            "repos": {"worktree": {"head": FREEZE, "status": ""}, "main": {"status": ""}}},
}}  # fmt: skip


def summary(**over: Any) -> dict[str, Any]:
    s: dict[str, Any] = {
        "complete": True, "faulted": [], "determinism": {"faulted_pairs": []},
        "jev": {"usage_missing": 0}, "options": {"classifier": True, "for_gate": False},
        "prompt_digest": "pd", "gate_key": "gk", "cases": 2, "private_cases": 1,
        "power": {"ac_at_start": True, "ac_at_end": True},
        "started_at": "2026-11-12T10:10:00Z", "wall_s": 60.0,
    }  # fmt: skip
    return s | over


def result(cs: list[CaseResult], **over: Any) -> ResultFile:
    created = over.pop("created_at", "2026-11-12T10:11:05Z")
    return ResultFile(run_id="r" * 32, pair="jev/hosted", set_version="v1", created_at=created,
                      cases=cs, summary=summary(**over), format=7)  # fmt: skip


def case(cid: str, **over: Any) -> CaseResult:
    base: dict[str, Any] = {"id": cid, "correct": True, "got": {"fraud_risk": "low"},
                            "actions": ["flag", "escalate"]}  # fmt: skip
    return CaseResult.model_validate(base | over)


def run(r: ResultFile, arm: str = "J-claude") -> Any:
    return JQ.Run(arm, "1", Path("x.json"), r)


def test_a_clean_run_counts() -> None:
    assert JQ.void_reasons(run(result([case("a")])), REG, ROUNDS, 1) == []


@pytest.mark.parametrize(("over", "why"), [
    ({"complete": False}, "not complete"),
    ({"faulted": ["a"]}, "faulted cases"),
    ({"determinism": {"faulted_pairs": ["a"]}}, "faulted determinism pairs"),
    ({"jev": {"usage_missing": 1}}, "replies without usage"),
    ({"options": {"for_gate": True}}, "not --not-for-gate"),
    ({"prompt_digest": "other"}, "prompt digest not as registered"),
    ({"gate_key": "other"}, "gate key not as registered"),
    ({"cases": 3}, "case count not as registered"),
    ({"private_cases": 0}, "private count not as registered"),
    ({"power": {"ac_at_start": False, "ac_at_end": True}}, "not on AC power throughout"),
    ({"started_at": "2026-11-12T10:05:00Z"}, "a pause during the run (sleep?)"),
    ({"created_at": "2026-11-12T12:00:00Z", "started_at": "2026-11-12T11:58:00Z"},
     "the run ended outside its round's records"),
])  # fmt: skip
def test_each_void_condition(over: dict[str, Any], why: str) -> None:
    assert why in JQ.void_reasons(run(result([case("a")], **over)), REG, ROUNDS, 1)


def test_round_records_and_injection_count_void() -> None:
    dirty = {"1": {**ROUNDS["1"], "end": {**ROUNDS["1"]["end"], "repos": {
        "worktree": {"head": FREEZE, "status": ""}, "main": {"status": " M x"}}}}}  # fmt: skip
    assert "a repo had uncommitted changes" in JQ.void_reasons(
        run(result([case("a")])), REG, dirty, 1)  # fmt: skip
    moved = {"1": {**ROUNDS["1"], "start": {**ROUNDS["1"]["start"], "repos": {
        "worktree": {"head": "0" * 40, "status": ""}}}}}  # fmt: skip
    assert "worktree HEAD isn't the freeze commit" in JQ.void_reasons(
        run(result([case("a")])), REG, moved, 1)  # fmt: skip
    assert "injection cards not as registered" in JQ.void_reasons(
        run(result([case("a")])), REG, ROUNDS, 0)  # fmt: skip
    assert "round 1 has no start and end record" in JQ.void_reasons(
        run(result([case("a")])), REG, {}, 1)  # fmt: skip


def test_required_actions() -> None:
    assert JQ._required({"rule": "fraud_guard"}) == {"flag", "escalate"}
    assert JQ._required({"rule": "fraud_weak"}) == {"flag"}
    assert JQ._required({"rule": "regulatory", "safety": {"must_escalate": True}}) == {"escalate"}
    assert JQ._required({"rule": "marketing"}) == set()


def test_caution_tests() -> None:
    labels = {"a": {"rule": "fraud_guard", "labels": {"fraud_risk": "medium"}},
              "b": {"rule": "fraud_weak", "labels": {"fraud_risk": "low"}}}  # fmt: skip
    claude = [result([case("a"), case("b")]) for _ in range(2)]
    fine = result([case("a"), case("b", actions=["flag"])])
    c = JQ.caution(fine, claude, labels)
    assert c["labelled_to_none"] == [] and c["required_actions_lost"] == []
    worse = result([case("a", got={"fraud_risk": "none"}, actions=["flag"]), case("b")])
    c = JQ.caution(worse, claude, labels)
    assert c["labelled_to_none"] == ["a"] and c["required_actions_lost"] == ["a:escalate"]
    # an action the label doesn't require may go (Q3): b's escalate isn't required
    assert JQ.caution(result([case("a"), case("b", actions=[])]), claude,
                      labels)["required_actions_lost"] == ["b:flag"]  # fmt: skip


def test_agreement_counts_differences() -> None:
    a = result([case("a", got={"rule": "x", "phishing": True}), case("b")])
    b = result([case("a", got={"rule": "y", "phishing": True}, correct=False), case("b")])
    got = JQ.agreement(a, b)
    assert got["rule"] == 1 and got["correct"] == 1 and got["phishing"] == 0


def _raw(topic: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for qid, q in NATIVE.questions.items():
        if q["type"] == "noul":
            out[qid] = 0.95 if qid == "cat_fits" else 0.1
        elif q["type"] == "choice":
            opts = list(q["criteria"])
            pick = topic if qid == "cat_topic" else opts[0]
            out[qid] = {"choice": pick, "probabilities": {o: 0.9 if o == pick else 0.01
                                                           for o in opts}}  # fmt: skip
        else:
            out[qid] = {"probabilities": [0.1, 0.7, 0.1, 0.1]}
    return out


def test_reversed_flips_and_sensitivity() -> None:
    fwd1 = result([case("a", question_answers=_raw("marketing"), got={"category": "marketing"})])
    fwd2 = result([case("a", question_answers=_raw("marketing"), got={"category": "marketing"})])
    rev = result([case("a", question_answers=_raw("shipping"), got={"category": "shipping"})])
    flips = JQ.reversed_flips(rev, [fwd1, fwd2])
    assert flips["cat_topic"] == {"reversed_vs_forward": 1.0, "forward_vs_forward": 0.0}
    table = JQ.sensitivity(fwd1)
    assert {(t["field"], t["threshold"]) for t in table} == {
        ("category", 0.5), ("category", 0.6), ("category", 0.8),
        ("fraud_risk", 0.4), ("fraud_risk", 0.6)}  # fmt: skip
    assert all(t["changed"] == 0 for t in table)  # every question far from its thresholds


def test_grams() -> None:
    assert ("a", "b", "c", "d", "e") in JQ._grams("A b, c! d e f")
    assert not JQ._grams("four words only here")
