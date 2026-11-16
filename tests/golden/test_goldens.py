"""v1's item engine against its golden traces (planner step 2, D114 (2), D116). Run with
ECF_GOLDEN_RECORD=1 to rewrite the traces; review the diff before committing them."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from tests.golden.harness import Scenario, golden
from tests.golden.scenarios import SCENARIOS

TRACES = Path(__file__).parent / "traces"


def _first_difference(want: Any, got: Any, path: str = "") -> str | None:
    """Where the golden and the new trace first differ, for the failure message."""
    if isinstance(want, dict) and isinstance(got, dict):
        w, g = cast(dict[str, Any], want), cast(dict[str, Any], got)
        for k in sorted(set(w) | set(g)):
            if k not in w or k not in g:
                return f"{path}/{k}: only in {'trace' if k in g else 'golden'}"
            if d := _first_difference(w[k], g[k], f"{path}/{k}"):
                return d
        return None
    if isinstance(want, list) and isinstance(got, list):
        wl, gl = cast(list[Any], want), cast(list[Any], got)
        for i, (x, y) in enumerate(zip(wl, gl, strict=False)):
            if d := _first_difference(x, y, f"{path}[{i}]"):
                return d
        return None if len(wl) == len(gl) else f"{path}: {len(wl)} in the golden, {len(gl)} now"
    return None if want == got else f"{path}: {json.dumps(want)[:300]} != {json.dumps(got)[:300]}"


@pytest.mark.parametrize("s", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_golden(s: Scenario) -> None:
    got = json.loads(json.dumps(golden(s)))  # tuples become lists, as in the file
    path = TRACES / f"{s.name}.json"
    if os.environ.get("ECF_GOLDEN_RECORD") == "1":
        TRACES.mkdir(exist_ok=True)
        path.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return
    assert path.exists(), f"no golden for {s.name}: record it with ECF_GOLDEN_RECORD=1"
    want = json.loads(path.read_text(encoding="utf-8"))
    assert want == got, _first_difference(want, got)


def test_every_trace_has_a_scenario() -> None:
    names = {s.name for s in SCENARIOS}
    assert {p.stem for p in TRACES.glob("*.json")} <= names
