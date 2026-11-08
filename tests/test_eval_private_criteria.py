"""Step 3's build list and pass criteria (planning-docs/EVAL-PRIVATE.md), traced to their tests
(tests/eval_private_criteria.toml). The table must hold every criterion in the plan's own words,
cover the build list with nothing left over, and name only tests that exist; an entry with no
test says where the plan puts it instead. A change to the plan's Step 3 fails here until the
table follows it."""

from __future__ import annotations

import ast
import re
import tomllib
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "planning-docs" / "EVAL-PRIVATE.md"
TABLE = ROOT / "tests" / "eval_private_criteria.toml"


def _norm(text: str) -> str:
    return " ".join(text.split())


def _step3() -> tuple[str, list[str]]:
    """The build list (the text between "...shares it...): " and ". Tests build") and the pass
    criteria (the *Pass:* text split on semicolons), whitespace folded."""
    text = PLAN.read_text(encoding="utf-8")
    step = text[text.index("3. **Code** on a branch") : text.index("4. **Private repository:**")]
    build, passes = step.split("*Pass:*", 1)
    build = _norm(build)
    build = build[build.index("): ") + 3 : build.index(". Tests build")]
    return build, [c.strip() for c in _norm(passes).split(";") if c.strip()]


def _table() -> dict[str, list[dict[str, Any]]]:
    return tomllib.loads(TABLE.read_text(encoding="utf-8"))


SKIPS = ("skip", "importorskip", "xfail")


def _always_mark(node: ast.expr) -> bool:
    """A mark that always skips: `skip`, `xfail`, or `skipif` on a constant true condition (a
    list or tuple of marks: any of them). A `skipif` on a real condition doesn't count: the
    macOS merge gate runs the suite with 0 skipped, so a test that skipped there fails it."""
    if isinstance(node, ast.List | ast.Tuple):
        return any(_always_mark(e) for e in node.elts)
    target = node.func if isinstance(node, ast.Call) else node
    name = target.attr if isinstance(target, ast.Attribute) else ""
    if name in ("skip", "xfail"):
        return True
    if name == "skipif" and isinstance(node, ast.Call):
        conds = [*node.args[:1], *(k.value for k in node.keywords if k.arg == "condition")]
        return any(isinstance(c, ast.Constant) and bool(c.value) for c in conds)
    return False


def _skip_names(tree: ast.Module) -> set[str]:
    """The names a skip call can go by in this module: `pytest.skip` and the like, and any
    `from pytest import skip as s` alias."""
    out: set[str] = set(SKIPS)
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module == "pytest":
            out |= {a.asname or a.name for a in n.names if a.name in SKIPS}
    return out


def _always_skipped(fn: ast.FunctionDef, names: frozenset[str] = frozenset(SKIPS)) -> bool:
    """Whether the test may not run its checks, so it can't back a criterion: a decorator mark
    that always skips (`_always_mark`), a `pytest.param(..., marks=...)` that does in its
    parametrize, or a `skip`, `importorskip` or `xfail` call anywhere in its body, conditional
    or not (round 3, L10; round 5: not only the first statement; round 6: module and param
    marks, aliased imports). Tests marked `macos` or `private` count: the macOS merge gate runs
    them with 0 skipped."""
    for d in fn.decorator_list:
        if _always_mark(d):
            return True
        for node in ast.walk(d):
            if isinstance(node, ast.keyword) and node.arg == "marks" and _always_mark(node.value):
                return True
    for node in ast.walk(ast.Module(body=fn.body, type_ignores=[])):
        if isinstance(node, ast.Call):
            f = node.func
            name = (
                f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            )
            if name in names:
                return True
    return False


def _module_skipped(tree: ast.Module) -> bool:
    """A module-level `pytestmark` that always skips (annotated or not): every test in the
    module. Not seen here, so left to the 0-skipped macOS gate: a fixture that skips, and an
    early `return` before a test's checks (round 7, L4)."""
    for n in tree.body:
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(
            n, ast.AnnAssign) else []  # fmt: skip
        value = n.value if isinstance(n, ast.Assign | ast.AnnAssign) else None
        if value is not None and any(isinstance(t, ast.Name) and t.id == "pytestmark"
                                     for t in targets) and _always_mark(value):  # fmt: skip
            return True
    return False


@cache
def _tests(path: str) -> frozenset[str]:
    """The module-level `test_` functions pytest collects from `path` and runs (helpers, nested
    functions and always-skipped tests don't count)."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    if _module_skipped(tree):
        return frozenset()
    names = frozenset(_skip_names(tree))
    return frozenset(n.name for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name.startswith("test_") and not _always_skipped(n, names))  # fmt: skip


def test_every_pass_criterion_is_in_the_table_in_the_plans_words() -> None:
    _build, criteria = _step3()
    rows = _table()["criterion"]
    assert [r["id"] for r in rows] == [f"C{i}" for i in range(1, len(rows) + 1)]
    assert [r["plan"] for r in rows] == criteria


def test_the_build_items_cover_the_build_list_and_nothing_else() -> None:
    build, _criteria = _step3()
    rows = _table()["item"]
    assert [r["id"] for r in rows] == [f"B{i}" for i in range(1, len(rows) + 1)]
    rest = build
    for r in rows:  # in the plan's order, each once
        at = rest.find(r["plan"])
        assert at != -1, r["id"]
        assert re.fullmatch(r"[\s,]*(and\s+)?", rest[:at]), (r["id"], rest[:at])
        rest = rest[at + len(r["plan"]) :]
    assert re.fullmatch(r"[\s,.]*", rest), rest


def test_every_entry_names_real_tests_or_says_where_it_lands() -> None:
    """Each entry names collected tests; one with none must quote the plan's own words for
    where it lands instead (a quoted phrase found in the plan)."""
    plan = _norm(PLAN.read_text(encoding="utf-8"))
    table = _table()
    for row in [*table["item"], *table["criterion"]]:
        tests: list[str] = row["tests"]
        if not tests:
            why = str(row.get("note") or row.get("deferred") or "")
            quoted = re.findall(r'"([^"]{12,})"', why)
            assert quoted and all(_norm(q) in plan for q in quoted), (row["id"], why)
        for ref in tests:
            path, name = ref.split("::", 1)
            assert (ROOT / path).is_file(), (row["id"], path)
            assert name.split("[", 1)[0] in _tests(path), (row["id"], ref)


def test_a_test_that_skips_in_its_body_doesnt_count() -> None:
    """Round 3, L10: a body that skips first is never run, so it can't back a criterion."""
    tree = ast.parse('def test_a():\n    """d"""\n    pytest.skip("x")\n\n'
                     "def test_b():\n    assert True\n")  # fmt: skip
    a, b = (n for n in tree.body if isinstance(n, ast.FunctionDef))
    assert _always_skipped(a) and not _always_skipped(b)


def test_a_skip_anywhere_in_the_body_or_a_skipif_doesnt_count() -> None:
    """Round 5: a skip after setup, inside an `if`, `importorskip` or a `skipif` decorator."""
    src = (
        "def test_a():\n    x = 1\n    pytest.skip('x')\n\n"
        "def test_b():\n    if True:\n        pytest.skip('x')\n\n"
        "def test_c():\n    pytest.importorskip('nope')\n\n"
        "@pytest.mark.skipif(True, reason='x')\ndef test_d():\n    assert True\n\n"
        "def test_e():\n    x = 1\n    assert x\n"
    )
    fns = [n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)]
    assert [_always_skipped(f) for f in fns] == [True, True, True, True, False]


def test_module_param_and_aliased_skips_dont_count_but_a_real_condition_does() -> None:
    """Round 6, L10: `pytestmark`, `pytest.param(marks=...)` and `from pytest import skip as s`;
    a `skipif` on a real condition counts (the macOS gate runs with 0 skipped)."""
    always = ast.parse("import pytest\npytestmark = pytest.mark.skip(reason='x')\n")
    real = ast.parse("import pytest\npytestmark = pytest.mark.skipif(not BUILT, reason='x')\n")
    annotated = ast.parse("import pytest\npytestmark: list = [pytest.mark.skip]\n")
    keyword = ast.parse("import pytest\npytestmark = pytest.mark.skipif(condition=True)\n")
    assert _module_skipped(always) and not _module_skipped(real)
    assert _module_skipped(annotated) and _module_skipped(keyword)
    src = (
        "from pytest import skip as s\n\n"
        "@pytest.mark.parametrize('x', [1, pytest.param(2, marks=pytest.mark.skip)])\n"
        "def test_a(x):\n    assert x\n\n"
        "def test_b():\n    s('x')\n\n"
        "@pytest.mark.skipif(sys.platform != 'darwin', reason='x')\ndef test_c():\n    assert 1\n"
    )
    tree = ast.parse(src)
    names = frozenset(_skip_names(tree))
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert [_always_skipped(f, names) for f in fns] == [True, True, False]
