"""Building blocks for golden scenarios: one address `ap`, items, mail and step-ups. Setup goes
through v1's own writers (create_item, transition, approvals, ...) wherever one exists."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from unittest import mock

from ecf.errors import StepupRequiredError
from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import items, probe, slack_admin, slack_in, stepup
from ecf_server.db import write_tx
from ecf_server.message import parse
from ecf_server.state_machine import GUARDS, TRANSITIONS, Origin, Stage, TransitionContext
from ecf_server.stepper import FakeStepper
from tests.golden.harness import MB, World, ts
from tests.mail_contract import message

ME = "U0ME1"
ARRIVED = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)

MARKETING: dict[str, Any] = {
    "category": "marketing",
    "priority": "low",
    "requires_action": False,
    "requires_reply": False,
    "payment_related": False,
    "deadline_mentioned": False,
    "sender_type": "vendor",
    "fraud_risk": "none",
}
INVOICE: dict[str, Any] = {
    "category": "invoice",
    "priority": "medium",
    "requires_action": True,
    "requires_reply": False,
    "payment_related": True,
    "deadline_mentioned": False,
    "sender_type": "vendor",
    "fraud_risk": "none",
}
REPLY: dict[str, Any] = {
    "category": "invoice",
    "priority": "normal",
    "requires_action": True,
    "requires_reply": True,
    "payment_related": False,
    "deadline_mentioned": False,
    "sender_type": "vendor",
    "fraud_risk": "none",
}
KNOWN_BULK: dict[str, Any] = {
    "triggers": {},
    "auth_result": "pass",
    "sender_seen_before": True,
    "bulk_signal": True,
    "bulk_corroborates": True,
}
FRAUD: dict[str, Any] = {"triggers": {"fraud": ["bank details from an unconfirmed sender"]}}


def sid(n: int) -> StableId:
    return StableId(f"{n:02x}".ljust(64, "e"))


def address(
    w: World,
    *,
    stage: str = "live",
    sensitivity: str = "standard",
    preset: str = "C",
    slack: bool = True,
) -> None:
    with write_tx(w.conn):
        w.conn.execute(
            "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at,"
            " stage) VALUES ('ap', 'ap@acme.example', ?, ?, ?, ?)",
            (sensitivity, preset, ts(w), stage),
        )
    if slack:
        with write_tx(w.conn):
            w.conn.execute(
                "INSERT INTO routes (address_id, surface, route_ref, name)"
                " VALUES ('ap', 'slack', 'CAP', 'ecf-default-ap')"
            )
            for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", ME)):
                slack_admin.put_setting(w.conn, k, v, ts(w), actor="test")
    if w.src is not None:
        r = probe.ProbeResult(
            roles={"\\Sent": "Sent", "\\Drafts": "Drafts"},
            custom_keywords=True,
            move=True,
            uidplus=True,
            condstore=True,
            append_limit=None,
            saves_sent=None,
            max_message_bytes=None,
            max_size_source=None,
        )
        with write_tx(w.conn):
            probe.store(w.conn, w.clock, "ap", "imap.acme.example", r)


def item(
    w: World,
    n: int,
    *,
    classification: dict[str, Any] | None = None,
    facts: dict[str, Any] | None = None,
    to: Status | None = Status.CLASSIFIED,
    sender: str = "news@vendor-a.example",
) -> StableId:
    """An item for message n: delivered to the fake mailbox when the world has one, created at
    `new`, given its classification, and moved to `to` (classified by default; None stays new)."""
    s = sid(n)
    cols: dict[str, Any] = {
        "facts": json.dumps(facts if facts is not None else KNOWN_BULK),
        "subject": f"Test message {n}",
        "sender": sender,
    }
    content_hash = "h"
    if w.src is not None:
        raw = message(n)
        uid = w.src.deliver(raw, ARRIVED)
        content_hash = parse(raw, max_scan_bytes=10 * MB).content_hash
        cols |= {
            "message_id": f"<contract-{n}@synthetic.acme.example>",
            "locator": json.dumps({"uid": uid, "uidvalidity": w.src.inbox().uidvalidity}),
        }
    items.create_item(
        w.conn, w.clock, stable_id=s, address_id=AddressId("ap"), content_hash=content_hash, **cols
    )
    with write_tx(w.conn):
        w.conn.execute(
            "UPDATE items SET classification = ? WHERE stable_id = ?",
            (json.dumps(classification or MARKETING), s),
        )
    if to is Status.CLASSIFIED:
        items.transition(
            w.conn, w.clock, s, Status.CLASSIFIED, TransitionContext(), actor="classifier"
        )
    return s


def cursor(w: World) -> None:
    """The check's cursor, so close_gone runs (one check has been done)."""
    assert w.src is not None
    with write_tx(w.conn):
        w.conn.execute(
            "INSERT INTO cursors (address_id, uidvalidity, last_uid, deferred_uids,"
            " version) VALUES ('ap', ?, ?, '[]', 1)",
            (w.src.inbox().uidvalidity, w.src.inbox().uidnext - 1),
        )


def status(w: World, s: str) -> str:
    return str(w.conn.execute("SELECT status FROM items WHERE stable_id = ?", (s,)).fetchone()[0])


def nonce(w: World, exc: StepupRequiredError) -> str:
    """A verified step-up for the purpose and target the refusal named."""
    issued = stepup.issue(w.conn, w.clock, FakeStepper(), exc.extra["purpose"], exc.extra["target"])
    stepup.verify(w.conn, w.clock, FakeStepper(), issued.nonce_id)
    return issued.nonce_id


# Events that need more than one call: keep a step-up refusal, answer it, click a Slack button.


def keep(fn: Callable[[World], Any]) -> Callable[[World], Any]:
    """Run fn; a step-up refusal is kept in w.vars['refusal'] for the next `stepup` event."""

    def run(w: World) -> Any:
        try:
            return fn(w)
        except StepupRequiredError as exc:
            w.vars["refusal"] = exc
            raise

    return run


def stepup_done(w: World) -> str:
    """The person passes the step-up the last refusal asked for; the nonce goes in w.vars."""
    w.vars["nonce"] = nonce(w, w.vars.pop("refusal"))
    return w.vars["nonce"]


def click(
    action: str,
    ref: Callable[[World], str],
    *,
    values: dict[str, str] | None = None,
    kind: Literal["button", "form"] = "button",
) -> Callable[[World], Any]:
    """A Slack click or form, run by its registered handler as the receiver runs it."""

    def run(w: World) -> Any:
        c = slack_in.Click(
            kind,
            action,
            ref(w),
            None if kind == "form" else "CAP",
            ME,
            values or {},
        )
        return slack_in.HANDLERS[action](w.conn, w.clock, c)

    return run


def advance(seconds: float) -> Callable[[World], Any]:
    def run(w: World) -> None:
        w.clock.advance(seconds)

    return run


def proposed(
    w: World,
    n: int,
    *,
    facts: dict[str, Any] | None = None,
    classification: dict[str, Any] | None = None,
) -> StableId:
    """An item at `proposed` (setup through items.transition, as v1's tests do)."""
    s = item(w, n, facts=facts, classification=classification)
    items.transition(w.conn, w.clock, s, Status.PROPOSED, TransitionContext(), actor="test")
    return s


def grant_of(w: World, s: str, status: str = "issued") -> str:
    row = w.conn.execute(
        "SELECT grant_id FROM grants WHERE stable_id = ? AND status = ? ORDER BY rowid DESC",
        (s, status),
    ).fetchone()
    return str(row[0])


class FakeExecutor:
    """An executor that fails `fail` times with a retryable error, then does nothing."""

    def __init__(self, fail: int = 0, error: type[Exception] = OSError) -> None:
        self.fail, self.error = fail, error

    def __call__(self, conn: Any, clock: Any, item: Any, actions: Any) -> list[str]:
        if self.fail:
            self.fail -= 1
            raise self.error("server said no")
        return ["done"]


def sends(w: World) -> None:
    """Outbound on: SMTP, templates and the forward list (test_send_actions' setup)."""
    with write_tx(w.conn):
        w.conn.execute(
            "UPDATE addresses SET outbound = 1, smtp_host = 'smtp.acme.example', smtp_port = 465"
        )
        for key, value in (
            ("templates", TEMPLATES),
            ("forward_allow_list", [{"id": "ap_lead", "address": "lead@acme.example"}]),
        ):
            w.conn.execute(
                "INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                " (?, ?, ?, 'test') ON CONFLICT (key) DO UPDATE SET"
                " value = excluded.value",
                (f"config.{key}", json.dumps(value), ts(w)),
            )


TEMPLATES: dict[str, Any] = {
    "version": 1,
    "templates": [
        {
            "id": "ack",
            "enabled": True,
            "subject": "Re: {subject}",
            "body": "Hello {sender_name},\n\nThanks, we got it.\n",
        },
    ],
}
SEND_FACTS: dict[str, Any] = {
    "triggers": {},
    "auth_result": "pass",
    "sender_seen_before": True,
    "from_count": 1,
    "sender_hash": "s1",
    "thread_root": "<root@vendor-a.example>",
}


# Reaching every open status through items.transition (setup only): the shortest path from
# `new`, each edge with the first context its guard accepts.

_CONTEXTS = tuple(
    TransitionContext(
        stage=st,
        origin=o,
        payment_or_fraud=False,
        send_on_high=h,
        reversible=True,
        assist_safe=True,
        requeue=True,
        stepup_verified=True,
        reconciled=True,
    )
    for st in (Stage.LIVE, Stage.ASSIST, Stage.SHADOW)
    for o in (Origin.APPROVAL, Origin.ANSWER)
    for h in (False, True)
)


def path_to(target: Status) -> list[tuple[Status, TransitionContext]]:
    seen: dict[Status, list[tuple[Status, TransitionContext]]] = {Status.NEW: []}
    todo = [Status.NEW]
    while todo:
        s = todo.pop(0)
        if s is target:
            return seen[s]
        for t in sorted(TRANSITIONS[s], key=lambda x: x.value):
            if t in seen or t in (Status.RESOLVED_MANUAL, Status.RESOLVED_BY_MAILBOX):
                continue
            for c in _CONTEXTS:
                guard = GUARDS.get((s, t))
                if guard is None or guard(c):
                    seen[t] = [*seen[s], (t, c)]
                    todo.append(t)
                    break
    raise AssertionError(f"no path to {target}")


def at(w: World, n: int, target: Status) -> StableId:
    s = item(w, n, to=None)
    if target is Status.NEEDS_HUMAN:  # the rounds guard reads rounds from the row: ask 3 times
        for t, c in path_to(Status.PROPOSED):
            items.transition(w.conn, w.clock, s, t, c, actor="test")
        for _ in range(2):
            for t in (Status.NEEDS_CLARIFICATION, Status.CLARIFIED, Status.PROPOSED):
                items.transition(w.conn, w.clock, s, t, TransitionContext(), actor="test")
        for t in (Status.NEEDS_CLARIFICATION, Status.NEEDS_HUMAN):
            items.transition(w.conn, w.clock, s, t, TransitionContext(), actor="test")
        return s
    for t, c in path_to(target):
        items.transition(w.conn, w.clock, s, t, c, actor="test")
    return s


def interleave(
    owner: Any,
    name: str,
    before: Callable[[World], Any],
    fn: Callable[[World], Any],
    match: Callable[..., bool] = lambda *_a, **_k: True,
) -> Callable[[World], Any]:
    """Run fn with `before` (another writer, as a concurrent request would) run once, just before
    the first call of owner.name whose arguments `match` accepts. Only the call path changes; the
    engine code doesn't."""

    def run(w: World) -> Any:
        real = getattr(owner, name)
        done: list[bool] = []

        def wrapped(*args: Any, **kw: Any) -> Any:
            if not done and match(*args, **kw):
                done.append(True)
                before(w)
            return real(*args, **kw)

        with mock.patch.object(owner, name, wrapped):
            return fn(w)

    return run


def resolve(ref: Callable[[World], str]) -> Callable[[World], Any]:
    """Another writer: `ecf item resolve` on one plain item."""
    from ecf_server import inbox  # noqa: PLC0415

    return lambda w: inbox.resolve(
        w.conn, w.clock, inbox.Selection(refs=[ref(w)]), reason="by hand", nonce=None
    )


def after(
    owner: Any,
    name: str,
    then: Callable[[World], Any],
    fn: Callable[[World], Any],
    match: Callable[..., bool] = lambda *_a, **_k: True,
) -> Callable[[World], Any]:
    """Run fn with `then` (another writer) run once, just after the first call of owner.name
    whose arguments `match` accepts returns: for a race between two commits of one call."""

    def run(w: World) -> Any:
        real = getattr(owner, name)
        done: list[bool] = []

        def wrapped(*args: Any, **kw: Any) -> Any:
            out = real(*args, **kw)
            if not done and match(*args, **kw):
                done.append(True)
                then(w)
            return out

        with mock.patch.object(owner, name, wrapped):
            return fn(w)

    return run


def to(status: Status) -> Callable[..., bool]:
    """A `match` for items.transition: the call that moves the item to `status`."""

    def match(*args: Any, **kw: Any) -> bool:
        target = kw["to"] if "to" in kw else args[3] if len(args) > 3 else None
        return target is status

    return match
