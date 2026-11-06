"""The merged view of a public and a private eval set (EVAL-PRIVATE.md "The overlay", V1, W1,
W2)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

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
        with pytest.raises(mg.RefreshBusy, match="a run is reading this set"):
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


def test_hard_linked_build_file_written_in_place_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    with pytest.raises(InvalidInputError, match="out of date"):
        mg.resolve(pub)


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
