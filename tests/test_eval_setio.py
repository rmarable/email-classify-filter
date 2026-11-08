"""Set writers: atomic writes and the merged-folder guard (EVAL-PRIVATE.md "The overlay", W1,
OD-647)."""

from __future__ import annotations

import ast
import contextlib
import errno
import json
import os
import threading
import time
from pathlib import Path

import pytest

from ecf.cli_evalgen import note_old_temps
from ecf.errors import InvalidInputError
from ecf.eval import fixed_labels, review, setio
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
    (held / "merge.json").write_text("{}")
    for root in (merged, merged / "cases", held, held / "cases"):
        assert is_merged(root)
        with pytest.raises(InvalidInputError, match="merged eval set"):
            writable_root(root)
    plain = tmp_path / "tests" / "eval" / "synthetic"
    plain.mkdir(parents=True)
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


# Writes outside any set, or into a generated set's root only (guarded at entry by `generate`'s
# own check or `gen.verify`), plus the helper itself. Every other write in these files, and every
# `atomic_write`, sits in a function that calls `writable_root`, so a new writer can't skip it.
ALLOWED = {
    ("eval/setio.py", "atomic_write"),
    ("eval/setio.py", "replace_file"),  # the one way a set file is written (round 23)
    ("eval/setio.py", "write_new"),
    ("eval/setio.py", "copy_new"),
    ("eval/setio.py", "_drop"),  # removes only this write's own temporary file
    ("eval/setio.py", "sweep_old_temps"),  # crashed writes' temporary files (round 24)
    ("eval/setio.py", "_sweep_folder"),  # the same, one folder (round 25)
    ("eval/settle.py", "*"),
    ("eval/mix.py", "*"),
    ("eval/tools.py", "*"),
    ("eval/codex_tool.py", "*"),
    ("eval/corpus_labels.py", "*"),
    ("eval/grade.py", "*"),
    ("cli_evalgen.py", "_write_private"),
    ("eval/generate.py", "Work.append"),
    ("eval/generate.py", "write_manifest"),
    ("eval/generate.py", "start"),  # makes a new generated set's empty folder (round 26)
    # refresh_merged, the one writer of the merged folder (EVAL-PRIVATE.md, "The merged set")
    ("eval/merged.py", "_place_copy"),
    ("eval/merged.py", "_place_link"),
    ("eval/merged.py", "_build"),
    ("eval/merged.py", "_copy_cards"),  # a case-only renamed copy's old name, removed first
    ("eval/merged.py", "_sweep"),  # orphans (review round 12)
    ("eval/merged.py", "_clear_extras"),  # anything else in the view, before copying (round 13)
    ("eval/merged.py", "_remove"),  # an extra inside the view, no link followed (round 14)
    ("eval/merged.py", "_clone_or_link"),  # a built email's temporary clone (round 23)
    ("eval/merged.py", "_open_lock"),  # the view's lock file, its flags passed in (round 25)
    ("eval/merged.py", "_writable"),  # the view's folders, made and given 0755 (round 26)
    ("eval/merged.py", "_mode"),  # a view file's or folder's mode (round 26)
}
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
    "rmdir",
    "chmod",
    "lchmod",
    "mkdir",
    "makedirs",
    "symlink",
    "symlink_to",
    "hardlink_to",
}  # fmt: skip (rounds 24, B2, and 26)
# os functions that write only when called through the os module (a list's `remove` doesn't)
_OS_WRITES = {"replace", "rename", "link", "remove", "rmdir", "removedirs", "truncate", "open"}
_READ_FLAGS = {"O_RDONLY", "O_NOFOLLOW", "O_NONBLOCK", "O_DIRECTORY", "O_CLOEXEC", "O_EVTONLY"}


def _os_aliases(tree: ast.AST) -> tuple[set[str], dict[str, str]]:
    """The names `os` is bound to in a module (`import os`, `import os as _os`), and the os and
    shutil functions imported by name (`from os import open as o` gives o -> open; review
    round 26)."""
    mods: set[str] = {"os"}
    funcs: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.asname for a in node.names if a.name == "os" and a.asname}
        elif isinstance(node, ast.ImportFrom) and node.module in {"os", "shutil"}:
            funcs |= {a.asname or a.name: a.name for a in node.names}
    return mods, funcs


def _read_only(flags: ast.expr | None, mods: frozenset[str] = frozenset({"os"})) -> bool:
    """Whether `os.open`'s flags are written out as read-only flags alone (`os.O_RDONLY | ...`,
    or `getattr(os, "O_EVTONLY", 0)`): flags held in a variable or computed count as a write
    (review round 25, S3)."""
    if isinstance(flags, ast.BinOp) and isinstance(flags.op, ast.BitOr):
        return _read_only(flags.left, mods) and _read_only(flags.right, mods)
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
    call: ast.Call, mods: frozenset[str] = frozenset({"os"}), funcs: dict[str, str] | None = None
) -> bool:
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
    via_os = getattr(getattr(f, "value", None), "id", "") in mods
    if isinstance(f, ast.Name) and name in (funcs or {}):  # from os import x [as y]
        name, via_os = (funcs or {})[name], True
    if name in _PATH_WRITES:
        return True
    if (  # Path.replace(target): one argument, where str.replace takes two (review round 26)
        name == "replace"
        and isinstance(f, ast.Attribute)
        and getattr(f.value, "id", "") not in {"dataclasses", *mods}
        and len(call.args) == 1
        and not call.keywords
    ):
        return True
    if via_os and name in _OS_WRITES - {"open"}:
        return True
    if via_os and name == "open":  # os.open(flags)
        flags = call.args[1] if len(call.args) > 1 else None
        flags = next((k.value for k in call.keywords if k.arg == "flags"), flags)
        return not _read_only(flags, mods)
    if name in {"open", "fdopen"}:
        modes = [a.value for a in call.args[1:2] if isinstance(a, ast.Constant)]
        modes += [k.value.value for k in call.keywords
                  if k.arg == "mode" and isinstance(k.value, ast.Constant)]  # fmt: skip
        if not modes and isinstance(f, ast.Attribute):  # Path.open(mode)
            modes = [a.value for a in call.args[:1] if isinstance(a, ast.Constant)]
        return any(isinstance(m, str) and any(c in m for c in "wax") for m in modes)
    return False


def _writes(path: Path) -> list[tuple[str, int, bool]]:
    """(enclosing function's qualified name, line, whether that function calls writable_root)
    for every file write in `path`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods, funcs = _os_aliases(tree)
    found: list[tuple[str, int, bool]] = []

    def visit(node: ast.AST, scope: list[str], guarded: bool) -> None:
        for child in ast.iter_child_nodes(node):
            s, g = scope, guarded
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                s = [*scope, child.name]
                g = isinstance(child, ast.ClassDef) is False and any(
                    isinstance(n, ast.Call) and getattr(n.func, "id", "") == "writable_root"
                    for n in ast.walk(child)
                )
            if isinstance(child, ast.Call) and _is_write(child, frozenset(mods), funcs):
                found.append((".".join(scope), child.lineno, guarded))
            visit(child, s, g)

    visit(tree, [], False)
    return found


def test_no_set_writer_skips_the_guard() -> None:
    files = [*sorted((SRC / "eval").glob("*.py")),
             SRC / "cli.py", SRC / "cli_evalgen.py", SRC / "cli_card_review.py"]  # fmt: skip
    stray: list[str] = []
    for f in files:
        rel = str(f.relative_to(SRC))
        for scope, line, guarded in _writes(f):
            if guarded or (rel, "*") in ALLOWED or (rel, scope) in ALLOWED:
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
    its target is left (review round 25, S1)."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    theirs = _old_temp(elsewhere)
    real = _set(tmp_path / "real", "t-1")
    root = tmp_path / "tests" / "eval" / "synthetic"
    if linked == "the set folder":
        root.parent.mkdir(parents=True)
        root.symlink_to(elsewhere, target_is_directory=True)
        assert sweep_old_temps(root) == ([], [])
    else:
        root.mkdir(parents=True)
        (root / linked).symlink_to(elsewhere, target_is_directory=True)
        if linked != "cases":
            (root / "cases").symlink_to(real / "cases", target_is_directory=True)
        assert sweep_old_temps(root) == ([], [])
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
    (root / "merge.json").write_text("{}")
    [line] = sweep_old_temps(root)[1]
    assert line.startswith(f"{root}: old temporary files not checked")
    (root / "merge.json").unlink()

    def denied(_root: Path) -> bool:
        raise PermissionError(errno.EACCES, "denied", str(root))

    monkeypatch.setattr(setio, "is_merged", denied)
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
