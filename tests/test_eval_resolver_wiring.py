"""Readers resolve the logical set path; stored roots stay logical (EVAL-PRIVATE.md "The
resolver", "The stored root")."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import labels as label_file
from ecf.eval import merged as mg
from ecf_server import evalrun, ruletest

EVAL = Path(__file__).resolve().parent / "eval"


def complete_copy(name: str, dst: Path) -> Path:
    """A copy of a committed set without the cards built into `.build/` (untracked, so absent in
    CI; building them takes seconds): every remaining row's file is present, so the copy is a
    complete set that reads with no problems."""
    shutil.copytree(EVAL / name, dst, ignore=shutil.ignore_patterns(".build", ".*.tmp"))
    rows = (dst / "labels.jsonl").read_text().splitlines()
    large = {json.loads(r)["id"] for r in rows if json.loads(r)["file"].startswith(".build/")}
    for f in ("labels.jsonl", "fixed_labels.jsonl", "reviews.jsonl"):
        if (dst / f).is_file():
            kept = [
                r for r in (dst / f).read_text().splitlines() if json.loads(r)["id"] not in large
            ]
            (dst / f).write_text("".join(r + "\n" for r in kept))
    for i in large:
        (dst / "cases" / f"{i}.md").unlink()
    return dst


def _split_set(tmp_path: Path, name: str = "synthetic", n: int = 12) -> tuple[Path, Path, int]:
    pub = complete_copy(name, tmp_path / "tests" / "eval" / name)
    total = len(label_file.read(pub))
    ids = set(mg.card_ids(pub)[:n])
    priv = tmp_path / "eval" / "private" / name
    for d in ("cases", "eml"):
        (priv / d).mkdir(parents=True, exist_ok=True)
        for p in list((pub / d).glob("*")) if (pub / d).is_dir() else []:
            if p.stem in ids:
                shutil.move(p, priv / d / p.name)
    for f in ("labels.jsonl", "fixed_labels.jsonl", "reviews.jsonl"):
        if not (pub / f).is_file():
            continue
        rows = (pub / f).read_text().splitlines()
        (pub / f).write_text("".join(r + "\n" for r in rows if json.loads(r)["id"] not in ids))
        (priv / f).write_text("".join(r + "\n" for r in rows if json.loads(r)["id"] in ids))
    return pub, priv, total


def test_the_loader_and_version_read_the_merged_set(tmp_path: Path) -> None:
    pub, _, total = _split_set(tmp_path)
    with pytest.raises(InvalidInputError, match="ecf eval build"):
        evalrun.load(pub, fraud_only=False)
    mg.refresh_merged(pub)
    m = mg.resolve(pub)
    assert evalrun.set_version(pub) == label_file.set_version(m)
    assert evalrun.set_version(pub) != label_file.set_version(pub)
    rows = label_file.read(m)
    assert len(rows) == total
    cases, version = evalrun.load(pub, fraud_only=False)
    assert version == label_file.set_version(m)
    assert len(cases) == total and not evalrun.read_set(pub).problems
    assert {c.path.parent.parent for c in cases} == {m.resolve()}
    assert {c.id for c in cases} >= set(mg.card_ids(mg.paths(pub).private or pub))
    with pytest.raises(InvalidInputError, match="public set's path"):
        evalrun.load(m, fraud_only=False)  # a merged path is refused by path


def test_rules_test_reads_the_merged_set(tmp_path: Path) -> None:
    pub, _, total = _split_set(tmp_path)
    mg.refresh_merged(pub)
    cases, _missing = ruletest.load_cases(pub)
    assert len(cases) + len(_missing) == total


def test_stored_roots_are_logical(tmp_path: Path) -> None:
    pub, _, _ = _split_set(tmp_path)
    mg.refresh_merged(pub)
    assert evalrun.logical(pub) == str(pub.resolve())
    assert evalrun.logical(pub / ".." / pub.name) == str(pub.resolve())
    assert ".merged" not in evalrun.logical(pub)
