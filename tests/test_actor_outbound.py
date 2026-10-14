"""Drafts and sends in the actor's vocabulary (V1.5 step 2b; OD-317): what each email is offered,
the draft text field, and Claude's `propose_action` checks (the local actor's output schema and
parser went in v3.0.0, ADR 0031)."""

from __future__ import annotations

from typing import Any

import pytest

from ecf_server import actor, claude_review

REPLY = {"category": "invoice", "requires_reply": True, "requires_action": True}
ROUTINE = {"category": "marketing", "requires_reply": False, "requires_action": False}
LABELS = frozenset({"invoice"})
T, F = frozenset({"ack"}), frozenset({"ap_lead"})


def test_drafts_are_offered_only_for_mail_that_needs_a_reply() -> None:
    assert "draft_reply" in actor.allowed(REPLY)
    assert "draft_reply" not in actor.allowed(ROUTINE)
    assert actor.allowed(REPLY)[-1] == "needs_clarification"


def test_sends_need_their_targets() -> None:
    assert "reply_template" not in actor.allowed(REPLY)  # no templates enabled
    full = actor.allowed(REPLY, templates=T, forwards=F)
    assert {"reply_template", "forward_internal"} <= set(full)
    assert "reply_template" not in actor.allowed(ROUTINE, templates=T)  # not a reply
    assert "forward_internal" in actor.allowed(ROUTINE, forwards=F)


def _parse(d: dict[str, Any]) -> str | None:
    return _check(**d)


@pytest.mark.parametrize(
    "d",
    [
        {"action": "draft_reply", "target": "", "reason": "r", "text": "  "},
        {"action": "reply_template", "target": "nope", "reason": "r"},
        {"action": "reply_template", "target": "ap_lead", "reason": "r"},
        {"action": "forward_internal", "target": "ack", "reason": "r"},
        {"action": "draft_reply", "target": "", "reason": "r", "text": 5},
    ],
)
def test_bad_drafts_and_targets_are_refused(d: dict[str, Any]) -> None:
    assert _parse(d) is not None


def test_drafts_and_send_targets_pass() -> None:
    assert _parse({"action": "draft_reply", "reason": "r", "text": "Hello,\nThanks."}) is None
    assert _parse({"action": "reply_template", "target": "ack", "reason": "r"}) is None
    assert _parse({"action": "forward_internal", "target": "ap_lead", "reason": "r"}) is None


def _check(**kw: Any) -> str | None:
    proposal = {"action": "flag", "target": "", "reason": "r", "question": None} | kw
    return claude_review.check_proposal(proposal, LABELS, frozenset(),
                                        actor.allowed(REPLY, templates=T, forwards=F),
                                        templates=T, forwards=F)  # fmt: skip


def test_claude_text_goes_only_with_a_draft() -> None:
    assert _check(action="draft_reply", text="Hello") is None
    assert _check(action="draft_reply") == "draft_reply needs the reply in text"
    assert _check(action="flag", text="Hello") == "text goes only with draft_reply"
    assert _check(action="reply_template", target="ack") is None
    assert "template" in str(_check(action="reply_template", target="other"))
