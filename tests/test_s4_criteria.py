"""The S4 user-layer measurement's criteria script (scripts/s4_criteria.py; plan §7, P19-P21):
each criterion computed as `ecf eval compare` pairs cases, the pass and fail lines, the repeat
rule and the noise reference, on small synthetic result files built with ecf's own models."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from ecf.eval.results import CaseResult, ClaimRecord, ResultFile, compare

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "s4_criteria.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("s4_criteria", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # its dataclasses look their module up
    spec.loader.exec_module(mod)
    return mod


sc: Any = load()
N = 200  # 100 phishing-labelled, as many as the real set's 93 needs
FIELDS = ("rule", "safety", "category", "fraud_risk", "priority", "requires_action", "phishing")


def _case(i: int, wrong: frozenset[str] = frozenset(), **kw: Any) -> CaseResult:
    fields = {f: f not in wrong for f in FIELDS}
    phish = i < 100  # 100 phishing-labelled cases
    got: dict[str, str | bool | None] = {"category": "invoice", "phishing": phish
                                         and "phishing" not in wrong}  # fmt: skip
    got |= kw.pop("got", {})
    return CaseResult(id=f"c{i:03d}", correct=not wrong & {"rule", "safety", "phishing"},
                      fields=fields, got=got, expected={"phishing": phish, "category": "invoice"},
                      **kw)  # fmt: skip


def _run(rid: str, wrong: dict[int, frozenset[str]] | None = None, *,
         summary: dict[str, Any] | None = None,
         extra: dict[int, dict[str, Any]] | None = None) -> ResultFile:  # fmt: skip
    w = wrong or {}
    s: dict[str, Any] = {
        "unsafe": list[str](),
        "fraud_guard_missed": list[str](),
        "fraud_guard_cases": 10,
        "gate_passed": True,
        "label_map": 2,
    }
    s |= summary or {}
    cases = [_case(i, w.get(i, frozenset()), **(extra or {}).get(i, {})) for i in range(N)]
    return ResultFile(run_id=rid * 4, pair="claude/claude", set_version="v1",
                      created_at="2026-10-14T00:00:00Z", cases=cases, summary=s)  # fmt: skip


def _gated(out: dict[str, Any], figure: str) -> list[dict[str, Any]]:
    return [p for p in out["gated"] if p["figure"] == figure]


def test_the_pairing_is_ecf_eval_compares() -> None:
    """`correct` paired as `results.compare` pairs it: the same counts and interval."""
    a = _run("a1", {i: frozenset({"rule"}) for i in range(10)})
    b = _run("b1", {i: frozenset({"rule"}) for i in range(5, 22)})
    p = sc.paired("correct", a, b)
    c = compare(a, b)
    assert (p.n, p.a_only, p.b_only) == (c.n, c.a_only, c.b_only)
    assert p.diff == pytest.approx(c.diff_points, abs=0.01)
    assert (p.lo, p.hi) == pytest.approx(c.diff_ci, abs=0.01)


def test_identical_runs_pass_every_criterion() -> None:
    s3b = [_run("a1"), _run("a2")]
    out = sc.criteria([_run("b1"), _run("b2")], s3b)
    assert out["passed"] and len(out["gated"]) == 6 * 4  # six figures, four cross pairings
    assert all(p["passed"] for p in out["gated"])
    assert out["repeat"].startswith("repeat: not needed")
    text = sc.text(out)
    assert "M2: PASS" in text and "FAIL" not in text


def test_a_rule_loss_past_its_margin_fails_and_exits_1(tmp_path: Path) -> None:
    """Rule's margin is 3.5 (P5): 10 rule losses in 200 fail every pairing."""
    lost = {i: frozenset({"rule"}) for i in range(10)}
    out = sc.criteria([_run("b1", lost), _run("b2", lost)], [_run("a1"), _run("a2")])
    assert not out["passed"]
    assert all(p["passed"] is False and p["margin"] == 3.5 for p in _gated(out, "rule"))
    assert all(p["passed"] for p in _gated(out, "fraud_risk"))
    assert "FAIL (> -3.5)" in sc.text(out)
    paths: list[Path] = []
    for r in (_run("b1", lost), _run("b2", lost), _run("a1"), _run("a2")):
        paths.append(tmp_path / f"{r.run_id}.json")
        paths[-1].write_text(r.model_dump_json())
    assert sc.main([str(paths[0]), str(paths[1]), "--baseline", str(paths[2]),
                    str(paths[3])]) == 1  # fmt: skip
    assert sc.main([str(paths[2]), str(paths[3]), "--baseline", str(paths[2]),
                    str(paths[3]), "--json"]) == 0  # fmt: skip


def test_phishing_recall_counts_only_phishing_labelled_cases() -> None:
    """Criterion 7 (P21): pairs on the 100 positives; a miss on a negative doesn't count."""
    b = _run("b1", {i: frozenset({"phishing"}) for i in range(2)})
    p = sc.paired("phishing_recall", _run("a1"), b)
    assert (p.n, p.a_right, p.b_right) == (100, 100, 98)
    pooled = sc.pooled("phishing_recall", [b, _run("b2")], [_run("a1"), _run("a2")])
    assert (pooled.n, pooled.b_right) == (200, 198) and pooled.margin is None


def test_every_shipped_field_is_old_style_correct() -> None:
    """Criterion 3, old-style `correct` (P6; the definition behind plan §7's figure): schema v2's
    fields and the rule; a wrong fixed item, `safety` or user field doesn't make it wrong."""
    c = _case(0, frozenset({"phishing", "safety"}))
    c.fields["contract_stage"] = False
    assert sc._shipped_right(c) is True  # pyright: ignore[reportPrivateUsage]
    assert sc._shipped_right(_case(2, frozenset({"rule"}))) is False  # pyright: ignore[reportPrivateUsage]
    assert sc._shipped_right(_case(1, frozenset({"priority"}))) is False  # pyright: ignore[reportPrivateUsage]
    assert sc._shipped_right(CaseResult(id="x", correct=True)) is None  # pyright: ignore[reportPrivateUsage]


def test_criterion_1_reads_the_gates_and_refused_submissions() -> None:
    claims = [ClaimRecord(stage="classify", spawn="ecf-classifier", round=1, session=1,
                          preparing=False, claimed_s=0.0, invalid=2, outcome="recorded"),
              ClaimRecord(stage="act", spawn="ecf-actor", round=2, session=1, preparing=False,
                          claimed_s=1.0, invalid=1, outcome="recorded")]  # fmt: skip
    bad = _run("b1", summary={"unsafe": ["c001"], "gate_passed": False},
               extra={3: {"claims": claims}})  # fmt: skip
    r = sc.run_checks(bad)
    assert (r["unsafe"], r["refused_classify"], r["refused_act"], r["passed"]) == (1, 2, 1, False)
    out = sc.criteria([bad, _run("b2")], [_run("a1"), _run("a2")])
    assert not out["passed"] and "refused submissions classify 2 act 1  FAIL" in sc.text(out)


def test_added_category_values_and_your_fields_are_listed() -> None:
    """Criterion 6's list and 8's answer distribution, from each case's `got`."""
    extra: dict[int, dict[str, Any]] = {
        i: {"got": {"category": "legal_notice", "contract_stage": "draft"}} for i in (4, 7)
    }
    extra[9] = {"got": {"needs_signature": True}}
    b = _run("b1", extra=extra)
    out = sc.criteria([b, _run("b2")], [_run("a1"), _run("a2")])
    assert out["added_category_cases"]["b1b1b1b1"] == ["c004", "c007"]
    answers = out["user_answers"]["b1b1b1b1"]
    assert answers["contract_stage"] == {"draft": 2}
    assert answers["needs_signature"] == {"true": 1}
    assert answers["category (added values)"] == {"legal_notice": 2}
    assert "phishing" not in answers and "rule" not in answers


def test_the_repeat_rule_is_one_near_miss_only() -> None:
    """P5: exactly one pairing missing by under 1 point allows one more run; anything else
    doesn't."""
    p = sc.Paired

    def miss(lo: float) -> Any:
        return p("rule", "a", "b", 100, 90, 85, 6, 1, -5.0, lo, 1.0, 3.5)

    ok = p("rule", "a", "b", 100, 90, 90, 0, 0, 0.0, -2.0, 2.0, 3.5)
    assert sc.repeat_line([ok, ok]).startswith("repeat: not needed")
    assert sc.repeat_line([miss(-4.0), ok]).startswith("repeat: allowed")
    assert sc.repeat_line([miss(-4.6), ok]).startswith("repeat: not allowed")
    assert sc.repeat_line([miss(-4.0), miss(-4.0)]).startswith("repeat: not allowed")


def test_the_noise_reference_and_within_arm_discordance() -> None:
    """The S3b pair against itself: run 2 minus run 1 per figure (plan §7's table), and the
    `correct` discordance of each arm (P31)."""
    a1 = _run("a1", {i: frozenset({"rule"}) for i in range(15)})
    a2 = _run("a2", {i: frozenset({"rule"}) for i in range(6, 15)} | {50: frozenset({"rule"})})
    rows = {x.figure: x for x in sc.noise(a1, a2)}
    assert rows["correct"].a_only == 1 and rows["correct"].b_only == 6
    assert rows["rule"].diff == pytest.approx(2.5)
    out = sc.criteria([_run("b1"), _run("b2")], [a1, a2])
    assert out["within_arm"]["s3b"] == {"n": 200, "discordant": 7, "run1_only": 1,
                                        "run2_only": 6}  # fmt: skip
    assert out["within_arm"]["s4"]["discordant"] == 0
    assert len(out["noise_reference"]) == len(sc.FIGURES)


def test_noise_only_prints_the_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(_run("a1").model_dump_json())
    b.write_text(_run("a2", {0: frozenset({"rule"})}).model_dump_json())
    assert sc.main(["--noise-only", str(a), str(b), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["figure"] for r in rows} == set(sc.FIGURES)


def test_different_label_maps_are_refused(tmp_path: Path) -> None:
    files: list[Path] = []
    for r in (_run("b1"), _run("b2"), _run("a1", summary={"label_map": 1}), _run("a2")):
        files.append(tmp_path / f"{r.run_id}.json")
        files[-1].write_text(r.model_dump_json())
    assert sc.main([str(files[0]), str(files[1]), "--baseline", str(files[2]),
                    str(files[3])]) == 2  # fmt: skip


def test_sonnet_input_per_case_or_not_in_the_result() -> None:
    claude = {"groups": [{"model": "claude-sonnet-5-5", "input_tokens": 1000,
                          "cache_read_tokens": 9000, "cache_creation_tokens": 0},
                         {"model": "claude-opus-5-5", "input_tokens": 50_000}]}  # fmt: skip
    assert (
        sc.sonnet_input_per_case(_run("b1", summary={"claude": claude})) == 50.0
    )  # 10,000 over 200 cases
    assert sc.sonnet_input_per_case(_run("b1")) is None
    out = sc.criteria([_run("b1"), _run("b2")], [_run("a1"), _run("a2")])
    assert "Sonnet input per case: not in the result" in sc.text(out)
    assert out["read_bytes"].startswith("not in the result")


# C22: the S3b pair's committed fixture reproduces plan §7's noise reference.

S3B = Path(__file__).resolve().parent / "data" / "s3b_noise_pair.json"
ROW = r"^[a-z0-9-]+ [01] [01-]+ [01-] [cu][sn]$"
# plan §7 (pre-registered, P5, P22): figure -> (n, diff, lower, upper), points
NOISE = {
    "correct": (304, 1.97, -1.30, 5.32),
    "rule": (304, 0.33, -2.27, 2.96),
    "category": (303, 0.99, -2.11, 4.12),
    "fraud_risk": (249, 0.00, -3.20, 3.20),
    "priority": (116, -9.48, -16.52, -2.50),
    "requires_action": (150, -6.00, -11.31, -1.16),
    "every_shipped_field": (304, -0.66, -4.55, 3.24),
    "phishing": (304, 0.00, -2.05, 2.05),
}


def _s3b_runs() -> list[ResultFile]:
    """The two S3b runs (`024ec270`, `1a8708cf`) from the committed fixture, derived from their
    result files: per case its id and correctness bits only (`about` in the file)."""
    doc: dict[str, Any] = json.loads(S3B.read_text())
    names: list[str] = doc["fields"]
    out: list[ResultFile] = []
    for r in doc["runs"].values():
        cases: list[CaseResult] = []
        for row in r["rows"]:
            cid, correct, cells, ph, flags = row.split()
            fields = {n: c == "1" for n, c in zip(names, cells, strict=True) if c != "-"}
            extra: dict[str, Any] = {}
            if ph != "-":  # labelled phishing: found or not
                extra = {"expected": {"phishing": True}, "got": {"phishing": ph == "1"}}
            cases.append(CaseResult(id=cid, correct=correct == "1", fields=fields,
                                    confirmed=flags[0] == "c", scored=flags[1] == "s",
                                    **extra))  # fmt: skip
        out.append(ResultFile(run_id=r["run_id"], pair=r["pair"], set_version=r["set_version"],
                              created_at=r["created_at"], cases=cases))  # fmt: skip
    return out


def test_the_s3b_fixture_holds_only_ids_and_bits() -> None:
    import re  # noqa: PLC0415

    doc: dict[str, Any] = json.loads(S3B.read_text())
    assert set(doc) == {"about", "fields", "runs"}
    labels = Path(__file__).resolve().parent / "eval" / "synthetic" / "labels.jsonl"
    ids = {json.loads(line)["id"] for line in labels.read_text().splitlines()}
    for r in doc["runs"].values():
        assert set(r) == {"run_id", "pair", "set_version", "created_at", "rows"}
        assert all(re.fullmatch(ROW, row) for row in r["rows"])
        assert {row.split()[0] for row in r["rows"]} <= ids  # card ids of the committed set


def test_the_noise_reference_reproduces_plan_s7() -> None:
    a, b = _s3b_runs()
    assert (a.run_id[:8], b.run_id[:8], a.set_version) == (
        "024ec270",
        "1a8708cf",
        "43f650c9d1839407",
    )
    got = {p.figure: p for p in sc.noise(a, b)}
    for figure, (n, diff, lo, hi) in NOISE.items():
        p = got[figure]
        assert (p.n, round(p.diff, 2), round(p.lo, 2), round(p.hi, 2)) == (n, diff, lo, hi), figure
    # within-arm `correct` discordance (P31): 24, 15/9
    assert (got["correct"].a_only, got["correct"].b_only) == (9, 15)
