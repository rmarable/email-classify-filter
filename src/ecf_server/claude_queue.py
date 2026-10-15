"""The Claude queue (SPEC §4.3, §6.2; V1.4 step 1): items of preset C wait at `awaiting_claude`
until `/ecf-review`. Presets A and B, and the local fallback that took items from this queue, went
in v3.0.0 (ADR 0031): mail waits here however long it takes.

- **New mail:** every email goes `new → awaiting_claude` once the pre-check has decided it (the
  pre-check's flags and escalations still apply at once, §5.4).
- **After a Claude classification:** `classified → awaiting_claude` when the actor is needed
  (OD-269).

An answered question (`clarified`) waits for Claude too; it stays at `clarified`.

Records-only backfilled mail (`ecf backfill` without --act) is closed as `observed` and never
queued. `sweep` runs each tick for items a crash left between the pre-check (or the rule) and
this queue.

`overdue` (V1.4 step 9): items that have waited longer than `claude_review_reminder_hours` (24;
OD-115), for the reminder (claude_review.remind) and the daily summary; with `factor` 2, for the
escalation (claude_review.escalate; v3.0.0, ADR 0031, P10).

`backlog` (§5.3; v3.0.0, P9): what waits at `awaiting_claude`; above `BACKLOG_BATCH`, approvals go
to digests, not one card each.

`batch_risky` is the cross-item hide guard (§5.6) for Claude's batches (claude_review.py, step 3).

**Preset D** (v3.0.0 step 4, §2.1; OD-509, P13): D items wait at `awaiting_claude` like C's, with
no classification until TypeSafe Jev's drain (`jev_dispatch`) classifies them; then the actor is
Claude's, as for C. `route_new` routes D as C and wakes the drain (`jev_keys.WAKE`). An item
waiting for Jev (D, no classification) never counts in `waiting`, `backlog` or `overdue`, so the
reminder and escalation leave it out: `waiting_for_jev` counts it instead.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ecf.errors import ConflictError
from ecf.ids import StableId
from ecf.schema import fixed_item
from ecf_server import items, jev_facts, jev_keys, marked, policy
from ecf_server.clock import Clock, to_ts
from ecf_server.log_bridge import log
from ecf_server.state_machine import Status, TransitionContext

CLAUDE_PRESETS = frozenset({"C", "D"})  # the actor is Claude
CLAUDE_CLASSIFIES = frozenset({"C"})  # the classifier is Claude too
QUEUED = frozenset({"C", "D"})  # new mail waits at `awaiting_claude` (D for Jev's drain, §2.1)
REMIND_DEFAULT_H = 24  # claude_review_reminder_hours (§14.1)
BACKLOG_BATCH = 100  # §5.3: above this many waiting, approvals go to digests, not one card each


def preset(conn: sqlite3.Connection, address_id: str) -> str:
    row = conn.execute("SELECT preset FROM addresses WHERE address_id = ?",
                       (address_id,)).fetchone()  # fmt: skip
    return str(row["preset"]) if row else "C"


def route_new(conn: sqlite3.Connection, clock: Clock, sid: str) -> bool:
    """A pre-checked email on a C or D address goes to the Claude queue (a D one waits there for
    Jev, whose drain this wakes); True if it moved."""
    item = conn.execute("SELECT address_id, status FROM items WHERE stable_id = ?",
                        (sid,)).fetchone()  # fmt: skip
    if item is None or item["status"] != Status.NEW:
        return False
    p = preset(conn, item["address_id"])
    if p not in QUEUED:
        return False
    moved = _move(conn, clock, sid, Status.NEW)
    if moved and p == jev_keys.PRESET:
        jev_keys.WAKE.set()
    return moved


def to_actor(conn: sqlite3.Connection, clock: Clock, sid: str) -> Status:
    """A classified item whose rule continues to the actor: the Claude queue (C). Returns its
    status; an address of a removed preset (until migration 0038 converts it) leaves it
    `classified`."""
    item = conn.execute("SELECT address_id FROM items WHERE stable_id = ?", (sid,)).fetchone()
    if preset(conn, item["address_id"]) not in CLAUDE_PRESETS:
        return Status.CLASSIFIED
    _move(conn, clock, sid, Status.CLASSIFIED)
    return Status.AWAITING_CLAUDE


def _move(conn: sqlite3.Connection, clock: Clock, sid: str, frm: Status) -> bool:
    try:
        items.transition(conn, clock, StableId(sid), Status.AWAITING_CLAUDE, TransitionContext(),
                         actor="service", expected=frm)  # fmt: skip
    except ConflictError:
        log.info("claude_queue.item_moved_on", stable_id=sid[:8])  # resolved meanwhile
        return False
    return True


SETTLED = timedelta(minutes=1)
# a backfilled email recorded only is closed as observed right after its pre-check (OD-221)
_SWEEP = (
    "SELECT i.stable_id, i.status FROM items i JOIN addresses a USING (address_id)"
    " WHERE a.removed_at IS NULL AND i.updated_at < ? AND ("
    "(i.status = 'new' AND a.preset IN ('C', 'D') AND i.prechecked = 1"
    " AND coalesce(json_extract(i.facts, '$.precheck.stage'), '') <> 'backfill')"
    " OR (i.status = 'classified' AND a.preset IN ('C', 'D') AND i.decision_source = 'rule'"
    " AND json_extract(i.proposal, '$.plan.to_actor') = 1))"
    " ORDER BY i.updated_at LIMIT ?"
)


def sweep(conn: sqlite3.Connection, clock: Clock, limit: int = 50) -> int:
    """Queue items left short of the Claude queue (a crash, or an error before the move). Each
    tick; returns how many moved. Only items untouched for a minute, so a check or a decision in
    progress finishes first."""
    before = to_ts(clock.now() - SETTLED)
    moved = 0
    for sid, status in conn.execute(_SWEEP, (before, limit)).fetchall():
        moved += _move(conn, clock, sid, Status(status))
    return moved


# `i.status IN (...)` as its own term, as `items_claude_waiting` (0038) is defined, so SQLite can
# use that partial index (review R2; test_claude_queue checks the plans)
_IN_QUEUE = "i.status IN ('awaiting_claude', 'clarified')"
# a D item waiting for Jev's drain, not for `/ecf-review` (§2.1, P13)
FOR_JEV = "(a.preset = 'D' AND i.classification IS NULL)"
# old mail a scan or reprocess marked never counts here (OD-507 §2): `scans.waiting_line` instead
_WAITING = (
    " FROM items i JOIN addresses a USING (address_id)"
    f" WHERE a.removed_at IS NULL AND {_IN_QUEUE} AND (i.status = 'awaiting_claude'"
    f" OR (i.status = 'clarified' AND a.preset IN ('C', 'D'))) AND NOT {marked.sql('i.')}"
    f" AND NOT {FOR_JEV}"
)


def waiting(conn: sqlite3.Connection) -> dict[str, int]:
    """Items waiting for `/ecf-review`, per address: at `awaiting_claude`, and answered
    questions on C addresses."""
    rows = conn.execute("SELECT i.address_id, count(*)" + _WAITING
                        + " GROUP BY i.address_id ORDER BY i.address_id").fetchall()  # fmt: skip
    return {r[0]: r[1] for r in rows}


@dataclass(frozen=True)
class Overdue:
    """Items that have waited for `/ecf-review` longer than `claude_review_reminder_hours`."""

    address_id: str
    count: int
    oldest: str  # claude_since of the oldest, UTC


def reminder_hours(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key = 'claude_review_reminder_hours'"
                       ).fetchone()  # fmt: skip
    return int(json.loads(row[0])) if row else REMIND_DEFAULT_H


def backlog(conn: sqlite3.Connection) -> int:
    """Items waiting at `awaiting_claude` on addresses that are neither paused nor removed (§5.3;
    v3.0.0, P9: the local model's backlog before), old mail apart (OD-507 §2: a scan never turns
    new mail's cards into digest lines)."""
    row = conn.execute("SELECT count(*) FROM items i JOIN addresses a USING (address_id)"  # noqa: S608
                       f" WHERE a.removed_at IS NULL AND a.paused = 0 AND {_IN_QUEUE}"
                       f" AND i.status = 'awaiting_claude' AND NOT {marked.sql('i.')}"
                       f" AND NOT {FOR_JEV}").fetchone()  # fmt: skip
    return int(row[0])


def waiting_for_jev(conn: sqlite3.Connection) -> dict[str, int]:
    """D items waiting for Jev's drain, per address (§2.1): "waiting for Jev" in the daily summary
    and `ecf status`; new and old mail alike."""
    rows = conn.execute("SELECT i.address_id, count(*) FROM items i JOIN addresses a"  # noqa: S608
                        f" USING (address_id) WHERE a.removed_at IS NULL AND {_IN_QUEUE}"
                        f" AND i.status = 'awaiting_claude' AND {FOR_JEV}"
                        " GROUP BY i.address_id ORDER BY i.address_id").fetchall()  # fmt: skip
    return {r[0]: r[1] for r in rows}


def overdue(conn: sqlite3.Connection, now: datetime, factor: int = 1) -> list[Overdue]:
    """Per address, items waiting for `/ecf-review` longer than `factor` times the reminder hours
    (since they entered the queue, `items.claude_since`)."""
    before = to_ts(now - timedelta(hours=factor * reminder_hours(conn)))
    rows = conn.execute(
        "SELECT i.address_id, count(*), min(i.claude_since)" + _WAITING
        + " AND i.claude_since < ? GROUP BY i.address_id ORDER BY i.address_id", (before,)
    ).fetchall()  # fmt: skip
    return [Overdue(r[0], r[1], r[2]) for r in rows]


def overdue_twice(conn: sqlite3.Connection, now: datetime,
                  factor: int) -> tuple[list[Overdue], list[Overdue]]:  # fmt: skip
    """`overdue(conn, now)` and `overdue(conn, now, factor)` from one query, for the tick's
    reminder and escalation (review R2)."""
    hours = reminder_hours(conn)
    first = to_ts(now - timedelta(hours=hours))
    second = to_ts(now - timedelta(hours=factor * hours))
    rows = conn.execute(
        "SELECT i.address_id, count(*), min(i.claude_since), sum(i.claude_since < ?)" + _WAITING
        + " AND i.claude_since < ? GROUP BY i.address_id ORDER BY i.address_id", (second, first)
    ).fetchall()  # fmt: skip
    return ([Overdue(r[0], r[1], r[2]) for r in rows],
            [Overdue(r[0], r[3], r[2]) for r in rows if r[3]])  # fmt: skip


def batch_risky(conn: sqlite3.Connection, sid: str) -> bool:
    """§5.6 for Claude batches: True when any batch this item was claimed in held an item with a
    fraud signal, phishing (`ecf.schema.fixed_item`; plain spam no longer counts, P14, OD-500) or
    `fraud_risk` of low or more, or one not yet classified (unknown counts as risky; operator
    decision 2026-10-02). Items classified one per request are in no batch."""
    rows = conn.execute(
        "SELECT i.classification, i.facts FROM claim_batches b"
        " JOIN claim_batches m ON m.batch_id = b.batch_id JOIN items i ON i.stable_id = m.stable_id"
        " WHERE b.stable_id = ?",
        (sid,),
    ).fetchall()
    for r in rows:
        if r["classification"] is None:
            return True
        f: dict[str, Any] = json.loads(r["facts"] or "{}")
        c = jev_facts.risk_view(json.loads(r["classification"]), f)  # unsure as yes (OD-511)
        t: dict[str, Any] = f.get("triggers") or {}
        if (fixed_item(c, "phishing") or c.get("fraud_risk") in policy.FRAUD_RISKY
                or t.get("fraud") or t.get("fraud_weak") or t.get("lookalikes")
                or f.get("quarantined")):  # fmt: skip
            return True
    return False
