"""Where every write lands (EVAL-PRIVATE.md, "Finding the private set"; Step 3 review rounds 7-8):
for a link at each place a set's location is read from, inside either half, or inside the merged
view, every writer (the merged view's refresh, a card writer through `halves`)
either refuses or writes only where it should: nothing outside the checkout changes, and neither
half gains a file or changes a byte it didn't write itself. A source is edited first, so the
refresh really rebuilds."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import merged as mg
from ecf.eval.setio import atomic_write
from tests.test_eval_merged import _small  # pyright: ignore[reportPrivateUsage]

M = "eval/private/.merged/heldout3"
# where the link sits, relative to the checkout top (heldout3 split by `_small`)
LOCATION = ["tests/eval/heldout3", "tests/eval", "tests", "eval", "eval/private",
            "eval/private/heldout3", "eval/private/.merged", M, f"{M}.lock"]  # fmt: skip
INSIDE = ["tests/eval/heldout3/cases", "tests/eval/heldout3/labels.jsonl",
          "eval/private/heldout3/cases", "eval/private/heldout3/eml",
          "eval/private/heldout3/labels.jsonl"]  # fmt: skip
MERGED = [f"{M}/cases", f"{M}/eml", f"{M}/labels.jsonl", "into public"]  # round 8, B1


def _contents(d: Path) -> dict[str, bytes]:
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*"))
            if p.is_file() and not p.is_symlink()}  # fmt: skip


def _writers(pub: Path) -> list[tuple[str, Callable[[], object]]]:
    def card_writer() -> None:
        for h in mg.halves(pub):
            f = h / "labels.jsonl"
            atomic_write(f, f.read_bytes())

    return [("refresh", lambda: mg.refresh_merged(pub)),
            ("card writer", card_writer)]  # fmt: skip


def _link(top: Path, pub: Path, outside: Path, where: str) -> None:
    """Put a link at `where`, to a copy of what was there moved outside the checkout (or an
    empty folder); "into public": the merged cards folder made a link to the public half's."""
    if where == "into public":
        shutil.rmtree(top / M / "cases")
        (top / M / "cases").symlink_to(pub / "cases", target_is_directory=True)
        return
    if where == "none":
        return
    src = top / where
    dst = outside / where.replace("/", "_")
    if os.path.lexists(src):
        shutil.move(src, dst)
    else:  # a folder the set doesn't have yet (heldout3's emails are built locally)
        dst.mkdir()
    os.symlink(dst, src, target_is_directory=dst.is_dir())


@pytest.mark.parametrize("where", ["none", *LOCATION, *INSIDE, *MERGED])
def test_no_write_lands_where_it_shouldnt(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, where: str
) -> None:
    top = tmp_path / "top"
    top.mkdir()
    pub, priv = _small(top)
    mg.refresh_merged(pub)
    outside = tmp_path_factory.mktemp("outside")
    _link(top, pub, outside, where)
    card = next((priv / "cases").glob("*.md"), None) if not os.path.islink(priv) else None
    if card is not None and not card.parent.is_symlink():  # a source edit: the refresh rebuilds
        card.write_text(card.read_text() + "\n")
    halves = {"outside": outside, "public": pub, "private": priv}
    before = {k: _contents(d) for k, d in halves.items() if d.is_dir()}
    refused: list[str] = []
    for name, call in _writers(pub):
        try:
            call()
        except InvalidInputError:
            refused.append(name)
        for k, d in halves.items():
            if k in before:
                got, want = _contents(d), dict(before[k])
                if name == "card writer" and k != "outside":  # its own write: labels.jsonl
                    got.pop("labels.jsonl", None)
                    want.pop("labels.jsonl", None)
                assert got == want, f"{name} changed {k} ({where})"
    if where == "none":
        assert refused == []
    elif where in LOCATION:  # the location itself: every writer refuses
        assert refused == ["refresh", "card writer"], refused
    else:  # inside a half or the merged view: the refresh refuses the link before it writes
        assert "refresh" in refused, refused
