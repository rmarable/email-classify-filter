"""`ecf eval grade`, `expect` and `report` on a generated set (eval generate plan: Grading,
Report, decision 8; P2, P8, P13, P22, P25), with the fake generator of test_eval_generate; and
the commands' edges (providers not built yet, the shipped mix to edit)."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ecf.cli import app
from ecf.errors import InvalidInputError
from ecf.eval import expect, providers, review, tools
from ecf.eval import generate as gen
from ecf.eval import grade as grading
from ecf.eval import labels as label_file
from ecf.eval import report as rep
from ecf.eval.builder import build_all
from ecf.eval.cards import load_cards
from ecf.eval.results import CaseResult, ResultFile, load_result
from ecf_server import config, evalrun, ruletest
from ecf_server.clock import FakeClock
from tests.test_eval_generate import LABELS, calls, fake_tool, run

FRAUD = LABELS | {"category": "invoice", "payment_related": True, "fraud_risk": "high",
                  "bank_detail_change": True, "requires_action": True}  # fmt: skip


def _grade(tmp_path: Path, root: Path, scenario: dict[str, Any] | None = None,
           resume: bool = False) -> tuple[Path | None, dict[str, int], str]:  # fmt: skip
    tool, environ = fake_tool(tmp_path, scenario or {})
    adapter = tools.CliAdapter(tool, "fake-grader-1", environ)
    return grading.Grading(root, adapter).run(resume=resume)


def _label(root: Path, by_id: dict[str, dict[str, Any]] | None = None) -> None:
    """Your blind labels, as `ecf eval label --review all --add` writes them, then confirmed."""
    for card in load_cards(root / "cases"):
        review.set_labels(root / "cases" / f"{card.id}.md", (by_id or {}).get(card.id, LABELS))
    assert not build_all(root).findings
    for row in label_file.read(root):
        label_file.confirm(root, row["id"], date(2026, 10, 20))


def _result(root: Path, got: dict[str, Any]) -> ResultFile:
    rows = label_file.read(root)
    cases = [CaseResult(id=r["id"], correct=True, got=got) for r in rows]
    return ResultFile(run_id="r" * 16, pair="claude/claude", set_version="x", created_at="t",
                      cases=cases)  # fmt: skip


def test_a_grade_is_a_result_file_with_the_graders_answers_and_nothing_scored(
    tmp_path: Path,
) -> None:
    _summary, root = run(tmp_path, mode="prod")
    path, counts, _detail = _grade(tmp_path, root)
    assert path is not None and counts == {"graded": 20}
    g = load_result(path)
    assert grading.is_grade(g) and g.pair == "grader:fake-grader-1"
    assert len(g.cases) == 20 and all(c.got == LABELS and not c.scored for c in g.cases)
    grading.check_grade(g, gen.verify(root))
    graded = [c for c in calls(tmp_path) if not c["seeds"]][-20:]
    assert all(c["cwd_files"] == ["schema.json"] for c in graded)
    with pytest.raises(InvalidInputError, match="already graded"):
        _grade(tmp_path, root)


def test_a_refused_grade_is_recorded_and_left_out(tmp_path: Path) -> None:
    _summary, root = run(tmp_path, mode="prod")
    path, counts, _detail = _grade(tmp_path, root, {"grade_refuse": True})
    assert counts == {"refused": 20} and path is not None
    assert load_result(path).cases[0].got == {}


def test_a_stopped_grading_resumes_and_asks_only_the_rest(tmp_path: Path) -> None:
    _summary, root = run(tmp_path, mode="prod")
    path, _counts, detail = _grade(tmp_path, root, {"limit": True})
    assert path is None and "--resume" in detail
    path, counts, _detail = _grade(tmp_path, root, resume=True)
    assert path is not None and counts == {"graded": 20}


def test_the_report_states_the_graders_accuracy_against_your_labels(tmp_path: Path) -> None:
    _summary, root = run(tmp_path, mode="prod")
    path, _c, _d = _grade(tmp_path, root)
    assert path is not None
    lines = rep.report(root, [load_result(path)])
    assert any("no confirmed labels yet" in x for x in lines)
    _label(root)
    ecf_run = _result(root, LABELS | {"fraud_risk": "low"})
    text = "\n".join(rep.report(root, [load_result(path), ecf_run]))
    assert "grader fake-grader-1 against your labels (its measured accuracy):" in text
    assert "all fields           260/260 100.0%" in text
    assert "generator's label hints against your labels:" in text
    assert "fraud_risk           0/20 0.0%" in text  # the ecf result's one wrong field
    assert "(report-only)" in text  # groups under 20 cases
    assert "rule and safety: not scored" in text
    assert "Opening" not in text  # no email text
    # relabelled: the first measured figure is kept and shown beside the new one (P8)
    first = next(c.id for c in load_cards(root / "cases"))
    review.set_labels(root / "cases" / f"{first}.md", LABELS | {"priority": "high"})
    assert not build_all(root).findings
    label_file.confirm(root, first, date(2026, 10, 21))
    text = "\n".join(rep.report(root, [load_result(path)]))
    assert "measured on your labels as first reported" in text and "260/260" in text


def test_a_dev_report_shows_plumbing_only(tmp_path: Path) -> None:
    _summary, root = run(tmp_path)  # dev
    lines = rep.report(root, [])
    assert lines[0] == rep.DEV_BANNER
    assert not any("against your labels" in x for x in lines)


def test_a_grade_from_another_set_is_refused(tmp_path: Path) -> None:
    _summary, root = run(tmp_path, mode="prod")
    path, _c, _d = _grade(tmp_path, root)
    assert path is not None
    g = load_result(path)
    other = g.model_copy(update={"summary": {**(g.summary or {}), "cases_sha256": {}}})
    with pytest.raises(InvalidInputError, match="another set"):
        grading.check_grade(other, gen.verify(root))


def test_expect_fills_rule_and_safety_from_the_rules_on_your_labels(
    conn: sqlite3.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    _summary, root = run(tmp_path, mode="prod")
    ids = [c.id for c in load_cards(root / "cases")]
    _label(root, {ids[0]: FRAUD})
    rows = ruletest.outcomes(clock, config.current_rules(conn), root.resolve())
    cases: list[dict[str, Any]] = [
        {"id": r["id"], "rule": r["rule"], "error": r.get("error"),
         "safety": evalrun.expected_safety(r["labels"], r["rule"], r["actions"])}
        for r in rows]  # fmt: skip
    by_id = {c["id"]: c for c in cases}
    assert by_id[ids[0]]["safety"]["must_not_hide"] is True
    counts = expect.apply(root, cases)
    assert counts["filled"] == 20 and counts["unlabelled"] == 0
    card = {c.id: c for c in load_cards(root / "cases")}[ids[0]]
    assert card.expected.rule == by_id[ids[0]]["rule"]
    assert card.expected.safety.must_not_hide is True
    assert card.expected.labels == FRAUD
    gen.verify(root)  # messages and facts unchanged
    assert label_file.counts(root)["confirmed"] == 0  # you confirm them again
    assert expect.apply(root, cases)["unchanged"] == 20


def test_expect_leaves_an_unlabelled_card_alone(clock: FakeClock, conn: sqlite3.Connection,
                                                tmp_path: Path) -> None:  # fmt: skip
    _summary, root = run(tmp_path, mode="prod")
    rows = ruletest.outcomes(clock, config.current_rules(conn), root.resolve())
    cases = [{"id": r["id"], "rule": r["rule"], "safety": {"must_escalate": False,
                                                            "must_not_hide": False}}
             for r in rows]  # fmt: skip
    assert expect.apply(root, cases) == {"filled": 0, "unchanged": 0, "unlabelled": 20,
                                         "errors": 0}  # fmt: skip


def test_providers_not_built_yet_are_refused_with_their_step() -> None:
    with pytest.raises(InvalidInputError, match=r"claude generator isn't built yet \(build step 2"):
        providers.resolve(None, "generator", "dev")
    with pytest.raises(InvalidInputError, match="build step 3"):
        providers.resolve(None, "grader", "prod")
    with pytest.raises(InvalidInputError, match="no generator provider 'gemini'"):
        providers.resolve("gemini:x", "generator", "prod")


def test_prod_refuses_claude_as_generator_or_grader(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_argv(_model: str, _schema: Path, _out: Path) -> list[str]:
        return []

    def no_reply(_ran: tools.Ran, _out: Path) -> tools.Reply:
        return tools.Reply("ok")

    tool = tools.CliTool("claude", "claude", no_argv, no_reply)
    monkeypatch.setitem(providers.BUILT, "claude", lambda m: tools.CliAdapter(tool, m))
    with pytest.raises(InvalidInputError, match="can't be Claude"):
        providers.resolve("claude:claude-haiku-5-5", "generator", "prod")
    adapter = providers.resolve(None, "generator", "dev")
    assert adapter.model == "claude-haiku-5-5"


def test_write_mix_writes_the_shipped_mix_to_edit(tmp_path: Path) -> None:
    out = tmp_path / "mix.yaml"
    r = CliRunner().invoke(app, ["eval", "generate", "--write-mix", str(out)])
    assert r.exit_code == 0, r.output
    assert "phishing" in out.read_text() and "other-ic3" in out.read_text()
    r = CliRunner().invoke(app, ["eval", "generate", "--root", str(tmp_path / "s"), "--yes"])
    assert r.exit_code != 0 and "isn't built yet" in (r.output + str(r.exception))
    assert not (tmp_path / "s").exists()  # refused before the folder is made
