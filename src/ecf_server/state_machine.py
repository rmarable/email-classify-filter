"""The item state machine (SPEC §6.2).

The transition table and the guard table are data. `check_transition` is the pure rule;
`transition()` (added with the SQLite state module in step 5) is the only writer of an item's
status and calls it first.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from ecf.errors import ConflictError, PolicyDeniedError
from ecf.status import OPEN, Status

S = Status
MAX_CLARIFICATION_ROUNDS = 2


class Stage(StrEnum):
    SHADOW = "shadow"
    ASSIST = "assist"
    LIVE = "live"


class Origin(StrEnum):
    """Why an item is waiting at awaiting_stepup or expired: an approval or a risky answer."""

    APPROVAL = "approval"
    ANSWER = "answer"


@dataclass(frozen=True)
class TransitionContext:
    """Facts a guard may need. Everything defaults to the most restrictive value."""

    stage: Stage = Stage.SHADOW
    origin: Origin = Origin.APPROVAL
    payment_or_fraud: bool = False
    send_on_high: bool = False
    reversible: bool = False
    assist_safe: bool = False  # label, flag, escalate or leave: allowed to run in assist stage
    fix: bool = False
    requeue: bool = False
    stepup_verified: bool = False
    clarification_rounds: int = 0
    expiry_count: int = 0


Guard = Callable[[TransitionContext], bool]


def _live(c: TransitionContext) -> bool:
    return c.stage is Stage.LIVE


def _approval(c: TransitionContext) -> bool:
    return c.origin is Origin.APPROVAL


def _answer(c: TransitionContext) -> bool:
    return c.origin is Origin.ANSWER


_TABLE: dict[Status, frozenset[Status]] = {
    S.NEW: frozenset({S.CLASSIFIED, S.AWAITING_CLAUDE}),
    S.CLASSIFIED: frozenset({S.AWAITING_CLAUDE, S.PROPOSED}),
    S.AWAITING_CLAUDE: frozenset({S.CLASSIFIED, S.PROPOSED}),
    S.PROPOSED: frozenset(
        {S.EXECUTING, S.AWAITING_APPROVAL, S.NEEDS_CLARIFICATION, S.OBSERVED, S.HELD}
    ),
    S.HELD: frozenset({S.PROPOSED}),
    S.AWAITING_APPROVAL: frozenset(
        {S.APPROVED, S.AWAITING_STEPUP, S.REJECTED, S.EXPIRED, S.PROPOSED}
    ),
    S.AWAITING_STEPUP: frozenset({S.APPROVED, S.CLARIFIED, S.EXPIRED}),
    S.APPROVED: frozenset({S.EXECUTING, S.DELAYED}),
    S.DELAYED: frozenset({S.EXECUTING, S.CANCELLED}),
    S.EXPIRED: frozenset({S.AWAITING_APPROVAL, S.NEEDS_CLARIFICATION}),
    S.NEEDS_CLARIFICATION: frozenset({S.CLARIFIED, S.AWAITING_STEPUP, S.NEEDS_HUMAN}),
    S.CLARIFIED: frozenset({S.PROPOSED}),
    S.NEEDS_HUMAN: frozenset({S.PROPOSED}),
    S.EXECUTING: frozenset({S.EXECUTED, S.FAILED, S.FAILED_UNKNOWN, S.EXECUTING}),
    S.FAILED: frozenset({S.EXECUTING}),
    S.FAILED_UNKNOWN: frozenset({S.EXECUTING}),
    S.EXECUTED: frozenset({S.UNDOING}),
    S.UNDOING: frozenset({S.UNDONE, S.UNDO_FAILED}),
    S.UNDONE: frozenset({S.PROPOSED}),
}
# Any open status may be resolved by a person, or closed because the mail was handled in the client.
for _s in OPEN:
    _TABLE[_s] = _TABLE.get(_s, frozenset()) | {S.RESOLVED_MANUAL, S.RESOLVED_BY_MAILBOX}

TRANSITIONS: Mapping[Status, frozenset[Status]] = {s: _TABLE.get(s, frozenset()) for s in Status}


def _rounds_open(c: TransitionContext) -> bool:
    """A clarification round may still be answered (rounds count questions asked and expired
    answers; SPEC §6.2: at most 2 rounds)."""
    return c.clarification_rounds <= MAX_CLARIFICATION_ROUNDS


GUARDS: Mapping[tuple[Status, Status], Guard] = {
    (S.PROPOSED, S.EXECUTING): lambda c: _live(c) or (c.stage is Stage.ASSIST and c.assist_safe),
    (S.PROPOSED, S.AWAITING_APPROVAL): _live,
    (S.PROPOSED, S.OBSERVED): lambda c: c.stage is Stage.SHADOW,
    (S.PROPOSED, S.HELD): lambda c: c.stage is Stage.ASSIST,
    (S.HELD, S.PROPOSED): _live,
    (S.AWAITING_APPROVAL, S.APPROVED): lambda c: c.reversible,
    (S.AWAITING_APPROVAL, S.PROPOSED): lambda c: c.fix,
    (S.AWAITING_STEPUP, S.APPROVED): lambda c: _approval(c) and c.stepup_verified,
    (S.AWAITING_STEPUP, S.CLARIFIED): lambda c: _answer(c) and c.stepup_verified,
    (S.APPROVED, S.EXECUTING): lambda c: not c.send_on_high,
    (S.APPROVED, S.DELAYED): lambda c: c.send_on_high,
    (S.EXPIRED, S.AWAITING_APPROVAL): _approval,
    (S.EXPIRED, S.NEEDS_CLARIFICATION): _answer,
    (S.NEEDS_CLARIFICATION, S.CLARIFIED): lambda c: not c.payment_or_fraud and _rounds_open(c),
    (S.NEEDS_CLARIFICATION, S.AWAITING_STEPUP): lambda c: c.payment_or_fraud and _rounds_open(c),
    # a third round (a third question, or an answer expiring in the second round) goes to a person
    (S.NEEDS_CLARIFICATION, S.NEEDS_HUMAN): lambda c: not _rounds_open(c),
    (S.CLARIFIED, S.PROPOSED): _rounds_open,
    (S.EXECUTING, S.EXECUTING): lambda c: c.requeue,
    (S.FAILED, S.EXECUTING): lambda c: c.requeue,
    (S.FAILED_UNKNOWN, S.EXECUTING): lambda c: c.requeue,
    (S.EXECUTED, S.UNDOING): lambda c: c.reversible,
    (S.UNDONE, S.PROPOSED): lambda c: c.fix,
    **{
        (s, S.RESOLVED_MANUAL): (lambda c: not c.payment_or_fraud or c.stepup_verified)
        for s in OPEN
    },
}


def allowed(frm: Status, to: Status) -> bool:
    """Whether the table has the edge at all (ignoring guards)."""
    return to in TRANSITIONS[frm]


def check_transition(frm: Status, to: Status, ctx: TransitionContext) -> None:
    """Raise ConflictError for a non-edge, PolicyDeniedError when the edge's guard refuses."""
    if not allowed(frm, to):
        raise ConflictError(f"no transition {frm} -> {to}", from_status=str(frm), to_status=str(to))
    guard = GUARDS.get((frm, to))
    if guard is not None and not guard(ctx):
        raise PolicyDeniedError(
            f"guard refused {frm} -> {to}", from_status=str(frm), to_status=str(to)
        )


def edges() -> list[tuple[Status, Status]]:
    return [(f, t) for f in Status for t in sorted(TRANSITIONS[f])]
