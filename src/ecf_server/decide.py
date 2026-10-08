"""Applying the policy to a classified item (SPEC §5.1 steps 6-8, §9.1, §9.5; V1.3 step 4b).

After the classifier stores a classification, `apply` plans (policy.py) and moves the item on:

- **Escalations** queue at once in every stage, as the pre-check's do (a model-driven fraud guard
  escalates the same way); the Slack thread posts them. An item the pre-check already escalated
  isn't queued twice.
- **To the actor:** when the rule continues to the actor, the item waits at `classified` for the
  local actor (preset A, V1.3 step 4c) or at `awaiting_claude` for `/ecf-review` (B and C, V1.4
  step 1); `apply` is called again with the actor's proposal.
- **shadow:** `classified → proposed → observed`; the plan is kept for review (V1.3 step 6a).
- **assist:** when every mailbox action is safe (label, flag), `proposed → executing` under an
  automatic grant; otherwise the whole plan is `held` until the address goes live.
- **live:** when an action needs a person, the whole plan goes to approval (`awaiting_approval`, a
  card with the grant; while more than 100 emails wait for the model, no card each: the digest's
  "Approve all N reversible" lists them, §5.3); otherwise `proposed → executing` under an
  automatic grant. A plan with no mailbox action (only escalate or leave) runs an empty grant, so
  the item still ends `executed`.

The proposal stored on the item holds the mailbox actions the runner executes (`actions`, bound to
the grant's hash) and the plan's reasons (`plan`: rule, dropped actions and why, risk, and whether
the digest offers "confirm this sender's category"). `decision_source` is `rule` or `actor`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any

from ecf.ids import AddressId, StableId, new_grant_id
from ecf_server import (
    approvals,
    claude_queue,
    config,
    items,
    jobs,
    modelq,
    outbound_plan,
    policy,
    probe,
)
from ecf_server.actions import Planned as MailAction
from ecf_server.actions import action_hash
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.policy import Context, Plan
from ecf_server.state_machine import Stage, Status, TransitionContext

MAILBOX = frozenset({"label", "flag", "mark_read", "archive", "move", "junk", "draft_reply",
                     "reply_template", "forward_internal"})  # fmt: skip
ASSIST_SAFE = frozenset({"label", "flag"})
AUTO_GRANT_TTL_S = 3600
EXECUTE_ATTEMPTS = 3
BACKLOG_BATCH = modelq.BACKLOG_BATCH  # §5.3: above this, approvals go to digests, not cards


def context(conn: sqlite3.Connection, item: sqlite3.Row,
            classification: dict[str, Any] | None = None) -> Context:  # fmt: skip
    """`classification`: another than the item's (the local fallback's shadow run, V1.4)."""
    addr = conn.execute("SELECT sensitivity, outbound, preset FROM addresses"
                        " WHERE address_id = ?", (item["address_id"],)).fetchone()  # fmt: skip
    facts: dict[str, Any] = json.loads(item["facts"] or "{}")
    cfg = config.current(conn)
    policy_doc: dict[str, Any] = cfg["action_policy"] or {}
    standard: dict[str, str] = policy_doc.get("standard") or {}
    confirmed = None
    if facts.get("sender_hash"):
        row = conn.execute(
            "SELECT confirmed_category FROM senders WHERE address_id = ? AND sender_hash = ?",
            (item["address_id"], facts["sender_hash"]),
        )
        r = row.fetchone()  # fmt: skip
        confirmed = r["confirmed_category"] if r else None
    return Context(
        classification=classification
        if classification is not None
        else json.loads(item["classification"]),
        facts=facts,
        sensitivity=addr["sensitivity"] if addr else "high",
        rules=config.current_rules(conn),
        action_policy=standard,
        move_folders=frozenset(cfg["move_folders"] or []),
        confirmed_category=confirmed,
        batch_risky=claude_queue.batch_risky(conn, item["stable_id"]),
        outbound=bool(addr["outbound"]) if addr else False,
        local_pair=addr is None or addr["preset"] == "A" or bool(item["fallback_at"]),
        templates=frozenset(outbound_plan.enabled_templates(conn)),
        forwards=frozenset(outbound_plan.forward_entries(conn)),
        keywords_stored=probe.keywords_stored(conn, item["address_id"]),
    )


def plan_for(conn: sqlite3.Connection, item: sqlite3.Row) -> tuple[Context, Plan]:
    ctx = context(conn, item)
    return ctx, policy.plan(ctx, policy.labels(ctx.rules.schema, ctx.rules))


def apply(conn: sqlite3.Connection, clock: Clock, sid: str, p: Plan | None = None,
          *, source: str = "rule") -> Status:  # fmt: skip
    """Move a classified item on by its plan; returns its new status."""
    item = conn.execute("SELECT * FROM items WHERE stable_id = ?", (sid,)).fetchone()
    if p is None:
        _ctx, p = plan_for(conn, item)
    stage = Stage(conn.execute("SELECT stage FROM addresses WHERE address_id = ?",
                               (item["address_id"],)).fetchone()["stage"])  # fmt: skip
    if any(a.name == "escalate" for a in p.actions):
        _escalate(conn, clock, item)
    mailbox = [MailAction(a.name, a.target, a.payload) for a in p.actions if a.name in MAILBOX]
    needs_person = any(a.mode == "approve" for a in p.actions if a.name in MAILBOX)
    _record(conn, clock, sid, p, mailbox, source)
    if p.to_actor and source == "rule":  # the actor decides next: local (4c) or Claude (V1.4)
        return claude_queue.to_actor(conn, clock, sid)
    ctx = TransitionContext(stage=stage)
    if item["status"] != Status.PROPOSED:  # from classified, or clarified after an answer
        items.transition(conn, clock, StableId(sid), Status.PROPOSED, ctx, actor="service")
    if stage is Stage.SHADOW:
        items.transition(conn, clock, StableId(sid), Status.OBSERVED, ctx, actor="service")
        return Status.OBSERVED
    if stage is Stage.ASSIST:
        if all(a.name in ASSIST_SAFE for a in mailbox) and not needs_person:
            _run(conn, clock, sid, mailbox, TransitionContext(stage=stage, assist_safe=True))
            return Status.EXECUTING
        items.transition(conn, clock, StableId(sid), Status.HELD, ctx, actor="service")
        return Status.HELD
    if needs_person:
        backlog = sum(modelq.waiting(conn).values())
        approvals.request(conn, clock, sid, mailbox, card=backlog <= BACKLOG_BATCH)
        return Status.AWAITING_APPROVAL
    _run(conn, clock, sid, mailbox, ctx)
    return Status.EXECUTING


def record(conn: sqlite3.Connection, clock: Clock, sid: str, p: Plan, source: str) -> None:
    """Keep a plan with the item without moving it on (the actor's question, which goes to you)."""
    _record(conn, clock, sid, p, [], source)


def _record(conn: sqlite3.Connection, clock: Clock, sid: str, p: Plan,
            mailbox: list[MailAction], source: str) -> None:  # fmt: skip
    doc: dict[str, Any] = {
        "actions": [a.to_json() for a in mailbox],
        "plan": {
            "rule": p.rule_id,
            "actions": [
                {"name": a.name, "target": a.target, "mode": a.mode}
                | ({"payload": a.payload} if a.payload is not None else {})
                for a in p.actions
            ],
            "dropped": [{"name": d.name, "target": d.target, "why": d.why} for d in p.dropped],
            "to_actor": p.to_actor,
            "high_risk": p.high_risk,
            "payment_or_fraud": p.payment_or_fraud,
            "offer_confirm": p.offer_confirm,
            "actor": p.actor,
        },
    }
    item = conn.execute("SELECT proposal FROM items WHERE stable_id = ?", (sid,)).fetchone()
    kept: dict[str, Any] = json.loads(item["proposal"] or "{}") if item else {}
    doc = kept | doc  # questions and answers (answers.py) stay with the item
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute(
            "UPDATE items SET proposal = ?, decision_source = ?, updated_at = ?,"
            " suppressed_action = coalesce(?, suppressed_action) WHERE stable_id = ?",
            (json.dumps(doc, sort_keys=True), source, now, p.suppressed, sid),
        )
        conn.execute(
            "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
            " SELECT ?, address_id, stable_id, 'policy.decided', 'service', 'ok', ? FROM items"
            " WHERE stable_id = ?",
            (now, json.dumps({"rule": p.rule_id, "source": source,
                              "actions": doc["plan"]["actions"],
                              "dropped": doc["plan"]["dropped"]}), sid),
        )  # fmt: skip


def _escalate(conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row) -> None:
    with write_tx(conn):
        conn.execute(
            "INSERT INTO escalations (stable_id, address_id, state, created_at)"
            " VALUES (?, ?, 'pending', ?) ON CONFLICT (stable_id) DO NOTHING",
            (item["stable_id"], item["address_id"], to_ts(clock.now())),
        )


def _run(conn: sqlite3.Connection, clock: Clock, sid: str, mailbox: list[MailAction],
         ctx: TransitionContext) -> None:  # fmt: skip
    """An automatic grant, `proposed → executing`, and a job for the action runner."""
    item = conn.execute("SELECT * FROM items WHERE stable_id = ?", (sid,)).fetchone()
    grant_id = new_grant_id()
    now = clock.now()
    with write_tx(conn):
        conn.execute(
            "INSERT INTO grants (grant_id, stable_id, action_hash, content_hash, principal, status,"
            " expires_at) VALUES (?, ?, ?, ?, 'service', 'approved', ?)",
            (grant_id, sid, action_hash(sid, item["content_hash"], mailbox), item["content_hash"],
             to_ts(now + timedelta(seconds=AUTO_GRANT_TTL_S))),
        )  # fmt: skip
    items.transition(conn, clock, StableId(sid), Status.EXECUTING, ctx, actor="service",
                     expected=Status.PROPOSED)  # fmt: skip
    jobs.enqueue(conn, clock, jobs.Queue.ACTIONS, AddressId(item["address_id"]),
                 {"stable_id": sid, "grant_id": grant_id}, timeout_s=120,
                 max_attempts=EXECUTE_ATTEMPTS)  # fmt: skip
    jobs.make_due(conn, clock, item["address_id"])


STRANDED_AFTER = timedelta(minutes=2)  # longer than any apply in progress takes


def sweep(conn: sqlite3.Connection, clock: Clock, limit: int = 50) -> int:
    """Apply the policy again to classified items it didn't move on: none applied yet (a crash
    between classifying and deciding), or a plan recorded and then an error before the item moved
    (`_record` runs first). Items waiting for the actor are left to it. Each tick; returns how many
    were applied."""
    before = to_ts(clock.now() - STRANDED_AFTER)
    rows = conn.execute(
        "SELECT stable_id FROM items WHERE status = 'classified' AND (proposal IS NULL"
        " OR (updated_at < ? AND model_failed = 0 AND NOT (decision_source = 'rule'"
        " AND json_extract(proposal, '$.plan.to_actor') = 1)))"
        " ORDER BY updated_at LIMIT ?",
        (before, limit),
    ).fetchall()
    done = 0
    for (sid,) in rows:
        try:
            apply(conn, clock, sid)
            done += 1
        except Exception as exc:  # one bad item never stops the others; audited and logged
            log.error("policy.apply_failed", stable_id=sid[:8], error_type=type(exc).__name__)
    return done
