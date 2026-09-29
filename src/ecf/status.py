"""Item statuses (SPEC §6.2). Shared so clients can display them; only the service changes them."""

from __future__ import annotations

from enum import StrEnum


class Status(StrEnum):
    NEW = "new"
    CLASSIFIED = "classified"
    AWAITING_CLAUDE = "awaiting_claude"
    PROPOSED = "proposed"
    HELD = "held"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_STEPUP = "awaiting_stepup"
    APPROVED = "approved"
    DELAYED = "delayed"
    EXECUTING = "executing"
    FAILED = "failed"
    FAILED_UNKNOWN = "failed_unknown"
    EXPIRED = "expired"
    NEEDS_CLARIFICATION = "needs_clarification"
    CLARIFIED = "clarified"
    NEEDS_HUMAN = "needs_human"
    UNDOING = "undoing"
    UNDO_FAILED = "undo_failed"
    # terminal
    OBSERVED = "observed"
    EXECUTED = "executed"
    UNDONE = "undone"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    RESOLVED_MANUAL = "resolved_manual"
    RESOLVED_BY_MAILBOX = "resolved_by_mailbox"
    # [M3] answer_proposed is added with the remote MCP.


TERMINAL: frozenset[Status] = frozenset(
    {
        Status.OBSERVED,
        Status.EXECUTED,
        Status.UNDONE,
        Status.REJECTED,
        Status.CANCELLED,
        Status.RESOLVED_MANUAL,
        Status.RESOLVED_BY_MAILBOX,
    }
)
OPEN: frozenset[Status] = frozenset(Status) - TERMINAL

# Check outcomes that are failures (shown with their error by `ecf status`, `ecf check`,
# `ecf doctor`; audited as check.failed). `secret_unavailable` and `internal_error` were added
# after the V1.1 review (2026-09-29).
CHECK_FAILED = frozenset(
    {"error", "login_rejected", "lease_lost", "secret_unavailable", "internal_error"}
)
