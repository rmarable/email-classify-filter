"""Drafts and sends in the actors' vocabulary (V1.5 step 2b; OD-317): what each email is offered,
the draft text field, and Claude's `propose_action` checks."""

from __future__ import annotations

import json
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


def test_sends_need_their_targets_and_aren_t_offered_where_policy_would_reject_them() -> None:
    assert "reply_template" not in actor.allowed(REPLY)  # no templates enabled
    full = actor.allowed(REPLY, templates=T, forwards=F)
    assert {"reply_template", "forward_internal"} <= set(full)
    no_sends = actor.allowed(REPLY, templates=T, forwards=F, sends=False)
    assert "reply_template" not in no_sends and "draft_reply" in no_sends
    assert "reply_template" not in actor.allowed(ROUTINE, templates=T)  # not a reply
    assert "forward_internal" in actor.allowed(ROUTINE, forwards=F)


def test_the_schema_lists_template_and_forward_targets_only_when_offered() -> None:
    acts = actor.allowed(REPLY, templates=T, forwards=F)
    schema = actor.output_schema(LABELS, frozenset(), acts, templates=T, forwards=F)
    assert set(schema["properties"]["target"]["enum"]) == {"", "invoice", "ack", "ap_lead"}
    assert schema["required"] == ["action", "target", "reason", "text"]
    plain = actor.output_schema(LABELS, frozenset(), actor.allowed(REPLY), templates=T)
    assert "ack" not in plain["properties"]["target"]["enum"]


def _parse(d: dict[str, Any]) -> dict[str, str] | None:
    acts = actor.allowed(REPLY, templates=T, forwards=F)
    return actor.parse(json.dumps(d), LABELS, frozenset(), acts, templates=T, forwards=F)


def test_parse_keeps_draft_text_and_send_targets() -> None:
    got = _parse({"action": "draft_reply", "target": "", "reason": "r", "text": "Hello,\nThanks."})
    assert got is not None and got["text"] == "Hello,\nThanks."
    got = _parse({"action": "reply_template", "target": "ack", "reason": "r", "text": ""})
    assert got is not None and got["target"] == "ack" and "text" not in got
    got = _parse({"action": "flag", "target": "", "reason": "r", "text": "ignored"})
    assert got is not None and "text" not in got


@pytest.mark.parametrize(
    "d",
    [
        {"action": "draft_reply", "target": "", "reason": "r", "text": "  "},
        {"action": "reply_template", "target": "nope", "reason": "r", "text": ""},
        {"action": "reply_template", "target": "ap_lead", "reason": "r", "text": ""},
        {"action": "forward_internal", "target": "ack", "reason": "r", "text": ""},
        {"action": "draft_reply", "target": "", "reason": "r", "text": 5},
    ],
)
def test_parse_refuses_bad_drafts_and_targets(d: dict[str, Any]) -> None:
    assert _parse(d) is None


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
