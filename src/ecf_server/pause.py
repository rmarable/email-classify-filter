"""Pause and resume (SPEC §5.4, §10.1, §13; V1.2 step 8a).

A paused address keeps its pre-check: mail is still fetched, fraud and regulator triggers still
label, flag and escalate (§5.4, "pause never stops the pre-check or fraud flagging"). What waits:
approved actions (the action runner holds their jobs, not counting an attempt), delayed sends that
ran out (they start on resume), and, from V1.3, model checks. Pausing and resuming are instant and
need no step-up (§9.1); they are audited. `ecf pause|resume <address>|--all`, and the Pause and
Resume buttons (per address on digests, for all addresses on "Needs you").
"""

from __future__ import annotations

import json
import sqlite3

from ecf.errors import ConflictError, NotFoundError
from ecf_server import addresses, restore, slack_in, slack_out
from ecf_server.chat import RouteRef
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx

PAUSE, RESUME = "pause", "resume"
ALL = "*"  # a button's ref for every address (the pinned "Needs you")


def is_paused(conn: sqlite3.Connection, address_id: str) -> bool:
    r = conn.execute("SELECT paused FROM addresses WHERE address_id = ?", (address_id,)).fetchone()
    return bool(r and r["paused"])


def paused_addresses(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT address_id FROM addresses WHERE paused = 1 AND removed_at IS NULL"
        " ORDER BY address_id"
    )
    return [r[0] for r in rows]


def set_paused(
    conn: sqlite3.Connection, clock: Clock, ref: str, paused: bool, *, actor: str
) -> list[str]:
    """Pause or resume one address (by ID or email), or every address with `ALL`; returns the
    addresses that changed."""
    if ref == ALL:
        targets = [r[0] for r in conn.execute(
            "SELECT address_id FROM addresses WHERE removed_at IS NULL AND paused = ?",
            (0 if paused else 1,))]  # fmt: skip
    else:
        a = addresses.get_address(conn, ref)  # raises NotFoundError
        targets = [a["address_id"]] if a["paused"] != paused else []
    if not paused:  # after a restore, an address waits for a passing mail check (OD-366)
        held = {aid: why for aid in targets if (why := restore.resume_blocked(conn, aid))}
        if held and ref != ALL:
            raise ConflictError(next(iter(held.values())))
        targets = [aid for aid in targets if aid not in held]
    now = to_ts(clock.now())
    event = "address.paused" if paused else "address.resumed"
    with write_tx(conn):
        for aid in targets:
            conn.execute("UPDATE addresses SET paused = ? WHERE address_id = ?",
                         (int(paused), aid))  # fmt: skip
            if not paused:
                restore.resumed(conn, aid)
            conn.execute(
                "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
                " VALUES (?, ?, ?, ?, 'ok', ?)",
                (now, aid, event, actor, json.dumps({"all": ref == ALL})),
            )
    return targets


def describe(changed: list[str], paused: bool) -> str:
    if not changed:
        return "Nothing changed." if paused else "Nothing was paused."
    names = ", ".join(changed)
    if paused:
        return (f"Paused {names}. ecf still checks new mail for fraud and regulator mail;"
                " it takes no other action until you resume.")  # fmt: skip
    return f"Resumed {names}."


# ---- Slack buttons ------------------------------------------------------------------------------


@slack_in.handles(PAUSE)
def _pause_click(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click) -> None:
    _click(conn, clock, click, paused=True)


@slack_in.handles(RESUME)
def _resume_click(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click) -> None:
    _click(conn, clock, click, paused=False)


def _click(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click, *, paused: bool) -> None:
    try:
        changed = set_paused(conn, clock, click.ref, paused, actor=f"slack:{click.user}")
        text = describe(changed, paused)
    except (NotFoundError, ConflictError) as exc:  # unknown address; held after a restore
        _tell(conn, clock, click, f"Not done: {exc.detail}")
        raise
    _tell(conn, clock, click, text)


def _tell(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click, text: str) -> None:
    if click.channel:
        slack_out.enqueue_ephemeral(conn, clock, route=RouteRef(click.channel), user=click.user,
                                    text=text)  # fmt: skip
