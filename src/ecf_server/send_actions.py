"""Carrying out approved sends: template replies and internal forwards (SPEC §8.4; OD-317, OD-321,
OD-322, OD-325; V1.5 step 3a).

They run in the address's check (mailbox_actions), after these checks at execution, each a refusal
that fails the item without a retry:
- the address is live and its outbound switch is on, and it has an SMTP server;
- the approved payload still matches (outbound_plan.check: the recipient, the template's text, the
  allow-list entry) and §8.4's guardrails still hold for the email (policy.send_refusal);
- the recipient isn't one of this install's monitored addresses (a reply there would come back in
  as new mail);
- a template reply is the first in its thread (the thread is the address, the sender and the
  thread's first Message-ID) and the first to that sender in 7 days.

A template reply is built from the template as approved (`Re:`, threaded under the email); a
forward attaches the email as it is in the mailbox now, which the check has just verified is the
item's (same content hash). Then `send.submit` records and sends it, once (OD-322): its Message-ID
is the grant's, so a retry after a temporary failure reuses it.

The SMTP connection comes from `sender_for`, which the service sets at start (the address's SMTP
server, its app password from the secret store at each connect).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from ecf_server import install_identity, outbound_plan, send
from ecf_server.actions import Planned
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.mail import MailSource
from ecf_server.mail.smtp import Sender, SendNotSentError
from ecf_server.outbound_msg import build_forward, build_reply

SENDS = outbound_plan.SENDS
SENDER_DAYS = 7  # one template reply per sender per 7 days (OD-325)
sender_for: Callable[[sqlite3.Connection, str], Sender] | None = None


def refusal(  # noqa: PLR0911 - one return per check, in order
    conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, a: Planned
) -> str | None:
    """Why this send may not run now, or None (see the module docstring)."""
    from ecf_server import decide, policy  # noqa: PLC0415 - decide imports the executors' modules

    addr = conn.execute("SELECT * FROM addresses WHERE address_id = ?",
                        (item["address_id"],)).fetchone()  # fmt: skip
    if addr["stage"] != "live":
        return f"the address is in {addr['stage']}: sends need live"
    if not addr["outbound"]:
        return "outbound is off for this address"
    if not addr["smtp_host"]:
        return "no SMTP server set for this address (`ecf address set --smtp-host`)"
    why = outbound_plan.check(conn, item, a.name, a.target, a.payload)
    if why is not None:
        return why
    ctx = decide.context(conn, item)
    why = policy.send_refusal(a.name, ctx.facts, fraud_signal=policy.fraud_signal(ctx))
    if why is not None:
        return why
    to = str((a.payload or {}).get("to", "")).lower()
    monitored = {str(r[0]).lower() for r in conn.execute(
        "SELECT email FROM addresses WHERE removed_at IS NULL")}  # fmt: skip
    if to in monitored:
        return "the recipient is a monitored address"
    if a.name == "reply_template":
        return _reply_limits(conn, clock, item, _grant(conn, item["stable_id"]))
    return None


def thread_hash(item: sqlite3.Row) -> str:
    facts: dict[str, Any] = json.loads(item["facts"] or "{}")
    root = facts.get("thread_root") or item["message_id"] or item["stable_id"]
    key = f"{item['address_id']}|{facts.get('sender_hash') or ''}|{root}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _reply_limits(conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row,
                  grant_id: str | None) -> str | None:  # fmt: skip
    """OD-325, counting sends that may have gone (pending, sent, unknown), never this grant's."""
    mine = conn.execute("SELECT 1 FROM sent WHERE grant_id = ?", (grant_id,)).fetchone()
    if mine is not None:
        return None  # a retry of this grant's own send
    row = conn.execute("SELECT template_replies FROM threads WHERE thread_hash = ?",
                       (thread_hash(item),)).fetchone()  # fmt: skip
    if row is not None and row["template_replies"] >= 1:
        return "this thread already got a template reply"
    facts: dict[str, Any] = json.loads(item["facts"] or "{}")
    since = to_ts(clock.now() - timedelta(days=SENDER_DAYS))
    recent = conn.execute(
        "SELECT 1 FROM sent s JOIN items i USING (stable_id) WHERE s.address_id = ?"
        " AND s.kind = 'reply' AND s.status != 'failed' AND s.sent_at >= ?"
        " AND json_extract(i.facts, '$.sender_hash') = ?",
        (item["address_id"], since, facts.get("sender_hash")),
    ).fetchone()
    return "this sender got a template reply in the last 7 days" if recent else None


def run(conn: sqlite3.Connection, clock: Clock, src: MailSource, item: sqlite3.Row,
        a: Planned, uid: int) -> dict[str, Any]:  # fmt: skip
    """Build and send one approved send; returns what `proposal.done` records."""
    if sender_for is None:
        raise SendNotSentError("sending isn't set up in this service", retryable=False)
    payload = a.payload or {}
    addr = conn.execute("SELECT email FROM addresses WHERE address_id = ?",
                        (item["address_id"],)).fetchone()  # fmt: skip
    frm, to = str(addr["email"]), str(payload["to"])
    grant_id = _grant(conn, item["stable_id"])
    mid = send.message_id_for(conn, grant_id or "", frm.rpartition("@")[2])
    header = install_identity.header_value(conn)
    if a.name == "reply_template":
        t = outbound_plan.enabled_templates(conn)[str(a.target)]
        r = outbound_plan.render(item, t)
        built = build_reply(from_addr=frm, to_addr=to, subject=r.subject, body=r.body,
                            in_reply_to=item["message_id"], references=None,
                            install_header=header, date=clock.now(), message_id=mid)  # fmt: skip
        kind = "reply"
        _count_thread(conn, item, grant_id)
    else:
        original = src.fetch(uid)
        if original is None:
            raise SendNotSentError("the email is gone from INBOX", retryable=False)
        cover = (f"Forwarded by ecf from {item['address_id']} (item {item['stable_id'][:8]}).\n"
                 "The original email is attached unchanged.\n")  # fmt: skip
        built = build_forward(from_addr=frm, to_addr=to, original=original,
                              original_subject=str(item["subject"] or ""), cover=cover,
                              install_header=header, date=clock.now(), message_id=mid)  # fmt: skip
        kind = "forward"
    out = send.Outgoing(item["address_id"], kind, frm, (to,), built, stable_id=item["stable_id"],
                        grant_id=grant_id)  # fmt: skip
    try:
        send.submit(conn, clock, sender_for(conn, item["address_id"]), out, src)
    except SendNotSentError as exc:
        if not exc.retryable and kind == "reply":
            _uncount_thread(conn, item)
        raise
    return {"name": a.name, "target": a.target, "to": to, "message_id": built.message_id}


def _count_thread(conn: sqlite3.Connection, item: sqlite3.Row, grant_id: str | None) -> None:
    """Count the reply against its thread before it goes, once per grant (a retry doesn't)."""
    if conn.execute("SELECT 1 FROM sent WHERE grant_id = ?", (grant_id,)).fetchone():
        return
    with write_tx(conn):
        conn.execute(
            "INSERT INTO threads (thread_hash, address_id, template_replies) VALUES (?, ?, 1)"
            " ON CONFLICT (thread_hash) DO UPDATE SET template_replies = template_replies + 1",
            (thread_hash(item), item["address_id"]),
        )


def _uncount_thread(conn: sqlite3.Connection, item: sqlite3.Row) -> None:
    """A reply the server refused for good never went: it doesn't use up its thread."""
    with write_tx(conn):
        conn.execute("UPDATE threads SET template_replies = max(template_replies - 1, 0)"
                     " WHERE thread_hash = ?", (thread_hash(item),))  # fmt: skip


def _grant(conn: sqlite3.Connection, sid: str) -> str | None:
    """The grant the runner consumed for this execution (the newest consumed one)."""
    row = conn.execute(
        "SELECT grant_id FROM grants WHERE stable_id = ? AND status = 'consumed'"
        " ORDER BY consumed_at DESC, rowid DESC LIMIT 1",
        (sid,),
    ).fetchone()
    return None if row is None else str(row["grant_id"])
