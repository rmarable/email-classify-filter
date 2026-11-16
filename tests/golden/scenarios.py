"""Golden scenarios (planner step 2, D116). Each drives v1's engine through its entry functions,
called as their callers call them; setup builds the starting state through v1's own writers. The
edge table (test_edge_table.py) checks which state-machine edges the recorded traces cover."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from unittest import mock

from ecf.errors import StepupRequiredError
from ecf.ids import StableId
from ecf.status import OPEN, Status
from ecf_server import (
    actor,
    answers,
    approvals,
    cards,
    classifier,
    claude_pins,
    claude_queue,
    claude_review,
    config,
    decide,
    digest_actions,
    digests,
    escalations,
    execute,
    gate,
    inbox,
    items,
    mailbox_actions,
    outbound,
    scans,
    slack_admin,
    stages,
)
from ecf_server.actions import Planned
from ecf_server.db import write_tx
from ecf_server.state_machine import Origin, Stage, TransitionContext
from ecf_server.telemetry import Hold, Seen, Telemetry
from tests.golden import world as W
from tests.golden.harness import Event, Scenario, World, check, tick, ts

SCENARIOS: list[Scenario] = []
ARCHIVE = [Planned("archive")]
SEND = [Planned("reply_template", "ack")]
QUESTION = "Which cost center?"


def scenario(
    name: str, doc: str, setup: Callable[[World], Any], *events: Event, mail: bool = False
) -> None:
    assert all(x.name != name for x in SCENARIOS), name
    SCENARIOS.append(Scenario(name, doc, setup, tuple(events), mail=mail))


def a(w: World) -> StableId:
    return StableId(w.vars["a"])


def b(w: World) -> StableId:
    return StableId(w.vars["b"])


def apply_a(w: World) -> Any:
    return decide.apply(w.conn, w.clock, a(w))


def approve_cli(ref: Any = a, **kw: Any) -> Callable[[World], Any]:
    return lambda w: approvals.approve(
        w.conn, w.clock, w.notifier, ref(w), actor="os_user", nonce=w.vars.get("nonce"), **kw
    )


def run_actions(fail: int = 0) -> Callable[[World], Any]:
    """execute.run_once with a stand-in executor (fresh per run), for failures the fake mailbox
    can't make."""
    return lambda w: execute.run_once(w.conn, w.clock, W.FakeExecutor(fail))


# decide.apply by stage, and the rules' own runs (decide.py).


def _ap(
    stage: str, cls: dict[str, Any], facts: dict[str, Any], *, cursor: bool = True, **kw: Any
) -> Any:
    def setup(w: World) -> None:
        W.address(w, stage=stage, **kw)
        w.vars["a"] = W.item(w, 0, classification=cls, facts=facts)
        if cursor and w.src is not None:
            W.cursor(w)

    return setup


PLAIN: dict[str, Any] = {"triggers": {}, "auth_result": "pass", "sender_seen_before": True}

scenario(
    "live_archive",
    "Live, known bulk: the rules archive at once; the check runs it.",
    _ap("live", W.MARKETING, W.KNOWN_BULK),
    ("decide.apply", apply_a),
    ("check", check),
    ("tick", tick),
    mail=True,
)
scenario(
    "live_invoice_label",
    "Live, an invoice: label and leave, run by the check.",
    _ap("live", W.INVOICE, PLAIN),
    ("decide.apply", apply_a),
    ("check", check),
    mail=True,
)
scenario(
    "live_fraud_flag",
    "Live, fraud trigger: label suspicious, flag, escalate.",
    _ap("live", W.INVOICE, W.FRAUD),
    ("decide.apply", apply_a),
    ("check", check),
    ("escalations.sweep", lambda w: escalations.sweep(w.conn, w.clock)),
    mail=True,
)
scenario(
    "shadow_observe",
    "Shadow: the plan is recorded, nothing runs (proposed->observed).",
    _ap("shadow", W.MARKETING, W.KNOWN_BULK),
    ("decide.apply", apply_a),
    ("tick", tick),
)
scenario(
    "assist_hold",
    "Assist, a move: held (proposed->held).",
    _ap("assist", W.MARKETING, W.KNOWN_BULK),
    ("decide.apply", apply_a),
    ("tick", tick),
)
scenario(
    "assist_safe_runs",
    "Assist, label only: runs (proposed->executing, assist+safe).",
    _ap("assist", W.INVOICE, PLAIN),
    ("decide.apply", apply_a),
    ("check", check),
    mail=True,
)
scenario(
    "to_actor",
    "Live, needs a reply: handed to Claude (classified->awaiting_claude).",
    _ap("live", W.REPLY, PLAIN),
    ("decide.apply", apply_a),
    ("tick", tick),
)


def _sweep_setup(w: World) -> None:
    W.address(w)
    w.vars["a"] = W.item(w, 0, classification=W.MARKETING, facts=W.KNOWN_BULK)  # no proposal yet
    w.vars["b"] = W.item(w, 1, classification=W.REPLY, facts=PLAIN)


scenario(
    "decide_sweep",
    "A tick applies classified items a crash left without a plan.",
    _sweep_setup,
    ("tick", tick),
)


# The Claude queue and the classifier (claude_queue.py, classifier.py, actor.py).


def _new_c(w: World) -> None:
    W.address(w)
    w.vars["a"] = W.item(w, 0, classification=W.REPLY, facts=PLAIN, to=None)
    with write_tx(w.conn):  # the pre-check ran; Claude hasn't classified it
        w.conn.execute("UPDATE items SET classification = NULL, prechecked = 1")


def _lost_move(*_args: object) -> Status:
    return Status.CLASSIFIED


def _queue_sweep_setup(w: World) -> None:
    """a: prechecked new mail never routed; b: classified for the actor, the move lost (the
    crash between decide's record and to_actor; test_claude_queue patches the same call)."""
    _new_c(w)
    w.vars["b"] = W.item(w, 1, classification=W.REPLY, facts=PLAIN)
    with mock.patch.object(decide.claude_queue, "to_actor", _lost_move):
        decide.apply(w.conn, w.clock, b(w))


def _store(cls: dict[str, Any]) -> Callable[[World], Any]:
    return lambda w: classifier.store(
        w.conn, w.clock, a(w), cls, {"classifier": "claude-test"}, expected=Status.AWAITING_CLAUDE
    )


scenario(
    "route_new_then_store",
    "Preset C: new->awaiting_claude, Claude classifies"
    " (awaiting_claude->classified), the rules hand it back to the actor.",
    _new_c,
    ("claude_queue.route_new", lambda w: claude_queue.route_new(w.conn, w.clock, a(w))),
    ("classifier.store", _store(W.REPLY)),
)
scenario(
    "route_new_store_rules",
    "Claude classifies bulk mail; the rules archive it.",
    _new_c,
    ("claude_queue.route_new", lambda w: claude_queue.route_new(w.conn, w.clock, a(w))),
    ("classifier.store", _store(W.MARKETING)),
)
scenario(
    "claude_queue_sweep",
    "A tick moves prechecked new mail and a classified item the"
    " actor never got to Claude after a minute.",
    _queue_sweep_setup,
    ("advance 2 min", W.advance(120)),
    ("tick", tick),
)


def _decide_one(got: dict[str, str]) -> Callable[[World], Any]:
    def run(w: World) -> Any:
        item = w.conn.execute("SELECT * FROM items WHERE stable_id = ?", (a(w),)).fetchone()
        ctx, p = decide.plan_for(w.conn, item)
        return actor.decide_one(w.conn, w.clock, item, ctx, p, got, frozenset())

    return run


ASK = {"action": "needs_clarification", "target": "", "reason": "r", "question": QUESTION}


def _awaiting_claude(w: World) -> None:
    _ap("live", W.REPLY, PLAIN)(w)
    decide.apply(w.conn, w.clock, a(w))  # -> awaiting_claude


scenario(
    "actor_proposes_label",
    "Claude's proposal on an awaiting item (awaiting_claude->proposed).",
    _awaiting_claude,
    ("actor.decide_one", _decide_one({"action": "leave", "target": "", "reason": "r"})),
    ("tick", tick),
)
scenario(
    "actor_asks_then_answer",
    "Claude asks; you answer; Claude proposes"
    " (awaiting_claude->proposed->needs_clarification->clarified->proposed).",
    _awaiting_claude,
    ("actor.decide_one ask", _decide_one(ASK)),
    ("answers.answer", lambda w: answers.answer(w.conn, w.clock, a(w), "CC-7", actor="os_user")),
    ("actor.decide_one", _decide_one({"action": "leave", "target": "", "reason": "r"})),
)


# Approvals (approvals.py): request, approve, reject, expire, delay, cancel, requeue.


def _waiting(*, facts: dict[str, Any] | None = None, mail: bool = False) -> Any:
    """a: a proposed item; the `request` event asks for approval of an archive."""

    def setup(w: World) -> None:
        W.address(w)
        w.vars["a"] = W.proposed(w, 0, facts=facts if facts is not None else PLAIN)
        if mail:
            W.cursor(w)

    return setup


def request(w: World) -> Any:
    return approvals.request(w.conn, w.clock, a(w), ARCHIVE)


def grant(w: World) -> str:
    return W.grant_of(w, a(w))


def _approve(w: World, s: str) -> Any:
    """Setup only: approve from the CLI, with step-up when asked."""
    try:
        return approvals.approve(w.conn, w.clock, w.notifier, s, actor="os_user")
    except StepupRequiredError as exc:
        return approvals.approve(
            w.conn, w.clock, w.notifier, s, actor="os_user", nonce=W.nonce(w, exc)
        )


scenario(
    "approve_cli_reversible",
    "request, then one CLI approval of a reversible move; runs.",
    _waiting(mail=True),
    ("approvals.request", request),
    ("approvals.approve", approve_cli()),
    ("check", check),
    mail=True,
)
scenario(
    "approve_slack_reversible",
    "A Slack Approve click on a reversible move.",
    _waiting(),
    ("approvals.request", request),
    ("click approve", W.click(approvals.APPROVE, grant)),
)
scenario(
    "approve_fraud_cli_stepup",
    "Hiding a fraud item: the CLI approval needs step-up"
    " (awaiting_approval->awaiting_stepup->approved).",
    _waiting(facts=W.FRAUD),
    ("approvals.request", request),
    ("approvals.approve (step-up asked)", W.keep(approve_cli())),
    ("stepup", W.stepup_done),
    ("approvals.approve", approve_cli()),
)


def _rules_approve(w: World) -> None:
    """The action policy says archive needs approval (test_digest_actions' setup)."""
    _ap("live", W.MARKETING, W.KNOWN_BULK)(w)
    with write_tx(w.conn):
        w.conn.execute(
            "INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
            " ('config.action_policy', ?, ?, 'test')",
            (json.dumps({"standard": {"archive": "approve"}}), ts(w)),
        )


scenario(
    "rules_need_approval",
    "The policy says archive needs a person: decide.apply requests"
    " it (proposed->awaiting_approval), a tick posts nothing more.",
    _rules_approve,
    ("decide.apply", apply_a),
    ("tick", tick),
)


def _approve_all_setup(w: World) -> None:
    _rules_approve(w)
    decide.apply(w.conn, w.clock, a(w))
    g = W.grant_of(w, a(w))
    with write_tx(w.conn):  # what digests.build stores for its button
        slack_admin.put_setting(
            w.conn, digest_actions.BATCH_KEY + "k1", json.dumps([g]), ts(w), actor="service"
        )


scenario(
    "digest_approve_all",
    "The digest's Approve all on one reversible approval.",
    _approve_all_setup,
    ("click approve_all", W.click(digest_actions.APPROVE_ALL, lambda w: "k1")),
)


def _two_queued(w: World) -> None:
    """Two fraud items whose archive was approved in Slack: both wait for the computer."""
    W.address(w)
    for key, n in (("a", 0), ("b", 1)):
        w.vars[key] = s = W.proposed(w, n, facts=W.FRAUD)
        g = approvals.request(w.conn, w.clock, s, ARCHIVE)
        W.click(approvals.APPROVE, lambda _w, g=g: g)(w)


scenario(
    "approve_pending_batch",
    "Two Slack-queued approvals finished under one step-up (awaiting_stepup->approved).",
    _two_queued,
    (
        "approvals.approve_pending (step-up asked)",
        W.keep(
            lambda w: approvals.approve_pending(
                w.conn, w.clock, w.notifier, [a(w), b(w)], nonce=None
            )
        ),
    ),
    ("stepup", W.stepup_done),
    (
        "approvals.approve_pending",
        lambda w: approvals.approve_pending(
            w.conn, w.clock, w.notifier, [a(w), b(w)], nonce=w.vars["nonce"]
        ),
    ),
)
scenario(
    "reject_cli",
    "Reject from the CLI (awaiting_approval->rejected), then a stale click.",
    _waiting(),
    ("approvals.request", request),
    ("approvals.reject", lambda w: approvals.reject(w.conn, w.clock, a(w), actor="os_user")),
    ("click approve (stale)", W.click(approvals.APPROVE, lambda w: W.grant_of(w, a(w), "voided"))),
)
scenario(
    "expire_reoffer",
    "Expiry: offered again once (awaiting_approval->expired->"
    "awaiting_approval), then left expired; `ecf approve` offers it again (_reoffer).",
    _waiting(),
    ("approvals.request", request),
    ("advance 15 d", W.advance(15 * 86400)),
    ("tick", tick),
    ("advance 15 d", W.advance(15 * 86400)),
    ("tick", tick),
    ("approvals.approve (reoffer)", approve_cli()),
)


def _approved_now(w: World) -> None:
    _waiting()(w)
    approvals.request(w.conn, w.clock, a(w), ARCHIVE)
    _approve(w, a(w))


scenario(
    "fail_then_requeue",
    "Three retryable failures (executing->failed), a requeue (failed->executing), then it runs.",
    _approved_now,
    ("execute.run_once fail", run_actions(1)),
    ("advance 1 h", W.advance(3600)),
    ("execute.run_once fail", run_actions(1)),
    ("advance 1 h", W.advance(3600)),
    ("execute.run_once fail", run_actions(1)),
    (
        "approvals.requeue",
        lambda w: approvals.requeue(w.conn, w.clock, a(w), actor="os_user", nonce=None),
    ),
    ("execute.run_once", run_actions()),
)


def _consumed(w: World) -> None:
    """The executor acted, then the service died before recording it (test_approvals' setup)."""
    _approved_now(w)
    with write_tx(w.conn):
        w.conn.execute(
            "UPDATE grants SET status = 'consumed', consumed_at = ? WHERE status = 'approved'",
            (ts(w),),
        )


scenario(
    "outcome_unknown_requeue",
    "A used grant found at run time (executing->failed_unknown);"
    " requeued (failed_unknown->executing) and run.",
    _consumed,
    ("execute.run_once", run_actions()),
    (
        "approvals.requeue",
        lambda w: approvals.requeue(w.conn, w.clock, a(w), actor="os_user", nonce=None),
    ),
    ("execute.run_once", run_actions()),
)


def _stuck(w: World) -> None:
    """Executing with its job gone dead (a crash state; set by hand)."""
    _approved_now(w)
    with write_tx(w.conn):
        w.conn.execute("UPDATE jobs SET state = 'dead' WHERE queue = 'actions'")


scenario(
    "requeue_stuck",
    "A stuck executing item requeued (executing->executing, requeue).",
    _stuck,
    (
        "approvals.requeue",
        lambda w: approvals.requeue(w.conn, w.clock, a(w), actor="os_user", nonce=None),
    ),
    ("execute.run_once", run_actions()),
)


def _left_live(w: World) -> None:
    _waiting()(w)
    with write_tx(w.conn):  # set_stage moved the address on after decide.apply read it
        w.conn.execute("UPDATE addresses SET stage = 'assist'")


scenario(
    "guard_stage_refuses_request",
    "The address left live: request is refused by the proposed->awaiting_approval guard.",
    _left_live,
    ("approvals.request", request),
)


# Sends: proposed by Claude (the only path that attaches the recipient and text, OD-317),
# approved with step-up, sent by the check, settled from Sent.

REPLY_ACK = {"action": "reply_template", "target": "ack", "reason": "r"}


def _send_ready(
    *,
    sensitivity: str = "standard",
    mail: bool = False,
    n: int = 0,
    key: str = "a",
    facts: dict[str, Any] | None = None,
) -> Any:
    def setup(w: World) -> None:
        if n == 0:
            W.address(w, sensitivity=sensitivity)
            W.sends(w)
        w.vars[key] = W.item(w, n, classification=W.REPLY, facts=facts or W.SEND_FACTS)
        with write_tx(w.conn):
            w.conn.execute(
                "UPDATE items SET sender_name = 'Pat', subject = 'Invoice 42' WHERE stable_id = ?",
                (w.vars[key],),
            )
        if mail and n == 0:
            W.cursor(w)

    return setup


def _send_waiting(**kw: Any) -> Any:
    def setup(w: World) -> None:
        _send_ready(**kw)(w)
        _decide_one(REPLY_ACK)(w)

    return setup


def _send_approved(**kw: Any) -> Any:
    def setup(w: World) -> None:
        _send_waiting(**kw)(w)
        _approve(w, a(w))

    return setup


scenario(
    "send_slack_queued_then_cli",
    "Claude proposes a template reply (decide.apply requests"
    " it); approved in Slack it waits for the computer; the CLI finishes it with step-up;"
    " the check sends it; the next check settles the copy.",
    _send_ready(mail=True),
    ("actor.decide_one reply", _decide_one(REPLY_ACK)),
    ("click approve", W.click(approvals.APPROVE, grant)),
    ("approvals.approve (step-up asked)", W.keep(approve_cli())),
    ("stepup", W.stepup_done),
    ("approvals.approve", approve_cli()),
    ("check", check),
    ("check again", check),
    mail=True,
)
scenario(
    "send_high_delay",
    "A send on a high address: approved->delayed, counted down in"
    " awake time, then executing; the check sends it.",
    _send_waiting(sensitivity="high", mail=True),
    ("approvals.approve (step-up asked)", W.keep(approve_cli())),
    ("stepup", W.stepup_done),
    ("approvals.approve", approve_cli()),
    ("advance_delays 300", lambda w: approvals.advance_delays(w.conn, w.clock, 300, woke=False)),
    ("advance_delays woke", lambda w: approvals.advance_delays(w.conn, w.clock, 0, woke=True)),
    ("advance_delays 301", lambda w: approvals.advance_delays(w.conn, w.clock, 301, woke=False)),
    ("check", check),
    mail=True,
)
scenario(
    "send_high_cancel",
    "Cancel during the delay (delayed->cancelled), then a stale click.",
    _send_approved(sensitivity="high"),
    ("approvals.cancel", lambda w: approvals.cancel(w.conn, w.clock, a(w), actor="os_user")),
    ("click cancel (stale)", W.click(approvals.CANCEL, a)),
)


def _two_sends(w: World) -> None:
    """a: a high send past its countdown (executing); b: one still counting down (delayed)."""
    _send_approved(sensitivity="high")(w)
    approvals.advance_delays(w.conn, w.clock, 601, woke=False)
    _send_ready(n=1, key="b")(w)
    item = w.conn.execute("SELECT * FROM items WHERE stable_id = ?", (b(w),)).fetchone()
    ctx, p = decide.plan_for(w.conn, item)
    actor.decide_one(w.conn, w.clock, item, ctx, p, REPLY_ACK, frozenset())
    _approve(w, b(w))


scenario(
    "outbound_disable",
    "Outbound off with one executing and one delayed send: the delayed"
    " one is cancelled, the executing one's grant voided; its run then fails at consume.",
    _two_sends,
    ("outbound.disable", lambda w: outbound.disable(w.conn, w.clock, "ap", actor="os_user")),
    ("execute.run_once", run_actions()),
    ("tick", tick),
)
scenario(
    "reject_queued_send",
    "A Slack-queued send rejected (awaiting_stepup->rejected).",
    _send_waiting(),
    ("click approve", W.click(approvals.APPROVE, grant)),
    ("click reject", W.click(approvals.REJECT, grant)),
)
scenario(
    "expire_queued_send",
    "A Slack-queued send expires (awaiting_stepup->expired, approval).",
    _send_waiting(),
    ("click approve", W.click(approvals.APPROVE, grant)),
    ("advance 5 d", W.advance(5 * 86400)),
    ("tick", tick),
)
scenario(
    "fail_send_high_requeue",
    "A failed send on a high address requeued with step-up (failed->delayed).",
    _send_approved(sensitivity="high"),
    ("advance_delays 601", lambda w: approvals.advance_delays(w.conn, w.clock, 601, woke=False)),
    ("execute.run_once fail", run_actions(1)),
    ("advance 1 h", W.advance(3600)),
    ("execute.run_once fail", run_actions(1)),
    ("advance 1 h", W.advance(3600)),
    ("execute.run_once fail", run_actions(1)),
    (
        "approvals.requeue (step-up asked)",
        W.keep(lambda w: approvals.requeue(w.conn, w.clock, a(w), actor="os_user", nonce=None)),
    ),
    ("stepup", W.stepup_done),
    (
        "approvals.requeue",
        lambda w: approvals.requeue(w.conn, w.clock, a(w), actor="os_user", nonce=w.vars["nonce"]),
    ),
)


def _sent_unknown(w: World) -> None:
    """A provider known to save sent mail (settle searches Sent only then); SMTP will give no
    final reply."""
    _send_approved(mail=True)(w)
    with write_tx(w.conn):
        w.conn.execute("UPDATE probe SET saves_sent = 1")
    w.smtp.fail = "unknown"


def _copy_to_sent(w: World) -> None:
    """The provider's own copy of the send turns up in Sent."""
    assert w.src is not None
    raw = w.smtp.sent[-1][2]
    w.src.elsewhere.setdefault("Sent", {})[900] = type(next(iter(w.src._msgs.values())))(  # pyright: ignore[reportPrivateUsage]
        raw, W.ARRIVED
    )


scenario(
    "send_unknown_reconciled",
    "SMTP gives no final reply (->failed_unknown); the copy is in"
    " Sent, so settle reconciles it (failed_unknown->executed).",
    _sent_unknown,
    ("check", check),
    ("copy found in Sent", _copy_to_sent),
    ("check", check),
    mail=True,
)
scenario(
    "send_unknown_not_found",
    "No copy in Sent at two settles (failed_unknown->failed).",
    _sent_unknown,
    ("check", check),
    ("check", check),
    ("check", check),
    mail=True,
)


# Answers (answers.py): ask, answer, the third round, the risky answer, its expiry.


def _asked(*, facts: dict[str, Any] | None = None) -> Any:
    def setup(w: World) -> None:
        W.address(w)
        w.vars["a"] = W.proposed(w, 0, facts=facts if facts is not None else PLAIN)

    return setup


def ask(w: World) -> Any:
    return answers.ask(w.conn, w.clock, a(w), QUESTION, member=W.ME)


def answer_cli(text: str | None = "CC-7") -> Callable[[World], Any]:
    return lambda w: answers.answer(
        w.conn, w.clock, a(w), text, actor="os_user", nonce=w.vars.get("nonce")
    )


def to_proposed(w: World) -> Any:
    return items.transition(
        w.conn, w.clock, a(w), Status.PROPOSED, TransitionContext(), actor="test"
    )


scenario(
    "ask_three_rounds",
    "Claude asks three times; the third goes to a person"
    " (clarified->proposed->needs_clarification->needs_human).",
    _awaiting_claude,
    ("actor.decide_one ask", _decide_one(ASK)),
    ("answers.answer", answer_cli()),
    ("actor.decide_one ask", _decide_one(ASK)),
    ("answers.answer", answer_cli()),
    ("actor.decide_one ask", _decide_one(ASK)),
    ("tick", tick),
)
scenario(
    "answer_fraud_slack_then_cli",
    "A fraud item's answer from Slack waits for step-up"
    " (needs_clarification->awaiting_stepup); the CLI finishes it (->clarified).",
    _asked(facts=W.FRAUD),
    ("answers.ask", ask),
    ("form answer", W.click(answers.ANSWER, a, values={answers.ANSWER: "No, never"}, kind="form")),
    ("answers.answer (step-up asked)", W.keep(answer_cli(None))),
    ("stepup", W.stepup_done),
    ("answers.answer", answer_cli(None)),
)
scenario(
    "answer_fraud_cli",
    "A fraud item's answer from the CLI steps up there.",
    _asked(facts=W.FRAUD),
    ("answers.ask", ask),
    ("answers.answer (step-up asked)", W.keep(answer_cli("No"))),
    ("stepup", W.stepup_done),
    ("answers.answer", answer_cli("No")),
)
scenario(
    "answer_expires",
    "A Slack answer waiting 14 days expires (awaiting_stepup->expired->needs_clarification).",
    _asked(facts=W.FRAUD),
    ("answers.ask", ask),
    ("form answer", W.click(answers.ANSWER, a, values={answers.ANSWER: "No"}, kind="form")),
    ("advance 15 d", W.advance(15 * 86400)),
    ("tick", tick),
)


def _round3(w: World) -> None:
    """The crash state between ask's T (round 3 committed) and _to_person (inventory-1b §4, S1)."""
    _asked()(w)
    s = a(w)
    for _ in range(2):
        items.transition(
            w.conn, w.clock, s, Status.NEEDS_CLARIFICATION, TransitionContext(), actor="test"
        )
        items.transition(w.conn, w.clock, s, Status.CLARIFIED, TransitionContext(), actor="test")
        items.transition(w.conn, w.clock, s, Status.PROPOSED, TransitionContext(), actor="test")
    items.transition(
        w.conn, w.clock, s, Status.NEEDS_CLARIFICATION, TransitionContext(), actor="test"
    )


def _round3_fraud(w: World) -> None:
    _asked(facts=W.FRAUD)(w)
    s = a(w)
    ctx = TransitionContext(payment_or_fraud=True, origin=Origin.ANSWER, stepup_verified=True)
    for _ in range(2):
        items.transition(
            w.conn, w.clock, s, Status.NEEDS_CLARIFICATION, TransitionContext(), actor="test"
        )
        items.transition(w.conn, w.clock, s, Status.AWAITING_STEPUP, ctx, actor="test")
        items.transition(w.conn, w.clock, s, Status.CLARIFIED, ctx, actor="test")
        items.transition(w.conn, w.clock, s, Status.PROPOSED, TransitionContext(), actor="test")
    items.transition(
        w.conn, w.clock, s, Status.NEEDS_CLARIFICATION, TransitionContext(), actor="test"
    )


scenario(
    "guard_rounds_refuse_fraud",
    "As guard_rounds_refuse on a fraud item: the Slack answer"
    " is refused by the needs_clarification->awaiting_stepup guard.",
    _round3_fraud,
    ("form answer", W.click(answers.ANSWER, a, values={answers.ANSWER: "No"}, kind="form")),
)
scenario(
    "guard_rounds_refuse",
    "Round 3 committed, _to_person not run: an answer is refused by"
    " the rounds guard (needs_clarification->clarified refuses).",
    _round3,
    ("answers.answer", answer_cli()),
)


# Undo (digests.py, mailbox_actions.py).


def _executed(w: World) -> None:
    _ap("live", W.MARKETING, W.KNOWN_BULK)(w)
    decide.apply(w.conn, w.clock, a(w))
    check(w)


def queue_undo(w: World) -> Any:
    item = w.conn.execute("SELECT * FROM items WHERE stable_id = ?", (a(w),)).fetchone()
    return digests.queue_undo(w.conn, w.clock, item, "os_user")


scenario(
    "undo",
    "The digest's Undo (executed->undoing->undone) at the next check.",
    _executed,
    ("digests.queue_undo", queue_undo),
    ("check", check),
    mail=True,
)


def lose_lease(w: World) -> None:
    w.lost = True


scenario(
    "undo_lease_lost",
    "The lease is lost before the queued Undo runs (executed->undoing->undo_failed, no IMAP).",
    _executed,
    ("digests.queue_undo", queue_undo),
    ("lease lost", lose_lease),
    ("check", check),
    mail=True,
)


# Held mail when an address goes live (stages._release_held).


def _held(w: World) -> None:
    _ap("assist", W.MARKETING, W.KNOWN_BULK)(w)
    decide.apply(w.conn, w.clock, a(w))  # held, today
    w.clock.advance(8 * 86400)
    w.vars["b"] = W.item(w, 1, classification=W.MARKETING, facts=W.KNOWN_BULK)
    decide.apply(w.conn, w.clock, b(w))  # held, recent
    w.clock.advance(60)


LIVE_GATE = gate.Gate("ap", "standard", "d", (), 0, 0, 0, 0)


def _gate_met(*_args: object, **_kw: object) -> gate.Gate:
    return LIVE_GATE


def go_live(held: str) -> Callable[[World], Any]:
    def run(w: World) -> Any:
        with mock.patch.object(stages, "_live_allowed", _gate_met):
            return stages.set_stage(
                w.conn, w.clock, "ap", "live", nonce=w.vars.get("nonce"), held=held
            )

    return run


for choice in ("run", "resolve_older", "resolve_all"):
    scenario(
        f"go_live_held_{choice}",
        f"Going live with held mail, held={choice!r}.",
        _held,
        (f"set_stage live (step-up asked) {choice}", W.keep(go_live(choice))),
        ("stepup", W.stepup_done),
        (f"set_stage live {choice}", go_live(choice)),
    )


# Closing: resolve, Dismiss, mail gone from INBOX.


def _every_open(w: World) -> None:
    W.address(w)
    for i, st in enumerate(sorted(OPEN, key=lambda s: s.value)):
        w.vars[st.value] = W.at(w, i, st)
    if w.src is not None:
        W.cursor(w)


def _all_open(w: World) -> list[str]:
    return [w.vars[s.value] for s in sorted(OPEN, key=lambda s: s.value)]


scenario(
    "resolve_every_open",
    "`ecf item resolve` over one item in each open status.",
    _every_open,
    (
        "inbox.resolve",
        lambda w: inbox.resolve(
            w.conn, w.clock, inbox.Selection(refs=_all_open(w)), reason="done", nonce=None
        ),
    ),
)


def _gone(w: World) -> None:
    assert w.src is not None
    for uid in list(w.src._msgs):  # pyright: ignore[reportPrivateUsage]
        w.src.expunge(uid)


scenario(
    "mailbox_gone_every_open",
    "Every open item's mail left INBOX; the check closes each (resolved_by_mailbox).",
    _every_open,
    ("mail moved in the client", _gone),
    ("check", check),
    mail=True,
)


def _fraud_and_plain(w: World) -> None:
    W.address(w)
    w.vars["a"] = W.proposed(w, 0, facts=W.FRAUD)
    w.vars["b"] = W.proposed(w, 1, facts=PLAIN)


scenario(
    "resolve_fraud_stepup",
    "Resolving a set with a fraud item needs step-up for all.",
    _fraud_and_plain,
    (
        "inbox.resolve (step-up asked)",
        W.keep(
            lambda w: inbox.resolve(
                w.conn, w.clock, inbox.Selection(refs=[a(w), b(w)]), reason="done", nonce=None
            )
        ),
    ),
    ("stepup", W.stepup_done),
    (
        "inbox.resolve",
        lambda w: inbox.resolve(
            w.conn,
            w.clock,
            inbox.Selection(refs=[a(w), b(w)]),
            reason="done",
            nonce=w.vars["nonce"],
        ),
    ),
)
scenario(
    "dismiss",
    "Slack Dismiss: refused on a fraud item, done on a plain one.",
    _fraud_and_plain,
    ("click dismiss fraud", W.click(cards.DISMISS, a)),
    ("click dismiss", W.click(cards.DISMISS, b)),
)


# Unused-name guard for imports kept for scenarios added later.
_ = (Origin, Stage, OPEN)


# Races and D115's behaviours (SPEC §21.4, OD-841): another writer at a named point.


def _awaiting_claude_c(w: World) -> None:
    """Preset C mail waiting for Claude to classify it."""
    W.address(w)
    w.vars["a"] = W.item(w, 0, classification=W.REPLY, facts=PLAIN, to=None)
    with write_tx(w.conn):
        w.conn.execute("UPDATE items SET classification = NULL")
    items.transition(
        w.conn, w.clock, a(w), Status.AWAITING_CLAUDE, TransitionContext(), actor="service"
    )


scenario(
    "store_after_resolve",
    "Resolved by hand while Claude classified it: store's UPDATE"
    " matches no row, yet the item.classified audit row is written (inventory-1b §4.4).",
    _awaiting_claude_c,
    ("inbox.resolve", W.resolve(a)),
    ("classifier.store", _store(W.REPLY)),
)
scenario(
    "to_actor_lost_cas",
    "The item closes between decide's record and to_actor's move:"
    " decide.apply still reports awaiting_claude (inventory-1b §4.5).",
    _ap("live", W.REPLY, PLAIN),
    ("decide.apply", W.interleave(claude_queue, "_move", W.resolve(a), apply_a)),
)


class _ClosingExecutor:
    """An executor during whose run the item is resolved by hand (inventory-1b §4.7)."""

    def __init__(self, w: World) -> None:
        self.w = w

    def __call__(self, conn: Any, clock: Any, item: Any, actions: Any) -> list[str]:
        W.resolve(lambda _w: str(item["stable_id"]))(self.w)
        return ["done"]


scenario(
    "executor_closes_item",
    "The item is resolved while its action runs: the run's"
    " executing->executed raises, the grant stays consumed (inventory-1b §4.7).",
    _approved_now,
    ("execute.run_once", lambda w: execute.run_once(w.conn, w.clock, _ClosingExecutor(w))),
    ("tick", tick),
)
scenario(
    "outbound_disable_abort",
    "A delayed send closes just before disable cancels it:"
    " cancel raises and outbound stays on (D115).",
    _two_sends,
    (
        "outbound.disable",
        W.interleave(
            outbound.approvals,
            "cancel",
            W.resolve(b),
            lambda w: outbound.disable(w.conn, w.clock, "ap", actor="os_user"),
        ),
    ),
)


def _consumed_send(w: World) -> None:
    """An executing send whose grant was already consumed (the run is past _consume)."""
    _two_sends(w)
    with write_tx(w.conn):
        w.conn.execute(
            "UPDATE grants SET status = 'consumed', consumed_at = ? WHERE"
            " stable_id = ? AND status = 'approved'",
            (ts(w), a(w)),
        )


scenario(
    "outbound_disable_not_sent",
    "Disable with a send already past _consume: nothing stops"
    " it, yet its card says 'Not sent' (D114 (10), D115).",
    _consumed_send,
    ("outbound.disable", lambda w: outbound.disable(w.conn, w.clock, "ap", actor="os_user")),
)


# /ecf-review's settle (claude_review.py): a held submission applied once telemetry shows the
# pinned model. Claims and holds are set up as test_claude_review does.


def _review_held(kind: str) -> Any:
    def setup(w: World) -> None:
        from tests import test_claude_review as tcr  # noqa: PLC0415

        W.address(w)
        sid = tcr.waiting_item(
            w.conn, w.clock, "ap", classification=tcr.REQUEST if kind == "act" else None
        )
        w.vars["a"] = sid
        tok = tcr.token_for(tcr.queue(w.conn, w.clock), sid)
        agent = tcr.agent_of(w.conn, sid)
        if kind == "classify":
            hold = claude_review.record_classification(
                w.conn, w.clock, tcr.S1, sid, tok, tcr.ROUTINE, agent
            )
        else:
            hold = claude_review.propose_action(
                w.conn,
                w.clock,
                tcr.S1,
                sid,
                tok,
                {"action": "flag", "target": "", "reason": "x"},
                agent,
            )
        assert isinstance(hold, Hold), hold
        w.vars["hold"] = hold

    return setup


def settle(w: World) -> Any:
    hold = w.vars["hold"]
    pin = claude_pins.effective(w.conn)[claude_review.ROLE[hold.agent]]
    return claude_review.settle(w.conn, w.clock, w.notifier, Telemetry(), hold, Seen(pin))


scenario(
    "review_settle_classify",
    "A held classification settles: claim done, the item"
    " classified (awaiting_claude->classified) and decided.",
    _review_held("classify"),
    ("claude_review.settle", settle),
    ("tick", tick),
)
scenario(
    "review_settle_act",
    "A held proposal settles: the actor's flag is applied (awaiting_claude->proposed->...).",
    _review_held("act"),
    ("claude_review.settle", settle),
    ("tick", tick),
)
scenario(
    "review_settle_after_resolve",
    "The item was resolved by hand while the proposal was"
    " held: the claim is marked done, nothing applied.",
    _review_held("act"),
    ("inbox.resolve", W.resolve(a)),
    ("claude_review.settle", settle),
)


# More entry paths of the planner's scope (D113 Q3): item creation, refusals at execution,
# resolve by age, held cards, a plain Slack answer, a refused send requeue.


def _no_items(w: World) -> None:
    W.address(w)


scenario(
    "create_item",
    "items.create_item: a fetched message inserted at new, with its audit row.",
    _no_items,
    ("items.create_item", lambda w: W.item(w, 0, to=None)),
)


def _executing_then_assist(w: World) -> None:
    _ap("live", W.MARKETING, W.KNOWN_BULK)(w)
    decide.apply(w.conn, w.clock, a(w))
    with write_tx(w.conn):  # the address left live after the plan ran
        w.conn.execute("UPDATE addresses SET stage = 'assist'")


scenario(
    "refused_at_execution",
    "A move refused when it runs (the address left live): executing->failed at once.",
    _executing_then_assist,
    ("check", check),
    mail=True,
)


def _old_and_new(w: World) -> None:
    W.address(w)
    w.vars["a"] = W.proposed(w, 0, facts=PLAIN)
    w.clock.advance(10 * 86400)
    w.vars["b"] = W.proposed(w, 1, facts=PLAIN)


scenario(
    "resolve_older_than",
    "`ecf item resolve --older-than 7`: only the old item closes.",
    _old_and_new,
    (
        "inbox.resolve older",
        lambda w: inbox.resolve(
            w.conn,
            w.clock,
            inbox.Selection(older_than_days=7, address_id="ap"),
            reason="old",
            nonce=None,
        ),
    ),
)


def _carded_later(w: World) -> None:
    _waiting()(w)
    approvals.request(w.conn, w.clock, a(w), ARCHIVE, card=False)  # during a backlog


scenario(
    "post_held_cards",
    "An approval requested during a backlog got no card; the next tick posts it.",
    _carded_later,
    ("tick", tick),
    ("tick again", tick),
)
scenario(
    "answer_slack_plain",
    "A plain item's answer from the Slack form (needs_clarification->clarified).",
    _asked(),
    ("answers.ask", ask),
    ("form answer", W.click(answers.ANSWER, a, values={answers.ANSWER: "CC-7"}, kind="form")),
)
scenario(
    "requeue_unknown_send_refused",
    "A send whose outcome is unknown can't be requeued: it may already have gone out.",
    _sent_unknown,
    ("check", check),
    (
        "approvals.requeue",
        lambda w: approvals.requeue(w.conn, w.clock, a(w), actor="os_user", nonce=None),
    ),
    mail=True,
)


# Coverage baseline additions (planner-coverage-baseline.md, groups A and B; operator option a):
# lost races, partial commits and write branches no test drove.


def reject_a(w: World) -> Any:
    return approvals.reject(w.conn, w.clock, a(w), actor="os_user")


scenario(
    "approve_loses_to_resolve",
    "The item is resolved by hand after a Slack Approve moved it to approved, before the grant"
    " update: the grant is voided, so _set_grant raises; the item stays resolved.",
    _waiting(),
    ("approvals.request", request),
    (
        "click approve",
        W.interleave(approvals, "_set_grant", W.resolve(a), W.click(approvals.APPROVE, grant)),
    ),
)
scenario(
    "approve_slack_twice",
    "A second Slack Approve on an item already waiting for the computer: no second move.",
    _send_waiting(),
    ("click approve", W.click(approvals.APPROVE, grant)),
    ("click approve again", W.click(approvals.APPROVE, grant)),
)


scenario(
    "delay_item_closed",
    "A high send is resolved by hand between its move to delayed and the delay row's insert:"
    " the row outlives the item until the next countdown deletes it.",
    _send_waiting(sensitivity="high"),
    ("approvals.approve (step-up asked)", W.keep(approve_cli())),
    ("stepup", W.stepup_done),
    (
        "approvals.approve",
        W.after(approvals.items, "transition", W.resolve(a), approve_cli(), W.to(Status.DELAYED)),
    ),
    ("advance_delays 60", lambda w: approvals.advance_delays(w.conn, w.clock, 60, woke=False)),
)


scenario(
    "expire_loses_to_reject",
    "Expiry finds the approval rejected meanwhile: that item is skipped, the tick goes on.",
    _waiting(),
    ("approvals.request", request),
    ("advance 15 d", W.advance(15 * 86400)),
    ("tick", W.interleave(approvals, "_expire_one", reject_a, tick)),
)


scenario(
    "answer_expire_loses_to_answer",
    "Answer expiry finds the answer finished at the CLI meanwhile (its step-up done before):"
    " skipped, the tick goes on.",
    _asked(facts=W.FRAUD),
    ("answers.ask", ask),
    ("form answer", W.click(answers.ANSWER, a, values={answers.ANSWER: "No"}, kind="form")),
    ("advance 15 d", W.advance(15 * 86400)),
    ("answers.answer (step-up asked)", W.keep(answer_cli(None))),
    ("stepup", W.stepup_done),
    ("tick", W.interleave(answers, "_expire_one", answer_cli(None), tick)),
)


def _decide_sweep_race(w: World) -> None:
    _sweep_setup(w)
    w.clock.advance(180)


scenario(
    "decide_sweep_item_closed",
    "The sweep's decide.apply finds its item resolved meanwhile: its plan and policy.decided row"
    " are still recorded on the resolved item, the move fails and is logged; the next item"
    " still runs.",
    _decide_sweep_race,
    (
        "decide.sweep",
        W.interleave(decide, "apply", W.resolve(a), lambda w: decide.sweep(w.conn, w.clock)),
    ),
)
scenario(
    "store_then_apply_fails",
    "Claude's classification is stored, then the item is resolved before the rules run:"
    " store keeps the classification; apply records its plan on the resolved item, then logs the"
    " failed move.",
    _new_c,
    ("claude_queue.route_new", lambda w: claude_queue.route_new(w.conn, w.clock, a(w))),
    (
        "classifier.store",
        W.interleave(classifier.decide, "apply", W.resolve(a), _store(W.MARKETING)),
    ),
)
scenario(
    "go_live_held_apply_fails",
    "Going live, held=run: the recent held item moves to proposed, then is resolved before"
    " decide.apply; apply records its plan on the resolved item, the move fails, it counts as"
    " failed.",
    _held,
    ("set_stage live (step-up asked) run", W.keep(go_live("run"))),
    ("stepup", W.stepup_done),
    ("set_stage live run", W.interleave(stages.decide, "apply", W.resolve(b), go_live("run"))),
)


# /ecf-review submissions: resubmits, a claim ended between the check and the hold, a held
# result whose claim or rules changed before settle.


def _review_claimed(kind: str) -> Any:
    def setup(w: World) -> None:
        from tests import test_claude_review as tcr  # noqa: PLC0415

        W.address(w)
        sid = tcr.waiting_item(
            w.conn, w.clock, "ap", classification=tcr.REQUEST if kind == "act" else None
        )
        w.vars["a"] = sid
        w.vars["tok"] = tcr.token_for(tcr.queue(w.conn, w.clock), sid)
        w.vars["agent"] = tcr.agent_of(w.conn, sid)

    return setup


FLAG = {"action": "flag", "target": "", "reason": "x"}


def _s1() -> str:
    from tests import test_claude_review as tcr  # noqa: PLC0415

    return tcr.S1


def propose(body: dict[str, Any]) -> Callable[[World], Any]:
    return lambda w: claude_review.propose_action(
        w.conn, w.clock, _s1(), a(w), w.vars["tok"], body, w.vars["agent"]
    )


def release_session(w: World) -> Any:
    return claude_review.release_session(w.conn, _s1())


scenario(
    "review_resubmit_same",
    "The same proposal sent twice under one claim: the second is pending, not a second record.",
    _review_claimed("act"),
    ("claude_review.propose_action", propose(FLAG)),
    ("claude_review.propose_action again", propose(FLAG)),
)
scenario(
    "review_resubmit_different",
    "A different proposal under a held claim is refused.",
    _review_claimed("act"),
    ("claude_review.propose_action", propose(FLAG)),
    (
        "claude_review.propose_action other",
        propose({"action": "leave", "target": "", "reason": "y"}),
    ),
)
scenario(
    "review_hold_lost",
    "The session ends between the claim check and the hold: _hold's fenced update misses.",
    _review_claimed("act"),
    (
        "claude_review.propose_action",
        W.interleave(claude_review, "_hold", release_session, propose(FLAG)),
    ),
)
scenario(
    "review_done_lost",
    "The item was resolved; the session ends before propose_action ends the claim:"
    " _done's fenced update misses.",
    _review_claimed("act"),
    ("inbox.resolve", W.resolve(a)),
    (
        "claude_review.propose_action",
        W.interleave(claude_review, "_done", release_session, propose(FLAG)),
    ),
)
scenario(
    "review_settle_released",
    "The session ended while the proposal was held: settle applies nothing.",
    _review_held("act"),
    ("claude_review.release_session", release_session),
    ("claude_review.settle", settle),
)


def apply_templates_off(w: World) -> Any:
    """`ecf config apply` with the template disabled (the real writer of config.templates)."""
    off = W.TEMPLATES | {"templates": [W.TEMPLATES["templates"][0] | {"enabled": False}]}
    text = json.dumps({"version": 1, "templates": off})
    return config.apply(w.conn, w.clock, w.notifier, text, dry_run=False, nonce=w.vars.get("nonce"))


def _review_reply(w: World) -> None:
    _review_claimed("act")(w)
    W.sends(w)
    hold = claude_review.propose_action(
        w.conn, w.clock, _s1(), a(w), w.vars["tok"],
        {"action": "reply_template", "target": "ack", "reason": "x"}, w.vars["agent"],
    )  # fmt: skip
    assert isinstance(hold, Hold), hold
    w.vars["hold"] = hold


scenario(
    "review_settle_rules_changed",
    "`ecf config apply` disables the template a held reply names before settle: the claim ends,"
    " nothing applied; the item waits for the next round.",
    _review_reply,
    ("config.apply (step-up asked)", W.keep(apply_templates_off)),
    ("stepup", W.stepup_done),
    ("config.apply", apply_templates_off),
    ("claude_review.settle", settle),
)


# Outbound disable, undo, reply limits, digest Approve all, held cards.


def _sends_and_archive(w: World) -> None:
    """_two_sends plus an executing archive on the same address."""
    _two_sends(w)
    w.vars["c"] = s = W.proposed(w, 2, facts=PLAIN)
    approvals.request(w.conn, w.clock, s, ARCHIVE)
    _approve(w, s)


scenario(
    "outbound_disable_skips_non_send",
    "Outbound off: an executing archive on the address is left alone.",
    _sends_and_archive,
    ("outbound.disable", lambda w: outbound.disable(w.conn, w.clock, "ap", actor="os_user")),
)


def _executed_flag(w: World) -> None:
    """Claude's flag on a live item, run by the check (what the model's plan did, so Undo may
    remove it)."""
    _awaiting_claude(w)  # its setup writes the check's cursor
    _decide_one({"action": "flag", "target": "", "reason": "r"})(w)
    check(w)


scenario(
    "undo_flag",
    "Undo of a flag Claude's plan set: the flag comes off (executed->undoing->undone).",
    _executed_flag,
    ("digests.queue_undo", queue_undo),
    ("check", check),
    mail=True,
)


def _executed_label(w: World) -> None:
    _ap("live", W.INVOICE, PLAIN)(w)
    decide.apply(w.conn, w.clock, a(w))
    check(w)


scenario(
    "undo_lease_lost_midway",
    "The lease goes after Undo found the message, before it removes the label (undo_failed).",
    _executed_label,
    ("digests.queue_undo", queue_undo),
    ("check", W.after(mailbox_actions, "_inbox_uid", lose_lease, check)),
    mail=True,
)


def _old_send_then_new(w: World) -> None:
    """A send to the sender 8 days ago; a new email from them in another thread."""
    _send_approved(mail=True)(w)
    check(w)
    w.clock.advance(8 * 86400)
    other = W.SEND_FACTS | {"thread_root": "<other@vendor-a.example>"}
    _send_ready(n=1, key="b", facts=other)(w)
    item = w.conn.execute("SELECT * FROM items WHERE stable_id = ?", (b(w),)).fetchone()
    ctx, p = decide.plan_for(w.conn, item)
    actor.decide_one(w.conn, w.clock, item, ctx, p, REPLY_ACK, frozenset())
    _approve(w, b(w))


scenario(
    "reply_limit_old_send",
    "The sender got a template reply 8 days ago in another thread: the new reply goes.",
    _old_send_then_new,
    ("check", check),
    mail=True,
)


scenario(
    "digest_approve_all_loses_to_resolve",
    "Approve all: the listed item is resolved by hand just before its approve runs;"
    " approve refuses, it counts as skipped, the batch is cleared.",
    _approve_all_setup,
    (
        "click approve_all",
        W.interleave(
            digest_actions.approvals,
            "approve",
            W.resolve(a),
            W.click(digest_actions.APPROVE_ALL, lambda w: "k1"),
        ),
    ),
)


def _many_carded_later(w: World) -> None:
    W.address(w)
    for n in range(approvals.CARDS_PER_TICK + 1):
        s = W.proposed(w, n, facts=PLAIN)
        approvals.request(w.conn, w.clock, s, ARCHIVE, card=False)


scenario(
    "post_held_cards_limit",
    "21 approvals requested during a backlog (no cards), the backlog gone: a tick posts 20, the"
    " next the last one.",
    _many_carded_later,
    ("tick", tick),
    ("tick again", tick),
)


# Delta review (planner-step2-delta-review.md, MEDIUM 3): races and partial commits between two
# commits of one v1 call, and SMTP failures the fake sender makes.


def _smtp(mode: str | None) -> Callable[[World], Any]:
    def run(w: World) -> None:
        w.smtp.fail = mode

    return run


def _send_failing(mode: str) -> Any:
    def setup(w: World) -> None:
        _send_approved(mail=True)(w)
        w.smtp.fail = mode

    return setup


scenario(
    "send_temp_fail_retry",
    "SMTP says try later: the sent row stays pending, the grant goes back to approved; the retry"
    " meets its own grant's row and sends.",
    _send_failing("temp"),
    ("check", check),
    ("smtp back", _smtp(None)),
    ("advance 1 h", W.advance(3600)),
    ("check", check),
    mail=True,
)
scenario(
    "send_temp_fail_requeue",
    "Three try-laters: failed, the row still pending; a requeue (step-up) issues a new grant;"
    " the run skips the earlier attempt's row in the reply limits, writes a new row and sends."
    " The old row stays pending, never settled.",
    _send_failing("temp"),
    ("check", check),
    ("advance 1 h", W.advance(3600)),
    ("check", check),
    ("advance 1 h", W.advance(3600)),
    ("check", check),
    ("smtp back", _smtp(None)),
    (
        "approvals.requeue (step-up asked)",
        W.keep(lambda w: approvals.requeue(w.conn, w.clock, a(w), actor="os_user", nonce=None)),
    ),
    ("stepup", W.stepup_done),
    (
        "approvals.requeue",
        lambda w: approvals.requeue(w.conn, w.clock, a(w), actor="os_user", nonce=w.vars["nonce"]),
    ),
    ("check", check),
    mail=True,
)
scenario(
    "send_refused",
    "SMTP refuses the send: the row is failed, the grant voided, the item failed (no retry).",
    _send_failing("refused"),
    ("check", check),
    mail=True,
)


def cancel_a(w: World) -> Any:
    return approvals.cancel(w.conn, w.clock, a(w), actor="os_user")


scenario(
    "delay_start_loses_to_cancel",
    "The countdown ran out and its delay row is deleted; a cancel lands before _start's move,"
    " which has no expected status: the error escapes advance_delays.",
    _send_approved(sensitivity="high"),
    (
        "advance_delays 601",
        W.interleave(
            approvals,
            "_start",
            cancel_a,
            lambda w: approvals.advance_delays(w.conn, w.clock, 601, woke=False),
        ),
    ),
)
scenario(
    "request_grant_for_closed",
    "Resolved by hand between request's move to awaiting_approval and its grant insert: the"
    " grant is issued for a closed item and its card is posted.",
    _waiting(),
    (
        "approvals.request",
        W.after(
            approvals.items, "transition", W.resolve(a), request, W.to(Status.AWAITING_APPROVAL)
        ),
    ),
)
scenario(
    "expire_reoffer_grant_for_closed",
    "Resolved by hand between expiry's re-offer move and its grant insert: a fresh grant for a"
    " closed item.",
    _waiting(),
    ("approvals.request", request),
    ("advance 15 d", W.advance(15 * 86400)),
    (
        "tick",
        W.after(approvals.items, "transition", W.resolve(a), tick, W.to(Status.AWAITING_APPROVAL)),
    ),
)


def _expired_twice(w: World) -> None:
    _waiting()(w)
    approvals.request(w.conn, w.clock, a(w), ARCHIVE)
    for _ in range(2):
        w.clock.advance(15 * 86400)
        tick(w)


scenario(
    "reoffer_grant_for_closed",
    "`ecf approve` on an item expired twice: resolved by hand between _reoffer's move and its"
    " grant insert; the approve then fails on the closed item.",
    _expired_twice,
    (
        "approvals.approve (reoffer)",
        W.after(
            approvals.items,
            "transition",
            W.resolve(a),
            approve_cli(),
            W.to(Status.AWAITING_APPROVAL),
        ),
    ),
)
scenario(
    "decide_run_loses_to_resolve",
    "Live archive: decide's automatic grant is inserted, then the item is resolved before the"
    " move to executing: the resolve voids the grant, decide.apply raises.",
    _ap("live", W.MARKETING, W.KNOWN_BULK),
    (
        "decide.apply",
        W.interleave(decide.items, "transition", W.resolve(a), apply_a, W.to(Status.EXECUTING)),
    ),
)


def _failed_three(w: World) -> None:
    _approved_now(w)
    for _ in range(3):
        execute.run_once(w.conn, w.clock, W.FakeExecutor(1))
        w.clock.advance(3600)


scenario(
    "requeue_loses_to_resolve",
    "A requeue's new grant is inserted, then the item is resolved before its move: the resolve"
    " voids it, the requeue raises.",
    _failed_three,
    (
        "approvals.requeue",
        W.interleave(
            approvals.items,
            "transition",
            W.resolve(a),
            lambda w: approvals.requeue(w.conn, w.clock, a(w), actor="os_user", nonce=None),
            W.to(Status.EXECUTING),
        ),
    ),
)


class _ClosingFailingExecutor:
    """An executor during whose failing run the item is resolved by hand."""

    def __init__(self, w: World) -> None:
        self.w = w

    def __call__(self, conn: Any, clock: Any, item: Any, actions: Any) -> list[str]:
        W.resolve(lambda _w: str(item["stable_id"]))(self.w)
        raise OSError("server said no")


def closing_fail(w: World) -> Any:
    return execute.run_once(w.conn, w.clock, _ClosingFailingExecutor(w))


scenario(
    "fail_run_item_closed",
    "The item is resolved during a run that then fails: the consumed grant (a resolve voids only"
    " issued and approved ones) is set back to approved by _retry and stays approved on the closed"
    " item; the retry finds the item closed and completes the job.",
    _approved_now,
    ("execute.run_once fail, closed meanwhile", closing_fail),
    ("advance 1 h", W.advance(3600)),
    ("execute.run_once", run_actions()),
)


def _failed_twice(w: World) -> None:
    _approved_now(w)
    for _ in range(2):
        execute.run_once(w.conn, w.clock, W.FakeExecutor(1))
        w.clock.advance(3600)


scenario(
    "fail_last_attempt_item_closed",
    "The last attempt fails after the item was resolved: _failed's move to failed raises.",
    _failed_twice,
    ("execute.run_once fail, closed meanwhile", closing_fail),
)


def settle_racing(other: Callable[[World], Any]) -> Callable[[World], Any]:
    """settle, with `other` run between its claim read and its unfenced claim update (at its
    pin read)."""

    def run(w: World) -> Any:
        hold = w.vars["hold"]
        pin = claude_pins.effective(w.conn)[claude_review.ROLE[hold.agent]]
        return W.interleave(
            claude_review.claude_pins,
            "effective",
            other,
            lambda w: claude_review.settle(
                w.conn, w.clock, w.notifier, Telemetry(), hold, Seen(pin)
            ),
        )(w)

    return run


def release_and_reclaim(w: World) -> Any:
    from tests import test_claude_review as tcr  # noqa: PLC0415

    release_session(w)
    return tcr.queue(w.conn, w.clock, tcr.S2)


scenario(
    "review_settle_races_release",
    "The session ends between settle's claim read and its claim update: the update is unfenced,"
    " so the released claim is marked done and the proposal applied.",
    _review_held("act"),
    ("claude_review.settle", settle_racing(release_session)),
)
scenario(
    "review_settle_races_reclaim",
    "The session ends and another session claims the item again before settle's update: the old"
    " fence matches nothing, the proposal is applied under the new claim.",
    _review_held("act"),
    ("claude_review.settle", settle_racing(release_and_reclaim)),
)


def reject_b(w: World) -> Any:
    return approvals.reject(w.conn, w.clock, b(w), actor="os_user")


scenario(
    "approve_pending_item_closes",
    "approve_pending's step-up is spent; b is rejected after a is approved: b's approve raises,"
    " a goes on to executing.",
    _two_queued,
    (
        "approvals.approve_pending (step-up asked)",
        W.keep(
            lambda w: approvals.approve_pending(
                w.conn, w.clock, w.notifier, [a(w), b(w)], nonce=None
            )
        ),
    ),
    ("stepup", W.stepup_done),
    (
        "approvals.approve_pending",
        W.after(
            approvals,
            "approve",
            reject_b,
            lambda w: approvals.approve_pending(
                w.conn, w.clock, w.notifier, [a(w), b(w)], nonce=w.vars["nonce"]
            ),
        ),
    ),
)


def _scan_expired(w: World) -> None:
    """A scan run's two archives, one expired twice (old mail: no cards, no re-offer card)."""
    W.address(w)
    with write_tx(w.conn):
        run = scans.new_run(
            w.conn, w.clock, kind="scan", address_id="ap", until=None, mode="acting",
            since="2026-09-01T00:00:00.000000Z",
        )  # fmt: skip
    w.vars["run"] = run
    old = {"backfill": 1, "scan_run": run}
    w.vars["a"] = W.proposed(w, 0, facts=PLAIN | old)
    approvals.request(w.conn, w.clock, a(w), ARCHIVE, card=False)
    for _ in range(2):
        w.clock.advance(15 * 86400)
        tick(w)
    w.vars["b"] = W.proposed(w, 1, facts=PLAIN | old)
    approvals.request(w.conn, w.clock, b(w), ARCHIVE, card=False)


def approve_scan(w: World) -> Any:
    return approvals.approve_scan(
        w.conn, w.clock, w.notifier, w.vars["run"], [a(w), b(w)], nonce=w.vars.get("nonce")
    )


scenario(
    "approve_scan_reoffer",
    "`ecf approve --scan` with an expired item: the re-offer (expired->awaiting_approval, a new"
    " grant) commits before the step-up is asked; with the step-up both are approved.",
    _scan_expired,
    ("approvals.approve_scan (step-up asked)", W.keep(approve_scan)),
    ("stepup", W.stepup_done),
    ("approvals.approve_scan", approve_scan),
)
