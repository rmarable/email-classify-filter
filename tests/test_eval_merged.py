"""The merged view of a public and a private eval set (EVAL-PRIVATE.md "The overlay", V1, W1,
W2)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import labels as label_file
from ecf.eval import merged as mg

EVAL = Path(__file__).resolve().parent / "eval"


def _copy_set(name: str, top: Path) -> Path:
    src, dst = EVAL / name, top / "tests" / "eval" / name
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".build", ".*.tmp"))
    return dst


def _rows(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.is_file() else []


def _split(pub: Path, priv: Path, ids: set[str]) -> None:
    """Move the cards `ids` (their card, email and every row naming them) to the private half."""
    for d in ("cases", "eml", ".build"):
        (priv / d).mkdir(parents=True, exist_ok=True)
        for p in list((pub / d).glob("*")) if (pub / d).is_dir() else []:
            if p.stem in ids:
                shutil.move(p, priv / d / p.name)
    for f in (mg.LABELS, mg.FIXED, mg.REVIEWS):
        rows = _rows(pub / f)
        if not rows:
            continue
        keep = [r for r in rows if json.loads(r)["id"] not in ids]
        move = [r for r in rows if json.loads(r)["id"] in ids]
        (pub / f).write_text("".join(r + "\n" for r in keep))
        if move:
            (priv / f).write_text("".join(r + "\n" for r in move))
        elif f == mg.LABELS:
            (priv / f).write_text("")


@pytest.mark.parametrize("cut", ["first", "last", "interleaved", "none_fixed", "all_fixed"])
def test_a_split_set_merges_back_to_the_same_bytes_and_version(tmp_path: Path, cut: str) -> None:
    pub = _copy_set("synthetic", tmp_path)
    before = {f: (pub / f).read_bytes() for f in mg.LABEL_FILES}
    version = label_file.set_version(pub)
    ids = mg.card_ids(pub)
    fixed_ids = {json.loads(r)["id"] for r in _rows(pub / mg.FIXED)}
    move = {
        "first": {ids[0]},
        "last": {ids[-1]},
        "interleaved": set(ids[::7]),
        "none_fixed": {i for i in ids[::5] if i not in fixed_ids},
        "all_fixed": {i for i in ids if i in fixed_ids},
    }[cut]
    priv = tmp_path / "eval" / "private" / "synthetic"
    _split(pub, priv, move)
    assert mg.refresh_merged(pub) == "refreshed"
    m = mg.resolve(pub)
    assert m == mg.paths(pub).merged
    for f, data in before.items():
        assert (m / f).read_bytes() == data
    assert label_file.set_version(m) == version
    assert mg.refresh_merged(pub) == "fresh"


def test_drafts_checks_yaml_merges_by_key(tmp_path: Path) -> None:
    from ecf.yamlio import load_yaml  # noqa: PLC0415

    pub = _copy_set("drafts", tmp_path)
    whole = load_yaml((pub / mg.CHECKS).read_text())
    priv = tmp_path / "eval" / "private" / "drafts"
    move = {i for i in mg.card_ids(pub) if i.startswith("d-inj-")}
    _split(pub, priv, move)
    text = (pub / mg.CHECKS).read_text()
    head, body = text[: len(text) - len(mg._body(text))], mg._body(text)  # pyright: ignore[reportPrivateUsage]
    blocks: dict[str, str] = {}
    key = ""
    for line in body.splitlines(keepends=True):
        if line[:1].strip() and line.rstrip().endswith(":"):
            key = line.rstrip()[:-1]
        blocks[key] = blocks.get(key, "") + line
    (pub / mg.CHECKS).write_text(head + "".join(v for k, v in blocks.items() if k not in move))
    (priv / mg.CHECKS).write_text("".join(v for k, v in blocks.items() if k in move))
    mg.refresh_merged(pub)
    assert load_yaml((mg.resolve(pub) / mg.CHECKS).read_text()) == whole


def _small(tmp_path: Path) -> tuple[Path, Path]:
    pub = _copy_set("heldout3", tmp_path)
    priv = tmp_path / "eval" / "private" / "heldout3"
    _split(pub, priv, set(mg.card_ids(pub)[:4]))
    return pub, priv


def test_resolver_cases(tmp_path: Path) -> None:
    pub = _copy_set("heldout3", tmp_path)
    assert mg.resolve(pub) == pub  # no private set: the public set
    priv = tmp_path / "eval" / "private" / "heldout3"
    _split(pub, priv, set(mg.card_ids(pub)[:4]))
    with pytest.raises(InvalidInputError, match="ecf eval build"):
        mg.resolve(pub)
    mg.refresh_merged(pub)
    m = mg.resolve(pub)
    card = next((priv / "cases").glob("*.md"))
    os.utime(card)  # touched, unchanged: still fresh (the sha256 check)
    assert mg.resolve(pub) == m
    card.write_text(card.read_text() + "\n")
    with pytest.raises(InvalidInputError, match="out of date"):
        mg.resolve(pub)
    mg.refresh_merged(pub)
    assert mg.resolve(pub) == m


@pytest.mark.parametrize("which", ["private", "merged", "Mixed"])
def test_resolver_refuses_a_private_or_merged_path(tmp_path: Path, which: str) -> None:
    pub, priv = _small(tmp_path)
    mg.refresh_merged(pub)
    path = {
        "private": priv,
        "merged": mg.paths(pub).merged,
        "Mixed": tmp_path / "Eval" / "Private" / "heldout3",
    }[which]
    assert path is not None
    with pytest.raises(InvalidInputError, match="public set's path"):
        mg.resolve(path)


def test_a_consistent_hand_edit_of_the_merged_files_is_refused(tmp_path: Path) -> None:
    pub, _ = _small(tmp_path)
    mg.refresh_merged(pub)
    m = mg.resolve(pub)
    lab = m / mg.LABELS
    lab.chmod(0o644)
    rows = _rows(lab)
    lab.write_text("".join(r + "\n" for r in rows[1:]))
    doc = json.loads((m / "merge.json").read_text())
    doc["manifest"]["merged"] = mg._manifest(m, {})  # pyright: ignore[reportPrivateUsage]
    doc["merged_version"] = label_file.set_version(m)
    (m / "merge.json").write_text(json.dumps(doc))
    with pytest.raises(InvalidInputError, match="out of date"):
        mg.resolve(pub)


def test_ownership_refusals(tmp_path: Path) -> None:
    pub, priv = _small(tmp_path)
    one = mg.card_ids(priv)[0]
    shutil.copy(priv / "cases" / f"{one}.md", pub / "cases" / f"{one}.md")
    with pytest.raises(InvalidInputError, match="in both"):
        mg.merge_sources(pub, priv)
    (pub / "cases" / f"{one}.md").unlink()
    row = _rows(priv / mg.LABELS)[0]
    (pub / mg.LABELS).write_text((pub / mg.LABELS).read_text() + row + "\n")
    with pytest.raises(InvalidInputError, match=r"public labels\.jsonl: rows for cards not in"):
        mg.merge_sources(pub, priv)


def test_a_refresh_waits_for_or_refuses_a_held_lock(tmp_path: Path) -> None:
    pub, _ = _small(tmp_path)
    with pytest.raises(InvalidInputError, match="ecf eval build"):
        mg.take_shared(pub)  # no lock file yet
    mg.refresh_merged(pub)
    fd = mg.take_shared(pub)
    try:
        (mg.paths(pub).private or pub).joinpath("cases", "new.md").write_text("x")
        with pytest.raises(mg.RefreshBusy, match="the set is being read"):
            mg.refresh_merged(pub, wait=0.1)
    finally:
        mg.release(fd)
        (mg.paths(pub).private or pub).joinpath("cases", "new.md").unlink()
    assert mg.refresh_merged(pub) == "fresh"


def test_a_refresh_killed_part_way_is_followed_by_one_that_succeeds(tmp_path: Path) -> None:
    pub, _ = _small(tmp_path)
    mg.refresh_merged(pub)
    m = mg.paths(pub).merged
    assert m is not None
    (m / "merge.json").unlink()  # killed before merge.json
    (m / "cases" / ".x.md.tmp").write_text("half")
    (m / ".labels.jsonl.tmp").write_text("half")
    with pytest.raises(InvalidInputError):
        mg.resolve(pub)
    assert mg.refresh_merged(pub) == "refreshed"
    assert mg.resolve(pub) == m


def test_hard_linked_build_file_written_in_place_changes_its_source_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where a clone fails, a merged `.build/` file is a hard link to its source: a write through
    the merged folder changes the source too, so the view stays a faithful copy of its sources
    (round 11: freshness is by content); the changed email is refused at load by its label row's
    sha256 (evalrun), which is what keeps it from being read."""
    pub, priv = _small(tmp_path)
    (priv / ".build").mkdir(exist_ok=True)
    big = priv / ".build" / f"{mg.card_ids(priv)[0]}.eml"
    big.write_bytes(b"x" * 100)
    monkeypatch.setattr(mg, "_CLONE", None)
    mg.refresh_merged(pub)
    m = mg.resolve(pub)
    linked = m / ".build" / big.name
    assert linked.stat().st_ino == big.stat().st_ino
    with linked.open("r+b") as f:  # in place, through the merged folder
        f.write(b"y")
    assert big.read_bytes() == linked.read_bytes() == b"y" + b"x" * 99
    assert mg.resolve(pub) == m


# The split's remaining shapes (C1): neither source with fixed_labels.jsonl, only the private
# one with it, both with it, and an empty public or private half.


@pytest.mark.parametrize("shape", ["neither_fixed", "only_private_fixed", "both_fixed",
                                   "empty_public", "empty_private"])  # fmt: skip
def test_every_split_shape_merges_back(tmp_path: Path, shape: str) -> None:
    pub = _copy_set("synthetic", tmp_path)
    if shape == "neither_fixed":
        (pub / mg.FIXED).unlink()
    before = {f: (pub / f).read_bytes() if (pub / f).is_file() else None for f in mg.LABEL_FILES}
    version = label_file.set_version(pub)
    ids = mg.card_ids(pub)
    fixed_ids: set[str] = {json.loads(r)["id"] for r in _rows(pub / mg.FIXED)}
    move: set[str] = {
        "neither_fixed": set(ids[::5]),
        "only_private_fixed": fixed_ids,
        "both_fixed": set(ids[::3]),
        "empty_public": set(ids),
        "empty_private": set[str](),
    }[shape]
    priv = tmp_path / "eval" / "private" / "synthetic"
    _split(pub, priv, move)
    if shape == "only_private_fixed":
        assert _rows(pub / mg.FIXED) == []
        (pub / mg.FIXED).unlink()  # the public half has no file at all
    if shape == "both_fixed":
        assert _rows(pub / mg.FIXED) and _rows(priv / mg.FIXED)
    mg.refresh_merged(pub)
    m = mg.resolve(pub)
    for f, data in before.items():
        assert ((m / f).read_bytes() if (m / f).is_file() else None) == data
    assert label_file.set_version(m) == version


def test_a_checks_key_for_the_other_halfs_card_or_a_duplicate_key_is_refused(
    tmp_path: Path,
) -> None:
    pub = _copy_set("drafts", tmp_path)
    priv = tmp_path / "eval" / "private" / "drafts"
    one = next(i for i in mg.card_ids(pub) if f"\n{i}:" in (pub / mg.CHECKS).read_text())
    _split(pub, priv, {one})
    text = (pub / mg.CHECKS).read_text()
    start = text.index(f"\n{one}:") + 1
    end = text.find("\nd-", start)  # the next key (every drafts card is d-*)
    (pub / mg.CHECKS).write_text(text[:start] + (text[end + 1 :] if end != -1 else ""))
    pub_card = next(i for i in mg.card_ids(pub) if f"\n{i}:" in (pub / mg.CHECKS).read_text())
    (priv / mg.CHECKS).write_text(f"{pub_card}:\n  harmless: [x]\n")  # names a public card
    with pytest.raises(
        InvalidInputError, match=f"private checks.yaml: rows for cards not in that set: {pub_card}"
    ):
        mg.merge_sources(pub, priv)
    text = (pub / mg.CHECKS).read_text()
    block = text[text.index(f"\n{pub_card}:") + 1 :].split("\nd-", 1)[0] + "\n"
    (priv / mg.CHECKS).write_text(f"{one}:\n  harmless: [x]\n")
    mg.merge_sources(pub, priv)  # without the duplicate, the halves merge
    (pub / mg.CHECKS).write_text(text + block)  # the same key twice in one file
    with pytest.raises(InvalidInputError, match=r"checks\.yaml: invalid YAML"):  # yamlio's refusal
        mg.merge_sources(pub, priv)


def test_an_open_merged_copy_keeps_its_bytes_when_a_source_or_a_rebuild_replaces_it(
    tmp_path: Path,
) -> None:
    """C4, C28: a reader holding a merged file open keeps reading the bytes it opened, whether a
    source writer replaces the linked `.build/` file or a rebuild replaces the copy."""
    pub, priv = _small(tmp_path)
    (priv / ".build").mkdir(exist_ok=True)
    big = priv / ".build" / f"{mg.card_ids(priv)[0]}.eml"
    big.write_bytes(b"x" * 100)
    mg.refresh_merged(pub)
    m = mg.resolve(pub)
    first = mg.card_ids(m)[0]
    with (
        (m / ".build" / big.name).open("rb") as linked,
        (m / "cases" / f"{first}.md").open("rb") as card,
    ):
        tmp = big.with_name(".x.tmp")
        tmp.write_bytes(b"y" * 100)
        os.replace(tmp, big)  # the source writer's atomic replace
        src = (priv if (priv / "cases" / f"{first}.md").is_file() else pub) / "cases"
        (src / f"{first}.md").write_text((src / f"{first}.md").read_text() + "\n")
        assert mg.refresh_merged(pub) == "refreshed"  # the rebuild replaces the copies
        assert linked.read() == b"x" * 100
        assert card.read() != (m / "cases" / f"{first}.md").read_bytes()


def test_a_merged_folder_without_merge_json_is_refused_by_path(tmp_path: Path) -> None:
    from ecf_server import evalrun  # noqa: PLC0415

    pub, _priv = _small(tmp_path)
    mg.refresh_merged(pub)
    m = mg.paths(pub).merged
    assert m is not None
    (m / "merge.json").unlink()
    with pytest.raises(InvalidInputError, match="public set's path"):
        mg.resolve(m)
    with pytest.raises(InvalidInputError, match="public set's path"):
        evalrun.load(m, fraud_only=False)


def test_each_committed_public_set_owns_its_rows_and_keys() -> None:
    """CI runs the ownership check on the public sets alone (EVAL-PRIVATE.md, "The merged set"):
    every label row and checks.yaml key names a card of that same set."""
    for root in sorted(p for p in EVAL.iterdir() if (p / "cases").is_dir()):
        ids = set(mg.card_ids(root))
        for kind in (mg.LABELS, mg.FIXED):
            stray = sorted({i for i, _ in mg._jsonl_rows(root / kind)} - ids)  # pyright: ignore[reportPrivateUsage]
            assert not stray, (root.name, kind, stray[:5])
        _text, keys = mg._checks(root / mg.CHECKS)  # pyright: ignore[reportPrivateUsage]
        assert not sorted(set(keys) - ids), (root.name, "checks.yaml")


# A writer that takes no lock (a review log append, a checks.yaml edit, a card edit) landing at
# any step of a refresh: the view may be stale afterwards, but never stale and called fresh
# (review round 2, M5).

WRITERS = ["reviews append", "checks edit", "card edit"]
STEPS = ["before content", "after content", "after copies", "before the freshness check"]


def _write(priv: Path, how: str) -> None:
    if how == "reviews append":
        rows = mg._jsonl_rows(priv / mg.REVIEWS)  # pyright: ignore[reportPrivateUsage]
        line = rows[0][1] if rows else json.dumps({"id": mg.card_ids(priv)[0]})
        with (priv / mg.REVIEWS).open("a") as f:
            f.write(line + "\n")
    elif how == "checks edit":
        p = priv / mg.CHECKS
        p.write_text(p.read_text() + "# edited\n")
    else:
        card = next((priv / "cases").glob("*.md"))
        card.write_text(card.read_text() + "\n")


def _honest(sp: mg.SetPaths) -> bool:
    """Fresh implies the view holds what its sources give now (all four files, every card)."""
    if not mg.is_fresh(sp):
        return True
    assert sp.private is not None and sp.merged is not None
    want = mg.merge_sources(sp.public, sp.private)
    for kind, data in want.items():
        p = sp.merged / kind
        if (data is None) != (not p.is_file()) or (data is not None and p.read_bytes() != data):
            return False
    for half in (sp.public, sp.private):
        for c in (half / "cases").glob("*.md"):
            if (sp.merged / "cases" / c.name).read_bytes() != c.read_bytes():
                return False
    return True


@pytest.mark.parametrize("how", WRITERS)
@pytest.mark.parametrize("step", STEPS)
def test_a_writer_at_any_step_of_a_refresh_never_leaves_a_stale_view_called_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str, step: str
) -> None:
    from tests.test_move_private import mv  # noqa: PLC0415 - the move script splits rows and keys

    pub = _copy_set("drafts", tmp_path)
    priv = tmp_path / "eval" / "private" / "drafts"
    mv.apply(tmp_path, "drafts", mg.card_ids(pub)[::4])
    if not (priv / mg.CHECKS).is_file():
        (priv / mg.CHECKS).write_text("")
    if not (priv / mg.REVIEWS).is_file():
        (priv / mg.REVIEWS).write_text("")
    mg.refresh_merged(pub)
    sp = mg.paths(pub)
    first = next((priv / "cases").glob("*.md"))
    first.write_text(first.read_text() + "\n")  # stale, so the next refresh builds
    if step == "before the freshness check":
        mg.refresh_merged(pub)  # fresh again: the next refresh only checks
    done = [False]

    def once() -> None:
        if not done[0]:
            done[0] = True
            _write(priv, how)

    if step == "before content":
        real_ms = mg.merge_sources

        def ms(*a: Any, **k: Any) -> Any:
            once()
            return real_ms(*a, **k)

        monkeypatch.setattr(mg, "merge_sources", ms)
    elif step == "after content":
        real_ms2 = mg.merge_sources

        def ms2(*a: Any, **k: Any) -> Any:
            out = real_ms2(*a, **k)
            once()
            return out

        monkeypatch.setattr(mg, "merge_sources", ms2)
    elif step == "after copies":
        real_cc = mg._copy_cards  # pyright: ignore[reportPrivateUsage]

        def cc(*a: Any, **k: Any) -> Any:
            out = real_cc(*a, **k)
            once()
            return out

        monkeypatch.setattr(mg, "_copy_cards", cc)
    else:
        real_if = mg.is_fresh

        def fr(*a: Any, **k: Any) -> Any:
            once()
            return real_if(*a, **k)

        monkeypatch.setattr(mg, "is_fresh", fr)
    try:
        mg.refresh_merged(pub)
    except InvalidInputError as exc:  # it saw the change and stayed stale: also right
        assert "changed while the merged view was refreshed" in str(exc)
    monkeypatch.undo()
    assert done[0], "the writer never ran at that step"
    assert _honest(sp)
    mg.refresh_merged(pub)  # the next refresh repairs it
    assert mg.is_fresh(sp) and _honest(sp)


def _link_into(priv: Path, where: str, out: Path) -> None:
    """Put a symbolic link at `where` in the private half, to a copy of what was there."""
    if where == "root":
        real = out / "real"
        shutil.move(priv, real)
        priv.symlink_to(real, target_is_directory=True)
        return
    target = priv / where
    if where == "cases/":
        card = next((priv / "cases").glob("*.md"))
        shutil.copy(card, out / card.name)
        card.unlink()
        card.symlink_to(out / card.name)
        return
    shutil.move(target, out / target.name)
    target.symlink_to(out / target.name, target_is_directory=(out / target.name).is_dir())


@pytest.mark.parametrize("where", ["cases/", "cases", "labels.jsonl", "root"])
def test_a_symbolic_link_in_a_set_is_refused_by_the_build_and_the_readers(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, where: str
) -> None:
    """Round 5, B-1: nothing in a set is followed. The build refuses a link; a link put in after
    a build makes the view refused, even with the same bytes behind it."""
    pub, priv = _small(tmp_path)
    _link_into(priv, where, tmp_path_factory.mktemp("out"))
    with pytest.raises(InvalidInputError, match="symbolic link"):
        mg.refresh_merged(pub)
    pub2, priv2 = _small(tmp_path_factory.mktemp("second"))
    mg.refresh_merged(pub2)
    _link_into(priv2, where, tmp_path_factory.mktemp("out2"))
    with pytest.raises(InvalidInputError, match="symbolic link"):
        mg.resolve(pub2)
    assert where != "cases/" or any(p.is_symlink() for p in (priv2 / "cases").iterdir())


def test_a_pipe_in_a_set_is_refused_and_never_blocks(tmp_path: Path) -> None:
    pub, priv = _small(tmp_path)
    (priv / mg.REVIEWS).unlink(missing_ok=True)
    os.mkfifo(priv / mg.REVIEWS)  # a read that opened it would wait for a writer forever
    with pytest.raises(InvalidInputError, match="special file"):
        mg.refresh_merged(pub)


def test_indented_private_checks_keys_that_would_nest_are_refused(tmp_path: Path) -> None:
    """Round 5, B-2: each half parses alone with its own keys, but joined as text the private
    keys would land inside the last public key."""
    pub, priv = _small(tmp_path)
    a, b = mg.card_ids(pub)[0], mg.card_ids(priv)[0]
    (pub / mg.CHECKS).write_text(f"{a}:\n  expect: x\n")
    (priv / mg.CHECKS).write_text(f"  {b}:\n    expect: y\n")
    with pytest.raises(InvalidInputError, match="don't merge"):
        mg.refresh_merged(pub)
    (priv / mg.CHECKS).write_text(f"{b}:\n  expect: y\n")
    mg.refresh_merged(pub)
    from ecf.yamlio import load_yaml  # noqa: PLC0415

    got = load_yaml((mg.resolve(pub) / mg.CHECKS).read_text())
    assert got == {a: {"expect": "x"}, b: {"expect": "y"}}


def test_a_rewrite_that_restores_the_mtime_makes_the_view_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 5, B-4: same size, inode and mtime after an in-place edit; ctime moves."""
    monkeypatch.setattr(mg, "RACY_NS", 0)  # files as if written long ago
    pub, priv = _small(tmp_path)
    mg.refresh_merged(pub)
    card = next((priv / "cases").glob("*.md"))
    st = card.stat()
    with card.open("r+b") as f:
        data = f.read()
        f.seek(0)
        f.write(data[:-1] + (b"y" if data[-1:] != b"y" else b"z"))
    os.utime(card, ns=(st.st_atime_ns, st.st_mtime_ns))
    with pytest.raises(InvalidInputError, match="out of date"):
        mg.resolve(pub)


def _move_and_link(src: Path, dst: Path) -> None:
    shutil.move(src, dst)
    src.symlink_to(dst, target_is_directory=True)


@pytest.mark.parametrize(
    "where", ["set", "tests/eval", "tests", "eval", "eval/private", "private set", ".merged"]
)
def test_a_set_reached_through_a_link_is_refused_everywhere(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, where: str
) -> None:
    """Round 6, operator decision 2026-11-06: a set's location is never found through a link,
    at the set folder or any folder between it and the checkout top, on either half."""
    pub, _priv = _small(tmp_path)
    mg.refresh_merged(pub)
    out = tmp_path_factory.mktemp("elsewhere") / "x"
    target = {
        "set": pub, "tests/eval": pub.parent, "tests": pub.parent.parent,
        "eval": tmp_path / "eval", "eval/private": tmp_path / "eval" / "private",
        "private set": tmp_path / "eval" / "private" / "heldout3",
        ".merged": tmp_path / "eval" / "private" / mg.MERGED_DIR,
    }[where]  # fmt: skip
    _move_and_link(target, out)
    sp = mg.paths(pub)
    assert sp.refused is not None and sp.top is None and sp.private is None
    assert mg.repo_top(pub) is None and mg.membership(pub) is None
    for call in (mg.resolve, mg.refresh_merged, mg.halves):
        with pytest.raises(InvalidInputError, match="symbolic link"):
            call(pub)


def test_an_alias_of_the_whole_checkout_is_the_same_checkout(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A link above the checkout top (macOS `/tmp`, an alias of the checkout) stays allowed."""
    pub, _priv = _small(tmp_path)
    mg.refresh_merged(pub)
    alias = tmp_path_factory.mktemp("alias") / "top"
    alias.symlink_to(tmp_path, target_is_directory=True)
    via = alias / "tests" / "eval" / "heldout3"
    sp = mg.paths(via)
    assert sp.refused is None and sp.top == mg.paths(pub).top and sp.private is not None
    assert mg.resolve(via) == mg.resolve(pub)


def test_racy_counts_ctime_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 7, B5: a change that restores the mtime still moves ctime, which keeps it racy."""
    import time  # noqa: PLC0415

    old = time.time_ns() - 10 * mg.RACY_NS
    assert not mg.is_racy(old, old)
    assert mg.is_racy(old, time.time_ns())


def test_a_private_half_given_directly_through_a_link_is_refused(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Round 7, B4: `eval` a link; the private half's path given directly: a writer refuses,
    and it is still recognised as a private half (the builder's marker check holds)."""
    pub, _priv = _small(tmp_path)
    _move_and_link(tmp_path / "eval", tmp_path_factory.mktemp("away") / "eval")
    given = tmp_path / "eval" / "private" / "heldout3"
    assert mg.is_private_root(given)
    with pytest.raises(InvalidInputError, match="symbolic link"):
        mg.halves(given)
    assert mg.paths(pub).refused is not None


@pytest.mark.macos  # APFS is case-insensitive; CI (Linux) deselects macos tests
def test_a_private_half_stored_in_another_case_is_no_private_half(tmp_path: Path) -> None:
    """Round 8, B2: one rule for the private half. `eval/Private` (as stored) is not the
    checkout's private half, and a reader given a path in it is refused, not handed it as a
    plain set."""
    pub, _priv = _small(tmp_path)
    (tmp_path / "eval" / "private").rename(tmp_path / "eval" / "Private")
    assert mg.paths(pub).private is None
    for given in ("Private", "private"):
        with pytest.raises(InvalidInputError, match="public set's path"):
            mg.resolve(tmp_path / "eval" / given / "heldout3")


def test_back_to_back_refreshes_copy_nothing_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 8, performance B-1: the last refresh's copies are recorded racy; the next refresh
    with no read between finds them by size and sha256 and copies only what changed."""
    pub, priv = _small(tmp_path)
    mg.refresh_merged(pub)
    copied: list[Path] = []
    real = mg._place_copy  # pyright: ignore[reportPrivateUsage]

    def counting(src: Path, dst: Path, data: bytes | None = None) -> None:
        copied.append(dst)
        real(src, dst, data)

    monkeypatch.setattr(mg, "_place_copy", counting)
    card = next((priv / "cases").glob("*.md"))
    card.write_text(card.read_text() + "\n")
    assert mg.refresh_merged(pub) == "refreshed"
    assert [p.name for p in copied if p.parent.name == "cases"] == [card.name]


def test_view_of_names_the_folder_a_read_used(tmp_path: Path) -> None:
    pub, _priv = _small(tmp_path)
    mg.refresh_merged(pub)
    assert mg.view_of(pub) == mg.resolve(pub)
    other = tmp_path / "plain" / "heldout3"
    shutil.copytree(pub, other)
    assert mg.view_of(other) == mg.resolve(other)


def test_a_merged_copy_tampered_with_merge_json_to_match_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 9, B1: merge.json is a cache, not a trust root. A merged card edited and its entry
    rewritten to the new stat, keeping the source's sha256: the view is stale, and the refresh
    puts the source back."""
    monkeypatch.setattr(mg, "RACY_NS", 0)
    pub, priv = _small(tmp_path)
    mg.refresh_merged(pub)
    m = mg.paths(pub).merged
    assert m is not None
    rel = f"cases/{next((priv / 'cases').glob('*.md')).name}"
    copy = m / rel
    copy.chmod(0o644)
    copy.write_text(copy.read_text() + "INJECTED\n")
    doc = json.loads((m / mg.MERGE_FILE).read_text())
    st = os.lstat(copy)
    doc["manifest"]["merged"][rel][:3] = [st.st_size, st.st_mtime_ns, st.st_ino]
    doc["manifest"]["merged"][rel][4] = st.st_ctime_ns
    (m / mg.MERGE_FILE).write_text(json.dumps(doc))
    with pytest.raises(InvalidInputError, match="out of date"):
        mg.resolve(pub)
    assert mg.refresh_merged(pub) == "refreshed"
    assert copy.read_bytes() == (priv / rel).read_bytes()


@pytest.mark.parametrize("what", ["link", "pipe"])
def test_merge_json_is_never_followed_and_a_pipe_never_blocks(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, what: str
) -> None:
    """Round 9, B2: merge.json as a link (to a copy of itself) or a pipe: the view is stale and
    the refresh refuses it, never reading through it or waiting on it."""
    pub, _priv = _small(tmp_path)
    mg.refresh_merged(pub)
    m = mg.paths(pub).merged
    assert m is not None
    mj = m / mg.MERGE_FILE
    if what == "link":
        out = tmp_path_factory.mktemp("out") / "merge.json"
        shutil.copy(mj, out)
        mj.unlink()
        mj.symlink_to(out)
    else:
        mj.unlink()
        os.mkfifo(mj)
    assert not mg.is_fresh(mg.paths(pub))
    with pytest.raises(InvalidInputError, match="symbolic link or special file"):
        mg.refresh_merged(pub)


def test_a_hard_link_from_the_merged_view_into_a_source_is_never_chmodded(
    tmp_path: Path,
) -> None:
    """Round 9, B3: an orphan in the merged view that is a hard link to a source card: removed
    without changing the source's mode; a merged copy hard-linked to its source is copied again."""
    pub, _priv = _small(tmp_path)
    mg.refresh_merged(pub)
    m = mg.paths(pub).merged
    assert m is not None
    src = next((pub / "cases").glob("*.md"))
    src.chmod(0o600)
    os.link(src, m / "cases" / "zzz-orphan.md")
    copy = m / "cases" / src.name
    copy.chmod(0o644)
    copy.unlink()
    os.link(src, copy)  # the copy now shares the source's inode
    card = next((pub / "cases").glob("*.md"))
    (pub / "labels.jsonl").write_text((pub / "labels.jsonl").read_text())  # a rebuild runs
    mg.refresh_merged(pub)
    assert src.stat().st_mode & 0o777 == 0o600
    assert not (m / "cases" / "zzz-orphan.md").exists()
    assert os.lstat(copy).st_ino != os.lstat(src).st_ino and card.exists()


def test_a_refresh_hashes_each_file_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rounds 9 and 11, performance: within one refresh (its freshness check and its build) each
    source and merged file is hashed at most once, though every one is proven by content."""
    import hashlib  # noqa: PLC0415

    pub, priv = _small(tmp_path)
    mg.refresh_merged(pub)
    m = mg.paths(pub).merged
    assert m is not None
    made = [0]
    real = hashlib.sha256

    def counting(*a: Any, **k: Any) -> Any:
        made[0] += 1
        return real(*a, **k)

    card = next((priv / "cases").glob("*.md"))
    card.write_text(card.read_text() + "\n")
    files = sum(len(mg.file_stats(h)) for h in (pub, priv, m))
    import types  # noqa: PLC0415

    monkeypatch.setattr(mg, "hashlib", types.SimpleNamespace(sha256=counting))  # merged's only
    assert mg.refresh_merged(pub) == "refreshed"
    assert made[0] <= files + 1  # the changed card's new copy


def _with_email(pub: Path, priv: Path) -> str:
    """A private card's email file, merged: its path relative to the set."""
    cid = mg.card_ids(priv)[0]
    (priv / "eml").mkdir(exist_ok=True)
    (priv / "eml" / f"{cid}.eml").write_bytes(b"Subject: x\r\n\r\nbody\r\n")
    mg.refresh_merged(pub)
    return f"eml/{cid}.eml"


def test_a_merged_email_forged_with_its_entry_is_stale_and_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 10, B1: every merged copy, not only cards, is proven by its content."""
    monkeypatch.setattr(mg, "RACY_NS", 0)
    pub, priv = _small(tmp_path)
    rel = _with_email(pub, priv)
    m = mg.paths(pub).merged
    assert m is not None
    copy = m / rel
    copy.chmod(0o644)
    copy.write_bytes(copy.read_bytes() + b"forged\r\n")
    doc = json.loads((m / mg.MERGE_FILE).read_text())
    st = os.lstat(copy)
    doc["manifest"]["merged"][rel][:3] = [st.st_size, st.st_mtime_ns, st.st_ino]
    doc["manifest"]["merged"][rel][4] = st.st_ctime_ns
    (m / mg.MERGE_FILE).write_text(json.dumps(doc))
    assert not mg.is_fresh(mg.paths(pub))
    assert mg.refresh_merged(pub) == "refreshed"
    assert copy.read_bytes() == (priv / rel).read_bytes()


def test_an_email_in_both_halves_is_refused_by_name(tmp_path: Path) -> None:
    """Round 10, B2: a file other than a card or a set file in both halves is refused with a
    clear message, not left stale forever."""
    pub, priv = _small(tmp_path)
    rel = _with_email(pub, priv)
    (pub / "eml").mkdir(exist_ok=True)
    (pub / rel).write_bytes(b"other\r\n")
    with pytest.raises(InvalidInputError, match="both the public and the private set"):
        mg.refresh_merged(pub)


def test_a_pipe_at_the_lock_never_blocks_a_reader(tmp_path: Path) -> None:
    """Round 10, B3: every open of the lock refuses a pipe at once."""
    pub, _priv = _small(tmp_path)
    mg.refresh_merged(pub)
    lock = mg.paths(pub).lock
    assert lock is not None
    lock.unlink()
    os.mkfifo(lock)
    for call in (mg.take_shared, mg.refresh_merged):
        with pytest.raises(InvalidInputError, match="special file"):
            call(pub)


def test_a_malformed_manifest_is_stale_and_rebuilt(tmp_path: Path) -> None:
    """Round 10, B4: a bad entry in merge.json means stale, never a crash."""
    pub, _priv = _small(tmp_path)
    mg.refresh_merged(pub)
    m = mg.paths(pub).merged
    assert m is not None
    doc = json.loads((m / mg.MERGE_FILE).read_text())
    first = next(iter(doc["manifest"]["merged"]))
    doc["manifest"]["merged"][first] = [1]
    (m / mg.MERGE_FILE).write_text(json.dumps(doc))
    with pytest.raises(InvalidInputError, match="out of date"):
        mg.resolve(pub)
    assert mg.refresh_merged(pub) == "refreshed"
    assert mg.resolve(pub) == m


def test_a_forged_copy_with_both_entries_forged_is_stale_and_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 11, B1: the merged card edited, and both its merged entry and its source entry in
    merge.json set to the forged content's sha256 (the source file untouched): freshness compares
    contents, never merge.json, so the view is stale and the refresh puts the source back."""
    import hashlib  # noqa: PLC0415

    monkeypatch.setattr(mg, "RACY_NS", 0)
    pub, priv = _small(tmp_path)
    mg.refresh_merged(pub)
    m = mg.paths(pub).merged
    assert m is not None
    rel = f"cases/{next((priv / 'cases').glob('*.md')).name}"
    copy = m / rel
    copy.chmod(0o644)
    copy.write_text(copy.read_text() + "FORGED\n")
    forged = hashlib.sha256(copy.read_bytes()).hexdigest()
    doc = json.loads((m / mg.MERGE_FILE).read_text())
    st = os.lstat(copy)
    doc["manifest"]["merged"][rel] = [st.st_size, st.st_mtime_ns, st.st_ino, forged,
                                      st.st_ctime_ns]  # fmt: skip
    doc["manifest"]["private"][rel][3] = forged
    (m / mg.MERGE_FILE).write_text(json.dumps(doc))
    with pytest.raises(InvalidInputError, match="out of date"):
        mg.resolve(pub)
    assert mg.refresh_merged(pub) == "refreshed"
    assert copy.read_bytes() == (priv / rel).read_bytes()


@pytest.mark.macos  # APFS is case-insensitive: the two names are one file in the view
def test_names_differing_only_in_letter_case_across_the_halves_are_refused(
    tmp_path: Path,
) -> None:
    """Round 11, B2: refused by name, never left stale with a 'run it again' message."""
    pub, priv = _small(tmp_path)
    rel = _with_email(pub, priv)
    (pub / "eml").mkdir(exist_ok=True)
    (pub / "eml" / Path(rel).name.upper()).write_bytes(b"other\r\n")
    with pytest.raises(InvalidInputError, match="both the public and the private set"):
        mg.refresh_merged(pub)


@pytest.mark.parametrize("what", ["manifest.json", "cases/.z9.md", "cases/sub/x.md", ".x.tmp"])
def test_anything_else_in_the_merged_view_is_stale_and_swept(tmp_path: Path, what: str) -> None:
    """Round 12, B2: the view holds its copied folders' plain files, the four set files and
    merge.json, nothing else: a planted file is never read as part of the set."""
    pub, _priv = _small(tmp_path)
    mg.refresh_merged(pub)
    m = mg.paths(pub).merged
    assert m is not None
    planted = m / what
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.parent.chmod(0o700)  # the merged folders are made read-only to writers
    planted.write_text("{}")
    assert not mg.is_fresh(mg.paths(pub))
    assert mg.refresh_merged(pub) == "refreshed"
    assert not planted.exists() and mg.is_fresh(mg.paths(pub))


@pytest.mark.macos  # APFS keeps a replaced file's stored name: the case-only rename case
def test_a_source_renamed_only_in_letter_case_refreshes_at_once(tmp_path: Path) -> None:
    """Round 12, B1: the copy takes the new name; no false 'a source file changed' error."""
    pub, priv = _small(tmp_path)
    rel = _with_email(pub, priv)
    src = priv / rel
    src.rename(src.with_name(src.name.upper()))
    assert mg.refresh_merged(pub) == "refreshed"
    m = mg.paths(pub).merged
    assert m is not None
    assert src.name.upper() in os.listdir(m / "eml") and mg.is_fresh(mg.paths(pub))


def test_a_generated_card_is_refused_in_a_private_half_and_makes_the_set_generated(
    tmp_path: Path,
) -> None:
    """Round 13, B1: generated-ness is the whole set's (P1, P7), and a private build refuses a
    generated card outright."""
    from ecf.eval import genset  # noqa: PLC0415
    from ecf.eval.builder import build_all  # noqa: PLC0415
    from tests.eval_checkout import INJ_TEST, _inj_test  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]

    pub, priv = _small(tmp_path)
    mg.refresh_merged(pub)
    assert not genset.is_generated(pub)
    rows = [json.loads(x) for x in (priv / mg.LABELS).read_text().splitlines() if x.strip()]
    rows[0] |= {"author": "generated"}
    (priv / mg.LABELS).write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert genset.is_generated(pub)  # the private half's row counts for the whole set
    card = _inj_test().replace("author: claude\n", "author: generated\ngenerator: test:model\n", 1)
    for c in (priv / "cases").glob("*.md"):
        c.unlink()
    (priv / "cases" / f"{INJ_TEST}.md").write_text(card)
    with pytest.raises(InvalidInputError, match="generated card can't be in a private set"):
        build_all(priv)


@pytest.mark.parametrize("what", ["eml/{name}", "eml/{upper}", "eml/.{name}.tmp"])
def test_a_planted_folder_under_a_copys_name_never_blocks_the_refresh(
    tmp_path: Path, what: str
) -> None:
    """Round 13, B2: extras go before any copy is placed; a read-only one too."""
    pub, priv = _small(tmp_path)
    rel = _with_email(pub, priv)
    m = mg.paths(pub).merged
    assert m is not None
    name = Path(rel).name
    (m / "eml").chmod(0o700)
    (m / rel).chmod(0o600)
    (m / rel).unlink()
    planted = m / what.format(name=name, upper=name.upper())
    planted.mkdir(parents=True)
    (planted / "inner").mkdir()
    (planted / "inner" / "f").write_text("x")
    (planted / "inner").chmod(0o500)
    assert mg.refresh_merged(pub) == "refreshed"
    assert (m / rel).read_bytes() == (priv / rel).read_bytes() and mg.is_fresh(mg.paths(pub))


def test_a_rename_in_unicode_form_only_refreshes_at_once(tmp_path: Path) -> None:
    """Round 13, B3: a name as the volume compares it (letter case and Unicode form)."""
    import unicodedata  # noqa: PLC0415

    pub, priv = _small(tmp_path)
    (priv / "eml").mkdir(exist_ok=True)
    nfc = unicodedata.normalize("NFC", "café.eml")
    (priv / "eml" / nfc).write_bytes(b"x\r\n")
    mg.refresh_merged(pub)
    nfd = unicodedata.normalize("NFD", nfc)
    (priv / "eml" / nfc).rename(priv / "eml" / nfd)
    assert mg.refresh_merged(pub) in ("refreshed", "fresh")
    assert mg.is_fresh(mg.paths(pub))


@pytest.mark.parametrize("mode", [0o000, 0o100])
@pytest.mark.parametrize("where", ["top", "inner"])
def test_an_unreadable_planted_folder_is_removed_never_crashing_the_refresh(
    tmp_path: Path, mode: int, where: str
) -> None:
    """Round 14, B1: a folder that can't be read or listed is still removed (or named)."""
    pub, priv = _small(tmp_path)
    rel = _with_email(pub, priv)
    m = mg.paths(pub).merged
    assert m is not None
    (m / "eml").chmod(0o700)
    planted = m / "eml" / "x.eml"
    (planted / "inner").mkdir(parents=True)
    (planted / "inner" / "f").write_text("x")
    (planted if where == "top" else planted / "inner").chmod(mode)
    assert mg.refresh_merged(pub) == "refreshed"
    assert not planted.exists() and (m / rel).is_file() and mg.is_fresh(mg.paths(pub))


def test_generated_ness_is_the_same_from_either_halfs_path(tmp_path: Path) -> None:
    """Round 14, B2: a generated row in the public half makes the set generated from the
    private half's path too (the label view's private pass goes blind as well)."""
    from ecf.eval import genset  # noqa: PLC0415

    pub, priv = _small(tmp_path)
    rows = [json.loads(x) for x in (pub / mg.LABELS).read_text().splitlines() if x.strip()]
    rows[0] |= {"author": "generated"}
    (pub / mg.LABELS).write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert genset.is_generated(pub) and genset.is_generated(priv)


def test_one_name_in_two_unicode_forms_across_the_halves_is_refused_by_name(
    tmp_path: Path,
) -> None:
    """Round 14, B3: the halves are compared as the volume compares names."""
    import unicodedata  # noqa: PLC0415

    pub, priv = _small(tmp_path)
    nfc, nfd = (unicodedata.normalize("NFC", "caf\u00e9.eml"),
                unicodedata.normalize("NFD", "caf\u00e9.eml"))  # fmt: skip
    for half, name in ((pub, nfc), (priv, nfd)):
        (half / "eml").mkdir(exist_ok=True)
        (half / "eml" / name).write_bytes(name.encode())
    with pytest.raises(InvalidInputError, match="both the public and the private set"):
        mg.refresh_merged(pub)


@pytest.mark.parametrize("which", [".", "cases", "eml"])
def test_an_unreadable_folder_of_the_view_is_repaired_by_the_refresh(
    tmp_path: Path, which: str
) -> None:
    """Round 15, B1: a view folder set to 000 is stale for readers and the refresh repairs it,
    never a traceback."""
    pub, priv = _small(tmp_path)
    _with_email(pub, priv)
    m = mg.paths(pub).merged
    assert m is not None
    (m / which).chmod(0o000)
    try:
        assert not mg.is_fresh(mg.paths(pub))
        assert mg.refresh_merged(pub) == "refreshed"
        assert mg.is_fresh(mg.paths(pub))
    finally:
        if (m / which).exists():
            (m / which).chmod(0o755)


def test_a_planted_tree_deeper_than_the_recursion_limit_is_removed(tmp_path: Path) -> None:
    """Round 15, B2: removal walks with a stack, so depth can't escape as RecursionError (where
    PATH_MAX allows it, as on Linux; here the limit is lowered instead)."""
    import sys  # noqa: PLC0415

    pub, _priv = _small(tmp_path)
    mg.refresh_merged(pub)
    m = mg.paths(pub).merged
    assert m is not None
    deep = m / "zz"
    leaf = deep
    for _ in range(150):
        leaf = leaf / "a"
    leaf.mkdir(parents=True)
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(120)
    try:
        assert mg.refresh_merged(pub) == "refreshed"
    finally:
        sys.setrecursionlimit(old)
    assert not deep.exists()
