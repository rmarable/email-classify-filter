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
from ecf.status import Status
from ecf_server.clock import Clock, to_ts
from ecf_server.db import items_writer, write_tx
from ecf_server.state_machine import TransitionContext, check_transition


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
        expiries = row["expiry_count"] + (1 if to is Status.EXPIRED else 0)
        with items_writer("transition"):
            cur = conn.execute(
                "UPDATE items SET status = ?, updated_at = ?, clarification_rounds = ?, "
                "expiry_count = ? WHERE stable_id = ? AND status = ?",
                (to.value, now, rounds, expiries, stable_id, frm.value),
            )
        if cur.rowcount != 1:
            raise ConflictError("item changed concurrently")
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
