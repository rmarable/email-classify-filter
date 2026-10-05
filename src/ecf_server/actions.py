"""Mailbox actions the service may take on its own: label, flag and their undo (SPEC §8.3, §6.4).

Built in V1.1 and exercised only in tests against the fake and Dovecot; real addresses stay in
shadow until they go live (OD-189). Every execution:
- re-finds the message (same UIDVALIDITY and UID, and Message-ID when it has one) and verifies its
  content hash before touching it (§6.4), so a replaced or renumbered message is left alone;
- runs under a single-use grant bound to the item, its content hash and the action set, consumed
  by a conditional update (§6.4); pre-check actions change no item status (§5.4);
- is audited (`action.granted`, `action.executed`, `action.undone`).
Escalations are recorded here and posted to Slack by `escalations.py`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, cast

from ecf.errors import ConflictError, GrantInvalidError
from ecf.ids import new_grant_id
from ecf_server import probe
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.mail import MailSource
from ecf_server.message import PARTIAL_HASH_VERSION, parse, parse_partial

GRANT_TTL = timedelta(minutes=10)  # a pre-check grant is used at once
HEADER_LIMIT = 256 * 1024


@dataclass(frozen=True)
class Planned:
    name: str  # label | flag | escalate, and the mailbox actions (§8.3)
    target: str | None = None
    # a draft's or send's recipient and text (outbound_plan.resolve; V1.5, OD-317); in the grant's
    # action hash, and left out of the JSON when absent so earlier grants keep their hash
    payload: dict[str, Any] | None = field(default=None, compare=False, hash=False)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "target": self.target}
        if self.payload is not None:
            out["payload"] = self.payload
        return out

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Planned:
        raw: Any = d.get("payload")
        payload = cast("dict[str, Any]", raw) if isinstance(raw, dict) else None
        return cls(str(d["name"]), d.get("target"), payload)


def keyword(install: str, label: str) -> str:
    """`$ecf_<install>_<label>` (SPEC §6.4)."""
    return f"$ecf_{install}_{label}"


def action_hash(stable_id: str, content_hash: str, actions: list[Planned]) -> str:
    canonical = json.dumps(
        {"item": stable_id, "content": content_hash, "actions": [a.to_json() for a in actions]},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class MessageChangedError(ConflictError):
    """The message is gone, renumbered or no longer the content the item was made from."""


def verify(src: MailSource, item: sqlite3.Row, max_scan_bytes: int) -> int:
    """The message's UID, after checking it is still the item's message (§6.4)."""
    loc: dict[str, Any] = json.loads(item["locator"])
    uid = int(loc["uid"])
    if src.inbox().uidvalidity != loc["uidvalidity"]:
        raise MessageChangedError("mailbox was reset (UIDVALIDITY changed)")
    meta = src.meta([uid]).get(uid)
    if meta is None:
        raise MessageChangedError("message no longer in INBOX")
    if item["message_id"] and meta.message_id != item["message_id"]:
        raise MessageChangedError("a different message now has this UID")
    if item["hash_version"] == PARTIAL_HASH_VERSION:
        parts = src.structure(uid)
        header = src.fetch_part(uid, "HEADER", HEADER_LIMIT)
        if parts is None or header is None:
            raise MessageChangedError("message no longer in INBOX")
        found = parse_partial(header, parts, {}, size=meta.size).content_hash
    else:
        raw = src.fetch(uid)
        if raw is None:
            raise MessageChangedError("message no longer in INBOX")
        found = parse(raw, max_scan_bytes=max_scan_bytes).content_hash
    if found != item["content_hash"]:
        raise MessageChangedError("message content differs from the item's")
    return uid


def execute(
    conn: sqlite3.Connection,
    clock: Clock,
    src: MailSource,
    item: sqlite3.Row,
    actions: list[Planned],
    *,
    install: str,
    max_scan_bytes: int,
    principal: str = "service",
) -> list[str]:
    """Apply label and flag actions to the item's message; return what was done."""
    uid = verify(src, item, max_scan_bytes)
    grant = _grant(conn, clock, item, actions, principal)
    _claim(conn, clock, grant)  # single use: claimed before anything is done
    done: list[str] = []
    stored = probe.keywords_stored(conn, item["address_id"])
    try:
        for a in actions:
            if a.name == "label" and a.target and not stored:  # skipped, not a failure (OD-439)
                done.append(f"label {a.target} not stored by this provider")
            elif a.name == "label" and a.target:
                src.add_keyword(uid, keyword(install, a.target))
                done.append(f"label {a.target}")
            elif a.name == "flag":
                src.set_flagged(uid, True)
                done.append("flag")
            elif a.name == "escalate":
                done.append("escalate")
    except Exception as exc:
        with write_tx(conn):
            _audit(
                conn,
                clock,
                item,
                "action.failed",
                {"grant_id": grant, "done": done, "error": type(exc).__name__},
                "error",
            )
        raise
    with write_tx(conn):
        _audit(conn, clock, item, "action.executed", {"grant_id": grant, "done": done})
    return done


def undo(
    conn: sqlite3.Connection,
    clock: Clock,
    src: MailSource,
    item: sqlite3.Row,
    actions: list[Planned],
    *,
    install: str,
    max_scan_bytes: int,
) -> list[str]:
    """Reverse label and flag actions (§4: labels and flags are reversible)."""
    uid = verify(src, item, max_scan_bytes)
    undone: list[str] = []
    stored = probe.keywords_stored(conn, item["address_id"])
    for a in actions:
        if a.name == "label" and a.target and stored:  # never written where not stored (OD-439)
            src.remove_keyword(uid, keyword(install, a.target))
            undone.append(f"label {a.target}")
        elif a.name == "flag":
            src.set_flagged(uid, False)
            undone.append("flag")
    with write_tx(conn):
        _audit(conn, clock, item, "action.undone", {"actions": undone})
    return undone


def _grant(
    conn: sqlite3.Connection,
    clock: Clock,
    item: sqlite3.Row,
    actions: list[Planned],
    principal: str,
) -> str:
    grant_id = new_grant_id()
    now = clock.now()
    with write_tx(conn):
        conn.execute(
            "INSERT INTO grants (grant_id, stable_id, action_hash, content_hash, principal, status,"
            " expires_at) VALUES (?, ?, ?, ?, ?, 'approved', ?)",
            (
                grant_id,
                item["stable_id"],
                action_hash(item["stable_id"], item["content_hash"], actions),
                item["content_hash"],
                principal,
                to_ts(now + GRANT_TTL),
            ),
        )
        _audit(
            conn,
            clock,
            item,
            "action.granted",
            {"grant_id": grant_id, "actions": [a.to_json() for a in actions]},
        )
    return grant_id


def _claim(conn: sqlite3.Connection, clock: Clock, grant_id: str) -> None:
    """Consume the grant, once: a second claim, or an expired grant, is refused."""
    now = to_ts(clock.now())
    with write_tx(conn):
        used = conn.execute(
            "UPDATE grants SET status = 'consumed', consumed_at = ? WHERE grant_id = ?"
            " AND status = 'approved' AND expires_at > ?",
            (now, grant_id, now),
        ).rowcount
    if used != 1:
        raise GrantInvalidError(f"grant {grant_id[:8]} expired or already used")


def _audit(
    conn: sqlite3.Connection,
    clock: Clock,
    item: sqlite3.Row,
    event: str,
    data: dict[str, Any],
    outcome: str = "ok",
) -> None:
    conn.execute(
        "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
        " VALUES (?, ?, ?, ?, 'service', ?, ?)",
        (
            to_ts(clock.now()),
            item["address_id"],
            item["stable_id"],
            event,
            outcome,
            json.dumps(data),
        ),
    )
