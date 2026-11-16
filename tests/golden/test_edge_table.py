"""The edge/guard table (planner step 2; D114 (2), D116 Q4; inventory-1b §6).

Every state-machine edge is driven by a golden scenario through a v1 event, or marked "no writer"
or "out of scope". Every guard refuses either through a v1 event (three guards) or only in a direct
`check_transition` call ("pure-function only"), which this file runs for each such guard."""

from __future__ import annotations

import itertools
import json
from dataclasses import fields
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import PolicyDeniedError
from ecf.status import OPEN, Status
from ecf_server import state_machine as sm
from ecf_server.state_machine import Origin, Stage, TransitionContext

S = Status
TRACES = Path(__file__).parent / "traces"

# inventory-1b §6: edges no production code writes, and edges whose writer is out of planner scope
NO_WRITER = frozenset(
    {
        (S.NEW, S.CLASSIFIED),
        (S.AWAITING_APPROVAL, S.PROPOSED),
        (S.NEEDS_HUMAN, S.PROPOSED),
        (S.OBSERVED, S.CLASSIFIED),
        (S.EXECUTED, S.CLASSIFIED),
        (S.UNDONE, S.PROPOSED),
    }
)
OUT_OF_SCOPE = frozenset(
    {
        (S.NEW, S.OBSERVED),  # backfill.py (D114 (8))
        (S.OBSERVED, S.AWAITING_CLAUDE),
        (S.OBSERVED, S.PROPOSED),  # reprocess.py (D113 Q3)
        (S.EXECUTED, S.AWAITING_CLAUDE),
        (S.EXECUTED, S.PROPOSED),
    }
)
# the three guards a v1 event can refuse (inventory-1b §6), and the scenario that shows it
REFUSED_BY_EVENT = {
    (S.PROPOSED, S.AWAITING_APPROVAL): "guard_stage_refuses_request",
    (S.NEEDS_CLARIFICATION, S.CLARIFIED): "guard_rounds_refuse",
    (S.NEEDS_CLARIFICATION, S.AWAITING_STEPUP): "guard_rounds_refuse_fraud",
}


def _edges() -> list[tuple[Status, Status]]:
    return sorted(
        ((f, t) for f, ts in sm.TRANSITIONS.items() for t in ts),
        key=lambda e: (e[0].value, e[1].value),
    )


def _transitions(events: list[dict[str, Any]]) -> set[tuple[Status, Status]]:
    out: set[tuple[Status, Status]] = set()
    for e in events:
        for c in e["commits"]:
            for r in c.get("audit", {}).get("+", []):
                if r["row"]["event"] == "item.transitioned":
                    d = json.loads(r["row"]["data"])
                    out.add((S(d["from"]), S(d["to"])))
    return out


@cache
def _traces() -> dict[str, dict[str, Any]]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in TRACES.glob("*.json")}


def _driven() -> dict[tuple[Status, Status], list[str]]:
    by: dict[tuple[Status, Status], list[str]] = {}
    for name, t in sorted(_traces().items()):
        found = _transitions(t["events"])
        for c in t["crashes"]:
            found |= _transitions(list(c["recovery"].values()))
        for e in found:
            by.setdefault(e, []).append(name)
    return by


def test_the_table_has_every_edge() -> None:
    edges = _edges()
    assert len(edges) == 88 and len(sm.GUARDS) == 51  # inventory-1b §6, counted by script
    assert set(edges) >= NO_WRITER | OUT_OF_SCOPE and not NO_WRITER & OUT_OF_SCOPE
    resolve = [e for e in edges if e[1] in (S.RESOLVED_MANUAL, S.RESOLVED_BY_MAILBOX)]
    assert len(resolve) == 2 * len(OPEN) == 36


@pytest.mark.parametrize("edge", _edges(), ids=[f"{f}->{t}" for f, t in _edges()])
def test_edge(edge: tuple[Status, Status]) -> None:
    driven = _driven()
    if edge in NO_WRITER or edge in OUT_OF_SCOPE:
        assert edge not in driven, (
            f"{edge} is marked unwritten or out of scope, yet {driven[edge]} drive it"
        )
    else:
        assert edge in driven, f"no golden scenario drives {edge[0]}->{edge[1]}"


_FLAGS = [f.name for f in fields(TransitionContext) if f.type in ("bool", bool)]


@cache
def _contexts() -> list[TransitionContext]:
    out: list[TransitionContext] = []
    for stage, origin, rounds in itertools.product(Stage, Origin, (0, 3)):
        for bits in itertools.product((False, True), repeat=len(_FLAGS)):
            out.append(
                TransitionContext(
                    stage=stage,
                    origin=origin,
                    clarification_rounds=rounds,
                    **dict(zip(_FLAGS, bits, strict=True)),
                )
            )
    return out


def _by_name(e: tuple[Status, Status]) -> tuple[str, str]:
    return e[0].value, e[1].value


GUARDED: list[tuple[Status, Status]] = sorted(sm.GUARDS, key=_by_name)


@pytest.mark.parametrize("edge", GUARDED, ids=[f"{f}->{t}" for f, t in GUARDED])
def test_guard(edge: tuple[Status, Status]) -> None:
    """Each guard both passes and refuses; refusals through an event are in their scenario's
    golden, the rest are checked here by a direct call (pure-function only, D116 Q4)."""
    frm, to = edge
    guard = sm.GUARDS[edge]
    refusing = next((c for c in _contexts() if not guard(c)), None)
    passing = next((c for c in _contexts() if guard(c)), None)
    assert refusing is not None and passing is not None
    with pytest.raises(PolicyDeniedError):
        sm.check_transition(frm, to, refusing)
    sm.check_transition(frm, to, passing)
    if edge in REFUSED_BY_EVENT:
        t = _traces()[REFUSED_BY_EVENT[edge]]
        raised = [e["raises"] for e in t["events"] if "raises" in e]
        assert {"type": "PolicyDeniedError", "message": f"guard refused {frm} -> {to}"} in raised
