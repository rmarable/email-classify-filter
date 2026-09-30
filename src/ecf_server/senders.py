"""`ecf sender confirm|set-reply-to|set-verified|show` (SPEC §7.2, §8.5, §8.6; V1.2 step 10c).

Sender records are per address and keyed by the hash of the sender's address (the address itself
is never stored there; its domain is). A command may create the record for a sender ecf hasn't
seen pass DMARC yet.

- **confirm** sets the sender's category. A confirmed category makes the sender known (`sender
  seen before`), counts for the bank-detail trigger (§8.5 trigger 1: only a human-confirmed sender
  is known there) and adds the domain to the known vendors for lookalike checks. Because every
  confirmation counts for the bank-detail trigger, it always needs step-up (§9.6). Senders on the
  shared-platform list never count as known (OD-061); confirming one is recorded but says so.
- **set-reply-to** records the sender's expected Reply-To domain; a match isn't a Reply-To
  mismatch (OD-068). Step-up. `--clear` removes it without step-up (it only adds scrutiny).
- **set-verified** marks the sender "human-verified" for rule 1a: payment mail from it with
  `auth_result = none` is no longer labelled `unverified_sender` and flagged (OD-065). Fraud
  triggers stay on. Step-up. `--off` removes it without step-up.

None of these changes an item already checked: a trigger that fired stays fired (§9.2). Each change
is audited (`sender.confirmed`, `sender.reply_to_set`, `sender.verified_set`) with the sender's
hash and domain, never the address.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ecf.errors import InvalidInputError
from ecf.schema import load_schema_v1
from ecf_server import addresses, stepup
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.facts import SHARED_PLATFORMS, domain_of, in_domains, sender_hash


def _address(conn: sqlite3.Connection, ref: str | None) -> dict[str, Any]:
    if ref:
        return addresses.get_address(conn, ref)
    active = addresses.list_addresses(conn)
    if len(active) != 1:
        raise InvalidInputError("sender records are per address: add --address")
    return active[0]


def _sender(email: str) -> tuple[str, str]:
    """(normalized address, domain)."""
    local, domain = addresses.parse_email(email)
    return f"{local}@{domain}".lower(), domain


def _row(conn: sqlite3.Connection, aid: str, email: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM senders WHERE address_id = ? AND sender_hash = ?",
        (aid, sender_hash(email)),
    ).fetchone()
    return row


def _state(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {"known": False, "confirmed_category": None, "expected_reply_to_domain": None,
                "verified": False, "dmarc_pass_count": 0}  # fmt: skip
    return {
        "known": True,
        "confirmed_category": row["confirmed_category"],
        "expected_reply_to_domain": row["expected_reply_to_domain"],
        "verified": bool(row["verified_rule1a"]),
        "dmarc_pass_count": row["dmarc_pass_count"],
    }


def show(conn: sqlite3.Connection, sender: str, address: str | None) -> dict[str, Any]:
    a = _address(conn, address)
    email, domain = _sender(sender)
    return {"address_id": a["address_id"], "sender": email, "domain": domain,
            "shared_platform": in_domains(domain, SHARED_PLATFORMS)} | _state(
        _row(conn, a["address_id"], email))  # fmt: skip


# ---------------------------------------------------------------------------- step-up purposes


def _bound(conn: sqlite3.Connection, name: str, target: dict[str, Any], what: str) -> stepup.Bound:
    a = addresses.get_address(conn, str(target.get("address_id", "")))
    email, _ = _sender(str(target.get("sender", "")))
    state = _state(_row(conn, a["address_id"], email))
    value = target.get("value")
    return stepup.Bound(
        stepup.digest(name, a["address_id"], sender_hash(email), value, state),
        f"ecf: {what.format(value=value)} for {email} at {a['email']}",
    )


@stepup.purpose("sender_confirm")
def _describe_confirm(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    return _bound(conn, "sender_confirm", target, "confirm category {value}")


@stepup.purpose("sender_reply_to")
def _describe_reply_to(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    return _bound(conn, "sender_reply_to", target, "expect Reply-To domain {value}")


@stepup.purpose("sender_verified")
def _describe_verified(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    return _bound(conn, "sender_verified", target,
                  "mark as human-verified (no unverified-sender flag)")  # fmt: skip


# ---------------------------------------------------------------------------- changes


def _write(
    conn: sqlite3.Connection,
    clock: Clock,
    aid: str,
    email: str,
    column: str,
    value: Any,
    event: str,
    actor: str,
) -> None:
    now = to_ts(clock.now())
    extra = ", confirmed_at = excluded.confirmed_at" if column == "confirmed_category" else ""
    with write_tx(conn):
        conn.execute(
            f"INSERT INTO senders (address_id, sender_hash, domain, {column}, confirmed_at)"  # noqa: S608 - column is one of three fixed names
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT (address_id, sender_hash) DO UPDATE SET"
            f" {column} = excluded.{column}{extra}",
            (aid, sender_hash(email), domain_of(email), value,
             now if column == "confirmed_category" else None),
        )  # fmt: skip
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, ?, ?, 'ok', ?)",
            (now, aid, event, actor, json.dumps({"sender_hash": sender_hash(email),
                                                 "domain": domain_of(email), column: value})),
        )  # fmt: skip


def confirm(
    conn: sqlite3.Connection,
    clock: Clock,
    sender: str,
    category: str,
    *,
    address: str | None,
    nonce: str | None,
    actor: str = "os_user",
) -> dict[str, Any]:
    values = load_schema_v1().fields["category"].values or ()
    if category not in values:
        raise InvalidInputError(f"category is one of {', '.join(values)}")
    a = _address(conn, address)
    email, _ = _sender(sender)
    target = {"address_id": a["address_id"], "sender": email, "value": category}
    stepup.consume(conn, clock, "sender_confirm", target, nonce)
    _write(conn, clock, a["address_id"], email, "confirmed_category", category,
           "sender.confirmed", actor)  # fmt: skip
    return show(conn, email, a["address_id"])


def set_reply_to(
    conn: sqlite3.Connection,
    clock: Clock,
    sender: str,
    domain: str | None,
    *,
    address: str | None,
    nonce: str | None,
    actor: str = "os_user",
) -> dict[str, Any]:
    a = _address(conn, address)
    email, _ = _sender(sender)
    value = addresses.normalize_domain(domain) if domain is not None else None
    if value is not None:  # clearing only adds scrutiny: no step-up
        target = {"address_id": a["address_id"], "sender": email, "value": value}
        stepup.consume(conn, clock, "sender_reply_to", target, nonce)
    _write(conn, clock, a["address_id"], email, "expected_reply_to_domain", value,
           "sender.reply_to_set", actor)  # fmt: skip
    return show(conn, email, a["address_id"])


def set_verified(
    conn: sqlite3.Connection,
    clock: Clock,
    sender: str,
    on: bool,
    *,
    address: str | None,
    nonce: str | None,
    actor: str = "os_user",
) -> dict[str, Any]:
    a = _address(conn, address)
    email, _ = _sender(sender)
    if on:  # turning it off only adds scrutiny: no step-up
        target = {"address_id": a["address_id"], "sender": email, "value": True}
        stepup.consume(conn, clock, "sender_verified", target, nonce)
    _write(conn, clock, a["address_id"], email, "verified_rule1a", int(on),
           "sender.verified_set", actor)  # fmt: skip
    return show(conn, email, a["address_id"])
