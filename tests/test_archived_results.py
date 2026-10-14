"""Every archived result shape still loads and rescores after v3.0.0 removed Gemma, preset B and
the decision models (ADR 0031; R-D12, P6, P15): no field left `CaseResult`, `CallMetrics`,
`ClaimRecord` or `ResultFile`, and both rescores read Gemma live and `-policy` results, B's
(Gemma's classifications, Claude's actor), C's, v1.0's and a decision model's with
probabilities. The fixtures are real synthetic-set results from `state-archive/evals/` and
`state-archive/v1.0/eval/` cut to their first cases (case IDs, closed values and figures only,
no message text, I5); `systemone.json` is built in their shape (no such run was archived)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ecf.eval.results import ResultFile, compare, load_result
from ecf_server import evalrun
from ecf_server.clock import FakeClock

FIXTURES = Path(__file__).parent / "fixtures" / "archived_results"
SYNTHETIC = Path(__file__).parent / "eval" / "synthetic"
SHAPES = {
    "gemma_live.json": "gemma4-12b/local",
    "gemma_policy.json": "gemma4-12b/local",
    "gemma_v1.json": "gemma4-12b/local",
    "preset_b.json": "gemma4-12b/claude",
    "preset_b_v1.json": "gemma4-12b/claude",
    "preset_c.json": "claude/claude",
    "systemone.json": "systemone-tev1-4b/local",
}


def _copy(name: str, tmp_path: Path) -> Path:
    path = tmp_path / name
    shutil.copy(FIXTURES / name, path)
    return path


@pytest.mark.parametrize(("name", "pair"), SHAPES.items())
def test_every_shape_loads_and_rescores(
    name: str, pair: str, tmp_path: Path, clock: FakeClock
) -> None:
    path = _copy(name, tmp_path)
    old = load_result(path)  # `extra="forbid"`: a dropped field would refuse the file
    assert old.pair == pair and old.cases
    # re-serialised, the file keeps every key it had (P15)
    raw = json.loads(path.read_text())
    again = json.loads(old.model_dump_json())
    for before, after in zip(raw["cases"], again["cases"], strict=True):
        assert set(before) <= set(after), name
    for rescore in (evalrun.rescore_synthetic, evalrun.rescore_through_policy):
        out = rescore(SYNTHETIC, path, clock)
        new = load_result(Path(out["path"]))
        assert new.pair == pair and new.run_id == old.run_id and new.digest == old.digest
        assert {c.id for c in new.cases} == {c.id for c in old.cases}
        assert out["summary"]["gate_passed"] is False  # a rescore never passes the gate
        assert out["summary"]["rescored_from"]["run_id"] == old.run_id
        by_id = {c.id: c for c in new.cases}
        for c in old.cases:  # what the run measured is kept
            assert by_id[c.id].probabilities == c.probabilities
            assert by_id[c.id].classifier_ms == c.classifier_ms
            assert by_id[c.id].claims == c.claims
    assert path.exists()  # the original is never touched


def test_rescored_old_results_compare_with_each_other(tmp_path: Path, clock: FakeClock) -> None:
    """Only a rescore bridges the label maps (P6): Gemma's and C's archived results, rescored
    onto today's set, compare."""
    files: list[ResultFile] = []
    for name in ("gemma_policy.json", "preset_c.json"):
        out = evalrun.rescore_synthetic(SYNTHETIC, _copy(name, tmp_path), clock)
        files.append(load_result(Path(out["path"])))
    assert compare(files[0], files[1]).n >= 1


def test_a_decision_models_probabilities_survive_the_rescore(
    tmp_path: Path, clock: FakeClock
) -> None:
    out = evalrun.rescore_through_policy(SYNTHETIC, _copy("systemone.json", tmp_path), clock)
    s = out["summary"]
    assert s["backend"] == "systemone:tev1-4b"  # copied from the original's summary
    case = load_result(Path(out["path"])).cases[0]
    assert case.probabilities is not None and case.probabilities["fraud_risk"]["high"] == 0.45
