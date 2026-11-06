"""The public and private halves of an eval set, and the merged view every reader uses
(planning-docs/EVAL-PRIVATE.md, "The overlay", V1, W1 OD-647, W2 OD-648).

Each set name has a public half, `<top>/tests/eval/<set>`, and may have a private half,
`<top>/eval/private/<set>` (a separate, private repository). Writers write to the half that owns
a card; `refresh_merged` builds `<top>/eval/private/.merged/<set>/`, a full set with both halves,
which `resolve` hands to every reader once it has checked the view is fresh. `merge.json` is a
cache, not a trust root: freshness is checked against the sources, and the merged label files are
recomputed in memory and compared byte for byte."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import shutil
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.eval import labels as label_file
from ecf.eval.setio import MERGE_FILE, MERGED_DIR, is_merged
from ecf.yamlio import load_yaml

FORMAT = 1
LABELS, FIXED, CHECKS, REVIEWS = (
    "labels.jsonl",
    "fixed_labels.jsonl",
    "checks.yaml",
    "reviews.jsonl",
)
FILES = (LABELS, FIXED, CHECKS, REVIEWS)
LABEL_FILES = (LABELS, FIXED)
COPIED = ("cases", "eml")  # plain copies, made read-only
LINKED = ".build"  # large: a clone, else a hard link, else a copy
POLL_S = 0.02

BUSY = ("a run is reading this set (see `ecf eval status`); run `ecf eval build` after it ends,"
        " or `ecf eval stop` it")  # fmt: skip


class RefreshBusy(InvalidInputError):
    """The merged view's lock is held (a run reads the set, or another refresh is running)."""


@dataclass(frozen=True)
class SetPaths:
    """Where one set's halves and merged view live. `top` is None for a root that isn't
    `<top>/tests/eval/<set>` (a set in a temporary folder, read as a plain set)."""

    public: Path
    top: Path | None
    private: Path | None  # None when the private half is absent

    @property
    def name(self) -> str:
        return self.public.name

    @property
    def merged(self) -> Path | None:
        return None if self.top is None else self.top / "eval" / "private" / MERGED_DIR / self.name

    @property
    def lock(self) -> Path | None:
        m = self.merged
        return None if m is None else m.with_name(f"{self.name}.lock")


def _logical(root: Path) -> Path:
    return Path(os.path.abspath(root))  # no symlink resolution: the path as given


def repo_top(root: Path) -> Path | None:
    """`<top>` for a logical `<top>/tests/eval/<set>` path, else None. No `git` call."""
    p = _logical(root)
    if p.parent.name == "eval" and p.parent.parent.name == "tests":
        return p.parents[2]
    return None


def paths(root: Path) -> SetPaths:
    p = _logical(root)
    top = repo_top(p)
    priv = None if top is None else top / "eval" / "private" / p.name
    return SetPaths(p, top, priv if priv is not None and priv.is_dir() else None)


def is_private_root(root: Path) -> bool:
    """Whether `root` is the private half of a set, `<top>/eval/private/<set>`."""
    p = _logical(root)
    return p.parent.name == "private" and p.parent.parent.name == "eval"


def card_ids(root: Path) -> list[str]:
    """The set's card IDs, from its file names (the builder makes each card's ID its stem)."""
    d = root / "cases"
    return sorted(p.stem for p in d.glob("*.md")) if d.is_dir() else []


# Merging the label and check files. Label files hold current state, so a row naming a card that
# isn't in its own half is refused; reviews.jsonl is append-only history, so only a row naming the
# other half's card is refused (a card removed later keeps its own history).


def _jsonl_rows(path: Path) -> list[tuple[str, str]]:
    if not path.is_file():
        return []
    out: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append((str(json.loads(line)["id"]), line))
    return out


def _owned(rows: list[tuple[str, str]], ids: set[str], where: str, file: str) -> None:
    stray = sorted({i for i, _ in rows if i not in ids})
    if stray:
        raise InvalidInputError(f"{where} {file}: rows for cards not in that set:"
                                f" {', '.join(stray[:10])}")  # fmt: skip


def _checks(path: Path) -> tuple[str, dict[str, Any]]:
    if not path.is_file():
        return "", {}
    text = path.read_text(encoding="utf-8")
    data: object = load_yaml(text, source=str(path)) or {}  # refuses duplicate keys
    if not isinstance(data, dict):
        raise InvalidInputError(f"{path}: must be a mapping of card IDs")
    return text, dict(cast("dict[str, Any]", data))


def _body(text: str) -> str:
    """A checks.yaml without its leading comment lines (the merged file keeps the public header)."""
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        i += 1
    return "".join(lines[i:])


def merge_sources(public: Path, private: Path,
                  kinds: tuple[str, ...] = FILES) -> dict[str, bytes | None]:  # fmt: skip
    """The merged content of each file in `kinds` (None when neither half has the file).
    `refresh_merged` asks for all four; the gate and the resolver only for the two label files."""
    pub_ids, priv_ids = set(card_ids(public)), set(card_ids(private))
    both = sorted(pub_ids & priv_ids)
    if both:
        raise InvalidInputError(f"cards in both the public and the private set: {', '.join(both)}")
    out: dict[str, bytes | None] = {}
    for kind in kinds:
        if kind in LABEL_FILES:
            a, b = _jsonl_rows(public / kind), _jsonl_rows(private / kind)
            _owned(a, pub_ids, "public", kind)
            _owned(b, priv_ids, "private", kind)
            if not (public / kind).is_file() and not (private / kind).is_file():
                out[kind] = None
                continue
            rows = sorted(a + b, key=lambda r: r[0])
            out[kind] = "".join(line + "\n" for _, line in rows).encode()
        elif kind == CHECKS:
            (ta, da), (tb, db) = _checks(public / kind), _checks(private / kind)
            _owned([(k, "") for k in da], pub_ids, "public", kind)
            _owned([(k, "") for k in db], priv_ids, "private", kind)
            if not ta and not tb:
                out[kind] = None
                continue
            body = _body(tb)
            text = ta + ("" if not ta or ta.endswith("\n") else "\n") + body
            load_yaml(text, source="merged checks.yaml")  # duplicate keys refuse
            out[kind] = text.encode()
        elif kind == REVIEWS:
            pa, pb = public / kind, private / kind
            a, b = _jsonl_rows(pa), _jsonl_rows(pb)
            for rows, other, where in ((a, priv_ids, "public"), (b, pub_ids, "private")):
                stray = sorted({i for i, _ in rows if i in other})
                if stray:
                    raise InvalidInputError(f"{where} {kind}: rows for the other set's cards:"
                                            f" {', '.join(stray[:10])}")  # fmt: skip
            if not pa.is_file() and not pb.is_file():
                out[kind] = None
                continue
            out[kind] = "".join(line + "\n" for _, line in a + b).encode()
        else:
            raise ValueError(kind)
    return out


# The manifest: every source and merged file's path, size, mtime, inode and sha256. A stat match
# reuses the recorded sha256; a mismatch is hashed, so a touched but unchanged file stays fresh.


def _walk(root: Path) -> Iterator[Path]:
    for d in (*COPIED, LINKED):
        sub = root / d
        if sub.is_dir():
            yield from sorted(
                p for p in sub.iterdir() if p.is_file() and not p.name.startswith(".")
            )
    for f in FILES:
        if (root / f).is_file():
            yield root / f


def _stat(p: Path) -> tuple[int, int, int]:
    st = p.stat()
    return st.st_size, st.st_mtime_ns, st.st_ino


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _entry(p: Path, prev: list[Any] | None) -> list[Any]:
    size, mtime, ino = _stat(p)
    if prev is not None and prev[:3] == [size, mtime, ino]:
        return prev
    return [size, mtime, ino, _sha(p)]


def _manifest(root: Path, prev: dict[str, list[Any]]) -> dict[str, list[Any]]:
    return {str(p.relative_to(root)): _entry(p, prev.get(str(p.relative_to(root))))
            for p in _walk(root)}  # fmt: skip


def _read_merge(merged: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads((merged / MERGE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    d = cast("dict[str, Any]", doc)
    return d if d.get("format") == FORMAT else None


def _same(root: Path, recorded: dict[str, list[Any]]) -> bool:
    """Whether `root`'s files are the ones `recorded`: same names, and each file's stat or, on a
    stat mismatch, its sha256."""
    now = {str(p.relative_to(root)): p for p in _walk(root)}
    if set(now) != set(recorded):
        return False
    for rel, p in now.items():
        rec = recorded[rel]
        try:
            if list(_stat(p)) != rec[:3] and _sha(p) != rec[3]:
                return False
        except OSError:
            return False
    return True


def is_fresh(sp: SetPaths) -> bool:
    """Whether the merged view equals what its two sources would give now. The two label files
    are recomputed in memory and compared byte for byte, so a consistent hand edit of the merged
    files and `merge.json` is still refused."""
    if sp.private is None or sp.merged is None:
        return False
    doc = _read_merge(sp.merged)
    if doc is None:
        return False
    man = doc.get("manifest", {})
    if not (_same(sp.public, man.get("public", {})) and _same(sp.private, man.get("private", {}))
            and _same(sp.merged, man.get("merged", {}))):  # fmt: skip
        return False
    try:
        want = merge_sources(sp.public, sp.private, LABEL_FILES)
    except InvalidInputError:
        return False
    for kind, data in want.items():
        p = sp.merged / kind
        if (data is None) != (not p.is_file()) or (data is not None and p.read_bytes() != data):
            return False
    return doc.get("merged_version") == label_file.set_version(sp.merged)


# Writing the merged view. Crash-safe: a stale temporary file is removed before it is made, a copy
# is made read-only only after its rename, folders are made writable before writing, and
# merge.json is written last, so a refresh killed part way leaves a stale view, never a fresh one.


def _clonefile() -> Any:
    if sys.platform != "darwin":
        return None
    try:
        fn = ctypes.CDLL(None, use_errno=True).clonefile
    except (OSError, AttributeError):
        return None
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
    fn.restype = ctypes.c_int
    return fn


_CLONE = _clonefile()


def _tmp(dst: Path) -> Path:
    t = dst.with_name(f".{dst.name}.tmp")
    t.unlink(missing_ok=True)
    return t


def _place_copy(src: Path, dst: Path, data: bytes | None = None) -> None:
    tmp = _tmp(dst)
    if data is None:
        shutil.copyfile(src, tmp)
    else:
        tmp.write_bytes(data)
    os.replace(tmp, dst)
    dst.chmod(0o444)


def _place_link(src: Path, dst: Path) -> None:
    """An APFS clone where available (to a temporary name, since clonefile refuses an existing
    destination), else a hard link, else a copy."""
    tmp = _tmp(dst)
    if _CLONE is not None and _CLONE(str(src).encode(), str(tmp).encode(), 0) == 0:
        os.replace(tmp, dst)
        return
    try:
        os.link(src, tmp)
    except OSError:
        shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def _writable(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o755)


def _copy_cards(m: Path, halves: tuple[tuple[Path, dict[str, list[Any]]], ...],
                prev_merged: dict[str, list[Any]]) -> set[str]:  # fmt: skip
    """Copy (or link, for `.build/`) every card and email file of both halves into `m`, skipping
    a file whose merged copy is unchanged since the last refresh. Returns the paths placed."""
    keep: set[str] = set()
    for half, man in halves:
        for rel, ent in man.items():
            if rel in FILES:
                continue
            dst = m / rel
            keep.add(rel)
            _writable(dst.parent)
            old = prev_merged.get(rel)
            if (
                old is not None
                and old[3] == ent[3]
                and dst.is_file()
                and list(_stat(dst)) == old[:3]
            ):
                continue
            if rel.startswith(LINKED + "/"):
                _place_link(half / rel, dst)
            else:
                _place_copy(half / rel, dst)
    return keep


def _build(sp: SetPaths) -> None:
    if sp.private is None or sp.merged is None:
        raise InvalidInputError(f"{sp.public}: no private set to merge")
    content = merge_sources(sp.public, sp.private)
    m = sp.merged
    _writable(m)
    prev = _read_merge(m) or {}
    (m / MERGE_FILE).unlink(missing_ok=True)  # stale until written again, last
    prev_man: dict[str, dict[str, list[Any]]] = prev.get("manifest", {})
    pub_man = _manifest(sp.public, prev_man.get("public", {}))
    priv_man = _manifest(sp.private, prev_man.get("private", {}))
    prev_merged = prev_man.get("merged", {})
    keep = _copy_cards(m, ((sp.public, pub_man), (sp.private, priv_man)), prev_merged)
    for kind, data in content.items():
        dst = m / kind
        if data is None:
            if dst.exists():
                dst.chmod(0o644)
                dst.unlink()
            continue
        keep.add(kind)
        if not (dst.is_file() and dst.read_bytes() == data):
            _place_copy(dst, dst, data)
    for p in list(_walk(m)):  # orphans last
        rel = str(p.relative_to(m))
        if rel not in keep:
            p.chmod(0o644)
            p.unlink()
    doc = {
        "format": FORMAT,
        "public_version": label_file.set_version(sp.public),
        "private_version": label_file.set_version(sp.private),
        "merged_version": label_file.set_version(m),
        "ids": {"public": card_ids(sp.public), "private": card_ids(sp.private)},
        "manifest": {
            "public": pub_man,
            "private": priv_man,
            "merged": _manifest(m, prev_merged),
        },
    }
    tmp = _tmp(m / MERGE_FILE)
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, m / MERGE_FILE)


def _take(fd: int, op: int, wait: float) -> bool:
    """Take a flock, polling every 20 ms until `wait` seconds have passed; never blocking."""
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, op | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_S)


def refresh_merged(root: Path, *, wait: float = 0.0) -> str:
    """Rebuild the merged view of `root`'s set if it is stale, under the exclusive lock (the one
    writer of the merged folder, and the only one that creates the lock file). Returns "fresh" or
    "refreshed"; raises RefreshBusy when the lock stays held for `wait` seconds."""
    sp = paths(root)
    if sp.private is None or sp.lock is None:
        raise InvalidInputError(f"{sp.public}: no private set to merge (public set only)")
    _writable(sp.lock.parent)
    fd = os.open(sp.lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if not _take(fd, fcntl.LOCK_EX, wait):
            raise RefreshBusy(BUSY)
        try:
            if is_fresh(sp):
                return "fresh"
            _build(sp)
            return "refreshed"
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def take_shared(root: Path, *, wait: float = 0.0) -> int | None:
    """A reader's shared lock on the set's merged view, as an open descriptor (None when there is
    no private set). Raises when the lock file is missing or the lock stays busy."""
    sp = paths(root)
    if sp.private is None or sp.lock is None:
        return None
    try:
        fd = os.open(sp.lock, os.O_RDONLY)
    except FileNotFoundError as exc:
        raise InvalidInputError(f"{sp.name}: run `ecf eval build` (no merged set yet)") from exc
    if not _take(fd, fcntl.LOCK_SH, wait):
        os.close(fd)
        raise RefreshBusy("a refresh is running; try again")
    return fd


def release(fd: int | None) -> None:
    if fd is not None:
        os.close(fd)  # closing the descriptor drops its flock


def _under_private(p: Path) -> bool:
    """At or under some `<top>/eval/private` (letter case ignored: APFS is case-insensitive), or
    inside a merged view."""
    for d in (p, *p.parents):
        if d.name.lower() == "private" and d.parent.name.lower() == "eval":
            return True
    return is_merged(p)


def _same_dir(a: Path, b: Path) -> bool:
    try:
        sa, sb = a.stat(), b.stat()
    except OSError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def resolve(root: Path) -> Path:
    """The set a reader should read for `root`: the public set when there is no private half;
    the merged view when there is one and it is fresh; refused otherwise. A path at or under
    `eval/private/`, or inside a merged view, is refused: readers pass the logical public path."""
    p = _logical(root)
    real = p.resolve()
    if _under_private(real) or _under_private(p):
        raise InvalidInputError(f"{root}: give the public set's path (tests/eval/<set>), not a"
                                " private or merged one")  # fmt: skip
    sp = paths(p)
    if sp.top is not None:
        priv_dir = sp.top / "eval" / "private"
        if priv_dir.exists() and any(_same_dir(d, priv_dir) for d in (real, *real.parents)):
            raise InvalidInputError(f"{root}: give the public set's path, not a private one")
    if sp.private is None or sp.merged is None:
        return p
    if not is_fresh(sp):
        raise InvalidInputError(f"the merged {sp.name} set is missing or out of date: run"
                                " `ecf eval build`")  # fmt: skip
    return sp.merged
