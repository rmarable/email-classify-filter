"""Stages and sensitivity (SPEC §9.1, §9.4; V1.2 step 10a).

- **Stages** ("Watching only / Labels only / Full"): `shadow` decides and posts but changes
  nothing; `assist` also labels and flags; `live` runs the full policy. From V1.2 `assist` is
  available with step-up; `live` waits for the V1.3 go-live gate (OD-209). Going back
  (live → assist → shadow) is instant, like `ecf pause`. Every change is audited and posted in the
  address's channel.
- **Sensitivity** (`standard`, `high`): upgrading is instant; downgrading needs step-up and a
  reason, and sends a Security Notice (§9.4, §13.3). `sensitivity_downgrade_delay_minutes` is 0 in
  local mode (OD-070), so there is no waiting window.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from ecf.errors import InvalidInputError, PolicyDeniedError
from ecf_server import addresses, pause, slack_admin, slack_out, slack_routes, stepup
from ecf_server.chat import Card
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier

STAGES = ("shadow", "assist", "live")
LABELS = {"shadow": "Watching only", "assist": "Labels only", "live": "Full"}
LEVELS = ("standard", "high")
REASON_MAX = 500


def _since(conn: sqlite3.Connection, address_id: str) -> str:
    row = conn.execute(
        "SELECT ts FROM audit WHERE address_id = ? AND event = 'stage.changed'"
        " ORDER BY id DESC LIMIT 1", (address_id,)).fetchone()  # fmt: skip
    if row:
        return str(row[0])
    return str(conn.execute("SELECT created_at FROM addresses WHERE address_id = ?",
                            (address_id,)).fetchone()[0])  # fmt: skip


def status(conn: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for a in addresses.list_addresses(conn):
        aid = a["address_id"]
        since = _since(conn, aid)
        held = conn.execute("SELECT count(*) FROM items WHERE address_id = ? AND status = 'held'",
                            (aid,)).fetchone()[0]  # fmt: skip
        out.append({
            "address_id": aid,
            "stage": a["stage"],
            "label": LABELS[a["stage"]],
            "since": since,
            "days": (now - from_ts(since)).days,
            "sensitivity": a["sensitivity"],
            "paused": pause.is_paused(conn, aid),
            "held": held,
            "gate": "the go-live gate arrives with local models in V1.3",
        })  # fmt: skip
    return out


@stepup.purpose("stage_set")
def _describe_stage(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    a = addresses.get_address(conn, str(target.get("address_id", "")))
    to = str(target.get("stage", ""))
    if to not in STAGES:  # validated before it reaches the dialog (V1.2 review, 2026-09-30)
        raise InvalidInputError(f"stages are {', '.join(STAGES)}")
    prompt = f"ecf: move {a['email']} from {a['stage']} to {to} ({LABELS.get(to, to)})"
    return stepup.Bound(stepup.digest("stage_set", a["address_id"], a["stage"], to), prompt)


def set_stage(
    conn: sqlite3.Connection,
    clock: Clock,
    ref: str,
    to: str,
    *,
    reason: str = "",
    nonce: str | None,
    actor: str = "os_user",
) -> dict[str, Any]:
    if to not in STAGES:
        raise InvalidInputError(f"stages are {', '.join(STAGES)}")
    a = addresses.get_address(conn, ref)
    aid, frm = a["address_id"], a["stage"]
    if frm == to:
        return {"address_id": aid, "stage": to, "changed": False}
    if to == "live":
        raise PolicyDeniedError("live waits for the go-live gate, which arrives with local models"
                                " in V1.3 (OD-209)")  # fmt: skip
    if STAGES.index(to) > STAGES.index(frm):  # moving forward (shadow → assist) needs step-up
        stepup.consume(conn, clock, "stage_set", {"address_id": aid, "stage": to}, nonce)
    reason = reason.strip()[:REASON_MAX]
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = ? WHERE address_id = ?", (to, aid))
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, 'stage.changed', ?, 'ok', ?)",
            (now, aid, actor, json.dumps({"from": frm, "to": to, "reason": reason})),
        )
    _post(conn, clock, aid, f"Stage: {to} ({LABELS[to]})", _stage_text(frm, to, reason))
    return {"address_id": aid, "stage": to, "changed": True}


def _stage_text(frm: str, to: str, reason: str) -> str:
    what = {
        "shadow": "ecf decides and posts, and changes nothing in the mailbox.",
        "assist": "ecf also labels and flags; anything else it would do is held until live.",
    }[to]
    return f"Was {frm}. {what}" + (f" Reason: {reason}" if reason else "")


@stepup.purpose("sensitivity_downgrade")
def _describe_downgrade(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    a = addresses.get_address(conn, str(target.get("address_id", "")))
    return stepup.Bound(
        stepup.digest("sensitivity_downgrade", a["address_id"], a["sensitivity"]),
        f"ecf: lower {a['email']} from high to standard sensitivity (fewer checks)",
    )


def set_sensitivity(
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    ref: str,
    to: str,
    *,
    reason: str,
    nonce: str | None,
    actor: str = "os_user",
) -> dict[str, Any]:
    if to not in LEVELS:
        raise InvalidInputError("sensitivity is standard or high")
    a = addresses.get_address(conn, ref)
    aid, frm = a["address_id"], a["sensitivity"]
    if frm == to:
        return {"address_id": aid, "sensitivity": to, "changed": False}
    downgrade = to == "standard"
    reason = reason.strip()
    if downgrade:
        if not reason:
            raise InvalidInputError("lowering sensitivity needs a reason (--reason)")
        stepup.consume(conn, clock, "sensitivity_downgrade", {"address_id": aid}, nonce)
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("UPDATE addresses SET sensitivity = ? WHERE address_id = ?", (to, aid))
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, 'sensitivity.changed', ?, 'ok', ?)",
            (now, aid, actor, json.dumps({"from": frm, "to": to, "reason": reason[:REASON_MAX]})),
        )
    text = (f"{a['email']} is now {to} sensitivity (was {frm})."
            + (f" Reason: {reason[:REASON_MAX]}" if reason else ""))  # fmt: skip
    if downgrade:  # a Security Notice (§13.3): to your DM, the summary channel and the desktop
        ident = slack_admin.identity(conn)
        notice = text + " High-sensitivity checks no longer apply."
        slack_admin.notice(conn, clock, notifier, notice,
                           dms=[ident.member] if ident and ident.member else [])  # fmt: skip
    _post(conn, clock, aid, f"Sensitivity: {to}", text)
    return {"address_id": aid, "sensitivity": to, "changed": True}


def _post(conn: sqlite3.Connection, clock: Clock, aid: str, title: str, text: str) -> None:
    route = slack_routes.route_for(conn, aid)
    if route is not None:
        slack_out.enqueue_post(conn, clock, key=f"address:{aid}:{to_ts(clock.now())}:{title}",
                               route=route, card=Card(title, text=text),
                               identity=slack_routes.identity(aid))  # fmt: skip
