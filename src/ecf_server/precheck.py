"""The scheduled pre-check's model-free rules (SPEC §5.4). V1.1 step 10.

Runs on items just created by a fetch, before any model. Each trigger that fired adds its actions
(SPEC §5.4); a message with several gets them all. Nothing is ever hidden, moved, answered or sent.

- fraud trigger       -> label(suspicious), flag, escalate
- weak fraud signal   -> label(suspicious), flag, and a digest section (OD-062, OD-068)
- regulator trigger   -> label(regulatory), flag, escalate
- unverified payment  -> label(unverified_sender), flag, and a digest section (rule 1a)

The decision is stored on the item (`prechecked`, and `facts.precheck`) and audited. Items stay at
`new` (§5.4: no status change); the model check picks them up later. In shadow, nothing is done to
the mailbox; outside shadow, label and flag run under a grant (actions.py). Pause never stops the
pre-check (§5.4). Each escalation is queued in `escalations`, and the Slack thread posts it
(`escalations.py`, V1.2); V1.1 recorded them only as pending.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ecf_server import actions
from ecf_server.actions import Planned
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.mail import MailSource

# (trigger, actions, reason shown on the card, goes to the digest instead of a thread)
TABLE: tuple[tuple[str, tuple[Planned, ...], str, bool], ...] = (
    (
        "fraud",
        (Planned("label", "suspicious"), Planned("flag"), Planned("escalate")),
        "possible fraud",
        False,
    ),
    ("fraud_weak", (Planned("label", "suspicious"), Planned("flag")), "weak fraud signal", True),
    (
        "regulator",
        (Planned("label", "regulatory"), Planned("flag"), Planned("escalate")),
        "regulatory mail",
        False,
    ),
    (
        "unverified_payment",
        (Planned("label", "unverified_sender"), Planned("flag")),
        "payment mail from an unverified sender",
        True,
    ),
)


@dataclass
class Decision:
    actions: list[Planned] = field(default_factory=list[Planned])
    reasons: list[str] = field(default_factory=list[str])
    escalate: bool = False
    digest: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "actions": [a.to_json() for a in self.actions],
            "reasons": self.reasons,
            "escalate": self.escalate,
            "digest": self.digest,
        }


def payment_or_fraud(facts: dict[str, Any]) -> bool:
    """A "payment or fraud item" for step-up rules (§9.6): a payment keyword, any fraud trigger
    (weak ones and lookalike domains included), an unverified payment sender, or quarantine.
    The classifier's `payment_related` joins this in V1.3."""
    t: dict[str, Any] = facts.get("triggers") or {}
    return bool(
        facts.get("payment_keyword")
        or facts.get("quarantined")
        or any(t.get(k) for k in ("fraud", "fraud_weak", "unverified_payment", "lookalikes"))
    )


def fired(facts: dict[str, Any]) -> set[str]:
    """The trigger names that fired, as the rules engine's `trigger:` operand reads them."""
    t: dict[str, Any] = facts.get("triggers") or {}
    return {name for name, *_ in TABLE if t.get(name)}


def decide(facts: dict[str, Any]) -> Decision:
    d = Decision()
    names = fired(facts)
    if facts.get("quarantined"):
        names.add("fraud")  # §5.1: a quarantined message is escalated for a person
    for trigger, planned, reason, digest in TABLE:
        if trigger not in names:
            continue
        d.reasons.append(reason)
        for a in planned:
            if a not in d.actions:
                d.actions.append(a)
        d.escalate |= any(a.name == "escalate" for a in planned)
        d.digest |= digest
    d.digest &= not d.escalate  # an escalation gets its own thread, not a digest line as well
    return d


@dataclass
class Outcome:
    stable_id: str
    decision: Decision
    executed: list[str] = field(default_factory=list[str])
    skipped: str | None = None  # why nothing was done to the mailbox


def run(
    conn: sqlite3.Connection,
    clock: Clock,
    src: MailSource,
    address_id: str,
    stable_ids: list[str],
    *,
    install: str,
    max_scan_bytes: int,
) -> list[Outcome]:
    stage = conn.execute(
        "SELECT stage FROM addresses WHERE address_id = ?", (address_id,)
    ).fetchone()["stage"]
    out: list[Outcome] = []
    for sid in stable_ids:
        item = conn.execute("SELECT * FROM items WHERE stable_id = ?", (sid,)).fetchone()
        if item is None or item["prechecked"]:
            continue
        facts: dict[str, Any] = json.loads(item["facts"])
        d = decide(facts)
        o = Outcome(sid, d)
        if not d.actions:
            o.skipped = "nothing fired"
        elif stage == "shadow":
            o.skipped = "shadow: decided, not done"
        else:
            try:
                o.executed = actions.execute(
                    conn,
                    clock,
                    src,
                    item,
                    d.actions,
                    install=install,
                    max_scan_bytes=max_scan_bytes,
                )
            except actions.MessageChangedError as exc:
                o.skipped = exc.detail
        _record(conn, clock, item, facts, stage, o)
        out.append(o)
    return out


def _record(
    conn: sqlite3.Connection,
    clock: Clock,
    item: sqlite3.Row,
    facts: dict[str, Any],
    stage: str,
    o: Outcome,
) -> None:
    facts["precheck"] = o.decision.to_json() | {
        "stage": stage,
        "executed": o.executed,
        "skipped": o.skipped,
        "escalation": "queued" if o.decision.escalate else None,
        "at": to_ts(clock.now()),
    }
    with write_tx(conn):
        conn.execute(
            "UPDATE items SET prechecked = 1, facts = ?, updated_at = ? WHERE stable_id = ?",
            (json.dumps(facts, sort_keys=True), to_ts(clock.now()), item["stable_id"]),
        )
        if o.decision.escalate:  # posted by the Slack thread (escalations.py)
            conn.execute(
                "INSERT INTO escalations (stable_id, address_id, state, created_at)"
                " VALUES (?, ?, 'pending', ?) ON CONFLICT (stable_id) DO NOTHING",
                (item["stable_id"], item["address_id"], to_ts(clock.now())),
            )
        conn.execute(
            "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
            " VALUES (?, ?, ?, 'precheck.decided', 'service', 'ok', ?)",
            (
                to_ts(clock.now()),
                item["address_id"],
                item["stable_id"],
                json.dumps(
                    {
                        "reasons": o.decision.reasons,
                        "stage": stage,
                        "executed": o.executed,
                        "skipped": o.skipped,
                    }
                ),
            ),
        )
