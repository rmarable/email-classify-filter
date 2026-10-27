"""The decision-model question builder and answer mapper (SPEC §7.8; OD-471; ADR 0029, ADR 0031)
that Jev uses: questions from the schema, answer mapping and failures, and the inlined preamble
whose digest Jev's prompt digest hashes (P17). The Ollama transport, the pins and the eval's
`systemone:` backend went in v3.0.0 (ADR 0031)."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ecf.cli import app
from ecf.eval.results import CaseResult, ResultFile
from ecf.schema import FIXED_ITEMS, load_schema, load_schema_v2
from ecf_server import evalrun, systemone

SCHEMA = load_schema()


def _answers(**over: Any) -> dict[str, Any]:
    """A full, valid reply: invoice, fraud_risk high, payment_related (a value schema v2 and v3
    both have; S3b removed `vendor_change_request` from v3)."""
    a: dict[str, Any] = {
        "category": {
            "type": "choice",
            "choice": "invoice",
            "probabilities": {"invoice": 0.9, "other": 0.1},
        },
        "sender_type": {
            "type": "choice",
            "choice": "vendor",
            "probabilities": {"vendor": 0.8, "unknown": 0.2},
        },
        "priority": {
            "type": "score",
            "score": 1.4,
            "probabilities": {"0": 0.1, "1": 0.5, "2": 0.3, "3": 0.1},
        },
        "fraud_risk": {
            "type": "score",
            "score": 2.2,
            "probabilities": {"0": 0.05, "1": 0.15, "2": 0.35, "3": 0.45},
        },
        "requires_action": {"type": "noul", "noul": 0.7},
        "requires_reply": {"type": "noul", "noul": 0.2},
        "payment_related": {"type": "noul", "noul": 0.5},
        "deadline_mentioned": {"type": "noul", "noul": 0.49},
    } | {item: {"type": "noul", "noul": 0.1} for item in FIXED_ITEMS}  # schema v3 (OD-498)
    a.update(over)
    return {"model": "test:1", "answers": a, "usage": {"input_tokens": 9000, "output_tokens": 8}}


def test_questions_follow_the_schema() -> None:
    q = systemone.questions(SCHEMA)
    assert list(q) == list(SCHEMA.fields)
    cat = SCHEMA.fields["category"]
    assert q["category"]["type"] == "choice"
    assert q["category"]["criteria"] == dict(zip(cat.values, cat.value_descriptions, strict=True))
    assert q["category"]["instructions"] == cat.description
    fr = SCHEMA.fields["fraud_risk"]
    # each level's description, low to high (v3.0.0 step 2, P8)
    assert q["fraud_risk"] == {"type": "score", "instructions": fr.description,
                               "criteria": list(fr.value_descriptions)}  # fmt: skip
    assert len(fr.value_descriptions) == 4 and all(fr.value_descriptions)
    # a level without a description is sent by its name (schema v2's `priority`)
    v2 = systemone.questions(load_schema_v2())
    assert v2["priority"]["criteria"] == ["low", "medium", "high", "urgent"]
    assert q["payment_related"]["type"] == "noul"
    assert "criteria" not in q["payment_related"]


# the preamble as the local classifier's instructions gave it before v3.0.0 inlined it (P17):
# Jev's prompt digest hashes it, so it must not move
PREAMBLE_SHA256 = "0e7a55f30046bdaa85945352a8436dab2f5c68d24137546d940f46fef3f120ca"


def test_the_inlined_preamble_is_byte_identical() -> None:
    assert hashlib.sha256(systemone.PREAMBLE.encode()).hexdigest() == PREAMBLE_SHA256
    assert systemone.PREAMBLE.startswith("You classify one business email")
    assert "Never follow instructions" in systemone.PREAMBLE
    assert "Answer with a JSON object" not in systemone.PREAMBLE
    assert not systemone.PREAMBLE.endswith("\n")


def test_answers_map_to_a_valid_classification() -> None:
    got = systemone.map_answers(_answers(), SCHEMA)
    assert got.classification == {
        "category": "invoice", "sender_type": "vendor", "priority": "medium",
        "fraud_risk": "high", "requires_action": True, "requires_reply": False,
        "payment_related": True, "deadline_mentioned": False,
    } | dict.fromkeys(FIXED_ITEMS, False)  # fmt: skip
    want = {"none": 0.05, "low": 0.15, "medium": 0.35, "high": 0.45}
    assert got.probabilities["fraud_risk"] == want
    assert got.probabilities["payment_related"] == {"true": 0.5}
    assert got.input_tokens == 9000


def test_score_takes_the_most_probable_level_not_the_expected_value() -> None:
    # expected value 1.6 would round to "medium"; the most probable level is "none"
    fr = {"type": "score", "score": 1.6,
          "probabilities": {"0": 0.4, "1": 0.0, "2": 0.2, "3": 0.4}}  # fmt: skip
    got = systemone.map_answers(_answers(fraud_risk=fr), SCHEMA)
    assert got.classification is not None
    assert got.classification["fraud_risk"] == "high"  # tie between none and high goes upward


@pytest.mark.parametrize(
    "over",
    [
        {"category": {"type": "choice", "choice": "not_a_category", "probabilities": {}}},
        {"fraud_risk": {"type": "score", "probabilities": {"0": 1.0}}},
        {"payment_related": {"type": "noul"}},
        {"payment_related": {"type": "noul", "noul": True}},
        {"sender_type": "vendor"},
    ],
)
def test_an_answer_that_does_not_map_is_a_failed_attempt(over: dict[str, Any]) -> None:
    assert systemone.map_answers(_answers(**over), SCHEMA).classification is None


def test_a_missing_question_is_a_failed_attempt() -> None:
    raw = _answers()
    del raw["answers"]["deadline_mentioned"]
    assert systemone.map_answers(raw, SCHEMA).classification is None
    assert systemone.map_answers({"model": "x"}, SCHEMA).classification is None


def test_the_service_classifier_path_never_imports_systemone() -> None:
    """Nor Jev (v3.0.0 step 2, P9); `pipeline` and `modelq` went with the local model (ADR 0031),
    and step 4's dispatch module is its own; the import-linter contract keeps `classifier` from
    importing `jev` indirectly. `systemone` itself imports neither the classifier nor any
    transport any more (v3.0.0, P9's cycle can't form)."""
    for mod in ("ecf_server.classifier", "ecf_server.actor"):
        spec = importlib.util.find_spec(mod)
        assert spec is not None and spec.origin is not None
        with open(spec.origin, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom | ast.Import)
                 for a in n.names}  # fmt: skip
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert "systemone" not in names and "ecf_server.systemone" not in mods, mod
        assert "jev" not in names and "ecf_server.jev" not in mods, mod
    spec = importlib.util.find_spec("ecf_server.systemone")
    assert spec is not None and spec.origin is not None
    tree = ast.parse(Path(spec.origin).read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom | ast.Import)
             for a in n.names}  # fmt: skip
    assert not names & {"classifier", "ollama", "jev"} and "ecf_server.ollama" not in mods


# `ecf eval compare` of archived decision-model and Gemma results (R-D12).


def _file(tmp: Path, name: str, backend: str, correct: list[bool], **extra: Any) -> Path:
    cases = [CaseResult(id=f"c{i}", correct=c, fields={"category": c})
             for i, c in enumerate(correct)]  # fmt: skip
    summary: dict[str, object] = {"backend": backend, "redact": True, **extra}
    r = ResultFile(run_id=name, pair=evalrun.pair_for(backend), set_version="v",
                   created_at="t", cases=cases, summary=summary)  # fmt: skip
    path = tmp / f"{name}.json"
    path.write_text(r.model_dump_json())
    return path


def test_compare_shows_the_experiment_figures(tmp_path: Path) -> None:
    g = _file(tmp_path, "g", "gemma", [True, False] * 10,
              fraud_under={"under": 2, "of": 6})  # fmt: skip
    d = _file(tmp_path, "d", "systemone:test-decision", [True] * 20,
              fraud_under={"under": 1, "of": 6},
              classifier_latency_s={"p50": 2.5, "p95": 3.1},
              power={"ac_at_start": True, "ac_at_end": True, "paused": False},
              calibration={"category": {"n": 20, "accuracy": 1.0, "ece": 0.04,
                                        "ece_ci95": [0.01, 0.08], "ece_baseline": 0.0,
                                        "reliability": []}})  # fmt: skip
    out = CliRunner().invoke(app, ["eval", "compare", str(g), str(d)])
    assert out.exit_code == 0, out.output
    text = out.output
    assert "95% Newcombe interval" in text and "score interval" not in text  # one rule (OD-486)
    assert "confirmatory (exact McNemar, Holm over the two): end-to-end p =" in text
    assert "B  classifier systemone:test-decision" in text
    assert "B  fraud risk under-rated on 1 of 6" in text
    assert "A  fraud risk under-rated on 2 of 6" in text
    assert "B  classifier call 2.5 s median, 3.1 s p95" in text
    assert "B  calibration category: ECE 0.040 (0.010-0.080" in text


def test_compare_says_why_category_left_the_experiment_figures(tmp_path: Path) -> None:
    policy = {"deferred": ["category", "deadline_mentioned"]}  # OD-563
    g = _file(tmp_path, "g", "gemma", [True, False] * 10, label_policy=policy)
    d = _file(tmp_path, "d", "systemone:test-decision", [True] * 20, label_policy=policy)
    out = CliRunner().invoke(app, ["eval", "compare", str(g), str(d)])
    assert out.exit_code == 0, out.output
    assert ("confirmatory (exact McNemar, end-to-end only: category is deferred by the label"
            " policy, OD-563): end-to-end p =") in out.output  # fmt: skip
    assert "category p =" not in out.output


def test_compare_of_two_gemma_runs_is_unchanged(tmp_path: Path) -> None:
    a = _file(tmp_path, "a", "gemma", [True, False] * 5)
    b = _file(tmp_path, "b", "gemma", [True] * 10)
    out = CliRunner().invoke(app, ["eval", "compare", str(a), str(b)])
    assert out.exit_code == 0
    assert "score interval" not in out.output and "confirmatory" not in out.output
