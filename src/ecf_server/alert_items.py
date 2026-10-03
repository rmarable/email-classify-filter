"""Alert email about mail itself (SPEC §13.3; OD-065, OD-114, OD-334; V1.5 step 7b).

These go by email only (Slack already has the escalation card or the digest line), whenever the
install-wide `alerts.routes` include `email` (OD-334). Like every alert email they name the
address and item IDs and give commands, never a subject, sender or excerpt (OD-316); the caps
(10 an hour per type, then an hourly roll-up) are alert_mail's.

- **Possible Fraud Attempt:** one per escalated item with a fraud signal: a fraud trigger,
  quarantine, the fraud guard rule, a classifier fraud risk of medium or high, or a vendor change
  request. The weak first-time + payment case isn't escalated, so it goes to the digest only.
- **Regulatory Mail Notice:** one per escalated item with the regulator trigger, the `regulatory`
  category or rule, and no fraud signal.
- **Unverified Payment Sender:** on `high` addresses only, at most one email an hour listing the
  items rule 1a flagged since the last one (a human-verified sender doesn't fire it, OD-065);
  items escalated as fraud are left to their own email.

Mail that answers or repeats an alert email (`alert_echo`, own_mail.py) never raises one (OD-330).
Each escalation, and each unverified item, is handled once, whether or not email is a route.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any

from ecf_server import alert_mail, alerts, slack_admin
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx

SHORT = 8  # cards.SHORT_ID
FRAUD = "Possible Fraud Attempt"
REGULATORY = "Regulatory Mail Notice"
UNVERIFIED = "Unverified Payment Sender"
UNVERIFIED_MARK = "alerts.unverified_mark"  # created_at of the last item handled
UNVERIFIED_SENT = "alerts.unverified_sent_at"
BATCH_EVERY = timedelta(hours=1)
LIST = 20  # item IDs listed per address


def emailed(conn: sqlite3.Connection) -> bool:
    return "email" in alerts.install_routes(conn) and alert_mail.config(conn) is not None


def sweep(conn: sqlite3.Connection, clock: Clock) -> int:
    """Escalations not yet handled: an email each for fraud and regulatory mail (the timer)."""
    rows = conn.execute(
        "SELECT e.stable_id, e.address_id, i.facts, i.classification, i.proposal"
        " FROM escalations e JOIN items i USING (stable_id) WHERE e.emailed_at IS NULL"
        " ORDER BY e.created_at"
    ).fetchall()
    on = emailed(conn)
    now = to_ts(clock.now())
    for r in rows:
        kind, why = _kind(r)
        if on and kind is not None:
            short = str(r["stable_id"])[:SHORT]
            if kind == FRAUD:
                text = (f"{r['address_id']}: possible fraud in item {short} ({why}). It is flagged"
                        " and labelled suspicious; nothing was hidden or answered. Look before"
                        f" paying or changing anything: ecf item show {short}, or its card in"
                        " Slack.")  # fmt: skip
            else:
                text = (f"{r['address_id']}: regulatory mail in item {short} ({why}). It is"
                        f" flagged and labelled regulatory. ecf item show {short}, or its card in"
                        " Slack.")  # fmt: skip
            alert_mail.queue(conn, clock, f"{alerts.PREFIX} {kind}", text, slack_done=True)
        with write_tx(conn):
            conn.execute("UPDATE escalations SET emailed_at = ? WHERE stable_id = ?",
                         (now, r["stable_id"]))  # fmt: skip
    return len(rows)


def unverified_batch(conn: sqlite3.Connection, clock: Clock) -> int:
    """At most once an hour: one email listing `high` addresses' unverified payment items since
    the last one. Returns how many items it listed."""
    now = clock.now()
    sent_at = slack_admin.setting(conn, UNVERIFIED_SENT)
    if sent_at and now - from_ts(sent_at) < BATCH_EVERY:
        return 0
    mark = slack_admin.setting(conn, UNVERIFIED_MARK) or "0"
    rows = conn.execute(
        "SELECT i.stable_id, i.address_id, i.created_at, i.facts FROM items i"
        " JOIN addresses a USING (address_id) WHERE a.sensitivity = 'high'"
        " AND a.removed_at IS NULL AND i.created_at > ?"
        " AND json_extract(i.facts, '$.triggers.unverified_payment')"
        " AND i.stable_id NOT IN (SELECT stable_id FROM escalations) ORDER BY i.created_at",
        (mark,),
    ).fetchall()
    if not rows:
        return 0
    picked = [r for r in rows if not json.loads(r["facts"] or "{}").get("alert_echo")]
    listed = 0
    if picked and emailed(conn):
        per: dict[str, list[str]] = {}
        for r in picked:
            per.setdefault(str(r["address_id"]), []).append(str(r["stable_id"])[:SHORT])
        lines: list[str] = []
        for aid, ids in sorted(per.items()):
            more = f" and {len(ids) - LIST} more" if len(ids) > LIST else ""
            lines.append(f"{aid}: {len(ids)} (items {', '.join(ids[:LIST])}{more});"
                         f" ecf inbox --address {aid}")  # fmt: skip
        text = ("Payment mail from senders ecf couldn't authenticate (no DMARC result), on your"
                " `high` addresses since the last notice. Each is flagged and labelled"
                " unverified_sender; confirm a sender you trust with `ecf sender set-verified`."
                "\n\n"
                + "\n".join(lines))  # fmt: skip
        alert_mail.queue(conn, clock, f"{alerts.PREFIX} {UNVERIFIED}", text, slack_done=True)
        listed = len(picked)
    ts = to_ts(now)
    with write_tx(conn):
        slack_admin.put_setting(conn, UNVERIFIED_MARK, str(rows[-1]["created_at"]), ts,
                                actor="service")  # fmt: skip
        if listed:
            slack_admin.put_setting(conn, UNVERIFIED_SENT, ts, ts, actor="service")
    return listed


def _kind(r: sqlite3.Row) -> tuple[str | None, str]:
    facts: dict[str, Any] = json.loads(r["facts"] or "{}")
    if facts.get("alert_echo"):
        return None, ""  # OD-330: never an alert email about mail answering one
    cls: dict[str, Any] = json.loads(r["classification"] or "{}")
    plan: dict[str, Any] = (json.loads(r["proposal"] or "{}").get("plan")) or {}
    t: dict[str, Any] = facts.get("triggers") or {}
    fraud = [why for hit, why in (
        (t.get("fraud"), "a fraud trigger"),
        (facts.get("quarantined"), "quarantined"),
        (plan.get("rule") == "fraud_guard", "the fraud guard rule"),
        (cls.get("fraud_risk") in ("medium", "high"), f"fraud risk {cls.get('fraud_risk')}"),
        (cls.get("category") == "vendor_change_request", "a vendor change request"),
    ) if hit]  # fmt: skip
    if fraud:
        return FRAUD, ", ".join(fraud)
    reg = [why for hit, why in (
        (t.get("regulator"), "a regulator trigger"),
        (cls.get("category") == "regulatory" or plan.get("rule") == "regulatory",
         "classified regulatory"),
    ) if hit]  # fmt: skip
    if reg:
        return REGULATORY, ", ".join(reg)
    return None, ""
