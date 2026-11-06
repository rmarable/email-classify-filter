"""Set writers: atomic writes and the merged-folder guard (EVAL-PRIVATE.md "The overlay", W1,
OD-647)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import fixed_labels, review
from ecf.eval import labels as label_file
from ecf.eval.builder import build_all
from ecf.eval.setio import atomic_write, is_merged, writable_root
from ecf.eval.setio import tmp_path as tmp_name

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
    p = tmp_path / "labels.jsonl"
    stale = tmp_path / ".labels.jsonl.tmp"
    stale.write_text("stale half write")
    atomic_write(p, "one\n")
    atomic_write(p, b"two\n")
    assert p.read_text() == "two\n"
    assert not stale.exists()
    assert tmp_name(p) == stale


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
    ("eval/settle.py", "*"),
    ("eval/mix.py", "*"),
    ("eval/tools.py", "*"),
    ("eval/codex_tool.py", "*"),
    ("eval/corpus_labels.py", "*"),
    ("eval/grade.py", "*"),
    ("cli_evalgen.py", "_write_private"),
    ("eval/generate.py", "Work.append"),
    ("eval/generate.py", "write_manifest"),
    # refresh_merged, the one writer of the merged folder (EVAL-PRIVATE.md, "The merged set")
    ("eval/merged.py", "_place_copy"),
    ("eval/merged.py", "_place_link"),
    ("eval/merged.py", "_build"),
    ("eval/merged.py", "_restat"),  # merge.json's stats, under refresh_merged's lock
}
_PATH_WRITES = {"write_text", "write_bytes", "rename", "link", "copy", "copy2", "copyfile",
                "copytree", "clonefile", "atomic_write"}  # fmt: skip


def _is_write(call: ast.Call) -> bool:
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
    if name in _PATH_WRITES:
        return True
    if (
        name in {"replace", "rename", "link"}
        and getattr(getattr(f, "value", None), "id", "") == "os"
    ):
        return True
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
            if isinstance(child, ast.Call) and _is_write(child):
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
