"""What the hourly digest offers from V1.3 (SPEC §5.1 step 9, §8.3, §9.5; OD-210; V1.3 step 4d).

- **Done automatically:** a count of the model-driven actions that ran since the last digest, by
  action. (Undo for hide actions arrives with the executor, V1.3 step 5.)
- **Approve all N reversible** (§9.5): the approvals waiting for this address whose actions are all
  reversible and which aren't excluded: outbound, the fraud guard, an unverified sender, any
  high-risk item (`local_high_risk`), regulator, `content_unscanned`, or a `high` address. The
  button binds the fixed set of grant IDs shown when the digest was posted; a click approves each
  one that is still waiting and still eligible, re-checked at the click (a grant that changed,
  expired or became ineligible is skipped and counted), one click each, never with step-up
  (none of these needs it: they are reversible and exclude every payment, fraud and regulator item).
- **Confirm sender category** (OD-210): items whose hide wasn't corroborated offer it. A
  confirmation counts toward the bank-detail trigger, so it always needs step-up at your computer
  (§9.6): the button answers only you, with the exact `ecf sender confirm` command to run there.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from email.utils import parseaddr
from typing import Any

from ecf.errors import ConflictError, EcfError
from ecf_server import approvals, cards, slack_admin, slack_in, slack_out
from ecf_server.chat import Button, RouteRef
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.precheck import fired

APPROVE_ALL = "approve_all"
CONFIRM = "confirm_category"
BATCH_KEY = "digest_batch:"  # + key: the grant IDs a button binds
BATCH_MAX = 20
CONFIRM_MAX = 5
EXCLUDED_TRIGGERS = frozenset({"fraud", "fraud_weak", "regulator", "unverified_payment"})


def eligible(conn: sqlite3.Connection, item: sqlite3.Row) -> bool:
    """May this waiting approval join "Approve all N reversible"? (§9.5 exclusions)"""
    if item["status"] != "awaiting_approval":
        return False
    doc: dict[str, Any] = json.loads(item["proposal"] or "{}")
    plan: dict[str, Any] = doc.get("plan") or {}
    actions = [str(a["name"]) for a in doc.get("actions", [])]
    facts: dict[str, Any] = json.loads(item["facts"] or "{}")
    labels = {a.get("target") for a in doc.get("actions", []) if a.get("name") == "label"}
    addr = conn.execute("SELECT sensitivity FROM addresses WHERE address_id = ?",
                        (item["address_id"],)).fetchone()  # fmt: skip
    return bool(
        actions
        and all(a in approvals.REVERSIBLE for a in actions)
        and not any(a in approvals.SENDS for a in actions)
        and plan.get("rule") != "fraud_guard"
        and not plan.get("high_risk", True)
        and not plan.get("payment_or_fraud", True)
        and not (fired(facts) & EXCLUDED_TRIGGERS)
        and not facts.get("content_unscanned")
        and not facts.get("quarantined")
        and "unverified_sender" not in labels
        and addr is not None
        and addr["sensitivity"] == "standard"
    )


def lines(conn: sqlite3.Connection, now: datetime, aid: str, rows: list[sqlite3.Row],
          key: str) -> tuple[list[str], list[Button]]:  # fmt: skip
    """The digest's V1.3 sections and buttons for one address."""
    text: list[str] = []
    buttons: list[Button] = []
    done: dict[str, int] = {}
    offers: list[sqlite3.Row] = []
    for r in rows:
        doc: dict[str, Any] = json.loads(r["proposal"] or "{}")
        if r["decision_source"] and r["status"] in ("executing", "executed"):
            for a in doc.get("actions", []):
                done[a["name"]] = done.get(a["name"], 0) + 1
        plan: dict[str, Any] = doc.get("plan") or {}
        if plan.get("offer_confirm"):
            offers.append(r)
    if done:
        text += [
            "",
            "Done automatically: " + ", ".join(f"{n} {k}" for k, n in sorted(done.items())),
        ]
    waiting = [r for r in conn.execute(
        "SELECT * FROM items WHERE address_id = ? AND status = 'awaiting_approval'"
        " ORDER BY created_at, stable_id", (aid,)) if eligible(conn, r)][:BATCH_MAX]  # fmt: skip
    grants = [g for g in (_grant_for(conn, r["stable_id"]) for r in waiting) if g]
    if grants:
        text += ["", f"Waiting for your approval, all reversible ({len(grants)}):",
                 *(_line(r) for r in waiting)]  # fmt: skip
        with write_tx(conn):
            slack_admin.put_setting(conn, BATCH_KEY + key, json.dumps(grants), to_ts(now),
                                    actor="service")  # fmt: skip
        buttons.append(Button(APPROVE_ALL, f"Approve all {len(grants)} reversible", key))
    if offers:
        text += ["", "Not hidden because the sender's category isn't confirmed:",
                 *(_line(r) for r in offers[:CONFIRM_MAX])]  # fmt: skip
        buttons += [Button(CONFIRM, f"Confirm category {r['stable_id'][: cards.SHORT_ID]}",
                           r["stable_id"]) for r in offers[:CONFIRM_MAX]]  # fmt: skip
    return text, buttons


def _grant_for(conn: sqlite3.Connection, sid: str) -> str | None:
    row = conn.execute("SELECT grant_id FROM grants WHERE stable_id = ? AND status = 'issued'"
                       " ORDER BY expires_at DESC LIMIT 1", (sid,)).fetchone()  # fmt: skip
    return str(row[0]) if row else None


def _line(r: sqlite3.Row) -> str:
    facts: dict[str, Any] = json.loads(r["facts"] or "{}")
    doc: dict[str, Any] = json.loads(r["proposal"] or "{}")
    what = ", ".join(str(a["name"]) + (f" {a['target']}" if a.get("target") else "")
                     for a in doc.get("actions", []))  # fmt: skip
    sender = cards.short_sender(cards.sender_line(r, facts))
    return f"{r['stable_id'][: cards.SHORT_ID]} {sender}: {cards.subject_line(r)[:60]} ({what})"


@slack_in.handles(APPROVE_ALL)
def _approve_all(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click) -> None:
    raw = slack_admin.setting(conn, BATCH_KEY + click.ref)
    grants: list[str] = json.loads(raw) if raw else []
    approved = skipped = 0
    for grant_id in grants:
        row = conn.execute("SELECT stable_id, status FROM grants WHERE grant_id = ?",
                           (grant_id,)).fetchone()  # fmt: skip
        item = conn.execute("SELECT * FROM items WHERE stable_id = ?",
                            (row["stable_id"],)).fetchone() if row else None  # fmt: skip
        if row is None or row["status"] != "issued" or item is None or not eligible(conn, item):
            skipped += 1
            continue
        try:
            approvals.approve(conn, clock, approvals.desktop, item["stable_id"],
                              actor=f"slack:{click.user}", grant_id=grant_id)  # fmt: skip
            approved += 1
        except EcfError:
            skipped += 1
    with write_tx(conn):
        conn.execute("DELETE FROM settings WHERE key = ?", (BATCH_KEY + click.ref,))
        conn.execute(
            "INSERT INTO audit (ts, event, actor, outcome, data)"
            " VALUES (?, 'approval.batch', ?, 'ok', ?)",
            (to_ts(clock.now()), f"slack:{click.user}",
             json.dumps({"approved": approved, "skipped": skipped})),
        )  # fmt: skip
    _tell(
        conn,
        clock,
        click,
        f"Approved {approved}."
        + (f" Skipped {skipped} that changed or were decided since the digest." if skipped else ""),
    )


@slack_in.handles(CONFIRM)
def _confirm(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click) -> None:
    item = conn.execute("SELECT * FROM items WHERE stable_id = ?", (click.ref,)).fetchone()
    if item is None or not item["classification"]:
        _tell(conn, clock, click, "Nothing to confirm here.")
        raise ConflictError("nothing to confirm")
    category = json.loads(item["classification"]).get("category", "")
    sender = parseaddr(str(item["sender"] or ""))[1] or "<sender>"
    _tell(conn, clock, click,
          "Confirming a sender's category counts for the bank-detail check, so it needs your"
          f" computer. Run: ecf sender confirm {sender} --category {category}"
          f" --address {item['address_id']}")  # fmt: skip


def _tell(conn: sqlite3.Connection, clock: Clock, click: slack_in.Click, text: str) -> None:
    if click.channel:
        slack_out.enqueue_ephemeral(conn, clock, route=RouteRef(click.channel), user=click.user,
                                    text=text)  # fmt: skip
