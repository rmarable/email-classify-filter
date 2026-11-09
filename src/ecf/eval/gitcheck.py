"""Whether the gate's set is committed and pushed (planning-docs/EVAL-PRIVATE.md, "The gate
guard": dirty or stale clone).

`clean_and_pushed` runs `git` with the operator's repository config only (no system or global
config, no hooks, no fsmonitor, no optional locks), with a timeout; any error or timeout counts as
not clean. After a timeout the check backs off for a few calls, process-wide, still reporting not
clean. `fingerprint` reads the files a commit, a push or a new card changes, without `git`, so a
caller re-runs the check only when one of them changed. One function serves the service's gate
and M1's upload."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
import subprocess
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecf.eval.setio import canonical, reason

GIT = "/usr/bin/git"
TIMEOUT_S = 5.0  # for the whole check: every git call in one pass shares it
BACKOFF_S = 300.0  # after a timeout, no git for five minutes (five ticks), however often asked
IGNORED_NAMES = (".DS_Store",)
# the modes of a plain file in git: nothing else may sit in a set (review round 5, B-1)
REGULAR_MODES = ("100644", "100755")
_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
_FLAGS = ("--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
          "-c", "core.attributesFile=/dev/null")  # fmt: skip

_lock = threading.Lock()
_run_git = subprocess.run  # a seam for tests (a fixed binary, no shell)
_now = time.monotonic  # likewise
_backoff_until = 0.0
_deadline: ContextVar[float | None] = ContextVar("gitcheck_deadline", default=None)


@dataclass(frozen=True)
class Clean:
    ok: bool
    detail: str
    private_commit: str | None = None
    error: bool = False  # git failed or timed out: not cached across passes
    no_private: bool = False  # no eval/private/<set>: never "clean" (ok is False)


class GitFailed(Exception):
    def __init__(self, text: str, *, timed_out: bool = False) -> None:
        super().__init__(text)
        self.timed_out = timed_out


def argv(top: Path, *args: str) -> list[str]:
    return [GIT, "-C", str(top), *_FLAGS, *args]


def _git(top: Path, *args: str, timeout: float = TIMEOUT_S) -> subprocess.CompletedProcess[str]:
    end = _deadline.get()
    if end is not None:  # inside a check: what is left of its one timeout
        timeout = end - _now()
        if timeout <= 0:
            raise GitFailed(f"git timed out ({TIMEOUT_S:g} s for the check)", timed_out=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | _ENV
    try:
        return _run_git(argv(top, *args), capture_output=True, text=True, encoding="utf-8",
                        errors="surrogateescape", env=env, timeout=timeout,
                        check=False)  # fmt: skip
    except subprocess.TimeoutExpired as exc:
        raise GitFailed(f"git timed out after {timeout:g} s", timed_out=True) from exc
    except OSError as exc:
        raise GitFailed(f"git couldn't run ({reason(exc)})") from exc


def _ok(top: Path, *args: str) -> str:
    r = _git(top, *args)
    if r.returncode != 0:
        lines = (r.stderr or r.stdout).strip().splitlines()
        raise GitFailed(lines[-1] if lines else f"git {args[0]} failed")
    return r.stdout


def set_files(root: Path) -> dict[str, Path]:
    """Every file of a set folder, by its path relative to the folder, found by walking it (no
    `git`, no ignore rules), except `.build/` (built, ignored by design), `*.tmp` and `.DS_Store`
    (a crashed atomic write, Finder). Symbolic links (to files or folders) and other special
    files are listed, never followed: the clean check counts each as uncommitted (round 5, B-1).
    The one list both the clean check and the fingerprint read (review round 4, B3). Plain
    strings, one join per file: it runs on every gate pass."""
    out: dict[str, Path] = {}
    base = os.fspath(root)
    cut = len(base) + 1
    for d, dirs, files in os.walk(base):
        if d == base:
            dirs[:] = [x for x in dirs if x != ".build"]
        links = [x for x in dirs if os.path.islink(os.path.join(d, x))]
        rel = d[cut:]
        for name in (*files, *links):
            if name.endswith(".tmp") or name in IGNORED_NAMES:
                continue
            key = f"{rel}/{name}" if rel else name
            out[key] = Path(d, name)
    return out


def _blob_id(p: Path, algo: str) -> str | None:
    """The id git gives a regular file's content; None for anything else (a symbolic link, a
    pipe, a socket, a device), which no reader may follow (review round 5, B-1). Opened without
    following a link, and checked to be a regular file before it is read."""
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ELOOP:  # a symbolic link
            return None
        raise
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = f.read()
    h = hashlib.new(algo)
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def dirty(top: Path, pathspec: str, head: str | None = None) -> list[str]:
    """The paths under `pathspec` whose content differs from the commit `head` names (default:
    HEAD, read from the ref files): a file missing, added (untracked or ignored alike) or
    changed. Each file is hashed as git would and compared with that tree, so neither the index
    (an assume-unchanged or skip-worktree flag, a stale stat), `status` options nor ignore rules
    can make an edit look committed (review round 4, B3, B4). Anything that isn't a regular file,
    in the tree (a symbolic link, a submodule) or on disk, is always listed: a reader would
    follow it to content no commit holds (round 5, B-1). Raises GitFailed when HEAD can't be
    read."""
    head = read_head(top) if head is None else head
    if head is None:
        raise GitFailed(f"{top.name}'s HEAD can't be read from its ref files")
    algo = "sha256" if len(head) == 64 else "sha1"
    want: dict[str, str] = {}
    for entry in _ok(top, "ls-tree", "-r", "-z", head, "--", pathspec).split("\0"):
        if entry:
            meta, path = entry.split("\t", 1)
            mode, _kind, blob = meta.split()
            want[path] = blob if mode in REGULAR_MODES else ""  # "": never matches a file
    have = {f"{pathspec}/{rel}": p for rel, p in set_files(top / pathspec).items()}
    return sorted(
        {
            *(want.keys() ^ have.keys()),
            *(n for n in want.keys() & have.keys() if _differs(have[n], want[n], algo)),
        }
    )


def _differs(p: Path, blob: str, algo: str) -> bool:
    """Whether the file isn't that blob; a file that can't be read (removed mid-check, say)
    can't be shown to be it."""
    try:
        got = _blob_id(p, algo)
    except OSError:
        return True
    return got is None or got != blob


def git_dir(top: Path) -> tuple[Path, Path] | None:
    """(the repository's own git folder, its common folder), read without `git`: `.git` is a
    folder, or a worktree's `gitdir:` file with a `commondir`."""
    dot = top / ".git"
    if dot.is_dir():
        return dot, dot
    try:
        line = dot.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    own = (top / line[7:].strip()).resolve()
    try:
        common = (own / (own / "commondir").read_text(encoding="utf-8").strip()).resolve()
    except OSError:
        common = own
    return own, common


def _reftable(top: Path) -> bool:
    dirs = git_dir(top)
    if dirs is None:
        return False
    try:
        text = (dirs[1] / "config").read_text(encoding="utf-8")
    except OSError:
        return False
    return "refstorage" in text.lower().replace(" ", "")


def _check(top: Path, set_name: str) -> Clean:  # noqa: PLR0911 - one return per reason
    public = dirty(top, f"tests/eval/{set_name}")
    if public:
        return Clean(
            False, f"tests/eval/{set_name} has uncommitted changes: {', '.join(public[:5])}"
        )
    priv = top / "eval" / "private"
    if not private_half(priv, set_name):
        return Clean(False, f"no eval/private/{set_name} (public set only)", no_private=True)
    if _reftable(priv):
        return Clean(False, "eval/private uses reftable, which this check can't read; use files")
    # the private HEAD is read once: the commit compared, checked pushed and recorded is one
    # commit, whatever lands meanwhile (review round 5, B-3)
    head = read_head(priv)  # from the HEAD and ref files, never `git rev-parse` (the plan)
    if head is None:
        return Clean(False, "eval/private's HEAD can't be read from its ref files")
    private = dirty(priv, set_name, head)
    if private:
        return Clean(False, f"eval/private/{set_name} has uncommitted changes:"
                     f" {', '.join(private[:5])}")  # fmt: skip
    try:
        upstream = _ok(priv, "rev-parse", "--symbolic-full-name", "@{u}").strip()
    except GitFailed as exc:
        return Clean(False, f"eval/private has no upstream branch ({exc})")
    if not upstream.startswith("refs/remotes/"):
        return Clean(False, f"eval/private's upstream {upstream} isn't a remote branch")
    r = _git(priv, "merge-base", "--is-ancestor", head, "@{u}")
    if r.returncode != 0:
        return Clean(False, "eval/private has commits not pushed to its upstream")
    return Clean(True, f"private {head[:8]} committed and pushed", head)


SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")


def read_head(top: Path) -> str | None:
    """The commit HEAD names, read from the files: HEAD, then its loose ref or `packed-refs`."""
    dirs = git_dir(top)
    head = _read(dirs[0] / "HEAD") if dirs is not None else None
    if dirs is None or head is None:
        return None
    if not head.startswith("ref:"):
        return head if SHA.fullmatch(head) else None
    ref = head[4:].strip()
    sha = _read(dirs[1] / ref)
    if sha is None:  # not loose: packed
        packed = _read(dirs[1] / "packed-refs") or ""
        sha = next((p[0] for p in (ln.split() for ln in packed.splitlines())
                    if len(p) == 2 and p[1] == ref), None)  # fmt: skip
    return sha if sha is not None and SHA.fullmatch(sha) else None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def clean_and_pushed(top: Path, set_name: str, *, fresh: bool = False) -> Clean:
    """Whether `tests/eval/<set>` and `eval/private/<set>` have no uncommitted changes, and the
    private HEAD is on a branch whose remote-tracking upstream contains it. Never raises."""
    global _backoff_until  # noqa: PLW0603 - process-wide, shared by the tick, status, address_gate
    with _lock:
        if not fresh and _now() < _backoff_until:  # `stage set live` asks fresh: past it
            return Clean(False, "git timed out recently; the check is retried shortly", error=True)
    token = _deadline.set(_now() + TIMEOUT_S)
    try:
        return _check(top, set_name)
    except GitFailed as exc:
        if exc.timed_out:
            with _lock:
                _backoff_until = _now() + BACKOFF_S
        return Clean(False, f"git: {exc}", error=True)
    except OSError as exc:  # a folder or file of either repository that can't be read: not
        return Clean(False, f"{exc.filename or top}: can't be read ({reason(exc)})")  # clean
    finally:
        _deadline.reset(token)


def reset_backoff() -> None:
    global _backoff_until  # noqa: PLW0603
    with _lock:
        _backoff_until = 0.0


def _stat(p: Path) -> tuple[int, int, int, int] | None:
    try:
        st = p.stat()
    except OSError:
        return None  # a missing file is a value, not an error
    return st.st_size, st.st_mtime_ns, st.st_ino, st.st_ctime_ns


def _repo_stats(top: Path) -> tuple[Any, ...]:
    dirs = git_dir(top)
    if dirs is None:
        return (None,)
    own, common = dirs
    files = [own / "HEAD", common / "config", own / "config.worktree", own / "index",
             common / "packed-refs"]  # fmt: skip
    try:
        head = (own / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        head = ""
    if head.startswith("ref:"):
        files.append(common / head[4:].strip())
    remotes = common / "refs" / "remotes"
    if remotes.is_dir():
        files += sorted(p for p in remotes.rglob("*") if p.is_file())
    return tuple((str(f), _stat(f)) for f in files)


def _set_stats(root: Path) -> tuple[Any, ...]:
    """Name, size, mtime, inode and ctime of every file `set_files` lists (the clean check's own
    list). ctime, which no program can set, catches an in-place rewrite that restores the mtime
    (`touch -r`, `rsync --inplace -t`; review round 5, B-4)."""
    out: list[Any] = []
    for rel, p in sorted(set_files(root).items()):
        try:
            st = p.lstat()
        except OSError:
            continue
        out.append((rel, (st.st_size, st.st_mtime_ns, st.st_ino, st.st_ctime_ns)))
    return tuple(out)


def private_half(priv: Path, set_name: str) -> bool:
    """Whether `priv/<set>` is the private half, by the one rule `merged.paths` uses: present and
    stored exactly so (on a case-insensitive volume `eval/Private` answers `is_dir` too; review
    round 9)."""
    p = priv / set_name
    return p.is_dir() and canonical(p) == p


def fingerprint(top: Path, set_name: str) -> tuple[Any, ...]:
    """`_fingerprint`, never raising: a path that can't be read is a marker in it, so the key
    changes and the check that follows fails (review round 16)."""
    try:
        return _fingerprint(top, set_name)
    except OSError as exc:
        return ("unreadable", str(exc.filename))


def _fingerprint(top: Path, set_name: str) -> tuple[Any, ...]:
    """What a commit, a push, a new card or an in-place edit changes, in both repositories: the
    HEAD, config, branch ref, packed-refs, remote-tracking refs and index files, and every set
    file's name, size, mtime, inode and ctime (`set_files`). No `git`."""
    priv = top / "eval" / "private"
    return (_repo_stats(top), _set_stats(top / "tests" / "eval" / set_name),
            _repo_stats(priv) if priv.is_dir() else None,
            _set_stats(priv / set_name) if private_half(priv, set_name) else None)  # fmt: skip
