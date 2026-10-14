"""The actor's checks and decision (SPEC §5.1 step 7, §8.1, §8.2; V1.3 step 4c), which Claude's
proposals go through (`problem`, `offered`, `decide_one`; claude_review.py, claude_eval.py). The
local actor (Gemma 4 12B through Ollama) was removed in v3.0.0 (ADR 0031; R-D14 keeps the module
name).

- **Actions:** one of label, flag, escalate, leave, mark_read, archive, move, junk or
  `needs_clarification`; `target` one of the known label names or `move_folders`, or empty.
- **Drafts and sends** (V1.5, OD-317): `draft_reply` is offered when the email needs a reply;
  `reply_template` too when a template is enabled, and `forward_internal` when the forward
  allow-list has entries (targets: the template ids and entry ids). Policy checks them again
  (§8.4).
- **No hiding mail that needs someone** (operator decision 2026-10-01, OD-250): when the
  classification says the email needs action or a reply, the hide actions (mark_read, archive,
  move, junk) aren't offered and `problem` refuses them, so text in the email can't talk the
  actor into hiding it. Mail the classifier calls routine still gets them, and policy's I1 check.
- **Then the policy** (policy.proposal): the proposed action gets the same checks as a rule's (I1:
  a hide still needs corroboration; I4: targets), and joins the rule's actions.
- **A question** goes to you with an Answer button (V1.2's machinery, at most two rounds).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ecf.ids import StableId
from ecf_server import answers, decide, items, outbound_plan, policy
from ecf_server.clock import Clock
from ecf_server.policy import Dropped, Planned
from ecf_server.rules import HIDE_ACTIONS
from ecf_server.state_machine import Stage, Status, TransitionContext

ACTIONS = ("label", "flag", "escalate", "leave", "mark_read", "archive", "move", "junk",
           "needs_clarification")  # fmt: skip
OUTBOUND = ("draft_reply", "reply_template", "forward_internal")  # offered by `allowed` (V1.5)
REASON_MAX = 300


def allowed(
    classification: dict[str, Any],
    *,
    templates: frozenset[str] = frozenset(),
    forwards: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """The actions the actor may propose for this email (OD-250; drafts and sends, OD-317)."""
    reply = bool(classification.get("requires_reply"))
    base = list(ACTIONS)
    if reply or classification.get("requires_action"):
        base = [a for a in base if a not in HIDE_ACTIONS]
    extra = [a for a, on in (("draft_reply", reply),
                             ("reply_template", reply and bool(templates)),
                             ("forward_internal", bool(forwards))) if on]  # fmt: skip
    return (*base[:-1], *extra, base[-1])  # needs_clarification stays last


def _targets(labels: frozenset[str], folders: frozenset[str], actions: tuple[str, ...],
             templates: frozenset[str], forwards: frozenset[str]) -> frozenset[str]:  # fmt: skip
    out = set(labels)
    if "move" in actions:
        out |= folders
    if "reply_template" in actions:
        out |= templates
    if "forward_internal" in actions:
        out |= forwards
    return frozenset(out)


TARGETED = ("label", "move", "reply_template", "forward_internal")


def problem(action: Any, target: Any, reason: Any, labels: frozenset[str],  # noqa: PLR0913 - one reason per check
            folders: frozenset[str], actions: tuple[str, ...] = ACTIONS, *,
            templates: frozenset[str] = frozenset(), forwards: frozenset[str] = frozenset(),
            text: Any = "") -> str | None:  # fmt: skip
    """Why a proposal is refused, or None (Claude's `propose_action`; before v3.0.0 the local
    actor's reply too). The message names no model text. A target on an action that takes none
    is dropped by the caller, never passed on."""
    if not (isinstance(action, str) and isinstance(target, str) and isinstance(reason, str)):
        return "action, target and reason must be text"
    if not isinstance(text, str):
        return "text must be text"
    if action not in actions:
        return ("action isn't one of " + ", ".join(actions)
                + (" (hiding isn't available: the email needs action or a reply)"
                   if not set(HIDE_ACTIONS) <= set(actions) else ""))  # fmt: skip
    if target and target not in _targets(labels, folders, actions, templates, forwards):
        return "target isn't a known label, move folder, template or forward entry"
    checks = [
        (action == "label" and target not in labels, "label needs a known label name as target"),
        (action == "move" and target not in folders,
         "move needs a folder from move_folders as target"),
        (action == "reply_template" and target not in templates,
         "reply_template needs an enabled template id as target"),
        (action == "forward_internal" and target not in forwards,
         "forward_internal needs a forward allow-list id as target"),
        (action == "draft_reply" and not text.strip(), "draft_reply needs the reply in text"),
    ]  # fmt: skip
    return next((why for applies, why in checks if applies), None)


def offered(ctx: policy.Context) -> tuple[str, ...]:
    """The actions this item's actor is offered."""
    return allowed(ctx.classification, templates=ctx.templates, forwards=ctx.forwards)


def decide_one(conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, ctx: policy.Context,
            p: policy.Plan, got: dict[str, str], labels: frozenset[str]) -> None:  # fmt: skip
    """Apply one proposal: Claude's (V1.4 step 3: `got` may also carry `question`, `model` and
    `agent`; a question goes to you on any item, §8.2)."""
    sid = item["stable_id"]
    p.to_actor = False
    p.actor = {"action": got["action"], "target": got["target"] or None, "reason": got["reason"],
               **{k: got[k] for k in ("model", "agent") if k in got}}  # fmt: skip
    if got["action"] == "needs_clarification":
        decide.record(conn, clock, sid, p, "actor")  # what the actor decided stays with the item
        _to_proposed(conn, clock, item)
        answers.ask(conn, clock, sid, got.get("question") or got["reason"])
        return
    one = policy.proposal(ctx, p, got["action"], got["target"] or None, labels)
    if isinstance(one, Planned) and one.name in outbound_plan.OUTBOUND:
        try:  # the recipient and text the approval will cover (OD-317)
            payload = outbound_plan.resolve(conn, item, one.name, one.target, got.get("text"))
            one = Planned(one.name, one.target, one.mode, payload)
        except outbound_plan.Unresolved as exc:
            one = Dropped(one.name, one.target, str(exc.detail))
    if isinstance(one, Dropped):
        p.dropped.append(one)
    else:
        _add(p, one)
    decide.apply(conn, clock, sid, p, source="actor")


def _add(p: policy.Plan, a: Planned) -> None:
    if all((x.name, x.target) != (a.name, a.target) for x in p.actions):
        p.actions.append(a)


def _to_proposed(conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row) -> None:
    stage = conn.execute("SELECT stage FROM addresses WHERE address_id = ?",
                         (item["address_id"],)).fetchone()["stage"]  # fmt: skip
    items.transition(conn, clock, StableId(item["stable_id"]), Status.PROPOSED,
                     TransitionContext(stage=Stage(stage)), actor="actor")  # fmt: skip
