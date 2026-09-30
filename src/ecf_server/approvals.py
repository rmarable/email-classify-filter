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
- `requeue`: a failed or stuck action runs again under a new grant; a send needs step-up again.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ecf.errors import ConflictError, EcfError, GrantInvalidError, InvalidInputError
from ecf.ids import AddressId, StableId, new_grant_id
from ecf.status import Status
from ecf_server import (
    cards,
    inbox,
    items,
    jobs,
    pause,
    slack_in,
    slack_out,
    slack_routes,
    stepup,
)
from ecf_server.actions import Planned, action_hash
from ecf_server.chat import Button, Card, RouteRef
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier, NullNotifier
from ecf_server.precheck import payment_or_fraud
from ecf_server.state_machine import Origin, Stage, TransitionContext

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


# ---- what an action is --------------------------------------------------------------------------


def describe(actions: list[Planned]) -> str:
    def one(a: Planned) -> str:
        t = a.target or ""
        return {
            "label": f"label {t}",
            "flag": "flag",
            "mark_read": "mark read",
            "archive": "archive email",
            "move": f"move to {t}",
            "junk": "move to Junk",
            "draft_reply": "save a draft reply",
            "reply_template": f"send template '{t}'",
            "forward_internal": f"forward to {t}",
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


def _actions(item: sqlite3.Row) -> list[Planned]:
    p: dict[str, Any] = json.loads(item["proposal"] or "{}")
    return [Planned(str(a["name"]), a.get("target")) for a in p.get("actions", [])]


def _facts(item: sqlite3.Row) -> dict[str, Any]:
    return json.loads(item["facts"] or "{}")


# ---- requesting ---------------------------------------------------------------------------------


def request(
    conn: sqlite3.Connection, clock: Clock, sid: str, actions: list[Planned], *, member: str = ""
) -> str:
    """A proposed item needs a person: record the proposal, issue its grant and post its card.
    Returns the grant ID. (Called by the rules from V1.3; by tests in V1.2.)"""
    if not actions:
        raise InvalidInputError("nothing to approve")
    item = _item(conn, sid)
    with write_tx(conn):
        conn.execute("UPDATE items SET proposal = ?, updated_at = ? WHERE stable_id = ?",
                     (json.dumps({"actions": [a.to_json() for a in actions]}),
                      to_ts(clock.now()), sid))  # fmt: skip
    items.transition(conn, clock, StableId(sid), Status.AWAITING_APPROVAL,
                     TransitionContext(stage=_stage(conn, item)), actor="service",
                     expected=Status.PROPOSED)  # fmt: skip
    grant_id = _issue(conn, clock, _item(conn, sid), actions)
    _card(conn, clock, _item(conn, sid), grant_id, member=member)
    return grant_id


def _issue(
    conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, actions: list[Planned]
) -> str:
    grant_id = new_grant_id()
    ttl = _ttl(conn, item["address_id"], send=is_send(actions))
    with write_tx(conn):
        conn.execute(
            "INSERT INTO grants (grant_id, stable_id, action_hash, content_hash, principal, status,"
            " expires_at) VALUES (?, ?, ?, ?, 'os_user', 'issued', ?)",
            (grant_id, item["stable_id"], action_hash(item["stable_id"], item["content_hash"],
             actions), item["content_hash"], to_ts(clock.now() + ttl)),
        )  # fmt: skip
        _audit(conn, clock, item, "approval.requested", "service",
               {"grant_id": grant_id, "actions": [a.to_json() for a in actions]})  # fmt: skip
    return grant_id


# ---- deciding -----------------------------------------------------------------------------------


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
    prompt = f"ecf: approve {len(ids)} waiting action(s), none of them a send"
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
    if status not in (Status.AWAITING_APPROVAL, Status.AWAITING_STEPUP):
        raise ConflictError(f"this email is {status}: nothing to approve", current=str(status))
    g = _open_grant(conn, clock, item, grant_id)
    actions = _actions(item)
    sid = StableId(item["stable_id"])
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
    _set_grant(conn, g.grant_id, "issued", "approved")
    _audit(conn, clock, item, "approval.approved", actor, {"grant_id": g.grant_id}, tx=True)
    return _after_approved(conn, clock, _item(conn, sid), g.grant_id, actions)


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
    notifier.notify(f"[ecf-alert] Operator Input Needed: approval waiting ({item['address_id']})",
                    f"Confirm with Touch ID or your password: ecf approve {short}"
                    f" ({waiting} waiting)")  # fmt: skip
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
    _edit(conn, clock, item, f"Approved: {describe(_actions(item))} (running)", [])


def reject(conn: sqlite3.Connection, clock: Clock, ref: str, *, actor: str) -> dict[str, Any]:
    item = inbox.find(conn, ref)
    status = Status(item["status"])
    if status not in (Status.AWAITING_APPROVAL, Status.AWAITING_STEPUP):
        raise ConflictError(f"this email is {status}: nothing to reject", current=str(status))
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


# ---- approve --pending ---------------------------------------------------------------------------


def pending(conn: sqlite3.Connection) -> dict[str, Any]:
    """Waiting step-up approvals: up to PENDING_MAX non-sends for one batch step-up, and the sends,
    which are never batched (each needs `ecf approve <id>`)."""
    rows = conn.execute(
        "SELECT * FROM items WHERE status = 'awaiting_stepup' ORDER BY updated_at, stable_id"
    ).fetchall()
    batch: list[dict[str, Any]] = []
    sends: list[dict[str, Any]] = []
    for r in rows:
        if not _actions(r):
            continue  # an answer waiting for step-up (answers.py), not an approval
        entry = inbox.summary(r) | {"action": describe(_actions(r))}
        (sends if is_send(_actions(r)) else batch).append(entry)
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
    """Approve the confirmed list with one step-up (`approve --pending`); sends are refused."""
    if not ids or len(ids) > PENDING_MAX:
        raise InvalidInputError(f"confirm 1 to {PENDING_MAX} items")
    rows = [inbox.find(conn, i) for i in ids]
    grants: list[Grant] = []
    for r in rows:
        if r["status"] != Status.AWAITING_STEPUP or not _actions(r):
            raise ConflictError(f"{r['stable_id'][:8]} isn't waiting for step-up")
        if is_send(_actions(r)):
            raise InvalidInputError(f"{r['stable_id'][:8]} is a send: approve it on its own")
        grants.append(_open_grant(conn, clock, r, None))
    stepup.consume(conn, clock, "approve_batch",
                   {"grant_ids": sorted(g.grant_id for g in grants)}, nonce)  # fmt: skip
    return [approve(conn, clock, notifier, r["stable_id"], actor="os_user",
                    grant_id=g.grant_id, batch_verified=True) | {"id": r["stable_id"]}
            for r, g in zip(rows, grants, strict=True)]  # fmt: skip


# ---- the 10-minute delay -------------------------------------------------------------------------


def advance_delays(conn: sqlite3.Connection, clock: Clock, awake_s: float, *, woke: bool) -> int:
    """Count down delayed sends by `awake_s` (monotonic time since the last tick, which excludes
    sleep); start the ones that ran out. After a wake, re-announce the ones still waiting."""
    started = 0
    with write_tx(conn):
        conn.execute("UPDATE delays SET remaining_s = max(0, remaining_s - ?)", (max(awake_s, 0),))
    for d in conn.execute("SELECT * FROM delays ORDER BY created_at").fetchall():
        sid = StableId(d["stable_id"])
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
    row = conn.execute("SELECT remaining_s FROM delays WHERE stable_id = ?",
                       (item["stable_id"],)).fetchone()  # fmt: skip
    minutes = max(1, round((row["remaining_s"] if row else SEND_DELAY_S) / 60))
    _edit(conn, clock, item, f"Sending in {minutes} minute(s) unless you cancel",
          [Button(CANCEL, "Cancel", item["stable_id"], "danger")])  # fmt: skip
    with write_tx(conn):
        conn.execute("UPDATE delays SET announced_at = ? WHERE stable_id = ?",
                     (to_ts(clock.now()), item["stable_id"]))  # fmt: skip
    del grant_id


# ---- expiry and requeue --------------------------------------------------------------------------


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
        sid = StableId(r["stable_id"])
        ctx = TransitionContext(origin=Origin.APPROVAL)
        items.transition(conn, clock, sid, Status.EXPIRED, ctx, actor="service",
                         expected=Status(r["status"]))  # fmt: skip
        _void(conn, sid)
        item = _item(conn, sid)
        _audit(conn, clock, item, "approval.expired", "service", {"grant_id": r["grant_id"]},
               tx=True)  # fmt: skip
        if item["expiry_count"] >= 2:  # a second expiry: the daily summary lists it (§6.2)
            _edit(conn, clock, item, "Expired twice: see the daily summary or ecf inbox", [])
            continue
        items.transition(conn, clock, sid, Status.AWAITING_APPROVAL,
                         TransitionContext(origin=Origin.APPROVAL), actor="service",
                         expected=Status.EXPIRED)  # fmt: skip
        grant = _issue(conn, clock, _item(conn, sid), _actions(item))
        _card(conn, clock, _item(conn, sid), grant, title="Expired, decide again")
    return len(rows)


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
    ran = conn.execute(
        "SELECT 1 FROM grants WHERE stable_id = ? AND status = 'consumed' AND action_hash = ?",
        (sid, action_hash(sid, item["content_hash"], actions)),
    ).fetchone()
    if is_send(actions) and (status is Status.FAILED_UNKNOWN or ran):
        raise ConflictError(
            "this send may already have gone out; check the Sent folder, then close it with"
            " `ecf item resolve` (a send is never retried unchecked)"
        )  # until V1.5 reconciles against Sent (§6.2)
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
    items.transition(conn, clock, sid, Status.EXECUTING, TransitionContext(requeue=True),
                     actor=actor, expected=status)  # fmt: skip
    jobs.enqueue(conn, clock, jobs.Queue.ACTIONS, AddressId(item["address_id"]),
                 {"stable_id": sid, "grant_id": grant_id}, timeout_s=120,
                 max_attempts=EXECUTE_ATTEMPTS)  # fmt: skip
    _audit(conn, clock, item, "item.requeued", actor, {"grant_id": grant_id}, tx=True)
    return {"status": Status.EXECUTING.value}


# ---- grants -------------------------------------------------------------------------------------


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


def _set_grant(conn: sqlite3.Connection, grant_id: str, frm: str, to: str) -> None:
    with write_tx(conn):
        n = conn.execute("UPDATE grants SET status = ? WHERE grant_id = ? AND status = ?",
                         (to, grant_id, frm)).rowcount  # fmt: skip
    if n != 1:
        raise ConflictError("someone else decided this first")


def _void(conn: sqlite3.Connection, sid: str) -> None:
    with write_tx(conn):
        conn.execute("UPDATE grants SET status = 'voided' WHERE stable_id = ?"
                     " AND status IN ('issued', 'approved')", (sid,))  # fmt: skip


# ---- cards --------------------------------------------------------------------------------------


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
    card = Card(title or f"Approve? {verb}", fields=fields,
                buttons=(Button(APPROVE, f"Approve: {verb}", grant_id, "primary"),
                         Button(REJECT, "Reject", grant_id),
                         Button(cards.SHOW_EXCERPT, "Show excerpt", item["stable_id"])),
                note=cards.PAYMENT_NOTE if payment_or_fraud(_facts(item)) else "",
                mention=member)  # fmt: skip
    slack_out.enqueue_post(conn, clock, key=f"item:{item['stable_id']}", route=route, card=card,
                           identity=slack_routes.identity(item["address_id"]))  # fmt: skip


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
    """Posted, or queued to be posted (an edit queued behind it edits it)."""
    if slack_out.message_ref(conn, key):
        return True
    return bool(conn.execute(
        "SELECT 1 FROM jobs WHERE queue = 'slack_out' AND json_extract(payload, '$.key') = ?",
        (key,)).fetchone())  # fmt: skip


# ---- helpers ------------------------------------------------------------------------------------


def _ttl(conn: sqlite3.Connection, address_id: str, *, send: bool) -> timedelta:
    """`approval_ttl_days_send` / `approval_ttl_days` for the address (OD-041)."""
    key = "approval_ttl_days_send" if send else "approval_ttl_days"
    default = TTL_SEND if send else TTL_OTHER
    row = conn.execute("SELECT json_extract(overrides, '$.' || ?) FROM addresses"
                       " WHERE address_id = ?", (key, address_id)).fetchone()  # fmt: skip
    return timedelta(days=int(row[0])) if row and row[0] is not None else default


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


# ---- Slack buttons ----------------------------------------------------------------------------


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
