"""The Slack summary of each `ecf backfill` and `ecf reprocess` run (OD-507 §3.4; D8, P10): see
`scans.py` for what it shows and when. Counts and fixed wording; item lines in the burst-card
style (sender, subject, short id), as escalation cards show them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any

from ecf_server import cards, marked, retention, scans, slack_admin, slack_out, slack_routes
from ecf_server.chat import Card, RouteRef
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.scans import PREFIX, REPLY_LINES

_OF_RUN = scans.OF_RUN
TICK_KEYS = ("reported", "replies", "reply_lines", "head", "settled")  # this tick's own fields


def head_card(doc: dict[str, Any], n: dict[str, int]) -> Card:
    what = "Scan" if doc["kind"] == "scan" else "Reprocess"
    until = (doc["until"] or "now")[:16].replace("T", " ")
    since = str(doc["since"])[:16].replace("T", " ")
    if doc["settled"] or doc["outcome"]:
        state = f"{doc['outcome'] or 'finished'}" + (
            "" if doc["settled"] else ", items still waiting for /ecf-review"
        )
    else:
        state = "running"
    fields = (
        ("Address", str(doc["email"])),
        ("Range (UTC)", f"{since} to {until}"),
        ("Mode", str(doc["mode"])),
        ("State", state),
        ("Fetched" if doc["kind"] == "scan" else "Items", str(n["fetched"])),
        ("Recorded", str(n["recorded"])),
        ("Classified", str(n["classified"])),
        ("Waiting for /ecf-review", str(n["waiting"])),
        ("Approvals pending", f"{n['approvals']}"
         + (f" (ecf approve --scan {doc['run_id']})" if n["approvals"] else "")),
        ("Fraud", str(n["fraud"])),
        ("Regulator", str(n["regulator"])),
    )  # fmt: skip
    note = ("Old mail: no card of its own; its hides need approval and aren't in Approve all."
            " While it waits, new mail is reviewed first.")  # fmt: skip
    return Card(f"{what} of {doc['email']} ({doc['run_id']})", fields=fields,
                text="\n".join(doc["lines"]), note=note)  # fmt: skip


def tick(conn: sqlite3.Connection, clock: Clock) -> int:
    """Post or edit each open run's summary and its replies; returns how many runs are open.
    Waits for the summary channel; never posts the same text twice."""
    route = slack_routes.summary_route(conn)
    ident = slack_admin.identity(conn)
    member = ident.member if ident else ""
    open_runs = 0
    cutoff = to_ts(clock.now() - timedelta(days=retention.days(conn)))
    for doc in scans.runs(conn):
        if doc["settled"]:
            if doc["finished_at"] and doc["finished_at"] < cutoff:
                with write_tx(conn):
                    conn.execute("DELETE FROM settings WHERE key = ?", (PREFIX + doc["run_id"],))
            continue
        open_runs += 1
        n = scans.counts(conn, doc["run_id"])
        if doc["finished_at"] and not n["waiting"]:
            doc["settled"] = True  # settles, and is pruned later, with or without Slack (L1)
        if route is not None:
            card = head_card(doc, n)
            text = json.dumps([card.title, card.fields, card.text])
            if text != doc["head"]:  # the head first: replies thread under it (review M2)
                slack_out.enqueue_post(conn, clock, key=marked.head_key(doc["run_id"]),
                                       route=route, card=card)  # fmt: skip
                doc["head"] = text
            _replies(conn, clock, doc, route, member)
        mine = {k: doc[k] for k in TICK_KEYS}
        scans.update(conn, clock, doc["run_id"], lambda d, mine=mine: d.update(mine))
    return open_runs


def _replies(conn: sqlite3.Connection, clock: Clock, doc: dict[str, Any], route: RouteRef,
             member: str) -> None:  # fmt: skip
    """Thread replies for the run's fraud, regulator and escalated items not yet reported."""
    seen: set[str] = set(doc["reported"])
    found = conn.execute(
        f"SELECT * FROM items WHERE {_OF_RUN} AND (coalesce(json_array_length(facts,"  # noqa: S608
        " '$.triggers.fraud'), 0) > 0 OR coalesce(json_array_length(facts,"
        " '$.triggers.regulator'), 0) > 0 OR stable_id IN (SELECT stable_id FROM escalations))"
        " ORDER BY created_at, stable_id", {"run": doc["run_id"]}).fetchall()  # fmt: skip
    rows = [r for r in found if r["stable_id"] not in seen]
    if not rows:
        return
    title = (
        "Found in old mail, no action taken"
        if doc["mode"] == "records only"
        else "Found in old mail"
        if doc["kind"] == "scan"
        else "Found while reprocessing"
    )
    head = marked.head_key(doc["run_id"])
    reply: int = doc["replies"]
    cur: list[list[str]] = doc["reply_lines"]
    reported: list[str] = doc["reported"]

    def post() -> None:
        fraud = any(kind == "fraud" for _, kind in cur)
        card = Card(
            title,
            text="\n".join(text for text, _ in cur),
            note=cards.PAYMENT_NOTE if fraud else "",
            mention=member if fraud else "",
        )
        slack_out.enqueue_post(
            conn, clock, key=f"{head}:r{reply}", route=route, card=card, thread_key=head
        )  # an edit while the reply has room

    for r in rows:
        if not reply or len(cur) >= REPLY_LINES:
            reply, cur = reply + 1, []
        cur.append([cards.escalation_line(r), cards.kind(json.loads(r["facts"] or "{}"))])
        reported.append(r["stable_id"])
        if len(cur) == REPLY_LINES:
            post()
    if len(cur) < REPLY_LINES:
        post()
    doc |= {"replies": reply, "reply_lines": cur, "reported": reported}
