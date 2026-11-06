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


def _split_set(tmp_path: Path, name: str = "synthetic", n: int = 12) -> tuple[Path, Path, int]:
    pub = tmp_path / "tests" / "eval" / name
    shutil.copytree(EVAL / name, pub, ignore=shutil.ignore_patterns(".build", ".*.tmp"))
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
    assert len(cases) > total - 5  # the two large cards' .build files aren't in this worktree
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
