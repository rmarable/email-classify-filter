"""`ecf eval agree` (eval generate plan, Judges; OD-547): judges set the labels where they agree,
the rest waits for the operator; judges' confirmations count only on a generated set. With the
fake generator and grader of test_eval_generate; the judges' answers are edited per test."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ecf.cli import app
from ecf.errors import InvalidInputError
from ecf.eval import agree as ag
from ecf.eval import generate as gen
from ecf.eval import grade as grading
from ecf.eval import labels as label_file
from ecf.eval import report as rep
from ecf.eval import tools
from ecf.eval.cards import load_cards
from ecf.eval.results import load_result
from tests.test_eval_generate import LABELS, fake_tool, run

ON = date(2026, 10, 20)


def _judge(tmp_path: Path, root: Path, model: str,
           edits: dict[str, dict[str, Any]] | None = None) -> Path:  # fmt: skip
    """A grade file by `model`, with some cases' answers changed (`edits`: case -> fields;
    an empty dict empties the case, as a refusal does)."""
    (tmp_path / model).mkdir(exist_ok=True)
    tool, environ = fake_tool(tmp_path / model, {})
    path, _c, _d = grading.Grading(root, tools.CliAdapter(tool, model, environ)).run(resume=False)
    assert path is not None
    doc = json.loads(path.read_text())
    for c in doc["cases"]:
        if c["id"] in (edits or {}):
            change = (edits or {})[c["id"]]
            c["got"] = {} if change == {} else c["got"] | change
    path.write_text(json.dumps(doc))
    return path


def _ids(root: Path) -> list[str]:
    return [r["id"] for r in label_file.read(root)]


def test_agreed_fields_become_the_judges_labels(tmp_path: Path) -> None:
    _s, root = run(tmp_path, mode="dev")
    _judge(tmp_path, root, "judge-a")
    _judge(tmp_path, root, "judge-b")
    o = ag.run(root, ON)
    assert (o.agreed, o.disputed, o.whole) == (20, 0, 0)
    assert label_file.by_source(root) == {"models": 20, "operator": 0}
    card = next(iter(load_cards(root / "cases")))
    assert card.expected.labels == {k: v for k, v in LABELS.items() if k != "priority"}
    grades = [load_result(p) for p in sorted((root / "grades").glob("*.json"))]
    text = "\n".join(rep.report(root, grades))
    assert "labels: 20 emails set by the judges, 0 by you" in text
    assert "judge agreement per field" in text
    gen.verify(root)


def test_disagreements_wait_for_the_operator_with_the_judges_values(tmp_path: Path) -> None:
    _s, root = run(tmp_path, mode="dev")
    a, b = _ids(root)[:2]
    _judge(tmp_path, root, "judge-a")
    _judge(tmp_path, root, "judge-b", {a: {"phishing": True}, b: {"category": "invoice"}})
    o = ag.run(root, ON)
    assert (o.agreed, o.disputed) == (18, 2) and o.fields == {"phishing": 1, "category": 1}
    open_ = {d["id"]: d for d in ag.open_disputes(root)}
    assert open_[a]["fields"] == ["phishing"]
    assert open_[a]["judges"]["phishing"] == {"fake:judge-a": False, "fake:judge-b": True}
    assert label_file.counts(root)["confirmed"] == 18
    # the agreed fields are on the card already; the disputed one isn't
    card = next(c for c in load_cards(root / "cases") if c.id == a)
    assert "phishing" not in card.expected.labels and card.expected.labels["category"] == "other"


def test_a_refused_email_goes_to_the_operator_whole(tmp_path: Path) -> None:
    _s, root = run(tmp_path, mode="dev")
    a = _ids(root)[0]
    _judge(tmp_path, root, "judge-a")
    _judge(tmp_path, root, "judge-b", {a: {}})
    o = ag.run(root, ON)
    assert o.whole == 1
    d = next(x for x in ag.open_disputes(root) if x["id"] == a)
    assert d["whole"] and "phishing" in d["fields"] and "priority" not in d["fields"]


def test_three_judges_majority_only_for_category_and_sender_type(tmp_path: Path) -> None:
    _s, root = run(tmp_path, mode="dev")
    a, b = _ids(root)[:2]
    _judge(tmp_path, root, "judge-a")
    _judge(tmp_path, root, "judge-b", {a: {"category": "invoice"}, b: {"phishing": True}})
    _judge(tmp_path, root, "judge-c")
    o = ag.run(root, ON)
    assert o.fields == {"phishing": 1}  # 2-1 on a boolean is a dispute; on category, a majority
    card = next(c for c in load_cards(root / "cases") if c.id == a)
    assert card.expected.labels["category"] == "other"


def test_a_second_run_never_overwrites_a_confirmed_case(tmp_path: Path) -> None:
    _s, root = run(tmp_path, mode="dev")
    _judge(tmp_path, root, "judge-a")
    _judge(tmp_path, root, "judge-b")
    ag.run(root, ON)
    o = ag.run(root, ON)
    assert (o.agreed, o.skipped) == (0, 20)


def test_judges_confirmations_never_count_outside_a_generated_set(tmp_path: Path) -> None:
    _s, root = run(tmp_path, mode="dev")
    _judge(tmp_path, root, "judge-a")
    _judge(tmp_path, root, "judge-b")
    ag.run(root, ON)
    rows = label_file.read(root)
    assert all(label_file.is_confirmed(r, models_ok=True) for r in rows)
    assert not any(label_file.is_confirmed(r) for r in rows)  # e.g. copied into the synthetic set
    fresh = [{k: v for k, v in r.items() if k != "confirmed"} for r in rows]
    rebuilt = label_file.carry_over(rows, fresh)  # `ecf eval build` outside a generated set
    assert not any("confirmed" in r for r in rebuilt)


def test_two_grades_by_one_model_or_one_judge_alone_are_refused(tmp_path: Path) -> None:
    _s, root = run(tmp_path, mode="dev")
    _judge(tmp_path, root, "judge-a")
    with pytest.raises(InvalidInputError, match="at least two judges"):
        ag.run(root, ON)
    path = next((root / "grades").glob("*.json"))
    (root / "grades" / "copy.json").write_text(path.read_text())
    with pytest.raises(InvalidInputError, match="a second grade by judge-a"):
        ag.run(root, ON)


def test_the_generator_never_judges_its_own_set(tmp_path: Path) -> None:
    _s, root = run(tmp_path, mode="dev")
    doc = gen.read_manifest(root)
    with pytest.raises(InvalidInputError, match="never grades its own set"):
        ag.check_roles(doc, [("fake", "fake-model-1"), ("fake", "judge-b")])


def test_label_refuses_disputed_emails_and_points_to_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _s, root = run(tmp_path, mode="dev")
    _judge(tmp_path, root, "judge-a")
    _judge(tmp_path, root, "judge-b", {_ids(root)[0]: {"phishing": True}})
    ag.run(root, ON)
    monkeypatch.setattr("ecf.prompts.require_terminal", lambda: None)
    monkeypatch.setenv("ECF_HOME", str(tmp_path / "home"))
    r = CliRunner().invoke(app, ["eval", "label", "--root", str(root)])
    assert isinstance(r.exception, InvalidInputError)
    assert "ecf eval settle" in r.exception.detail


def test_kappa() -> None:
    assert ag.kappa([1, 1, 0, 0], [1, 1, 0, 0]) == 1.0
    assert ag.kappa([1, 0, 1, 0], [0, 1, 0, 1]) == -1.0
    assert ag.kappa([1, 1], [1, 1]) is None  # no variation: undefined


def _one_dispute(tmp_path: Path) -> tuple[Path, str]:
    _s, root = run(tmp_path, mode="dev")
    a = _ids(root)[0]
    _judge(tmp_path, root, "judge-a")
    _judge(tmp_path, root, "judge-b", {a: {"phishing": True}})
    ag.run(root, ON)
    return root, a


def test_the_phone_page_export_is_blind_and_its_answers_settle(tmp_path: Path) -> None:
    from ecf.eval import settle as st  # noqa: PLC0415

    root, a = _one_dispute(tmp_path)
    data, key = st.export(root, tmp_path / "page")
    page = data.read_text()
    assert "dev-" not in page and "judge-a" not in page and "judge-b" not in page
    tokens = json.loads(key.read_text())
    (t,) = tokens
    assert tokens[t] == a
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({t: {}}))
    assert st.apply_page(root, answers, key, ON) == (0, 1)  # not answered yet: left for later
    answers.write_text(json.dumps({t: {"phishing": False}}))
    assert st.apply_page(root, answers, key, ON) == (1, 0)
    assert label_file.by_source(root) == {"models": 19, "operator": 1}
    assert ag.open_disputes(root) == []
    card = next(c for c in load_cards(root / "cases") if c.id == a)
    assert card.expected.labels["phishing"] is False
    gen.verify(root)


def test_settle_refuses_bad_answers(tmp_path: Path) -> None:
    from ecf.eval import settle as st  # noqa: PLC0415

    root, a = _one_dispute(tmp_path)
    with pytest.raises(InvalidInputError, match="isn't a phishing value"):
        st.apply(root, {a: {"phishing": "maybe"}}, ON)
    with pytest.raises(InvalidInputError, match="isn't a disputed field"):
        st.apply(root, {a: {"phishing": True, "category": "invoice"}}, ON)
    with pytest.raises(InvalidInputError, match="no open dispute"):
        st.apply(root, {_ids(root)[1]: {"phishing": True}}, ON)


def test_settling_in_the_terminal_shows_the_judges_only_after_you_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _a = _one_dispute(tmp_path)
    monkeypatch.setattr("ecf.prompts.require_terminal", lambda: None)
    r = CliRunner().invoke(app, ["eval", "settle", "--root", str(root)], input="yes\ny\n")
    assert r.exit_code == 0, r.output
    assert r.output.index("judges said for phishing") > r.output.index("phishing [yes / no")
    assert "dev-" not in r.output and "settled 1 emails" in r.output
    assert label_file.by_source(root)["operator"] == 1


def test_the_report_never_measures_judges_on_their_own_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, a = _one_dispute(tmp_path)
    from ecf.eval import settle as st  # noqa: PLC0415

    st.apply(root, {a: {"phishing": False}}, ON)
    doc = json.loads((root / "manifest.json").read_text())
    doc["mode"] = "prod"  # the judges' figures as PROD shows them
    (root / "manifest.json").write_text(json.dumps(doc))
    grades = [load_result(p) for p in sorted((root / "grades").glob("*.json"))]
    from ecf.eval import models  # noqa: PLC0415

    fams = {"fake-model-1": "openai", "judge-a": "mistral", "judge-b": "google"}

    def family(_provider: str, model: str) -> str:
        return fams[model]

    monkeypatch.setattr(models, "family", family)
    text = "\n".join(rep.report(root, grades))
    assert "against the labels you settled (1 emails; never the labels the judges set)" in text
    assert "report-only (judges' kappa under 0.6):" in text
    assert "judges' labels contradicting the assignment" in text
