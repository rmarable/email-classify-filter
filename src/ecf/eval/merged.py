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
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.eval import labels as label_file
from ecf.eval.setio import MERGE_FILE, MERGED_DIR, canonical, is_merged
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

BUSY = ("the set is being read (by an eval run: see `ecf eval status`; or for a moment by the"
        " service): try again, or run `ecf eval build` after the run ends")  # fmt: skip
CLI_WAIT_S = 2.0  # a writer's refresh waits out the service's brief reads (57-190 ms measured)


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
    """`<top>` when `root` is a checkout's `<top>/tests/eval/<set>`, by what the folder is
    (`setio.canonical`: symlinks followed, stored letter case), never by how it was typed;
    else None. No `git` call."""
    c = canonical(root)
    if c.parent.name == "eval" and c.parent.parent.name == "tests":
        return c.parents[2]
    return None


def paths(root: Path) -> SetPaths:
    """A set's halves. A set in a checkout is named by its canonical path; any other set (a
    temporary folder) by its path as given."""
    top = repo_top(root)
    if top is None:
        return SetPaths(_logical(root), None, None)
    pub = canonical(root)
    priv = top / "eval" / "private" / pub.name
    return SetPaths(pub, top, priv if priv.is_dir() else None)


def is_private_root(root: Path) -> bool:
    """Whether `root` is the private half of a set, `<top>/eval/private/<set>` (canonical)."""
    c = canonical(root)
    return c.parent.name == "private" and c.parent.parent.name == "eval"


def public_of(root: Path) -> Path:
    """The public path of either half: `<top>/eval/private/<set>` gives `<top>/tests/eval/<set>`;
    a public path is returned as it is."""
    if is_private_root(root):
        c = canonical(root)
        return c.parents[2] / "tests" / "eval" / c.name
    return _logical(root)


def card_ids(root: Path) -> list[str]:
    """The set's card IDs, from its file names (the builder makes each card's ID its stem)."""
    d = root / "cases"
    return sorted(p.stem for p in d.glob("*.md")) if d.is_dir() else []


def halves(root: Path) -> list[Path]:
    """The set's halves for a writer: the public set, then the private one when present."""
    sp = paths(root)
    return [sp.public] + ([sp.private] if sp.private is not None else [])


def owners(root: Path) -> dict[str, Path]:
    """Which half owns each card, by its file (a card in both halves fails the merge)."""
    return {i: h for h in halves(root) for i in card_ids(h)}


def owner(root: Path, case_id: str) -> Path:
    """The half that owns `case_id`: a single-ID command writes there, never the merged view."""
    found = owners(root).get(case_id)
    if found is None:
        raise InvalidInputError(f"{case_id}: no card for it in {root} or its private set")
    return found


def refresh_after_write(root: Path) -> str | None:
    """A CLI writer's refresh at its end, in a `finally`: None when done (or no private half),
    else the refusal, which the caller prints as a note after its own result."""
    if paths(root).private is None:
        return None
    try:
        refresh_merged(root, wait=CLI_WAIT_S)
    except Exception as exc:  # never masks the writer's own result or error (C89)
        return f"note: the merged set wasn't refreshed: {exc or type(exc).__name__}"
    return None


def membership(root: Path) -> tuple[frozenset[str], frozenset[str]] | None:
    """Each half's card IDs, from its label file (no card is read), for a set in a checkout; the
    private half is empty when it is absent, so a run there records `private_cases` 0 and the
    gate refuses it. None for a set outside a checkout (EVAL-PRIVATE.md, "The gate guard")."""
    sp = paths(root)
    if sp.top is None:
        return None
    pub = frozenset(i for i, _ in _jsonl_rows(sp.public / LABELS))
    if sp.private is None:
        return pub, frozenset()
    priv = frozenset(i for i, _ in _jsonl_rows(sp.private / LABELS))
    return pub, priv


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


def stray(kind: str, ids: list[str] | set[str], own: set[str], other: set[str]) -> list[str]:
    """The IDs one half's `kind` file may not name (the rule above, by kind): in a label file or
    `checks.yaml` any ID that isn't a card of that half; in `reviews.jsonl` only a card of the
    other half. The build and the card scan both ask this one function (review round 4, B1)."""
    if kind == REVIEWS:
        return sorted({i for i in ids if i in other})
    return sorted({i for i in ids if i not in own})


def ids_in(kind: str, text: str, source: str) -> list[str]:
    """The ID each row of a `.jsonl` file or each top-level key of `checks.yaml` names, parsed
    as the merge reads them (`checks.yaml` through `ecf.yamlio`, which refuses duplicate keys;
    review round 4, B2). Raises InvalidInputError when the file doesn't parse."""
    if kind == CHECKS:
        try:
            data: object = load_yaml(text, source=source) or {}
        except Exception as exc:  # a YAML error of any kind: the file is refused
            raise InvalidInputError(f"{source}: doesn't parse ({exc})") from exc
        if not isinstance(data, dict):
            raise InvalidInputError(f"{source}: must be a mapping of card IDs")
        return [str(k) for k in cast("dict[Any, Any]", data)]
    out: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip():
            try:
                out.append(str(json.loads(line)["id"]))
            except (ValueError, KeyError, TypeError) as exc:
                raise InvalidInputError(f"{source}: line {n} doesn't parse") from exc
    return out


def _owned(kind: str, ids: list[str], own: set[str], other: set[str], where: str) -> None:
    bad = stray(kind, ids, own, other)
    if bad:
        what = "the other set's cards" if kind == REVIEWS else "cards not in that set"
        raise InvalidInputError(f"{where} {kind}: rows for {what}: {', '.join(bad[:10])}")


def _checks(path: Path) -> tuple[str, dict[str, Any]]:
    if not path.is_file():
        return "", {}
    text = path.read_text(encoding="utf-8")
    data: object = load_yaml(text, source=str(path)) or {}  # refuses duplicate keys
    if not isinstance(data, dict):
        raise InvalidInputError(f"{path}: must be a mapping of card IDs")
    return text, dict(cast("dict[str, Any]", data))


def _merged_checks(public: Path, private: Path, pub_ids: set[str], priv_ids: set[str], *,
                   validate: bool) -> bytes | None:  # fmt: skip
    """checks.yaml merged: the public file, then the private one's keys (its header dropped)."""
    if validate:
        (ta, da), (tb, db) = _checks(public / CHECKS), _checks(private / CHECKS)
        _owned(CHECKS, list(da), pub_ids, priv_ids, "public")
        _owned(CHECKS, list(db), priv_ids, pub_ids, "private")
    else:
        ta, tb = _text(public / CHECKS), _text(private / CHECKS)
    if not ta and not tb:
        return None
    text = ta + ("" if not ta or ta.endswith("\n") else "\n") + _body(tb)
    if validate:
        load_yaml(text, source="merged checks.yaml")  # duplicate keys refuse
    return text.encode()


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _body(text: str) -> str:
    """A checks.yaml without its leading comment lines (the merged file keeps the public header)."""
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        i += 1
    return "".join(lines[i:])


def merge_sources(public: Path, private: Path, kinds: tuple[str, ...] = FILES, *,
                  validate: bool = True) -> dict[str, bytes | None]:  # fmt: skip
    """The merged content of each file in `kinds` (None when neither half has the file).
    `validate` (the build) refuses rows and keys of the other half and duplicate keys; the
    freshness check only compares bytes, so it skips the YAML parse."""
    pub_ids, priv_ids = set(card_ids(public)), set(card_ids(private))
    both = sorted(pub_ids & priv_ids)
    if both:
        raise InvalidInputError(f"cards in both the public and the private set: {', '.join(both)}")
    out: dict[str, bytes | None] = {}
    for kind in kinds:
        if kind in LABEL_FILES:
            a, b = _jsonl_rows(public / kind), _jsonl_rows(private / kind)
            _owned(kind, [i for i, _ in a], pub_ids, priv_ids, "public")
            _owned(kind, [i for i, _ in b], priv_ids, pub_ids, "private")
            if not (public / kind).is_file() and not (private / kind).is_file():
                out[kind] = None
                continue
            rows = sorted(a + b, key=lambda r: r[0])
            out[kind] = "".join(line + "\n" for _, line in rows).encode()
        elif kind == CHECKS:
            out[kind] = _merged_checks(public, private, pub_ids, priv_ids, validate=validate)
        elif kind == REVIEWS:
            pa, pb = public / kind, private / kind
            a, b = _jsonl_rows(pa), _jsonl_rows(pb)
            _owned(kind, [i for i, _ in a], pub_ids, priv_ids, "public")
            _owned(kind, [i for i, _ in b], priv_ids, pub_ids, "private")
            if not pa.is_file() and not pb.is_file():
                out[kind] = None
                continue
            out[kind] = "".join(line + "\n" for _, line in a + b).encode()
        else:
            raise ValueError(kind)
    return out


# The manifest: every source and merged file's path, size, mtime, inode and sha256. A stat match
# reuses the recorded sha256; a mismatch is hashed, so a touched but unchanged file stays fresh.


def _stat(p: Path) -> tuple[int, int, int]:
    st = p.stat()
    return st.st_size, st.st_mtime_ns, st.st_ino


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# A stat taken within RACY_NS of now can't vouch for the content: a same-size write in the same
# timestamp tick leaves it unchanged (git's "racy" case; coarse clocks on some file systems). Such
# an entry is recorded with mtime RACY, which no stat equals, so it is hashed again next time
# (review round 4, B6).
RACY_NS = 2_000_000_000
RACY = -1


def sha256_of(p: Path) -> str:
    """A file's sha256, or "" when it can't be read (a key part, never an error)."""
    try:
        return _sha(p)
    except OSError:
        return ""


def is_racy(mtime_ns: int) -> bool:
    return mtime_ns >= time.time_ns() - RACY_NS


def _entry(size: int, mtime: int, ino: int, sha: str) -> list[Any]:
    return [size, RACY if is_racy(mtime) else mtime, ino, sha]


def _manifest(root: Path, prev: dict[str, list[Any]]) -> dict[str, list[Any]]:
    """Each file's [size, mtime, inode, sha256], from one scandir per folder; a file whose stat
    matches its previous entry keeps that entry's sha256 unhashed."""
    out: dict[str, list[Any]] = {}
    for rel, (p, size, mtime, ino) in _stats(root).items():
        old = prev.get(rel)
        out[rel] = old if old is not None and old[:3] == [size, mtime, ino] else _entry(
            size, mtime, ino, _sha(p))  # fmt: skip
    return out


def _read_merge(merged: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads((merged / MERGE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    d = cast("dict[str, Any]", doc)
    return d if d.get("format") == FORMAT else None


def file_stats(root: Path) -> dict[str, tuple[Path, int, int, int]]:
    """Every card, email and set file of a set, with (path, size, mtime, inode): one scandir per
    folder (the gate's cache key and the freshness check)."""
    return _stats(root)


def _stats(root: Path) -> dict[str, tuple[Path, int, int, int]]:
    """`_walk`'s files with their stats, one `scandir` per folder (the freshness check's floor)."""
    out: dict[str, tuple[Path, int, int, int]] = {}
    for d in (*COPIED, LINKED):
        try:
            entries = list(os.scandir(root / d))
        except (FileNotFoundError, NotADirectoryError):
            continue
        for e in entries:
            if not e.name.startswith(".") and e.is_file():
                st = e.stat()
                out[f"{d}/{e.name}"] = (Path(e.path), st.st_size, st.st_mtime_ns, st.st_ino)
    for f in FILES:
        try:
            st = (root / f).stat()
        except FileNotFoundError:
            continue
        out[f] = (root / f, st.st_size, st.st_mtime_ns, st.st_ino)
    return out


def _same(root: Path, recorded: dict[str, list[Any]],
          restat: list[tuple[str, str, str]] | None = None, side: str = "") -> bool:  # fmt: skip
    """Whether `root`'s files are the ones `recorded`: same names, and each file's stat or, on a
    stat mismatch, its sha256. A file matched by its sha256 alone is added to `restat`, so a
    refresh can record its new stat and stop hashing it."""
    now = _stats(root)
    if now.keys() != recorded.keys():
        return False
    for rel, (p, size, mtime, ino) in now.items():
        rec = recorded[rel]
        if (size, mtime, ino) == (rec[0], rec[1], rec[2]):
            continue
        try:
            if _sha(p) != rec[3]:
                return False
        except OSError:
            return False
        if restat is not None:
            restat.append((side, rel, rec[3]))
    return True


def is_fresh(sp: SetPaths, restat: list[tuple[str, str, str]] | None = None) -> bool:
    """Whether the merged view equals what its two sources would give now. All four derived files
    (label files, checks.yaml, reviews.jsonl) are recomputed in memory and compared byte for
    byte, so neither a consistent hand edit of the merged files and `merge.json` nor a source
    changed while a refresh ran passes. `restat` collects (side, file, sha) matched by hash
    alone."""
    if sp.private is None or sp.merged is None:
        return False
    doc = _read_merge(sp.merged)
    if doc is None:
        return False
    man = doc.get("manifest", {})
    if not (_same(sp.public, man.get("public", {}), restat, "public")
            and _same(sp.private, man.get("private", {}), restat, "private")
            and _same(sp.merged, man.get("merged", {}), restat, "merged")):  # fmt: skip
        return False
    try:
        want = merge_sources(sp.public, sp.private, FILES, validate=False)
    except (InvalidInputError, OSError, ValueError, KeyError):
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
    ready: set[Path] = set()
    for half, man in halves:
        for rel, ent in man.items():
            if rel in FILES:
                continue
            dst = m / rel
            keep.add(rel)
            if dst.parent not in ready:  # once per folder, not per file
                _writable(dst.parent)
                ready.add(dst.parent)
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
    m = sp.merged
    _writable(m)
    prev = _read_merge(m) or {}
    (m / MERGE_FILE).unlink(missing_ok=True)  # stale until written again, last
    prev_man: dict[str, dict[str, list[Any]]] = prev.get("manifest", {})
    # the sources' manifests before their content: a writer changing a source after this point
    # leaves it newer than its manifest, so the view reads stale and is rebuilt, never fresh
    pub_man = _manifest(sp.public, prev_man.get("public", {}))
    priv_man = _manifest(sp.private, prev_man.get("private", {}))
    content = merge_sources(sp.public, sp.private)
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
    for rel, (p, *_s) in _stats(m).items():  # orphans last
        if rel not in keep:
            p.chmod(0o644)
            p.unlink()
    merged_man = _manifest(m, prev_merged)
    copied = {**pub_man, **priv_man}
    moved = sorted(
        rel
        for rel, ent in copied.items()
        if rel not in FILES and merged_man.get(rel, [None] * 4)[3] != ent[3]
    )
    if moved:  # a source changed under the refresh (and maybe back): stay stale, never fresh
        raise InvalidInputError(
            f"a source file changed while the merged view was refreshed"
            f" ({', '.join(moved[:5])}): run `ecf eval build` again"
        )
    doc = {
        "format": FORMAT,
        "public_version": label_file.set_version(sp.public),
        "private_version": label_file.set_version(sp.private),
        "merged_version": label_file.set_version(m),
        "ids": {"public": card_ids(sp.public), "private": card_ids(sp.private)},
        "manifest": {
            "public": pub_man,
            "private": priv_man,
            "merged": merged_man,
        },
    }
    tmp = _tmp(m / MERGE_FILE)
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, m / MERGE_FILE)


def _restat(sp: SetPaths, restat: list[tuple[str, str, str]]) -> None:
    """Record the new stat of each file `is_fresh` matched by hash alone, if its hash still is the
    recorded one (it is hashed again here, so a write since then leaves the entry stale)."""
    doc = None if sp.private is None or sp.merged is None else _read_merge(sp.merged)
    if doc is None or sp.private is None or sp.merged is None:
        return
    roots = {"public": sp.public, "private": sp.private, "merged": sp.merged}
    man: dict[str, dict[str, list[Any]]] = doc["manifest"]
    for side, rel, sha in restat:
        p = roots[side] / rel
        try:
            size, mtime, ino = _stat(p)
            if _sha(p) != sha:
                continue
        except OSError:
            continue
        man[side][rel] = _entry(size, mtime, ino, sha)
    tmp = _tmp(sp.merged / MERGE_FILE)
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, sp.merged / MERGE_FILE)


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


def refresh_merged(root: Path, *, wait: float = 0.0,
                   on_wait: Callable[[float], None] | None = None) -> str:  # fmt: skip
    """Rebuild the merged view of `root`'s set if it is stale, under the exclusive lock (the one
    writer of the merged folder, and the only one that creates the lock file). Returns "fresh" or
    "refreshed"; raises RefreshBusy when the lock stays held for `wait` seconds."""
    sp = paths(root)
    if sp.private is None or sp.lock is None:
        raise InvalidInputError(f"{sp.public}: no private set to merge (public set only)")
    _writable(sp.lock.parent)
    fd = os.open(sp.lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        t0 = time.monotonic()
        got = _take(fd, fcntl.LOCK_EX, wait)
        if on_wait is not None:
            on_wait(time.monotonic() - t0)
        if not got:
            raise RefreshBusy(BUSY)
        try:
            restat: list[tuple[str, str, str]] = []
            if is_fresh(sp, restat):
                if restat:  # touched, unchanged: record the new stats (manifest only)
                    _restat(sp, restat)
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
    try:
        got = _take(fd, fcntl.LOCK_SH, wait)
    except BaseException:
        os.close(fd)  # never leak the descriptor, whatever `_take` raised
        raise
    if not got:
        os.close(fd)
        raise RefreshBusy("a refresh is running; try again")
    return fd


def release(fd: int | None) -> None:
    if fd is not None:
        os.close(fd)  # closing the descriptor drops its flock


class SetLock:
    """A run's shared lock on its set's merged view, released exactly once (a stop while
    preparing and the end of the run may both release it). `fd` is None with no private half."""

    def __init__(self, fd: int | None) -> None:
        self._fd = fd
        self._guard = threading.Lock()

    @classmethod
    def take(cls, root: Path, *, wait: float = 0.0) -> SetLock:
        return cls(take_shared(root, wait=wait))

    @property
    def held(self) -> bool:
        return self._fd is not None

    def release(self) -> None:
        with self._guard:
            fd, self._fd = self._fd, None
        release(fd)


def _under_private(p: Path) -> bool:
    """At or under some `<top>/eval/private` (letter case ignored: APFS is case-insensitive), or
    inside a merged view."""
    c = canonical(p)
    for d in (c, *c.parents):
        if d.name == "private" and d.parent.name == "eval":
            return True
    return is_merged(c)


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
