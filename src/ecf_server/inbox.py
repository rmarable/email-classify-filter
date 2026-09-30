"""Items for a person (SPEC §6.2, §10.2, §15.1; V1.2 step 7a): `ecf inbox`, `ecf item show`,
`ecf item resolve` and the counts route.

- **Short IDs:** a unique hex prefix of at least 8 characters is accepted wherever an item ID is.
- **Inbox:** open items waiting on a person: an escalation (fraud, quarantine or regulator), or a
  status only a person moves on (approval, step-up, answer, expired, failed, undo failed, held,
  delayed). Stale items first, then the oldest.
- **Resolve:** closes open items as `resolved_manual` with your reason. Payment or fraud items
  need step-up bound to the exact set (one step-up for a bulk resolve); a card in Slack is edited
  to say it was resolved.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ecf.errors import ConflictError, InvalidInputError, NotFoundError
from ecf.ids import StableId
from ecf.status import OPEN, Status
from ecf_server import cards, escalations, items, stepup
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.precheck import payment_or_fraud
from ecf_server.state_machine import TransitionContext

MIN_PREFIX = 8
REASON_MAX = 500
BULK_MAX = 500
HISTORY_MAX = 50
PERSON: frozenset[Status] = frozenset(
    {
        Status.HELD,
        Status.AWAITING_APPROVAL,
        Status.AWAITING_STEPUP,
        Status.DELAYED,
        Status.FAILED,
        Status.FAILED_UNKNOWN,
        Status.EXPIRED,
        Status.NEEDS_CLARIFICATION,
        Status.NEEDS_HUMAN,
        Status.UNDO_FAILED,
    }
)
_HEX = re.compile(r"[0-9a-f]+")


def find(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    """An item by its ID or a unique prefix of at least 8 characters."""
    ref = ref.strip().lower()
    if len(ref) < MIN_PREFIX or not _HEX.fullmatch(ref):
        raise InvalidInputError(f"an item ID is at least {MIN_PREFIX} hex characters")
    rows = conn.execute(
        "SELECT * FROM items WHERE stable_id >= ? AND stable_id < ? LIMIT 2",
        (ref, ref + "g"),  # 'g' sorts after every hex digit: a prefix range on the primary key
    ).fetchall()
    if not rows:
        raise NotFoundError(f"no item {ref}")
    if len(rows) > 1:
        raise ConflictError(f"{ref} matches more than one item; give more characters")
    return rows[0]


def counts(conn: sqlite3.Connection, address_id: str | None = None) -> dict[str, dict[str, int]]:
    """Items by status, per address."""
    sql = "SELECT address_id, status, count(*) AS n FROM items"
    args: tuple[Any, ...] = ()
    if address_id:
        sql, args = sql + " WHERE address_id = ?", (address_id,)
    out: dict[str, dict[str, int]] = {}
    for r in conn.execute(sql + " GROUP BY address_id, status ORDER BY address_id, status", args):
        out.setdefault(r["address_id"], {})[r["status"]] = r["n"]
    return out


def inbox(
    conn: sqlite3.Connection, *, address_id: str | None = None, stale_only: bool = False
) -> list[dict[str, Any]]:
    marks = json.dumps(sorted(OPEN))
    person = json.dumps(sorted(PERSON))
    sql = (
        "SELECT i.*, e.state AS escalation FROM items i LEFT JOIN escalations e USING (stable_id)"
        " WHERE i.status IN (SELECT value FROM json_each(?))"
        " AND (e.stable_id IS NOT NULL OR i.status IN (SELECT value FROM json_each(?))"
        " OR i.model_failed = 1)"  # the local model gave up on it (OD-236)
    )
    args: list[Any] = [marks, person]
    if address_id:
        sql += " AND i.address_id = ?"
        args.append(address_id)
    if stale_only:
        sql += " AND i.stale = 1"
    rows = conn.execute(sql + " ORDER BY i.stale DESC, i.created_at, i.stable_id", args)
    return [summary(r) for r in rows]


def summary(r: sqlite3.Row) -> dict[str, Any]:
    facts: dict[str, Any] = json.loads(r["facts"] or "{}")
    why = cards.why(facts) if (facts.get("triggers") or facts.get("quarantined")) else ""
    return {
        "id": r["stable_id"],
        "short_id": r["stable_id"][: cards.SHORT_ID],
        "address_id": r["address_id"],
        "status": r["status"],
        "stale": bool(r["stale"]),
        "created_at": r["created_at"],
        "sender": cards.sender_line(r, facts),
        "subject": cards.subject_line(r),
        "why": why,
        "payment_or_fraud": payment_or_fraud(facts),
        "model_failed": bool(r["model_failed"]),
    }


def show(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    """Everything ecf keeps about one item, for your terminal (the durable path, OD-214)."""
    r = find(conn, ref)
    sid = r["stable_id"]
    facts: dict[str, Any] = json.loads(r["facts"] or "{}")
    ex = conn.execute("SELECT classifier_text FROM excerpts WHERE stable_id = ?", (sid,)).fetchone()
    esc = conn.execute("SELECT state, posted_at FROM escalations WHERE stable_id = ?",
                       (sid,)).fetchone()  # fmt: skip
    history = conn.execute(
        "SELECT ts, event, actor, outcome FROM audit WHERE stable_id = ? ORDER BY id DESC LIMIT ?",
        (sid, HISTORY_MAX),
    ).fetchall()
    grants = conn.execute(
        "SELECT grant_id, status, expires_at, consumed_at FROM grants WHERE stable_id = ?"
        " ORDER BY expires_at",
        (sid,),
    ).fetchall()
    precheck: dict[str, Any] = facts.get("precheck") or {}
    return summary(r) | {
        "message_id": r["message_id"],
        "sender_check": cards.sender_check(facts),
        "flags": cards.flags(facts),
        "done": cards.done(facts),
        "reasons": precheck.get("reasons", []),
        "escalation": dict(esc) if esc else None,
        "classification": json.loads(r["classification"]) if r["classification"] else None,
        "proposal": json.loads(r["proposal"]) if r["proposal"] else None,
        "grants": [dict(g) for g in grants],
        "history": [dict(h) for h in reversed(history)],
        "excerpt": ex["classifier_text"] if ex else None,
    }


# ---- resolve ------------------------------------------------------------------------------------


@stepup.purpose("item_resolve")
def _describe_resolve(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    """Bound to the exact items and their current statuses."""
    sids = sorted(str(s) for s in target.get("ids", []))
    rows = [find(conn, s) for s in sids]
    risky = sum(1 for r in rows if payment_or_fraud(json.loads(r["facts"] or "{}")))
    if len(rows) == 1:
        s = summary(rows[0])
        what = (f'the email from {s["sender"][:60]}, "{s["subject"][:60]}" on {s["address_id"]},'
                " without acting on it")  # fmt: skip
    else:
        what = f"{len(rows)} emails ({risky} payment or fraud) without acting on them"
    state = [(r["stable_id"], r["status"]) for r in rows]
    return stepup.Bound(stepup.digest("item_resolve", state), f"ecf: close {what}")


@dataclass(frozen=True)
class Selection:
    """Which items: IDs, or every open item older than N days (on one address)."""

    refs: list[str] | None = None
    older_than_days: int | None = None
    address_id: str | None = None


def resolve(
    conn: sqlite3.Connection,
    clock: Clock,
    select: Selection,
    *,
    reason: str,
    nonce: str | None,
    actor: str = "os_user",
    dry_run: bool = False,
) -> list[str]:
    """Close the chosen open items as `resolved_manual`; returns their IDs. With `dry_run`,
    only returns which items it would close."""
    reason = reason.strip()
    if not reason or len(reason) > REASON_MAX:
        raise InvalidInputError(f"a reason is required (at most {REASON_MAX} characters)")
    rows = _chosen(conn, clock, select.refs, select.older_than_days, select.address_id)
    if not rows or dry_run:
        return [r["stable_id"] for r in rows]
    risky = any(payment_or_fraud(json.loads(r["facts"] or "{}")) for r in rows)
    if risky:
        stepup.consume(conn, clock, "item_resolve",
                       {"ids": sorted(r["stable_id"] for r in rows)}, nonce)  # fmt: skip
    done: list[str] = []
    for r in rows:
        pf = payment_or_fraud(json.loads(r["facts"] or "{}"))
        ctx = TransitionContext(payment_or_fraud=pf, stepup_verified=risky)
        items.transition(conn, clock, StableId(r["stable_id"]), Status.RESOLVED_MANUAL, ctx,
                         actor=actor, expected=Status(r["status"]))  # fmt: skip
        _audit_reason(conn, clock, r, reason, actor)
        escalations.close_card(conn, clock, r["stable_id"], "Resolved at your computer")
        done.append(r["stable_id"])
    return done


def _chosen(
    conn: sqlite3.Connection,
    clock: Clock,
    refs: list[str] | None,
    older_than_days: int | None,
    address_id: str | None,
) -> list[sqlite3.Row]:
    if (refs is None) == (older_than_days is None):
        raise InvalidInputError("give item IDs or --older-than, not both")
    if refs is not None:
        rows = [find(conn, ref) for ref in refs]
        closed = [r["stable_id"][:MIN_PREFIX] for r in rows if Status(r["status"]) not in OPEN]
        if closed:
            raise ConflictError(f"already closed: {', '.join(closed)}")
    else:
        if older_than_days is None or older_than_days < 1:
            raise InvalidInputError("--older-than takes a number of days, at least 1")
        cutoff = to_ts(clock.now() - timedelta(days=older_than_days))
        sql = ("SELECT * FROM items WHERE status IN (SELECT value FROM json_each(?))"
               " AND created_at < ?")  # fmt: skip
        args: list[Any] = [json.dumps(sorted(OPEN)), cutoff]
        if address_id:
            sql += " AND address_id = ?"
            args.append(address_id)
        rows = conn.execute(sql + " ORDER BY stable_id", args).fetchall()
    if len({r["stable_id"] for r in rows}) > BULK_MAX:
        raise InvalidInputError(f"at most {BULK_MAX} items at once")
    unique = {r["stable_id"]: r for r in rows}
    return [unique[k] for k in sorted(unique)]


def _audit_reason(
    conn: sqlite3.Connection, clock: Clock, r: sqlite3.Row, reason: str, actor: str
) -> None:
    with write_tx(conn):
        conn.execute(
            "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
            " VALUES (?, ?, ?, 'item.resolved', ?, 'ok', ?)",
            (to_ts(clock.now()), r["address_id"], r["stable_id"], actor,
             json.dumps({"reason": reason})),
        )  # fmt: skip
