import re
from collections import deque
from pathlib import Path

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (  # Hypothesis leaves part of this signature untyped
    RuleBasedStateMachine,
    invariant,
    rule,
    run_state_machine_as_test,  # pyright: ignore[reportUnknownVariableType]
)

from ecf.errors import ConflictError, PolicyDeniedError
from ecf.status import OPEN, TERMINAL, Status
from ecf_server import state_machine as sm
from ecf_server.state_machine import Origin, Stage, TransitionContext

S = Status
SPEC = Path(__file__).resolve().parents[1] / "SPEC.md"


def spec_edges() -> set[tuple[Status, Status]]:
    text = SPEC.read_text(encoding="utf-8")
    section = text[text.index("### 6.2 Statuses and transitions") : text.index("### 6.3")]
    out: set[tuple[Status, Status]] = set()
    for line in section.splitlines():
        if not line.startswith("| `") and not line.startswith("| any open"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        targets = [Status(t) for t in re.findall(r"`([a-z_]+)`", cells[1])]
        sources = (
            OPEN
            if cells[0].startswith("any open")
            else [Status(f) for f in re.findall(r"`([a-z_]+)`", cells[0])]
        )
        out |= {(f, t) for f in sources for t in targets}
    return out


def test_every_status_has_a_row() -> None:
    assert set(sm.TRANSITIONS) == set(Status)
    assert set(Status) == OPEN | TERMINAL and not OPEN & TERMINAL


def test_edges_match_spec() -> None:
    ours = set(sm.edges())
    spec = spec_edges()
    assert spec, "no edges parsed from SPEC §6.2"
    assert ours - spec == set(), f"in code, not in SPEC: {sorted(ours - spec)}"
    assert spec - ours == set(), f"in SPEC, not in code: {sorted(spec - ours)}"


def test_terminal_exits_are_only_undo_and_fix() -> None:
    exits = {(f, t) for f in TERMINAL for t in sm.TRANSITIONS[f]}
    assert exits == {(S.EXECUTED, S.UNDOING), (S.UNDONE, S.PROPOSED)}


def _reachable(start: Status) -> set[Status]:
    seen, queue = {start}, deque([start])
    while queue:
        for nxt in sm.TRANSITIONS[queue.popleft()]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def test_everything_reachable_from_new() -> None:
    assert _reachable(S.NEW) == set(Status)


@pytest.mark.parametrize("status", sorted(OPEN))
def test_every_open_status_can_finish(status: Status) -> None:
    assert _reachable(status) & TERMINAL


def test_non_edge_is_a_conflict() -> None:
    with pytest.raises(ConflictError):
        sm.check_transition(S.NEW, S.EXECUTED, TransitionContext())


@pytest.mark.parametrize(
    ("frm", "to", "refused", "passes"),
    [
        (
            S.HELD,
            S.PROPOSED,
            TransitionContext(stage=Stage.ASSIST),
            TransitionContext(stage=Stage.LIVE),
        ),
        (S.AWAITING_APPROVAL, S.APPROVED, TransitionContext(), TransitionContext(reversible=True)),
        (S.APPROVED, S.EXECUTING, TransitionContext(send_on_high=True), TransitionContext()),
        (S.APPROVED, S.DELAYED, TransitionContext(), TransitionContext(send_on_high=True)),
        (
            S.AWAITING_STEPUP,
            S.CLARIFIED,
            TransitionContext(origin=Origin.ANSWER),
            TransitionContext(origin=Origin.ANSWER, stepup_verified=True),
        ),
        (
            S.AWAITING_STEPUP,
            S.APPROVED,
            TransitionContext(origin=Origin.ANSWER, stepup_verified=True),
            TransitionContext(origin=Origin.APPROVAL, stepup_verified=True),
        ),
        (
            S.EXPIRED,
            S.NEEDS_CLARIFICATION,
            TransitionContext(),
            TransitionContext(origin=Origin.ANSWER),
        ),
        (
            S.NEEDS_CLARIFICATION,
            S.CLARIFIED,
            TransitionContext(payment_or_fraud=True),
            TransitionContext(),
        ),
        (
            S.NEEDS_CLARIFICATION,
            S.NEEDS_HUMAN,
            TransitionContext(clarification_rounds=1),
            TransitionContext(expiry_count=2),
        ),
        (
            S.CLARIFIED,
            S.PROPOSED,
            TransitionContext(clarification_rounds=3),
            TransitionContext(clarification_rounds=2),
        ),
        (S.FAILED, S.EXECUTING, TransitionContext(), TransitionContext(requeue=True)),
        (S.EXECUTED, S.UNDOING, TransitionContext(), TransitionContext(reversible=True)),
        (
            S.PROPOSED,
            S.RESOLVED_MANUAL,
            TransitionContext(payment_or_fraud=True),
            TransitionContext(payment_or_fraud=True, stepup_verified=True),
        ),
    ],
)
def test_guards(
    frm: Status, to: Status, refused: TransitionContext, passes: TransitionContext
) -> None:
    with pytest.raises(PolicyDeniedError):
        sm.check_transition(frm, to, refused)
    sm.check_transition(frm, to, passes)


contexts = st.builds(
    TransitionContext,
    stage=st.sampled_from(Stage),
    origin=st.sampled_from(Origin),
    payment_or_fraud=st.booleans(),
    send_on_high=st.booleans(),
    reversible=st.booleans(),
    fix=st.booleans(),
    requeue=st.booleans(),
    stepup_verified=st.booleans(),
    clarification_rounds=st.integers(0, 4),
    expiry_count=st.integers(0, 3),
)


class ItemWalk(RuleBasedStateMachine):
    """Random walks: every move either follows the table and its guard or is refused."""

    def __init__(self) -> None:
        super().__init__()
        self.status = S.NEW

    @rule(to=st.sampled_from(Status), ctx=contexts)
    def move(self, to: Status, ctx: TransitionContext) -> None:
        frm = self.status
        try:
            sm.check_transition(frm, to, ctx)
        except ConflictError:
            assert not sm.allowed(frm, to)
            return
        except PolicyDeniedError:
            assert (frm, to) in sm.GUARDS
            return
        assert sm.allowed(frm, to)
        # safety properties
        if (frm, to) == (S.HELD, S.PROPOSED):
            assert ctx.stage is Stage.LIVE
        if (frm, to) == (S.APPROVED, S.EXECUTING):
            assert not ctx.send_on_high
        if frm is S.AWAITING_APPROVAL and to is S.APPROVED:
            assert ctx.reversible or ctx.stepup_verified
        if frm is S.AWAITING_STEPUP and to in (S.APPROVED, S.CLARIFIED):
            assert ctx.stepup_verified
        if (frm, to) == (S.CLARIFIED, S.PROPOSED):
            assert ctx.clarification_rounds <= sm.MAX_CLARIFICATION_ROUNDS
        if to is S.RESOLVED_MANUAL and ctx.payment_or_fraud:
            assert ctx.stepup_verified
        self.status = to

    @invariant()
    def status_is_known(self) -> None:
        assert self.status in Status


def test_random_walks() -> None:
    run_state_machine_as_test(
        ItemWalk, settings=settings(max_examples=300, stateful_step_count=40, deadline=None)
    )
