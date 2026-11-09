"""Writing into an eval set safely (planning-docs/EVAL-PRIVATE.md, "The overlay", W1, OD-647).

Every writer into a set goes through `atomic_write`, so a crash leaves the old file or the new
one, never half of it, and through `writable_root`, so nothing writes into the merged view of a
public and a private set (`eval/private/.merged/<set>/`), which only `refresh_merged` builds."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import re
import secrets
import shutil
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from ecf.errors import InvalidInputError
from ecf.text import escaped

# macOS: the path with each name's stored case; absent on Linux, where names are case-sensitive
# and realpath is already canonical
_GETPATH: int | None = getattr(fcntl, "F_GETPATH", None)

MERGED_DIR = ".merged"
MERGE_FILE = "merge.json"
# What a set holds by these names: folders, then the merged set files, then a generated set's
# manifest. Nothing in a set is written through a link (OD-705, review round 28): see `_odd_entry`
SET_FOLDERS = ("cases", "eml", ".build", "work", "grades")
SET_FILES = ("labels.jsonl", "fixed_labels.jsonl", "checks.yaml", "reviews.jsonl")
_LAYOUT_FILES = (*SET_FILES, "manifest.json")


def canonical(path: Path) -> Path:
    """What a path is, not how it was typed: symlinks followed and, on a case-insensitive file
    system, every name in the case it is stored in. Every layout check compares canonical paths,
    so `Tests/Eval/synthetic` is `tests/eval/synthetic` (EVAL-PRIVATE.md, "Finding the private
    set"). A path that doesn't exist yet is its nearest existing parent's canonical path plus
    the rest as given. A `..` is applied where the system applies it, after the link before it
    is followed (`abspath` folded `a/link/..` to `a` as text: review round 28, third delta
    check). In the part that doesn't exist yet a `..` is folded and the result looked at again,
    since a name after it may exist and be a link."""
    p = path if path.is_absolute() else Path.cwd() / path
    rest: list[str] = []
    while not os.path.lexists(p) and p != p.parent:
        rest.insert(0, p.name)
        p = p.parent
    real = Path(os.path.realpath(p))
    if ".." in rest:  # folded below `real`, then looked at again: it may lead back to a link
        return canonical(Path(os.path.normpath(real.joinpath(*rest))))
    if _GETPATH is not None:
        got = _getpath(real)
        # F_GETPATH failed (no permission to open it, say): each name's stored case from its
        # folder's listing, so a respelled path is never kept as typed (review round 4, B7)
        real = got if got is not None else _listed_case(real)
    return real.joinpath(*rest)


def _getpath(real: Path) -> Path | None:
    if _GETPATH is None:
        return None
    try:
        # O_NONBLOCK: a FIFO can't hang it; O_EVTONLY (macOS): no read access asked for, so a
        # cloud-only (dataless) folder isn't downloaded just to learn its name's case
        fd = os.open(real, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_EVTONLY", 0))
    except OSError:
        return None
    try:
        got: bytes = fcntl.fcntl(fd, _GETPATH, bytes(1024))
        return Path(os.fsdecode(got.split(b"\0", 1)[0]))
    except OSError:
        return None
    finally:
        os.close(fd)


def _listed_case(real: Path) -> Path:
    """`real` (an existing, symlink-free absolute path) with each name as its folder lists it:
    the one entry equal to it ignoring case. A folder that can't be listed keeps the rest as
    typed."""
    out = Path(real.anchor)
    for i, name in enumerate(real.parts[1:], 1):
        try:
            names = os.listdir(out)
        except OSError:
            return out.joinpath(*real.parts[i:])
        exact = name in names
        folded = [n for n in names if n.casefold() == name.casefold()]
        out = out / (name if exact or len(folded) != 1 else folded[0])
    return out


def _own_name(path: Path) -> Path:
    """A temporary name of this write's own beside `path`: `.<name>.<random>.tmp`, ignored by
    both repositories (`*.tmp`) and by every reader (a dot name)."""
    return path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")


def write_new(tmp: Path, data: bytes | str) -> None:
    """Create `tmp` (refused if anything is there) and write `data` into it."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)


def copy_new(src: Path, tmp: Path) -> None:
    """Create `tmp` (refused if anything is there) holding a copy of `src`'s bytes. The source is
    opened first, as every reader opens a set file: a link is never followed and a pipe never
    blocks, and anything but a regular file is refused, so its errors name the source (review
    round 24, B3-B4)."""
    try:
        sfd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise InvalidInputError(f"{src}: a symbolic link or special file") from exc
        raise
    with os.fdopen(sfd, "rb") as s:
        if not stat.S_ISREG(os.fstat(sfd).st_mode):
            raise InvalidInputError(f"{src}: a symbolic link or special file")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "wb") as f:
            shutil.copyfileobj(s, f, 1 << 20)


def replace_file(path: Path, make: Callable[[Path], None]) -> None:
    """The one way a file of a set or its merged view is written (review round 23): `make`
    creates a temporary file of this write's own (a fresh name, refused if anything is there:
    FileExistsError, and another name is tried), which is then renamed over `path`. So a write
    never removes or renames another writer's temporary file, a crash leaves the old file or the
    new one, and a failed write removes only its own temporary file. An error that names the
    temporary file (any OSError: a refusal, a missing folder, a full disk) is reported for
    `path`, the file being written: the temporary file is never the operator's to fix; one that
    names something else (a source) is left as it is (review round 24, B3, B5)."""
    for _ in range(8):
        tmp = _own_name(path)
        try:
            make(tmp)
            break
        except FileExistsError:
            continue  # someone else's name: never touched
        except BaseException as exc:
            _drop(tmp)
            _reraise_for(path, tmp, exc)
    else:
        raise InvalidInputError(f"{path}: no free temporary name beside it")
    try:
        os.replace(tmp, path)
    except FileNotFoundError as exc:
        if exc.filename == str(tmp) and path.parent.is_dir():
            # a write paused for over an hour (a debugger, a sleeping laptop) whose temporary
            # file a build swept meanwhile (review round 25, S4)
            raise InvalidInputError(
                f"{path}: its temporary file was removed while it was being written; try again"
            ) from exc
        _drop(tmp)
        _reraise_for(path, tmp, exc)
    except BaseException as exc:
        _drop(tmp)
        _reraise_for(path, tmp, exc)


def _drop(tmp: Path) -> None:
    with contextlib.suppress(OSError):
        tmp.unlink()


def _reraise_for(path: Path, tmp: Path, exc: BaseException) -> NoReturn:
    """Raise `exc`, naming `path` instead of `tmp` when it names `tmp`."""
    if isinstance(exc, OSError) and exc.errno is not None and exc.filename == str(tmp):
        why = exc.strerror or os.strerror(exc.errno)  # never "None" (review round 28)
        raise OSError(exc.errno, why, str(path)) from exc  # the same subclass
    raise exc


# A crashed write's temporary file (`.<name>.<12 hex digits>.tmp`) in a half is ignored by git
# and every reader, but nothing else would ever remove it: `ecf eval build` removes those older
# than an hour, an age no live write reaches (set writers take no lock; review round 24, B6).
_OWN_TEMP = re.compile(r"\..+\.[0-9a-f]{12}\.tmp")
STALE_TEMP_S = 3600


def sweep_old_temps(root: Path) -> tuple[list[Path], list[str]]:
    """Remove the crashed writes' temporary files older than `STALE_TEMP_S` from a half's own
    folder and its `cases/`, `eml/`, `.build/`, `work/` and `work/call/` folders (round 28).
    Best effort, and it never raises (review rounds 25-26): a root it can't check, or one inside
    a merged view, is skipped with a line; each folder is opened without following a link (a
    linked or missing one is skipped, and so is one that can't be listed), every entry is looked
    at and removed relative to that open folder, only a plain file is removed, and one that
    vanishes meanwhile is skipped. Returns what was removed, and one line for each old temporary
    file it couldn't remove, which is left in place."""
    try:
        writable_set(root)
    except (InvalidInputError, OSError) as exc:
        return [], [f"{escaped(str(root))}: old temporary files not checked ({reason(exc)})"]
    return _old_temps(root, remove=True)


def old_temps_left(root: Path) -> list[str]:
    """One line for each crashed write's old temporary file still in `root`'s folders, found as
    `sweep_old_temps` finds them, removing nothing: the commands that rebuild a half report them
    too, not only `ecf eval build` (review round 26)."""
    return _old_temps(root, remove=False)[1]


def _old_temps(root: Path, *, remove: bool) -> tuple[list[Path], list[str]]:
    cutoff = time.time() - STALE_TEMP_S
    gone: list[Path] = []
    left: list[str] = []
    try:
        top = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return gone, left
    try:
        # work/call too: a model call's schema file is written there (review round 28)
        for name in ("", "cases", "eml", ".build", "work", "work/call"):
            try:
                fd = _open_below(top, name)
            except OSError:
                continue  # missing, a link or not a folder: nothing of a write's own there
            try:
                _sweep_folder(fd, root / name if name else root, cutoff, gone, left, remove)
            finally:
                if fd != top:
                    os.close(fd)
    finally:
        os.close(top)
    return gone, left


def _open_below(top: int, name: str) -> int:
    """A folder below open folder `top` (`top` itself for ""), opened one name at a time
    without following a link at any of them."""
    fd = top
    for part in [p for p in name.split("/") if p]:
        try:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        finally:
            if fd != top:
                os.close(fd)
        fd = nxt
    return fd


def _sweep_folder(fd: int, where: Path, cutoff: float, gone: list[Path], left: list[str],
                  remove: bool) -> None:  # fmt: skip
    """`sweep_old_temps` for one open folder."""
    try:
        names = os.listdir(fd)
    except OSError:
        return
    for name in names:
        if not _OWN_TEMP.fullmatch(name):
            continue
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            continue  # another write renamed or removed it meanwhile
        except OSError as exc:
            left.append(f"{escaped(str(where / name))}: a temporary file's name, couldn't check it"
                        f" ({reason(exc)}); left in place")  # fmt: skip
            continue
        if not (stat.S_ISREG(st.st_mode) and st.st_mtime < cutoff):
            continue
        if not remove:
            left.append(f"{escaped(str(where / name))}: an old temporary file of a crashed write;"
                        " remove it when you can")  # fmt: skip
            continue
        try:
            os.unlink(name, dir_fd=fd)
        except FileNotFoundError:
            continue  # another write renamed or removed it meanwhile
        except OSError as exc:
            left.append(f"{escaped(str(where / name))}: an old temporary file of a crashed write,"
                        f" left in place ({reason(exc)}); remove it when you can")  # fmt: skip
            continue
        gone.append(where / name)


def atomic_write(path: Path, data: bytes | str) -> None:
    """Write `data` over `path` through `replace_file`."""
    replace_file(path, lambda tmp: write_new(tmp, data))


def reason(exc: BaseException) -> str:
    """An error's reason for a message: its system message, else its text, else its type (an
    OSError raised without a message has no strerror; review rounds 27-28). Every eval message
    that reports an OSError's reason uses this, so none reads "None" or "()"."""
    code = getattr(exc, "errno", None)
    text = getattr(exc, "strerror", None) or (os.strerror(code) if isinstance(code, int) else "")
    return escaped(text or str(exc) or type(exc).__name__)


def is_merged(root: Path) -> bool:
    """Whether `root`, or any folder above it, is a merged view (`merged_by`)."""
    return merged_by(root) is not None


def merged_by(root: Path) -> str | None:
    """What makes `root` part of a merged view, or None: a folder above it (or itself) named
    `.merged` in any case, since APFS ignores case (review round 28), or one holding a merged
    view's own `merge.json` (a copy of a view kept elsewhere). The path is canonical, so a link
    into a view is the view."""
    p = canonical(root)
    folders = (p, *p.parents)
    # the names first: inside a real view no merge.json (all its manifests) is read (round 27)
    for d in folders:
        if d.name.casefold() == MERGED_DIR:
            return f"the folder {d}"
    for d in folders:
        if _view_record(d / MERGE_FILE):
            return f"{d / MERGE_FILE}, a merged view's record"
    return None


def _view_record(path: Path) -> bool:
    """Whether a `merge.json` is a merged view's own: any file of that name counts unless it
    reads, without following a link, as JSON with no `merged_version` (another tool's file of
    the same name; review round 27). A link, a pipe or an unreadable file counts: refusing a
    write is the safe side."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    except OSError:
        return os.path.lexists(path)  # a link (ELOOP), or unreadable
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return True
        chunks: list[bytes] = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
        doc = json.loads(b"".join(chunks))
    except (OSError, ValueError):
        return True
    finally:
        os.close(fd)
    return isinstance(doc, dict) and "merged_version" in doc


def _typed(path: Path) -> Path:
    """`path` made absolute as typed: no link followed and no `..` folded away (`os.path.abspath`
    would fold `a/link/..` to `a`, where the system goes to the link's parent)."""
    return path if path.is_absolute() else Path.cwd() / path


def _odd_entry(top: Path, below: tuple[str, ...]) -> tuple[Path, str] | None:
    """The first entry of set `top` that a write to `top/below` could go through wrongly, and
    why: any of the set's folders and files by name that is a symbolic link or not what that
    name holds, then each name from `top` down to `top/below` that is a link or a special file.
    Only `top` itself may be a link (an alias of the whole set). Everything is looked at with
    lstat; a name that doesn't exist yet or can't be looked at is left to the write."""
    for name, folder in [*((n, True) for n in SET_FOLDERS),
                         *((n, False) for n in _LAYOUT_FILES)]:  # fmt: skip
        try:
            st = os.lstat(top / name)
        except OSError:
            continue
        if stat.S_ISLNK(st.st_mode):
            return top / name, "a symbolic link"
        if not (stat.S_ISDIR(st.st_mode) if folder else stat.S_ISREG(st.st_mode)):
            return top / name, f"not the {'folder' if folder else 'file'} a set holds by this name"
    here = top
    for name in below:
        here = here / name
        try:
            st = os.lstat(here)
        except OSError:
            return None
        if stat.S_ISLNK(st.st_mode):
            return here, "a symbolic link"
        if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
            return here, "a special file (a pipe or a device)"
    return None


def writable_root(path: Path) -> Path:
    """Refuse a path inside a merged view; return it unchanged otherwise. Every helper that
    writes where its caller says (a page, a result copy, a call folder, a catalogue) calls this
    before it writes; a write into a set goes through `writable_set`, which names the set
    (review round 28: a set is never guessed from what a folder holds)."""
    by = merged_by(path)
    if by is not None:
        raise InvalidInputError(
            f"{escaped(str(path))} is in a merged eval set ({escaped(by)}), which is rebuilt"
            " from its sources and never written: choose a folder outside it (a card or label"
            " goes in the public or private set that owns the card)"
        )
    return path


def writable_set(root: Path, path: Path | None = None) -> Path:
    """Refuse a write into set `root` at `path` (the set's own folder when not given) that is
    in a merged view or could go through a link; return `path`, or `root`. Every writer into a
    set names the set: nothing in it is written through a link or into a special file
    (OD-705). The set's folders and files must be plain folders and files, and so must each
    name from `root` down to `path`, which must lie below `root` with no `..`; only `root`
    itself may be a link (an alias of the whole set). The refusal names what caused it (review
    round 28)."""
    target = root if path is None else path
    writable_root(target)
    top, full = _typed(root), _typed(target)
    below = full.parts[len(top.parts) :]
    if full.parts[: len(top.parts)] != top.parts or ".." in below:
        raise InvalidInputError(
            f"{escaped(str(target))}: not a path inside its set {escaped(str(root))}"
        )
    odd = _odd_entry(top, below)
    if odd is not None:
        where, what = odd
        raise InvalidInputError(
            f"{escaped(str(where))}: {what}; nothing in an eval set is written through a link"
            " or into a special file (put the folder or file itself there)"
        )
    return target


def append_text(path: Path, text: str) -> None:
    """Append `text` to `path`, creating it 0644: the file is opened without following a link and
    must be a regular file (review round 28), so an append never writes where a link points."""
    refused = InvalidInputError(f"{escaped(str(path))}: a symbolic link or special file")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o644)  # fmt: skip
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENXIO}:  # a link; a pipe with no reader
            raise refused from exc
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise refused
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(text)
