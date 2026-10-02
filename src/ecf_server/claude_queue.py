"""The Claude queue (SPEC §4.3, §6.2; V1.4 step 1): items in presets B and C wait at
`awaiting_claude` until `/ecf-review`.

- **C:** every email goes `new → awaiting_claude` once the pre-check has decided it (the pre-check's
  flags and escalations still apply at once, §5.4). The local model never classifies it.
- **B:** the local model classifies; when the rule continues to the actor, the item goes
  `classified → awaiting_claude` instead of waiting for the local actor.
- **C after a Claude classification:** the same edge, `classified → awaiting_claude`, when the
  actor is needed (OD-269).

An answered question (`clarified`) on a B or C address waits for Claude too; it stays at
`clarified`, and the local model queue leaves it alone (modelq.WAITING).

Records-only backfilled mail (`ecf backfill` without --act) is closed as `observed` and never
queued. `sweep` runs each tick for items a crash left between the pre-check (or the rule) and
this queue.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from ecf.errors import ConflictError
from ecf.ids import StableId
from ecf_server import items
from ecf_server.clock import Clock, to_ts
from ecf_server.log_bridge import log
from ecf_server.state_machine import Status, TransitionContext

CLAUDE_PRESETS = frozenset({"B", "C"})  # the actor is Claude
CLAUDE_CLASSIFIES = frozenset({"C"})  # the classifier is Claude too


def preset(conn: sqlite3.Connection, address_id: str) -> str:
    row = conn.execute("SELECT preset FROM addresses WHERE address_id = ?",
                       (address_id,)).fetchone()  # fmt: skip
    return str(row["preset"]) if row else "A"


def route_new(conn: sqlite3.Connection, clock: Clock, sid: str) -> bool:
    """A pre-checked email on a C address goes to the Claude queue; True if it moved."""
    item = conn.execute("SELECT address_id, status FROM items WHERE stable_id = ?",
                        (sid,)).fetchone()  # fmt: skip
    if item is None or item["status"] != Status.NEW:
        return False
    if preset(conn, item["address_id"]) not in CLAUDE_CLASSIFIES:
        return False
    return _move(conn, clock, sid, Status.NEW)


def to_actor(conn: sqlite3.Connection, clock: Clock, sid: str) -> Status:
    """A classified item whose rule continues to the actor: the local actor (A) or the Claude
    queue (B, C). Returns its status."""
    item = conn.execute("SELECT address_id FROM items WHERE stable_id = ?", (sid,)).fetchone()
    if preset(conn, item["address_id"]) not in CLAUDE_PRESETS:
        return Status.CLASSIFIED  # the local actor decides next (V1.3 step 4c)
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
    "(i.status = 'new' AND a.preset = 'C' AND i.prechecked = 1 AND i.model_failed = 0"
    " AND coalesce(json_extract(i.facts, '$.precheck.stage'), '') <> 'backfill')"
    " OR (i.status = 'classified' AND a.preset IN ('B', 'C') AND i.decision_source = 'rule'"
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


def waiting(conn: sqlite3.Connection) -> dict[str, int]:
    """Items waiting for `/ecf-review`, per address: at `awaiting_claude`, and answered
    questions on B and C addresses."""
    rows = conn.execute(
        "SELECT i.address_id, count(*) FROM items i JOIN addresses a USING (address_id)"
        " WHERE a.removed_at IS NULL AND (i.status = 'awaiting_claude'"
        " OR (i.status = 'clarified' AND a.preset IN ('B', 'C')))"
        " GROUP BY i.address_id ORDER BY i.address_id"
    ).fetchall()
    return {r[0]: r[1] for r in rows}
