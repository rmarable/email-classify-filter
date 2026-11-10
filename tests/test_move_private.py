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
    # the list reads the cards; a public build of a marked card is refused (from the move on)
    with pytest.raises(InvalidInputError, match="made-inj: marked card in a public set"):
        build_all(pub)
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


def test_a_dry_run_lists_the_move_and_writes_nothing(tmp_path: Path) -> None:
    pub = _copy(tmp_path, "synthetic")
    ids = _pick(pub, "fixed")
    before = {p: p.read_bytes() for p in pub.rglob("*") if p.is_file()}
    m = mv.plan_move(tmp_path, "synthetic", ids)
    assert m.cards == len(ids) and m.rows["labels.jsonl"] == len(ids)
    assert {f"cases/{i}.md" for i in ids} <= set(m.files)
    assert {p: p.read_bytes() for p in pub.rglob("*") if p.is_file()} == before
    assert not (tmp_path / "eval").exists()


def test_a_move_is_refused_before_any_write_when_a_private_row_exists(tmp_path: Path) -> None:
    pub = _copy(tmp_path, "synthetic")
    ids = _pick(pub, "first")
    priv = tmp_path / "eval" / "private" / "synthetic"
    priv.mkdir(parents=True)
    row = next(x for i, x in mv._rows(pub / "labels.jsonl") if i == ids[0])  # pyright: ignore[reportPrivateUsage]
    (priv / "labels.jsonl").write_text(row + "\n")
    before = (pub / "labels.jsonl").read_bytes()
    with pytest.raises(InvalidInputError, match=r"already in the private set: labels\.jsonl"):
        mv.apply(tmp_path, "synthetic", ids)
    assert (pub / "labels.jsonl").read_bytes() == before
    assert all((pub / "cases" / f"{i}.md").is_file() for i in ids)


def test_a_comment_above_a_moved_key_moves_with_it_and_mentions_are_caught(tmp_path: Path) -> None:
    """L10: a comment about a card sits directly above its key and moves with it; any public
    mention of a moved ID, in any text, is caught by the guard."""
    pub = _copy(tmp_path, "drafts")
    text = (pub / "checks.yaml").read_text()
    key = mv._blocks(text)[1][3][0]  # pyright: ignore[reportPrivateUsage]
    at = text.index(f"\n{key}:") + 1
    (pub / "checks.yaml").write_text(text[:at] + f"# about {key}: the second draft\n" + text[at:])
    mv.apply(tmp_path, "drafts", [key])
    priv = tmp_path / "eval" / "private" / "drafts"
    assert f"# about {key}" in (priv / "checks.yaml").read_text()
    assert f"# about {key}" not in (pub / "checks.yaml").read_text()
    with (pub / "checks.yaml").open("a") as f:
        f.write(f"# see also {key}\n")
    found = mv.public_check(tmp_path, {mv._h(key)})  # pyright: ignore[reportPrivateUsage]
    assert any("checks.yaml: mentions" in x and key in x for x in found)


def test_a_split_that_doesnt_merge_back_is_refused_before_any_write(tmp_path: Path) -> None:
    pub = _copy(tmp_path, "synthetic")
    priv = tmp_path / "eval" / "private" / "synthetic"
    ids = _pick(pub, "first")
    writes, _n = mv._contents(pub, priv, set(ids))  # pyright: ignore[reportPrivateUsage]
    lost = {k: v.split("\n", 1)[1] if k.name == "reviews.jsonl" and v else v
            for k, v in writes.items()}  # fmt: skip
    if any(k.name == "reviews.jsonl" for k in writes):
        with pytest.raises(InvalidInputError, match=r"reviews\.jsonl: the split loses"):
            mv._verify(pub, priv, lost)  # pyright: ignore[reportPrivateUsage]
    dropped = dict(writes)
    lab = next(k for k in writes if k.name == "labels.jsonl" and "private" in str(k))
    dropped[lab] = ""
    with pytest.raises(InvalidInputError, match=r"labels\.jsonl: the split doesn't merge back"):
        mv._verify(pub, priv, dropped)  # pyright: ignore[reportPrivateUsage]
    assert not priv.exists()
