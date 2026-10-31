"""Each draft the actor writes, and what became of it (M8 plan §7; OD-583). Numbers and hashes
only; a draft's text stays on its item, and an email's in the excerpt.

A version is recorded when the actor proposes a draft, refused or not. `items.transition` then calls
`on_transition`, in its own transaction, so an outcome can't drift from the item's status. Only the
item's latest version moves. An expired draft that is offered again is waiting once more, so
`expired` means it finally went undecided. `closed` is for a version nobody decided: a newer one
replaced it while it waited, the address was in shadow, or the item was closed another way.

**Same text.** A draft is signed `[Your Name]`, and the person fills that and any other placeholder
in before sending, so a hash alone would call every send edited. `unchanged` compares against the
text, treating each `[...]` as room for what the person typed: `INLINE_ROOM` characters, or
`CLOSING_ROOM` for the last one when the draft ends on it, its sign-off (OD-583).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from ecf.plugin_digest import actor_prompt_digest
from ecf.status import Status
from ecf_server import draftcheck
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx

if TYPE_CHECKING:
    from ecf_server import policy

OUTCOMES = frozenset({
    "refused", "rejected", "deleted", "expired", "saved", "undone", "sent_unchanged", "sent_edited",
    "deleted_unsent", "still_waiting", "closed", "sent_unread",
})  # fmt: skip
# M9's "decided" (M8 plan, M9 entry evidence): refused, expired and undone don't count
DECIDED = frozenset({"saved", "sent_unchanged", "sent_edited", "sent_unread", "deleted_unsent",
                     "still_waiting", "rejected", "deleted"})  # fmt: skip
WATCH_DAYS = 14
INLINE_ROOM, CLOSING_ROOM = 120, 300
_PLACEHOLDER = re.compile(r"\[[^\[\]\n]{1,80}\]")
_ATTRIBUTION = re.compile(r"^on\b.{0,200}\bwrote:\s*$", re.IGNORECASE)
_ORIGINAL = re.compile(r"^-{2,}\s*(original|forwarded) message\s*-{2,}\s*$", re.IGNORECASE)
_HEADER = re.compile(r"^(from|de):\s", re.IGNORECASE)
_HEADER_NEXT = re.compile(r"^(sent|date|to|subject):\s", re.IGNORECASE)
_CLOSES = {
    Status.OBSERVED, Status.CANCELLED, Status.RESOLVED_MANUAL, Status.RESOLVED_BY_MAILBOX,
}  # fmt: skip
_SAVED_STILL = ("saved", "deleted_unsent", "still_waiting")
_OPEN = "(outcome IS NULL OR outcome = 'expired')"


def normalise(text: str) -> str:
    """The reply itself, whitespace folded: no quoted part, attribution line or signature, so a
    client's re-encoding or quoting isn't an edit (M8 plan §7)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list[str] = []
    for i, line in enumerate(lines):
        two = f"{line.strip()} {lines[i + 1].strip()}" if i + 1 < len(lines) else line.strip()
        if (line.rstrip(" ") == "--" or _ORIGINAL.match(line.strip())
                or _ATTRIBUTION.match(line.strip()) or _ATTRIBUTION.match(two)
                or (_HEADER.match(line) and i + 1 < len(lines)
                    and _HEADER_NEXT.match(lines[i + 1]))):  # fmt: skip
            break
        if not line.lstrip().startswith(">"):
            kept.append(line)
    return " ".join(" ".join(kept).split())


def text_hash(text: str) -> str:
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


def unchanged(draft: str, sent: str) -> bool:
    """`sent` is `draft` with at most its placeholders filled in (the module notes). Literal text
    is found left to right, never by a pattern built from model text."""
    d, s = normalise(draft), normalise(sent)
    parts = [p.strip() for p in _PLACEHOLDER.split(d)]
    if len(parts) == 1:
        return d == s
    if not s.startswith(parts[0]):
        return False
    pos = len(parts[0])
    for literal in parts[1:-1]:
        at = s.find(literal, pos, pos + INLINE_ROOM + len(literal))
        if at < 0:
            return False
        pos = at + len(literal)
    tail = parts[-1]
    if not tail:  # the draft ends on a placeholder: its sign-off
        return len(s) - pos <= CLOSING_ROOM
    start = len(s) - len(tail)
    return s.endswith(tail) and pos <= start <= pos + INLINE_ROOM


def _audit(conn: sqlite3.Connection, now: str, address_id: str, sid: str, event: str,
           data: dict[str, Any]) -> None:  # fmt: skip
    conn.execute("INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
                 " VALUES (?, ?, ?, ?, 'service', 'ok', ?)",
                 (now, address_id, sid, event, json.dumps(data, sort_keys=True)))  # fmt: skip


def record(conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, ctx: policy.Context,
           text: str, *, model: str | None, refused: tuple[str, ...]) -> int:  # fmt: skip
    """A draft the actor proposed: its version. The same text proposed again while it waits (an
    answered question, a held plan released) is the same version."""
    sid, now = item["stable_id"], to_ts(clock.now())
    digest = text_hash(text)
    excerpt = conn.execute("SELECT actor_text FROM excerpts WHERE stable_id = ?",
                           (sid,)).fetchone()  # fmt: skip
    email = str(excerpt["actor_text"]) if excerpt and excerpt["actor_text"] is not None else None
    copied = set(draftcheck.links(text)) & set(draftcheck.links(email or ""))
    cls = ctx.classification
    seen = ctx.facts.get("sender_seen_before")
    with write_tx(conn):
        last = conn.execute("SELECT version, outcome, normalised_hash FROM draft_versions"
                            " WHERE stable_id = ? ORDER BY version DESC LIMIT 1",
                            (sid,)).fetchone()  # fmt: skip
        again = "refused" if refused else None
        same = last and last["normalised_hash"] == digest
        if same and (last["outcome"] == again or (not refused and last["outcome"] == "expired")):
            return int(last["version"])
        conn.execute("UPDATE draft_versions SET outcome = 'closed', outcome_at = ?"
                     " WHERE stable_id = ? AND outcome IS NULL", (now, sid))  # fmt: skip
        version = (int(last["version"]) if last else 0) + 1
        conn.execute(
            "INSERT INTO draft_versions (stable_id, version, address_id, category, sender_type,"
            " sender_seen_before, drafter_model, actor_prompt, checker, created, email_chars,"
            " draft_chars, links_copied, refuse_codes, outcome, outcome_at, normalised_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, version, item["address_id"], cls.get("category"), cls.get("sender_type"),
             None if seen is None else int(bool(seen)), model, actor_prompt_digest(),
             draftcheck.digest(), now, None if email is None else len(email), len(text),
             len(copied), json.dumps(list(refused)), again, now if refused else None, digest),
        )  # fmt: skip
        _audit(conn, now, item["address_id"], sid, "draft.refused" if refused else "draft.created",
               {"version": version, "chars": len(text), "checker": draftcheck.digest()[:16]}
               | ({"codes": list(refused)} if refused else {}))  # fmt: skip
    return version


def close_waiting(conn: sqlite3.Connection, clock: Clock, sid: str) -> None:
    """The actor decided again and wrote no draft this time."""
    with write_tx(conn):
        conn.execute("UPDATE draft_versions SET outcome = 'closed', outcome_at = ?"
                     " WHERE stable_id = ? AND outcome IS NULL",
                     (to_ts(clock.now()), sid))  # fmt: skip


def saved(conn: sqlite3.Connection, clock: Clock, sid: str, message_id_hash: str,
          content_hash: str) -> None:  # fmt: skip
    """The draft is in Drafts: from here `draft_data.track_in_check` watches it."""
    now = clock.now()
    with write_tx(conn):
        conn.execute(
            "UPDATE draft_versions SET outcome = 'saved', outcome_at = ?, saved_at = ?,"
            " saved_message_id_hash = ?, content_hash = ?, watch_until = ?, checked_at = NULL,"
            " missing_checks = 0 WHERE stable_id = ? AND version = (SELECT max(version) FROM"
            " draft_versions WHERE stable_id = ?)",
            (to_ts(now), to_ts(now), message_id_hash, content_hash,
             to_ts(now + timedelta(days=WATCH_DAYS)), sid, sid),
        )  # fmt: skip


def on_transition(conn: sqlite3.Connection, sid: str, to: Status, now: str,
                  frm: Status | None = None) -> None:  # fmt: skip
    """Called inside `items.transition`'s transaction; a no-op for an item with no draft."""
    latest = "version = (SELECT max(version) FROM draft_versions WHERE stable_id = ?)"
    if to in (Status.APPROVED, Status.REJECTED):
        _decided(conn, sid, now)
    if to is Status.AWAITING_APPROVAL:
        if frm is not Status.EXPIRED:  # only the same draft offered again reopens it
            return
        sql, args = "outcome = NULL, outcome_at = NULL", "outcome = 'expired'"
    elif to is Status.EXPIRED:
        sql, args = "outcome = 'expired', outcome_at = ?", "outcome IS NULL"
    elif to is Status.REJECTED:
        sql, args = "outcome = 'rejected', outcome_at = ?", _OPEN
    elif to is Status.UNDONE:
        sql, args = "outcome = 'undone', outcome_at = ?", f"outcome IN {_SAVED_STILL!r}"
    elif to in _CLOSES:
        sql, args = "outcome = 'closed', outcome_at = ?", _OPEN
    else:
        return
    values = (now, sid, sid) if "?" in sql else (sid, sid)
    conn.execute(f"UPDATE draft_versions SET {sql} WHERE stable_id = ? AND {latest} AND {args}",  # noqa: S608 - fixed text above
                 values)  # fmt: skip


def _decided(conn: sqlite3.Connection, sid: str, now: str) -> None:
    """The first decision on the latest version, in seconds from when it was written."""
    row = conn.execute("SELECT version, created FROM draft_versions WHERE stable_id = ? AND"
                       " decided_after_s IS NULL AND (outcome IS NULL OR outcome = 'expired')"
                       " ORDER BY version DESC LIMIT 1", (sid,)).fetchone()  # fmt: skip
    if row is not None:
        after = (from_ts(now) - from_ts(row["created"])).total_seconds()
        conn.execute("UPDATE draft_versions SET decided_after_s = ? WHERE stable_id = ? AND"
                     " version = ?", (after, sid, row["version"]))  # fmt: skip
