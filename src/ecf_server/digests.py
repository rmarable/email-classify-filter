"""Hourly digests and Undo (SPEC §5.4, §10.1; V1.2 step 8b).

**Digests:** one post per address channel, at most hourly and only inside business hours; mail that
arrived off-hours rolls into the first digest of the workday. It opens with how many messages came
in ("Caught up: N messages since <time>" after a gap of more than 2 hours), then the sections a
digest owns: weak fraud signals (a first-time sender asking for payment, OD-171) and unverified
payment senders (OD-065), each with what was done, and how many emails weren't fully scanned.
Escalations are not repeated here: they have their own cards. Buttons: Pause (this address), and
Undo on items it may undo. No digest when nothing came in (no idle posts). `ecf digest <address>`
posts one now, at any hour, and restarts the hourly clock (operator decision 2026-09-30).

**Undo** removes the labels and flag ecf added to an email outside shadow. It is never offered on,
and never runs for, an item with a fraud or regulator signal (OD-213): in V1.2 that leaves
unverified payment senders (label `unverified_sender` and the flag; removing them hides nothing).
A click marks the item and makes its address due now; the next check (within a minute), which holds
the address lease and has the mailbox open, undoes it and tells you (`run_undos`).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from ecf.errors import ConflictError
from ecf_server import (
    addresses,
    approvals,
    cards,
    checks,
    pause,
    schedule,
    slack_admin,
    slack_in,
    slack_out,
    slack_routes,
)
from ecf_server.actions import MessageChangedError, Planned, undo
from ecf_server.chat import Button, Card, RouteRef
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.mail import MailSource
from ecf_server.precheck import fired

EVERY = timedelta(hours=1)
GAP = timedelta(hours=2)
SECTION_MAX = 15
UNDO_MAX = 20
UNDO = "undo"
LAST = "slack_digest_at:"  # + address_id
NEVER_UNDONE = frozenset({"suspicious", "regulatory"})  # fraud and regulator labels (OD-213)


def business_hours(conn: sqlite3.Connection, address_id: str) -> dict[str, Any]:
    s = schedule.settings(conn, address_id, schedule.Power(laptop=False, on_ac=True))
    return dict(s["business_hours"])


def run(conn: sqlite3.Connection, clock: Clock) -> int:
    """Post each address's digest when due; returns how many were posted."""
    now = clock.now()
    posted = 0
    for (aid,) in conn.execute(
        "SELECT address_id FROM addresses WHERE removed_at IS NULL ORDER BY address_id"
    ).fetchall():
        route = slack_routes.route_for(conn, aid)
        last = slack_admin.setting(conn, LAST + aid)
        if route is None:
            continue
        if not last:  # first sight: start from now, not from all of history
            _set_last(conn, aid, now)
            continue
        since = from_ts(last)
        if now - since < EVERY or not schedule.in_business_hours(now, business_hours(conn, aid)):
            continue
        card = build(conn, aid, since, now)
        if card is not None:
            slack_out.enqueue_post(conn, clock, key=f"digest:{aid}:{to_ts(now)}", route=route,
                                   card=card, identity=slack_routes.identity(aid))  # fmt: skip
            posted += 1
        _set_last(conn, aid, now)
    return posted


def post_now(conn: sqlite3.Connection, clock: Clock, ref: str) -> dict[str, Any]:
    """`ecf digest <address>`: this address's digest now, whatever the hour, covering mail since
    the last one; the hourly clock restarts from now, so nothing is listed twice."""
    aid = addresses.get_address(conn, ref)["address_id"]
    route = slack_routes.route_for(conn, aid)
    if route is None:
        raise ConflictError(f"{aid} has no Slack channel yet: see `ecf slack status`")
    now = clock.now()
    last = slack_admin.setting(conn, LAST + aid)
    since = from_ts(last) if last else now - EVERY
    card = build(conn, aid, since, now)
    if card is not None:
        slack_out.enqueue_post(conn, clock, key=f"digest:{aid}:{to_ts(now)}", route=route,
                               card=card, identity=slack_routes.identity(aid))  # fmt: skip
    _set_last(conn, aid, now)
    return {"address_id": aid, "posted": card is not None, "since": to_ts(since)}


def build(conn: sqlite3.Connection, aid: str, since: datetime, now: datetime) -> Card | None:
    rows = conn.execute(
        "SELECT * FROM items WHERE address_id = ? AND created_at > ? AND created_at <= ?"
        " ORDER BY created_at, stable_id",
        (aid, to_ts(since), to_ts(now)),
    ).fetchall()
    if not rows:
        return None
    weak: list[str] = []
    unverified: list[str] = []
    undoable: list[str] = []
    unscanned = 0
    for r in rows:
        facts: dict[str, Any] = json.loads(r["facts"] or "{}")
        names = fired(facts)
        unscanned += bool(facts.get("content_unscanned"))
        if "fraud_weak" in names and "fraud" not in names:
            weak.append(_line(r, facts))
        elif "unverified_payment" in names and "fraud" not in names:
            unverified.append(_line(r, facts))
        if may_undo(facts):
            undoable.append(r["stable_id"])
    when = since.strftime("%H:%M UTC")
    opener = (f"Caught up: {len(rows)} message(s) since {since.strftime('%Y-%m-%d %H:%M UTC')}."
              " Anything waiting: ecf inbox" if now - since > GAP
              else f"{len(rows)} new message(s) since {when}.")  # fmt: skip
    text = [opener]
    for title, lines in (("Weak fraud signals (first-time sender asking for payment):", weak),
                         ("Payment email from unverified senders:", unverified)):  # fmt: skip
        if lines:
            text += ["", title, *lines[:SECTION_MAX]]
            if len(lines) > SECTION_MAX:
                text.append(f"... and {len(lines) - SECTION_MAX} more: ecf inbox")
    if unscanned:
        text += ["", f"Not fully scanned: {unscanned}"]
    buttons = [Button(UNDO, f"Undo {sid[: cards.SHORT_ID]}", sid) for sid in undoable[:UNDO_MAX]]
    buttons.append(Button(pause.PAUSE, f"Pause {aid}", aid))
    note = cards.PAYMENT_NOTE if (weak or unverified) else ""
    return Card(f"Digest: {aid}", text="\n".join(text), buttons=tuple(buttons), note=note)


def _line(r: sqlite3.Row, facts: dict[str, Any]) -> str:
    sender = cards.short_sender(cards.sender_line(r, facts))
    return (f"{r['stable_id'][: cards.SHORT_ID]} {sender}: "
            f"{cards.subject_line(r)[:60]} ({cards.done(facts)})")  # fmt: skip


def _set_last(conn: sqlite3.Connection, aid: str, now: datetime) -> None:
    with write_tx(conn):
        slack_admin.put_setting(conn, LAST + aid, to_ts(now), to_ts(now), actor="service")


# ---- Undo -----------------------------------------------------------------------------------


def undoable_actions(facts: dict[str, Any]) -> list[Planned]:
    """The labels and flag the pre-check added that Undo may remove."""
    p: dict[str, Any] = facts.get("precheck") or {}
    out: list[Planned] = []
    executed: list[Any] = p.get("executed") or []
    for done in executed:
        if done == "flag":
            out.append(Planned("flag"))
        elif str(done).startswith("label "):
            label = str(done).removeprefix("label ")
            if label not in NEVER_UNDONE:
                out.append(Planned("label", label))
    return out


def may_undo(facts: dict[str, Any]) -> bool:
    p: dict[str, Any] = facts.get("precheck") or {}
    return (not approvals.fraud_or_regulator(facts) and bool(undoable_actions(facts))
            and not p.get("undo"))  # fmt: skip


@slack_in.handles(UNDO)
def _undo_click(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click) -> None:
    item = conn.execute("SELECT * FROM items WHERE stable_id = ?", (click.ref,)).fetchone()
    facts: dict[str, Any] = json.loads(item["facts"] or "{}") if item else {}
    if item is None or not may_undo(facts):
        _tell(conn, clock, click, "Nothing to undo here (fraud and regulator labels stay).")
        raise ConflictError("nothing to undo")
    facts["precheck"] = dict(facts.get("precheck") or {}) | {"undo": "queued"}
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("UPDATE items SET facts = ?, updated_at = ? WHERE stable_id = ?",
                     (json.dumps(facts, sort_keys=True), now, item["stable_id"]))  # fmt: skip
        conn.execute(  # checked within a minute, under the address lease
            "INSERT INTO check_state (address_id, next_due_at) VALUES (?, ?)"
            " ON CONFLICT (address_id) DO UPDATE SET next_due_at = excluded.next_due_at",
            (item["address_id"], now),
        )
        conn.execute(
            "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
            " VALUES (?, ?, ?, 'action.undo_requested', ?, 'ok', '{}')",
            (now, item["address_id"], item["stable_id"], f"slack:{click.user}"),
        )
    _tell(conn, clock, click, f"Undo queued for {item['stable_id'][:8]}: done at the next check,"
                              " within a minute while your computer is awake.")  # fmt: skip


def run_undos(
    conn: sqlite3.Connection,
    clock: Clock,
    src: MailSource,
    address_id: str,
    *,
    install: str,
    max_scan_bytes: int,
) -> int:
    """Inside a check (lease held, mailbox open): carry out queued Undos for this address."""
    rows = conn.execute(
        "SELECT * FROM items WHERE address_id = ?"
        " AND json_extract(facts, '$.precheck.undo') = 'queued'",
        (address_id,),
    ).fetchall()
    for item in rows:
        facts: dict[str, Any] = json.loads(item["facts"] or "{}")
        actions = [] if approvals.fraud_or_regulator(facts) else undoable_actions(facts)
        try:
            done = undo(conn, clock, src, item, actions, install=install,
                        max_scan_bytes=max_scan_bytes) if actions else []  # fmt: skip
            result, text = "done", f"Undone for {item['stable_id'][:8]}: removed {', '.join(done)}."
        except MessageChangedError as exc:
            result, text = f"failed: {exc.detail}", f"Couldn't undo {item['stable_id'][:8]}: " \
                f"{exc.detail}"  # fmt: skip
        facts["precheck"] = dict(facts.get("precheck") or {}) | {"undo": result}
        with write_tx(conn):
            conn.execute("UPDATE items SET facts = ? WHERE stable_id = ?",
                         (json.dumps(facts, sort_keys=True), item["stable_id"]))  # fmt: skip
        _tell_member(conn, clock, address_id, text)
        log.info("action.undo", address_id=address_id, result=result.split(":")[0])
    return len(rows)


def _tell(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click, text: str) -> None:
    if click.channel:
        slack_out.enqueue_ephemeral(conn, clock, route=RouteRef(click.channel), user=click.user,
                                    text=text)  # fmt: skip


def _tell_member(conn: sqlite3.Connection, clock: Clock, address_id: str, text: str) -> None:
    ident = slack_admin.identity(conn)
    route = slack_routes.route_for(conn, address_id)
    if ident is not None and ident.member and route is not None:
        slack_out.enqueue_ephemeral(conn, clock, route=route, user=ident.member, text=text)


def _undos_in_check(conn: sqlite3.Connection, clock: Clock, src: MailSource, address_id: str,
                    install: str, max_scan_bytes: int) -> int:  # fmt: skip
    return run_undos(conn, clock, src, address_id, install=install, max_scan_bytes=max_scan_bytes)


checks.IN_LEASE.append(_undos_in_check)
