"""Set writers: atomic writes and the merged-folder guard (EVAL-PRIVATE.md "The overlay", W1,
OD-647)."""

from __future__ import annotations

import ast
import contextlib
import errno
import fcntl
import functools
import json
import os
import re
import stat
import symtable
import threading
import time
from collections import Counter
from collections.abc import Container, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ecf.cli_evalgen import note_old_temps
from ecf.errors import InvalidInputError
from ecf.eval import fixed_labels, generate, grade, review, setio
from ecf.eval import labels as label_file
from ecf.eval.builder import build_all
from ecf.eval.setio import (
    STALE_TEMP_S,
    atomic_write,
    copy_new,
    is_merged,
    old_temps_left,
    replace_file,
    sweep_old_temps,
    writable_root,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "ecf"
# macOS only: Linux's type stubs lack it, so it is read by name (as `setio` does)
FULLFSYNC: int | None = getattr(fcntl, "F_FULLFSYNC", None)

CARD = """---
id: {id}
title: test
from: "A <a@vendor-a.example>"
subject: "Hello"
---
Body line
## html
<p>Body line</p>
"""


class _Asked:
    """A model that records each call's folder and is never meant to be reached."""

    provider, model = "test", "m"

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def describe(self) -> dict[str, Any]:
        return {"model": self.model}

    def ask(self, prompt: str, schema: dict[str, Any], folder: Path) -> Any:
        self.calls.append(folder)
        raise AssertionError("a model was asked")


def _grading(root: Path) -> grade.Grading:
    return grade.Grading(root, _Asked())  # type: ignore[arg-type]


def _set(root: Path, *ids: str) -> Path:
    (root / "cases").mkdir(parents=True)
    for i in ids:
        (root / "cases" / f"{i}.md").write_text(CARD.format(id=i), encoding="utf-8")
    return root


def test_atomic_write_replaces_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    """A write leaves no temporary file of its own and never touches one already there (a
    crashed write's, or another writer's: ignored by git and every reader; review round 23)."""
    p = tmp_path / "labels.jsonl"
    stale = tmp_path / ".labels.jsonl.tmp"
    stale.write_text("stale half write")
    atomic_write(p, "one\n")
    atomic_write(p, b"two\n")
    assert p.read_text() == "two\n"
    assert stale.read_text() == "stale half write"
    assert sorted(f.name for f in tmp_path.iterdir()) == [".labels.jsonl.tmp", "labels.jsonl"]


def test_writable_root_refuses_a_merged_view(tmp_path: Path) -> None:
    merged = tmp_path / "eval" / "private" / ".merged" / "synthetic"
    merged.mkdir(parents=True)
    held = tmp_path / "copy"
    held.mkdir()
    (held / "merge.json").write_text('{"format": 2, "merged_version": "abc"}')
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "merge.json").write_text('{"format": 2, "merged_ver')  # cut short: counts
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "merge.json").symlink_to(held / "merge.json")
    for root in (merged, merged / "cases", held, held / "cases", broken, linked / "cases"):
        assert is_merged(root)
        with pytest.raises(InvalidInputError, match="merged eval set"):
            writable_root(root)
    plain = tmp_path / "tests" / "eval" / "synthetic"
    plain.mkdir(parents=True)
    assert writable_root(plain) == plain
    # another tool's merge.json above a set isn't a merged view (review round 27, delta check)
    (tmp_path / "tests" / "merge.json").write_text('{"strategy": "ours"}')
    assert writable_root(plain) == plain


def test_every_set_writer_refuses_a_merged_view(tmp_path: Path) -> None:
    root = tmp_path / ".merged" / "synthetic"
    _set(root, "t-1")
    for write in (
        lambda: build_all(root),
        lambda: label_file.write(root, []),
        lambda: fixed_labels.write(root, []),
        lambda: review.append_log(root, []),
    ):
        with pytest.raises(InvalidInputError, match="merged eval set"):
            write()
    assert not (root / "labels.jsonl").exists()


def test_build_skips_an_unchanged_file_and_repairs_a_damaged_one(tmp_path: Path) -> None:
    root = _set(tmp_path / "s", "t-1", "t-2")
    assert not build_all(root).findings
    one, two = root / "eml" / "t-1.eml", root / "eml" / "t-2.eml"
    before = one.stat().st_mtime_ns, one.stat().st_ino
    good = two.read_bytes()
    two.write_bytes(good[:-1] + b"X")  # same size, other bytes
    assert not build_all(root).findings
    assert (one.stat().st_mtime_ns, one.stat().st_ino) == before
    assert two.read_bytes() == good
    two.unlink()
    assert not build_all(root).findings
    assert two.read_bytes() == good
    rows = [json.loads(x) for x in (root / "labels.jsonl").read_text().splitlines()]
    assert [r["id"] for r in rows] == ["t-1", "t-2"]
    assert not list(root.rglob("*.tmp"))


# The write helpers themselves and the merged view's own writers (the view is written only by
# refresh_merged). Every other write in these files, and every call of a write helper, comes
# after a `writable_root` call in its function, so a new writer can't skip it; a helper that
# writes to a path its caller passes checks that path itself. No whole module is listed: an
# entry here is checked against the code, not taken from a comment (review round 27).
ALLOWED = {
    ("eval/setio.py", "atomic_write"),
    ("eval/setio.py", "replace_file"),  # the one way a set file is written (round 23)
    ("eval/setio.py", "write_new"),
    ("eval/setio.py", "append_text"),  # appends without following a link (round 28)
    ("eval/setio.py", "copy_new"),
    ("eval/setio.py", "write_durable"),  # replace_file, flushed (round 29, delta 13)
    ("eval/setio.py", "create_file"),  # a new file, never replacing one (round 29, delta 14)
    ("eval/setio.py", "private_folder"),  # a new page folder, 0700 (round 29, delta 14)
    ("eval/setio.py", "open_append"),  # the one in-place opener (round 29, delta 13)
    ("eval/setio.py", "write_all"),  # to `open_append`'s descriptor
    ("eval/setio.py", "_drop"),  # removes only this write's own temporary file
    ("eval/setio.py", "sweep_old_temps"),  # crashed writes' temporary files (round 24)
    ("eval/setio.py", "_sweep_folder"),  # the same, one folder (round 25)
    ("eval/setio.py", "end_call"),  # a model call's own folder, once read (round 29, delta 16)
    ("eval/setio.py", "_sweep_calls"),  # crashed calls' folders, held by none (delta 17)
    # refresh_merged, the one writer of the merged folder (EVAL-PRIVATE.md, "The merged set")
    ("eval/merged.py", "_place_copy"),
    ("eval/merged.py", "_place_link"),
    ("eval/merged.py", "_build"),
    ("eval/merged.py", "_copy_cards"),  # a case-only renamed copy's old name, removed first
    ("eval/merged.py", "_sweep"),  # orphans (review round 12)
    ("eval/merged.py", "_clear_extras"),  # anything else in the view, before copying (round 13)
    ("eval/merged.py", "_remove"),  # an extra inside the view, no link followed (round 14)
    ("eval/merged.py", "_chmod_at"),  # a view folder's mode, through its folder's handle (r27)
    ("eval/merged.py", "_clone_or_link"),  # a built email's temporary clone (round 23)
    ("eval/merged.py", "_open_lock"),  # the view's lock file, its flags passed in (round 25)
    ("eval/merged.py", "_writable"),  # the view's folders, made and given 0755 (round 26)
    ("eval/merged.py", "_mode"),  # a view file's or folder's mode (round 26)
    ("eval/merged.py", "_readable_view"),  # the view's own folders given 0755 (round 27)
    ("eval/merged.py", "refresh_merged"),  # the lock's folder in .merged/ (round 27)
}
GUARDS = {"writable_root", "writable_set"}  # a set's own folder or any path (round 28)
_PATH_WRITES = {
    "write_text",
    "write_bytes",
    "rename",
    "link",
    "copy",
    "copy2",
    "copyfile",
    "move",
    "unlink",
    "rmtree",
    "touch",
    "copytree",
    "clonefile",
    "atomic_write",
    "replace_file",
    "write_new",
    "copy_new",
    "append_text",
    "write_durable",
    "create_file",
    "open_append",
    "private_folder",
    "rmdir",
    "chmod",
    "lchmod",
    "mkdir",
    "makedirs",
    "symlink",
    "symlink_to",
    "hardlink_to",
    "copymode",
    "copystat",
    "mkstemp",
    "mkdtemp",
    "NamedTemporaryFile",
    "extract",
    "extractall",
    "make_archive",
    "unpack_archive",
    # the merged view's own write helpers: their callers are writers too (review round 27)
    "_writable",
    "_mode",
    "_remove",
    "_chmod_at",
    "_place_copy",
    "_place_link",
    "_clone_or_link",
}  # fmt: skip (rounds 24, B2, 26 and 27)
# os functions that write only when called through the os module (a list's `remove` doesn't)
_OS_WRITES = {"replace", "rename", "renames", "link", "remove", "rmdir", "removedirs", "truncate",
              "utime", "chown", "lchown", "mkfifo", "mknod", "open", "write", "pwrite", "writev",
              "ftruncate", "fchmod", "fchown", "chflags", "lchflags", "posix_fallocate",
              "sendfile", "copy_file_range"}  # fmt: skip
_READ_FLAGS = {"O_RDONLY", "O_NOFOLLOW", "O_NONBLOCK", "O_DIRECTORY", "O_CLOEXEC", "O_EVTONLY"}


def _os_aliases(tree: ast.AST) -> tuple[set[str], dict[str, str]]:
    """The names `os` and `shutil` are bound to in a module (`import os`, `import os as _os`,
    `import shutil`), and the os and shutil functions imported by name (`from os import open as
    o` gives o -> open; review rounds 26-27)."""
    mods: set[str] = {"os"}
    funcs: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.asname or a.name for a in node.names if a.name in {"os", "shutil"}}
        elif isinstance(node, ast.ImportFrom) and node.module in {"os", "shutil"}:
            funcs |= {a.asname or a.name: a.name for a in node.names}
    return mods, funcs


def _read_only(flags: ast.expr | None, mods: frozenset[str] = frozenset({"os"}),
               consts: dict[str, ast.expr] | None = None) -> bool:  # fmt: skip
    """Whether `os.open`'s flags are written out as read-only flags alone (`os.O_RDONLY | ...`,
    or `getattr(os, "O_EVTONLY", 0)`): flags held in a variable or computed count as a write
    (review round 25, S3), except a module constant set once to such flags (`consts`, review
    round 29, thirteenth delta check)."""
    if isinstance(flags, ast.BinOp) and isinstance(flags.op, ast.BitOr):
        return _read_only(flags.left, mods, consts) and _read_only(flags.right, mods, consts)
    if isinstance(flags, ast.Name) and flags.id in (consts or {}):
        return _read_only((consts or {})[flags.id], mods)
    if isinstance(flags, ast.Attribute):
        return flags.attr in _READ_FLAGS and getattr(flags.value, "id", "") in mods
    if isinstance(flags, ast.Call) and getattr(flags.func, "id", "") == "getattr":
        a = flags.args
        return (
            len(a) == 3
            and getattr(a[0], "id", "") in mods
            and isinstance(a[1], ast.Constant)
            and a[1].value in _READ_FLAGS
            and isinstance(a[2], ast.Constant)
            and a[2].value == 0
        )
    return False


def _is_write(
    call: ast.Call,
    mods: frozenset[str] = frozenset({"os"}),
    funcs: dict[str, str] | None = None,
    modules: frozenset[str] = frozenset(),
) -> bool:
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
    via_os = getattr(getattr(f, "value", None), "id", "") in mods
    if isinstance(f, ast.Name) and name in (funcs or {}):  # from os import x [as y]
        name, via_os = (funcs or {})[name], True
    path_replace = (  # Path.replace(target): one argument; str.replace takes two (round 26)
        name == "replace"
        and isinstance(f, ast.Attribute)
        and getattr(f.value, "id", "") not in {"dataclasses", *mods}
        and len(call.args) == 1
        and not call.keywords
    )
    if name in _PATH_WRITES or path_replace or (via_os and name in _OS_WRITES - {"open"}):
        return True
    if via_os and name == "open":  # os.open(flags)
        flags = call.args[1] if len(call.args) > 1 else None
        flags = next((k.value for k in call.keywords if k.arg == "flags"), flags)
        return not _read_only(flags, mods)
    if name in {"open", "fdopen", "ZipFile"}:
        given = [*call.args[1:2], *(k.value for k in call.keywords if k.arg == "mode")]
        # open(name, mode) and a module's open (io, gzip, tarfile...: reads by default) take the
        # mode second; any other receiver's open is Path.open(mode), whatever the receiver is
        # (a name, `(a / b)`, a call's or an attribute's result: review round 28)
        module = isinstance(f, ast.Name) or (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id in modules | mods
        )
        if not given and name == "open" and not module:
            given = call.args[:1]  # a mode in a variable counts (round 27)
        # a mode that isn't written out counts as a write, as os.open's flags do (round 27)
        return any(not (isinstance(m, ast.Constant) and isinstance(m.value, str))
                   or any(c in m.value for c in "wax+") for m in given)  # fmt: skip
    return False


def _writes(path: Path) -> list[tuple[str, int, bool]]:
    """(enclosing function's qualified name, line, whether that function calls writable_root
    before the write) for every file write in `path` (the order: review round 27)."""
    tree = _parsed(path)
    mods, funcs = _os_aliases(tree)
    modules = frozenset(  # every module name bound by an import
        (a.asname or a.name).split(".")[0]
        for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    )  # fmt: skip
    found: list[tuple[str, int, bool]] = []

    def guards(fn: ast.AST) -> list[ast.Call]:
        """The `writable_root` calls in a function's own body (a nested def's are its own)."""
        out: list[ast.Call] = []
        todo = list(ast.iter_child_nodes(fn))
        while todo:
            n = todo.pop()
            if isinstance(n, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                continue  # a nested def's or lambda's guard runs only if it is called
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") in GUARDS:
                out.append(n)
            todo.extend(ast.iter_child_nodes(n))
        return out

    def visit(node: ast.AST, scope: list[str], held: list[ast.Call]) -> None:
        for child in ast.iter_child_nodes(node):
            s, h = scope, held
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                s = [*scope, child.name]
                h = [] if isinstance(child, ast.ClassDef) else guards(child)
            if isinstance(child, ast.Call) and _is_write(child, frozenset(mods), funcs, modules):
                # guarded when a check comes first by line order: on an earlier line, or inside
                # the write's own arguments. Line order only, not path or variable analysis: a
                # guard in another branch, or of another path, still counts (review round 27)
                inside = {id(n) for n in ast.walk(child)}
                ok = any(g.lineno < child.lineno or id(g) in inside for g in h)
                found.append((".".join(scope), child.lineno, ok))
            visit(child, s, h)

    visit(tree, [], [])
    return found


def test_no_set_writer_skips_the_guard() -> None:
    files = [*sorted((SRC / "eval").glob("*.py")),
             SRC / "cli.py", SRC / "cli_evalgen.py", SRC / "cli_card_review.py"]  # fmt: skip
    stray: list[str] = []
    for f in files:
        rel = str(f.relative_to(SRC))
        for scope, line, guarded in _writes(f):
            if guarded or (rel, scope) in ALLOWED:
                continue
            stray.append(f"{rel}:{line} ({scope or 'module'})")
    assert not stray, "write without writable_root: " + ", ".join(stray)


def test_the_writer_check_sees_the_write_helpers(tmp_path: Path) -> None:
    """The guard check counts `replace_file`, `write_new`, `copy_new` and any `os.open` whose
    flags aren't written out as read-only (a variable's count) as writes, so a writer using them
    without `writable_root` is caught (review rounds 24, B2, and 25, S3)."""
    mod = tmp_path / "w.py"
    mod.write_text(
        "import os\n"
        "def a(p, d):\n    replace_file(p, lambda t: write_new(t, d))\n"
        "def b(p, d):\n    setio.write_new(p, d)\n"
        "def c(s, p):\n    copy_new(s, p)\n"
        "def d(p):\n    os.open(p, os.O_WRONLY | os.O_CREAT)\n"
        "def e(p):\n    os.open(p, os.O_RDONLY | os.O_NOFOLLOW)\n"
        "def f(p, flags):\n    os.open(p, flags | os.O_NOFOLLOW)\n"
        "def g(p):\n    os.open(p, flags=os.O_RDWR)\n"
        "def h(p):\n    os.open(p, os.O_RDONLY | getattr(os, 'O_EVTONLY', 0))\n"
        "def i(p):\n    os.open(p)\n"
    )
    assert sorted({w[0] for w in _writes(mod) if not w[2]}) == ["a", "b", "c", "d", "f", "g", "i"]


def test_the_writer_check_sees_os_under_any_name(tmp_path: Path) -> None:
    """`os` imported under another name, os functions imported by name, and os.remove, rmdir,
    removedirs and truncate count as writes; a list's `remove` and a read-only open under an
    alias don't (review round 26)."""
    mod = tmp_path / "w.py"
    mod.write_text(
        "import os as _os\n"
        "from os import open as o, remove, rmdir as rd\n"
        "def a(p):\n    _os.open(p, _os.O_WRONLY)\n"
        "def b(p):\n    o(p, _os.O_RDWR)\n"
        "def c(p):\n    remove(p)\n"
        "def d(p):\n    rd(p)\n"
        "def e(p):\n    _os.truncate(p, 0)\n"
        "def f(p):\n    _os.removedirs(p)\n"
        "def g(p):\n    _os.replace(p, p)\n"
        "def h(p):\n    _os.open(p, _os.O_RDONLY | _os.O_NOFOLLOW)\n"
        "def i(xs):\n    xs.remove(1)\n"
        "def j(p):\n    o(p, _os.O_RDONLY)\n"
    )
    assert sorted({w[0] for w in _writes(mod) if not w[2]}) == ["a", "b", "c", "d", "e", "f", "g"]


def test_the_writer_check_sees_more_writes_and_the_view_helpers(tmp_path: Path) -> None:
    """os renames/utime/chown/lchown/mkfifo/mknod, shutil copymode/copystat/chown, tempfile
    files, a zip opened for writing, an open whose mode isn't written out, and a call of the
    merged view's write helpers count as writes; a plain read doesn't. A one-argument open of
    another object (`zf.open(name)`) counts too: it can't be told from `Path.open(mode)` with the
    mode in a variable (review round 27, delta check)."""
    mod = tmp_path / "w.py"
    mod.write_text(
        "import os, shutil, tempfile, zipfile\n"
        "def a(p, q):\n    os.renames(p, q)\n"
        "def b(p):\n    os.utime(p)\n"
        "def c(p):\n    os.chown(p, 1, 1)\n"
        "def d(p):\n    os.mkfifo(p)\n"
        "def e(p, q):\n    shutil.copystat(p, q)\n"
        "def f(p):\n    shutil.chown(p, 'x')\n"
        "def g(d):\n    tempfile.mkstemp(dir=d)\n"
        "def h(d):\n    tempfile.NamedTemporaryFile(dir=d, delete=False)\n"
        "def i(p):\n    zipfile.ZipFile(p, 'w')\n"
        "def j(p, m):\n    open(p, m)\n"
        "def k(p, m):\n    p.open(mode=m)\n"
        "def l(d):\n    _writable(d)\n"
        "def m(s, d):\n    _place_copy(s, d)\n"
        "def n(p):\n    _mode(p, 0o777)\n"
        "def o(p):\n    open(p, 'r+')\n"
        "def r1(p):\n    return open(p).read()\n"
        "def r2(p):\n    return p.open('rb')\n"
        "def p(zf, name):\n    return zf.open(name)\n"
        "def r4(p):\n    return zipfile.ZipFile(p)\n"
    )
    assert sorted({w[0] for w in _writes(mod) if not w[2]}) == [
        "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n", "o", "p"
    ]  # fmt: skip


@pytest.mark.parametrize("resume", [False, True])
def test_generate_refuses_a_merged_view_before_writing(tmp_path: Path, resume: bool) -> None:
    """A generated set's folder inside a merged view is refused by `start` before anything is
    made or bought, and the work log refuses it too (review round 27)."""
    from ecf.eval import generate as gen  # noqa: PLC0415

    root = tmp_path / "eval" / "private" / ".merged" / "synthetic" / "new"
    with pytest.raises(InvalidInputError, match="merged eval set"):
        gen.start(root, resume=resume)
    assert not root.exists()
    with pytest.raises(InvalidInputError, match="merged eval set"):
        gen.Work(root).append("seeds", [{"id": "s-1"}])
    assert not root.exists()


@pytest.mark.parametrize("way", ["clone", "hard link"])
def test_a_view_copy_never_follows_a_swapped_source_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, way: str
) -> None:
    """A `.build/` source swapped for a link after it was listed is placed as a link (which
    every reader refuses), never as its target's bytes, by a clone or the hard-link fallback
    (review round 27)."""
    from ecf.eval import merged  # noqa: PLC0415

    if way == "hard link":
        monkeypatch.setattr(merged, "_CLONE", None)

    target = tmp_path / "secret.eml"
    target.write_bytes(b"target bytes")
    src = tmp_path / "src.eml"
    src.symlink_to(target)
    tmp = tmp_path / "out.eml"
    merged._clone_or_link(src, tmp)  # pyright: ignore[reportPrivateUsage]
    assert tmp.is_symlink() or tmp.read_bytes() != b"target bytes"


def test_a_temp_the_sweep_cannot_check_is_named_as_such(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A temporary file's name the sweep can't stat is reported as not checked, not as an old
    file of a crashed write (review round 27)."""
    root = _set(tmp_path / "tests" / "eval" / "synthetic", "t-1")
    t = _old_temp(root / "cases")
    real_stat = os.stat

    def denied(path: object, *args: object, **kw: object) -> os.stat_result:
        if path == t.name:
            raise PermissionError(errno.EACCES, "denied", path)
        return real_stat(path, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "stat", denied)
    [line] = sweep_old_temps(root)[1]
    assert line.startswith(f"{t}: a temporary file's name, couldn't check it") and t.exists()


def test_the_writer_check_sees_path_and_shutil_writes(tmp_path: Path) -> None:
    """Path.replace, rmdir, chmod, mkdir, makedirs, symlink_to, hardlink_to and shutil functions
    imported under another name count as writes; str.replace, dataclasses.replace and a
    datetime's replace(...=...) don't (review round 26, delta check)."""
    mod = tmp_path / "w.py"
    mod.write_text(
        "import dataclasses\n"
        "from shutil import copyfile as cf, rmtree\n"
        "def a(t, p):\n    t.replace(p)\n"
        "def b(p):\n    p.rmdir()\n"
        "def c(p):\n    p.chmod(0o644)\n"
        "def d(p):\n    p.mkdir()\n"
        "def e(p):\n    os.makedirs(p)\n"
        "def f(p, q):\n    p.symlink_to(q)\n"
        "def g(p, q):\n    p.hardlink_to(q)\n"
        "def h(s, p):\n    cf(s, p)\n"
        "def i(p):\n    rmtree(p)\n"
        "def j(s):\n    return s.replace('a', 'b')\n"
        "def k(x):\n    return dataclasses.replace(x)\n"
        "def m(d):\n    return d.replace(tzinfo=None)\n"
    )
    assert sorted({w[0] for w in _writes(mod) if not w[2]}) == [
        "a", "b", "c", "d", "e", "f", "g", "h", "i"
    ]  # fmt: skip


def test_a_copy_names_its_unreadable_source_never_the_file_it_writes(tmp_path: Path) -> None:
    """An unreadable source is named as itself (opened before the temporary file), and
    nothing is left beside the file being written (review round 24, B3)."""
    src, dst = tmp_path / "src.md", tmp_path / "out" / "src.md"
    src.write_text("x")
    dst.parent.mkdir()
    src.chmod(0o000)
    try:
        with pytest.raises(PermissionError) as got:
            replace_file(dst, lambda t: copy_new(src, t))
    finally:
        src.chmod(0o644)
    assert got.value.filename == str(src)
    assert list(dst.parent.iterdir()) == []


@pytest.mark.parametrize("kind", ["pipe", "link"])
def test_a_copy_refuses_a_pipe_or_a_link_at_once(tmp_path: Path, kind: str) -> None:
    """A source swapped for a pipe is refused without blocking (the refresh holds the view's
    lock), and a link is never followed (review round 24, B4)."""
    src, dst = tmp_path / "src.md", tmp_path / "out" / "src.md"
    dst.parent.mkdir()
    if kind == "pipe":
        os.mkfifo(src)
    else:
        (tmp_path / "real.md").write_text("x")
        src.symlink_to(tmp_path / "real.md")
    done = threading.Event()
    caught: list[BaseException] = []

    def run() -> None:
        try:
            replace_file(dst, lambda t: copy_new(src, t))
        except BaseException as exc:
            caught.append(exc)
        done.set()

    threading.Thread(target=run, daemon=True).start()
    if not done.wait(5):
        with contextlib.suppress(OSError):  # unblock a reader, never blocking (round 25, P1)
            os.close(os.open(src, os.O_WRONLY | os.O_NONBLOCK))
        pytest.fail("the copy blocked on a pipe")
    assert caught and isinstance(caught[0], InvalidInputError), caught
    assert "symbolic link or special file" in str(caught[0])
    assert list(dst.parent.iterdir()) == []


def test_an_error_on_the_temporary_file_names_the_file_being_written(tmp_path: Path) -> None:
    """Any error naming the temporary file (here a missing folder) names the file being
    written instead, with no exception chained to itself (review round 24, B5)."""
    path = tmp_path / "no-folder" / "labels.jsonl"
    with pytest.raises(FileNotFoundError) as got:
        atomic_write(path, "x")
    assert got.value.filename == str(path)
    assert got.value.__cause__ is not got.value
    assert got.value.errno == errno.ENOENT


def test_old_temporary_files_of_crashed_writes_are_swept(tmp_path: Path) -> None:
    """A crashed write's temporary file older than an hour is removed by the next build; a
    fresh one (a write may be running), another name, a link and a folder are left (round 24,
    B6)."""
    root = _set(tmp_path / "tests" / "eval" / "synthetic", "t-1")
    old = time.time() - STALE_TEMP_S - 60
    made = {
        "old": root / "cases" / ".t-1.md.0123456789ab.tmp",
        "old top": root / ".labels.jsonl.ba9876543210.tmp",
        "fresh": root / "cases" / ".t-1.md.abcdefabcdef.tmp",
        "other name": root / "cases" / ".t-1.md.tmp",
        "not hex": root / "cases" / ".t-1.md.0123456789zz.tmp",
    }
    for p in made.values():
        p.write_text("half")
    link = root / "cases" / ".x.md.aaaaaaaaaaaa.tmp"
    link.symlink_to(made["fresh"])
    folder = root / "cases" / ".y.md.bbbbbbbbbbbb.tmp"
    folder.mkdir()
    for k in ("old", "old top", "other name", "not hex"):
        os.utime(made[k], (old, old))
    os.utime(folder, (old, old))
    os.utime(link, (old, old), follow_symlinks=False)
    assert build_all(root).left == []
    assert not made["old"].exists() and not made["old top"].exists()
    assert all(made[k].exists() for k in ("fresh", "other name", "not hex"))
    assert link.is_symlink() and folder.is_dir()
    assert sweep_old_temps(root) == ([], [])


def _old_temp(folder: Path, name: str = ".t-1.md.0123456789ab.tmp") -> Path:
    t = folder / name
    t.write_text("half")
    old = time.time() - STALE_TEMP_S - 60
    os.utime(t, (old, old))
    return t


@pytest.mark.parametrize("linked", ["cases", "eml", ".build", "the set folder"])
def test_the_sweep_never_follows_a_linked_folder(tmp_path: Path, linked: str) -> None:
    """A linked `cases/`, `eml/`, `.build/` or set folder is skipped: an old temporary file in
    its target is left (review round 25, S1). A set holding a linked folder isn't swept at all,
    with one line naming the link, as every writer refuses it (review round 28)."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    theirs = _old_temp(elsewhere)
    real = _set(tmp_path / "real", "t-1")
    root = tmp_path / "tests" / "eval" / "synthetic"
    if linked == "the set folder":
        root.parent.mkdir(parents=True)
        root.symlink_to(elsewhere, target_is_directory=True)
        gone, [line] = sweep_old_temps(root)  # skipped, with a line (review round 29)
        assert gone == [] and "the set folder is a symbolic link" in line
    else:
        root.mkdir(parents=True)
        (root / linked).symlink_to(elsewhere, target_is_directory=True)
        if linked != "cases":
            (root / "cases").symlink_to(real / "cases", target_is_directory=True)
        gone, [line] = sweep_old_temps(root)
        assert gone == [] and line.startswith(f"{root}: old temporary files not checked")
        assert "a symbolic link;" in line
    assert theirs.exists()


def test_the_sweep_never_fails_a_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An old temporary file the build can't remove (its folder not writable) is left and
    reported as a line, and one that vanishes while it is looked at is skipped; the build
    succeeds either way (review round 25, S2)."""
    root = _set(tmp_path / "tests" / "eval" / "synthetic", "t-1")
    build_all(root)
    stuck = _old_temp(root / "eml")
    vanishing = _old_temp(root / "cases", ".t-1.md.ffffffffffff.tmp")
    real_stat = os.stat

    def stat_gone(path: object, *args: object, **kw: object) -> os.stat_result:
        if path == vanishing.name:
            raise FileNotFoundError(errno.ENOENT, "gone", path)
        return real_stat(path, *args, **kw)  # type: ignore[arg-type]

    if os.geteuid() == 0:
        pytest.skip("root can remove a file from a read-only folder")
    monkeypatch.setattr(os, "stat", stat_gone)
    (root / "eml").chmod(0o555)
    try:
        report = build_all(root)
    finally:
        (root / "eml").chmod(0o755)
    assert not report.findings and report.built == ["t-1"]
    assert stuck.exists() and vanishing.exists()
    assert len(report.left) == 1 and report.left[0].startswith(f"{stuck}: an old temporary")


@pytest.mark.parametrize("folder", ["eml", ".build"])
def test_the_sweep_removes_old_temps_from_eml_and_build(tmp_path: Path, folder: str) -> None:
    """An old temporary file in `eml/` or `.build/` is removed too, a fresh one kept
    (review round 26)."""
    root = _set(tmp_path / "tests" / "eval" / "synthetic", "t-1")
    build_all(root)
    (root / folder).mkdir(exist_ok=True)
    old = _old_temp(root / folder)
    fresh = root / folder / ".t-1.eml.abcdefabcdef.tmp"
    fresh.write_text("half")
    assert build_all(root).left == []
    assert not old.exists() and fresh.exists()


def test_the_sweep_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A root that turns out to be inside a merged view, or can't be checked, is skipped with a
    line, and its old temporary files are left (review round 26)."""
    root = _set(tmp_path / "tests" / "eval" / "synthetic", "t-1")
    old = _old_temp(root / "cases")
    (root / "merge.json").write_text('{"merged_version": "abc"}')  # a view's own record
    [line] = sweep_old_temps(root)[1]
    assert line.startswith(f"{root}: old temporary files not checked")
    (root / "merge.json").unlink()

    def denied(_root: Path) -> str | None:
        raise PermissionError(errno.EACCES, "denied", str(root))

    monkeypatch.setattr(setio, "merged_by", denied)
    gone, [line] = sweep_old_temps(root)
    assert gone == [] and "denied" in line and old.exists()


def test_old_temps_are_named_escaped_and_reported_by_other_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A note names the file with control characters escaped, and the read-only listing the
    rebuilding commands print finds what the build left, removing nothing (review round 26)."""
    root = _set(tmp_path / "tests" / "eval" / "synthetic", "t-1")
    build_all(root)
    odd = _old_temp(root / "cases", ".t\x1b[2J.md.0123456789ab.tmp")
    [line] = old_temps_left(root)
    assert "\x1b" not in line and "\\u001b[2J" in line and odd.exists()
    assert build_all(root).left == [] and not odd.exists()
    assert old_temps_left(root) == []
    _old_temp(root / "eml")
    note_old_temps(root)
    err = capsys.readouterr().err
    assert err.startswith(f"note: {root / 'eml'}/.t-1.md.0123456789ab.tmp: an old temporary")


def test_the_sweep_runs_only_after_the_build_has_written(tmp_path: Path) -> None:
    """A build stopped by hygiene writes nothing, old temporary files included, so
    `build_all`'s promise holds (review round 25, P2)."""
    root = _set(tmp_path / "tests" / "eval" / "synthetic", "t-1")
    (root / "cases" / "t-1.md").write_text(
        CARD.format(id="t-1").replace("a@vendor-a.example", "a@realcorp.com")
    )
    old = _old_temp(root / "cases")
    assert build_all(root).findings
    assert old.exists()


def test_a_write_whose_temporary_file_was_swept_says_so(tmp_path: Path) -> None:
    """A write paused for over an hour whose temporary file a build removed meanwhile is told
    so, not "no such file" for the file it writes (review round 25, S4)."""
    path = tmp_path / "labels.jsonl"

    def make_then_swept(tmp: Path) -> None:
        tmp.write_text("x")
        tmp.unlink()

    with pytest.raises(InvalidInputError, match="temporary file was removed while it was being"):
        replace_file(path, make_then_swept)
    assert list(tmp_path.iterdir()) == []


def test_the_writer_check_sees_descriptor_writes_archives_and_the_order(tmp_path: Path) -> None:
    """Writes through a descriptor (os.write, ftruncate, fchmod, fchown), chflags, archive
    extraction and creation, and Path.open with its mode in a variable count as writes; a guard
    counts only when it comes before the write in the function's own body, not after it and not
    in a nested def or a lambda; a file object's write, os.read and a module's open(name) (io,
    gzip, tarfile) don't count (review round 27, delta checks)."""
    mod = tmp_path / "w.py"
    mod.write_text(
        "import os, shutil, io, gzip, tarfile as tf\n"
        "def a(fd):\n    os.write(fd, b'x')\n"
        "def b(fd):\n    os.ftruncate(fd, 0)\n"
        "def c(fd):\n    os.fchmod(fd, 0o644)\n"
        "def d(fd):\n    os.fchown(fd, 1, 1)\n"
        "def e(p):\n    os.chflags(p, 0)\n"
        "def f(zf, d):\n    zf.extractall(d)\n"
        "def g(b, d):\n    shutil.make_archive(b, 'zip', d)\n"
        "def h(p, m):\n    p.open(m)\n"
        "def i(r):\n    r.mkdir()\n    writable_root(r)\n"
        "def j(r):\n    def inner():\n        writable_root(r)\n    r.mkdir()\n"
        "def ok1(r):\n    writable_root(r)\n    r.mkdir()\n"
        "def ok2(r):\n    atomic_write(\n        writable_root(r) / 'x', 'y')\n"
        "def k(r):\n    later = lambda: writable_root(r)\n    r.mkdir()\n"
        "def ok3(f, fd):\n    f.write('x')\n    os.read(fd, 1)\n"
        "def ok4(p):\n    io.open(p)\n    gzip.open(p)\n    tf.open(p)\n"
    )
    assert sorted({w[0] for w in _writes(mod) if not w[2]}) == [
        "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"
    ]  # fmt: skip


def test_grading_refuses_a_merged_view_before_any_call(tmp_path: Path) -> None:
    """`Grading.run` refuses a set in a merged view before a call is made or a file written
    (review round 27, delta check)."""
    from ecf.eval import grade as grading  # noqa: PLC0415

    class NoCall:
        provider, model = "fake", "fake-model-1"

        def ask(self, *_a: object) -> None:
            raise AssertionError("a call was made")

    root = tmp_path / "eval" / "private" / ".merged" / "synthetic" / "g"
    with pytest.raises(InvalidInputError, match="merged eval set"):
        grading.Grading(root, NoCall()).run(resume=True)  # type: ignore[arg-type]
    assert not root.exists()


def test_a_helper_that_writes_where_told_checks_the_path(tmp_path: Path) -> None:
    """A call, the canary, the settle page, the mix file, the corpus labels and an exported page
    refuse a path in a merged view themselves, before anything is made or asked (review round
    27, delta checks)."""
    from ecf.cli_evalgen import _write_private  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415
    from ecf.eval import corpus_labels, tools  # noqa: PLC0415
    from ecf.eval import mix as mixmod  # noqa: PLC0415
    from ecf.eval import settle as st  # noqa: PLC0415
    from tests.test_eval_generate import fake_tool  # noqa: PLC0415

    view = tmp_path / "eval" / "private" / ".merged" / "synthetic"
    tool, environ = fake_tool(tmp_path, {})
    adapter = tools.CliAdapter(tool, "fake-model-1", environ)
    for write in (
        lambda: adapter.ask("p", {}, view / "work" / "call"),
        lambda: tools.canary(adapter, view / "canary"),
        lambda: st.export(tmp_path / "unused", view / "page"),
        lambda: mixmod.write_default(view / "mix.yaml"),
        lambda: corpus_labels.save(view / "labels.jsonl", {}),
        lambda: _write_private(view / "page" / "calibration.json", "{}"),
    ):
        with pytest.raises(InvalidInputError, match="merged eval set"):
            write()
    assert not view.exists()
    assert not (tmp_path / "calls.log").exists()  # no call made


@pytest.mark.parametrize("command", ["generate", "grade"])
def test_the_commands_refuse_a_merged_view_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """`ecf eval generate` and `grade` refuse a set in a merged view before the provider is
    checked or the canary call made (review round 27, delta check)."""
    from typer.testing import CliRunner  # noqa: PLC0415

    from ecf import cli_evalgen  # noqa: PLC0415
    from ecf.cli import app  # noqa: PLC0415

    def no_canary(_adapter: object) -> None:
        raise AssertionError("the canary ran")

    monkeypatch.setattr(cli_evalgen, "_checked", no_canary)
    monkeypatch.setenv("PATH", str(tmp_path / "no-tools"))
    monkeypatch.setenv("ECF_HOME", str(tmp_path / "home"))
    root = tmp_path / "eval" / "private" / ".merged" / "synthetic" / "new"
    r = CliRunner().invoke(app, ["eval", command, "--root", str(root), "--yes"])
    assert r.exit_code != 0
    assert "merged eval set" in r.output + str(r.exception), r.output
    assert not root.exists()


def test_a_view_mode_change_never_follows_a_link(tmp_path: Path) -> None:
    """The merged view's mode change leaves a link alone, so a folder or file swapped for one
    never has its target's mode changed (review round 27, delta check)."""
    from ecf.eval import merged  # noqa: PLC0415

    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (tmp_path / "f").write_text("x")
    (tmp_path / "f").chmod(0o600)
    for target, mode in ((outside, 0o755), (tmp_path / "f", 0o444)):
        link = tmp_path / f"link-{target.name}"
        link.symlink_to(target)
        merged._mode(link, mode)  # pyright: ignore[reportPrivateUsage]
    assert stat.S_IMODE(outside.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "f").stat().st_mode) == 0o600


def test_removing_a_view_folder_never_walks_a_swapped_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder inside the view swapped for a link after it was found is refused, never walked:
    the files it points at stay (review round 27, delta check)."""
    from ecf.eval import merged  # noqa: PLC0415

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.eml").write_text("keep")
    view = tmp_path / "view"
    (view / "extra" / "sub").mkdir(parents=True)
    real_chmod_at = merged._chmod_at  # pyright: ignore[reportPrivateUsage]

    def swap(fd: int, name: str, mode: int) -> None:
        if name == "sub":  # swapped between the listing and the open
            (view / "extra" / "sub").rmdir()
            (view / "extra" / "sub").symlink_to(outside)
        real_chmod_at(fd, name, mode)

    monkeypatch.setattr(merged, "_chmod_at", swap)
    with pytest.raises(OSError):
        merged._remove(view / "extra")  # pyright: ignore[reportPrivateUsage]
    assert (outside / "keep.eml").read_text() == "keep"


def test_a_note_never_reads_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An OSError with no message is named by its type in a sweep's note, not as "None"
    (review round 27, delta check)."""
    root = _set(tmp_path / "tests" / "eval" / "synthetic", "t-1")
    t = _old_temp(root / "cases")
    real_unlink = os.unlink

    def bare(path: object, *args: object, **kw: object) -> None:
        if path == t.name:
            raise OSError
        real_unlink(path, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "unlink", bare)
    [line] = sweep_old_temps(root)[1]
    assert line.endswith("left in place (OSError); remove it when you can") and t.exists()


def test_a_path_inside_the_view_never_reads_its_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path under a folder named `.merged` is refused by name, with no `merge.json` read (a
    real view's holds all its manifests; review round 27, delta check)."""

    def no_read(_path: Path) -> bool:
        raise AssertionError("a merge.json was read")

    monkeypatch.setattr(setio, "_view_record", no_read)
    assert is_merged(tmp_path / "eval" / "private" / ".merged" / "synthetic" / "cases" / "x.md")


def test_a_view_folder_in_any_case_is_refused_and_named(tmp_path: Path) -> None:
    """APFS ignores case, so `.Merged` is the view folder: refused before it exists and after,
    and the refusal names what made the path a view (review round 28)."""
    priv = tmp_path / "eval" / "private"
    for typed in (".Merged", ".MERGED"):
        named = r"merged eval set \(the folder .*/\.(Merged|MERGED)\)"
        with pytest.raises(InvalidInputError, match=named):
            writable_root(priv / typed / "synthetic" / "new")
    (priv / ".Merged").mkdir(parents=True)
    for typed in (".merged", ".Merged"):
        assert is_merged(priv / typed / "synthetic" / "new")
    held = tmp_path / "copy"
    held.mkdir()
    (held / "merge.json").write_text("")  # not JSON: counts, and is named
    with pytest.raises(InvalidInputError, match=r"copy/merge\.json, a merged view's record"):
        writable_root(held / "s")


@pytest.mark.parametrize(
    "name",
    [
        "cases",
        "eml",
        ".build",
        "work",
        "grades",
        "labels.jsonl",
        "reviews.jsonl",
        "checks.yaml",
        "fixed_labels.jsonl",
        "manifest.json",
    ],
)
def test_a_set_writer_never_writes_through_a_link(tmp_path: Path, name: str) -> None:
    """A set whose folder or set file is a symbolic link is refused by every writer before it
    writes, naming the link, so nothing lands where it points (OD-705, review round 28)."""
    root = _set(tmp_path / "tests" / "eval" / "synthetic", "t-1")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if name == "cases":
        (root / "cases").rename(elsewhere / "cases")
        (root / "cases").symlink_to(elsewhere / "cases", target_is_directory=True)
    elif "." in name and name != ".build":
        (elsewhere / name).write_text("")
        (root / name).symlink_to(elsewhere / name)
    else:
        (root / name).symlink_to(elsewhere, target_is_directory=True)
    before = sorted(p.name for p in elsewhere.rglob("*"))
    for write in (
        lambda: build_all(root),
        lambda: label_file.write(root, []),
        lambda: review.append_log(root, [{"id": "t-1"}]),
        lambda: fixed_labels.write(root, []),
        lambda: generate.Work(root).append("seeds", [{"n": 1}]),
        lambda: generate.start(root, resume=True),
        lambda: generate.write_manifest(SimpleNamespace(root=root), "stopped"),  # type: ignore[arg-type]
        lambda: _grading(root).run(resume=True),
    ):
        with pytest.raises(InvalidInputError, match=f"{name}: a symbolic link;"):
            write()
    assert sorted(p.name for p in elsewhere.rglob("*")) == before
    if "." in name and name != ".build":
        assert (elsewhere / name).read_text() == ""  # nothing written or appended through it


def test_a_special_file_where_a_set_folder_goes_is_refused(tmp_path: Path) -> None:
    root = _set(tmp_path / "s", "t-1")
    os.mkfifo(root / "eml")
    with pytest.raises(InvalidInputError, match="eml: not the folder a set holds by this name"):
        build_all(root)


def test_an_append_never_follows_a_link(tmp_path: Path) -> None:
    """The review log and a generated set's work files are appended without following a link,
    even one put there after the set was checked (review round 28)."""
    from ecf.eval import generate  # noqa: PLC0415

    outside = tmp_path / "outside.jsonl"
    outside.write_text("")
    link = tmp_path / "x.jsonl"
    link.symlink_to(outside)
    with pytest.raises(InvalidInputError, match="a symbolic link, hard link or special file"):
        setio.append_text(link, "row\\n")
    root = tmp_path / "gen"
    work = generate.Work(root)
    work.append("batches", [{"n": 1}])
    (root / "work" / "batches.jsonl").unlink()
    (root / "work" / "batches.jsonl").symlink_to(outside)
    with pytest.raises(InvalidInputError, match="a symbolic link, hard link or special file"):
        work.append("batches", [{"n": 2}])
    assert outside.read_text() == ""


def test_a_new_card_is_never_written_through_a_linked_cases_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner  # noqa: PLC0415

    from ecf.cli import app  # noqa: PLC0415

    monkeypatch.setenv("ECF_HOME", str(tmp_path / "home"))
    root = tmp_path / "set"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "cases").symlink_to(elsewhere, target_is_directory=True)
    r = CliRunner().invoke(
        app, ["eval", "new-case", "t-9", "--template", "bec", "--root", str(root)]
    )
    assert r.exit_code != 0
    assert "cases: a symbolic link;" in r.output + str(r.exception), r.output
    assert not list(elsewhere.iterdir())


def test_the_writer_check_sees_an_open_on_any_receiver(tmp_path: Path) -> None:
    """`Path.open` with a write mode counts whatever its receiver is: `(a / b)`, a call's result,
    an attribute or `Path(r)` (review round 28: only a bare name was seen)."""
    mod = tmp_path / "w.py"
    mod.write_text(
        "import io\nfrom pathlib import Path\n"
        "def a(r):\n    (r / 'log').open('a')\n"
        "def b(self):\n    self._path('x').open('a')\n"
        "def c(self):\n    self.p.open('w')\n"
        "def d(r):\n    Path(r).open('w')\n"
        "def ok1(r):\n    (r / 'log').open()\n    io.open(r)\n    open(r)\n"
        "def ok2(r):\n    (writable_root(r) / 'log').open('a')\n"
    )
    assert sorted({w[0] for w in _writes(mod) if not w[2]}) == ["a", "b", "c", "d"]


def test_an_eval_result_copy_never_goes_into_a_view(tmp_path: Path) -> None:
    """`ecf eval run --copy-to` refuses a merged view (review round 28); the service checks the
    copy in `_save` (test_evalrun), not every result write (review round 29)."""
    from ecf.cli import _copy_folder  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415

    view = tmp_path / "eval" / "private" / ".merged" / "synthetic" / "results"
    with pytest.raises(InvalidInputError, match="merged eval set"):
        _copy_folder(view)
    assert not view.exists()


@pytest.mark.parametrize("command", ["calibration-export", "settle-drafts"])
def test_an_export_into_a_view_is_refused_before_the_service_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    from typer.testing import CliRunner  # noqa: PLC0415

    from ecf import cli_evalgen  # noqa: PLC0415
    from ecf.cli import app  # noqa: PLC0415

    class NoService:
        def __init__(self, *_a: object) -> None:
            raise AssertionError("the service was asked")

    monkeypatch.setattr(cli_evalgen, "LocalClient", NoService)
    view = tmp_path / "eval" / "private" / ".merged" / "synthetic" / "page"
    r = CliRunner().invoke(app, ["eval", command, "r1", "--export", str(view)])
    assert r.exit_code != 0
    assert "merged eval set" in r.output + str(r.exception), r.output


def test_a_message_never_reads_none(tmp_path: Path) -> None:
    """An OSError with no message is named by its type wherever an eval message gives a reason
    (review round 28: the sweep's notes were fixed alone in round 27)."""
    from ecf.eval import merged  # noqa: PLC0415

    assert "(OSError)" in str(merged.unreadable(tmp_path / "x", OSError()))
    assert "(OSError)" in str(merged.unreadable(None, OSError()))
    assert setio.reason(OSError(errno.EACCES, None)) == os.strerror(errno.EACCES)


def test_the_set_names_match_the_merged_layout() -> None:
    from ecf.eval import merged  # noqa: PLC0415

    assert {*merged.COPIED, merged.LINKED} <= set(setio.SET_FOLDERS)
    assert set(merged.FILES) == set(setio.SET_FILES)


def test_a_card_writer_never_writes_through_a_linked_cases_folder(tmp_path: Path) -> None:
    """A card's labels or expectations, written by its own path, are refused when the set's
    `cases/` is a link: the guard finds the card's set and checks it (review round 28, delta
    check)."""
    from ecf.eval.cards import parse_card  # noqa: PLC0415

    src = SRC.parents[1] / "tests" / "eval" / "synthetic" / "cases" / "bank-change-reply-to.md"
    other = tmp_path / "other"
    (other / "cases").mkdir(parents=True)
    before = src.read_text(encoding="utf-8")
    (other / "cases" / "t-1.md").write_text(before, encoding="utf-8")
    labels = parse_card(before, source="t-1.md").expected.labels
    root = tmp_path / "tests" / "eval" / "synthetic"
    root.mkdir(parents=True)
    (root / "cases").symlink_to(other / "cases", target_is_directory=True)
    card = root / "cases" / "t-1.md"
    for write in (
        lambda: review.set_labels(card, labels),
        lambda: review.write_expected(card, "x", {}),
    ):
        with pytest.raises(InvalidInputError, match="cases: a symbolic link;"):
            write()
    assert (other / "cases" / "t-1.md").read_text() == before


def test_a_set_reached_through_a_link_is_still_checked(tmp_path: Path) -> None:
    """Only a set's own folder may be a link (an alias of the whole set): what is inside it is
    checked as for the set itself, and so is a link further down a helper's path, such as a
    call folder (review round 28, delta check)."""
    real = _set(tmp_path / "real", "t-1")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (real / "eml").symlink_to(elsewhere, target_is_directory=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    for write in (lambda: build_all(alias), lambda: review.append_log(alias, [{"id": "t-1"}])):
        with pytest.raises(InvalidInputError, match="eml: a symbolic link;"):
            write()
    assert not list(elsewhere.iterdir())
    (real / "eml").unlink()
    (real / "work").mkdir()
    (real / "work" / "call").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(InvalidInputError, match="call: a symbolic link;"):
        setio.writable_set(alias, alias / "work" / "call")
    card = alias / "cases" / "t-1.md"
    assert setio.writable_set(alias, card) == card


def test_a_folder_outside_any_set_is_checked_for_the_view_alone(tmp_path: Path) -> None:
    """A page or result folder holding a plain file or a link named like a set's folder is no
    set, and isn't refused for it (review round 28, delta check)."""
    from ecf.cli import _copy_folder  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415

    out = tmp_path / "Desktop"
    out.mkdir()
    (out / "grades").write_text("my notes")
    (out / "work").symlink_to(tmp_path, target_is_directory=True)
    assert writable_root(out) == out
    assert writable_root(out / "page.json") == out / "page.json"
    assert _copy_folder(out) == out.resolve()


def test_a_new_set_is_checked_as_a_set_before_it_holds_anything(tmp_path: Path) -> None:
    """`writable_set` checks a set's own folder whatever it holds yet: a generated set's work
    folder planted as a link is refused before the first card exists (review round 28)."""
    root = tmp_path / "gen"
    root.mkdir()
    (root / "work").symlink_to(tmp_path, target_is_directory=True)
    assert writable_root(root) == root  # holds nothing that makes it a set
    with pytest.raises(InvalidInputError, match="work: a symbolic link;"):
        setio.writable_set(root)


def test_an_append_to_a_pipe_is_refused_by_name(tmp_path: Path) -> None:
    fifo = tmp_path / "reviews.jsonl"
    os.mkfifo(fifo)
    with pytest.raises(InvalidInputError, match="a symbolic link, hard link or special file"):
        setio.append_text(fifo, "row\n")


def test_a_refusal_shows_a_name_escaped(tmp_path: Path) -> None:
    """A path with a line break or a terminal control in its name is shown escaped (review
    round 28, delta check)."""
    for root in (tmp_path / ".merged" / "a\nb", tmp_path / "s" / "x\x1b[31m"):
        if ".merged" not in str(root):
            _set(root.parent, "t-1")
            root.symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(InvalidInputError) as got:
            setio.writable_set(root.parent, root)
        assert "\n" not in str(got.value) and "\x1b" not in str(got.value)


def test_a_page_or_refresh_error_never_reads_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The settle page's read error and a writer's refresh note name an OSError with no message
    by its type (review round 28, delta check)."""
    from datetime import date  # noqa: PLC0415
    from types import SimpleNamespace  # noqa: PLC0415

    from ecf.eval import merged  # noqa: PLC0415
    from ecf.eval import settle as st  # noqa: PLC0415

    def bare(*_a: object, **_k: object) -> str:
        raise OSError

    monkeypatch.setattr(Path, "read_text", bare)
    with pytest.raises(InvalidInputError, match=r"answers \(OSError\)"):
        st.apply_page(tmp_path, tmp_path / "a.json", tmp_path / "t.json", date(2026, 11, 8))

    def with_private(_root: Path) -> SimpleNamespace:
        return SimpleNamespace(private=tmp_path)

    monkeypatch.setattr(merged, "paths", with_private)
    monkeypatch.setattr(merged, "refresh_merged", bare)
    assert merged.refresh_after_write(tmp_path) == (
        "note: the merged set wasn't refreshed: OSError"
    )


def test_a_link_inside_a_set_to_another_set_is_refused(tmp_path: Path) -> None:
    """A set is named by its writer, never guessed from what a folder holds: a call folder
    linked to another set is a link inside this one, refused (review round 28, second delta
    check)."""
    s_root, other = _set(tmp_path / "S", "t-1"), _set(tmp_path / "T", "t-2")
    (s_root / "work").mkdir()
    (s_root / "work" / "call").symlink_to(other, target_is_directory=True)
    with pytest.raises(InvalidInputError, match="call: a symbolic link;"):
        setio.writable_set(s_root, s_root / "work" / "call")
    (other / "cases" / "checks.yaml").write_text("")  # a stray set-file name moves nothing
    assert setio.writable_set(other, other / "cases" / "t-2.md") == other / "cases" / "t-2.md"


def test_a_path_must_lie_inside_its_set(tmp_path: Path) -> None:
    """`..` below a set, which the system resolves after following any link, and a path outside
    the set are refused (review round 28, second delta check)."""
    root = _set(tmp_path / "S", "t-1")
    (tmp_path / "other" / "sub").mkdir(parents=True)
    (root / "foo").symlink_to(tmp_path / "other" / "sub", target_is_directory=True)
    for path in (root / "foo" / ".." / "labels.jsonl", root / "cases" / ".." / "x",
                 tmp_path / "elsewhere"):  # fmt: skip
        with pytest.raises(InvalidInputError, match="not a path inside its set"):
            setio.writable_set(root, path)
    assert not (tmp_path / "other" / "labels.jsonl").exists()


# The writers into a set, each naming its set with `writable_set` (review round 28): a writer
# moved back to the view-only `writable_root` would no longer check the set for links
SET_WRITERS = {
    ("eval/builder.py", "build_all"),
    ("eval/labels.py", "write"),
    ("eval/fixed_labels.py", "write"),
    ("eval/review.py", "append_log"),
    ("eval/review.py", "set_labels"),
    ("eval/review.py", "write_expected"),
    ("eval/generate.py", "Work.append"),
    ("eval/generate.py", "Run._ask"),
    ("eval/generate.py", "Run._accept"),
    ("eval/generate.py", "write_manifest"),
    ("eval/generate.py", "start"),
    ("eval/grade.py", "Grading.run"),
    ("eval/grade.py", "Grading._write"),
    ("eval/setio.py", "sweep_old_temps"),
    ("cli.py", "eval_new_case"),
}


def _called(call: ast.Call) -> str:
    """The name a call calls, however it is spelled: `f(...)` or `module.f(...)`. An import of a
    guard under another name is refused by `test_no_guard_is_imported_under_another_name`, so
    the last name is the function (review round 29, second delta check)."""
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def test_no_guard_is_imported_under_another_name() -> None:
    """Nor `write_result`, whose callers are pinned by name (review round 29, fourth delta
    check); every source file is read, as the pins read them."""
    pinned = GUARDS | {"write_result"}
    renamed = [f"{f.name}:{n.lineno}" for f in _ALL_SRC
               for n in ast.walk(_parsed(f))
               if isinstance(n, ast.ImportFrom)
               for a in n.names if a.name in pinned and a.asname not in (None, a.name)]  # fmt: skip
    assert not renamed, "a guard imported under another name: " + ", ".join(renamed)


def _callers(path: Path, name: str) -> set[str]:
    """The qualified names of the functions in `path` that call `name`."""
    found: set[str] = set()

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            s = scope
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                s = [*scope, child.name]
            if isinstance(child, ast.Call) and _called(child) == name and scope:
                found.add(".".join(scope))
            visit(child, s)

    visit(_parsed(path), [])
    return found


def test_every_set_writer_names_its_set() -> None:
    missing = [f"{rel}:{fn}" for rel, fn in sorted(SET_WRITERS)
               if fn not in _callers(SRC / rel, "writable_set")]  # fmt: skip
    assert not missing, "set writer without writable_set: " + ", ".join(missing)


def test_a_model_call_folder_in_a_set_is_checked_as_part_of_it() -> None:
    """Generate and grade run each model call in the set's `work/call`, so the folder they pass
    is a `writable_set` result: a link there out of the set is refused before the call (review
    round 28, second delta check). A temporary folder (judge-drafts, the canary) is no set."""
    bad: list[str] = []
    for rel in ("eval/generate.py", "eval/grade.py"):
        tree = _parsed(SRC / rel)
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            checked = {
                t.id
                for n in ast.walk(fn)
                if isinstance(n, ast.Assign)
                and isinstance(n.value, ast.Call)
                and _called(n.value) == "writable_set"
                for t in n.targets
                if isinstance(t, ast.Name)
            }
            for n in ast.walk(fn):
                if not (isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "ask"):
                    continue
                arg = n.args[2] if len(n.args) > 2 else None
                ok = (isinstance(arg, ast.Name) and arg.id in checked) or (
                    isinstance(arg, ast.Call) and _called(arg) == "writable_set"
                )
                if not ok:
                    bad.append(f"{rel}:{n.lineno}")
    assert not bad, "a model call folder not checked as part of its set: " + ", ".join(bad)


def test_a_link_then_dot_dot_into_the_view_is_refused(tmp_path: Path) -> None:
    """`X/link/..` is the link's target's parent, as the system goes there, not `X`: a path
    through a link into the view and back up is refused (review round 28, third delta check).
    A `..` in the part that doesn't exist yet leads on through a link after it too."""
    from ecf.eval.setio import canonical  # noqa: PLC0415

    view = tmp_path / "eval" / "private" / ".merged" / "synthetic"
    (view / "cases").mkdir(parents=True)
    x = tmp_path / "X"
    x.mkdir()
    (x / "link").symlink_to(view / "cases", target_is_directory=True)
    with pytest.raises(InvalidInputError, match="merged eval set"):
        writable_root(x / "link" / ".." / "out.json")
    assert canonical(x / "link" / "..") == canonical(view)
    (x / "into").symlink_to(view, target_is_directory=True)
    assert canonical(x / "new" / ".." / "into" / "y") == canonical(view) / "y"
    with pytest.raises(InvalidInputError, match="merged eval set"):
        writable_root(x / "new" / ".." / "into" / "y")
    assert writable_root(x / "new" / ".." / "page.json") == x / "new" / ".." / "page.json"


def test_a_model_call_never_writes_its_schema_through_a_link(tmp_path: Path) -> None:
    """A call's schema is written in a fresh folder of its own, so a link at `schema.json` in
    the call folder is never written through, nor touched (review round 28, third delta check;
    round 29, sixteenth)."""
    from ecf.eval import tools  # noqa: PLC0415
    from tests.test_eval_generate import fake_tool  # noqa: PLC0415

    tool, environ = fake_tool(tmp_path, {})
    adapter = tools.CliAdapter(tool, "fake-model-1", environ)
    call = tmp_path / "set" / "work" / "call"
    call.mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("mine")
    (call / "schema.json").symlink_to(victim)
    adapter.ask("p", {"type": "object"}, call)
    assert victim.read_text() == "mine"
    assert (call / "schema.json").is_symlink() and len(list(call.iterdir())) == 1


def test_the_sweep_reports_and_removes_a_crashed_model_calls_folder(tmp_path: Path) -> None:
    """A crashed model call's folder in `work/call` (it may hold a model's answer) is named by
    `old_temps_left` and removed by the build's sweep once over an hour old; a call still
    holding its folder, or a younger one, or a link, is left alone (review round 29,
    seventeenth delta check)."""
    root = _set(tmp_path / "s", "t-1")
    call = root / "work" / "call"
    call.mkdir(parents=True)
    old = time.time() - 2 * STALE_TEMP_S
    crashed = call / ".call-crashed"
    crashed.mkdir()
    (crashed / "out.json").write_text("raw")
    young = call / ".call-young"
    young.mkdir()
    held, fd = setio.call_folder(call)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (call / ".call-link").symlink_to(elsewhere)
    try:
        for d in (crashed, held):
            os.utime(d, (old, old))
        os.utime(call / ".call-link", (old, old), follow_symlinks=False)
        left = old_temps_left(root)
        assert len(left) == 1 and ".call-crashed" in left[0] and "crashed model call" in left[0]
        gone, left = sweep_old_temps(root)
        assert gone == [crashed] and not left
        assert held.is_dir() and young.is_dir() and (call / ".call-link").is_symlink()
    finally:
        setio.end_call(held, fd)
    assert not held.exists()


def test_a_call_folder_on_a_disk_without_locks_is_named_not_taken_for_a_live_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a lock held by a call counts as a live call: a disk that can't lock leaves a crashed
    call's folder in place with a line saying so, never silently (review round 29, eighteenth
    delta check)."""
    root = _set(tmp_path / "s", "t-1")
    crashed = root / "work" / "call" / ".call-crashed"
    crashed.mkdir(parents=True)
    old = time.time() - 2 * STALE_TEMP_S
    os.utime(crashed, (old, old))

    def no_locks(_fd: int, _op: int) -> None:
        raise OSError(errno.ENOLCK, os.strerror(errno.ENOLCK))

    monkeypatch.setattr(setio.fcntl, "flock", no_locks)
    for left in (old_temps_left(root), sweep_old_temps(root)[1]):
        assert len(left) == 1 and "couldn't check whether a call still uses it" in left[0]
    assert crashed.is_dir()


def test_the_sweep_removes_a_model_calls_old_temps(tmp_path: Path) -> None:
    """A model call's schema file is written in `work/call`, so a crashed one's temporary file
    there is swept too (review round 28, fourth delta check)."""
    root = _set(tmp_path / "s", "t-1")
    (root / "work" / "call").mkdir(parents=True)
    t = _old_temp(root / "work" / "call", ".schema.json.0123456789ab.tmp")
    w = _old_temp(root / "work", ".seeds.jsonl.0123456789ab.tmp")
    (root / "grades").mkdir()  # a grades file is written there too (review round 29)
    g = _old_temp(root / "grades", ".m-1.json.0123456789ab.tmp")
    gone, left = sweep_old_temps(root)
    assert sorted(gone) == sorted([t, w, g]) and not left
    # a linked work folder is never walked, even by the listing (one name opened at a time);
    # it gets one line, and nothing below it is looked at (review round 29, delta check)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "call").mkdir(parents=True)
    _old_temp(elsewhere / "call", ".schema.json.0123456789ab.tmp")
    (root / "work" / "call").rmdir()
    (root / "work").rmdir()
    (root / "work").symlink_to(elsewhere, target_is_directory=True)
    assert old_temps_left(root) == [
        f"{root / 'work'}: old temporary files not checked (a symbolic link)"
    ]


def test_a_set_path_with_dot_dot_after_a_link_is_refused(tmp_path: Path) -> None:
    """A set's location is read as given, so a `..` after a link, which the system resolves
    where the link points, is refused; after a plain folder it is folded (review round 28,
    fourth delta check)."""
    from ecf.eval import merged  # noqa: PLC0415

    top = tmp_path / "top"
    (top / "eval" / "private" / "synthetic").mkdir(parents=True)
    (top / "tests" / "eval" / "synthetic").mkdir(parents=True)
    (tmp_path / "elsewhere" / "a" / "b").mkdir(parents=True)
    (top / "eval" / "private" / "L").symlink_to(tmp_path / "elsewhere" / "a" / "b")
    via_link = top / "eval" / "private" / "L" / ".." / "synthetic"
    for ask in (merged.is_private_root, merged.public_of):
        with pytest.raises(InvalidInputError, match=r"a `\.\.` after the symbolic link"):
            ask(via_link)
    plain = top / "eval" / "private" / "synthetic" / ".." / "synthetic"
    assert merged.is_private_root(plain)
    assert merged.public_of(plain) == merged.public_of(top / "eval" / "private" / "synthetic")


def test_the_public_half_of_a_linked_private_set_is_refused(tmp_path: Path) -> None:
    """A private set folder that is a link is no checkout set, for `public_of` as for every
    other entry point (review round 28, fifth delta check)."""
    from ecf.eval import merged  # noqa: PLC0415

    top = tmp_path / "top"
    (top / "tests" / "eval" / "synthetic").mkdir(parents=True)
    (top / "eval" / "private").mkdir(parents=True)
    (tmp_path / "x" / "a" / "b").mkdir(parents=True)
    (top / "eval" / "private" / "synthetic").symlink_to(tmp_path / "x" / "a" / "b")
    with pytest.raises(InvalidInputError):
        merged.public_of(top / "eval" / "private" / "synthetic")


def test_a_grades_file_is_never_written_through_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run's grades file is written by `atomic_write`, so a link at its name is replaced, never
    followed (review round 28, fifth delta check)."""
    from collections import Counter  # noqa: PLC0415
    from datetime import UTC, datetime  # noqa: PLC0415

    from ecf.eval import grade as grading  # noqa: PLC0415

    class Fake:
        provider, model = "fake", "fake-model-1"

        def describe(self) -> dict[str, str]:
            return {"model": self.model}

    monkeypatch.setattr(grading.secrets, "token_hex", lambda _n=8: "abcd1234abcd1234")
    root = _set(tmp_path / "s", "t-1")
    assert not build_all(root).findings  # labels.jsonl, which the set's version reads
    (root / "grades").mkdir()
    victim = tmp_path / "victim.json"
    victim.write_text("mine")
    name = f"{grading._slug('fake-model-1')}-abcd1234abcd1234.json"  # pyright: ignore[reportPrivateUsage]
    (root / "grades" / name).symlink_to(victim)
    g = grading.Grading(root, Fake(), now=lambda: datetime(2026, 11, 8, tzinfo=UTC))  # type: ignore[arg-type]
    g._write({"cases": {}}, [], {}, Counter())  # pyright: ignore[reportPrivateUsage]
    assert victim.read_text() == "mine"
    assert not (root / "grades" / name).is_symlink()


def test_a_model_call_or_grades_folder_linked_out_of_the_set_is_refused_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generate's and grade's model calls run in the set's `work/call`, and grade writes in its
    `grades/`: a link there is refused before any call or write, as the set's own folder names
    it (review round 29: the static checks alone passed a writer naming a subfolder as its set)."""
    root = _set(tmp_path / "s", "t-1")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "work").mkdir()
    (root / "work" / "call").symlink_to(elsewhere, target_is_directory=True)
    asked = _Asked()
    with pytest.raises(InvalidInputError, match="call: a symbolic link;"):
        generate.Run._ask(SimpleNamespace(root=root, adapter=asked),  # type: ignore[arg-type]
                          "seed", "g", "p", {}, [])  # fmt: skip

    def fixed(value: object) -> Any:
        def answer(*_a: object, **_k: object) -> object:
            return value

        return answer

    monkeypatch.setattr(grade.gen, "verify", fixed({}))
    monkeypatch.setattr(grade.models, "check_judge", fixed(None))
    monkeypatch.setattr(grade.label_file, "read", fixed([{"id": "t-1"}]))
    monkeypatch.setattr(grade, "case_view", fixed("view"))
    with pytest.raises(InvalidInputError, match="call: a symbolic link;"):
        grade.Grading(root, asked).run(resume=False)  # type: ignore[arg-type]
    (root / "work" / "call").unlink()
    (root / "grades").symlink_to(elsewhere, target_is_directory=True)
    monkeypatch.setattr(grade, "set_version", fixed("v"))
    with pytest.raises(InvalidInputError, match="grades: a symbolic link;"):
        _grading(root)._write({"mode": "dev", "cases": {}}, [], {}, Counter())  # pyright: ignore[reportPrivateUsage]
    assert asked.calls == []
    assert not list(elsewhere.iterdir())


# What a set writer may pass as its set: the set's own folder, never a folder inside it, which
# `writable_set` would take as an alias of the whole set (review round 29). A name only counts
# while it still holds the set: these are the only rebindings of one inside a set writer (the
# delta check of round 29: `root = self.root / "work"` passed a check of names alone)
SET_ROOTS = {"root", "self.root", "run.root", "path.parents[1]", "home"}
REBINDS = {
    "home = root",
    "home = priv",  # new-case: the private half, for an injection card
    "priv = merged.paths(root).private",
    "root = writable_set(self.root)",  # Run._accept: the checked set itself
    "root = root.expanduser().resolve()",  # generate: the typed set, made absolute
}


# Every source file: the scans below that pin who calls a guard or writer read all of them, so a
# new caller anywhere is seen (review round 29, third delta check). Known gaps, not checked: a
# local alias of a guard (`ws = writable_set`), a path built from a guard's result with `..`, and
# a call at module level (no function to name); the behaviour tests that link a set's folders
# are the protection there.
_ALL_SRC = sorted([*SRC.rglob("*.py"), *(SRC.parent / "ecf_server").rglob("*.py")])
# Callers of writable_set that only check a set before any work, writing nothing themselves
SET_PREFLIGHT = {
    ("cli_evalgen.py", "make_commands.generate"),
    ("cli_evalgen.py", "make_commands.grade"),
}


def test_every_caller_of_writable_set_is_a_listed_set_writer() -> None:
    callers = {(str(f.relative_to(SRC)) if f.is_relative_to(SRC) else f.name, q)
               for f in _ALL_SRC for q in _callers(f, "writable_set")}  # fmt: skip
    unlisted = sorted(callers - SET_WRITERS - SET_PREFLIGHT)
    assert not unlisted, "writable_set called outside SET_WRITERS: " + ", ".join(
        f"{rel}:{q}" for rel, q in unlisted
    )


def test_every_set_writer_names_the_set_itself() -> None:
    bad: list[str] = []
    for rel, qual in sorted(SET_WRITERS | SET_PREFLIGHT):  # a pre-flight check names it too
        tree = _parsed(SRC / rel)
        fn = _function(tree, qual)
        held: set[str] = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and _called(n) == "writable_set":
                arg = ast.unparse(n.args[0]) if n.args else ""
                if arg not in SET_ROOTS:
                    bad.append(f"{rel}:{n.lineno} ({qual})")
                held.add(arg.removesuffix(".parents[1]"))
        if "home" in held:
            held |= {"root", "priv"}
        for n in ast.walk(fn):
            for t in _bound(n):
                if ast.unparse(t) in held and ast.unparse(n) not in REBINDS:
                    bad.append(f"{rel}:{t.lineno} ({qual}: {ast.unparse(n)[:60]})")
    assert not bad, "a set writer names a folder other than its set: " + ", ".join(bad)


def _bound(n: ast.AST) -> list[ast.expr]:
    """The names a statement binds: assignments, loop and `with` targets, `:=`."""
    if isinstance(n, ast.Assign):
        found = list(n.targets)
    elif isinstance(n, ast.AnnAssign | ast.AugAssign | ast.NamedExpr | ast.For | ast.AsyncFor):
        found = [n.target]
    elif isinstance(n, ast.withitem) and n.optional_vars is not None:
        found = [n.optional_vars]
    else:
        return []
    flat: list[ast.expr] = []
    while found:  # `a, (b, *c) = ...` binds a, b and c (review round 29, second delta check)
        t = found.pop()
        if isinstance(t, ast.Tuple | ast.List):
            found.extend(t.elts)
        elif isinstance(t, ast.Starred):
            found.append(t.value)
        else:
            flat.append(t)
    return flat


def _function(tree: ast.Module, qual: str) -> ast.FunctionDef:
    body: list[ast.stmt] = tree.body
    found: ast.AST = tree
    for part in qual.split("."):
        node = next(n for n in body if isinstance(n, ast.ClassDef | ast.FunctionDef)
                    and n.name == part)  # fmt: skip
        body = node.body
        found = node
    assert isinstance(found, ast.FunctionDef)
    return found


def test_a_dot_dot_after_a_link_in_the_set_folder_is_refused(tmp_path: Path) -> None:
    """`S/link/..` is the link target's parent, not `S`: refused as the set location refuses it,
    so a set's own layout is never skipped that way (review round 29)."""
    s = tmp_path / "S"
    s.mkdir()
    other = tmp_path / "other" / "inner"
    other.mkdir(parents=True)
    (s / "lnk").symlink_to(other, target_is_directory=True)
    with pytest.raises(InvalidInputError, match=r"a `\.\.` after the symbolic link"):
        setio.writable_set(s / "lnk" / "..")
    assert setio.writable_set(s / "plain" / "..") == s / "plain" / ".."  # no link: folded


def test_the_sweep_names_a_set_reached_through_its_alias(tmp_path: Path) -> None:
    """The sweep never follows a link, even an alias of the set (review round 25, S1), but it
    says so: an alias is never skipped without a line (review round 29)."""
    root = _set(tmp_path / "s", "t-1")
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    old = _old_temp(root / "cases")
    (line,) = setio.old_temps_left(alias)
    assert "the set folder is a symbolic link" in line
    assert setio.sweep_old_temps(alias) == ([], [line])
    assert old.exists()


def test_a_linked_email_with_the_same_bytes_is_replaced_by_the_build(tmp_path: Path) -> None:
    """A built email that is a link, even to the same bytes, is replaced by a plain file, so
    the build never reports a set its readers then refuse (review round 29)."""
    root = _set(tmp_path / "s", "t-1")
    assert not build_all(root).findings
    eml = root / "eml" / "t-1.eml"
    same = tmp_path / "same.eml"
    same.write_bytes(eml.read_bytes())
    eml.unlink()
    eml.symlink_to(same)
    assert not build_all(root).findings
    assert not eml.is_symlink() and eml.read_bytes() == same.read_bytes()


def test_a_fixed_label_file_error_never_reads_empty_parentheses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ecf.cli import _import_fixed  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415

    root = _set(tmp_path / "s", "t-1")
    page = tmp_path / "page.json"
    page.write_text("{}")

    def bare(*_a: object, **_k: object) -> str:
        raise OSError

    from ecf.eval import merged  # noqa: PLC0415

    monkeypatch.setattr(Path, "read_text", bare)
    for halves in ([root], [root, tmp_path / "private"]):  # one set; a public and private pair

        def given(_r: Path, h: list[Path] = halves) -> list[Path]:
            return h

        monkeypatch.setattr(merged, "halves", given)
        with pytest.raises(InvalidInputError) as got:
            _import_fixed(root, page)
        assert "()" not in str(got.value) and "OSError" in str(got.value)


def test_a_rescore_is_never_written_into_a_view(tmp_path: Path) -> None:
    """A rescore goes beside the result file the user names, so it checks that folder; the
    install's own record is the only result written unchecked (review round 29, delta check)."""
    from ecf_server import evalrun  # noqa: PLC0415

    view = tmp_path / "eval" / "private" / ".merged" / "synthetic" / "results"
    with pytest.raises(InvalidInputError, match="merged eval set"):
        evalrun._write_beside(view / "x.json", None, "y.json", None)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert not view.exists()
    callers = {(f.name, q) for f in _ALL_SRC for q in _callers(f, "write_result")}
    assert callers == {("evalrun.py", "_save"), ("evalrun.py", "_write_beside")}, callers


def test_a_new_card_from_generate_is_never_written_through_a_linked_cases_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Run._accept` names the set before its card is written (review round 29, delta check)."""
    root = tmp_path / "gen"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "cases").symlink_to(elsewhere, target_is_directory=True)

    def fixed(value: object) -> Any:
        def answer(*_a: object, **_k: object) -> object:
            return value

        return answer

    monkeypatch.setattr(generate, "card_text", fixed("text"))
    monkeypatch.setattr(generate, "parse_card", fixed(SimpleNamespace(body="b")))
    monkeypatch.setattr(generate, "problems", fixed((None, list[str]())))
    run = SimpleNamespace(root=root, mode="prod", case_no=Counter(), show=None,
                          adapter=SimpleNamespace(model="m"),
                          dups=SimpleNamespace(seen=fixed(None)),
                          work=SimpleNamespace(append=fixed(None)))  # fmt: skip
    bucket = SimpleNamespace(group="bec", delivery=None)
    with pytest.raises(InvalidInputError, match="cases: a symbolic link;"):
        generate.Run._accept(run, bucket, {"id": "s"}, {"labels": {}}, False)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert not list(elsewhere.iterdir())


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any folder")
def test_the_sweep_names_a_folder_it_cannot_open(tmp_path: Path) -> None:
    """A set folder the sweep can't open (unreadable, a link, not a folder) gets a line; a
    missing one is silent (review round 29, delta check)."""
    root = _set(tmp_path / "s", "t-1")
    (root / "work").mkdir()
    (root / "work" / "call").symlink_to(tmp_path, target_is_directory=True)
    (root / ".build").write_text("")
    os.chmod(root / "cases", 0)
    try:
        lines = setio.old_temps_left(root)
    finally:
        os.chmod(root / "cases", 0o700)
    text = "\n".join(lines)
    assert f"{root / 'cases'}: old temporary files not checked" in text
    assert f"{root / 'work' / 'call'}: old temporary files not checked (a symbolic link)" in text
    assert f"{root / '.build'}: old temporary files not checked (not a folder)" in text
    assert "eml" not in text  # missing: nothing to say
    assert len(lines) == 3


def test_no_eval_message_prints_an_os_error_raw() -> None:
    """An error caught as OSError (or any exception) becomes text only through `reason`, so no
    message reads "()" or ends with nothing (review round 29 and its delta checks). In such a
    handler the caught name may appear only inside `reason(...)`, `type(...)` or
    `isinstance(...)`, re-raised or as a `raise ... from` cause, as `.errno`, `.filename` or
    `.filename2`, or handed whole to one of `EXC_HELPERS`; any other use (an f-string, `str()`,
    `print`, `echo`, a log field, `.args`, `.strerror`, a class built from it) fails. Not seen:
    an OSError alias reached through a module (`except socket.timeout`); none is in these files.
    `.filename` may be None (a descriptor call): a message using it gives a fallback
    (`exc.filename or top`); that is read, not checked. Also stated, not checked: a module that
    reaches `ecf.eval` only through another module or as `from ecf import eval` isn't scanned,
    and a local alias or lambda given a helper's name would be trusted as that helper."""
    raw: list[str] = []
    for f in _eval_modules():
        tree = _parsed(f)
        for h in ast.walk(tree):
            if not (isinstance(h, ast.ExceptHandler) and h.name and h.type is not None):
                continue
            if _catches_os_error(h.type):
                raw += [f"{f.name}:{line}" for line in sorted(set(_as_text(h, h.name)))]
    assert not raw, "an exception made text without reason(): " + ", ".join(raw)


def _catches_os_error(t: ast.expr) -> bool:
    """Whether an `except` clause's type can catch an OSError: OSError or any subclass of it
    (FileExistsError, TimeoutError, ...), or Exception/BaseException."""
    import builtins  # noqa: PLC0415

    for n in ast.walk(t):
        last = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else ""
        kind = getattr(builtins, last, None)
        if isinstance(kind, type) and (issubclass(kind, OSError) or issubclass(OSError, kind)):
            return True
    return False


def _as_text(handler: ast.ExceptHandler, name: str) -> list[int]:
    """The lines where `handler` uses its caught `name` other than in an allowed way (review
    round 29, fifth delta check: an allow-list, since every list of ways to make text leaked)."""
    out: list[int] = []

    def visit(n: ast.AST) -> None:
        if isinstance(n, ast.Call):
            last = _called(n)
            if last in {"reason", "type", "isinstance"}:
                return  # its text through reason; its class; a test of its class
            if isinstance(n.func, ast.Name) and last in EXC_HELPERS:  # the helper itself
                for c in [*n.args, *(k.value for k in n.keywords)]:
                    if not (isinstance(c, ast.Name) and c.id == name):
                        visit(c)
                visit(n.func)
                return
        if isinstance(n, ast.Raise):
            for c in (n.exc, n.cause):  # re-raised, or the cause of another error
                if c is not None and not (isinstance(c, ast.Name) and c.id == name):
                    visit(c)
            return
        if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == name
                and n.attr in {"errno", "filename", "filename2"}):  # fmt: skip
            return  # the code or the path, not the error's text
        if isinstance(n, ast.Name) and n.id == name:
            out.append(n.lineno)
        for c in ast.iter_child_nodes(n):
            visit(c)

    for stmt in handler.body:
        visit(stmt)
    return out


# Helpers handed a caught error whole: each puts it in a message through `reason` or re-raises it.
# Called by bare name only, and each defined once, so a method or a second def of the same name
# is checked like any other call (review round 29, sixth delta check)
EXC_HELPERS = {
    "unreadable",
    "denied",
    "_reraise_for",
    "_stop_or_fail",
    "_unreadable_state",
    "_unreadable_key",
    "_recovery_why",  # a failed recovery's reason: via _cause_of, and that it is over (r30)
    "_cause_of",  # an error's cause for a record: ecf's own cause, reason(), else its kind (r30)
    "_told",  # an ecf error's cause and advice, for a request's status only, never a record (r30)
}


@functools.cache
def _parsed(path: Path) -> ast.Module:
    """A source file's tree, parsed once per test run: the source scans share it and never
    change it (review round 30: seven scans each parsed all 249 files again)."""
    return ast.parse(path.read_text(encoding="utf-8"))


@functools.cache
def _eval_modules() -> tuple[Path, ...]:
    """Every eval module, chosen by rule so a new one is never missed: all of `ecf/eval`, and
    every other module that imports from it (review round 29, sixth delta check)."""

    def imports_eval(f: Path) -> bool:
        return any(
            (isinstance(n, ast.ImportFrom) and (n.module or "").startswith("ecf.eval"))
            or (isinstance(n, ast.Import) and any(a.name.startswith("ecf.eval") for a in n.names))
            for n in ast.walk(_parsed(f))
        )

    return tuple(f for f in _ALL_SRC if f.is_relative_to(SRC / "eval") or imports_eval(f))


def test_each_exception_helper_is_defined_once() -> None:
    defs = [
        n.name
        for f in _ALL_SRC
        for n in ast.walk(_parsed(f))
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name in EXC_HELPERS
    ]
    assert sorted(defs) == sorted(EXC_HELPERS), defs


def test_the_sweep_names_a_folder_it_opened_but_could_not_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _set(tmp_path / "s", "t-1")
    real = os.listdir

    def failing(path: Any = ".") -> list[str]:
        if isinstance(path, int):
            raise OSError(errno.EIO, "Input/output error")
        return list(real(path))  # pyright: ignore[reportUnknownArgumentType]

    monkeypatch.setattr(os, "listdir", failing)
    lines = setio.old_temps_left(root)
    assert lines and all("old temporary files not checked (Input/output error)" in x
                         for x in lines)  # fmt: skip


def test_a_result_file_is_never_written_through_a_link(tmp_path: Path) -> None:
    """A symbolic link, hard link or pipe at a result file's own name (a rescore's predictable
    name, say) is replaced by the new file, never written through or waited on (review round
    29, second and third delta checks)."""
    from ecf.eval.results import ResultFile  # noqa: PLC0415
    from ecf_server.evalrun import write_result  # noqa: PLC0415

    folder = tmp_path / "results"
    folder.mkdir()
    target = tmp_path / "target.json"
    target.write_text("keep")
    (folder / "sym.json").symlink_to(target)
    os.link(target, folder / "hard.json")
    os.mkfifo(folder / "pipe.json")
    result = ResultFile(run_id="a" * 32, pair="p", set_version="v", created_at="t", cases=[])
    for name in ("sym.json", "hard.json", "pipe.json"):
        path = write_result(folder, result, name)
        st = os.lstat(path)
        assert stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) & 0o077 == 0
        assert json.loads(path.read_text())["run_id"] == "a" * 32
    assert target.read_text() == "keep"
    assert sorted(p.name for p in folder.iterdir()) == ["hard.json", "pipe.json", "sym.json"]


def test_the_gate_names_a_set_it_cannot_read(tmp_path: Path) -> None:
    """`reason` gives the system message only, so the gate's detail names the set and the file
    the error names (review round 29, seventh delta check)."""
    from ecf_server import gate  # noqa: PLC0415

    root = tmp_path / "s"
    root.write_text("")  # a file where the set's folder goes
    unreadable = gate._unreadable_state  # pyright: ignore[reportPrivateUsage]
    got = unreadable(root / "cases", NotADirectoryError(20, "Not a directory", str(root / "cases")))
    assert got.error == f"the gate's set {root / 'cases'} can't be read: Not a directory"
    other = unreadable(root, OSError(5, "Input/output error", str(root / "x")))
    assert other.error == (
        f"the gate's set {root} can't be read ({root / 'x'}): Input/output error"
    )
    real = gate.set_state(root, fresh=True)
    assert real.error is not None and str(root) in real.error, real.error


def test_the_gate_message_names_each_path_once_and_as_text(tmp_path: Path) -> None:
    """ecf's own refusal already names the set: not repeated; a bytes path is decoded, a file
    number shown as nothing; the unreadable key holds the file (review round 29, eighth delta
    check)."""
    from ecf_server import gate  # noqa: PLC0415

    root = tmp_path / "s"
    unreadable = gate._unreadable_state  # pyright: ignore[reportPrivateUsage]
    own = unreadable(root, InvalidInputError(f"{root}: a symbolic link"))
    assert own.error == f"the gate's set can't be read: {root}: a symbolic link"
    raw = unreadable(root, OSError(5, "Input/output error", os.fsencode(str(root / "x"))))
    assert raw.error == f"the gate's set {root} can't be read ({root / 'x'}): Input/output error"
    fd = unreadable(root, OSError(9, "Bad file descriptor", 3))
    assert fd.error == f"the gate's set {root} can't be read: Bad file descriptor"
    key = gate._unreadable_key  # pyright: ignore[reportPrivateUsage]
    one = key(root, OSError(13, "Permission denied", str(root / "a")))
    two = key(root, OSError(13, "Permission denied", str(root / "b")))
    assert one != two


def test_a_rescore_of_the_installs_own_result_ignores_a_stray_merge_file(tmp_path: Path) -> None:
    """The install's own results folder is trusted for a rescore as for the run's record; any
    other folder is still checked (review round 29, eighth delta check)."""
    from ecf_server.evalrun import rescore_folder  # noqa: PLC0415

    (tmp_path / "merge.json").write_text("")  # another tool's stray file, not JSON
    data = tmp_path / "home" / "data"
    own = data / "evals" / "x.json"
    assert rescore_folder(own, data) == own.parent
    with pytest.raises(InvalidInputError, match="merged eval set"):
        rescore_folder(tmp_path / "elsewhere" / "x.json", data)
    with pytest.raises(InvalidInputError, match="merged eval set"):
        rescore_folder(own, None)


def test_a_drafts_record_is_never_written_through_a_link(tmp_path: Path) -> None:
    """The drafts record beside a result is written as the result is (review round 29, tenth
    delta check)."""
    from ecf.eval.drafts import DraftsFile  # noqa: PLC0415
    from ecf_server import drafteval  # noqa: PLC0415

    folder = tmp_path / "evals"
    folder.mkdir()
    target = tmp_path / "target.json"
    target.write_text("keep")
    record = DraftsFile(run_id="r1", set_version="v", created_at="t", forced=False,
                        complete=True, state="done", models={}, actor_prompt_digest="a",
                        checker_digest="c", cases=[])  # fmt: skip
    name = folder / "r1.drafts.json"
    for plant in (lambda: name.symlink_to(target), lambda: os.link(target, name),
                  lambda: os.mkfifo(name)):  # fmt: skip
        plant()
        path = drafteval.write(folder, record)
        st = os.lstat(path)
        assert stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) & 0o077 == 0
        assert target.read_text() == "keep"
        assert DraftsFile.model_validate_json(path.read_text()) == record  # what was written
        path.unlink()


def _no_questions(*_a: object) -> list[dict[str, Any]]:
    return []


@pytest.mark.parametrize(
    "writer",
    ["export page", "codex catalogue", "draft-run registry", "settle page", "judges' answers",
     "corpus labels"],
)  # fmt: skip
def test_no_eval_writer_writes_through_a_link_hard_link_or_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: str
) -> None:
    """Every eval file is a new file renamed over its name (review round 29, eleventh delta
    check): a symbolic link, hard link or pipe at the name is replaced, its target untouched."""
    from ecf import cli_evalgen  # noqa: PLC0415
    from ecf.eval import codex_tool, tools  # noqa: PLC0415
    from ecf_server import draftruns  # noqa: PLC0415

    target = tmp_path / "target"
    target.write_text("keep")
    folder = tmp_path / "out"
    folder.mkdir(mode=0o700)

    def ran(*_a: object, **_k: object) -> tools.Ran:
        doc = json.dumps({"models": [{"slug": "m", "tool_mode": "x"}]}).encode()
        return tools.Ran(0, doc, b"", 0.0)

    def in_folder(*_a: object) -> Path:
        return folder / ("cat.json" if writer == "codex catalogue" else "")

    def write() -> Path:
        if writer == "export page":
            cli_evalgen._write_private(folder / "page.json", "{}")  # pyright: ignore[reportPrivateUsage]
            return folder / "page.json"
        if writer == "codex catalogue":
            monkeypatch.setattr(tools, "run_cli", ran)
            monkeypatch.setattr(codex_tool, "catalog_path", in_folder)
            codex_tool.write_catalog(None, "m", "codex", {}, tmp_path)  # type: ignore[arg-type]
            return folder / "cat.json"
        if writer == "settle page":
            from ecf.eval import settle  # noqa: PLC0415

            monkeypatch.setattr(settle, "questions", _no_questions)
            settle.export(tmp_path / "set", folder)
            return folder / "settle-page.json"
        if writer == "judges' answers":
            from ecf_server import draftjudging  # noqa: PLC0415

            draftjudging.write_json(folder / "j.json", {})
            return folder / "j.json"
        if writer == "corpus labels":
            from ecf.eval import corpus_labels  # noqa: PLC0415

            corpus_labels.save(folder / "labels.jsonl", {})
            return folder / "labels.jsonl"
        monkeypatch.setattr(draftruns, "folder", in_folder)
        draftruns.save(tmp_path, draftruns.Registry())
        return folder / draftruns.REGISTRY

    name = write()
    for plant in (lambda: name.symlink_to(target), lambda: os.link(target, name),
                  lambda: os.mkfifo(name)):  # fmt: skip
        name.unlink()
        plant()
        assert write() == name
        st = os.lstat(name)
        assert stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) & 0o077 == 0
        assert target.read_text() == "keep" and name.read_text() != "keep"
    assert not [p for p in folder.iterdir() if p.name.endswith(".tmp")]


def test_the_draft_run_registry_is_flushed_before_it_is_renamed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registry must survive a power cut: its new file is flushed to disk before the rename,
    as it was before it moved onto the shared write (review round 29, eleventh delta check)."""
    from ecf_server import draftruns  # noqa: PLC0415

    steps: list[str] = []
    real_sync, real_replace = setio.flush_to_disk, os.replace

    def fsync(fd: int) -> None:
        steps.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_sync(fd)

    def replace(src: Any, dst: Any) -> None:
        steps.append("rename")
        real_replace(src, dst)

    monkeypatch.setattr(setio, "flush_to_disk", fsync)
    monkeypatch.setattr(os, "replace", replace)
    draftruns.save(tmp_path, draftruns.Registry())
    assert steps == ["file", "rename", "dir"], steps  # flushed, renamed, then its folder


def test_the_registry_save_removes_old_leftovers(tmp_path: Path) -> None:
    """Saved every 15 s, the registry removes crashed writes' temporary files over an hour old
    from its folder, and nothing else (review round 29, twelfth delta check)."""
    from ecf_server import draftruns  # noqa: PLC0415

    evals = draftruns.folder(tmp_path)
    evals.mkdir(parents=True, mode=0o700)
    old = _old_temp(evals, ".draft-runs.json.0123456789ab.tmp")
    young = evals / ".r.json.0123456789ab.tmp"
    young.write_text("now")
    other = _old_temp(evals, "notes.tmp")
    draftruns.save(tmp_path, draftruns.Registry())
    assert not old.exists() and young.exists() and other.exists()


# Every call in an eval module that can write a file, as an allow-list: a new write call fails
# until it is listed here, so it is looked at (review round 29, thirteenth delta check: a list
# of bad patterns kept missing one). Eval files are written through `setio`: `write_new` and
# `copy_new` (a new file, never through a link), `replace_file` (renamed over the name),
# `open_append` (added to in place, a regular file with one name). Anything else is listed
# with why.
WRITE_CALLS = {
    ("setio.py", "write_new", "open"),  # O_EXCL | O_NOFOLLOW: a new file of its own
    ("setio.py", "write_new", "fdopen"),
    ("setio.py", "copy_new", "open"),
    ("setio.py", "copy_new", "fdopen"),
    ("setio.py", "replace_file", "replace"),  # the rename over the name
    ("setio.py", "create_file", "link"),  # a new file's name: never replaces anything
    ("setio.py", "create_file", "rename"),  # the same on a disk that can't hard-link
    ("setio.py", "open_append", "open"),  # NOFOLLOW | NONBLOCK, a regular file, one name
    ("draftruns.py", "_cut_partial", "ftruncate"),  # on `open_append`'s descriptor
    ("tools.py", "canary", "write_text"),  # the canary's decoy, in its own temporary folder
    ("merged.py", "_clone_or_link", "link"),  # a view file, linked to a new name of its own
    ("merged.py", "_open_lock", "open"),  # the view's lock: NOFOLLOW, NONBLOCK, regular only;
    # nothing is written to it
    ("api.py", "create_session", "open"),  # a telemetry session, not a file
    ("tools.py", "run_cli", "Popen"),  # a generator's command line: it writes only its answer,
    # to a fresh 0700 folder of the call's own (`CliTool.ask`); a Claude call writes nothing
    ("gitcheck.py", "<module>", "run"),  # git, reading only: status, rev-parse, ls-files
    ("ruletest.py", "_conn", "connect"),  # an in-memory database (":memory:"), no file
    ("merged.py", "_clonefile", "CDLL"),  # macOS clonefile: a view file cloned to a new name of
    # its own (refused if anything is there; the source not followed: CLONE_NOFOLLOW)
}
# The calls that put bytes in a file or make one; removing, renaming away or chmod don't
# write through a name and are left to the set writers' own check above
_CONTENT_WRITES = {"write_text", "write_bytes", "truncate", "ftruncate", "mkstemp", "mktemp",
                   "NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile", "copytree",
                   "symlink_to", "hardlink_to", "touch", "unpack_archive", "make_archive",
                   "extract", "extractall", "FileIO"}  # fmt: skip


@functools.cache
def _constants(tree: ast.Module) -> dict[str, ast.expr]:
    """Module-level names assigned exactly once, to their value."""
    seen = Counter(t.id for n in tree.body if isinstance(n, ast.Assign | ast.AnnAssign)
                   for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
                   if isinstance(t, ast.Name))  # fmt: skip
    return {
        n.targets[0].id: n.value
        for n in tree.body
        if isinstance(n, ast.Assign) and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name) and seen[n.targets[0].id] == 1
    }  # fmt: skip


def _open_mode(call: ast.Call, method: bool) -> ast.expr | bool | None:
    """`open(path, mode)`'s mode, or `something.open(mode)`'s (Path.open); True when it can't be
    told (`*args` or `**kwargs`: counted as a write; review round 29, fourteenth delta check)."""
    if any(isinstance(a, ast.Starred) for a in call.args) or any(
        k.arg is None for k in call.keywords
    ):
        return True
    pos = 0 if method else 1
    if len(call.args) > pos:
        return call.args[pos]
    return next((k.value for k in call.keywords if k.arg == "mode"), None)


def _imports(tree: ast.Module, package: str = "") -> dict[str, str]:
    """Each name an import binds, to what it names: `import os as _os` gives _os -> os, `import
    logging.handlers` gives logging -> logging, `from io import open as o` gives o -> io.open, and
    in package `package` `from . import setio` gives setio -> ecf.eval.setio. `_write_calls`
    works it out once per module (review round 29, nineteenth delta check: once per call made
    the scan quadratic)."""
    out = {m: m for m in ("os", "shutil", "tempfile", "subprocess")}  # however reached
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            if n.level:  # relative: from the module's own package, one level up per extra dot
                base = package.split(".")[: len(package.split(".")) - (n.level - 1)]
                mod = ".".join([*base, *([mod] if mod else [])]) if package else "." * n.level + mod
            out |= {a.asname or a.name: f"{mod}.{a.name}" for a in n.names if a.name != "*"}
    return out


_STD_MODS = {"os", "shutil", "tempfile", "subprocess", "io", "codecs", "gzip", "bz2", "lzma",
             "zipfile", "tarfile", "dbm", "shelve", "sqlite3", "logging", "asyncio", "pty",
             "importlib", "builtins", "ctypes", "sys", "urllib", "posix", "nt", "_io",
             "_posixsubprocess", "fileinput"}  # fmt: skip


# The modules behind os, io and subprocess, reached by their own names
_SAME_AS = {"posix": "os", "nt": "os", "_io": "io", "_posixsubprocess": "subprocess"}


def _qualified(f: ast.expr, names: dict[str, str]) -> str:
    """What a name or attribute chain names, through the module's imports: `_os.replace` and
    `mv` (`from os import replace as mv`) give "os.replace", `open` the builtin "open"; on
    anything else (a path, a file, a call's result) a method gives ".name"."""
    parts: list[str] = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name) and (f.id in names or not parts):
        chain = [*names.get(f.id, f.id).split("."), *reversed(parts)]
        # a module reached through another one's attribute (`tools.os.replace`) is that module
        at = max((i for i, p in enumerate(chain) if p in _STD_MODS), default=0)
        chain = chain[at:]
        return ".".join([_SAME_AS.get(chain[0], chain[0]), *chain[1:]])
    return "." + parts[0] if parts else ""  # `f()()`, `fs[0](...)`: nothing named


# What can write when called, by what it names (review round 29, fifteenth delta check: a list of
# method names missed `getattr(os, "replace")`, a renamed `open`, writer classes and programs).
# A program run can write anything it is told to: each is listed with what it writes.
_RUNS = {"subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call",
         "subprocess.check_output", "subprocess.getoutput", "subprocess.getstatusoutput",
         "os.system", "os.popen", "os.posix_spawn", "os.posix_spawnp", "os.execv", "os.execve",
         "os.execvp", "os.execvpe", "os.execl", "os.execle", "os.execlp", "os.execlpe",
         "os.spawnv", "os.spawnve", "os.spawnvp", "os.spawnvpe", "os.spawnl", "os.spawnle",
         "os.spawnlp", "os.spawnlpe", "asyncio.create_subprocess_exec",
         "asyncio.create_subprocess_shell", "pty.spawn"}  # fmt: skip
_WRITERS = _RUNS | {"sqlite3.connect", "logging.FileHandler", "urllib.request.urlretrieve",
                    ".subprocess_exec", ".subprocess_shell", "logging.config.dictConfig",
                    "logging.config.fileConfig",
                    "logging.handlers.RotatingFileHandler", "logging.handlers.WatchedFileHandler",
                    "logging.handlers.TimedRotatingFileHandler", "io.FileIO", "os.mkfifo",
                    "os.mknod", "os.truncate", "os.ftruncate", "os.posix_fallocate",
                    "os.sendfile", "os.copy_file_range", "os.link", "os.symlink", "os.rename",
                    "os.renames", "os.replace", "shutil.copy", "shutil.copy2", "shutil.copyfile",
                    "shutil.copytree", "shutil.move", "shutil.make_archive",
                    "shutil.unpack_archive", "tempfile.mkstemp", "tempfile.mktemp",
                    "tempfile.NamedTemporaryFile", "tempfile.TemporaryFile",
                    "tempfile.SpooledTemporaryFile", "zipfile.ZipFile.extract",
                    "zipfile.ZipFile.extractall"}  # fmt: skip
# A module or name reached by a computed name: whatever it is, it can't be told, so it counts
_DYNAMIC = {
    "__import__",
    "importlib.import_module",
    "importlib.__import__",
    "builtins.__import__",
    "vars",
    "exec",
    "eval",
    "operator.methodcaller",
    "operator.attrgetter",
    "globals",
    "locals",
    # a system library's function, called with no Python check (merged.py's `clonefile`;
    # review round 29, seventeenth delta check)
    "ctypes.CDLL",
    "ctypes.PyDLL",
    "ctypes.LibraryLoader",
    "ctypes.cdll",
    "ctypes.pydll",
}
# Writers only when given a file or told to work in place: the argument's place and name
_WRITES_IF = {"logging.basicConfig": (99, "filename"), "fileinput.input": (1, "inplace"),
              "fileinput.FileInput": (1, "inplace")}  # fmt: skip
# Openers whose mode (second, or `mode=`/`flag=`) says whether they write; the value is the
# mode used when none is given
_MODED = {"open": "r", "builtins.open": "r", "io.open": "r", "codecs.open": "r",
          "gzip.open": "rb", "gzip.GzipFile": "rb", "bz2.open": "rb", "bz2.BZ2File": "r",
          "lzma.open": "rb", "lzma.LZMAFile": "r", "zipfile.ZipFile": "r",
          "zipfile.PyZipFile": "r", "tarfile.open": "r", "tarfile.TarFile": "r", "dbm.open": "r",
          "dbm.dumb.open": "c", "dbm.gnu.open": "r", "dbm.ndbm.open": "r", "dbm.sqlite3.open": "r",
          "shelve.open": "c", "shelve.DbfilenameShelf": "c", "os.fdopen": "r"}  # fmt: skip
_READ_MODE = re.compile(r"[rbtU]+|r[:|][a-z0-9]*")  # open's, tarfile's (r:gz), dbm's (r)


def _mode_writes(mode: ast.expr | bool | None, default: str) -> bool:
    if mode is True:
        return True
    text = default if mode is None else getattr(mode, "value", None)
    return not (isinstance(text, str) and _READ_MODE.fullmatch(text))


def _dynamic(q: str) -> bool:
    """A module or function reached by a computed name, or a system library loaded."""
    return q in _DYNAMIC or q.startswith(("ctypes.cdll.", "ctypes.pydll."))


def _writes_when_called(q: str) -> bool:
    """Whether calling what `q` names can write (an opener only with some modes)."""
    last = q.rsplit(".", 1)[-1]
    return (q in _WRITERS or q in _MODED or _dynamic(q) or last in _CONTENT_WRITES
            or q == "os.open" or q in _WRITES_IF
            or (q.startswith(".") and last in {"open", "replace", "rename"}))  # fmt: skip


def _can_write(call: ast.Call, tree: ast.Module | None = None,
               names: dict[str, str] | None = None) -> bool:  # fmt: skip
    """Whether a call can put bytes in a file: an os, shutil or tempfile write, a program run, a
    database or log file opened, an open whose mode or flags aren't written out as read-only, or
    a Path write, however the module or function is imported (`names`: the module's imports)."""
    tree = tree or ast.Module(body=[], type_ignores=[])
    names = names if names is not None else _imports(tree)
    q = _qualified(call.func, names)
    last = q.rsplit(".", 1)[-1]
    if q in _WRITERS or _dynamic(q) or last in _CONTENT_WRITES:
        return True
    if q == "os.open":
        mods = {n for n, m in names.items() if m == "os"} | {"os"}
        return not _read_only(_arg(call, 1, "flags"), frozenset(mods), _constants(tree))
    if q in _WRITES_IF:  # only with a file or in place: the argument at this place or name
        pos, kw = _WRITES_IF[q]  # `*args` can't be told: counted
        return (len(call.args) > pos or any(isinstance(x, ast.Starred) for x in call.args)
                or any(k.arg in {kw, None} for k in call.keywords))  # fmt: skip
    if q in _MODED or q == ".open":  # `.open`: Path.open(mode), the mode first
        mode = _open_mode(call, method=q == ".open")
        if mode is None:
            mode = next((k.value for k in call.keywords if k.arg == "flag"), None)  # dbm, shelve
        return _mode_writes(mode, _MODED.get(q, "r"))
    if q.startswith(".") and last in {"replace", "rename"}:
        kws = [k.arg for k in call.keywords]  # Path.replace(target); str's takes 2
        return (len(call.args), tuple(kws)) in {(1, ()), (0, ("target",))}
    return False


def _arg(call: ast.Call, pos: int, kw: str) -> ast.expr | None:
    if len(call.args) > pos:
        return call.args[pos]
    return next((k.value for k in call.keywords if k.arg == kw), None)


@functools.cache
def _is_module(q: str) -> bool:
    """Whether `q` names a module: a standard one above, or one of ecf's own (a module of ours
    used as a value reaches the modules it imports; review rounds 29, eighteenth and nineteenth
    delta checks)."""
    if q in _STD_MODS:
        return True
    if q.split(".", 1)[0] not in {"ecf", "ecf_server"}:
        return False
    *pkg, last = q.split(".")  # read from the source tree, nothing imported; names exact, as
    folder = SRC.parent.joinpath(*pkg)  # on Linux, though macOS's disk ignores case
    try:
        listed = set(os.listdir(folder))
    except OSError:
        return False
    return f"{last}.py" in listed or (last in listed and (folder / last / "__init__.py").is_file())


def _getattr_owner(node: ast.AST, parent: ast.AST) -> bool:
    """`node` is `getattr`'s object, the name it takes looked at on its own."""
    return (
        isinstance(parent, ast.Call)
        and _called(parent) == "getattr"
        and (bool(parent.args) and parent.args[0] is node)
    )


def _getattr_ref(node: ast.AST, names: dict[str, str]) -> str | None:
    """`getattr(x, "name")` of something that writes, or of a module by a computed name."""
    if isinstance(node, ast.Call) and _called(node) == "getattr" and len(node.args) >= 2:
        got = node.args[1]
        owner = _qualified(node.args[0], names)
        if not isinstance(got, ast.Constant):
            # a module's name that can't be told; a record's field read by name (results) is
            # data, and a method so named on an object is a stated limit
            return "getattr ?" if owner in set(names.values()) | _STD_MODS else None
        if isinstance(got.value, str) and (
            _writes_when_called(f"{owner}.{got.value}") or _writes_when_called(f".{got.value}")
        ):
            return f"getattr {got.value}"
    return None


def _value_ref(node: ast.Name | ast.Attribute, parent: ast.AST, q: str, names: dict[str, str],
               rebound: Container[str]) -> str | None:  # fmt: skip
    """`_write_ref` for a name or attribute taken as a value, not called. A name the module
    also binds itself (a local `merged` list beside `from ecf.eval import merged`) isn't
    taken for the module."""
    if isinstance(node, ast.Name) and node.id not in names and node.id not in {"open", *_DYNAMIC}:
        return None  # a local name
    head = node
    while isinstance(head, ast.Attribute):
        head = head.value
    if (_is_module(q) and not _getattr_owner(node, parent)
            and str(getattr(head, "id", "")) not in rebound):  # fmt: skip
        return f"{q} as a value"  # its functions then called under any name
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "__dict__"
        and q.split(".", 1)[0] in (set(names.values()) | _STD_MODS)
    ):
        return "__dict__"  # a module's names by a computed key (a record's fields aren't)
    if _writes_when_called(q) and q not in {".open", ".replace", ".rename"}:
        return q.rsplit(".", 1)[-1]  # an attribute `open` (a run's flag) is no write
    return None


def _write_ref(node: ast.AST, parent: ast.AST, names: dict[str, str],
               rebound: Container[str] = frozenset()) -> str | None:  # fmt: skip
    """Something that writes taken without calling it (`w = p.write_text`, `o = open`,
    `partial(os.replace, ...)`, `getattr(os, "replace")`, `getattr(os, name)`): counted as its
    call is (review round 29, fourteenth and fifteenth delta checks)."""
    called = isinstance(parent, ast.Call) and parent.func is node
    if not (isinstance(node, ast.Name | ast.Attribute) and isinstance(node.ctx, ast.Load)):
        return _getattr_ref(node, names)
    q = _qualified(node, names)
    if q in {"sys.modules", "__builtins__"}:  # every module, every builtin, by a computed key
        return q
    if called or isinstance(parent, ast.Attribute):
        return None
    return _value_ref(node, parent, q, names, rebound)


_COMPS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, *_COMPS)
_TABLE_NAMES: dict[type[ast.AST], str] = {
    ast.Lambda: "lambda", ast.ListComp: "listcomp", ast.SetComp: "setcomp",
    ast.DictComp: "dictcomp", ast.GeneratorExp: "genexpr",
}  # fmt: skip


class _Hidden:
    """The names that, where a load sits, aren't the module's own binding: a name the scope
    binds as its own (or takes from an enclosing function), as Python's `symtable` says, and an
    inlined comprehension's loop names inside it (review round 29, nineteenth and twentieth
    delta checks: a hand-made scope rule kept differing from Python's)."""

    def __init__(self, table: symtable.SymbolTable, extra: frozenset[str] = frozenset(),
                 outside: _Hidden | None = None, *, unmatched: bool = False) -> None:  # fmt: skip
        self.table, self.extra, self.outside = table, extra, outside  # outside: where defined
        # inside a type parameter's bound: its scopes' tables sit under the type parameters'
        # own, so none is matched (a wrong one could hide a name; none can only add one)
        self.unmatched = unmatched

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        if name in self.extra:
            return True
        if self.table.get_type() == "module":  # a str, or a StrEnum from Python 3.13
            return False  # a module level rebinding still counts as the module
        try:
            s = self.table.lookup(name)
        except KeyError:
            return False
        return (s.is_local() and not s.is_global()) or s.is_free()


def _split(node: ast.AST, deferred: bool = True) -> tuple[list[ast.AST], list[ast.AST]]:
    """A scope's parts run where it is defined (defaults, decorators, bases, a comprehension's
    first source; annotations too unless `deferred`, `from __future__ import annotations`),
    and its own (body)."""
    if isinstance(node, _COMPS):
        first, *rest = node.generators
        own = [x for x in ast.iter_child_nodes(node) if not isinstance(x, ast.comprehension)]
        src: list[ast.AST] = [first.iter]
        return src, [first.target, *first.ifs, *rest, *own]
    if isinstance(node, ast.ClassDef):
        return [*node.decorator_list, *node.bases, *node.keywords], list(node.body)
    assert isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
    a = node.args
    outer: list[ast.AST] = [*a.defaults, *(d for d in a.kw_defaults if d is not None)]
    if isinstance(node, ast.Lambda):
        return outer, [node.body]
    if not deferred:  # annotations run, where the function is defined
        every = [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]
        outer += [x.annotation for x in every if x is not None and x.annotation is not None]
        outer += [node.returns] if node.returns is not None else []
    return [*node.decorator_list, *outer], list(node.body)


def _type_params(node: ast.AST) -> list[ast.AST]:
    """A generic def's or class's type parameters' bounds and defaults (`[T: X = Y]`), looked at
    where it is defined (review round 29, twenty-second delta check)."""
    parts = [(getattr(p, "bound", None), getattr(p, "default_value", None))
             for p in getattr(node, "type_params", [])]  # fmt: skip
    return [x for pair in parts for x in pair if x is not None]


def _defers_annotations(tree: ast.Module) -> bool:
    return any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
               and any(a.name == "annotations" for a in n.names) for n in tree.body)  # fmt: skip


def _comp_targets(node: ast.AST) -> frozenset[str]:
    return frozenset(n.id for g in getattr(node, "generators", []) for n in ast.walk(g.target)
                     if isinstance(n, ast.Name))  # fmt: skip


def _write_calls(f: Path, package: str | None = None) -> set[tuple[str, str, str]]:
    """(file, enclosing function or "<module>", call) for each write call in `f`. Whether a
    name is the module of that name is Python's own answer (`symtable`), scope by scope."""
    found: set[tuple[str, str, str]] = set()

    def look(nodes: list[ast.AST], parent: ast.AST, fn: str, hidden: _Hidden) -> None:
        # an annotation is a type, never run, under `from __future__ import annotations`
        typed = [getattr(parent, a, None) for a in ("annotation", "returns")] if deferred else []
        for child in nodes:
            if any(child is x for x in typed):
                continue
            here = child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else fn
            if isinstance(child, ast.ImportFrom) and any(a.name == "*" for a in child.names):
                found.add((f.name, here, "import *"))  # its names can't be told
            elif isinstance(child, ast.Call) and _can_write(child, tree, names):
                found.add((f.name, here, _called(child)))
            elif (ref := _write_ref(child, parent, names, hidden)) is not None:
                found.add((f.name, here, ref))
            if isinstance(child, ast.TypeAlias):  # `type X = ...`: its tables sit under its own,
                lazy = [*_type_params(child), child.value]  # so none is matched (delta check 24)
                look(lazy, child, here, _Hidden(hidden.table, hidden.extra, hidden.outside,
                                                unmatched=True))  # fmt: skip
            elif isinstance(child, _SCOPES):
                outer, own = _split(child, deferred)
                look(outer, child, fn, hidden)
                bounds = _type_params(child)  # where defined; review round 29, delta checks 22-23
                look(bounds, child, fn, _Hidden(hidden.table, hidden.extra, hidden.outside,
                                                unmatched=True))  # fmt: skip
                look(own, child, here, _inner(child, hidden))
            else:
                look(list(ast.iter_child_nodes(child)), child, here, hidden)

    def _inner(node: ast.AST, hidden: _Hidden) -> _Hidden:
        want = _TABLE_NAMES.get(type(node), getattr(node, "name", ""))
        line = getattr(node, "lineno", 0)
        kids = tables.setdefault(id(hidden.table), list(hidden.table.get_children()))
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        for i, k in enumerate([] if hidden.unmatched else kids):
            wrapped = str(k.get_type()).startswith("type param")  # `def f[T]`: its own is inside
            if (
                k.get_name() != want
                or k.get_lineno() != line
                or (str(k.get_type()) != kind and not wrapped)
            ):
                continue  # matched by name, line and kind (review round 29, delta check 24)
            del kids[i]  # each table once, in order
            own = k
            if wrapped:  # its own comes last, after the type variables' (one may share its name)
                own = next(c for c in reversed(k.get_children())
                           if c.get_name() == want and str(c.get_type()) == kind)  # fmt: skip
            return _Hidden(own, outside=hidden)
        # an inlined comprehension (Python 3.12+) has no table: its loop names are its own, and
        # in a class body it sees not the class's names but those around the class
        base = hidden
        while base.table.get_type() == "class" and base.outside:  # class scopes never nest
            base = base.outside
        return _Hidden(
            base.table, base.extra | _comp_targets(node), base.outside, unmatched=hidden.unmatched
        )

    if package is None:  # src/ecf/eval/tools.py is in ecf.eval
        rel = (
            f.resolve().relative_to(SRC.parent) if f.resolve().is_relative_to(SRC.parent) else None
        )
        package = ".".join(rel.parent.parts) if rel else ""
    text = f.read_text(encoding="utf-8")
    tree = _parsed(f)
    names = _imports(tree, package)
    deferred = _defers_annotations(tree)
    tables: dict[int, list[symtable.SymbolTable]] = {}
    top = _Hidden(symtable.symtable(text, str(f), "exec"))
    look(list(ast.iter_child_nodes(tree)), tree, "<module>", top)
    return found


@pytest.mark.parametrize(
    ("code", "ref"),
    [("w = p.write_text", "write_text"), ("f(os.ftruncate)", "ftruncate"),
     ("getattr(p, 'write_bytes')", "getattr write_bytes"), ("getattr(p, 'open')", "getattr open"),
     ("p.write_text(t)", "write_text"), ("x = p.read_text", None),
     ("getattr(os, 'replace')", "getattr replace"), ("getattr(shutil, 'copy')", "getattr copy"),
     ("getattr(os, n)", "getattr ?"), ("getattr(p, n)", None), ("o = open", "open"),
     ("partial(open, mode='w')", "open"), ("x = f.open", None), ("r = subprocess.run", "run"),
     ("h = logging.FileHandler", "FileHandler"), ("c = sqlite3.connect", "connect"),
     ("x: subprocess.Popen[bytes] = f", None), ("getattr(os, 'rep' + t)", "getattr ?"),
     ("d = os.__dict__", "__dict__"), ("d = p.__dict__", None), ("i = __import__", "__import__"),
     ("m = os", "os as a value"), ("f(shutil)", "shutil as a value"),
     ("getattr(os, 'O_EVTONLY', 0)", None), ("m = sys.modules['os']", "sys.modules"),
     ("g = globals", "globals"), ("c = ctypes.CDLL", "CDLL"), ("x = os.path", None),
     ("sys.modules.get('os').replace(a, t)", "sys.modules"), ("m = tools.os", "os as a value"),
     ("m = setio", "ecf.eval.setio as a value"), ("f = setio.reason", None),
     ("o = __builtins__['open']", "__builtins__"), ("m = posix", "os as a value"),
     ("merged = [m]; x = merged", None)],
)  # fmt: skip
def test_the_write_scan_sees_a_write_method_taken_uncalled(
    tmp_path: Path, code: str, ref: str | None
) -> None:
    mod = tmp_path / "m.py"
    mod.write_text("from __future__ import annotations\n"
                   "import os, shutil, subprocess, logging, sqlite3, sys, ctypes, posix\n"
                   "from ecf.eval import setio, tools, merged\n"
                   f"def g(p, t, f, n):\n    {code}\n")  # fmt: skip
    assert _write_calls(mod) == ({("m.py", "g", ref)} if ref else set())


def test_the_write_scan_counts_a_star_import(tmp_path: Path) -> None:
    """`from os import *` binds names the scan can't tell: counted as a write to list (review
    round 29, sixteenth delta check)."""
    mod = tmp_path / "m.py"
    mod.write_text("from os import *\n")
    assert _write_calls(mod) == {("m.py", "<module>", "import *")}


def test_a_local_name_hides_a_module_only_in_its_own_function(tmp_path: Path) -> None:
    """A function's own `merged` list hides the module `merged` in that function only; a module
    level rebinding still counts as the module (review round 29, nineteenth delta check)."""
    mod = tmp_path / "m.py"
    mod.write_text("from ecf.eval import merged, tools\n"
                   "def h():\n    merged = []\n    return merged\n"
                   "def k(os=None):\n    return os\n"
                   "def j():\n    return merged, tools.os\n"
                   "def n():\n    global merged\n    merged = 1\n    return merged\n")  # fmt: skip
    assert _write_calls(mod) == {("m.py", "j", "ecf.eval.merged as a value"),
                                 ("m.py", "j", "os as a value"),
                                 ("m.py", "n", "ecf.eval.merged as a value")}  # fmt: skip


@pytest.mark.parametrize(
    ("body", "seen"),
    [("def g(y):\n    a = [1 for merged in y]\n    return merged\n", True),
     ("def g(y):\n    return [merged for merged in y]\n", False),
     ("def g(y):\n    a = [merged for merged in y]\n    return a, merged.refresh\n", False),
     ("def g(y):\n    return list(merged for merged in y)\n", False),
     ("def f(merged=merged):\n    return 1\n", True),
     ("def f(merged=1):\n    return merged\n", False),
     ("f = lambda merged=merged: 0\n", True),
     ("def deco(x):\n    return x\n@deco(merged)\ndef h(merged=1):\n    return merged\n", True),
     ("def g():\n    merged = 1\n    def h():\n        global merged\n        return merged\n"
      "    return h\n", True),
     ("def g():\n    merged = 1\n    def h():\n        return merged\n    return h\n", False),
     ("class C:\n    merged = 1\n    x = merged\n", False),
     ("class C:\n    def m(self):\n        return merged\n", True),
     ("class C:\n    merged = 1\n    y = [merged for q in z]\n", True),
     ("class A:\n    merged = 1\n    class B:\n        y = [merged for q in z]\n", True),
     ("def f[T: merged]():\n    return 1\n", True),
     ("def f[T: (lambda: merged)](a=(lambda merged: 0)):\n    return a\n", True),
     ("class C[T: (merged for q in z)]:\n    x = (0 for merged in z)\n", True),
     ("def f[f: (lambda merged: 0)](): return (lambda: merged)\n", True),
     ("class C[C: (lambda merged: 0)]: x = (lambda: merged)\n", True),
     ("def f[f: int]():\n    merged = 1\n    return merged\n", False),
     ("type X = (lambda: merged); y = (lambda merged: 0)\n", True),
     ("type X[T: (lambda: merged)] = int; y = (lambda merged: 0)\n", True),
     ("type X = int; y = (lambda merged: merged)\n", False),
     ("class C:\n    merged = [1]\n    y = [q for q in merged]\n", False),
     ("def f[T](x: T) -> T:\n    merged = 1\n    return merged\n", False),
     ("x: (lambda merged: 0) = (lambda: merged)\n", True),
     ("def g(a: merged = 1):\n    return a\n", True)],
)  # fmt: skip
def test_the_scan_decides_a_names_scope_as_python_does(
    tmp_path: Path, body: str, seen: bool
) -> None:
    """Whether a name is the module of that name is Python's own answer (`symtable`): a
    comprehension's loop name, a function's own argument, a closure, a class body, `global`;
    defaults and decorators are looked at where they run (review round 29, twentieth delta
    check)."""
    mod = tmp_path / "m.py"
    mod.write_text("from ecf.eval import merged\n" + body)
    hits = {c for _f, _fn, c in _write_calls(mod)}
    assert ("ecf.eval.merged as a value" in hits) is seen


def test_a_module_of_ours_is_told_by_its_exact_name() -> None:
    """Read from the source tree, nothing imported, and names exact as on Linux, though
    macOS's disk ignores case (review round 29, twentieth delta check)."""
    assert _is_module("ecf.eval.setio") and _is_module("ecf.eval") and _is_module("ecf_server")
    assert not _is_module("ecf.eval.Setio") and not _is_module("ecf.eval.setio.reason")


def test_a_relative_import_of_a_module_of_ours_is_resolved(tmp_path: Path) -> None:
    """`from . import setio` in a module of `ecf.eval` is `ecf.eval.setio` (review round 29,
    nineteenth delta check)."""
    mod = tmp_path / "m.py"
    mod.write_text("from . import setio\nfrom ..eval import tools\nx = setio\ny = tools\n")
    assert _write_calls(mod, "ecf.eval") == {
        ("m.py", "<module>", "ecf.eval.setio as a value"),
        ("m.py", "<module>", "ecf.eval.tools as a value"),
    }


def test_every_eval_module_defers_its_annotations() -> None:
    """An annotation runs unless the module has `from __future__ import annotations`: each eval
    module with code has it, so the scan may take annotations for types (review round 29,
    twenty-first delta check)."""
    missing = [f.name for f in _eval_modules()
               if (tree := _parsed(f)).body
               and not (len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr))
               and not _defers_annotations(tree)]  # fmt: skip
    assert not missing, missing


def test_every_eval_write_call_is_listed() -> None:
    found: set[tuple[str, str, str]] = set()
    for f in _eval_modules():
        found |= _write_calls(f)
    assert found == WRITE_CALLS, sorted(found ^ WRITE_CALLS)


@pytest.mark.parametrize(
    ("code", "writes"),
    [
        ("open(p)", False), ("open(p, 'rb')", False), ("p.open()", False),
        ("p.open('rb')", False), ("os.open(p, os.O_RDONLY | os.O_NOFOLLOW)", False),
        ("s.replace('a', 'b')", False), ("d.copy()", False), ("os.fdopen(fd, 'rb')", False),
        ("open(p, 'w')", True), ("open(p, mode)", True), ("open(p, mode='a')", True),
        ("open(p, 'r+')", True), ("p.open('x')", True), ("p.open(m)", True),
        ("os.open(p, flags)", True), ("os.open(p, os.O_RDONLY | extra)", True),
        ("os.open(p, os.O_WRONLY | os.O_CREAT)", True), ("os.fdopen(fd, 'a')", True),
        ("os.fdopen(fd, m)", True), ("p.write_text(t)", True), ("os.truncate(p, 0)", True),
        ("os.ftruncate(fd, 0)", True), ("shutil.copy(a, b)", True),
        ("shutil.copyfile(a, b)", True), ("shutil.move(a, b)", True),
        ("tempfile.mkstemp()", True), ("tempfile.NamedTemporaryFile()", True),
        ("os.replace(a, b)", True), ("tmp.replace(p)", True), ("p.touch()", True),
        ("os.rename(a, b)", True), ("os.link(a, b)", True),
        ("os.open(p, getattr(os, 'O_EVTONLY', 0))", False),
        ("os.open(p, getattr(os, 'O_TRUNC', 0))", True),
        ("os.open(p, _RO)", False), ("os.open(p, _RW)", True), ("os.open(p, _TWICE)", True),
        ("_os.open(p, _os.O_WRONLY)", True), ("_os.open(p, _os.O_RDONLY)", False),
        ("o(p, os.O_RDWR)", True), ("mv(a, b)", True), ("copy(a, b)", True),
        ("_os.replace(a, b)", True), ("_os.link(a, b)", True),
        ("open(*a)", True), ("open(p, **kw)", True), ("p.open(**kw)", True),
        ("io.open(p, 'w')", True), ("io.open(p)", False), ("io.open('rb.txt')", False),
        ("codecs.open(p, 'a')", True), ("io.FileIO(p)", True), ("shutil.unpack_archive(a)", True),
        ("z.extractall(d)", True), ("t.extract(m)", True),
        ("zipfile.ZipFile(p, 'w')", True), ("zipfile.ZipFile(p, mode='a')", True),
        ("zipfile.ZipFile(p)", False), ("tarfile.open(p, 'r:gz')", False),
        ("tarfile.open(p, 'w:xz')", True), ("tarfile.TarFile(p, 'a')", True),
        ("gzip.open(p, 'wt')", True), ("gzip.open(p)", False), ("sqlite3.connect(p)", True),
        ("logging.FileHandler(p)", True), ("logging.handlers.RotatingFileHandler(p)", True),
        ("logging.basicConfig(filename=p)", True), ("logging.basicConfig(level=1)", False),
        ("dbm.open(p, 'c')", True), ("dbm.open(p, 'n')", True), ("dbm.open(p, flag='w')", True),
        ("dbm.open(p)", False), ("dbm.open(p, 'r')", False), ("shelve.open(p)", True),
        ("shelve.open(p, flag='r')", False), ("subprocess.run(a)", True),
        ("subprocess.Popen(a)", True), ("os.system(c)", True), ("run(a)", True),
        ("io_open(p, 'w')", True), ("io_open(p)", False), ("open(p, 'rt')", False),
        ("tools.os.replace(a, b)", True), ("tools.subprocess.run(a)", True),
        ("__import__('os')", True), ("importlib.import_module('os')", True),
        ("vars(os)", True), ("operator.methodcaller('write_text', t)", True),
        ("p.rename(target=q)", True), ("s.replace('a', b='c')", False),
        ("ctypes.CDLL(None)", True), ("ctypes.cdll.LoadLibrary(n)", True),
        ("ctypes.cdll.libc.clonefile(a, b, 0)", True), ("globals()", True),
        ("urllib.request.urlretrieve(u, p)", True), ("ctypes.c_int(1)", False),
        ("posix.replace(a, b)", True), ("_io.open(p, 'w')", True), ("_io.open(p)", False),
        ("loop.subprocess_exec(a)", True), ("logging.config.dictConfig(c)", True),
        ("fileinput.input(fs, inplace=True)", True), ("fileinput.input(fs, True)", True),
        ("fileinput.input(fs)", False), ("fileinput.input(*a)", True),
    ],
)  # fmt: skip
def test_the_write_scan_sees_each_way_to_write(code: str, writes: bool) -> None:
    tree = ast.parse(
        "import os as _os\nimport io\nimport codecs\nimport zipfile, tarfile, gzip, sqlite3\n"
        "import logging.handlers\nimport dbm, shelve\nfrom subprocess import run\n"
        "from io import open as io_open\nimport importlib, operator\nfrom ecf.eval import tools\n"
        "import ctypes, urllib.request, posix, _io, fileinput, logging.config\n"
        "from os import open as o, replace as mv\nfrom shutil import copy\n"
        "_RO = os.O_RDONLY | os.O_NOFOLLOW\n_RW = os.O_RDWR\n_TWICE = os.O_RDONLY\n"
        "_TWICE = os.O_WRONLY\n"
    )  # fmt: skip
    call = ast.parse(code, mode="eval").body
    assert isinstance(call, ast.Call)
    assert _can_write(call, tree) is writes


def test_the_default_mix_is_never_written_through_a_dangling_link(tmp_path: Path) -> None:
    """`--write-mix` refuses an existing file, a link to nothing too: it is left as it is and
    its target never created (review round 29, twelfth and thirteenth delta checks)."""
    from ecf.eval import mix  # noqa: PLC0415

    target = tmp_path / "elsewhere" / "made.yaml"
    target.parent.mkdir()
    name = tmp_path / "mix.yaml"
    name.symlink_to(target)
    with pytest.raises(InvalidInputError, match="already exists"):
        mix.write_default(name)
    assert not target.exists() and name.is_symlink()


@pytest.mark.parametrize("plant", ["link", "hard link", "pipe"])
def test_the_draft_run_journal_is_never_added_to_through_a_link_hard_link_or_pipe(
    tmp_path: Path, plant: str
) -> None:
    """The journal is cut and added to in place, so a link, hard link or pipe at its name is
    refused: never cut, written through or waited on (review round 29, thirteenth delta
    check)."""
    from ecf_server import draftruns  # noqa: PLC0415

    target = tmp_path / "target"
    target.write_text("keep, no newline")
    name = tmp_path / "evals" / "r.journal.jsonl"
    name.parent.mkdir(mode=0o700)
    {"link": lambda: name.symlink_to(target), "hard link": lambda: os.link(target, name),
     "pipe": lambda: os.mkfifo(name)}[plant]()  # fmt: skip
    with pytest.raises(InvalidInputError, match="hard link or special file"):
        draftruns.append(name, {"e": 1})
    assert target.read_text() == "keep, no newline"


@pytest.mark.parametrize("plant", ["link", "hard link", "pipe"])
def test_a_refused_journal_stops_the_draft_run(tmp_path: Path, plant: str) -> None:
    """A journal refused by name stops the run closed as a failed write does (5B-14): nothing
    more is handed out, and the request fails (review round 29, fourteenth delta check)."""
    from ecf.errors import ServiceUnavailableError  # noqa: PLC0415
    from ecf_server import claude_eval  # noqa: PLC0415

    target = tmp_path / "target"
    target.write_text("keep")
    name = tmp_path / "r.journal.jsonl"
    {"link": lambda: name.symlink_to(target), "hard link": lambda: os.link(target, name),
     "pipe": lambda: os.mkfifo(name)}[plant]()  # fmt: skip
    run: Any = SimpleNamespace(journal=name, clock=None, stop=threading.Event(), state="ready",
                               detail="", run_id="r" * 16, data_dir=tmp_path)  # fmt: skip
    with pytest.raises(ServiceUnavailableError, match="the draft run stopped") as got:
        claude_eval._j_many(run, [{"e": "claim"}])  # pyright: ignore[reportPrivateUsage]
    assert run.state == "failed" and run.stop.is_set() and run.journal is None
    assert "hard link or special file" in run.detail and target.read_text() == "keep"
    # a restart alone would be refused again: it says what to remove and what that costs, and
    # where to read the outcome, never the outcome (review round 30 and its delta checks 1-3)
    assert (f"remove what is at {name} (its journal: the cases it scored go with it), then"
            " restart the service" in str(got.value))  # fmt: skip
    assert claude_eval.SEE_STATUS in str(got.value) and not predicts_a_restart(str(got.value))
    # if its recovery fails, the record names the refused file (a typed cause; delta 5)
    why = claude_eval._recovery_why(got.value)  # pyright: ignore[reportPrivateUsage]
    assert why.startswith(f"{name}: a symbolic link"), why


_PREDICTS = re.compile(
    r"\b(?:resum(?:e|es|ing)\b|(?:be|get|gets|is) resumed|continu\w*|carr(?:y|ies) on"
    r"|pick(?:s|ed)? (?:it |the run )?up|recover(?:s|ed)?\b|runs? again|retr(?:y|ies|ied))",
    re.I,
)


def predicts_a_restart(text: str) -> bool:
    """Text that says what a restart will do: "restart" with a resume or carry-on word."""
    return "restart" in text.lower() and bool(_PREDICTS.search(text))


def _texts(tree: ast.Module) -> Iterator[tuple[int, str]]:
    """Every string a module can show or record: its constants and f-strings, never a docstring
    (which describes the code to its reader)."""
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)}  # fmt: skip
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr):
            yield n.lineno, ast.unparse(n)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            yield n.lineno, n.value


def test_no_text_predicts_what_a_restart_does() -> None:
    """What a restart does to a stopped run depends on the disk, the attempt's limits and the
    set, so no text anywhere in ecf says what it will do: it says what happened and what to do,
    and the record says the outcome afterwards (review round 30, delta checks 2-3)."""
    bad: list[str] = []
    for path in sorted(SRC.parent.rglob("*.py")):
        for line, text in _texts(_parsed(path)):
            if predicts_a_restart(text):
                bad.append(f"{path.relative_to(SRC.parent)}:{line}: {text[:80]}")
    assert not bad, bad


def test_the_restart_scan_finds_a_prediction() -> None:
    """The scan above catches each way of saying it, and leaves docstrings and outcomes alone."""
    for said in ("a service restart resumes it", "restart: the run carries on",
                 "after a restart it will resume", "a restart picks it up",
                 "it will be resumed after a restart", "restart to resume the run",
                 "a restart continues it", "restart and it gets resumed",
                 "it is picked up after a restart", "a restart recovers it",
                 "after a restart it runs again", "the restart retries it"):  # fmt: skip
        assert list(_texts(ast.parse(f"x = {said!r}")))
        assert predicts_a_restart(said), said
    assert not list(_texts(ast.parse('def f():\n    """a restart resumes it"""')))
    for outcome in ("lost to a service restart; resumed as 1234abcd",
                    "lost to a service restart; not resumed: its attempt passed 48 hours",
                    "fix what stopped the write, then restart the service"):  # fmt: skip
        assert not predicts_a_restart(outcome), outcome


ADVICE = re.compile(
    r"try again|restart the|\bsee `|\bremove (?:it|the|what)\b|\brun `|\brun it\b|\bgive the"
    r"|\bpass --|with `ecf|\bcheck the\b|\bfix (?:its|the)\b|,? then (?:run|restart)\b|\buse `"
    r"|start again|\bconfirm (?:them|it)\b|\bupgrade\b|\bchoose an?\b|\bput the\b"
    r"|\brestore the\b|\bmay allow\b",
    re.I,
)
# The modules whose errors can end a draft run's attempt: a resume's own (`claude_eval`,
# `draftruns`), the set's read (`merged`, `setio`, `evalrun`) and what a start reads with it
# (`drafts`, `cards`, `fixed_labels`, `config`)
_ADVICE_MODULES = (SRC / "eval" / "merged.py", SRC / "eval" / "setio.py",
                   SRC.parent / "ecf_server" / "claude_eval.py",
                   SRC.parent / "ecf_server" / "draftruns.py",
                   SRC.parent / "ecf_server" / "evalrun.py",
                   SRC / "eval" / "drafts.py", SRC / "eval" / "cards.py",
                   SRC / "eval" / "fixed_labels.py",
                   SRC.parent / "ecf_server" / "config.py")  # fmt: skip


def _error_kinds() -> set[str]:
    """Every ecf error type's name, wherever it is defined (subclasses of subclasses too)."""
    import importlib  # noqa: PLC0415

    from ecf import errors  # noqa: PLC0415

    for mod in ("ecf_server.claude_eval", "ecf_server.evalrun", "ecf.eval.merged"):
        importlib.import_module(mod)  # the modules that define error types of their own

    seen: set[str] = set()
    todo: list[type] = [errors.EcfError]
    while todo:
        c = todo.pop()
        seen.add(c.__name__)
        todo += c.__subclasses__()
    return seen


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """A module's top-level string constants, so text passed by name is read too."""
    out: dict[str, str] = {}
    for n in tree.body:
        if not (isinstance(n, ast.Assign) and len(n.targets) == 1):
            continue
        target, value = n.targets[0], n.value
        if (isinstance(target, ast.Name) and isinstance(value, ast.Constant)
                and isinstance(value.value, str)):  # fmt: skip
            out[target.id] = value.value
    return out


def _cause_texts(path: Path, kinds: set[str]) -> Iterator[tuple[int, str]]:
    """Each ecf error's cause text in a module, called by name or through a module, built in a
    variable or raised at once, with any module constant it names read in."""
    tree = _parsed(path)
    consts = _string_constants(tree)
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and n.args):
            continue
        name = (n.func.id if isinstance(n.func, ast.Name)
                else n.func.attr if isinstance(n.func, ast.Attribute) else "")  # fmt: skip
        if name in kinds:
            arg = n.args[0]
            named = [
                consts[x.id] for x in ast.walk(arg) if isinstance(x, ast.Name) and x.id in consts
            ]
            yield n.lineno, " ".join([ast.unparse(arg), *named])


def test_an_errors_cause_holds_no_advice() -> None:
    """In the modules whose errors can end a draft run's attempt, an error's first argument is
    its cause alone and any advice goes in `advice=`, so a record keeps the cause and never a
    "try again" made false by the attempt's end (review round 30, delta checks 6-7)."""
    kinds = _error_kinds()
    assert {"Unreadable", "RefreshBusy", "InvalidInputError"} <= kinds
    bad = [f"{path.name}:{line}" for path in _ADVICE_MODULES
           for line, text in _cause_texts(path, kinds) if ADVICE.search(text)]  # fmt: skip
    assert not bad, bad


def test_the_advice_scan_finds_each_form() -> None:
    """The scan reads a prefixed call, an error built in a variable and a constant's text."""
    import tempfile  # noqa: PLC0415

    kinds = {"InvalidInputError", "RefreshBusy"}
    code = ('HINT = "try again"\n'
            'a = errors.InvalidInputError("x; fix its card")\n'
            'b = InvalidInputError(f"x {HINT}")\n'
            'raise merged.RefreshBusy("busy, then run `ecf eval build`")\n'
            'c = InvalidInputError("the draft checker changed", advice="try again")\n')  # fmt: skip
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "m.py"
        f.write_text(code)
        found = [line for line, text in _cause_texts(f, kinds) if ADVICE.search(text)]
    assert found == [2, 3, 4], found


# Where text enters a draft run's record: `end_detail=` of `_update_record`, the detail of
# `_end_record` (4th) and `_finish` (5th)
# each call that writes a draft run's record detail: its keyword, and its place when passed by
# position (_finish_run's detail goes to the run and its record; delta check 11)
_RECORD_SINKS = {"_update_record": ("end_detail", None), "_end_record": ("detail", 3),
                 "_finish": ("detail", 4), "_finish_run": ("detail", 4)}  # fmt: skip
_CAUSE_HELPERS = {"_cause_of", "_recovery_why"}
# an error's advice with its cause: for whoever is told now, never a record (`reason` gives an
# ecf error's whole message; review round 30, delta check 9)
_NOT_FOR_RECORDS = {"_told", "reason"}


def _sink_args(n: ast.Call) -> list[ast.expr]:
    name = n.func.id if isinstance(n.func, ast.Name) else ""
    if name not in _RECORD_SINKS:
        return []
    kw, pos = _RECORD_SINKS[name]
    # `**mapping` could carry the detail by any key, and `*args` at or before its place could
    # carry it there: each is checked whole (delta checks 9 and 11)
    got = [k.value for k in n.keywords if k.arg == kw or k.arg is None] if kw else []
    if pos is None:
        return got
    star = next((i for i, a in enumerate(n.args) if isinstance(a, ast.Starred)), None)
    if star is not None and star <= pos:
        # an unpacking at or before the detail's place can move any later argument there:
        # each is checked, a Starred by its inner value (delta check 12)
        return got + [a.value if isinstance(a, ast.Starred) else a for a in n.args[star:]]
    return got + ([n.args[pos]] if len(n.args) > pos else [])


def _assigned(fn: ast.AST, name: str) -> list[ast.expr]:
    """What a function assigns to a local name: plain, tuple-to-tuple and walrus assignments."""
    values: list[ast.expr] = []
    for a in ast.walk(fn):
        if isinstance(a, ast.NamedExpr) and a.target.id == name:
            values.append(a.value)
        if not isinstance(a, ast.Assign):
            continue
        for t in a.targets:
            if isinstance(t, ast.Name) and t.id == name:
                values.append(a.value)
            elif isinstance(t, ast.Tuple) and isinstance(a.value, ast.Tuple):
                values += [v for e, v in zip(t.elts, a.value.elts, strict=False)
                           if isinstance(e, ast.Name) and e.id == name]  # fmt: skip
    return values


def _is_caught(x: ast.expr, caught: set[str]) -> bool:
    return isinstance(x, ast.Name) and x.id in caught


def _raw_error_text(expr: ast.expr, fn: ast.AST, caught: set[str], seen: set[str]) -> str | None:
    """Where an expression written to a record could carry an error's own text (its advice
    with it), following the local names it uses to their assignments in the function; None when
    it comes only from fixed text, a cause helper, or a name the function doesn't assign (a
    parameter: its callers are sinks of their own or pass fixed text)."""
    for x in ast.walk(expr):
        if isinstance(x, ast.Attribute) and x.attr in {"detail", "advice", "args"}:
            return f"its .{x.attr}"
        if (
            isinstance(x, ast.Call)
            and isinstance(x.func, ast.Name)
            and x.func.id in {"str", "repr"}
            and any(_is_caught(a, caught) for a in x.args)
        ):
            return f"{x.func.id}() of the error"  # fmt: skip
        if isinstance(x, ast.FormattedValue) and _is_caught(x.value, caught):
            return "the error itself"
        if (
            isinstance(x, ast.Call)
            and isinstance(x.func, ast.Name)
            and x.func.id in _NOT_FOR_RECORDS
        ):
            return f"{x.func.id}()"
        if isinstance(x, ast.Name) and x.id not in seen and not _is_caught(x, caught):
            seen.add(x.id)
            for v in _assigned(fn, x.id):
                if (why := _raw_error_text(v, fn, caught, seen)) is not None:
                    return f"{x.id} = {why}"
    return None


def _helper_args_ok(expr: ast.expr) -> ast.expr:
    """The expression with cause-helper calls taken out, so their own error argument is fine."""

    class Strip(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.AST:
            if isinstance(node.func, ast.Name) and node.func.id in _CAUSE_HELPERS:
                return ast.Constant("")
            return self.generic_visit(node)

    return Strip().visit(ast.parse(ast.unparse(expr), mode="eval")).body


def record_details_with_error_text(tree: ast.Module) -> list[str]:
    """Each write to a draft run's record whose text could carry an error's advice."""
    bad: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        caught = {h.name for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler) and h.name}
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                for arg in _sink_args(n):
                    why = _raw_error_text(_helper_args_ok(arg), fn, caught, set())
                    if why is not None:
                        bad.append(f"{fn.name}:{n.lineno}: {why}")
    return bad


def test_a_reason_tells_what_to_do_and_a_record_keeps_the_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whoever is told now gets an ecf error's whole message, what to do included (`reason`):
    the gate's check and a writer's note after its refresh; a record kept after the attempt
    ends gets the cause alone (`_cause_of`; review round 30, delta checks 8-9)."""
    from ecf.eval import merged  # noqa: PLC0415
    from ecf_server import claude_eval, gate  # noqa: PLC0415

    err = InvalidInputError("the set is being read", advice="try again")
    assert setio.reason(err) == "the set is being read; try again"
    assert setio.reason(InvalidInputError("a\x1bb")) == "a\\u001bb"  # escaped, as before
    cause_of = claude_eval._cause_of  # pyright: ignore[reportPrivateUsage]
    assert cause_of(err) == "the set is being read"
    assert cause_of(InvalidInputError("a\x1bb", advice="x")) == "a\\u001bb"
    # the gate's detail keeps the command to look and what to run
    refused = merged.unreadable(tmp_path / "labels.jsonl", PermissionError(13, "denied"))
    state = gate._unreadable_state(tmp_path, refused)  # pyright: ignore[reportPrivateUsage]
    assert "ls -ldO" in str(state) and "ecf eval build" in str(state), state
    # a writer's note when the merged view couldn't be refreshed keeps what to do
    busy = merged.RefreshBusy(merged.BUSY, advice=merged.BUSY_ADVICE)

    def refused_refresh(*_a: object, **_k: object) -> str:
        raise busy

    def with_private(_r: Path) -> SimpleNamespace:
        return SimpleNamespace(private=tmp_path)

    monkeypatch.setattr(merged, "paths", with_private)
    monkeypatch.setattr(merged, "refresh_merged", refused_refresh)
    note = merged.refresh_after_write(tmp_path)
    assert note is not None and merged.BUSY_ADVICE in note, note


def test_every_record_detail_comes_from_a_cause() -> None:
    """At the one place text enters a draft run's record, it comes from fixed text or a cause
    helper (`_cause_of`, `_recovery_why`), never an error's own text, `reason()`, `.detail` or
    `str()`, which carry its advice; checked where the record is written, whatever made the
    error (review round 30, delta check 8). Limit: a parameter is trusted (its callers are
    checked where they are sinks), and so is text a called function returns."""
    bad = record_details_with_error_text(_parsed(SRC.parent / "ecf_server" / "claude_eval.py"))
    assert not bad, bad


def test_the_record_check_finds_raw_error_text() -> None:
    """The check above finds each way: `.detail`, `str()`, the error in an f-string, and each
    through a local name; a cause helper's own argument is fine."""
    code = (
        "def f():\n"
        "    try:\n        pass\n"
        "    except Exception as exc:\n"
        "        _update_record(d, r, end_detail=exc.detail)\n"
        "        _end_record(c, run, 's', str(exc), complete=False)\n"
        "        why = f'failed ({exc})'\n"
        "        _finish(c, k, run, 's', why)\n"
        "        kept = _cause_of(exc)\n"
        "        _finish(c, k, run, 's', kept)\n"
        "        _update_record(d, r, end_detail=f'x ({_cause_of(exc)})')\n"
        "        _finish(c, k, run, 's', _told(exc))\n"
        "        _update_record(d, r, end_detail=f'x ({reason(exc)})')\n"
        "        _update_record(d, r, **{'end_detail': exc.detail})\n"
        "        _end_record(c, run, 's', detail=exc.detail, complete=False)\n"
        "        _finish_run(c, k, run, 's', exc.detail)\n"
        "        _finish_run(c, k, run, 's', detail=str(exc))\n"
        "        _finish(c, k, *(run, 's', exc.detail))\n"
        "        _finish_run(c, k, run, 's', _cause_of(exc))\n"
        "        _finish_run(c, k, *(run, 's'), exc.detail)\n"
        "        _finish_run(*xs, exc.detail)\n"
        "        _end_record(c, *a, str(exc), complete=False)\n"
        "        _finish_run(c, k, run, 's', *[exc.detail])\n"
        "        _finish_run(c, k, *(run, 's'), 'fixed')\n"
    )
    found = record_details_with_error_text(ast.parse(code))
    assert [x.split(":")[1] for x in found] == ["5", "6", "8", "12", "13", "14", "15", "16", "17",
                                                "18", "20", "21", "22", "23"], found  # fmt: skip


def test_a_failed_recovery_says_why_in_its_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run whose recovery fails records what caused it (a refused journal names its file, a
    disk error its reason, anything else only its kind) and that the attempt is over, never the
    stopped request's advice to restart: nothing resumes it (review round 30, delta check 1)."""
    from ecf.errors import ServiceUnavailableError  # noqa: PLC0415
    from ecf_server import claude_eval  # noqa: PLC0415

    def stopped(cause: Exception) -> ServiceUnavailableError:
        try:
            raise ServiceUnavailableError("the draft run stopped (...); a service restart resumes"
                                          " it from what was written") from cause  # fmt: skip
        except ServiceUnavailableError as exc:
            return exc

    why = claude_eval._recovery_why  # pyright: ignore[reportPrivateUsage]
    refused = InvalidInputError("/x/r.journal.jsonl: a symbolic link, hard link or special file")
    assert why(stopped(refused)) == (
        "/x/r.journal.jsonl: a symbolic link, hard link or special file; this attempt is over"
    )
    full = OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
    assert why(stopped(full)) == f"{os.strerror(errno.ENOSPC)}; this attempt is over"
    assert why(ValueError("email text")) == "ValueError; this attempt is over"
    # an ecf error gives its cause and never its advice: the attempt is over (deltas 5-6)
    from ecf.errors import ConflictError  # noqa: PLC0415
    from ecf.eval.merged import RefreshBusy  # noqa: PLC0415

    busy = RefreshBusy("a refresh is running", advice="try again")
    assert busy.detail == "a refresh is running; try again"  # the request still says both
    assert why(busy) == "a refresh is running; this attempt is over"
    checker = ConflictError("the draft checker changed since the pilot (AP5)")
    assert why(checker) == "the draft checker changed since the pilot (AP5); this attempt is over"
    assert why(stopped(busy)) == "a refresh is running; this attempt is over"
    seen: dict[str, Any] = {}

    def recover_one(*_a: object) -> str:
        raise stopped(refused)

    monkeypatch.setattr(claude_eval, "_recover_one", recover_one)

    def update(*_a: object, **k: Any) -> None:
        seen.update(k)

    monkeypatch.setattr(claude_eval, "_update_record", update)
    rec: Any = SimpleNamespace(run_id="r" * 16, end=None)
    clock: Any = SimpleNamespace(now=lambda: None)
    claude_eval._recover_all(None, clock, tmp_path, [rec], None)  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    assert seen["attempt_over"] is True and "resume" not in seen["end_detail"]
    assert "r.journal.jsonl" in seen["end_detail"]


def test_a_refused_heartbeat_save_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The heartbeat only times a run's end: a save refused by name is logged like a failed one,
    never raised into the request (review round 29, fourteenth delta check)."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from ecf_server import claude_eval  # noqa: PLC0415

    def refused(*_a: object, **_k: object) -> None:
        raise InvalidInputError("draft-runs.json: a symbolic link, hard link or special file")

    monkeypatch.setattr(claude_eval, "_update_record", refused)
    run: Any = SimpleNamespace(draft=True, open=True, last_beat=None, data_dir=tmp_path,
                               run_id="r" * 16)  # fmt: skip
    clock: Any = SimpleNamespace(now=lambda: datetime(2026, 11, 9, tzinfo=UTC))
    claude_eval._beat(clock, run)  # pyright: ignore[reportPrivateUsage]
    assert run.last_beat is None


def test_a_durable_write_in_a_linked_folder_succeeds(tmp_path: Path) -> None:
    """A record saved in a folder reached through a link (a corpus on another disk) is written
    and flushed without a false error (review round 29, fourteenth delta check)."""
    from ecf.eval import corpus_labels  # noqa: PLC0415

    real = tmp_path / "disk"
    real.mkdir()
    (tmp_path / "corpora").symlink_to(real)
    setio.write_durable(tmp_path / "corpora" / "a.json", "{}")
    corpus_labels.save(tmp_path / "corpora" / "c.labels.jsonl", {})
    setio.create_file(tmp_path / "corpora" / "new.txt", "x")
    assert sorted(p.name for p in real.iterdir()) == ["a.json", "c.labels.jsonl", "new.txt"]


def test_a_record_is_flushed_past_the_drives_cache(monkeypatch: pytest.MonkeyPatch,
                                                  tmp_path: Path) -> None:  # fmt: skip
    """On macOS `fsync` leaves a write in the drive's cache; a record is flushed with
    `F_FULLFSYNC` where offered, `fsync` where it isn't or the disk refuses it (review round
    30)."""
    calls: list[str] = []
    real: Any = fcntl.fcntl

    def full(fd: int, op: int, *a: Any) -> Any:
        if op == getattr(fcntl, "F_FULLFSYNC", None):
            calls.append("full")
            raise OSError(errno.ENOTSUP, "no")  # a disk that refuses it
        return real(fd, op, *a)

    def fsync(fd: int) -> None:
        calls.append("fsync")

    monkeypatch.setattr(setio.fcntl, "fcntl", full)
    monkeypatch.setattr(setio.os, "fsync", fsync)
    setio.write_durable(tmp_path / "rec.json", "{}")
    want = ["full", "fsync"] if hasattr(fcntl, "F_FULLFSYNC") else ["fsync"]
    assert calls == want * 2  # the file, then its folder
    assert (tmp_path / "rec.json").read_text() == "{}"


def test_a_full_flush_that_works_or_fails_on_the_disk_is_never_followed_by_fsync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A full flush that works is the flush; one the disk fails (EIO) is raised, never followed
    by an `fsync` that could report a flush that never was (review round 30, delta check 1)."""
    if not hasattr(fcntl, "F_FULLFSYNC"):
        pytest.skip("no F_FULLFSYNC on this system")
    real: Any = fcntl.fcntl
    fail: list[int] = []

    def full(fd: int, op: int, *a: Any) -> Any:
        if op == FULLFSYNC and fail:
            raise OSError(fail[0], os.strerror(fail[0]))
        return real(fd, op, *a)

    def fsync(_fd: int) -> None:
        raise AssertionError("fsync after a full flush")

    monkeypatch.setattr(setio.fcntl, "fcntl", full)
    monkeypatch.setattr(setio.os, "fsync", fsync)
    setio.write_durable(tmp_path / "rec.json", "{}")
    fail.append(errno.EIO)
    with pytest.raises(OSError, match=os.strerror(errno.EIO)):
        setio.write_durable(tmp_path / "rec.json", "[]")
    assert (tmp_path / "rec.json").read_text() == "{}"


@pytest.mark.parametrize("err", [errno.ENOSYS, errno.ENOTTY])
def test_a_volume_without_the_full_flush_falls_back_to_fsync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, err: int
) -> None:
    """A volume that doesn't implement the full flush (macFUSE answers ENOSYS) is flushed with
    `fsync`, never failing every record write (review round 30, delta check 2)."""
    if not hasattr(fcntl, "F_FULLFSYNC"):
        pytest.skip("no F_FULLFSYNC on this system")
    real: Any = fcntl.fcntl
    synced: list[int] = []

    def full(fd: int, op: int, *a: Any) -> Any:
        if op == FULLFSYNC:
            raise OSError(err, os.strerror(err))
        return real(fd, op, *a)

    monkeypatch.setattr(setio.fcntl, "fcntl", full)
    monkeypatch.setattr(setio.os, "fsync", synced.append)
    setio.write_durable(tmp_path / "rec.json", "{}")
    assert len(synced) == 2 and (tmp_path / "rec.json").read_text() == "{}"


@pytest.mark.parametrize("err", [errno.EINVAL, errno.ENOTSUP, errno.EACCES, errno.EIO])
@pytest.mark.parametrize("where", ["open", "fsync"])
def test_a_folder_that_cant_be_flushed_fails_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, err: int, where: str
) -> None:
    """A folder the disk can't open or flush (a network share, one others own, a disk error)
    leaves a new file written and reported so, so a retry doesn't read "already exists"
    (review rounds 29-30)."""
    real_open, real_fsync = os.open, setio.flush_to_disk

    def opened(path: Any, flags: int, *a: Any, **k: Any) -> int:
        if where == "open" and flags & os.O_DIRECTORY:
            raise OSError(err, os.strerror(err), str(path))
        return real_open(path, flags, *a, **k)

    def synced(fd: int) -> None:
        if where == "fsync" and stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(err, os.strerror(err))
        real_fsync(fd)

    monkeypatch.setattr(setio.os, "open", opened)
    monkeypatch.setattr(setio, "flush_to_disk", synced)
    setio.create_file(tmp_path / "new.txt", "x")
    setio.write_durable(tmp_path / "rec.json", "{}")
    assert (tmp_path / "new.txt").read_text() == "x" and (tmp_path / "rec.json").exists()


@pytest.mark.parametrize(
    "there", ["file", "dangling link", "made meanwhile", "made meanwhile, no hard links"]
)
def test_a_new_file_never_replaces_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, there: str
) -> None:
    """`create_file` refuses a name already taken, a link to nothing too, and one taken while it
    wrote (the hard link refuses it; a check then a rename would replace it), leaving no
    temporary file (review round 29, fourteenth delta check)."""
    name, target = tmp_path / "x.labels.jsonl", tmp_path / "elsewhere"
    if there == "file":
        name.write_text("theirs")
    elif there == "dangling link":
        name.symlink_to(target)
    else:
        real = setio.write_new

        def racing(tmp: Path, data: bytes | str, mode: int = 0o644, *, sync: bool = False) -> None:
            real(tmp, data, mode, sync=sync)
            name.write_text("theirs")

        monkeypatch.setattr(setio, "write_new", racing)
        if there.endswith("no hard links"):  # checked again before the rename
            monkeypatch.setattr(os, "link", _no_hard_links)
    with pytest.raises(InvalidInputError, match="already exists"):
        setio.create_file(name, "ours")
    assert not target.exists()
    assert name.is_symlink() or name.read_text() == "theirs"
    assert [p.name for p in tmp_path.iterdir()] == [name.name]


def _no_hard_links(*_a: object, **_k: object) -> None:
    raise OSError(errno.ENOTSUP, "Operation not supported")


def test_a_new_file_falls_back_on_a_disk_without_hard_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "link", _no_hard_links)
    setio.create_file(tmp_path / "x", "ours", 0o600)
    assert (tmp_path / "x").read_text() == "ours"
    assert stat.S_IMODE((tmp_path / "x").stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["x"]


def test_a_merged_corpus_labels_file_is_never_written_over(tmp_path: Path) -> None:
    """The merge's labels file is a new file: one already there is refused and kept, and no
    fixed temporary name is left to fail the next merge (review round 29, fourteenth delta
    check)."""
    from ecf_server import corpus_merge  # noqa: PLC0415

    corpus = tmp_path / "merged.sqlite"
    labels = {("a", "b"): {"id": "a"}}
    assert corpus_merge._write_labels(corpus, labels, "c1") == 1  # pyright: ignore[reportPrivateUsage]
    first = corpus.with_name(corpus.name + corpus_merge.LABELS_SUFFIX)
    kept = first.read_text()
    with pytest.raises(InvalidInputError, match="already exists"):
        corpus_merge._write_labels(corpus, labels, "c2")  # pyright: ignore[reportPrivateUsage]
    assert first.read_text() == kept and stat.S_IMODE(first.stat().st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == [first.name]


def test_the_draft_run_journal_still_cuts_a_short_last_line(tmp_path: Path) -> None:
    from ecf_server import draftruns  # noqa: PLC0415

    name = tmp_path / "r.journal.jsonl"
    draftruns.append(name, {"e": 1})
    with name.open("ab") as f:
        f.write(b'{"e": 2')  # a crash mid-line
    draftruns.append(name, {"e": 3})
    assert draftruns.read(name) == [{"e": 1}, {"e": 3}]
    assert stat.S_IMODE(name.stat().st_mode) == 0o600


def test_an_append_only_log_refuses_a_hard_link(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("keep")
    name = tmp_path / "log.txt"
    os.link(target, name)
    with pytest.raises(InvalidInputError, match="hard link"):
        setio.append_text(name, "more")
    assert target.read_text() == "keep"


@pytest.mark.parametrize("writer", ["settle page", "export page"])
def test_an_existing_page_folder_is_left_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: str
) -> None:
    """A new page folder is 0700; one already there (`~`, a shared folder) is never narrowed,
    its new files 0600 (review round 29, fourteenth delta check)."""
    from ecf import cli_evalgen  # noqa: PLC0415
    from ecf.eval import settle  # noqa: PLC0415

    monkeypatch.setattr(settle, "questions", _no_questions)
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    old.chmod(0o755)
    for d in (old, new):
        if writer == "settle page":
            settle.export(tmp_path / "set", d)
        else:
            cli_evalgen._write_private(d / "page.json", "{}")  # pyright: ignore[reportPrivateUsage]
    assert stat.S_IMODE(old.stat().st_mode) == 0o755
    assert stat.S_IMODE(new.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(f.stat().st_mode) == 0o600 for d in (old, new) for f in d.iterdir())


def test_a_settle_page_folder_is_made_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ecf.eval import settle  # noqa: PLC0415

    monkeypatch.setattr(settle, "questions", _no_questions)
    out = tmp_path / "page"
    settle.export(tmp_path / "set", out)
    assert stat.S_IMODE(out.stat().st_mode) == 0o700


# The eval-set readers, the gate and the git checks: Python 3.12's `Path.resolve()` raises
# RuntimeError on a symbolic link loop, which escaped the gate and failed every service tick;
# they use `setio.real` (Step 7 review)
_SERVER_READERS = ("evalrun.py", "ruletest.py", "gate.py", "claude_eval.py")
NO_RESOLVE = sorted([*SRC.joinpath("eval").glob("*.py"),
                     *(SRC.parent / "ecf_server" / n for n in _SERVER_READERS)])  # fmt: skip


def test_eval_and_gate_code_never_calls_path_resolve() -> None:
    found = [
        f"{p.name}:{node.lineno}"
        for p in NO_RESOLVE
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "resolve"
        and not node.args
        and not node.keywords
    ]  # `merged.resolve(root)` takes the set: only the no-argument Path method is refused
    assert len(NO_RESOLVE) > 30 and all(p.is_file() for p in NO_RESOLVE) and not found, found
