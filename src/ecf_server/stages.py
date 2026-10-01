"""Stages and sensitivity (SPEC §9.1, §9.4; V1.2 step 10a).

- **Stages** ("Watching only / Labels only / Full"): `shadow` decides and posts but changes
  nothing; `assist` also labels and flags; `live` runs the full policy. From V1.2 `assist` is
  available with step-up. Going back (live → assist → shadow) is instant, like `ecf pause`. Every
  change is audited and posted in the address's channel.
- **Going live** (V1.3 step 6b; SPEC §9.3, §6.2): only when the go-live gate (gate.py) is met for
  the model ecf runs now, or, with an override and a written reason, when only its review count or
  accuracy falls short (OD-234). Step-up is bound to the address, the model digest, a snapshot of
  the gate, the override and what happens to held emails. Held emails from the last
  `HELD_RUN_DAYS` days then run: their stored plan, or a new one from your correction when you
  fixed them (OD-157; the rule's actions, the actor isn't asked again); older ones stay held unless
  you resolve them as handled by hand (`resolve_older`, or `resolve_all` for every held email).
- **Each tick** (`tick`): an address that newly meets its gate gets one "ready for live" post; a
  `live` address whose model digest changed goes back to `assist` until the gate passes again.
- **Sensitivity** (`standard`, `high`): upgrading is instant; downgrading needs step-up and a
  reason, and sends a Security Notice (§9.4, §13.3). `sensitivity_downgrade_delay_minutes` is 0 in
  local mode (OD-070), so there is no waiting window.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from ecf.errors import InvalidInputError, PolicyDeniedError
from ecf.ids import StableId
from ecf.schema import load_schema_v1
from ecf_server import (
    addresses,
    decide,
    gate,
    items,
    pause,
    policy,
    review,
    slack_admin,
    slack_out,
    slack_routes,
    stepup,
)
from ecf_server.chat import Card
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.notify import Notifier
from ecf_server.state_machine import Stage, Status, TransitionContext

STAGES = ("shadow", "assist", "live")
LABELS = {"shadow": "Watching only", "assist": "Labels only", "live": "Full"}
LEVELS = ("standard", "high")
REASON_MAX = 500
HELD_RUN_DAYS = 7  # held emails up to this old run when going live (§6.2)
HELD_CHOICES = ("run", "resolve_older", "resolve_all")


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
            "review": review.progress(conn, aid, a["sensitivity"], gate.current_digest())["text"],
            "gate": gate.compute(conn, aid).text(),
        })  # fmt: skip
    return out


@stepup.purpose("stage_set")
def _describe_stage(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    a = addresses.get_address(conn, str(target.get("address_id", "")))
    to = str(target.get("stage", ""))
    if to not in STAGES:  # validated before it reaches the dialog (V1.2 review, 2026-09-30)
        raise InvalidInputError(f"stages are {', '.join(STAGES)}")
    prompt = f"ecf: move {a['email']} from {a['stage']} to {to} ({LABELS.get(to, to)})"
    if to != "live":
        return stepup.Bound(stepup.digest("stage_set", a["address_id"], a["stage"], to), prompt)
    extra = [str(target.get(k, "")) for k in ("digest", "snapshot", "override", "held")]
    if target.get("override"):
        prompt += ", OVERRIDING the review count or accuracy"
    prompt += {"run": "", "resolve_older": "; older held emails marked handled by hand",
               "resolve_all": "; all held emails marked handled by hand"}.get(
                   str(target.get("held")), "")  # fmt: skip
    return stepup.Bound(stepup.digest("stage_set", a["address_id"], a["stage"], to, *extra),
                        prompt)  # fmt: skip


def set_stage(  # noqa: PLR0913 - the change, then who asked and how
    conn: sqlite3.Connection,
    clock: Clock,
    ref: str,
    to: str,
    *,
    reason: str = "",
    nonce: str | None,
    actor: str = "os_user",
    override: bool = False,
    held: str = "run",
) -> dict[str, Any]:
    if to not in STAGES:
        raise InvalidInputError(f"stages are {', '.join(STAGES)}")
    a = addresses.get_address(conn, ref)
    aid, frm = a["address_id"], a["stage"]
    if frm == to:
        return {"address_id": aid, "stage": to, "changed": False}
    reason = reason.strip()[:REASON_MAX]
    g: gate.Gate | None = None
    data: dict[str, Any] = {"from": frm, "to": to, "reason": reason}
    if to == "live":
        g = _live_allowed(conn, aid, override=override, reason=reason, held=held)
        overriding = override and not g.met
        stepup.consume(conn, clock, "stage_set", {"address_id": aid, "stage": "live",
                                                  "digest": g.digest, "snapshot": g.snapshot,
                                                  "override": overriding, "held": held},
                       nonce)  # fmt: skip
        data |= {"gate": g.as_json(), "override": overriding, "held": held}
    elif STAGES.index(to) > STAGES.index(frm):  # moving forward (shadow → assist) needs step-up
        stepup.consume(conn, clock, "stage_set", {"address_id": aid, "stage": to}, nonce)
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = ? WHERE address_id = ?", (to, aid))
        if g is not None:
            gate.record(conn, g, clock.now(), passed=True)
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, 'stage.changed', ?, 'ok', ?)",
            (now, aid, actor, json.dumps(data)),
        )
    released = _release_held(conn, clock, aid, held, actor=actor) if g is not None else {}
    text = _stage_text(frm, to, reason)
    if g is not None:
        text += _live_text(g, data["override"], released)
    _post(conn, clock, aid, f"Stage: {to} ({LABELS[to]})", text)
    return {"address_id": aid, "stage": to, "changed": True} | ({"held": released} if g else {})


def _live_allowed(conn: sqlite3.Connection, aid: str, *, override: bool, reason: str,
                  held: str) -> gate.Gate:  # fmt: skip
    if held not in HELD_CHOICES:
        raise InvalidInputError(f"held is one of {', '.join(HELD_CHOICES)}")
    g = gate.compute(conn, aid)
    if not g.safety_met:
        failed = "; ".join(c.detail for c in g.checks if not c.ok and not c.waivable)
        raise PolicyDeniedError(f"the go-live safety gates aren't met, and no override can waive"
                                f" them: {failed}")  # fmt: skip
    if not g.met:
        short = "; ".join(c.detail for c in g.checks if not c.ok)
        if not override:
            raise PolicyDeniedError(f"the go-live gate isn't met: {short}. An override"
                                    " (--override --reason) can waive only the review count and"
                                    " accuracy.")  # fmt: skip
        if not reason:
            raise InvalidInputError("an override needs a written reason (--reason)")
    return g


def held_by_age(conn: sqlite3.Connection, aid: str, now: datetime) -> dict[str, int]:
    cutoff = to_ts(now - timedelta(days=HELD_RUN_DAYS))
    row = conn.execute("SELECT count(*) AS n, sum(created_at >= ?) AS recent FROM items"
                       " WHERE address_id = ? AND status = 'held'",
                       (cutoff, aid)).fetchone()  # fmt: skip
    n, recent = int(row["n"]), int(row["recent"] or 0)
    return {"recent": recent, "older": n - recent}


def _release_held(conn: sqlite3.Connection, clock: Clock, aid: str, choice: str, *,
                  actor: str) -> dict[str, int]:  # fmt: skip
    """§6.2 on going live: recent held emails run; older ones stay held unless resolved by hand
    (the go-live step-up covers resolving payment or fraud emails)."""
    cutoff = to_ts(clock.now() - timedelta(days=HELD_RUN_DAYS))
    out = {"ran": 0, "resolved": 0, "still_held": 0, "failed": 0}
    rows = conn.execute("SELECT * FROM items WHERE address_id = ? AND status = 'held'"
                        " ORDER BY created_at", (aid,)).fetchall()  # fmt: skip
    for item in rows:
        sid = StableId(item["stable_id"])
        recent = item["created_at"] >= cutoff
        try:
            if choice == "resolve_all" or (choice == "resolve_older" and not recent):
                items.transition(conn, clock, sid, Status.RESOLVED_MANUAL,
                                 TransitionContext(stage=Stage.LIVE, stepup_verified=True),
                                 actor=actor, expected=Status.HELD)  # fmt: skip
                out["resolved"] += 1
            elif recent:
                items.transition(conn, clock, sid, Status.PROPOSED,
                                 TransitionContext(stage=Stage.LIVE), actor="service",
                                 expected=Status.HELD)  # fmt: skip
                decide.apply(conn, clock, sid, _held_plan(conn, item),
                             source=item["decision_source"] or "rule")  # fmt: skip
                out["ran"] += 1
            else:
                out["still_held"] += 1
        except Exception as exc:  # one email never stops the others; it stays where it got to
            log.error("stage.held_release_failed", stable_id=sid[:8], error_type=type(exc).__name__)
            out["failed"] += 1
    return out


def _held_plan(conn: sqlite3.Connection, item: sqlite3.Row) -> policy.Plan:
    """The held plan as it was, or a new one from your Fix (OD-157): the rule's actions, without
    asking the actor again."""
    if item["human_correction"]:
        ctx = decide.context(conn, item)
        fixed = replace(ctx, classification=ctx.classification
                        | json.loads(item["human_correction"]))  # fmt: skip
        p = policy.plan(fixed, policy.labels(load_schema_v1(), fixed.rules))
        p.to_actor = False
        return p
    doc: dict[str, Any] = json.loads(item["proposal"] or "{}").get("plan") or {}
    planned: list[dict[str, Any]] = doc.get("actions") or []
    return policy.Plan(
        str(doc.get("rule", "")),
        actions=[policy.Planned(str(a["name"]), a.get("target"), a["mode"]) for a in planned],
        high_risk=bool(doc.get("high_risk")),
        payment_or_fraud=bool(doc.get("payment_or_fraud")),
        actor=doc.get("actor"),
    )  # fmt: skip


def _live_text(g: gate.Gate, overriding: bool, released: dict[str, int]) -> str:
    out = " Go-live gate: " + ("met." if g.met else "NOT met; went live by override.")
    if overriding:
        out += " " + "; ".join(c.detail for c in g.checks if not c.ok) + "."
    if any(released.values()):
        out += (f" Held emails: {released['ran']} ran, {released['resolved']} marked handled by"
                f" hand, {released['still_held']} still held")  # fmt: skip
        out += f", {released['failed']} failed (see ecf logs)." if released["failed"] else "."
    return out


def tick(conn: sqlite3.Connection, clock: Clock) -> None:
    """Announce a newly met gate once; drop `live` to `assist` when the model changed (§9.3)."""
    digest = gate.current_digest()
    for a in addresses.list_addresses(conn):
        aid = a["address_id"]
        row = gate.stored(conn, aid)
        if a["stage"] == "live":
            if row is None or row["ollama_digest"] != digest:
                set_stage(conn, clock, aid, "assist", nonce=None, actor="service",
                          reason="the local model changed; back to assist until its gate"
                                 " passes (§9.3)")  # fmt: skip
            continue
        g = gate.compute(conn, aid)
        if g.met and (row is None or row["passed_at"] is None or row["ollama_digest"] != digest):
            with write_tx(conn):
                gate.record(conn, g, clock.now(), passed=True)
            _post(conn, clock, aid, "Ready for live",
                  f"{a['email']} meets its go-live gate ({g.reviewed} reviewed). When you're ready:"
                  f" ecf stage set {aid} live")  # fmt: skip


def _stage_text(frm: str, to: str, reason: str) -> str:
    what = {
        "shadow": "ecf decides and posts, and changes nothing in the mailbox.",
        "assist": "ecf also labels and flags; anything else it would do is held until live.",
        "live": "ecf acts by its policy: automatic where allowed, otherwise it asks you.",
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
