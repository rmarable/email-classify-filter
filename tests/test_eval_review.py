"""Blind label review (`ecf eval label --review`; SPEC §16.1; classifier plan 0.2): answers come
from the email alone, are logged in reviews.jsonl, move a card's labels only when the operator
keeps their answer, and confirm the card once its other expected values are checked."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ecf import cli_card_review as ui
from ecf.cli import app
from ecf.errors import InvalidInputError
from ecf.eval import labels
from ecf.eval import review as rv
from ecf.eval.builder import build_all
from ecf.eval.cards import parse_card
from ecf.schema import load_schema

SYNTHETIC = Path(__file__).parent / "eval" / "synthetic"
DAY = date(2026, 10, 9)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "synthetic"
    (r / "cases").mkdir(parents=True)
    for name in ("starter-bec.md", "starter-control.md"):
        shutil.copy(SYNTHETIC / "cases" / name, r / "cases" / name)
    assert not build_all(r).findings
    return r


def run(root: Path, fields: str, answers: list[str], **kw: Any) -> tuple[list[str], str]:
    """Review with scripted answers, cards in file order; returns the summary and the screen."""
    it: Iterator[str] = iter(answers)
    out: list[str] = []
    lines = ui.review(root, fields, read=lambda _p: next(it), write=out.append, today=DAY,
                      shuffle=lambda _x: None, **kw)  # fmt: skip
    assert next(it, None) is None, "not every scripted answer was asked for"
    return lines, "".join(out)


def card_labels(root: Path, case_id: str) -> dict[str, Any]:
    path = root / "cases" / f"{case_id}.md"
    return parse_card(path.read_text(encoding="utf-8")).expected.labels


def test_the_card_is_shown_without_its_id_notes_or_labels(root: Path) -> None:
    _, screen = run(root, "fraud_risk", ["q"], globs=("starter-bec",))
    card = parse_card((root / "cases" / "starter-bec.md").read_text(encoding="utf-8"))
    assert "Subject:" in screen and card.subject in screen
    for hidden in (card.id, card.title, card.threat, "expected", "fraud_guard"):
        assert hidden not in screen
    assert rv.read_log(root) == []


def test_a_matching_answer_confirms_and_the_pending_card_is_confirmed(root: Path) -> None:
    lines, screen = run(root, "fraud_risk", ["", "high", "y"], globs=("starter-bec",))
    [entry] = rv.read_log(root)
    assert entry["outcome"] == rv.CONFIRMED and entry["before"] == entry["final"] == "high"
    assert "This was starter-bec" in screen and "you said" not in screen
    assert labels.is_confirmed(labels.read(root)[0])
    assert "  fraud_risk: confirmed 1" in lines


def test_keeping_your_answer_moves_the_label_and_rebuilds(root: Path) -> None:
    run(root, "fraud_risk", ["", "3", "y", "y"], globs=("starter-bec",))  # 3 = medium
    assert card_labels(root, "starter-bec")["fraud_risk"] == "medium"
    [entry] = rv.read_log(root)
    assert (entry["outcome"], entry["before"], entry["final"]) == (rv.MOVED, "high", "medium")
    row = next(r for r in labels.read(root) if r["id"] == "starter-bec")
    assert row["expected"]["labels"]["fraud_risk"] == "medium" and labels.is_confirmed(row)


def test_keeping_the_cards_label_changes_nothing(root: Path) -> None:
    before = (root / "cases" / "starter-bec.md").read_text(encoding="utf-8")
    _, screen = run(root, "fraud_risk", ["", "medium", "c", "n"], globs=("starter-bec",))
    assert "fraud_risk: you said medium; the card says high." in screen
    assert (root / "cases" / "starter-bec.md").read_text(encoding="utf-8") == before
    assert rv.read_log(root)[0]["outcome"] == rv.KEPT
    assert not labels.is_confirmed(labels.read(root)[0])  # n: left pending


def test_a_sitting_resumes_and_redo_asks_again(root: Path) -> None:
    run(root, "fraud_risk", ["", "high", "y", "", "none", "y"])
    _, screen = run(root, "fraud_risk", [])
    assert "0 card(s) to review" in screen
    run(root, "fraud_risk", ["s", "s"], redo=True)


def test_quitting_mid_card_saves_nothing_for_it(root: Path) -> None:
    run(root, "category,fraud_risk", ["", "vendor_change_request", "q"])
    assert rv.read_log(root) == []


def test_add_asks_unlabelled_fields_and_logs_them_apart(root: Path) -> None:
    run(root, "fraud_risk,sender_type", ["", "-", "none", "y"], add=True,
        globs=("starter-control",))  # fmt: skip
    outcomes = {x["field"]: x["outcome"] for x in rv.read_log(root)}
    assert outcomes == {"fraud_risk": rv.CONFIRMED, "sender_type": rv.UNLABELLED}
    assert "sender_type" not in card_labels(root, "starter-control")
    run(root, "sender_type", ["", "company", "y"], add=True, redo=True,
        globs=("starter-control",))  # fmt: skip
    assert card_labels(root, "starter-control")["sender_type"] == "company"
    assert rv.read_log(root)[-1]["outcome"] == rv.ADDED


def test_all_asks_each_cards_labelled_fields(root: Path) -> None:
    [task] = rv.tasks([parse_card((root / "cases" / "starter-bec.md").read_text())],
                      labels.read(root)[:1], load_schema(), None, add=False)  # fmt: skip
    assert set(task.fields) == set(card_labels(root, "starter-bec"))


def test_a_recheck_asks_again_and_reports_agreement(root: Path) -> None:
    run(root, "fraud_risk", ["", "high", "y", "", "none", "y"])
    lines, _ = run(root, "fraud_risk", ["", "medium"], recheck=1, globs=("starter-bec",))
    last = rv.read_log(root)[-1]
    assert (last["kind"], last["before"], last["answer"]) == (rv.RECHECK, "high", "medium")
    assert card_labels(root, "starter-bec")["fraud_risk"] == "high"
    assert "  fraud_risk: 0 of 1 the same" in lines
    _, screen = run(root, "fraud_risk", [], recheck=1, globs=("starter-bec",))
    assert "0 card(s) to recheck" in screen


def test_fields_are_checked_against_the_schema() -> None:
    schema = load_schema()
    assert rv.fields_for("fraud_risk, category", schema) == ("category", "fraud_risk")
    assert rv.fields_for("all", schema) is None
    with pytest.raises(InvalidInputError, match="not a schema field: nope"):
        rv.fields_for("nope", schema)


def test_a_card_without_a_one_line_labels_map_is_left_for_a_hand_edit(root: Path) -> None:
    path = root / "cases" / "starter-bec.md"
    text = path.read_text(encoding="utf-8")
    line = next(x for x in text.splitlines() if x.startswith("  labels: {"))
    path.write_text(text.replace(line, "  labels:\n    fraud_risk: high"), encoding="utf-8")
    with pytest.raises(InvalidInputError, match="edit it by hand"):
        rv.set_labels(path, {"fraud_risk": "low"})


def test_the_cli_checks_its_options(root: Path) -> None:
    r = CliRunner().invoke(app, ["eval", "label", "--root", str(root), "--add"])
    assert isinstance(r.exception, InvalidInputError) and "go with --review" in str(r.exception)
    r = CliRunner().invoke(app, ["eval", "label", "--root", str(root), "--review", "fraud_risk",
                                 "--status"])  # fmt: skip
    assert isinstance(r.exception, InvalidInputError)
    assert "--review takes no case id" in str(r.exception)
    r = CliRunner().invoke(app, ["eval", "label", "--root", str(root), "--review", "fraud_risk"])
    assert r.exit_code != 0  # no terminal under the test runner
    assert rv.read_log(root) == []


def test_the_committed_review_log_is_consistent() -> None:
    for x in rv.read_log(SYNTHETIC):
        assert x["kind"] in (rv.REVIEW, rv.RECHECK)
        assert x["kind"] == rv.RECHECK or x["outcome"] in rv.OUTCOMES


def test_the_review_shows_the_labelling_texts_not_the_prompts(root: Path) -> None:
    from ecf_server import classifier  # noqa: PLC0415

    schema = load_schema()
    digest = classifier.prompt_digest(schema)
    _, screen = run(root, "sender_type", ["", "q"], add=True, globs=("starter-bec",))
    assert "Firms we don't buy from, including cold pitches" in screen
    assert "A business that is neither a vendor nor a customer" not in screen
    assert set(ui.LABEL_TEXTS["sender_type"]) == set(schema.fields["sender_type"].values)
    assert classifier.prompt_digest(load_schema()) == digest
