"""`ecf eval compare A B --root <set>`: the two results paired again on the labels you settled
only (J12; plan state-archive/eval-compare-settled draft 2, Phase R P1-P14). With the judged DEV
sets of test_eval_expect_judged."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ecf.cli import app
from ecf.errors import InvalidInputError
from ecf.eval import agree as ag
from ecf.eval import fixed_labels, models, report
from ecf.eval import grade as grading
from ecf.eval import labels as label_file
from ecf.eval import settle as st
from ecf.eval.results import CaseResult, ResultFile
from ecf_server import evalrun
from tests.test_eval_agree import _ids  # pyright: ignore[reportPrivateUsage]
from tests.test_eval_expect_judged import ON, Answers, Make, _fresh  # pyright: ignore[reportPrivateUsage]

FIELDS = ("requires_action", "phishing", "fraud_risk")
_BUILT: dict[str, Path] = {}


@pytest.fixture(autouse=True)
def writer_is_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake writer of the DEV set stands in for Claude, as a DEV set's writer is."""

    def family(_provider: str, _model: str) -> str:
        return "anthropic"

    monkeypatch.setattr(models, "family", family)


@pytest.fixture
def make(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Make:
    def build(a: Answers, b: Answers) -> tuple[Path, list[str]]:
        key = json.dumps([a, b], sort_keys=True)
        if key not in _BUILT:
            _BUILT[key] = _fresh(tmp_path_factory.mktemp("judged"), a, b)
        copy = tmp_path / "copy"
        shutil.copytree(_BUILT[key], copy)
        root = (copy / "set").resolve()
        return root, _ids(root)

    return build


def _settled(make: Make) -> tuple[Path, list[str]]:
    """Judges disagree on case 0's requires_action and case 1's phishing; you settle both."""
    root, ids = make({}, {"0": {"requires_action": True}, "1": {"phishing": True}})
    assert {d["id"] for d in ag.open_disputes(root)} == {ids[0], ids[1]}
    st.apply(root, {ids[0]: {"requires_action": True}, ids[1]: {"phishing": False}}, ON)
    return root, ids


def _result(root: Path, ids: list[str], name: str, wrong: dict[str, set[str]] | None = None,
            version: str | None = None) -> ResultFile:  # fmt: skip
    """Every field right except those in `wrong` (case id -> fields)."""
    cases: list[CaseResult] = []
    for i in ids:
        bad = (wrong or {}).get(i, set())
        fields = {f: f not in bad for f in (*FIELDS, "rule", "safety")}
        cases.append(CaseResult(id=i, correct=fields["phishing"], fields=fields))
    return ResultFile(run_id=name * 32, pair="claude/claude", created_at="t", cases=cases,
                      set_version=version or label_file.set_version(root))  # fmt: skip


def _files(tmp: Path, *results: ResultFile) -> list[str]:
    out: list[str] = []
    for n, r in enumerate(results):
        path = tmp / f"r{n}.json"
        path.write_text(r.model_dump_json())
        out.append(str(path))
    return out


def test_the_block_counts_only_the_labels_you_settled(make: Make) -> None:
    root, ids = _settled(make)
    a = _result(root, ids, "a")
    b = _result(root, ids, "b", {ids[0]: {"requires_action"}, ids[2]: {"requires_action"}})
    report.check_settled_root(root, a, b)
    text = "\n".join(report.settled_lines(root, a, b))
    assert text.startswith(f"NOT A MEASUREMENT: result {'a' * 8}")  # Claude wrote the DEV set
    assert "Not a gate figure" in text
    n = len(ids) * len(FIELDS)
    assert f"field labels compared: {n}; judges agreed {n - 2}, you settled 2" in text
    assert "requires_action    n=1    B-only 0   A-only 1   too few to test" in text
    assert "phishing           n=1    B-only 0   A-only 0   too few to test" in text
    rows = [line.split()[0] for line in text.splitlines() if line.startswith("  ")]
    assert "rule" not in rows and "safety" not in rows  # derived, never set by anyone
    assert "with at least one label of yours: 2 (1 where you set a safety field" in text
    assert "non-inferior" not in text  # never a verdict here (P2)


def test_compare_prints_the_block_only_with_root(make: Make, tmp_path: Path) -> None:
    root, ids = _settled(make)
    fa, fb = _files(tmp_path, _result(root, ids, "a"), _result(root, ids, "b"))
    r = CliRunner()
    plain = r.invoke(app, ["eval", "compare", fa, fb])
    assert plain.exit_code == 0 and "settled" not in plain.output
    with_root = r.invoke(app, ["eval", "compare", fa, fb, "--root", str(root)])
    assert with_root.exit_code == 0, with_root.output
    assert with_root.output.startswith(plain.output)  # today's output first, unchanged
    assert "On the labels you settled only" in with_root.output


def test_the_subset_narrows_the_block_too(make: Make, tmp_path: Path) -> None:
    root, ids = _settled(make)
    b = _result(root, ids, "b", {ids[0]: {"requires_action"}})
    text = "\n".join(report.settled_lines(root, _result(root, ids, "a"), b,
                                          frozenset(ids[1:])))  # fmt: skip
    assert "you settled 1 " in text and "requires_action" not in text.split("label of yours")[0]
    assert "with at least one label of yours: 1 " in text


def test_nothing_settled_says_so(make: Make) -> None:
    root, ids = make({}, {})
    text = "\n".join(report.settled_lines(root, _result(root, ids, "a"), _result(root, ids, "b")))
    assert "no labels you settled in both results" in text
    assert "end to end: no emails with a label of yours" in text


@pytest.mark.parametrize("how", ["stale", "foreign", "fixed", "not_generated"])
def test_root_is_refused_unless_both_results_match_the_sets_labels(
    make: Make, tmp_path: Path, how: str
) -> None:
    root, ids = _settled(make)
    a, b = _result(root, ids, "a"), _result(root, ids, "b")
    if how == "stale":  # A and B match each other, not the set
        a, b = (_result(root, ids, x, version="0" * 16) for x in "ab")
        match = "labels changed since result"
    elif how == "foreign":
        b = _result(root, [*ids, "elsewhere-001"], "b")
        match = "cases this set doesn't"
    elif how == "fixed":
        (root / fixed_labels.FILE).write_text("")
        match = fixed_labels.FILE
    else:
        root = tmp_path / "plain"
        root.mkdir()
        match = "isn't a generated set"
    with pytest.raises(InvalidInputError, match=match):
        report.check_settled_root(root, a, b)
    fa, fb = _files(tmp_path, a, b)
    out = CliRunner().invoke(app, ["eval", "compare", fa, fb, "--root", str(root)])
    assert out.exit_code != 0 and out.output == ""  # refused before any output


def test_one_set_version_everywhere(tmp_path: Path) -> None:
    (tmp_path / "labels.jsonl").write_text('{"id": "x"}\n')
    first = label_file.set_version(tmp_path)
    assert first == evalrun.set_version(tmp_path) == grading.set_version(tmp_path)
    (tmp_path / fixed_labels.FILE).write_text('{"id": "x"}\n')
    second = label_file.set_version(tmp_path)
    assert second != first  # grades now count fixed_labels.jsonl too, as the service does
    assert second == evalrun.set_version(tmp_path) == grading.set_version(tmp_path)


def test_who_set_each_label() -> None:
    judged: dict[str, Any] = {label_file.LABELLED_BY: {"judges": ["x"], "settled": ["phishing"]}}
    every = {"phishing", "requires_action"}
    assert label_file.yours(judged, every, "operator") == {"phishing"}
    assert label_file.yours({}, every, label_file.MODELS) == set()  # an older judges' row
    assert label_file.yours({}, every, "operator") == every  # a row you labelled yourself


def test_holm_runs_only_over_fields_that_can_reach_it() -> None:
    six = [(True, False)] * 6 + [(True, True)] * 4
    five = [(True, False)] * 5
    lines = report._settled_fields({"requires_action": six, "phishing": five})  # pyright: ignore[reportPrivateUsage]
    assert any(line.startswith("  requires_action    n=10") and "Holm p" in line for line in lines)
    assert any(line.startswith("  phishing") and "too few to test" in line for line in lines)
