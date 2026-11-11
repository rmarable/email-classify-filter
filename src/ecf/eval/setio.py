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
import tempfile
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


def real(path: Path) -> Path:
    """`Path.resolve()` for eval sets and the gate: links followed, the path made absolute, but
    a symbolic link loop is left in the path rather than raised. Python 3.12's `resolve()` raises
    RuntimeError on a loop (3.13's doesn't), which escaped the gate and failed every service tick;
    `os.path.realpath` raises on neither, so a loop reads as a file that isn't there, which every
    reader already refuses (Step 7 review). A source scan keeps `.resolve()` out of these modules
    (tests/test_eval_setio.py)."""
    return Path(os.path.realpath(path))


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


def write_new(tmp: Path, data: bytes | str, mode: int = 0o644, *, sync: bool = False) -> None:
    """Create `tmp` (refused if anything is there) and write `data` into it; `sync` flushes it
    to disk before it is renamed into place (a record that must survive a power cut)."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        if sync:
            f.flush()
            flush_to_disk(f.fileno())


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
    tmp = _made_own(path, make)
    try:
        os.replace(tmp, path)
    except FileNotFoundError as exc:
        if exc.filename == str(tmp) and path.parent.is_dir():
            # a write paused for over an hour (a debugger, a sleeping laptop) whose temporary
            # file a build swept meanwhile (review round 25, S4)
            raise InvalidInputError(
                f"{path}: its temporary file was removed while it was being written",
                advice="try again",
            ) from exc
        _drop(tmp)
        _reraise_for(path, tmp, exc)
    except BaseException as exc:
        _drop(tmp)
        _reraise_for(path, tmp, exc)


def _made_own(path: Path, make: Callable[[Path], None]) -> Path:
    """A temporary file of this write's own beside `path`, made by `make` (another name is tried
    when one is taken: someone else's, never touched); a failure removes only its own."""
    for _ in range(8):
        tmp = _own_name(path)
        try:
            make(tmp)
        except FileExistsError:
            continue
        except BaseException as exc:
            _drop(tmp)
            _reraise_for(path, tmp, exc)
        return tmp
    raise InvalidInputError(f"{path}: no free temporary name beside it")


# A disk that can't hard-link (exFAT, some network shares): the new file is renamed in after a
# check instead, so a file made at the name in between is replaced (a stated limit there)
_NO_LINKS = {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.EMLINK}


def create_file(path: Path, data: bytes | str, mode: int = 0o644) -> None:
    """Create `path` holding `data`, flushed to disk with its folder; refused when anything is
    at the name, a link to nothing included, even one made while it was written: the new file
    is hard-linked to the name, which never replaces anything, then its temporary name dropped
    (review round 29, fourteenth delta check: a check then a rename replaced a file made in
    between, and a fixed temporary name failed every write after a crash). A crash between
    the link and dropping the temporary name leaves that name too (`.<name>.<hex>.tmp`, the same
    file): a set or results folder sweeps it after an hour, any other folder (a corpus's) keeps
    it until it is removed by hand, a stated limit (fifteenth delta check)."""
    exists = InvalidInputError(f"{escaped(str(path))} already exists")
    if os.path.lexists(path):
        raise exists
    tmp = _made_own(path, lambda t: write_new(t, data, mode=mode, sync=True))
    try:
        try:
            os.link(tmp, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise exists from exc
        except OSError as exc:
            if exc.errno not in _NO_LINKS:
                raise
            if os.path.lexists(path):
                raise exists from exc
            os.rename(tmp, path)
    except BaseException as exc:
        _drop(tmp)
        _reraise_for(path, tmp, exc)
    _drop(tmp)
    sync_folder(path.parent)


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
    folder and its `cases/`, `eml/`, `.build/`, `work/`, `work/call/` and `grades/` folders
    (rounds 28-29). Best effort, and it never raises (review rounds 25-26): a root it can't
    check, or one inside a merged view, is skipped with a line; each folder is opened without
    following a link, and one that is a link, not a folder, unreadable or can't be listed gets
    a line (only a missing one is silent; review round 29), every entry is looked at and removed
    relative to that open folder, only a plain file is removed, and one that vanishes meanwhile
    is skipped. Returns what was removed, and one line for each old temporary
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
    except OSError as exc:
        # no link followed, even an alias of the set (review round 25, S1), but never skipped
        # without a line (review round 29)
        if os.path.islink(root):
            left.append(f"{escaped(str(root))}: old temporary files not checked (the set folder"
                        " is a symbolic link; run the command on the folder itself)")  # fmt: skip
        elif os.path.lexists(root):
            left.append(f"{escaped(str(root))}: old temporary files not checked ({reason(exc)})")
        return gone, left
    try:
        # work/call too: a model call's schema file was written there before each call had its
        # own folder (review rounds 28-29), and a crashed call's folder is left there
        said: list[str] = []  # folders already given a line: nothing below them is looked at
        for name in ("", "cases", "eml", ".build", "work", "work/call", "grades"):
            if any(name.startswith(f"{s}/") for s in said):
                continue
            try:
                fd = _open_below(top, name)
            except FileNotFoundError:
                continue  # nothing there, so nothing of a write's own
            except OSError as exc:  # a link, not a folder, or unreadable: said, not skipped
                here = root / name
                why = reason(exc)
                if os.path.islink(here):
                    why = "a symbolic link"
                elif exc.errno == errno.ENOTDIR:
                    why = "not a folder"
                left.append(f"{escaped(str(here))}: old temporary files not checked ({why})")
                said.append(name)
                continue
            try:
                _sweep_here(fd, root, name, (cutoff, gone, left, remove))
            finally:
                if fd != top:
                    os.close(fd)
    finally:
        os.close(top)
    return gone, left


def _sweep_here(fd: int, root: Path, name: str,
                how: tuple[float, list[Path], list[str], bool]) -> None:  # fmt: skip
    """One open folder of a set: its crashed writes' temporary files, and in `work/call` its
    crashed model calls' folders."""
    where = root / name if name else root
    _sweep_folder(fd, where, *how)
    if name == "work/call":
        _sweep_calls(fd, where, *how)


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


def sweep_folder(where: Path) -> list[Path]:
    """Remove the crashed writes' temporary files older than `STALE_TEMP_S` from one folder that
    is no set (the install's results folder: review round 29, twelfth delta check), as
    `sweep_old_temps` does in a set; best effort, never raises, follows no link."""
    gone: list[Path] = []
    try:
        fd = os.open(where, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return gone
    try:
        _sweep_folder(fd, where, time.time() - STALE_TEMP_S, gone, [], True)
    finally:
        os.close(fd)
    return gone


def _sweep_folder(fd: int, where: Path, cutoff: float, gone: list[Path], left: list[str],
                  remove: bool) -> None:  # fmt: skip
    """`sweep_old_temps` for one open folder."""
    try:
        names = os.listdir(fd)
    except OSError as exc:
        left.append(f"{escaped(str(where))}: old temporary files not checked ({reason(exc)})")
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


# A model call's own folder in its call folder (`tools.CliAdapter.ask`): its schema and the
# answer the tool writes itself. The call holds a lock on it while it runs, so a later call
# removes only a crashed one's, whatever the clock did meanwhile (review round 29, sixteenth
# and seventeenth delta checks: judges run side by side, and a Mac asleep mid-call for over an
# hour made a live folder look old; the age is a second guard)
CALL_FILES = ".call-"


_NO_LOCKS = {errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOLCK, errno.EINVAL}


def call_folder(workdir: Path) -> tuple[Path, int]:
    """A fresh 0700 folder for one model call in `workdir`, held until `end_call`."""
    writable_root(workdir)  # never in a merged view
    own = Path(tempfile.mkdtemp(prefix=CALL_FILES, dir=workdir))
    try:
        fd = os.open(own, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as exc:
            if exc.errno not in _NO_LOCKS:
                os.close(fd)
                raise
            # a disk without locks (NFS without locking): the call runs unheld; a sweep on this
            # mount can't lock either, so it leaves the folder with a line, never removing a live
            # one; one from another computer mounting the share with locks could, past an hour
            # (a computer asleep mid-call, or clocks far apart): a stated limit (review round 30)
        except BaseException:
            os.close(fd)
            raise
    except BaseException:
        shutil.rmtree(own, ignore_errors=True)
        raise
    return own, fd


def end_call(own: Path, held: int) -> None:
    """Remove a call's folder (its answer is the model's raw text), then let go of it."""
    try:
        shutil.rmtree(own, ignore_errors=True)
    finally:
        os.close(held)


def sweep_call_folders(workdir: Path) -> None:
    """Remove crashed calls' folders from a call folder, as `sweep_old_temps` does in a set's
    `work/call`; best effort, never raises, follows no link."""
    try:
        writable_root(workdir)  # never in a merged view
        fd = os.open(workdir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except (OSError, InvalidInputError):
        return
    try:
        _sweep_calls(fd, workdir, time.time() - STALE_TEMP_S, [], [], True)
    finally:
        os.close(fd)


def _sweep_calls(fd: int, where: Path, cutoff: float, gone: list[Path], left: list[str],
                 remove: bool) -> None:  # fmt: skip
    """The crashed calls' folders in one open call folder: a real folder, over `STALE_TEMP_S`
    old and held by no call; removed below the open folder, never through a link."""
    try:
        names = [n for n in os.listdir(fd) if n.startswith(CALL_FILES)]
    except OSError:
        return  # said by `_sweep_folder` for the same folder
    for name in names:
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not (stat.S_ISDIR(st.st_mode) and st.st_mtime < cutoff):
                continue
            cfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        except OSError:
            continue  # gone meanwhile, a link, or unreadable: left as it is
        try:
            try:
                fcntl.flock(cfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue  # a live call's (one that slept), held
            except OSError as exc:  # a disk without locks: said, never taken for a live call's
                left.append(f"{escaped(str(where / name))}: a model call's folder; couldn't check"
                            f" whether a call still uses it ({reason(exc)}); left in place, remove"
                            " it when no eval is running")  # fmt: skip
                continue
            if not remove:
                left.append(f"{escaped(str(where / name))}: a crashed model call's folder (it may"
                            " hold a model's answer); remove it when you can")  # fmt: skip
                continue
            try:
                shutil.rmtree(name, dir_fd=fd)
            except OSError as exc:
                left.append(f"{escaped(str(where / name))}: a crashed model call's folder, left in"
                            f" place ({reason(exc)}); remove it when you can")  # fmt: skip
                continue
            gone.append(where / name)
        finally:
            os.close(cfd)


def atomic_write(path: Path, data: bytes | str) -> None:
    """Write `data` over `path` through `replace_file`."""
    replace_file(path, lambda tmp: write_new(tmp, data))


# What an eval write can raise: a system error, or a refusal by name (a link, hard link or pipe
# at the name; no free temporary name). A handler that stops or logs a failed write catches both
# (review round 29, fourteenth delta check: a journal refusal slipped past `except OSError`).
WRITE_ERRORS: tuple[type[Exception], ...] = (OSError, InvalidInputError)


def write_durable(path: Path, data: bytes | str, mode: int = 0o600) -> None:
    """Write `data` over `path` as `replace_file` does, the new file flushed to disk before the
    rename and the folder flushed after it, so a power cut leaves the old file or the new one:
    the install's records (the draft-run registry, plan judges' answers, corpus labels; review
    round 29, thirteenth delta check)."""
    replace_file(path, lambda tmp: write_new(tmp, data, mode=mode, sync=True))
    sync_folder(path.parent)


# The full flush not offered here (a disk or file that doesn't take it, a volume that doesn't
# implement it: macFUSE's ENOSYS): `fsync` instead
_NO_FULL_FLUSH = {errno.ENOTSUP, errno.EOPNOTSUPP, errno.EINVAL, errno.ENOTTY, errno.ENOSYS}


def flush_to_disk(fd: int) -> None:
    """Flush an open file or folder to the disk itself: on macOS `fsync` leaves it in the
    drive's cache, which a power cut loses; `F_FULLFSYNC` doesn't (review round 30). Where that
    isn't offered, `fsync`; a disk error is raised (review round 30, delta check 1)."""
    full = getattr(fcntl, "F_FULLFSYNC", None)
    if full is not None:
        try:
            fcntl.fcntl(fd, full)
            return
        except OSError as exc:
            if exc.errno not in _NO_FULL_FLUSH:
                raise  # a disk error: an fsync after it could report a flush that never was
    os.fsync(fd)


def sync_folder(where: Path) -> None:
    """Flush a folder's entries to disk after a rename or link into it, as far as the disk
    allows. The file is already in place, so a folder that can't be opened or flushed (a
    network share, one others own, a disk error) never turns a written file into a failed
    write: a retry would read "already exists" (review rounds 29-30). A linked folder is
    followed: the rename already went through the link."""
    try:
        fd = os.open(where, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        flush_to_disk(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def private_folder(where: Path) -> None:
    """Make `where` 0700 for files that hold email text or case ids when it is new; a folder
    already there is left as it is, its new files 0600 (review round 29, fourteenth delta check:
    narrowing one the user already had, `~` or a shared folder, cut others off it)."""
    where.mkdir(mode=0o700, parents=True, exist_ok=True)


def open_append(path: Path, mode: int = 0o644, *, read: bool = False) -> int:
    """The one way an eval file is opened to add to it in place (review round 29, thirteenth
    delta check): append-only (`read` adds reading, to cut a crash's short last line), created
    `mode` when missing, opened without following a link and without waiting on a pipe, and
    refused unless it is a regular file with one name (a hard link would write into another
    file too). Returns the open descriptor."""
    refused = InvalidInputError(
        f"{escaped(str(path))}: a symbolic link, hard link or special file; nothing in an eval"
        " is added to through one"
    )
    flags = (os.O_RDWR if read else os.O_WRONLY) | os.O_APPEND | os.O_CREAT
    try:
        fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, mode)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENXIO}:  # a link; a pipe with no reader
            raise refused from exc
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise refused
    except BaseException:
        os.close(fd)
        raise
    return fd


def write_all(fd: int, data: bytes) -> None:
    """Write all of `data` to an open descriptor, finishing a short write."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def reason(exc: BaseException) -> str:
    """An error's reason for a message: its system message, else its text, else its type (an
    OSError raised without a message has no strerror; review rounds 27-28). Every eval message
    that reports an OSError's reason uses this, so none reads "None" or "()". An ecf error gives
    its whole message, what to do included: this is for whoever is told now; a record kept after
    the attempt ends takes `claude_eval._cause_of` instead (review round 30, delta checks 8-9)."""
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


def logical(path: Path) -> Path:
    """`path` made absolute, with no link resolved. A `..` is folded only when the name it
    cancels isn't a link; after a link it would go where the link points, so such a path is
    refused (review round 28, fourth delta check). Shared by the set location (`merged`) and
    `writable_set`'s set folder (review round 29), so neither can be reached by `link/..`."""
    p = _typed(path)
    if ".." not in p.parts:
        return Path(os.path.normpath(p))
    out = Path(p.anchor)
    for part in p.parts[1:]:
        if part != "..":
            out = out / part
        elif os.path.islink(out):
            raise InvalidInputError(
                f"{escaped(str(path))}: a `..` after the symbolic link {escaped(str(out))}",
                advice="give the folder without it",
            )
        else:
            out = out.parent
    return out


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
            " from its sources and never written",
            advice="choose a folder outside it (a card or label goes in the public or private"
            " set that owns the card)",
        )
    return path


def writable_set(root: Path, path: Path | None = None) -> Path:
    """Refuse a write into set `root` at `path` (the set's own folder when not given) that is
    in a merged view or could go through a link; return `path`, or `root`. Every writer into a
    set names the set: nothing in it is written through a link or into a special file
    (OD-705). The set's folders and files must be plain folders and files, and so must each
    name from `root` down to `path`, which must lie below `root` with no `..`; only `root`
    itself may be a link (an alias of the whole set), and a `..` in `root` after a link is
    refused (`logical`; review round 29). The refusal names what caused it (review round 28)."""
    target = root if path is None else path
    writable_root(target)
    top, full = _typed(root), _typed(target)
    below = full.parts[len(top.parts) :]
    if full.parts[: len(top.parts)] != top.parts or ".." in below:
        raise InvalidInputError(
            f"{escaped(str(target))}: not a path inside its set {escaped(str(root))}"
        )
    odd = _odd_entry(logical(root), below)
    if odd is not None:
        where, what = odd
        raise InvalidInputError(
            f"{escaped(str(where))}: {what}; nothing in an eval set is written through a link"
            " or into a special file",
            advice="put the folder or file itself there",
        )
    return target


def append_text(path: Path, text: str) -> None:
    """Append `text` to `path`, creating it 0644, through `open_append` (review rounds 28-29)."""
    fd = open_append(path)
    try:
        write_all(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
