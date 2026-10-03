"""Items and their status (SPEC §6.2).

`create_item` is the only way to insert an item (always at `new`); `transition` is the only writer
of `items.status` anywhere in the service (a test enforces this). Counters that guards depend on
(`clarification_rounds`, `expiry_count`) are read from the database, never taken from the caller.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from ecf.errors import ConflictError, NotFoundError
from ecf.ids import AddressId, StableId
from ecf.status import TERMINAL, Status
from ecf_server.clock import Clock, to_ts
from ecf_server.db import items_writer, write_tx
from ecf_server.state_machine import Origin, TransitionContext, check_transition

WAITS_FOR_CLAUDE = frozenset({Status.AWAITING_CLAUDE, Status.CLARIFIED})


def create_item(
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    stable_id: StableId,
    address_id: AddressId,
    content_hash: str,
    actor: str = "service",
    also: Callable[[sqlite3.Connection], None] | None = None,
    **columns: Any,
) -> None:
    """Insert one item at `new`. `also` runs inside the same transaction (excerpts, markers)."""
    now = to_ts(clock.now())
    cols = {
        "stable_id": stable_id,
        "address_id": address_id,
        "content_hash": content_hash,
        "status": Status.NEW.value,
        "created_at": now,
        "updated_at": now,
        **columns,
    }
    if cols["status"] != Status.NEW.value:
        raise ConflictError("items are always created at 'new'")
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    with write_tx(conn), items_writer("create"):
        conn.execute(f"INSERT INTO items ({names}) VALUES ({marks})", tuple(cols.values()))  # noqa: S608
        _audit(conn, now, address_id, stable_id, "item.created", actor, {})
        if also is not None:
            also(conn)


def get_status(conn: sqlite3.Connection, stable_id: StableId) -> Status:
    row = conn.execute("SELECT status FROM items WHERE stable_id = ?", (stable_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"no item {stable_id[:8]}")
    return Status(row["status"])


def transition(
    conn: sqlite3.Connection,
    clock: Clock,
    stable_id: StableId,
    to: Status,
    ctx: TransitionContext,
    *,
    actor: str,
    expected: Status | None = None,
) -> Status:
    """Move one item to `to`. Returns the previous status.

    Raises NotFoundError, ConflictError (unexpected current status, non-edge, or a concurrent
    change), or PolicyDeniedError (a guard refused).
    """
    now = to_ts(clock.now())
    with write_tx(conn):
        row = conn.execute(
            "SELECT address_id, status, clarification_rounds, expiry_count FROM items "
            "WHERE stable_id = ?",
            (stable_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"no item {stable_id[:8]}")
        frm = Status(row["status"])
        if expected is not None and frm is not expected:
            raise ConflictError(f"item is {frm}, not {expected}", current=str(frm))
        ctx = replace(
            ctx,
            clarification_rounds=row["clarification_rounds"],
            expiry_count=row["expiry_count"],
        )
        check_transition(frm, to, ctx)
        rounds = row["clarification_rounds"] + (1 if to is Status.NEEDS_CLARIFICATION else 0)
        # only approvals count: an expired answer is already one of the two rounds (§6.2), and a
        # shared count made a first approval expiry look like a second (V1.2 review, 2026-09-30)
        counts = to is Status.EXPIRED and ctx.origin is Origin.APPROVAL
        expiries = row["expiry_count"] + (1 if counts else 0)
        with items_writer("transition"):
            # claude_since: when it started waiting for Claude (the fallback's timeout, V1.4)
            cur = conn.execute(
                "UPDATE items SET status = ?, updated_at = ?, clarification_rounds = ?, "
                "expiry_count = ?, claude_since = CASE WHEN ? THEN ? ELSE claude_since END"
                " WHERE stable_id = ? AND status = ?",
                (to.value, now, rounds, expiries, to in WAITS_FOR_CLAUDE, now, stable_id,
                 frm.value),
            )  # fmt: skip
        if cur.rowcount != 1:
            raise ConflictError("item changed concurrently")
        if to in TERMINAL:  # closed however it happened: nothing may run from it any more
            conn.execute("UPDATE grants SET status = 'voided' WHERE stable_id = ?"
                         " AND status IN ('issued', 'approved')", (stable_id,))  # fmt: skip
            conn.execute("DELETE FROM delays WHERE stable_id = ?", (stable_id,))
        _audit(
            conn,
            now,
            row["address_id"],
            stable_id,
            "item.transitioned",
            actor,
            {"from": frm.value, "to": to.value},
        )
    return frm


def map_imported(staged: sqlite3.Connection, repost: tuple[str, ...],
                 unknown: tuple[str, ...]) -> None:  # fmt: skip
    """Import only (importer.py; OD-358), on the staging copy, never the live database: statuses
    from another computer are mapped, not transitioned. Waiting items go back to
    `awaiting_approval` (`needs_human` when the proposal has no actions); ones that may have run
    there become `failed_unknown`. Here so this module stays the only writer of `items.status`."""
    with write_tx(staged), items_writer("transition"):
        staged.execute("UPDATE items SET status = 'awaiting_approval' WHERE status IN"
                       " (SELECT value FROM json_each(?))", (json.dumps(repost),))  # fmt: skip
        staged.execute("UPDATE items SET status = 'failed_unknown' WHERE status IN"
                       " (SELECT value FROM json_each(?))", (json.dumps(unknown),))  # fmt: skip
        staged.execute("UPDATE items SET status = 'needs_human' WHERE status ="
                       " 'awaiting_approval' AND coalesce(json_array_length(proposal,"
                       " '$.actions'), 0) = 0")  # fmt: skip


def _audit(
    conn: sqlite3.Connection,
    ts: str,
    address_id: str | None,
    stable_id: str | None,
    event: str,
    actor: str,
    data: dict[str, Any],
    outcome: str = "ok",
) -> None:
    conn.execute(
        "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ts, address_id, stable_id, event, actor, outcome, json.dumps(data)),
    )
