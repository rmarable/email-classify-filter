"""The CLI on a set with a private half (EVAL-PRIVATE.md, "The overlay"): `ecf eval build` builds
both halves and refreshes the merged view; single-ID commands write to the card's own half;
whole-set modes cover both; `--import-fixed` checks both parts before writing either."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ecf.cli import app
from ecf.eval import fixed_labels
from ecf.eval import labels as label_file
from ecf.eval import merged as mg

SYN = Path(__file__).resolve().parent / "eval" / "synthetic"
runner = CliRunner()


@pytest.fixture
def pub(tmp_path: Path) -> Path:
    p = tmp_path / "tests" / "eval" / "synthetic"
    priv = tmp_path / "eval" / "private" / "synthetic"
    for half, ids in ((p, ("starter-bec", "starter-control")), (priv, ("inj-spanish",))):
        (half / "cases").mkdir(parents=True)
        for i in ids:
            (half / "cases" / f"{i}.md").write_bytes((SYN / "cases" / f"{i}.md").read_bytes())
    return p


def _build(pub: Path) -> str:
    r = runner.invoke(app, ["eval", "build", "--root", str(pub)])
    assert r.exit_code == 0, r.output
    return r.output


def test_build_builds_both_halves_and_refreshes(pub: Path) -> None:
    out = _build(pub)
    assert "[public set]" in out and "[private set]" in out
    assert "commit tests/eval/synthetic and eval/private/synthetic" in out
    assert mg.resolve(pub) == mg.paths(pub).merged
    assert [r["id"] for r in label_file.read(mg.resolve(pub))] == [
        "inj-spanish", "starter-bec", "starter-control"]  # fmt: skip


def test_build_refuses_an_unmarked_private_card(pub: Path) -> None:
    priv = mg.paths(pub).private
    assert priv is not None
    (priv / "cases" / "starter-control.md").write_bytes(
        (SYN / "cases" / "starter-control.md").read_bytes())  # fmt: skip
    (pub / "cases" / "starter-control.md").unlink()
    r = runner.invoke(app, ["eval", "build", "--root", str(pub)])
    assert r.exit_code == 1 and "private card without an injection marker" in r.output


def test_new_case_routes_an_injection_card_to_the_private_half(pub: Path, tmp_path: Path) -> None:
    r = runner.invoke(app, ["eval", "new-case", "inj-new", "--template", "injection", "--root",
                            str(pub)])  # fmt: skip
    assert r.exit_code == 0, r.output
    assert (tmp_path / "eval" / "private" / "synthetic" / "cases" / "inj-new.md").is_file()
    r = runner.invoke(app, ["eval", "new-case", "bec-new", "--template", "bec", "--root", str(pub)])
    assert r.exit_code == 0 and (pub / "cases" / "bec-new.md").is_file()
    r = runner.invoke(app, ["eval", "new-case", "inj-spanish", "--template", "bec", "--root",
                            str(pub)])  # fmt: skip
    assert r.exit_code != 0 and "already exists" in r.output


def test_new_case_refuses_an_injection_card_without_a_private_half(tmp_path: Path) -> None:
    root = tmp_path / "tests" / "eval" / "synthetic"
    root.mkdir(parents=True)
    r = runner.invoke(app, ["eval", "new-case", "inj-x", "--template", "injection", "--root",
                            str(root)])  # fmt: skip
    assert r.exit_code != 0 and "private set" in r.output


def test_label_status_counts_both_halves(pub: Path) -> None:
    _build(pub)
    r = runner.invoke(app, ["eval", "label", "--status", "--root", str(pub)])
    assert r.exit_code == 0, r.output
    assert "0 of 2 cases confirmed" in r.output and "0 of 1 cases confirmed" in r.output


def _page(tmp_path: Path, answers: dict[str, object]) -> Path:
    f = tmp_path / "page.json"
    f.write_text(json.dumps({"item": "phishing", "on": "2026-11-06", "answers": answers}))
    return f


def test_import_fixed_writes_each_part_to_its_own_half(pub: Path, tmp_path: Path) -> None:
    _build(pub)
    yes = {"label": True, "how": "single"}
    page = _page(tmp_path, {"starter-bec": yes, "inj-spanish": yes})
    r = runner.invoke(app, ["eval", "label", "--root", str(pub), "--import-fixed", str(page)])
    assert r.exit_code == 0, r.output
    priv = mg.paths(pub).private
    assert priv is not None
    assert [x["id"] for x in fixed_labels.read(pub)] == ["starter-bec"]
    assert [x["id"] for x in fixed_labels.read(priv)] == ["inj-spanish"]
    assert mg.resolve(pub) == mg.paths(pub).merged  # refreshed at the end


@pytest.mark.parametrize("bad", ["an unknown id", "a bad answer"])
def test_import_fixed_with_one_bad_part_writes_neither(pub: Path, tmp_path: Path, bad: str) -> None:
    _build(pub)
    yes = {"label": True, "how": "single"}
    answers: dict[str, object] = {"starter-bec": yes, "inj-spanish": yes}
    if bad == "an unknown id":
        answers["nope"] = yes
    else:
        answers["inj-spanish"] = {"label": "maybe", "how": "single"}
    r = runner.invoke(app, ["eval", "label", "--root", str(pub), "--import-fixed",
                            str(_page(tmp_path, answers))])  # fmt: skip
    assert r.exit_code != 0
    priv = mg.paths(pub).private
    assert priv is not None
    assert fixed_labels.read(pub) == [] and fixed_labels.read(priv) == []
