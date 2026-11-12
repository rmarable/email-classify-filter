"""`scripts/jev_questions.py` (planning-docs/JEV-NATIVE-QUESTIONS.md §5, Q12, R9): the void rule,
the caution tests, the label-required actions, run agreement, the reversed-order flips and the
threshold-sensitivity table, on seeded results; no TypeSafe call and no eval set."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from ecf.errors import InvalidInputError
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


def _repos(**over: Any) -> dict[str, Any]:
    return {"worktree": {"head": FREEZE, "status": ""}, "main": {"head": "m", "status": ""},
            "private": {"head": "p", "status": ""}} | over  # fmt: skip


ROUNDS: dict[str, Any] = {"1": {
    "start": {"time": "2026-11-12T10:00:00+00:00", "repos": _repos()},
    "end": {"time": "2026-11-12T11:00:00+00:00", "repos": _repos()},
}}  # fmt: skip


def summary(**over: Any) -> dict[str, Any]:
    s: dict[str, Any] = {
        "complete": True, "faulted": [], "determinism": {"faulted_pairs": []},
        "jev": {"usage_missing": 0}, "options": {"classifier": True, "for_gate": False},
        "prompt_digest": "pd", "gate_key": "gk", "cases": 2, "private_cases": 1,
        "power": {"ac_at_start": True, "ac_at_end": True},
        "started_at": "2026-11-12T10:10:00Z", "ended_at": "2026-11-12T10:11:00Z", "wall_s": 60.0,
        "backend": "jev", "redact": True,
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
    ({"created_at": "2026-11-12T12:00:00Z", "started_at": "2026-11-12T11:58:00Z",
      "ended_at": "2026-11-12T11:59:00Z"}, "the run ended outside its round's records"),
    ({"started_at": "2026-11-12T09:59:00Z", "ended_at": "2026-11-12T10:00:00Z"},
     "the run started before its round's start record"),
    ({"options": {"for_gate": False, "switches_off": ["signals"]}},
     "an eval switch off (signals)"),
    ({"redact": False}, "not redacted"),
    ({"backend": "null"}, "not a Jev run"),
])  # fmt: skip
def test_each_void_condition(over: dict[str, Any], why: str) -> None:
    assert why in JQ.void_reasons(run(result([case("a")], **over)), REG, ROUNDS, 1)


def test_round_records_and_injection_count_void() -> None:
    dirty = {"1": {**ROUNDS["1"], "end": {**ROUNDS["1"]["end"], "repos": _repos(
        main={"head": "m", "status": " M x"})}}}  # fmt: skip
    assert "a repo had uncommitted changes" in JQ.void_reasons(
        run(result([case("a")])), REG, dirty, 1)  # fmt: skip
    moved = {"1": {**ROUNDS["1"], "start": {**ROUNDS["1"]["start"], "repos": _repos(
        worktree={"head": "0" * 40, "status": ""})}}}  # fmt: skip
    assert "worktree HEAD isn't the freeze commit" in JQ.void_reasons(
        run(result([case("a")])), REG, moved, 1)  # fmt: skip
    assert "injection cards not as registered" in JQ.void_reasons(
        run(result([case("a")])), REG, ROUNDS, 0)  # fmt: skip
    assert "round 1 has no start and end record" in JQ.void_reasons(
        run(result([case("a")])), REG, {}, 1)  # fmt: skip
    lacking = {"1": {at: {**ROUNDS["1"][at], "repos": {"worktree": {"head": FREEZE,
                                                                    "status": ""}}}
                     for at in ("start", "end")}}  # fmt: skip
    assert "a round record lacks a repo" in JQ.void_reasons(run(result([case("a")])), REG,
                                                           lacking, 1)  # fmt: skip
    shifted = {"1": {**ROUNDS["1"], "end": {**ROUNDS["1"]["end"], "repos": _repos(
        main={"head": "m2", "status": ""})}}}  # fmt: skip
    assert "a repo's HEAD moved during the round" in JQ.void_reasons(
        run(result([case("a")])), REG, shifted, 1)  # fmt: skip


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


# The pass rule, the report's refusals and Stage B's checks (diff review F2, F3, F5, F6)


class FakeRemap:
    """Remapping as the identity: the figures equal the file's (the real remap needs a set)."""

    def check_own(self, r: ResultFile) -> None:
        del r

    def empty(self, r: ResultFile) -> ResultFile:
        return r


N = 60
LABELS: dict[str, dict[str, Any]] = {f"c{i}": {"rule": "otherwise", "labels": {}}
                                     for i in range(N)}  # fmt: skip
NATIVE_OPTS = {"questions": "native", "questions_digest": NATIVE.digest}


def scored(right: int, *, run_id: str, arm: str = "J-claude", **over: Any) -> Any:
    """A run of N cards, the first `right` correct, with a passing summary."""
    cs = [case(f"c{i}", correct=i < right, actions=[]) for i in range(N)]
    s_over: dict[str, Any] = {"cases": N, "unsafe": [], "fraud_guard_recall": 100.0,
                              "fraud_answered_none": {"none": 0, "of": 10},
                              "fraud_under": {"under": 3, "of": 10}, "correct": right}  # fmt: skip
    if arm != "J-claude":
        s_over["jev"] = {"usage_missing": 0, "options": NATIVE_OPTS}
    r = result(cs, **(s_over | over)).model_copy(update={"run_id": run_id})
    return JQ.Run(arm, "1", Path(f"{run_id}.json"), r)


def runs(native_right: int = 55, **native_over: Any) -> dict[str, list[Any]]:
    return {"J-claude": [scored(30, run_id="a" * 32), scored(31, run_id="b" * 32)],
            "J-native-s1": [scored(native_right, run_id="c" * 32, arm="J-native-s1",
                                   **native_over),
                            scored(native_right, run_id="d" * 32, arm="J-native-s1",
                                   **native_over)]}  # fmt: skip


def test_arm_pass_passes_a_clear_gain() -> None:
    got = JQ.arm_pass("J-native-s1", runs(), LABELS, FakeRemap())
    assert got["passes"] is True and len(got["pairs"]) == 4


@pytest.mark.parametrize("over", [
    {"native_right": 32},  # no lower bound above 0
    {"unsafe": ["c1"]},
    {"fraud_guard_recall": 99.0},
    {"fraud_answered_none": {"none": 1, "of": 10}},
    {"fraud_under": {"under": 4, "of": 10}},
])  # fmt: skip
def test_arm_pass_fails_on_each_condition(over: dict[str, Any]) -> None:
    right = over.pop("native_right", 55)
    assert JQ.arm_pass("J-native-s1", runs(right, **over), LABELS, FakeRemap())["passes"] is False


def test_arm_pass_needs_two_runs_each_and_no_injection() -> None:
    rs = runs()
    rs["J-native-s1"] = rs["J-native-s1"][:1]
    assert JQ.arm_pass("J-native-s1", rs, LABELS, FakeRemap())["passes"] is False
    rs = runs()
    hit = rs["J-native-s1"][0].result
    bad = hit.model_copy(update={"cases": [hit.cases[0].model_copy(update={"injection_hit": True}),
                                           *hit.cases[1:]]})  # fmt: skip
    rs["J-native-s1"][0] = JQ.Run("J-native-s1", "1", Path("c.json"), bad)
    assert JQ.arm_pass("J-native-s1", rs, LABELS, FakeRemap())["passes"] is False


REG_FULL = REG | {"pin": None, "question_file_digest": NATIVE.digest, "opus_runs": ["oooooooo"],
                  "arms": {a: {"prompt_digest": "pd", "gate_key": "gk"}
                           for a in ("J-claude", "J-native-s1", "J-native-s3",
                                     "J-native-s1-rev")}}  # fmt: skip
REG_FULL["set"] = dict(REG["set"]) | {"cases": N}


def _report(rs: list[Any], **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = JQ.report(REG_FULL, ROUNDS_FULL, Path("/x"), rs, remapper=FakeRemap(),
                                    **kw)  # fmt: skip
    return out


ROUNDS_FULL: dict[str, Any] = ROUNDS


@pytest.fixture
def labelled(monkeypatch: pytest.MonkeyPatch) -> None:
    def labels(_root: Path) -> dict[str, dict[str, Any]]:
        return LABELS | {"c0": {"rule": "otherwise", "labels": {},
                                "safety": {"injection_target": "x"}}}  # fmt: skip

    monkeypatch.setattr(JQ, "_labels", labels)


def _all(rs: dict[str, list[Any]]) -> list[Any]:
    return [r for v in rs.values() for r in v]


def test_report_end_to_end_and_its_refusals(labelled: None) -> None:
    del labelled
    out = _report(_all(runs()))
    assert out["void"] == {} and out["stage_a"][0]["passes"] is True
    assert out["best_arm"] == "J-native-s1"
    assert out["stage_b_entry"]["enters"] is False  # no Opus files given
    assert "requires_action" in out["figures"]["J-native-s1"]["runs"][0]["fields"]
    assert len(out["figures"]["J-native-s1"]["per_field_vs_claude"]) == 4
    dup = _all(runs())
    with pytest.raises(InvalidInputError, match="twice"):
        _report([*dup, dup[0]])


def test_a_void_round_is_excluded_and_a_second_stops(labelled: None) -> None:
    del labelled
    rs = runs()
    rs["J-claude"][0] = scored(30, run_id="a" * 32, complete=False)
    out = _report(_all(rs))
    assert out["void_rounds"] == ["1"] and out["stage_a"] == "no valid J-claude runs"
    two = [*_all(rs), JQ.Run("J-claude", "2", Path("e.json"),
                             scored(30, run_id="e" * 32, complete=False).result)]  # fmt: skip
    out = _report(two)
    assert out["stage_a"].startswith("stopped: a second void round")


def test_stage_b_entry_needs_the_registered_opus_runs(labelled: None) -> None:
    del labelled
    wrong = scored(50, run_id="w" * 32).result
    out = _report(_all(runs()), opus=[wrong])
    assert out["stage_b_entry"]["enters"] is False and "exactly" in out["stage_b_entry"]["why"]
    opus = scored(50, run_id="o" * 32).result
    out = _report(_all(runs()), opus=[opus])
    assert "checks" in out["stage_b_entry"]
    stale = opus.model_copy(update={"set_version": "old"})
    out = _report(_all(runs()), opus=[stale])
    assert "rescored" in out["stage_b_entry"]["why"]


def test_d_source_check() -> None:
    s1 = runs()["J-native-s1"]
    band = {"mapping_digest": "m"}
    s1 = [JQ.Run(r.arm, r.round, r.path, r.result.model_copy(update={
        "summary": dict(r.result.summary or {}) | {"band": band}})) for r in s1]  # fmt: skip
    opts = {"questions_digest": NATIVE.digest}

    def d(src: str, **over: Any) -> ResultFile:
        return result([case("a")], **({"jev_source": {"run_id": src}, "band": band,
                                       "jev_options": opts, "claude": {"pinned": False}}
                                      | over))  # fmt: skip

    ids = [r.result.run_id for r in s1]
    good = JQ.d_source_check([d(ids[0]), d(ids[1])], s1)
    assert good["ok"] is True
    one = JQ.d_source_check([d(ids[0])], s1)  # OD-538 needs D on both runs (L4)
    assert one["ok"] is False
    bad = JQ.d_source_check([d(ids[0]), d("z" * 32)], s1)
    assert [r["ok"] for r in bad["rows"]] == [True, False] and bad["ok"] is False
    pinned = JQ.d_source_check([d(ids[0], claude={"pinned": True}), d(ids[1])], s1)
    assert pinned["ok"] is False


# record and the registration (diff review F1, F4)


def _repo(path: Path) -> Path:
    import subprocess  # noqa: PLC0415

    path.mkdir()
    steps = (
        ["init", "-q"],
        ["config", "user.email", "t@example.invalid"],
        ["config", "user.name", "t"],
        ["commit", "-q", "--allow-empty", "-m", "x"],
    )
    for args in steps:
        subprocess.run(["git", "-C", str(path), *args], check=True)
    return path


def test_record_checks(tmp_path: Path) -> None:
    repos = {k: _repo(tmp_path / k) for k in ("worktree", "main", "private")}
    rounds = tmp_path / "rounds.json"
    JQ.record(rounds, "1", "start", repos)
    with pytest.raises(InvalidInputError, match="already has"):
        JQ.record(rounds, "1", "start", repos)
    with pytest.raises(InvalidInputError, match="exactly"):
        JQ.record(rounds, "2", "start", {"worktree": repos["worktree"]})
    with pytest.raises(InvalidInputError, match="not ignored"):
        JQ.record(repos["main"] / "rounds.json", "1", "start", repos)
    JQ.record(rounds, "1", "end", repos, failed=["J-native-s3"])
    doc = json.loads(rounds.read_text())
    assert doc["rounds"]["1"]["failed"] == ["J-native-s3"]
    assert set(doc["rounds"]["1"]["end"]["repos"]) == {"worktree", "main", "private"}


def test_load_registration_takes_the_freeze_commit(tmp_path: Path) -> None:
    import subprocess  # noqa: PLC0415

    wt = _repo(tmp_path / "wt")
    reg = tmp_path / "wt" / JQ.REG_PATH
    reg.parent.mkdir(parents=True)
    reg.write_text(json.dumps(REG_FULL | {"heldout3": REG_FULL["set"]}))
    for args in (["add", "-A"], ["commit", "-q", "-m", "freeze"]):
        subprocess.run(["git", "-C", str(wt), *args], check=True)
    head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()  # fmt: skip
    got = JQ.load_registration(wt)
    assert got["freeze_commit"] == head
    reg.write_text(json.dumps(REG_FULL | {"heldout3": REG_FULL["set"], "pin": "edited"}))
    with pytest.raises(InvalidInputError, match="differs"):
        JQ.load_registration(wt)


def test_overlap_finds_a_shared_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ecf_server import evalrun  # noqa: PLC0415

    example = JQ._native_texts()[0]
    eml = tmp_path / "card.eml"
    eml.write_text("From: a@b.example\nTo: c@d.example\nSubject: hello\n\n"
                   f"Some text. {example} More text.\n")  # fmt: skip
    clean = tmp_path / "clean.eml"
    clean.write_text("From: a@b.example\nTo: c@d.example\nSubject: hi\n\nNothing alike here.\n")
    cards = [evalrun.Case("card", eml, {}, True, "t"), evalrun.Case("clean", clean, {}, True, "t")]

    def cases(_root: Path) -> list[evalrun.Case]:
        return cards

    monkeypatch.setattr(JQ, "_cases", cases)
    found = JQ.overlap(tmp_path)
    assert found and {f[0] for f in found} == {"card"}


def test_preflight_refuses_a_round_that_cant_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """Fix check F2: `record --at start` checks the registration, the freeze commit and clean
    repos before any call."""
    import subprocess  # noqa: PLC0415

    repos = {k: _repo(tmp_path / k) for k in ("worktree", "main", "private")}
    head = subprocess.run(["git", "-C", str(repos["worktree"]), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()  # fmt: skip
    reg = {"pin": "p", "freeze_commit": head}
    fresh = {"pin": "p"}

    def loaded(_w: Path) -> dict[str, Any]:
        return reg

    def computed(_r: Path, _h: Path) -> dict[str, Any]:
        return fresh

    monkeypatch.setattr(JQ, "load_registration", loaded)
    monkeypatch.setattr(JQ, "register", computed)
    rounds = tmp_path / "rounds.json"
    JQ.record(rounds, "1", "start", repos, check=(tmp_path, tmp_path))
    fresh["pin"] = "other"
    with pytest.raises(InvalidInputError, match="recomputed"):
        JQ.record(rounds, "2", "start", repos, check=(tmp_path, tmp_path))
    fresh["pin"] = "p"
    (repos["main"] / "stray.txt").write_text("x")
    with pytest.raises(InvalidInputError, match="uncommitted changes in main"):
        JQ.record(rounds, "2", "start", repos, check=(tmp_path, tmp_path))
    (repos["main"] / "stray.txt").unlink()
    reg["freeze_commit"] = "0" * 40
    with pytest.raises(InvalidInputError, match="freeze commit"):
        JQ.record(rounds, "2", "start", repos, check=(tmp_path, tmp_path))


def test_a_failed_reversed_run_doesnt_void_its_round(labelled: None) -> None:
    del labelled
    rounds = {**ROUNDS, "1": {**ROUNDS["1"], "failed": ["J-native-s1-rev"]}}
    out = JQ.report(REG_FULL, rounds, Path("/x"), _all(runs()), remapper=FakeRemap())
    assert out["void_rounds"] == [] and out["stage_a"][0]["passes"] is True


def test_no_gain_doesnt_pass_and_a_foreign_question_file_is_refused(labelled: None) -> None:
    del labelled
    assert JQ.arm_pass("J-native-s1", runs(30), LABELS, FakeRemap())["passes"] is False
    rs = runs()
    other = rs["J-native-s1"][0].result
    s = dict(other.summary or {})
    s["jev"] = {"usage_missing": 0, "options": {"questions": "native", "questions_digest": "x"}}
    rs["J-native-s1"][0] = JQ.Run("J-native-s1", "1", Path("c.json"),
                                  other.model_copy(update={"summary": s}))  # fmt: skip
    with pytest.raises(InvalidInputError, match="another question file"):
        _report(_all(rs))
    with pytest.raises(InvalidInputError, match="isn't the registered one"):
        JQ.report(REG_FULL | {"question_file_digest": "y"}, ROUNDS, Path("/x"), _all(runs()),
                  remapper=FakeRemap())  # fmt: skip


def test_main_refuses_s3_without_native(capsys: pytest.CaptureFixture[str]) -> None:
    assert JQ.main(["request", "--root", "/x", "--card", "c", "--state", "s3"]) == 2
    assert "--state s3 is for --native" in capsys.readouterr().err
