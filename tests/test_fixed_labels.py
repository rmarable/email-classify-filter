"""Hand-set fixed-item labels (S2; schema doc §15, operator decision 2026-10-13): kept in
fixed_labels.jsonl beside the cards, bound to each card's built message, merged into the eval's
expected values at load, and part of the set's version."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ecf.cli import app
from ecf.errors import InvalidInputError
from ecf.eval import fixed_labels, review
from ecf.eval.builder import build_all
from ecf_server import evalrun

SYNTHETIC = Path(__file__).parent / "eval" / "synthetic"
DAY = date(2026, 10, 13)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "synthetic"
    (r / "cases").mkdir(parents=True)
    for name in ("starter-bec.md", "starter-control.md"):
        shutil.copy(SYNTHETIC / "cases" / name, r / "cases" / name)
    assert not build_all(r).findings
    return r


ANSWERS = {"starter-bec": {"label": True, "how": "single"},
           "starter-control": {"label": False, "how": "bulk"}}  # fmt: skip


def test_recorded_labels_are_confirmed_and_logged(root: Path) -> None:
    assert fixed_labels.record(root, "phishing", ANSWERS, DAY) == 2
    good, stale = fixed_labels.current(root)
    assert good == {"starter-bec": {"phishing": True}, "starter-control": {"phishing": False}}
    assert stale == []
    log = [e for e in review.read_log(root) if e["kind"] == fixed_labels.KIND]
    assert {(e["id"], e["final"], e["how"], e["outcome"]) for e in log} == {
        ("starter-bec", True, "single", "added"),
        ("starter-control", False, "bulk", "added"),
    }
    # the same answers again change nothing
    assert fixed_labels.record(root, "phishing", ANSWERS, DAY) == 0


def test_a_changed_message_makes_the_label_stale(root: Path) -> None:
    fixed_labels.record(root, "phishing", ANSWERS, DAY)
    card = root / "cases" / "starter-control.md"
    card.write_text(card.read_text() + "\nOne more line.\n")
    assert not build_all(root).findings
    good, stale = fixed_labels.current(root)
    assert stale == ["starter-control"] and set(good) == {"starter-bec"}


def test_a_hand_edited_label_does_not_count(root: Path) -> None:
    fixed_labels.record(root, "phishing", ANSWERS, DAY)
    path = root / fixed_labels.FILE
    path.write_text(path.read_text().replace('"phishing": false', '"phishing": true'))
    assert fixed_labels.current(root)[1] == ["starter-control"]


@pytest.mark.parametrize(
    ("item", "answers"),
    [("category", ANSWERS), ("phishing", {"nope": {"label": True, "how": "single"}}),
     ("phishing", {"starter-bec": {"label": "yes", "how": "single"}}),
     ("phishing", {"starter-bec": {"label": True, "how": "guess"}})],
)  # fmt: skip
def test_bad_answers_write_nothing(root: Path, item: str, answers: dict[str, dict[str, object]]
                                   ) -> None:  # fmt: skip
    with pytest.raises(InvalidInputError):
        fixed_labels.record(root, item, answers, DAY)
    assert not (root / fixed_labels.FILE).exists()


def test_the_eval_reads_them_and_the_set_version_covers_them(root: Path) -> None:
    before = evalrun.set_version(root)
    cases, _ = evalrun.load(root, fraud_only=False)
    assert all("phishing" not in c.expected["labels"] for c in cases)  # unlabelled until set
    fixed_labels.record(root, "phishing", ANSWERS, DAY)
    cases, version = evalrun.load(root, fraud_only=False)
    got = {c.id: c.expected["labels"].get("phishing") for c in cases}
    assert got == {"starter-bec": True, "starter-control": False}
    assert version != before


def test_a_label_the_card_states_wins() -> None:
    exp = {"labels": {"phishing": False, "category": "invoice"}}
    assert evalrun.with_fixed_labels(exp, {"phishing": True})["labels"]["phishing"] is False
    assert evalrun.with_fixed_labels(exp, None) is exp


def test_import_from_a_review_page_file(root: Path, tmp_path: Path) -> None:
    f = tmp_path / "answers.json"
    f.write_text(json.dumps({"item": "phishing", "on": "2026-10-13", "answers": ANSWERS}))
    r = CliRunner().invoke(app, ["eval", "label", "--import-fixed", str(f), "--root", str(root)])
    assert r.exit_code == 0, r.output
    assert "2 cases recorded (1 yes)" in r.output
    r = CliRunner().invoke(app, ["eval", "label", "--status", "--root", str(root)])
    assert "2 cases with hand-set fixed-item labels" in r.output
    f.write_text("{not json")
    r = CliRunner().invoke(app, ["eval", "label", "--import-fixed", str(f), "--root", str(root)])
    assert r.exit_code != 0


def test_the_committed_set_has_every_card_labelled_for_phishing() -> None:
    good, stale = fixed_labels.current(SYNTHETIC)
    assert stale == []
    cases = {json.loads(x)["id"] for x in (SYNTHETIC / "labels.jsonl").read_text().splitlines()}
    assert set(good) == cases and all(isinstance(v["phishing"], bool) for v in good.values())
