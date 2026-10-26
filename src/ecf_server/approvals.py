"""Approvals (SPEC §9.5, §9.6, §6.4, §6.5; V1.2 step 7b).

Built in V1.2 and exercised with fake proposals and a fake sender (OD-207): nothing proposes an
action that needs approval before the classifier (V1.3), and `live`, where approvals happen,
waits for the go-live gate (OD-209).

- `request` records a proposal, moves the item to `awaiting_approval` and issues a grant bound to
  the item, its content hash and the exact actions, expiring after 4 days for sends and 14 for
  everything else (OD-041). The card's buttons carry the grant ID; the action is always loaded
  from the grant, never from a button.
- `approve`: the first decision wins. A reversible action is one click in Slack. A send, an
  irreversible action, or hiding a fraud or regulator item (OD-213) needs step-up: clicked in
  Slack it waits at `awaiting_stepup` ("Queued for your computer (N waiting)", plus a desktop
  notification) until `ecf approve` at the computer; from the CLI the step-up happens there and
  then.
- A send on a `high` address waits 10 minutes of awake time (`delayed`), with Cancel.
- `expire` (each tick): an approval whose grant ran out goes to `expired`, its grant voided;
  after a first expiry it is offered again with a fresh grant ("expired, decide again"), after a
  second it stays `expired` (listed in the daily summary, §6.2).
- `requeue`: a failed or stuck action runs again under a new grant; a send needs step-up again."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from ecf.errors import (
    ConflictError,
    EcfError,
    GrantInvalidError,
    InvalidInputError,
    NotFoundError,
)
from ecf.ids import AddressId, StableId, new_grant_id
from ecf.status import Status
from ecf_server import (
    alerts,
    cards,
    claude_queue,
    inbox,
    items,
    jobs,
    marked,
    outbound_plan,
    pause,
    scans,
    slack_in,
    slack_out,
    slack_render,
    slack_routes,
    stepup,
)
from ecf_server.actions import Planned, action_hash
from ecf_server.chat import Button, Card, RouteRef
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.notify import Notifier, NullNotifier
from ecf_server.precheck import item_payment_or_fraud
from ecf_server.state_machine import Origin, Stage, TransitionContext, check_transition

SENDS = frozenset({"forward_internal", "reply_template"})
HIDE = frozenset({"mark_read", "archive", "move", "junk"})
REVERSIBLE = frozenset({"label", "flag", "mark_read", "archive", "move", "junk", "draft_reply"})
TTL_SEND = timedelta(days=4)  # OD-041: a long weekend doesn't lapse a send
TTL_OTHER = timedelta(days=14)
SEND_DELAY_S = 600.0  # 10 minutes of awake time
PENDING_MAX = 10  # `approve --pending` handles at most this many at once
EXECUTE_ATTEMPTS = 3
APPROVE, REJECT, CANCEL = "approve", "reject", "cancel"
desktop: Notifier = NullNotifier()  # set by the service; Slack clicks queued for step-up notify


@dataclass(frozen=True)
class Grant:
    grant_id: str
    stable_id: str
    action_hash: str
    status: str
    expires_at: str


# What an action is: how actions are described on cards, and which ones (sends,
# irreversible actions, hiding fraud or regulator mail) need step-up.


def describe(actions: list[Planned]) -> str:
    """What the actions do, naming a draft's or send's recipient (§9.5, §9.6)."""

    def one(a: Planned) -> str:
        t = a.target or ""
        to = str((a.payload or {}).get("to") or "")
        return {
            "label": f"label {t}",
            "flag": "flag",
            "mark_read": "mark read",
            "archive": "archive email",
            "move": f"move to {t}",
            "junk": "move to Junk",
            "draft_reply": f"save a draft reply to {to}" if to else "save a draft reply",
            "reply_template": f"send template '{t}' to {to}" if to else f"send template '{t}'",
            "forward_internal": f"forward to {to or t}",
        }.get(a.name, a.name)

    return " and ".join(one(a) for a in actions)


def fraud_or_regulator(facts: dict[str, Any]) -> bool:
    t: dict[str, Any] = facts.get("triggers") or {}
    return bool(facts.get("quarantined") or any(
        t.get(k) for k in ("fraud", "fraud_weak", "lookalikes", "regulator")))  # fmt: skip


def needs_stepup(facts: dict[str, Any], actions: list[Planned]) -> bool:
    """Every send, every irreversible action, and hiding a fraud or regulator item (§9.6,
    OD-213)."""
    names = {a.name for a in actions}
    return bool(names & SENDS or names - REVERSIBLE or (names & HIDE and fraud_or_regulator(facts)))


def is_send(actions: list[Planned]) -> bool:
    return any(a.name in SENDS for a in actions)


def on_its_own(actions: list[Planned]) -> bool:
    """A send or a draft: approved one at a time, where a draft's text is shown (§8.4, §9.5),
    never in a batch. `pending` and `scan_pending` list them under "sends" (the CLI's key)."""
    return any(a.name in outbound_plan.OUTBOUND for a in actions)


def _actions(item: sqlite3.Row) -> list[Planned]:
    p: dict[str, Any] = json.loads(item["proposal"] or "{}")
    return [Planned.from_json(a) for a in p.get("actions", [])]


def queued_answer(item: sqlite3.Row) -> bool:
    """An answer from Slack waiting for step-up at `awaiting_stepup` (answers.py), not an approval:
    told apart by the item's state, never by the caller (V1.2 review, 2026-09-30)."""
    p: dict[str, Any] = json.loads(item["proposal"] or "{}")
    return item["status"] == Status.AWAITING_STEPUP and "answer_pending" in p


ANSWER_WAITS = "this is your answer from Slack waiting for step-up: finish it with ecf answer {}"


def _facts(item: sqlite3.Row) -> dict[str, Any]:
    return json.loads(item["facts"] or "{}")


# Requesting: an approval for an item's planned actions, with its grant issued.


def request(
    conn: sqlite3.Connection,
    clock: Clock,
    sid: str,
    actions: list[Planned],
    *,
    member: str = "",
    card: bool = True,
) -> str:
    """A proposed item needs a person: record the proposal, issue its grant and post its card.
    Returns the grant ID. (Called by the rules from V1.3; by tests in V1.2.)"""
    if not actions:
        raise InvalidInputError("nothing to approve")
    item = _item(conn, sid)
    ctx = TransitionContext(stage=_stage(conn, item))
    # refuse before writing: a refused request must not replace the live card's proposal
    if item["status"] != Status.PROPOSED:
        raise ConflictError(f"this email is {item['status']}, not proposed",
                            current=str(item["status"]))  # fmt: skip
    check_transition(Status.PROPOSED, Status.AWAITING_APPROVAL, ctx)
    kept: dict[str, Any] = json.loads(item["proposal"] or "{}")  # the plan's reasons stay (V1.3)
    doc = kept | {"actions": [a.to_json() for a in actions]}
    with write_tx(conn):
        conn.execute("UPDATE items SET proposal = ?, updated_at = ? WHERE stable_id = ?",
                     (json.dumps(doc), to_ts(clock.now()), sid))  # fmt: skip
    items.transition(conn, clock, StableId(sid), Status.AWAITING_APPROVAL, ctx, actor="service",
                     expected=Status.PROPOSED)  # fmt: skip
    grant_id = _issue(conn, clock, _item(conn, sid), actions)
    if card:  # during a large backlog they're listed on digests instead (§5.3; V1.3 step 7)
        _card(conn, clock, _item(conn, sid), grant_id, member=member)
    return grant_id


def _issue(
    conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, actions: list[Planned]
) -> str:
    with write_tx(conn):
        return _insert_grant(conn, clock, item, actions)


def _insert_grant(
    conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, actions: list[Planned]
) -> str:
    """Inside the caller's write transaction."""
    grant_id = new_grant_id()
    ttl = _ttl(conn, item["address_id"], send=is_send(actions))
    conn.execute(
        "INSERT INTO grants (grant_id, stable_id, action_hash, content_hash, principal, status,"
        " expires_at) VALUES (?, ?, ?, ?, 'os_user', 'issued', ?)",
        (grant_id, item["stable_id"], action_hash(item["stable_id"], item["content_hash"],
         actions), item["content_hash"], to_ts(clock.now() + ttl)),
    )  # fmt: skip
    _audit(conn, clock, item, "approval.requested", "service",
           {"grant_id": grant_id, "actions": [a.to_json() for a in actions]})  # fmt: skip
    return grant_id


# Deciding: approve, reject and cancel, from the CLI or Slack, and what an approved item
# does next (run at once, wait out the send delay, or queue for this computer).


@stepup.purpose("approve")
def _describe_approve(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    g = _grant(conn, str(target.get("grant_id", "")))
    item = _item(conn, g.stable_id)
    s = inbox.summary(item)
    sender = cards.short_sender(s["sender"], 60)
    prompt = (f"ecf: {describe(_actions(item))}: the email from {sender},"
              f' "{s["subject"][:60]}" on {item["address_id"]}')  # fmt: skip
    return stepup.Bound(stepup.digest("approve", g.grant_id, g.action_hash, item["status"]), prompt)


@stepup.purpose("approve_batch")
def _describe_batch(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    ids = sorted(str(g) for g in target.get("grant_ids", []))
    grants = [_grant(conn, g) for g in ids]
    state = [(g.grant_id, g.action_hash, _item(conn, g.stable_id)["status"]) for g in grants]
    prompt = f"ecf: approve {len(ids)} waiting action(s), none of them a send or a draft"
    return stepup.Bound(stepup.digest("approve_batch", state), prompt)


def approve(
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    ref: str,
    *,
    actor: str,
    grant_id: str | None = None,
    nonce: str | None = None,
    batch_verified: bool = False,
) -> dict[str, Any]:
    item = inbox.find(conn, ref)
    status = Status(item["status"])
    if status is Status.EXPIRED and not actor.startswith("slack:") and _actions(item):
        item, status = _reoffer(conn, clock, item, actor), Status.AWAITING_APPROVAL
    if status not in (Status.AWAITING_APPROVAL, Status.AWAITING_STEPUP):
        raise ConflictError(f"this email is {status}: nothing to approve", current=str(status))
    if queued_answer(item):
        raise ConflictError(ANSWER_WAITS.format(item["stable_id"][:8]), current=str(status))
    g = _open_grant(conn, clock, item, grant_id)
    actions = _actions(item)
    sid = StableId(item["stable_id"])
    short, draft = sid[: cards.SHORT_ID], _draft_text(actions)
    if actor.startswith("slack:") and draft:  # a draft is approved where it is shown whole
        if not _drafts_refreshed(conn):  # until then a card an earlier version cut may be live
            raise InvalidInputError(
                f"this card is being updated: click again in a minute, or"
                f" approve it at your computer: ecf approve {short}"
            )
        if _refresh_skipped(conn, sid) or not slack_render.fits(draft):
            raise InvalidInputError(
                f"the card may not show the whole draft. Approve it at your"
                f" computer: ecf approve {short}"
            )
    # anything waiting at awaiting_stepup goes through step-up, however it got there
    risky = needs_stepup(_facts(item), actions) or status is Status.AWAITING_STEPUP
    if risky and not batch_verified:
        if actor.startswith("slack:"):
            return _queue_for_computer(conn, clock, notifier, item, g, actor)
        stepup.consume(conn, clock, "approve", {"grant_id": g.grant_id}, nonce)
    if risky:
        if status is Status.AWAITING_APPROVAL:
            items.transition(conn, clock, sid, Status.AWAITING_STEPUP,
                             TransitionContext(origin=Origin.APPROVAL), actor=actor,
                             expected=status)  # fmt: skip
        items.transition(conn, clock, sid, Status.APPROVED,
                         TransitionContext(origin=Origin.APPROVAL, stepup_verified=True),
                         actor=actor, expected=Status.AWAITING_STEPUP)  # fmt: skip
    else:
        items.transition(conn, clock, sid, Status.APPROVED, TransitionContext(reversible=True),
                         actor=actor, expected=status)  # fmt: skip
    _set_grant(conn, g.grant_id, "issued", "approved", nonce if risky else None)
    _audit(conn, clock, item, "approval.approved", actor, {"grant_id": g.grant_id}, tx=True)
    return _after_approved(conn, clock, _item(conn, sid), g.grant_id, actions)


def _reoffer(conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, actor: str) -> sqlite3.Row:
    """An approval that expired twice waits at `expired` (§6.2); `ecf approve <id>` offers it
    again with a fresh grant, then approves as usual (operator decision 2026-09-30, OD-223)."""
    sid = StableId(item["stable_id"])
    items.transition(conn, clock, sid, Status.AWAITING_APPROVAL,
                     TransitionContext(origin=Origin.APPROVAL), actor=actor,
                     expected=Status.EXPIRED)  # fmt: skip
    grant = _issue(conn, clock, _item(conn, sid), _actions(item))
    _audit(conn, clock, item, "approval.reoffered", actor, {"grant_id": grant}, tx=True)
    if not _marked(item):  # old mail: `ecf approve` re-offers it, without a card (OD-507 §2)
        _card(conn, clock, _item(conn, sid), grant, title="Offered again")
    return _item(conn, sid)


def reissue_after_import(conn: sqlite3.Connection, clock: Clock, sid: str) -> str:
    """An imported item waiting for a decision gets a fresh grant here (grants never travel in a
    bundle, OD-358); `post_held_cards` posts its card once Slack routes exist again."""
    item = _item(conn, sid)
    grant = _issue(conn, clock, item, _actions(item))
    data = {"grant_id": grant, "why": "import"}
    _audit(conn, clock, item, "approval.reoffered", "service", data, tx=True)
    return grant


def _queue_for_computer(
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    item: sqlite3.Row,
    g: Grant,
    actor: str,
) -> dict[str, Any]:
    if item["status"] == Status.AWAITING_APPROVAL:
        items.transition(conn, clock, StableId(item["stable_id"]), Status.AWAITING_STEPUP,
                         TransitionContext(origin=Origin.APPROVAL), actor=actor,
                         expected=Status.AWAITING_APPROVAL)  # fmt: skip
        _audit(conn, clock, item, "approval.queued", actor, {"grant_id": g.grant_id}, tx=True)
    waiting = queued_count(conn)
    short = item["stable_id"][: cards.SHORT_ID]
    head = f"[ecf-alert] Operator Input Needed: approval waiting ({item['address_id']})"
    text = f"Confirm with Touch ID or your password: ecf approve {short} ({waiting} waiting)"
    notifier.notify(head, text)
    alerts.email(conn, clock, "operator_input", head, text)
    _edit(conn, clock, item, f"Queued for your computer ({waiting} waiting)",
          [Button(REJECT, "Reject", g.grant_id)])  # fmt: skip
    return {"status": Status.AWAITING_STEPUP.value, "waiting": waiting}


def queued_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT count(*) FROM items WHERE status = 'awaiting_stepup'"
                            ).fetchone()[0])  # fmt: skip


def _after_approved(
    conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, grant_id: str,
    actions: list[Planned],
) -> dict[str, Any]:  # fmt: skip
    sid = StableId(item["stable_id"])
    on_high = is_send(actions) and _sensitivity(conn, item) == "high"
    if on_high:
        items.transition(conn, clock, sid, Status.DELAYED, TransitionContext(send_on_high=True),
                         actor="service", expected=Status.APPROVED)  # fmt: skip
        with write_tx(conn):
            conn.execute("INSERT INTO delays (stable_id, grant_id, remaining_s, created_at)"
                         " VALUES (?, ?, ?, ?)",
                         (sid, grant_id, SEND_DELAY_S, to_ts(clock.now())))  # fmt: skip
        _announce_delay(conn, clock, _item(conn, sid), grant_id)
        return {"status": Status.DELAYED.value}
    _start(conn, clock, sid, grant_id)
    return {"status": Status.EXECUTING.value}


def _start(conn: sqlite3.Connection, clock: Clock, sid: StableId, grant_id: str) -> None:
    items.transition(conn, clock, sid, Status.EXECUTING, TransitionContext(), actor="service")
    item = _item(conn, sid)
    jobs.enqueue(conn, clock, jobs.Queue.ACTIONS, AddressId(item["address_id"]),
                 {"stable_id": sid, "grant_id": grant_id}, timeout_s=120,
                 max_attempts=EXECUTE_ATTEMPTS)  # fmt: skip
    jobs.make_due(conn, clock, item["address_id"])
    _edit(conn, clock, item, f"Approved: {describe(_actions(item))} (running)", [])


def reject(conn: sqlite3.Connection, clock: Clock, ref: str, *, actor: str) -> dict[str, Any]:
    item = inbox.find(conn, ref)
    status = Status(item["status"])
    if status not in (Status.AWAITING_APPROVAL, Status.AWAITING_STEPUP):
        raise ConflictError(f"this email is {status}: nothing to reject", current=str(status))
    if queued_answer(item):  # the edge is for approvals only (§6.2)
        raise ConflictError(ANSWER_WAITS.format(item["stable_id"][:8]), current=str(status))
    items.transition(conn, clock, StableId(item["stable_id"]), Status.REJECTED,
                     TransitionContext(origin=Origin.APPROVAL), actor=actor,
                     expected=status)  # fmt: skip
    _void(conn, item["stable_id"])
    _audit(conn, clock, item, "approval.rejected", actor, {}, tx=True)
    _edit(conn, clock, _item(conn, item["stable_id"]), "Rejected: nothing was done", [])
    return {"status": Status.REJECTED.value}


def cancel(conn: sqlite3.Connection, clock: Clock, ref: str, *, actor: str) -> dict[str, Any]:
    """Cancel a send waiting out its 10-minute delay."""
    item = inbox.find(conn, ref)
    items.transition(conn, clock, StableId(item["stable_id"]), Status.CANCELLED,
                     TransitionContext(), actor=actor, expected=Status.DELAYED)  # fmt: skip
    with write_tx(conn):
        conn.execute("DELETE FROM delays WHERE stable_id = ?", (item["stable_id"],))
    _void(conn, item["stable_id"])
    _audit(conn, clock, item, "send.cancelled", actor, {}, tx=True)
    _edit(conn, clock, _item(conn, item["stable_id"]), "Cancelled: not sent", [])
    return {"status": Status.CANCELLED.value}


# `ecf approve --pending`: the approvals waiting for step-up, approved together under one.


def pending(conn: sqlite3.Connection) -> dict[str, Any]:
    """Waiting step-up approvals: up to PENDING_MAX for one batch step-up, and the sends and drafts,
    which are never batched (each needs `ecf approve <id>`, which shows a draft's text)."""
    rows = conn.execute(  # old mail has its own: `ecf approve --scan` (OD-507 §2, §3.5)
        f"SELECT * FROM items WHERE status = 'awaiting_stepup' AND NOT {marked.sql()}"  # noqa: S608
        " ORDER BY updated_at, stable_id"
    ).fetchall()
    batch: list[dict[str, Any]] = []
    sends: list[dict[str, Any]] = []
    for r in rows:
        if queued_answer(r) or not _actions(r):
            continue  # an answer waiting for step-up (answers.py), not an approval
        entry = inbox.summary(r) | {"action": describe(_actions(r))}
        (sends if on_its_own(_actions(r)) else batch).append(entry)
    return {"batch": batch[:PENDING_MAX], "more": max(0, len(batch) - PENDING_MAX),
            "sends": sends}  # fmt: skip


def approve_pending(
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    ids: list[str],
    *,
    nonce: str | None,
) -> list[dict[str, Any]]:
    """Approve the confirmed list with one step-up (`approve --pending`); sends and drafts are
    refused."""
    if not ids or len(ids) > PENDING_MAX:
        raise InvalidInputError(f"confirm 1 to {PENDING_MAX} items")
    rows = [inbox.find(conn, i) for i in ids]
    grants: list[Grant] = []
    for r in rows:
        if r["status"] != Status.AWAITING_STEPUP or not _actions(r):
            raise ConflictError(f"{r['stable_id'][:8]} isn't waiting for step-up")
        if on_its_own(_actions(r)):
            raise InvalidInputError(f"{r['stable_id'][:8]} is a send or a draft:"
                                    " approve it on its own")  # fmt: skip
        grants.append(_open_grant(conn, clock, r, None))
    stepup.consume(conn, clock, "approve_batch",
                   {"grant_ids": sorted(g.grant_id for g in grants)}, nonce)  # fmt: skip
    return [approve(conn, clock, notifier, r["stable_id"], actor="os_user", grant_id=g.grant_id,
                    nonce=nonce, batch_verified=True) | {"id": r["stable_id"]}
            for r, g in zip(rows, grants, strict=True)]  # fmt: skip


# Approving a scan's or reprocess's waiting approvals (`ecf approve --scan`, OD-507 §3.5, P9):
# old mail gets no card, so the run's list is shown and approved with one step-up; sends and
# drafts apart.


def scan_pending(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """A scan's or reprocess's waiting approvals (old mail gets no card; `ecf approve --scan`):
    each with its sender, subject and actions; an expired one too, re-offered when approved (as
    `ecf approve <id>` does; review M4). Sends and drafts are listed apart: each needs
    `ecf approve <id>`, which shows a draft's text.
    Not part of "Approve all"."""
    doc = scans.find(conn, run_id)
    if doc is None:
        raise NotFoundError(f"no scan or reprocess run {run_id}")
    rows = conn.execute(
        "SELECT * FROM items WHERE status IN ('awaiting_approval', 'awaiting_stepup', 'expired')"  # noqa: S608
        f" AND {scans.OF_RUN} ORDER BY created_at, stable_id",
        {"run": doc["run_id"]}).fetchall()  # fmt: skip
    batch: list[dict[str, Any]] = []
    sends: list[dict[str, Any]] = []
    for r in rows:
        if queued_answer(r) or not _actions(r):
            continue
        entry = inbox.summary(r) | {"action": describe(_actions(r))}
        (sends if on_its_own(_actions(r)) else batch).append(entry)
    return {"run_id": doc["run_id"], "address": doc["email"], "batch": batch, "sends": sends}


@stepup.purpose("approve_scan")
def _describe_scan(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    """Bound to the exact grant set, as `approve_batch` (P9)."""
    ids = sorted(str(g) for g in target.get("grant_ids", []))
    grants = [_grant(conn, g) for g in ids]
    state = [(g.grant_id, g.action_hash, _item(conn, g.stable_id)["status"]) for g in grants]
    run_id = str(target.get("run_id", ""))
    prompt = f"ecf: approve {len(ids)} action(s) on old mail (run {run_id[:12]}), none a send"
    return stepup.Bound(stepup.digest("approve_scan", run_id, state), prompt)


def approve_scan(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, run_id: str,
                 ids: list[str], *, nonce: str | None) -> list[dict[str, Any]]:  # fmt: skip
    """Approve the confirmed list of one run with one step-up; each must still be the run's,
    waiting, and not a send or a draft."""
    listed = {e["id"] for e in scan_pending(conn, run_id)["batch"]}
    if not ids or any(i not in listed for i in ids):
        raise ConflictError("the run's waiting approvals changed: run ecf approve --scan again")
    rows = [inbox.find(conn, i) for i in ids]
    rows = [_reoffer(conn, clock, r, "os_user") if r["status"] == Status.EXPIRED else r
            for r in rows]  # a fresh grant, before the step-up binds them all (M4)  # fmt: skip
    grants = [_open_grant(conn, clock, r, None) for r in rows]
    stepup.consume(conn, clock, "approve_scan", {"run_id": run_id,
                   "grant_ids": sorted(g.grant_id for g in grants)}, nonce)  # fmt: skip
    return [approve(conn, clock, notifier, r["stable_id"], actor="os_user", grant_id=g.grant_id,
                    nonce=nonce, batch_verified=True) | {"id": r["stable_id"]}
            for r, g in zip(rows, grants, strict=True)]  # fmt: skip


# The 10-minute send delay: counted down in awake time, announced on the card.


def advance_delays(conn: sqlite3.Connection, clock: Clock, awake_s: float, *, woke: bool) -> int:
    """Count down delayed sends by `awake_s` (monotonic time since the last tick, which excludes
    sleep); start the ones that ran out. After a wake, re-announce the ones still waiting."""
    started = 0
    with write_tx(conn):
        conn.execute("UPDATE delays SET remaining_s = max(0, remaining_s - ?)", (max(awake_s, 0),))
    for d in conn.execute("SELECT * FROM delays ORDER BY created_at").fetchall():
        sid = StableId(d["stable_id"])
        if _item(conn, sid)["status"] != Status.DELAYED:  # closed meanwhile (V1.2 review)
            with write_tx(conn):
                conn.execute("DELETE FROM delays WHERE stable_id = ?", (sid,))
            continue
        if d["remaining_s"] <= 0:
            if pause.is_paused(conn, _item(conn, sid)["address_id"]):
                continue  # starts on resume
            with write_tx(conn):
                conn.execute("DELETE FROM delays WHERE stable_id = ?", (sid,))
            _start(conn, clock, sid, d["grant_id"])
            started += 1
        elif woke:
            _announce_delay(conn, clock, _item(conn, sid), d["grant_id"])
    return started


def _announce_delay(
    conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, grant_id: str
) -> None:
    row = conn.execute("SELECT remaining_s, announced_at FROM delays WHERE stable_id = ?",
                       (item["stable_id"],)).fetchone()  # fmt: skip
    minutes = max(1, round((row["remaining_s"] if row else SEND_DELAY_S) / 60))
    _edit(conn, clock, item, f"Sending in {minutes} minute(s) unless you cancel",
          [Button(CANCEL, "Cancel", item["stable_id"], "danger")])  # fmt: skip
    if row is not None and row["announced_at"] is None:  # emailed once, when the delay starts
        aid, short = item["address_id"], item["stable_id"][: cards.SHORT_ID]
        alerts.email(conn, clock, "operator_input",
                     alerts.title("operator_input", f"send scheduled in 10 minutes ({aid})"),
                     f"{aid}: an approved send (item {short}) goes out in {minutes} minute(s)."
                     f" Cancel it: ecf cancel {short}, or Cancel in Slack.")  # fmt: skip
    with write_tx(conn):
        conn.execute("UPDATE delays SET announced_at = ? WHERE stable_id = ?",
                     (to_ts(clock.now()), item["stable_id"]))  # fmt: skip
    del grant_id


# Expiry and requeue: approvals whose grant ran out are offered once more, and a failed
# or stuck action can run again under a new grant (§6.2).


def expire(conn: sqlite3.Connection, clock: Clock) -> int:
    """Approvals whose grant ran out: expired; offered again once with a fresh grant (§6.2)."""
    now = to_ts(clock.now())
    rows = conn.execute(
        "SELECT i.*, g.grant_id FROM items i JOIN grants g USING (stable_id)"
        " WHERE i.status IN ('awaiting_approval', 'awaiting_stepup') AND g.status = 'issued'"
        " AND g.expires_at <= ? ORDER BY i.stable_id",
        (now,),
    ).fetchall()
    for r in rows:
        try:
            _expire_one(conn, clock, r)
        except EcfError as exc:  # e.g. a click decided it meanwhile: the rest still expire
            log.warning("approval.expire_skipped", stable_id=str(r["stable_id"])[:8],
                        error_type=type(exc).__name__)  # fmt: skip
    return len(rows)


def _expire_one(conn: sqlite3.Connection, clock: Clock, r: sqlite3.Row) -> None:
    sid = StableId(r["stable_id"])
    ctx = TransitionContext(origin=Origin.APPROVAL)
    items.transition(conn, clock, sid, Status.EXPIRED, ctx, actor="service",
                     expected=Status(r["status"]))  # fmt: skip
    _void(conn, sid)
    item = _item(conn, sid)
    _audit(conn, clock, item, "approval.expired", "service", {"grant_id": r["grant_id"]},
           tx=True)  # fmt: skip
    if item["expiry_count"] >= 2:  # a second expiry: the daily summary lists it (§6.2)
        short = item["stable_id"][:8]
        _edit(conn, clock, item, f"Expired twice: ecf approve {short} offers it again, or"
              f" ecf item resolve {short}", [])  # fmt: skip
        return
    items.transition(conn, clock, sid, Status.AWAITING_APPROVAL,
                     TransitionContext(origin=Origin.APPROVAL), actor="service",
                     expected=Status.EXPIRED)  # fmt: skip
    grant = _issue(conn, clock, _item(conn, sid), _actions(item))
    if not _marked(item):  # old mail: never a card (OD-507 §2)
        _card(conn, clock, _item(conn, sid), grant, title="Expired, decide again")


@stepup.purpose("item_requeue")
def _describe_requeue(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    item = _item(conn, str(target.get("stable_id", "")))
    s = inbox.summary(item)
    return stepup.Bound(stepup.digest("item_requeue", item["stable_id"], item["status"]),
                        f"ecf: try again: {describe(_actions(item))} for the email from "
                        f'{s["sender"][:60]} on {item["address_id"]}')  # fmt: skip


def requeue(
    conn: sqlite3.Connection, clock: Clock, ref: str, *, actor: str, nonce: str | None
) -> dict[str, Any]:
    """Run a failed or stuck action again under a new grant (§6.2); a send needs step-up again."""
    item = inbox.find(conn, ref)
    sid = StableId(item["stable_id"])
    status = Status(item["status"])
    if status is Status.EXECUTING:
        busy = conn.execute(
            "SELECT 1 FROM jobs WHERE queue = 'actions' AND state IN ('queued', 'claimed')"
            " AND json_extract(payload, '$.stable_id') = ?", (sid,)).fetchone()  # fmt: skip
        if busy:
            raise ConflictError("it's still running; requeue only a stuck or failed action")
    elif status not in (Status.FAILED, Status.FAILED_UNKNOWN):
        raise ConflictError(f"this email is {status}: nothing to requeue", current=str(status))
    actions = _actions(item)
    if is_send(actions) and (status is not Status.FAILED or _may_have_gone(conn, sid)):
        raise ConflictError(
            "this send may already have gone out; ecf checks the Sent folder on the next checks"
            " where the provider keeps sent mail, else check it yourself and close it with"
            " `ecf item resolve` (a send is retried only once it surely failed; OD-322)"
        )
    if is_send(actions):
        stepup.consume(conn, clock, "item_requeue", {"stable_id": sid}, nonce)
    _void(conn, sid)
    grant_id = new_grant_id()
    with write_tx(conn):
        conn.execute(
            "INSERT INTO grants (grant_id, stable_id, action_hash, content_hash, principal, status,"
            " expires_at) VALUES (?, ?, ?, ?, 'os_user', 'approved', ?)",
            (grant_id, sid, action_hash(sid, item["content_hash"], actions), item["content_hash"],
             to_ts(clock.now() + TTL_SEND)),
        )  # fmt: skip
    if is_send(actions) and _sensitivity(conn, item) == "high":  # the delay again (OD-329)
        items.transition(conn, clock, sid, Status.DELAYED,
                         TransitionContext(requeue=True, send_on_high=True), actor=actor,
                         expected=status)  # fmt: skip
        with write_tx(conn):
            conn.execute("INSERT INTO delays (stable_id, grant_id, remaining_s, created_at)"
                         " VALUES (?, ?, ?, ?) ON CONFLICT (stable_id) DO UPDATE SET"
                         " grant_id = excluded.grant_id, remaining_s = excluded.remaining_s,"
                         " created_at = excluded.created_at, announced_at = NULL",
                         (sid, grant_id, SEND_DELAY_S, to_ts(clock.now())))  # fmt: skip
        _audit(conn, clock, item, "item.requeued", actor, {"grant_id": grant_id}, tx=True)
        _announce_delay(conn, clock, _item(conn, sid), grant_id)
        return {"status": Status.DELAYED.value}
    items.transition(conn, clock, sid, Status.EXECUTING, TransitionContext(requeue=True),
                     actor=actor, expected=status)  # fmt: skip
    jobs.enqueue(conn, clock, jobs.Queue.ACTIONS, AddressId(item["address_id"]),
                 {"stable_id": sid, "grant_id": grant_id}, timeout_s=120,
                 max_attempts=EXECUTE_ATTEMPTS)  # fmt: skip
    jobs.make_due(conn, clock, item["address_id"])
    _audit(conn, clock, item, "item.requeued", actor, {"grant_id": grant_id}, tx=True)
    return {"status": Status.EXECUTING.value}


def _may_have_gone(conn: sqlite3.Connection, sid: str) -> bool:
    """A send of this item that went or may have gone: `sent` or `unknown`, or a used grant with
    no `sent` row saying otherwise (a crash before the record; V1.2 review)."""
    if conn.execute("SELECT 1 FROM sent WHERE stable_id = ? AND status IN ('sent', 'unknown')",
                    (sid,)).fetchone():  # fmt: skip
        return True
    used = conn.execute("SELECT grant_id FROM grants WHERE stable_id = ? AND status = 'consumed'",
                        (sid,)).fetchall()  # fmt: skip
    return any(conn.execute("SELECT 1 FROM sent WHERE grant_id = ? AND status IN ('failed',"
                            " 'pending')", (g["grant_id"],)).fetchone() is None
               for g in used)  # fmt: skip


# Grants: the record that binds an approval to its item and actions.


def _grant(conn: sqlite3.Connection, grant_id: str) -> Grant:
    r = conn.execute("SELECT * FROM grants WHERE grant_id = ?", (grant_id,)).fetchone()
    if r is None:
        raise GrantInvalidError("no such grant")
    return Grant(r["grant_id"], r["stable_id"], r["action_hash"], r["status"], r["expires_at"])


def _open_grant(
    conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, grant_id: str | None
) -> Grant:
    """The item's issued grant (the one named, if any), unexpired and still bound to the
    proposal as it stands."""
    if grant_id is None:
        r = conn.execute("SELECT grant_id FROM grants WHERE stable_id = ? AND status = 'issued'",
                         (item["stable_id"],)).fetchone()  # fmt: skip
        if r is None:
            raise GrantInvalidError("no approval is open for this email")
        grant_id = str(r["grant_id"])
    g = _grant(conn, grant_id)
    expected = action_hash(item["stable_id"], item["content_hash"], _actions(item))
    if g.stable_id != item["stable_id"] or g.status != "issued" or g.action_hash != expected:
        raise GrantInvalidError("that approval is no longer open (decided, expired or changed)")
    if from_ts(g.expires_at) <= clock.now():
        raise GrantInvalidError("that approval has expired")
    return g


def _set_grant(
    conn: sqlite3.Connection, grant_id: str, frm: str, to: str, nonce_id: str | None = None
) -> None:
    """`nonce_id`: the step-up that approved it, kept on the grant (record only; enforcement is
    stepup.consume)."""
    with write_tx(conn):
        n = conn.execute("UPDATE grants SET status = ?,"
                         " stepup_nonce_id = coalesce(?, stepup_nonce_id)"
                         " WHERE grant_id = ? AND status = ?",
                         (to, nonce_id, grant_id, frm)).rowcount  # fmt: skip
    if n != 1:
        raise ConflictError("someone else decided this first")


def _void(conn: sqlite3.Connection, sid: str) -> None:
    with write_tx(conn):
        conn.execute("UPDATE grants SET status = 'voided' WHERE stable_id = ?"
                     " AND status IN ('issued', 'approved')", (sid,))  # fmt: skip


# Cards: posting and editing an item's approval card, posting cards held back while the
# backlog was high, and, once after the upgrade to OD-551, new grants and cards for waiting drafts.


def _card(
    conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, grant_id: str, *,
    member: str = "", title: str = "",
) -> None:  # fmt: skip
    route = slack_routes.route_for(conn, item["address_id"])
    if route is None:
        return
    actions = _actions(item)
    base = cards.item_card(item, mention=member)
    verb = describe(actions)
    fields = (("Action", verb), *base.fields)
    text = _draft_text(actions)
    whole = slack_render.fits(text)  # a draft is approved only where all of it is shown (§8.4)
    approve = (Button(APPROVE, f"Approve: {verb}", grant_id, "primary"),) if whole else ()
    short = item["stable_id"][: cards.SHORT_ID]
    computer_note = (
        f"The draft is too long to show here. Approve it at your computer: ecf approve {short}"
    )
    payment = cards.PAYMENT_NOTE if item_payment_or_fraud(item) else ""
    notes = [payment, "" if whole else computer_note]
    card = Card(title or f"Approve? {verb}", fields=fields, text=text,
                buttons=(*approve, Button(REJECT, "Reject", grant_id),
                         Button(cards.SHOW_EXCERPT, "Show excerpt", item["stable_id"])),
                note=" ".join(n for n in notes if n), mention=member)  # fmt: skip
    slack_out.enqueue_post(conn, clock, key=f"item:{item['stable_id']}", route=route, card=card,
                           identity=slack_routes.identity(item["address_id"]))  # fmt: skip


def _draft_text(actions: list[Planned]) -> str:
    """A proposed draft in full, labelled as model output, links not clickable (OD-317)."""
    draft = next((a for a in actions if a.name == "draft_reply" and a.payload), None)
    if draft is None or draft.payload is None:
        return ""
    return ("Draft written by ecf's model (it can be wrong; it is saved, never sent):\n"
            + cards.defang(str(draft.payload.get("text") or "")))  # fmt: skip


CARDS_PER_TICK = 20


def post_held_cards(conn: sqlite3.Connection, clock: Clock, limit: int = CARDS_PER_TICK) -> int:
    """Once the backlog is back under `BACKLOG_BATCH`, post the cards that approvals requested
    during it didn't get (§5.3): the digest's "Approve all" lists only the reversible ones that
    aren't drafts (drafts it only names), so the rest would otherwise reach Slack only when they
    expire. Each tick, a few at a time; returns how many were posted."""
    if claude_queue.backlog(conn) > claude_queue.BACKLOG_BATCH:
        return 0
    rows = conn.execute(  # old mail never gets a card (OD-507 §2)
        "SELECT i.*, g.grant_id FROM items i JOIN grants g USING (stable_id)"  # noqa: S608
        f" WHERE i.status = 'awaiting_approval' AND g.status = 'issued' AND NOT {marked.sql('i.')}"
        " ORDER BY i.updated_at, i.stable_id").fetchall()  # fmt: skip
    posted = 0
    refused: set[str] | None = None  # read only when a card is missing: it scans the dead posts
    for row in rows:
        if posted >= limit:
            break
        key = f"item:{row['stable_id']}"
        if has_card(conn, key):
            continue
        if refused is None:
            refused = slack_out.refused_keys(conn)
        if key in refused:  # left for `ecf slack repost` (OD-557)
            continue
        _card(conn, clock, _item(conn, row["stable_id"]), row["grant_id"])
        posted += 1
    return posted


def repost_cards(conn: sqlite3.Connection, clock: Clock) -> dict[str, Any]:
    """`ecf slack repost` (OD-557): put ecf's cards back in Slack.

    In order: (a0) a waiting approval card recorded in a channel the item no longer uses (or with
    no channel yet) loses that record, so `post_held_cards` posts it in the current channel; (a)
    the waiting approval cards still in Slack are drawn again with their current grant, so a card
    left showing an old grant's buttons is fixed; (b) the other cards and "Needs you" are sent
    again as last posted; (c) the dead posts of waiting approval cards are marked done, so
    `post_held_cards` posts them again over the next ticks with today's card. Question and
    escalation cards aren't put back (`ecf inbox` lists them)."""
    rows = conn.execute(
        "SELECT i.stable_id, i.address_id, m.channel FROM items i"  # noqa: S608
        " JOIN slack_messages m ON m.key = 'item:' || i.stable_id"
        f" WHERE i.status = 'awaiting_approval' AND NOT {marked.sql('i.')}"
        " AND EXISTS (SELECT 1 FROM grants g WHERE g.stable_id = i.stable_id"
        "  AND g.status = 'issued')"
        " ORDER BY i.updated_at, i.stable_id").fetchall()  # fmt: skip
    # Every path that deletes a route either deletes the item's message record too
    # (`slack_out._channel_gone`, `ecf slack remove`, restore, import) or leaves one in a channel
    # Slack said is gone (`slack_routes._forget`); `remove_address` resolves every open item
    # before `slack_routes.archive`. So a record outside the current route is unreachable, and
    # `_card` posts nothing until a route exists. An edit of the same card in flight at this
    # moment re-records the old channel when it lands; the next repost clears it again.
    moved: list[str] = []
    live: list[str] = []
    for r in rows:
        route = slack_routes.route_for(conn, r["address_id"])
        if route is None or route.channel != r["channel"]:
            moved.append(f"item:{r['stable_id']}")
        else:
            live.append(str(r["stable_id"]))
    if moved:
        with write_tx(conn):
            conn.execute("DELETE FROM slack_messages WHERE key IN (SELECT value FROM json_each(?))",
                         (json.dumps(moved),))  # fmt: skip
    drawn: set[str] = set()
    for sid in live:
        item = _item(conn, sid)  # read again: a decision may have landed since the select
        grant = _latest_grant(conn, sid)
        if item["status"] != Status.AWAITING_APPROVAL or grant is None:
            continue
        title = ("Offered again" if item["expiry_count"] >= 2
                 else "Expired, decide again" if item["expiry_count"] == 1 else "")  # fmt: skip
        _card(conn, clock, item, grant, title=title)
        drawn.add(f"item:{sid}")
    resent = slack_out.repost_all(conn, clock, exclude=frozenset(drawn))
    with write_tx(conn):
        cleared = conn.execute(
            "UPDATE jobs SET state = 'done' WHERE queue = 'slack_out' AND state = 'dead'"  # noqa: S608
            " AND json_extract(payload, '$.key') IN (SELECT 'item:' || i.stable_id FROM items i"
            f"  WHERE i.status = 'awaiting_approval' AND NOT {marked.sql('i.')}"
            "  AND EXISTS (SELECT 1 FROM grants g WHERE g.stable_id = i.stable_id"
            "   AND g.status = 'issued'))"
            " RETURNING json_extract(payload, '$.key')").fetchall()  # fmt: skip
    reposting = set(moved) | {str(c[0]) for c in cleared
                              if not slack_out.message_ref(conn, str(c[0]))}  # fmt: skip
    reposting -= _unrouted(conn, reposting)  # posted once the address has a channel
    return {"queued": len(drawn) + resent, "reposting": len(reposting),
            "not_recovered": inbox.waiting_lost(conn),
            "backlog_held": claude_queue.backlog(conn) > claude_queue.BACKLOG_BATCH}  # fmt: skip


def _unrouted(conn: sqlite3.Connection, keys: set[str]) -> set[str]:
    """The item cards among `keys` whose address has no Slack channel yet."""
    rows = conn.execute(
        "SELECT 'item:' || i.stable_id FROM items i WHERE i.stable_id IN"
        " (SELECT substr(value, 6) FROM json_each(?) WHERE value LIKE 'item:%')"
        " AND NOT EXISTS (SELECT 1 FROM routes r"
        "  WHERE r.address_id = i.address_id AND r.surface = 'slack')",
        (json.dumps(sorted(keys)),)).fetchall()  # fmt: skip
    return {str(r[0]) for r in rows}


def lost_post_kind(conn: sqlite3.Connection, key: str) -> tuple[str, str]:
    """What a Slack post that gave up was, for its notice (OD-557): the kind and the email's short
    ID, or a digest's address ("none" for anything but an email's card, an escalation, a digest
    or "Needs you")."""
    kind, _, rest = key.partition(":")
    if kind in ("burst", "rollup"):
        return ("escalation", "")
    if kind == "digest":  # its "Approve all" list is gone for good: the digest clock moved on
        return ("digest", rest.split(":", 1)[0])
    if key == "needs-you":
        return ("needs_you", "")
    row = conn.execute("SELECT * FROM items WHERE stable_id = ?", (rest,)).fetchone()
    if kind != "item" or row is None:
        return ("none", "")
    return (_item_kind(conn, row), rest[: cards.SHORT_ID])


def _item_kind(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    """A question first (an item at `needs_clarification` can still hold an issued grant, and
    only `ecf answer` works then), then an approval, a send counting down, an escalation."""
    status, granted = row["status"], _latest_grant(conn, row["stable_id"]) is not None
    if status == Status.NEEDS_CLARIFICATION or queued_answer(row):
        kind = "question"
    elif status == Status.AWAITING_APPROVAL and granted:
        kind = "approval"
    elif status == Status.AWAITING_STEPUP and granted:
        kind = "approval_stepup"
    elif status == Status.DELAYED:
        kind = "delayed"
    elif conn.execute("SELECT 1 FROM escalations WHERE stable_id = ?",
                      (row["stable_id"],)).fetchone():  # fmt: skip
        kind = "escalation"
    else:
        kind = "other_item"
    return kind


def _latest_grant(conn: sqlite3.Connection, sid: str) -> str | None:
    """The item's issued grant (the one expiring last), as `digest_actions._grant_for` reads it;
    a copy, since that module imports this one."""
    row = conn.execute("SELECT grant_id FROM grants WHERE stable_id = ? AND status = 'issued'"
                       " ORDER BY expires_at DESC LIMIT 1", (sid,)).fetchone()  # fmt: skip
    return str(row[0]) if row else None


def _edit(
    conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, title: str, buttons: list[Button]
) -> None:
    """Edit the item's card in place, if it has one."""
    key = f"item:{item['stable_id']}"
    route = slack_routes.route_for(conn, item["address_id"])
    if route is None or not has_card(conn, key):
        return
    base = cards.item_card(item)
    fields = (("Action", describe(_actions(item))), *base.fields)
    slack_out.enqueue_post(conn, clock, key=key, route=route,
                           card=Card(title, fields=fields, buttons=tuple(buttons)),
                           identity=slack_routes.identity(item["address_id"]))  # fmt: skip


def edit_card(conn: sqlite3.Connection, clock: Clock, sid: str, title: str) -> None:
    """Edit an item's card (no buttons), if it has one: for the action runner."""
    _edit(conn, clock, _item(conn, sid), title, [])


def has_card(conn: sqlite3.Connection, key: str) -> bool:
    """Posted, or queued or in flight to be posted (an edit queued behind it edits it). A post
    Slack gave up on doesn't count, so it is posted again; nor does a finished job without a
    message record (its channel went, or Slack was removed or the data imported), since a post
    that went through is recorded before its job finishes (OD-557)."""
    if slack_out.message_ref(conn, key):
        return True
    return bool(conn.execute(
        "SELECT 1 FROM jobs WHERE queue = 'slack_out' AND state IN ('queued', 'claimed')"
        " AND json_extract(payload, '$.key') = ?", (key,)).fetchone())  # fmt: skip


DRAFT_CARDS_KEY = "cards.drafts_whole"  # set once waiting drafts have new grants (OD-551)


def _drafts_refreshed(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT 1 FROM settings WHERE key = ?", (DRAFT_CARDS_KEY,)).fetchone())


def _refresh_skipped(conn: sqlite3.Connection, sid: str) -> bool:
    """Whether the refresh failed for this item (its earlier card may be live); a marker that isn't
    a list counts every item as failed, so drafts are then approved only at the computer until the
    row is fixed."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (DRAFT_CARDS_KEY,)).fetchone()
    try:
        value: Any = json.loads(row[0]) if row else []
    except ValueError:
        return True
    if not isinstance(value, list):
        return True
    return sid in {str(s) for s in cast("list[Any]", value)}


def refresh_draft_cards(conn: sqlite3.Connection, clock: Clock) -> int:
    """Once, on the first tick of a version with OD-551: each waiting draft gets a new grant, so
    the Approve button of a card an earlier version posted (which could cut a long draft) stops
    working, and a card that was posted is posted again, in place, with the whole draft. The new
    grant starts a new expiry period. An item with bad data is logged and skipped, and the marker
    is written regardless, listing the items whose refresh failed, whose drafts are then approved
    only at the computer; a database error stops the tick, so the refresh runs again. Returns how
    many cards were posted."""
    if _drafts_refreshed(conn):
        return 0
    posted = 0
    skipped: list[str] = []
    rows = conn.execute("SELECT stable_id FROM items WHERE status = 'awaiting_approval'").fetchall()
    for row in rows:
        try:
            posted += _refresh_one(conn, clock, str(row["stable_id"]))
        except (EcfError, ValueError, KeyError) as exc:  # a bad item; database errors stop the tick
            # and the refresh runs again next tick (as in expire)
            log.warning("approval.refresh_skipped", stable_id=str(row["stable_id"])[:8],
                        error_type=type(exc).__name__)  # fmt: skip
            skipped.append(str(row["stable_id"]))
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by)"
                     " VALUES (?, ?, ?, 'service')",
                     (DRAFT_CARDS_KEY, json.dumps(skipped), to_ts(clock.now())))  # fmt: skip
    return posted


def _refresh_one(conn: sqlite3.Connection, clock: Clock, sid: str) -> int:
    """One waiting item, if it is a draft: in one transaction, a new grant first, then the item's
    other open grants voided, so a failure leaves the old grant in place; nothing is done if a
    decision got there first or the item isn't a draft."""
    with write_tx(conn):
        item = _item(conn, sid)
        actions = _actions(item)
        if item["status"] != Status.AWAITING_APPROVAL or not _draft_text(actions):
            return 0
        carded = has_card(conn, f"item:{sid}")
        why = "draft card refreshed (OD-551)" if carded else "draft grant re-issued (OD-551)"
        grant = _insert_grant(conn, clock, item, actions)
        conn.execute("UPDATE grants SET status = 'voided' WHERE stable_id = ? AND status ="
                     " 'issued' AND grant_id != ?", (sid, grant))  # fmt: skip
        _audit(conn, clock, item, "approval.reoffered", "service", {"grant_id": grant, "why": why})
    if not carded:
        return 0
    _card(conn, clock, _item(conn, sid), grant)
    return 1


# Helpers shared by the sections above: an address's approval TTL, the item, its stage and
# sensitivity, and the audit row.


def _ttl(conn: sqlite3.Connection, address_id: str, *, send: bool) -> timedelta:
    """`approval_ttl_days_send` / `approval_ttl_days` for the address (OD-041)."""
    key = "approval_ttl_days_send" if send else "approval_ttl_days"
    default = TTL_SEND if send else TTL_OTHER
    row = conn.execute("SELECT json_extract(overrides, '$.' || ?) FROM addresses"
                       " WHERE address_id = ?", (key, address_id)).fetchone()  # fmt: skip
    return timedelta(days=int(row[0])) if row and row[0] is not None else default


def _marked(item: sqlite3.Row) -> bool:
    return marked.is_marked(_facts(item))


def _item(conn: sqlite3.Connection, sid: str) -> sqlite3.Row:
    return inbox.find(conn, sid)


def _stage(conn: sqlite3.Connection, item: sqlite3.Row) -> Stage:
    r = conn.execute("SELECT stage FROM addresses WHERE address_id = ?",
                     (item["address_id"],)).fetchone()  # fmt: skip
    return Stage(r["stage"])


def _sensitivity(conn: sqlite3.Connection, item: sqlite3.Row) -> str:
    r = conn.execute("SELECT sensitivity FROM addresses WHERE address_id = ?",
                     (item["address_id"],)).fetchone()  # fmt: skip
    return str(r["sensitivity"])


def _audit(
    conn: sqlite3.Connection,
    clock: Clock,
    item: sqlite3.Row,
    event: str,
    actor: str,
    data: dict[str, Any],
    *,
    tx: bool = False,
) -> None:
    sql = ("INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
           " VALUES (?, ?, ?, ?, ?, 'ok', ?)")  # fmt: skip
    args = (to_ts(clock.now()), item["address_id"], item["stable_id"], event, actor,
            json.dumps(data))  # fmt: skip
    if tx:
        with write_tx(conn):
            conn.execute(sql, args)
    else:
        conn.execute(sql, args)


# Slack buttons: Approve, Reject and Cancel clicks, each run as a decision that says why
# when it's refused.


@slack_in.handles(APPROVE)
def _approve_click(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click) -> None:
    g = _grant(conn, click.ref)  # the button carries the grant; the action comes from it
    _answering(conn, clock, click, lambda: approve(conn, clock, desktop, g.stable_id,
                                                   actor=f"slack:{click.user}",
                                                   grant_id=g.grant_id))  # fmt: skip


@slack_in.handles(REJECT)
def _reject_click(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click) -> None:
    g = _grant(conn, click.ref)
    _answering(conn, clock, click,
               lambda: reject(conn, clock, g.stable_id, actor=f"slack:{click.user}"))  # fmt: skip


@slack_in.handles(CANCEL)
def _cancel_click(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click) -> None:
    _answering(conn, clock, click,
               lambda: cancel(conn, clock, click.ref, actor=f"slack:{click.user}"))  # fmt: skip


def _answering(
    conn: sqlite3.Connection,
    clock: Clock,
    click: slack_in.Click,
    decide: Callable[[], dict[str, Any]],
) -> None:
    """Run a decision; if it's refused (decided already, expired, ...), say why to you alone."""
    try:
        decide()
    except EcfError as exc:
        if click.channel:
            text = f"Not done: {exc.detail}"
            slack_out.enqueue_ephemeral(conn, clock, route=RouteRef(click.channel),
                                        user=click.user, text=text)  # fmt: skip
        raise
