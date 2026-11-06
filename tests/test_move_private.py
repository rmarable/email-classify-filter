"""The move script (scripts/move_private_cards.py; EVAL-PRIVATE.md Step 5): which cards move, the
split it makes, and the guard on public traces of a moved ID. Runs on copies of the public sets
and on cards made here, so CI needs no private set."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import labels as label_file
from ecf.eval import merged as mg
from ecf.eval.builder import build_all
from ecf_server import evalrun

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "tests" / "eval"


def _load() -> ModuleType:
    script = ROOT / "scripts" / "move_private_cards.py"
    spec = importlib.util.spec_from_file_location("move_private_cards", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["move_private_cards"] = mod
    spec.loader.exec_module(mod)
    return mod


mv = _load()


def _copy(top: Path, name: str) -> Path:
    pub = top / "tests" / "eval" / name
    shutil.copytree(EVAL / name, pub, ignore=shutil.ignore_patterns(".build", ".*.tmp"))
    return pub


def _pick(pub: Path, cut: str) -> list[str]:
    ids = mg.card_ids(pub)
    fixed = {r["id"] for r in label_file.read(pub)} & {
        i
        for i, _ in mv._rows(pub / mv.FIXED)  # pyright: ignore[reportPrivateUsage]
    }
    return {"first": ids[:3], "last": ids[-3:], "interleaved": ids[::7],
            "fixed": sorted(fixed)[:5] or ids[:1]}[cut]  # fmt: skip


@pytest.mark.parametrize("cut", ["first", "last", "interleaved", "fixed"])
def test_a_move_keeps_the_set_version_and_its_labels(tmp_path: Path, cut: str) -> None:
    pub = _copy(tmp_path, "synthetic")
    before = evalrun.set_version(pub)
    labels_before = (pub / "labels.jsonl").read_bytes()
    ids = _pick(pub, cut)
    m = mv.apply(tmp_path, "synthetic", ids)
    assert m.cards == len(ids) and m.rows["labels.jsonl"] == len(ids)
    assert not set(ids) & set(mg.card_ids(pub))
    mg.refresh_merged(pub)
    assert evalrun.set_version(pub) == before
    assert (mg.resolve(pub) / "labels.jsonl").read_bytes() == labels_before


def test_drafts_checks_keys_move_with_their_cards(tmp_path: Path) -> None:
    pub = _copy(tmp_path, "drafts")
    before = evalrun.set_version(pub)
    keyed = list(mv._parsed((pub / "checks.yaml").read_text(), "checks"))  # pyright: ignore[reportPrivateUsage]
    ids = keyed[::5]
    head = (pub / "checks.yaml").read_text().split("\nd-", 1)[0]
    m = mv.apply(tmp_path, "drafts", ids)
    assert m.rows["checks.yaml"] == len(ids)
    assert (pub / "checks.yaml").read_text().startswith(head)  # the header stays public
    priv = mg.paths(pub).private
    assert priv is not None and not (priv / "checks.yaml").read_text().startswith("#")
    mg.refresh_merged(pub)
    assert evalrun.set_version(pub) == before


def test_a_move_refuses_a_card_already_private_or_missing(tmp_path: Path) -> None:
    pub = _copy(tmp_path, "synthetic")
    one = mg.card_ids(pub)[0]
    mv.apply(tmp_path, "synthetic", [one])
    with pytest.raises(InvalidInputError, match="no public card"):
        mv.apply(tmp_path, "synthetic", [one])
    (pub / "cases" / f"{one}.md").write_text("x")
    with pytest.raises(InvalidInputError, match="already in the private set"):
        mv.apply(tmp_path, "synthetic", [one])


MARKED = """---
id: {id}
title: test
from: "A <a@vendor-a.example>"
subject: "Hello"
hidden_text: "a note"
---
Body line
"""


def test_the_list_is_the_marked_cards_but_the_pinned_starter(tmp_path: Path) -> None:
    pub = tmp_path / "tests" / "eval" / "synthetic"
    (pub / "cases").mkdir(parents=True)
    for name in ("starter-injection", "starter-control"):
        shutil.copy(EVAL / "synthetic" / "cases" / f"{name}.md", pub / "cases")
    (pub / "cases" / "made-inj.md").write_text(MARKED.format(id="made-inj"))
    assert not build_all(pub).findings
    listed = mv.move_list(tmp_path, ("synthetic",))
    assert listed == {"synthetic": ["made-inj"]}
    assert mv.check_pin(listed, {"synthetic": 1}, mv.digest(listed)) == []
    bad = mv.check_pin(listed, {"synthetic": 2}, "0" * 64)
    assert len(bad) == 2 and "pinned 2" in bad[0]
    # the starter with other bytes is no longer the pinned starter: it is listed
    p = pub / "cases" / "starter-injection.md"
    p.write_text(p.read_text() + "\n")
    want = {"synthetic": ["made-inj", "starter-injection"]}
    assert mv.move_list(tmp_path, ("synthetic",)) == want


def test_the_hash_file_matches_the_pinned_list() -> None:
    """The guard's hashes are the pinned list's IDs plus the IDs named before their card exists."""
    known = mv.hashes(ROOT)
    assert len(known) == sum(mv.PIN_COUNTS.values()) + len(mv.LATER)
    assert {mv._h(i) for i in mv.LATER} <= known  # pyright: ignore[reportPrivateUsage]


def test_the_public_check_finds_every_trace(tmp_path: Path) -> None:
    pub = _copy(tmp_path, "synthetic")
    _copy(tmp_path, "drafts")
    ids = mg.card_ids(pub)
    hashed = {mv._h(ids[0])}  # pyright: ignore[reportPrivateUsage]
    found = mv.public_check(tmp_path, hashed)
    assert any(f"cases/{ids[0]}.md" in x for x in found)
    assert any(f"eml/{ids[0]}.eml" in x for x in found)
    assert any(f"labels.jsonl: a row for {ids[0]}" in x for x in found)
    assert all("drafts" not in x or "d-inj-" in x for x in found)  # the prefix
    mv.apply(tmp_path, "synthetic", [ids[0]])
    assert not [x for x in mv.public_check(tmp_path, hashed) if "synthetic" in x]
