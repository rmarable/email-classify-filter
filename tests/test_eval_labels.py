"""Label confirmations (V1.3 step 8a; SPEC §16.1, OD-229, OD-241): `ecf eval label` writes them into
labels.jsonl, bound to the built file and the expected values; a rebuild keeps them only while the
case is unchanged."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ecf.cli import app
from ecf.errors import NotFoundError
from ecf.eval import labels
from ecf.eval.builder import build_all
from ecf.eval.results import CaseResult, ResultFile

SYNTHETIC = Path(__file__).parent / "eval" / "synthetic"
DAY = date(2026, 10, 1)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "synthetic"
    (r / "cases").mkdir(parents=True)
    for name in ("starter-bec.md", "starter-control.md"):
        shutil.copy(SYNTHETIC / "cases" / name, r / "cases" / name)
    assert not build_all(r).findings
    return r


def test_confirming_binds_the_file_and_the_expected_values(root: Path) -> None:
    assert labels.counts(root) == {"cases": 2, "confirmed": 0}
    row = labels.confirm(root, "starter-bec", DAY)
    assert row["confirmed"]["on"] == "2026-10-01" and labels.is_confirmed(row)
    assert [r["id"] for r in labels.pending(root)] == ["starter-control"]
    with pytest.raises(NotFoundError):
        labels.confirm(root, "nope", DAY)


def test_a_rebuild_keeps_confirmations_of_unchanged_cases(root: Path) -> None:
    labels.confirm(root, "starter-bec", DAY)
    labels.confirm(root, "starter-control", DAY)
    assert not build_all(root).findings
    assert labels.counts(root) == {"cases": 2, "confirmed": 2}


def test_editing_the_expected_values_undoes_the_confirmation(root: Path) -> None:
    labels.confirm(root, "starter-bec", DAY)
    card = root / "cases" / "starter-bec.md"
    card.write_text(card.read_text().replace("fraud_risk: high", "fraud_risk: medium"))
    assert not build_all(root).findings
    assert [r["id"] for r in labels.pending(root)] == ["starter-bec", "starter-control"]


def test_editing_the_message_undoes_the_confirmation(root: Path) -> None:
    labels.confirm(root, "starter-control", DAY)
    card = root / "cases" / "starter-control.md"
    card.write_text(card.read_text() + "\nOne more line.\n")
    assert not build_all(root).findings
    assert labels.counts(root)["confirmed"] == 0


def test_a_hand_edited_confirmation_does_not_count(root: Path) -> None:
    rows = labels.read(root)
    rows[0]["confirmed"] = {"sha256": "0" * 64, "expected": "0" * 64, "on": "2026-10-01"}
    labels.write(root, rows)
    assert labels.counts(root)["confirmed"] == 0


def test_the_cli_counts_and_needs_a_terminal(root: Path) -> None:
    r = CliRunner().invoke(app, ["eval", "label", "--root", str(root), "--status"])
    assert r.exit_code == 0 and "0 of 2 cases confirmed" in r.output
    r = CliRunner().invoke(app, ["eval", "label", "--root", str(root)])
    assert r.exit_code != 0  # no terminal under the test runner: nothing is confirmed silently
    assert labels.counts(root)["confirmed"] == 0


def test_flagged_cases_show_their_note_and_can_be_labelled_alone(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    card = root / "cases" / "starter-control.md"
    card.write_text(
        card.read_text().replace("\nauthor:", "\nreview: is a flag enough here?\nauthor:", 1)
    )
    assert not build_all(root).findings
    status = CliRunner().invoke(app, ["eval", "label", "--root", str(root), "--status"])
    assert "1 pending cases flagged for your judgement (--show-flags)" in status.output
    monkeypatch.setattr("ecf.prompts.require_terminal", lambda: None)
    r = CliRunner().invoke(app, ["eval", "label", "--root", str(root), "--show-flags"], input="y\n")
    assert r.exit_code == 0, r.output
    assert "== starter-control" in r.output and "== starter-bec" not in r.output
    assert "FLAG, needs your judgement: is a flag enough here?" in r.output
    assert "y = you agree with the expected values below as written" in r.output
    assert [x["id"] for x in labels.pending(root)] == ["starter-bec"]


def test_labelling_shows_what_the_model_returned(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = CaseResult(id="starter-bec", correct=False,
                      got={"category": "invoice", "rule": "fraud_guard"})  # fmt: skip
    result = ResultFile(run_id="abcdef123456", pair="p", set_version="v",
                        created_at="2026-10-01T12:00:00Z", cases=[case])  # fmt: skip
    path = tmp_path / "r.json"
    path.write_text(result.model_dump_json())
    monkeypatch.setattr("ecf.prompts.require_terminal", lambda: None)
    r = CliRunner().invoke(app, ["eval", "label", "starter-bec", "--root", str(root),
                                 "--results", str(path)], input="n\n")  # fmt: skip
    assert r.exit_code == 0, r.output
    assert "model answers from eval abcdef12 (2026-10-01)" in r.output
    assert "model returned: category invoice (expected vendor_change_request)" in r.output


def test_the_committed_set_is_consistent() -> None:
    rows = labels.read(SYNTHETIC)
    assert rows and all(r["sha256"] and r["expected"] for r in rows)
    for r in rows:
        if "confirmed" in r:
            assert labels.is_confirmed(r), f"{r['id']}: stale confirmation"
