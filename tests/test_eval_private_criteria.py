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


@cache
def _defined(path: str) -> frozenset[str]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return frozenset(n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))


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
    table = _table()
    for row in [*table["item"], *table["criterion"]]:
        tests: list[str] = row["tests"]
        assert tests or row.get("note") or row.get("deferred"), row["id"]
        for ref in tests:
            path, name = ref.split("::", 1)
            assert (ROOT / path).is_file(), (row["id"], path)
            assert name.split("[", 1)[0] in _defined(path), (row["id"], ref)
