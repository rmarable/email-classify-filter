"""The outbound switch per address (SPEC §8.4, §9.8; OD-058, OD-323; V1.5 step 3b).

`ecf outbound enable <address>` turns sending on (template replies and internal forwards; drafts
never needed it). It needs step-up bound to the address and its current state, and sends a
Security Notice. On a `high` address it also needs a track record first: at least 20 suppressed
send proposals you reviewed, at least 95% of them marked correct (§9.8; no override).

`ecf outbound disable <address>` is instant and needs no step-up. Sends already approved stop
(OD-323): one waiting out the 10-minute delay is cancelled; one queued to run has its grant voided,
so it fails without sending. Each is listed in the reply. A send still waiting for approval is
refused at execution while outbound is off.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ecf.errors import ConflictError, PolicyDeniedError
from ecf.status import Status
from ecf_server import addresses, approvals, stepup
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.outbound_plan import SENDS

PURPOSE = "outbound_enable"
HIGH_REVIEWED = 20  # §9.8
HIGH_CORRECT = 0.95
REVIEWED = ("correct", "fixed")  # not `asked` or `not_sampled` (review.py)


def track_record(conn: sqlite3.Connection, address_id: str) -> dict[str, Any]:
    """Suppressed send proposals, how many you reviewed, and how many you marked correct."""
    row = conn.execute(
        "SELECT count(*) AS suppressed,"
        " sum(json_extract(review, '$.verdict') IN ('correct', 'fixed')) AS reviewed,"
        " sum(json_extract(review, '$.verdict') = 'correct') AS correct"
        " FROM items WHERE address_id = ? AND suppressed_action IS NOT NULL",
        (address_id,),
    ).fetchone()
    reviewed, correct = int(row["reviewed"] or 0), int(row["correct"] or 0)
    return {"suppressed": int(row["suppressed"] or 0), "reviewed": reviewed, "correct": correct,
            "share": correct / reviewed if reviewed else 0.0}  # fmt: skip


def high_ready(record: dict[str, Any]) -> str | None:
    """Why a `high` address may not enable outbound yet, or None."""
    if record["reviewed"] < HIGH_REVIEWED:
        return (f"a high address needs {HIGH_REVIEWED} reviewed suppressed sends first"
                f" ({record['reviewed']} so far; `ecf outbound report`)")  # fmt: skip
    if record["share"] < HIGH_CORRECT:
        return (f"a high address needs {HIGH_CORRECT:.0%} of its reviewed suppressed sends marked"
                f" correct ({record['share']:.0%} so far)")  # fmt: skip
    return None


def enable(conn: sqlite3.Connection, clock: Clock, announce: Callable[[str], None], ref: str, *,
           actor: str, nonce: str | None) -> dict[str, Any]:  # fmt: skip
    a = addresses.get_address(conn, ref)
    aid = a["address_id"]
    if a["outbound"]:
        raise ConflictError(f"outbound is already on for {a['email']}")
    if a["sensitivity"] == "high":
        why = high_ready(track_record(conn, aid))
        if why is not None:
            raise PolicyDeniedError(why)
    stepup.consume(conn, clock, PURPOSE, {"address_id": aid}, nonce)
    _set(conn, clock, aid, on=True, actor=actor, data={})
    announce(f"Outbound is on for {a['email']}: approved template replies and internal forwards"
             " will be sent (each with step-up).")  # fmt: skip
    return addresses.get_address(conn, aid)


def disable(conn: sqlite3.Connection, clock: Clock, ref: str, *, actor: str) -> dict[str, Any]:
    a = addresses.get_address(conn, ref)
    aid = a["address_id"]
    stopped = _stop_sends(conn, clock, aid, actor=actor)
    _set(conn, clock, aid, on=False, actor=actor, data={"stopped": stopped})
    return addresses.get_address(conn, aid) | {"stopped": stopped}


def _stop_sends(conn: sqlite3.Connection, clock: Clock, aid: str, *, actor: str) -> list[str]:
    """Cancel delayed sends and void the grants of queued ones (OD-323); their short ids."""
    out: list[str] = []
    rows = conn.execute("SELECT * FROM items WHERE address_id = ? AND status IN (?, ?)",
                        (aid, Status.DELAYED, Status.EXECUTING)).fetchall()  # fmt: skip
    for item in rows:
        if not _is_send(item):
            continue
        sid = str(item["stable_id"])
        if item["status"] == Status.DELAYED:
            approvals.cancel(conn, clock, sid, actor=actor)
        else:
            with write_tx(conn):
                conn.execute("UPDATE grants SET status = 'voided' WHERE stable_id = ?"
                             " AND status IN ('issued', 'approved')", (sid,))  # fmt: skip
            approvals.edit_card(conn, clock, sid, "Not sent: outbound was turned off")
        out.append(sid[:8])
    return out


def _is_send(item: sqlite3.Row) -> bool:
    doc: dict[str, Any] = json.loads(item["proposal"] or "{}")
    return any(a.get("name") in SENDS for a in doc.get("actions", []))


def _set(conn: sqlite3.Connection, clock: Clock, aid: str, *, on: bool, actor: str,
         data: dict[str, Any]) -> None:  # fmt: skip
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("UPDATE addresses SET outbound = ? WHERE address_id = ?", (int(on), aid))
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, ?, ?, 'ok', ?)",
            (now, aid, "outbound.enabled" if on else "outbound.disabled", actor, json.dumps(data)),
        )


@stepup.purpose(PURPOSE)
def _describe(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    a = addresses.get_address(conn, str(target.get("address_id", "")))
    prompt = f"ecf: let {a['email']} send approved template replies and internal forwards"
    return stepup.Bound(stepup.digest(PURPOSE, a["address_id"], a["outbound"],
                                      a["sensitivity"]), prompt)  # fmt: skip
