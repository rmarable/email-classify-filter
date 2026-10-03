"""Sending one message and settling what became of it (SPEC §6.2, §8.4, §15.4; OD-322).

A send belongs to one grant and has one Message-ID, chosen before the first attempt and reused by
every retry (`message_id_for`). `submit` records it in `sent` as `pending` and commits (the
database runs `synchronous=FULL`) before anything goes to the server, then:

- the server accepted it: `sent`; the sent copy is settled below;
- not sent and worth retrying (a network error, a 4xx): stays `pending`, the error propagates and
  the action job retries with the same Message-ID;
- not sent for good (a 5xx, too large, no STARTTLS): `failed`;
- handed over without a final reply: `unknown`, and SendOutcomeUnknownError propagates; the
  action job never retries it (execute.py).

A row found already `sent` is not sent again (a crash between the send and the item's update);
one found `unknown` or `failed` raises the matching error without contacting the server.

**Sent copies** (§8.4): ecf keeps no full message on disk (§12.4), so its copy is appended to the
Sent folder (marked read) right after the send, from the message in memory, unless the provider is
known to save sent mail itself. Where that isn't known yet the row is marked `copy = 'check'`, and
`settle` on the next check counts the messages in Sent with that Message-ID: two means the provider
saved one too, so ecf deletes its own and records that the provider saves sent mail; one means it
doesn't (`probe.saves_sent`). A provider that saves a little late isn't mistaken for one that
doesn't, and nothing about the message is kept. Alerts get no copy.

`settle`, run by each address's check, works through the rows a send left open:
- **unknown outcomes**, only where the provider is known to save sent mail: found in the Sent
  folder → `sent`; missing on 2 later checks → `failed` (OD-322). Elsewhere nothing can prove
  either way, so they stay `unknown` for a person (`ecf item show`).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ecf.errors import MailUnavailableError
from ecf.ids import StableId
from ecf.status import Status
from ecf_server import checks, items, probe
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.mail import MailSource
from ecf_server.mail.smtp import Sender, SendNotSentError, SendOutcomeUnknownError
from ecf_server.outbound_msg import Built, new_message_id
from ecf_server.state_machine import TransitionContext

SENT_ROLE = "\\Sent"
MISSES_TO_FAIL = 2  # later checks that searched the Sent folder in vain (OD-322)
KINDS = ("reply", "forward", "alert")


def message_id_hash(message_id: str) -> str:
    return hashlib.sha256(message_id.encode("utf-8")).hexdigest()


def message_id_for(conn: sqlite3.Connection, grant_id: str, domain: str) -> str:
    """The Message-ID this grant's send uses: the one already recorded, or a new one."""
    row = conn.execute("SELECT message_id FROM sent WHERE grant_id = ?", (grant_id,)).fetchone()
    return str(row["message_id"]) if row is not None else new_message_id(domain)


@dataclass(frozen=True)
class Outgoing:
    address_id: str
    kind: str  # reply | forward | alert
    mail_from: str
    rcpt: tuple[str, ...]
    built: Built
    stable_id: str | None = None
    grant_id: str | None = None


def submit(conn: sqlite3.Connection, clock: Clock, sender: Sender, out: Outgoing,
           mail: MailSource | None = None) -> None:  # fmt: skip
    """Record, then send once; see the module docstring for what each outcome leaves behind.
    `mail` (the address's open mailbox) receives the sent copy; alerts pass none."""
    if out.kind not in KINDS:
        raise ValueError(f"unknown send kind {out.kind!r}")
    key = message_id_hash(out.built.message_id)
    row = conn.execute("SELECT status FROM sent WHERE message_id_hash = ?", (key,)).fetchone()
    if row is not None and row["status"] == "sent":
        return  # it went before a crash; never twice
    if row is not None and row["status"] == "unknown":
        raise SendOutcomeUnknownError("an earlier attempt may have sent it; not sending again")
    if row is not None and row["status"] == "failed":
        raise SendNotSentError("the server refused it before; not trying again", retryable=False)
    now = to_ts(clock.now())
    if row is None:
        with write_tx(conn):
            conn.execute(
                "INSERT INTO sent (message_id_hash, address_id, content_hash, kind, sent_at,"
                " message_id, stable_id, grant_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?,"
                " 'pending')",
                (key, out.address_id, out.built.content_hash, out.kind, now,
                 out.built.message_id, out.stable_id, out.grant_id),
            )  # fmt: skip
    try:
        sender.send(out.built.raw, out.mail_from, out.rcpt)
    except SendOutcomeUnknownError:
        _settle_row(conn, clock, key, "unknown", settled=False)
        log.warning("send.outcome_unknown", address_id=out.address_id, kind=out.kind)
        raise
    except SendNotSentError as exc:
        if not exc.retryable:
            _settle_row(conn, clock, key, "failed")
        raise
    with write_tx(conn):
        done = to_ts(clock.now())
        conn.execute("UPDATE sent SET status = 'sent', settled_at = ?, sent_at = ?"
                     " WHERE message_id_hash = ?", (done, done, key))  # fmt: skip
    log.info("send.sent", address_id=out.address_id, kind=out.kind)
    if mail is not None and out.kind != "alert":
        _copy_after_send(conn, mail, out, key)


def _copy_after_send(conn: sqlite3.Connection, mail: MailSource, out: Outgoing, key: str) -> None:
    """Append ecf's copy to Sent unless the provider is known to keep one (module docstring). A
    failure here never fails the send: the message went."""
    folder, saves = _sent_folder(conn, out.address_id)
    if folder is None:
        copy = "none"
    elif saves is True:
        copy = "provider"
    else:
        try:
            mail.append(folder, out.built.raw, ["\\Seen"])
            copy = "ecf" if saves is False else "check"
        except MailUnavailableError:
            copy = "none"
            log.warning("send.copy_failed", address_id=out.address_id)
    with write_tx(conn):
        conn.execute("UPDATE sent SET copy = ? WHERE message_id_hash = ?", (copy, key))


def _settle_row(conn: sqlite3.Connection, clock: Clock, key: str, status: str, *,
                settled: bool = True) -> None:  # fmt: skip
    with write_tx(conn):
        conn.execute("UPDATE sent SET status = ?, settled_at = ? WHERE message_id_hash = ?",
                     (status, to_ts(clock.now()) if settled else None, key))  # fmt: skip


@dataclass(frozen=True)
class Settled:
    copies: int  # sent copies decided this pass
    sent: int  # unknown sends found in the Sent folder
    failed: int  # unknown sends given up on


def settle(conn: sqlite3.Connection, clock: Clock, mail: MailSource, address_id: str) -> Settled:
    """One pass over this address's open sends, inside its check (which holds the lease)."""
    folder, _saves = _sent_folder(conn, address_id)
    copies = sent = failed = 0
    if folder is None:
        return Settled(0, 0, 0)
    for r in _rows(conn, address_id, "copy = 'check'"):
        copies += 1
        uids = mail.find_in(folder, r["message_id"])
        if len(uids) >= 2:  # ecf's copy and the provider's
            mail.delete_in(folder, max(uids))  # identical sends: either one may go
            _learn(conn, clock, address_id, saves_sent=True)
            copy = "provider"
        else:
            _learn(conn, clock, address_id, saves_sent=False)
            copy = "ecf" if uids else "none"
        with write_tx(conn):
            conn.execute("UPDATE sent SET copy = ? WHERE message_id_hash = ?",
                         (copy, r["message_id_hash"]))  # fmt: skip
    if _sent_folder(conn, address_id)[1] is not True:
        return Settled(copies, 0, 0)
    for r in _rows(conn, address_id, "status = 'unknown'"):
        if mail.find_in(folder, r["message_id"]):
            with write_tx(conn):
                conn.execute("UPDATE sent SET status = 'sent', settled_at = ?, copy = 'provider'"
                             " WHERE message_id_hash = ?",
                             (to_ts(clock.now()), r["message_id_hash"]))  # fmt: skip
            _settle_item(conn, clock, r, Status.EXECUTED)
            sent += 1
        elif r["searches"] + 1 >= MISSES_TO_FAIL:
            with write_tx(conn):
                conn.execute("UPDATE sent SET status = 'failed', settled_at = ?,"
                             " searches = searches + 1 WHERE message_id_hash = ?",
                             (to_ts(clock.now()), r["message_id_hash"]))  # fmt: skip
            _settle_item(conn, clock, r, Status.FAILED)
            failed += 1
        else:
            with write_tx(conn):
                conn.execute("UPDATE sent SET searches = searches + 1 WHERE message_id_hash = ?",
                             (r["message_id_hash"],))  # fmt: skip
    return Settled(copies, sent, failed)


def _settle_item(conn: sqlite3.Connection, clock: Clock, r: sqlite3.Row, to: Status) -> None:
    """The item that waited at `failed_unknown` for this send follows it (§6.2): `executed` when
    the Sent folder shows it went, `failed` (so `ecf item requeue` may send it again) when not."""
    if not r["stable_id"]:
        return
    item = conn.execute("SELECT status FROM items WHERE stable_id = ?",
                        (r["stable_id"],)).fetchone()  # fmt: skip
    if item is None or item["status"] != Status.FAILED_UNKNOWN:
        return
    items.transition(conn, clock, StableId(r["stable_id"]), to,
                     TransitionContext(reconciled=True), actor="service",
                     expected=Status.FAILED_UNKNOWN)  # fmt: skip


def _sent_folder(conn: sqlite3.Connection, address_id: str) -> tuple[str | None, bool | None]:
    """The Sent folder's name (None without one) and whether the provider saves sent mail."""
    info = probe.load(conn, address_id) or {}
    roles: dict[str, str] = info.get("roles") or {}
    saves = info.get("saves_sent")
    return roles.get(SENT_ROLE), None if saves is None else bool(saves)


def _rows(conn: sqlite3.Connection, address_id: str, where: str) -> Sequence[sqlite3.Row]:
    return conn.execute(
        f"SELECT * FROM sent WHERE address_id = ? AND kind != 'alert' AND {where}"  # noqa: S608
        " ORDER BY sent_at",
        (address_id,),
    ).fetchall()


def _learn(conn: sqlite3.Connection, clock: Clock, address_id: str, *, saves_sent: bool) -> None:
    """Record whether the provider saves sent mail, once (later passes change nothing)."""
    row = conn.execute("SELECT saves_sent FROM probe WHERE address_id = ?",
                       (address_id,)).fetchone()  # fmt: skip
    if row is None or row["saves_sent"] is not None:
        return
    with write_tx(conn):
        conn.execute("UPDATE probe SET saves_sent = ? WHERE address_id = ?",
                     (int(saves_sent), address_id))  # fmt: skip
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, 'probe.saves_sent_learned', 'service', 'ok', ?)",
            (to_ts(clock.now()), address_id, json.dumps({"saves_sent": saves_sent})),
        )


def settle_in_check(
    conn: sqlite3.Connection,
    clock: Clock,
    src: MailSource,
    address_id: str,
    _install: str,
    _max_scan_bytes: int,
    lost: Callable[[], bool],
) -> Settled:
    """`settle` with the check's open mailbox, once the check's own work is done."""
    if lost():
        return Settled(0, 0, 0)
    return settle(conn, clock, src, address_id)


checks.IN_LEASE.append(settle_in_check)
