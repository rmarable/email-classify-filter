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


def _always_skipped(fn: ast.FunctionDef) -> bool:
    """`@pytest.mark.skip` (unconditional) on the test, or a body that skips before anything
    else (`pytest.skip(...)` as its first statement after the docstring; round 3, L10). Tests
    marked `macos` or `private` count: the macOS merge gate runs them with 0 skipped."""
    for d in fn.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if isinstance(target, ast.Attribute) and target.attr == "skip":
            return True
    body = fn.body[1:] if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(
        fn.body[0].value, ast.Constant) else fn.body  # fmt: skip
    first = body[0] if body else None
    call = first.value if isinstance(first, ast.Expr) else None
    return (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "skip")  # fmt: skip


@cache
def _tests(path: str) -> frozenset[str]:
    """The module-level `test_` functions pytest collects from `path` and runs (helpers, nested
    functions and always-skipped tests don't count)."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return frozenset(n.name for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name.startswith("test_") and not _always_skipped(n))  # fmt: skip


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
