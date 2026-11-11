"""The public and private halves of an eval set, and the merged view every reader uses
(planning-docs/EVAL-PRIVATE.md, "The overlay", V1, W1 OD-647, W2 OD-648).

Each set name has a public half, `<top>/tests/eval/<set>`, and may have a private half,
`<top>/eval/private/<set>` (a separate, private repository). Writers write to the half that owns
a card; `refresh_merged` builds `<top>/eval/private/.merged/<set>/`, a full set with both halves,
which `resolve` hands to every reader once it has checked the view is fresh. `merge.json` is a
cache, not a trust root: freshness is checked against the sources, and the merged label files are
recomputed in memory and compared byte for byte."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import fcntl
import functools
import hashlib
import json
import os
import shlex
import stat
import sys
import threading
import time
import unicodedata
from collections.abc import Callable, Generator
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.eval import labels as label_file
from ecf.eval.setio import (
    MERGE_FILE,
    MERGED_DIR,
    canonical,
    copy_new,
    is_merged,
    logical,
    reason,
    replace_file,
    write_new,
)
from ecf.yamlio import load_yaml

FORMAT = 2  # 2: manifest entries carry ctime (review round 5, B-4)
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

BUSY = "the set is being read (by an eval run, or for a moment by the service)"
BUSY_ADVICE = "see `ecf eval status`; try again, or run `ecf eval build` after the run ends"
CLI_WAIT_S = 2.0  # a writer's refresh waits out the service's brief reads (57-190 ms measured)


class Unreadable(InvalidInputError):
    """A file or folder of a set (either half, the merged view, its lock) that can't be read or
    written: one refusal naming it, whichever reader or writer met it (review round 16)."""


def unreadable(path: object, exc: OSError) -> Unreadable:
    """One refusal for a read or a write the system refused. It names the part (`_blame`) and
    lists what to check, both the permissions and a lock flag of it and its folder, rather than
    guess which of them is the cause: a refused write can be the folder's permission, the file's
    `uchg` flag or the folder's (review rounds 21-22, B1). A write reports the file it writes,
    never its temporary file (`setio.replace_file`, round 23)."""
    why = reason(exc)
    if not (isinstance(path, (str, bytes, os.PathLike)) and path not in ("", b"")):
        return Unreadable(f"a file of the set: can't be read or written ({why})",
                          advice="fix its permissions and run `ecf eval build`")  # fmt: skip
    where = _blame(Path(os.fsdecode(cast("str | bytes | os.PathLike[str]", path))))
    look = f"ls -ldO {shlex.quote(str(where.parent))} {shlex.quote(str(where))}"
    return Unreadable(f"{where}: can't be read or written ({why})",
                      advice=f"check the permissions and any lock flag of it and its folder"
                             f" (`{look}`), then run `ecf eval build`")  # fmt: skip


def _blame(p: Path) -> Path:
    """What to name for a permission error on `p`: the outermost folder above it that can't be
    searched (the operator can't fix anything under it first); else, when `p` doesn't exist (a
    new file whose write was refused), its folder; else `p` itself (review rounds 20-21, B1: a
    name never points at a file that doesn't exist)."""
    for a in reversed(p.parents):
        if not os.access(a, os.F_OK):
            return p  # nothing more can be told from here
        if not os.access(a, os.X_OK):
            return a
    if not os.path.lexists(p) and os.path.lexists(p.parent):
        return p.parent
    return p


def named[F: Callable[..., Any]](fn: F) -> F:
    """The one rule at each entry point: a permission error anywhere inside (a path check, a
    walk, a read) leaves as `Unreadable` naming the file, never a raw OSError (review round 16;
    `pathlib`'s is_file / is_dir / exists raise on EACCES rather than answer False)."""

    @functools.wraps(fn)
    def wrapper(*a: Any, **k: Any) -> Any:
        try:
            return fn(*a, **k)
        except PermissionError as exc:
            raise unreadable(exc.filename, exc) from exc

    return cast("F", wrapper)


@contextlib.contextmanager
def named_errors() -> Generator[None]:
    """`named` as a block: a reader outside this module (`evalrun.read_set`) gets the one rule."""
    try:
        yield
    except PermissionError as exc:
        raise unreadable(exc.filename, exc) from exc


class RefreshBusy(InvalidInputError):
    """The merged view's lock is held (a run reads the set, or another refresh is running)."""


@dataclass(frozen=True)
class SetPaths:
    """Where one set's halves and merged view live. `top` is None for a root that isn't
    `<top>/tests/eval/<set>` (a set in a temporary folder, read as a plain set)."""

    public: Path
    top: Path | None
    private: Path | None  # None when the private half is absent
    refused: str | None = None  # a link where the set's location is read: never a checkout set

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


def _shaped(p: Path, *, fold: bool) -> bool:
    """Whether `p` is laid out as `<top>/tests/eval/<set>`: by the names as stored (`fold`
    False; on a case-sensitive volume `tests/Eval` is another folder, review round 7, B1), or
    ignoring letter case (`fold` True: only to decide which folders to check for links)."""
    a, b = p.parent.name, p.parent.parent.name
    return (
        (a.casefold(), b.casefold()) == ("eval", "tests") if fold else (a, b) == ("eval", "tests")
    )


def _private_shaped(p: Path) -> bool:
    """Whether `p` is laid out as `<top>/eval/private/<set>`, letter case ignored."""
    return (p.parent.name.casefold(), p.parent.parent.name.casefold()) == ("private", "eval")


def _linked(root: Path, folders: tuple[Path, ...]) -> str | None:
    q = next((q for q in folders if os.path.islink(q)), None)
    return None if q is None else (f"{root}: {q} is a symbolic link; a set's location is never"
                                   " found through a link")  # fmt: skip


def _locate(root: Path) -> tuple[Path, Path | None, str | None]:
    """(the set's path, its checkout top or None, why it is refused or None): the one place a
    set's location is worked out; every path a reader, writer, lock or clean check opens comes
    from it (`SetPaths`). A set's location is never found through a link (EVAL-PRIVATE.md,
    "Finding the private set"; review rounds 6-7, operator decisions 2026-11-06): a link at the
    set folder; at `tests/eval` or `tests` above a set laid out (any letter case) as a
    checkout's; at `eval`, `eval/private` or `eval/private/<set>` above a private half given
    directly; or, for a checkout set, at `eval`, `eval/private`, the private set, `.merged`,
    `.merged/<set>` or its lock file, refuses it before anything is read or written. A link
    above the checkout top (macOS `/tmp`, an alias of the whole checkout) is the same checkout.
    A checkout set is one whose stored names are exactly `tests/eval/<set>` (`setio.canonical`
    gives the stored case on a case-insensitive volume; on a case-sensitive one `tests/Eval` is
    another folder and no checkout set). No `git` call."""
    p = logical(root)
    if os.path.islink(p):
        return p, None, f"{root}: the set folder is a symbolic link; give the set's own path"
    c = canonical(p)
    if (_shaped(p, fold=True) or _shaped(c, fold=True)) and (
        why := _linked(root, (p.parent, p.parent.parent))
    ):
        return p, None, why
    if (_private_shaped(p) or _private_shaped(c)) and (
        why := _linked(root, (p.parent, p.parent.parent))
    ):
        return p, None, why
    if not _shaped(c, fold=False):
        return p, None, None
    top = c.parents[2]
    priv = top / "eval" / "private"
    m = priv / MERGED_DIR
    if why := _linked(root, (top / "eval", priv, priv / c.name, m, m / c.name,
                             m / f"{c.name}.lock")):  # fmt: skip
        return p, None, why
    return c, top, None


def repo_top(root: Path) -> Path | None:
    """`<top>` when `root` is a checkout's `<top>/tests/eval/<set>` reached through no link at
    or below the checkout top (`_locate`); else None."""
    return _locate(root)[1]


@named
def paths(root: Path) -> SetPaths:
    """A set's halves. A set in a checkout is named by its canonical path (stored letter case);
    any other set (a temporary folder) by its path as given; a set found through a link is
    refused (`refused`; no halves beyond the path as given)."""
    pub, top, refused = _locate(root)
    if refused is not None or top is None:
        return SetPaths(pub, None, None, refused)
    priv = top / "eval" / "private" / pub.name
    # the private half only as stored exactly `eval/private/<set>`: on a case-insensitive volume
    # `eval/Private` would answer `is_dir` too (review round 8, B2)
    found = priv.is_dir() and canonical(priv) == priv
    return SetPaths(pub, top, priv if found else None)


def _usable(sp: SetPaths) -> SetPaths:
    if sp.refused is not None:
        raise InvalidInputError(sp.refused)
    return sp


@named
def is_private_root(root: Path) -> bool:
    """Whether `root` is the private half of a set, `<top>/eval/private/<set>`, by its layout as
    given or as stored (letter case ignored), so a link at `eval` can't hide it (review round 7,
    B4); `_locate` refuses such a link for every reader and writer."""
    p = logical(root)
    return _private_shaped(p) or _private_shaped(canonical(p))


@named
def public_of(root: Path) -> Path:
    """The public path of either half: `<top>/eval/private/<set>` gives `<top>/tests/eval/<set>`;
    a public path is returned as it is."""
    if is_private_root(root):
        _usable(paths(root))  # a link at the private set or above it: refused (round 28)
        c = canonical(logical(root))  # the path is_private_root judged (review round 28)
        return c.parents[2] / "tests" / "eval" / c.name
    _usable(paths(root))  # a link where the location is read: refused
    return logical(root)


def card_ids(root: Path) -> list[str]:
    """The set's card IDs, from its file names (the builder makes each card's ID its stem). A
    `cases/` folder that can't be listed is refused by name, never read as empty (round 16)."""
    try:
        names = [e.name for e in os.scandir(root / "cases")]
    except (FileNotFoundError, NotADirectoryError):
        return []
    except PermissionError as exc:
        raise unreadable(root / "cases", exc) from exc
    return sorted(n[:-3] for n in names if n.endswith(".md") and not n.startswith("."))


@named
def halves(root: Path) -> list[Path]:
    """The set's halves for a writer: the public set, then the private one when present."""
    sp = _usable(paths(root))
    return [sp.public] + ([sp.private] if sp.private is not None else [])


@named
def owners(root: Path) -> dict[str, Path]:
    """Which half owns each card, by its file (a card in both halves fails the merge)."""
    return {i: h for h in halves(root) for i in card_ids(h)}


@named
def owner(root: Path, case_id: str) -> Path:
    """The half that owns `case_id`: a single-ID command writes there, never the merged view."""
    found = owners(root).get(case_id)
    if found is None:
        raise InvalidInputError(f"{case_id}: no card for it in {root} or its private set")
    return found


def refresh_after_write(root: Path) -> str | None:
    """A CLI writer's refresh at its end, in a `finally`: None when done (or no private half),
    else the refusal, which the caller prints as a note after its own result."""
    try:
        # inside the try: finding the halves can fail too (a folder made unreadable since the
        # write), and that must not replace the writer's own result either
        if paths(root).private is None:
            return None
        refresh_merged(root, wait=CLI_WAIT_S)
    except Exception as exc:  # never masks the writer's own result or error (C89)
        return f"note: the merged set wasn't refreshed: {reason(exc)}"
    return None


@named
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


def special(path: Path) -> InvalidInputError:
    return InvalidInputError(f"{path}: a symbolic link or special file; a set holds plain"
                             " files and folders only (review round 5, B-1)")  # fmt: skip


def _open_regular(path: Path) -> int:
    """A descriptor on `path`, which must be a regular file: a symbolic link is never followed
    and a pipe never blocks the open (InvalidInputError for either; OSError as usual)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise special(path) from exc
        if isinstance(exc, PermissionError):
            raise unreadable(path, exc) from exc
        raise
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise special(path)
    return fd


def read_regular(path: Path) -> bytes | None:
    """A set file's bytes, None when it is absent; refused when it is a link or special file."""
    try:
        fd = _open_regular(path)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as f:
        return f.read()


def _jsonl_rows(path: Path) -> list[tuple[str, str]]:
    data = read_regular(path)
    if data is None:
        return []
    out: list[tuple[str, str]] = []
    for line in data.decode("utf-8").splitlines():
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
            raise InvalidInputError(f"{source}: doesn't parse ({reason(exc)})") from exc
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
    text = _text(path)
    if not text:
        return "", {}
    data: object = load_yaml(text, source=str(path)) or {}  # refuses duplicate keys
    if not isinstance(data, dict):
        raise InvalidInputError(f"{path}: must be a mapping of card IDs")
    return text, dict(cast("dict[str, Any]", data))


def _merged_checks(public: Path, private: Path, pub_ids: set[str], priv_ids: set[str], *,
                   validate: bool) -> bytes | None:  # fmt: skip
    """checks.yaml merged: the public file, then the private one's keys (its header dropped)."""
    da: dict[str, Any] = {}
    db: dict[str, Any] = {}
    if validate:
        (ta, da), (tb, db) = _checks(public / CHECKS), _checks(private / CHECKS)
        _owned(CHECKS, list(da), pub_ids, priv_ids, "public")
        _owned(CHECKS, list(db), priv_ids, pub_ids, "private")
    else:
        ta, tb = _text(public / CHECKS), _text(private / CHECKS)
    if not ta and not tb:
        return None
    text = ta + ("" if not ta or ta.endswith("\n") else "\n") + _body(tb)
    if validate:  # duplicate keys refuse; the joined text must read as the two halves do, so
        # an indented private key can't nest under the last public one (review round 5, B-2)
        got: object = load_yaml(text, source="merged checks.yaml") or {}
        if got != {**da, **db}:
            raise InvalidInputError("checks.yaml: the halves don't merge as each reads alone"
                                    " (each card ID a top-level key, unindented)")  # fmt: skip
    return text.encode()


def _text(path: Path) -> str:
    data = read_regular(path)
    return "" if data is None else data.decode("utf-8")


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
    folded = {_fold(j) for j in priv_ids}  # as the volume compares names (round 14, B3)
    both = sorted(i for i in pub_ids if _fold(i) in folded)
    if both:
        raise InvalidInputError(f"cards in both the public and the private set: {', '.join(both)}")
    out: dict[str, bytes | None] = {}
    for kind in kinds:
        if kind in LABEL_FILES:
            a, b = _jsonl_rows(public / kind), _jsonl_rows(private / kind)
            _owned(kind, [i for i, _ in a], pub_ids, priv_ids, "public")
            _owned(kind, [i for i, _ in b], priv_ids, pub_ids, "private")
            if not os.path.lexists(public / kind) and not os.path.lexists(private / kind):
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
            if not os.path.lexists(pa) and not os.path.lexists(pb):
                out[kind] = None
                continue
            out[kind] = "".join(line + "\n" for _, line in a + b).encode()
        else:
            raise ValueError(kind)
    return out


# The manifest: every source and merged file's path, size, mtime, inode, sha256 and ctime (an
# entry is [size, mtime, inode, sha256, ctime]). It is a cache only: freshness never reads it
# (round 11); a refresh uses its merged entries to find the copies it can keep, each proven by
# its content first.

Stat = tuple[int, int, int, int]  # size, mtime, inode, ctime


def _stat(p: Path) -> Stat:
    st = os.lstat(p)
    return st.st_size, st.st_mtime_ns, st.st_ino, st.st_ctime_ns


def _key(ent: list[Any]) -> Stat:
    return ent[0], ent[1], ent[2], ent[4]


# Within one refresh (its freshness check and its build, under the exclusive lock) or one set
# read (`one_read`), a file is hashed once: the result is kept by path and full stat, so a file
# changed meanwhile (a new mtime, ctime or inode) is hashed again. Nothing is kept between them
# (rounds 11-12).
_MEMO: ContextVar[dict[tuple[str, int, int, int, int, int], str] | None] = ContextVar(
    "merged_sha_memo", default=None
)


def _sha(p: Path) -> str:
    """A regular file's sha256 (a link or special file raises, never followed)."""
    memo = _MEMO.get()
    fd = _open_regular(p)
    try:
        st = os.fstat(fd)
        key = (str(p), st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, st.st_dev)
        if memo is not None and key in memo:
            return memo[key]
        h = hashlib.sha256()
        while chunk := os.read(fd, 1 << 20):
            h.update(chunk)
    finally:
        os.close(fd)
    if memo is not None:
        memo[key] = h.hexdigest()
    return h.hexdigest()


@contextlib.contextmanager
def one_read() -> Generator[None]:
    """Within one read or refresh, each file is hashed once (`_MEMO`); a nested one shares the
    outer memo. A set read's freshness check and its row checks then hash each email once
    (review round 12, performance B-2)."""
    if _MEMO.get() is not None:
        yield
        return
    token = _MEMO.set({})
    try:
        yield
    finally:
        _MEMO.reset(token)


# A stat taken within RACY_NS of now can't vouch for the content: a same-size write in the same
# timestamp tick leaves it unchanged (git's "racy" case; coarse clocks on some file systems). The
# gate's cache keeps such files' sha256 and checks them on a hit (review round 4, B6); a manifest
# entry is recorded with mtime RACY, which no stat equals.
RACY_NS = 2_000_000_000
RACY = -1


def sha256_of(p: Path) -> str:
    """A file's sha256, or "" when it can't be read (a key part, never an error)."""
    try:
        return _sha(p)
    except (OSError, InvalidInputError):
        return ""


def is_racy(mtime_ns: int, ctime_ns: int = 0) -> bool:
    """Whether a file changed (content: mtime; anything: ctime) within the window, so its stat
    can't vouch for it (review round 7, B5: ctime too, as it is in the key)."""
    return max(mtime_ns, ctime_ns) >= time.time_ns() - RACY_NS


def _entry(st: Stat, sha: str) -> list[Any]:
    size, mtime, ino, ctime = st
    return [size, RACY if is_racy(mtime, ctime) else mtime, ino, sha, ctime]


def _unchanged(dst: Path, old: list[Any], rel: str) -> Stat | None:
    """Whether the merged copy `dst` still holds what `old` records, proven by its size and
    sha256, never by its stat alone (a stat in merge.json is a cache; review rounds 9-10, B1), so
    a refresh copies only what changed and a forged copy is replaced. A link or special file
    there is never unchanged, nor a hard-linked copy of a card or email (only `.build/` files
    are links to their sources; round 9, B3). Returns its stat."""
    try:
        st = _stat(dst)
        if not rel.startswith(LINKED + "/") and os.lstat(dst).st_nlink != 1:
            return None
        if st[0] == old[0] and _sha(dst) == old[3]:
            return st
    except (OSError, InvalidInputError):
        pass
    return None


def _manifest(root: Path, prev: dict[str, list[Any]],
              verified: dict[str, list[Any]] | None = None) -> dict[str, list[Any]]:  # fmt: skip
    """Each file's [size, mtime, inode, sha256, ctime], from one scandir per folder; a file whose
    stat matches its previous entry, or one `verified` (just hashed with this stat), keeps that
    entry's sha256 unhashed."""
    out: dict[str, list[Any]] = {}
    for rel, (p, *st) in _stats(root).items():
        now = cast("Stat", tuple(st))
        seen, old = (verified or {}).get(rel), prev.get(rel)
        if seen is not None and _key(seen) == now:  # hashed with this stat by this refresh
            out[rel] = _entry(now, seen[3])
        elif old is not None and _key(old) == now:
            out[rel] = old
        else:
            out[rel] = _entry(now, _sha(p))
    return out


def _read_merge(merged: Path, *, strict: bool = False) -> dict[str, Any] | None:
    """merge.json's document, None when absent or not one; `strict` (the freshness check) lets
    a permission error out, so it is named rather than read as "no manifest"."""
    try:
        data = read_regular(merged / MERGE_FILE)  # no link followed, a pipe never blocks (round 9)
        doc = json.loads(data) if data is not None else None
    except (Unreadable, PermissionError):
        if strict:
            raise
        return None
    except (OSError, ValueError, InvalidInputError):
        return None
    if not isinstance(doc, dict):
        return None
    d = cast("dict[str, Any]", doc)
    return d if d.get("format") == FORMAT and _well_formed(d.get("manifest")) else None


def _well_formed(man: object) -> bool:
    """A manifest of three sides, each mapping a path to a five-item entry; anything else is read
    as no manifest (stale), never a crash (review round 10, B4)."""
    if not isinstance(man, dict):
        return False
    sides = cast("dict[str, object]", man)
    for side in ("public", "private", "merged"):
        entries = sides.get(side)
        if not isinstance(entries, dict):
            return False
        for e in cast("dict[str, object]", entries).values():
            if not (isinstance(e, list) and len(cast("list[object]", e)) == 5):
                return False
    return True


def file_stats(root: Path) -> dict[str, tuple[Path, int, int, int, int]]:
    """Every card, email and set file of a set, with (path, size, mtime, inode, ctime): one
    scandir per folder (the gate's cache key). A symbolic link or special file is listed with
    its own stat, not refused: the key changes with it, and the read that follows refuses it."""
    return _stats(root, refuse=False)


def _stats(  # noqa: PLR0912, PLR0915 - one walk, each kind of entry handled in place
    root: Path, *, refuse: bool = True
) -> dict[str, tuple[Path, int, int, int, int]]:
    """`_walk`'s files with their stats, one `scandir` per folder (the freshness check's floor).
    Nothing is followed: a symbolic link or special file (the set folder, one of its folders, a
    card, an email, a set file) is refused (review round 5, B-1), or with `refuse=False` listed
    by its own stat."""
    out: dict[str, tuple[Path, int, int, int, int]] = {}

    def odd(rel: str, p: Path, st: os.stat_result) -> None:
        if refuse:
            raise special(p)
        out[rel] = (p, st.st_size, st.st_mtime_ns, st.st_ino, st.st_ctime_ns)

    def denied(rel: str, p: Path, exc: PermissionError) -> None:
        """A folder or file that can't be read: refused by name, or (`refuse=False`, the gate's
        key, which never raises) listed by its own stat or a marker (review round 16)."""
        if refuse:
            raise unreadable(p, exc) from exc
        try:
            st = os.lstat(p)
            out[rel] = (p, -1, st.st_mtime_ns, st.st_ino, st.st_ctime_ns)
        except OSError:
            out[rel] = (p, -1, -1, -1, -1)

    try:
        if root.is_symlink():
            odd(".", root, os.lstat(root))
            return out
    except PermissionError as exc:
        denied(".", root, exc)
        return out
    for d in (*COPIED, LINKED):
        p = root / d
        try:  # its own stat first: a link there is refused as one, never listed (round 21, B2)
            own = os.lstat(p)
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            denied(d, p, exc)
            continue
        if not stat.S_ISDIR(own.st_mode):  # a link, or a file where a folder goes
            odd(d, p, own)
            continue
        try:
            entries = list(os.scandir(p))
        except PermissionError as exc:
            denied(d, p, exc)
            continue
        except (FileNotFoundError, NotADirectoryError):
            continue  # gone since its stat: as if absent (a swap meanwhile: round 7's limit)
        try:
            now = os.lstat(p)
        except FileNotFoundError:
            continue  # gone since the listing: as if absent
        except PermissionError as exc:  # as before the listing (round 23, B3)
            denied(d, p, exc)
            continue
        if (now.st_dev, now.st_ino) != (own.st_dev, own.st_ino):  # swapped since its stat: the
            if refuse:  # listing may be another folder's (round 22, P2)
                raise InvalidInputError(f"{p}: changed while it was read", advice="try again")
            out[d] = (p, now.st_size, now.st_mtime_ns, now.st_ino, now.st_ctime_ns)
            continue
        for e in entries:
            if e.name.startswith(".") or e.is_dir(follow_symlinks=False):
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except PermissionError as exc:  # a folder listable but not searchable (0o444)
                denied(f"{d}/{e.name}", Path(e.path), exc)  # (review round 17)
                continue
            except FileNotFoundError:
                if refuse:
                    raise
                continue  # removed since the scandir: the key lists it as gone (round 18)
            if not stat.S_ISREG(st.st_mode):
                odd(f"{d}/{e.name}", Path(e.path), st)
                continue
            out[f"{d}/{e.name}"] = (Path(e.path), st.st_size, st.st_mtime_ns, st.st_ino,
                                    st.st_ctime_ns)  # fmt: skip
    for f in FILES:
        try:
            st = os.lstat(root / f)
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            denied(f, root / f, exc)
            continue
        if not stat.S_ISREG(st.st_mode):
            odd(f, root / f, st)
            continue
        out[f] = (root / f, st.st_size, st.st_mtime_ns, st.st_ino, st.st_ctime_ns)
    return out


def _copies_same(sp: SetPaths) -> bool:
    """Whether the merged view's copies are the sources' files, by content hashed now on both
    sides: no stat or sha256 recorded in merge.json decides it (operator decision 2026-11-06,
    review round 11: merge.json is a cache, never trusted for what a reader is served). The
    copies must be exactly the sources' files, no name may be in both halves (letter case
    ignored), and the view may hold nothing else (`_extras`)."""
    if sp.private is None or sp.merged is None:
        return False
    pub = {r: v for r, v in _stats(sp.public).items() if r not in FILES}
    priv = {r: v for r, v in _stats(sp.private).items() if r not in FILES}
    if {_fold(r) for r in pub} & {_fold(r) for r in priv}:
        return False
    src = {**pub, **priv}
    copies = {r: v for r, v in _stats(sp.merged).items() if r not in FILES}
    if src.keys() != copies.keys() or _extras(sp.merged):
        return False
    try:
        return all(src[r][1] == copies[r][1] and _sha(src[r][0]) == _sha(copies[r][0])
                   for r in src)  # fmt: skip
    except (Unreadable, PermissionError):
        raise  # named by `resolve`, stale to `is_fresh`
    except (OSError, InvalidInputError):
        return False


def _extras(m: Path) -> list[Path]:
    """Anything in the merged view besides its copied folders' plain files, the four set files
    and merge.json: a dot-file, a subfolder, any other top-level file (a planted manifest.json, a
    crashed write's temporary file). Such an entry makes the view stale and the refresh removes it
    (review round 12, B2)."""
    allowed = {*COPIED, LINKED, *FILES, MERGE_FILE}
    out: list[Path] = []
    try:
        top = list(os.scandir(m))
    except FileNotFoundError:
        return out
    for e in top:
        if e.name not in allowed:
            out.append(Path(e.path))
        elif e.name in (*COPIED, LINKED) and e.is_dir(follow_symlinks=False):
            out += [Path(f.path) for f in os.scandir(e.path)
                    if f.name.startswith(".") or f.is_dir(follow_symlinks=False)]  # fmt: skip
    return out


def is_fresh(sp: SetPaths) -> bool:
    """See `_fresh`; anything that can't be read means stale (review round 16): the refresh
    repairs the view's own folders and files, or names what it can't read (rounds 15-16)."""
    try:
        return _fresh(sp)
    except (Unreadable, PermissionError):
        return False


def _fresh(sp: SetPaths) -> bool:
    """Whether the merged view equals what its two sources would give now: every copied file
    (card, email, `.build/` file) compared by content with its source, and all four derived files
    (label files, checks.yaml, reviews.jsonl) recomputed in memory and compared byte for byte.
    merge.json is read only for its format and merged version, so no edit of it, consistent with
    the files or not, makes a stale or forged view fresh (review rounds 9-11).

    A part the check reads and can't (`Unreadable`, PermissionError) raises: `resolve` names it,
    `is_fresh` reads it as stale. What the check doesn't read is never named, so a refusal says
    "can't be read" exactly when a reader couldn't read the set (review round 21: one check, no
    second walk that could disagree with it)."""
    if sp.private is None or sp.merged is None:
        return False
    doc = _read_merge(sp.merged, strict=True)
    if doc is None or not _copies_same(sp):
        return False
    try:
        want = merge_sources(sp.public, sp.private, FILES, validate=False)
    except (Unreadable, PermissionError):
        raise
    except (InvalidInputError, OSError, ValueError, KeyError):
        return False
    for kind, data in want.items():
        p = sp.merged / kind
        try:
            got = read_regular(p)
        except Unreadable:
            raise
        except InvalidInputError:
            return False
        if got != data:
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
_CLONE_NOFOLLOW = 0x0001  # <sys/clonefile.h>


def _place_copy(src: Path, dst: Path, data: bytes | None = None) -> None:
    if data is None:
        replace_file(dst, lambda tmp: copy_new(src, tmp))
    else:
        replace_file(dst, lambda tmp: write_new(tmp, data))
    _mode(dst, 0o444)


def _clone_or_link(src: Path, tmp: Path) -> None:
    """An APFS clone where available, else a hard link, else a copy; each refuses an existing
    `tmp` (FileExistsError)."""
    # CLONE_NOFOLLOW and follow_symlinks=False: a source swapped for a link after it was listed
    # is placed as a link, which every reader refuses, never its target (review round 27). They
    # cover the last name only: a source folder swapped for a link is followed, and what it
    # places is caught by content, as every copy is (the `moved` check after the copy hashes the
    # view against the sources' manifests; a refresh it fails stays stale)
    if _CLONE is not None and _CLONE(str(src).encode(), str(tmp).encode(), _CLONE_NOFOLLOW) == 0:
        return  # (refused onto an existing `tmp`: the link below is refused the same way)
    try:
        os.link(src, tmp, follow_symlinks=False)
    except FileExistsError:
        raise
    except OSError:
        copy_new(src, tmp)


def _place_link(src: Path, dst: Path) -> None:
    replace_file(dst, lambda tmp: _clone_or_link(src, tmp))


def _writable(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    _mode(d, 0o755)


def _mode(p: Path, mode: int) -> None:
    """Give `p` its mode only when it differs: a chmod changes the ctime the gate's key reads,
    so a refresh that changes nothing leaves every stat as it was (review round 19, B1). A link
    is left alone and never followed, so a folder or file swapped for one never has its target's
    mode changed (review round 27; on Linux, which has no lchmod, a swap between the lstat and
    the chmod is still followed: Linux is unsupported until M5, ADR 0020)."""
    st = os.lstat(p)
    if stat.S_ISLNK(st.st_mode) or stat.S_IMODE(st.st_mode) == mode:
        return
    if os.chmod in os.supports_follow_symlinks:
        os.chmod(p, mode, follow_symlinks=False)
    else:
        os.chmod(p, mode)


def _fold(name: str) -> str:
    """A file name as a case- and normalization-insensitive volume (APFS) compares it."""
    return unicodedata.normalize("NFD", name).casefold()


def _copy_cards(m: Path, halves: tuple[tuple[Path, dict[str, list[Any]]], ...],
                prev_merged: dict[str, list[Any]],
                ) -> tuple[set[str], dict[str, list[Any]]]:  # fmt: skip
    """Copy (or link, for `.build/`) every card and email file of both halves into `m`, skipping
    a file whose merged copy is unchanged since the last refresh. Returns the paths placed and
    the entries of copies found unchanged (so the merged manifest doesn't hash them again)."""
    keep: set[str] = set()
    verified: dict[str, list[Any]] = {}
    ready: dict[Path, dict[str, str]] = {}  # each folder's stored file names, by `_fold`
    names = {rel for _half, man in halves for rel in man}  # every source file's own name
    for half, man in halves:
        for rel, ent in man.items():
            if rel in FILES:
                continue
            dst = m / rel
            keep.add(rel)
            if dst.parent not in ready:  # once per folder, not per file
                _writable(dst.parent)
                ready[dst.parent] = {_fold(e.name): e.name for e in os.scandir(dst.parent)
                                     if e.is_file(follow_symlinks=False)}  # fmt: skip
            stored = ready[dst.parent].get(_fold(dst.name))
            folder = rel.rsplit("/", 1)[0]
            if stored is not None and stored != dst.name and f"{folder}/{stored}" not in names:
                # a source renamed in letter case or Unicode form only: the old stored name goes
                # first, or the copy would keep it; never a name another source still has (on a
                # case-sensitive volume two such names can both be real; rounds 12-13)
                (dst.parent / stored).unlink()
                ready[dst.parent].pop(_fold(dst.name))
            old = prev_merged.get(rel)
            if old is not None and old[3] == ent[3] and (st := _unchanged(dst, old, rel)):
                verified[rel] = [*st[:3], old[3], st[3]]  # this stat, this content: just checked
                continue
            if rel.startswith(LINKED + "/"):
                _place_link(half / rel, dst)
            else:
                _place_copy(half / rel, dst)
    return keep, verified


def _readable_view(m: Path) -> None:
    """Make the merged view's own real folders (the view, `cases/`, `eml/`, `.build/`) readable
    and writable, never following a link (a link there is refused by the walk that follows), so a
    folder someone set to 000 is repaired by the refresh, not a traceback (review round 15)."""
    for d in (m, *(m / c for c in (*COPIED, LINKED))):
        try:
            if stat.S_ISDIR(os.lstat(d).st_mode):
                _mode(d, 0o755)
        except FileNotFoundError:
            continue


def _sweep(m: Path, keep: set[str]) -> None:
    """Remove orphans (copies no source has now). Unlinking needs the folder's write bit, never
    the file's, so no chmod follows a hard link into a source (round 9, B3)."""
    for rel, (p, *_s) in _stats(m).items():
        if rel not in keep:
            _writable(p.parent)
            p.unlink()


def _clear_extras(m: Path) -> None:
    """Remove anything the view mustn't hold (`_extras`; review round 12, B2), before any copy is
    placed, so a planted folder under a copy's name can't block the refresh (round 13, B2). A
    folder is removed without following a link, each folder in it made readable and writable
    first (a 000 folder too; round 14, B1); if anything still can't be removed, the refusal names
    it."""
    for p in _extras(m):
        _writable(p.parent)
        try:
            _remove(p)
        except OSError as exc:
            why = f"{p}: can't be removed from the merged set ({reason(exc)})"
            raise InvalidInputError(why, advice="remove it and run `ecf eval build` again") from exc


_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _chmod_at(fd: int, name: str, mode: int) -> None:
    """chmod `name` in the folder open as `fd`, never following a link where the system can
    (macOS; Linux, unsupported until M5 (ADR 0020), has no lchmod)."""
    if os.chmod in os.supports_follow_symlinks:
        os.chmod(name, mode, dir_fd=fd, follow_symlinks=False)
    else:
        os.chmod(name, mode, dir_fd=fd)


def _remove(p: Path) -> None:
    """Remove a file, link or folder inside the merged view, never following a link: each
    folder is opened without following one and emptied through its handle, so a folder swapped
    for a link part way is refused, never walked (review round 27). Walked with a stack, not
    recursion, so no depth makes it fail other than with an OSError (round 15). Each folder
    level open holds a descriptor, so a tree deeper than the open-file limit (256 by default on
    macOS) fails with "Too many open files", named by `_clear_extras`: a stated limit, as such a
    tree in the view is planted by hand (review round 28)."""
    if not stat.S_ISDIR(os.lstat(p).st_mode):
        p.unlink()
        return
    top = os.open(p.parent, _DIR)
    opened = [top]
    stack: list[tuple[int, str, int | None]] = [(top, p.name, None)]  # (folder, name, own fd)
    try:
        while stack:
            pfd, name, fd = stack.pop()
            if fd is not None:  # emptied
                os.close(opened.pop(opened.index(fd)))
                os.rmdir(name, dir_fd=pfd)
                continue
            _chmod_at(pfd, name, 0o700)  # list and empty it whatever its mode
            fd = os.open(name, _DIR, dir_fd=pfd)
            opened.append(fd)
            stack.append((pfd, name, fd))
            with os.scandir(fd) as it:
                entries = list(it)
            for e in entries:
                if e.is_dir(follow_symlinks=False):
                    stack.append((fd, e.name, None))
                else:
                    os.unlink(e.name, dir_fd=fd)
    finally:
        for fd in opened:
            os.close(fd)


def _build(sp: SetPaths) -> None:
    if sp.private is None or sp.merged is None:
        raise InvalidInputError(f"{sp.public}: no private set to merge")
    m = sp.merged
    if os.path.islink(m) or os.path.islink(m.parent):  # `_locate` checked; checked again here,
        raise special(m)  # before anything is written (review round 7, B2)
    if os.path.lexists(m):  # nothing inside the merged view is followed either: its folders and
        _readable_view(m)  # files are walked, refusing a link, before anything is written (round
        _stats(m)  # 8, B1); its own folders made readable first, whatever their mode (round 15)
        mj = m / MERGE_FILE
        if os.path.lexists(mj) and not stat.S_ISREG(os.lstat(mj).st_mode):
            raise special(mj)  # merge.json too (round 9, B2)
    _writable(m)
    prev = _read_merge(m) or {}
    (m / MERGE_FILE).unlink(missing_ok=True)  # stale until written again, last
    prev_man: dict[str, dict[str, list[Any]]] = prev.get("manifest", {})
    # the sources' manifests before their content: a writer changing a source after this point
    # leaves it newer than its manifest, so the view reads stale and is rebuilt, never fresh
    # every source hashed (no recorded stat trusted for content; round 11: a forged source entry
    # would otherwise keep a forged copy); within a refresh the freshness check's hashes are kept
    pub_man = _manifest(sp.public, {})
    priv_man = _manifest(sp.private, {})
    content = merge_sources(sp.public, sp.private)
    # a previous merged entry is reused only when it records its source's content (round 9, B1)
    folded = {_fold(r) for r in priv_man if r not in FILES}  # letter case and Unicode form
    both = sorted(r for r in pub_man if r not in FILES and _fold(r) in folded)  # (rounds 11, 14)
    if both:  # a card in both halves is refused by `merge_sources`; so is any other file (round 10)
        raise InvalidInputError(
            f"files in both the public and the private set: {', '.join(both[:5])}"
        )
    sources = {**pub_man, **priv_man}
    prev_merged = {r: e for r, e in prev_man.get("merged", {}).items()
                   if r in FILES or (r in sources and sources[r][3] == e[3])}  # fmt: skip
    _clear_extras(m)
    keep, verified = _copy_cards(m, ((sp.public, pub_man), (sp.private, priv_man)), prev_merged)
    for kind, data in content.items():
        dst = m / kind
        if data is None:
            if os.path.lexists(dst):
                dst.unlink()  # no chmod: it would follow a hard link into a source (round 9, B3)
            continue
        keep.add(kind)
        try:  # the view's own file at 000 is replaced, never a refusal (round 16)
            same = read_regular(dst) == data
        except InvalidInputError:
            same = False
        if not same:
            _place_copy(dst, dst, data)
    _sweep(m, keep)
    merged_man = _manifest(m, prev_merged, verified)
    copied = {**pub_man, **priv_man}
    moved = sorted(
        rel
        for rel, ent in copied.items()
        if rel not in FILES and merged_man.get(rel, [None] * 5)[3] != ent[3]
    )
    if moved:  # a source changed under the refresh (and maybe back): stay stale, never fresh
        raise InvalidInputError(
            f"a source file changed while the merged view was refreshed ({', '.join(moved[:5])})",
            advice="run `ecf eval build` again",
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
    text = json.dumps(doc, indent=1, sort_keys=True) + "\n"
    replace_file(m / MERGE_FILE, lambda tmp: write_new(tmp, text))


def _open_lock(path: Path, flags: int) -> int:
    """The lock file, opened without following a link or blocking on a pipe, and refused when it
    isn't a regular file (review round 10, B3: a pipe there hung every reader) or can't be opened
    (round 16; the refresh, its one writer, first gives its own regular lock back its mode)."""
    try:
        st = os.lstat(path)
        if flags & os.O_CREAT and stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) != 0o644:
            os.chmod(path, 0o644, follow_symlinks=False)  # only when wrong (round 19, B1)
    except (FileNotFoundError, NotImplementedError):
        pass
    except PermissionError as exc:
        raise unreadable(path, exc) from exc
    try:
        fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644)
    except PermissionError as exc:
        raise unreadable(path, exc) from exc
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise special(path)
    return fd


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


@named
def refresh_merged(root: Path, *, wait: float = 0.0,
                   on_wait: Callable[[float], None] | None = None) -> str:  # fmt: skip
    """Rebuild the merged view of `root`'s set if it is stale, under the exclusive lock (the one
    writer of the merged folder, and the only one that creates the lock file). Returns "fresh" or
    "refreshed"; raises RefreshBusy when the lock stays held for `wait` seconds."""
    sp = _usable(paths(root))
    if sp.private is None or sp.lock is None:
        raise InvalidInputError(f"{sp.public}: no private set to merge (public set only)")
    _writable(sp.lock.parent)
    fd = _open_lock(sp.lock, os.O_RDWR | os.O_CREAT)
    try:
        t0 = time.monotonic()
        got = _take(fd, fcntl.LOCK_EX, wait)
        if on_wait is not None:
            on_wait(time.monotonic() - t0)
        if not got:
            raise RefreshBusy(BUSY, advice=BUSY_ADVICE)
        try:
            with one_read():
                if is_fresh(sp):
                    return "fresh"
                try:
                    _build(sp)
                except PermissionError as exc:  # a write in the view that failed (EPERM, EACCES)
                    raise unreadable(exc.filename or sp.merged, exc) from exc
                return "refreshed"
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@named
def take_shared(root: Path, *, wait: float = 0.0) -> int | None:
    """A reader's shared lock on the set's merged view, as an open descriptor (None when there is
    no private set). Raises when the lock file is missing or the lock stays busy."""
    sp = paths(root)
    if sp.private is None or sp.lock is None:
        return None
    try:
        fd = _open_lock(sp.lock, os.O_RDONLY)
    except FileNotFoundError as exc:
        raise InvalidInputError(
            f"{sp.name}: no merged set yet", advice="run `ecf eval build`"
        ) from exc
    try:
        got = _take(fd, fcntl.LOCK_SH, wait)
    except BaseException:
        os.close(fd)  # never leak the descriptor, whatever `_take` raised
        raise
    if not got:
        os.close(fd)
        raise RefreshBusy("a refresh is running", advice="try again")
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
        if (d.name.casefold(), d.parent.name.casefold()) == ("private", "eval"):
            return True  # letter case ignored, as `is_private_root` does (review round 8, B2)
    return is_merged(c)


@named
def view_of(root: Path) -> Path:
    """The folder a read of `root` used: the merged view when there is a private half, else the
    public set; no freshness check, for a caller whose own read (`evalrun.load`) has just made
    it and checked the version (judge-drafts, calibration; review round 8, performance B-3)."""
    sp = _usable(paths(root))
    return sp.merged if sp.private is not None and sp.merged is not None else sp.public


def _same_dir(a: Path, b: Path) -> bool:
    try:
        sa, sb = a.stat(), b.stat()
    except OSError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


@named
def resolve(root: Path) -> Path:
    """The set a reader should read for `root`: the public set when there is no private half;
    the merged view when there is one and it is fresh; refused otherwise. A path at or under
    `eval/private/`, or inside a merged view, is refused: readers pass the logical public path."""
    p = logical(root)
    try:
        real = p.resolve()
    except RuntimeError as exc:  # a link loop (Python 3.12): a refusal, which callers handle
        raise InvalidInputError(f"{root}: a symbolic link loop in its path") from exc
    if _under_private(real) or _under_private(p):
        raise InvalidInputError(f"{root} is a private or merged set's path",
                                advice="give the public set's path (tests/eval/<set>)")  # fmt: skip
    sp = _usable(paths(p))
    if sp.top is not None:
        priv_dir = sp.top / "eval" / "private"
        if priv_dir.exists() and any(_same_dir(d, priv_dir) for d in (real, *real.parents)):
            raise InvalidInputError(
                f"{root} is a private set's path", advice="give the public set's path"
            )
    if sp.private is None or sp.merged is None:
        return p
    if not _fresh(sp):  # a part it can't read is named (`named`), never "out of date"
        raise InvalidInputError(f"the merged {sp.name} set is missing or out of date",
                                advice="run `ecf eval build`")  # fmt: skip
    return sp.merged
