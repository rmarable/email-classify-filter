"""What became of a saved draft, and how you rated it (M8 plan §7, P12; OD-583).

**Tracking, bounded.** Inside an address's check, after its approved actions and Undos, at most
`PER_CHECK` saved drafts, each at most once an hour, for `draft_versions.WATCH_DAYS` days. The pass
stops starting drafts after `BUDGET_S`; a search already sent waits on the mailbox like any other.
Each draft costs up to four searches: Sent and Drafts, each by the draft's exact Message-ID, then
for a reply to the same email. Gmail in a browser gives a draft a new Message-ID when it is sent or
edited, but keeps its In-Reply-To (real test, 2026-11-01). ecf reads only a message found in Sent,
at most `REPLY_READS` of them, once, in memory and only the first `READ_BYTES` of each (an
attachment isn't downloaded), and keeps only the outcome. It never reads a draft in Drafts.

A reply counts only if it arrived after the draft was saved (`SKEW` allowed), and only for the
item's newest saved draft, so one send isn't counted twice. The email's Message-ID is the sender's
to choose, so an odd one (`searchable`) is never put in a search.

- found in Sent: `sent_unchanged` if a copy is the draft (`draft_versions.unchanged`), else
  `sent_edited`, so a reply written from scratch reads as an edit; `sent_unread` when the first
  `READ_BYTES` hold no text, as when an attachment or inline image comes first. A reply ecf sent
  itself (`sent`) isn't one;
- found in Drafts: still `saved`;
- in neither, `MISSES` checks running: `deleted_unsent`, watched to the end, since a send can reach
  Sent late. A mailbox with no folder marked `\\Sent` or `\\Drafts` can't tell, so the draft stays
  `saved`;
- the watch ends on a draft still `saved`: `still_waiting`.

The Message-ID to search for is on the item (`proposal.done`); this table keeps only its hash.
Retention shorter than the watch deletes the row and ends its tracking.

**Ratings.** `rate` keeps one rating per version, and a later one replaces it. A "promised
something" 👎 alerts once per version. The card's buttons arrive in M8 Step 10.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from ecf.errors import ConflictError, InvalidInputError, MailUnavailableError
from ecf_server import alerts, checks, digests, draft_versions, mailbox_actions
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.mail import MailSource
from ecf_server.message import parse
from ecf_server.notify import Notifier
from ecf_server.outbound_msg import message_id_hash

PER_CHECK = 10
REPLY_READS = 3
SKEW = timedelta(minutes=5)  # this computer's clock against the mail server's
_SAFE_ID = re.compile(r"<[A-Za-z0-9.!#$&'+/=?^_`|~-]{1,400}@[A-Za-z0-9.!#$&'+/=?^_`|~-]{1,200}>")
EVERY = timedelta(hours=1)
BUDGET_S = 2.0
MISSES = 2
READ_BYTES = 256 * 1024
DRAFTS, SENT = "\\Drafts", "\\Sent"
PROMISED = "promised"
REASONS = frozenset({
    PROMISED, "unsupported", "followed_email", "shared_details", "unanswered", "generic", "tone",
    "length", "writing", "other",
})  # fmt: skip


def body_text(raw: bytes, max_scan_bytes: int) -> str:
    """A message's own text, plain first, as the reader sees it."""
    texts = [t for t in parse(raw, max_scan_bytes=max_scan_bytes).texts
             if not t.attachment and not t.forwarded]  # fmt: skip
    plain = next((t for t in texts if t.content_type == "text/plain"), None)
    if plain is not None:
        return plain.full
    html = next(iter(texts), None)
    if html is None:
        return ""
    return html.unquoted if html.unquoted is not None else html.visible


def _saved_record(doc: dict[str, Any], digest: str) -> dict[str, Any] | None:
    done: list[dict[str, Any]] = doc.get("done") or []
    for r in done:
        if (r.get("name") == "draft_reply" and r.get("message_id")
                and message_id_hash(str(r["message_id"])) == digest):  # fmt: skip
            return r
    return None


def _approved_text(doc: dict[str, Any], normalised: str) -> str | None:
    """The approved draft on the item, if it is this version's."""
    actions: list[dict[str, Any]] = doc.get("actions") or []
    for a in actions:
        payload: dict[str, Any] = a.get("payload") or {}
        text = payload.get("text")
        if (a.get("name") == "draft_reply" and isinstance(text, str)
                and draft_versions.text_hash(text) == normalised):  # fmt: skip
            return text
    return None


def _is_same(text: str | None, normalised: str, seen: str) -> bool:
    if text is not None:
        return draft_versions.unchanged(text, seen)
    return draft_versions.text_hash(seen) == normalised  # the item no longer has this version


def track_in_check(conn: sqlite3.Connection, clock: Clock, src: MailSource, address_id: str,
                   _install: str, max_scan_bytes: int,
                   lost: Callable[[], bool]) -> int:  # fmt: skip
    """One bounded pass; returns the drafts looked at. A mailbox error ends the pass, and the check
    goes on: this is a measurement, never a reason to fail a check."""
    now = to_ts(clock.now())
    for r in conn.execute("SELECT * FROM draft_versions WHERE address_id = ? AND outcome = 'saved'"
                          " AND watch_until < ?", (address_id, now)).fetchall():  # fmt: skip
        _update(conn, clock, r, {"outcome": "still_waiting"})
    rows = conn.execute(
        "SELECT * FROM draft_versions WHERE address_id = ? AND outcome IN ('saved',"
        " 'deleted_unsent') AND watch_until >= ? AND (checked_at IS NULL OR checked_at <= ?)"
        " ORDER BY checked_at LIMIT ?",
        (address_id, now, to_ts(clock.now() - EVERY), PER_CHECK),
    ).fetchall()  # fmt: skip
    if not rows or lost():
        return 0
    started, looked = clock.monotonic(), 0
    try:
        folders = {f.name: f.roles for f in src.folders()}
        drafts = next((n for n, roles in folders.items() if DRAFTS in roles), None)
        sent = next((n for n, roles in folders.items() if SENT in roles), None)
        for r in rows:
            if lost() or clock.monotonic() - started >= BUDGET_S:
                break
            _track_one(conn, clock, src, r, drafts, sent, max_scan_bytes)
            looked += 1
    except MailUnavailableError as exc:
        log.warning("drafts.tracking_stopped", error_type=type(exc).__name__)
    return looked


def _track_one(conn: sqlite3.Connection, clock: Clock, src: MailSource, r: sqlite3.Row,
               drafts: str | None, sent: str | None, max_scan_bytes: int) -> None:  # fmt: skip
    item = conn.execute("SELECT proposal, message_id FROM items WHERE stable_id = ?",
                        (r["stable_id"],)).fetchone()  # fmt: skip
    doc: dict[str, Any] = json.loads(item["proposal"] or "{}") if item else {}
    record = _saved_record(doc, str(r["saved_message_id_hash"]))
    if record is None:
        _update(conn, clock, r, {})
        return
    mid, text = str(record["message_id"]), _approved_text(doc, r["normalised_hash"])
    email = _reply_key(conn, r, item)
    since = from_ts(r["saved_at"]) - SKEW
    found = (src.find_in(sent, mid) or (src.find_replies_in(sent, email, since) if email else [])
             ) if sent else []  # fmt: skip
    texts = _sent_texts(conn, src, str(sent), found, max_scan_bytes)
    if texts is None:  # gone between the search and the read: look again next time
        _update(conn, clock, r, {})
        return
    if texts:
        after = (clock.now() - from_ts(r["saved_at"])).total_seconds()
        change: dict[str, Any] = {"outcome": "sent_unread", "edited": None}
        if any(t.strip() for t in texts):
            same = any(_is_same(text, r["normalised_hash"], t) for t in texts if t.strip())
            change = {"outcome": "sent_unchanged" if same else "sent_edited",
                      "edited": int(not same)}  # fmt: skip
        _update(conn, clock, r, change | {"sent_after_s": after, "missing_checks": 0})
        return
    if drafts is None or sent is None:  # can't tell a deleted draft from a sent one
        _update(conn, clock, r, {})
        return
    if src.find_in(drafts, mid) or (email and src.find_replies_in(drafts, email, since)):
        back = {"outcome": "saved"} if r["outcome"] == "deleted_unsent" else {}
        _update(conn, clock, r, {"missing_checks": 0, **back})
        return
    misses = int(r["missing_checks"]) + 1
    gone = misses >= MISSES and r["outcome"] == "saved"
    _update(conn, clock, r, {"missing_checks": misses,
                             **({"outcome": "deleted_unsent"} if gone else {})})  # fmt: skip


def searchable(message_id: str | None) -> bool:
    """Only characters safe in an IMAP SEARCH argument: the rest of an email's Message-ID could get
    the search refused, ending every pass for that address, or worse."""
    return bool(message_id and _SAFE_ID.fullmatch(message_id))


def _reply_key(conn: sqlite3.Connection, r: sqlite3.Row, item: sqlite3.Row | None) -> str | None:
    """The email's Message-ID, to find a reply by, for the item's newest saved draft only."""
    newest = conn.execute("SELECT max(version) FROM draft_versions WHERE stable_id = ?"
                          " AND saved_at IS NOT NULL AND outcome IS NOT 'undone'",
                          (r["stable_id"],)).fetchone()[0]  # fmt: skip
    email: str | None = item["message_id"] if item else None
    return email if r["version"] == newest and searchable(email) else None


def _sent_texts(conn: sqlite3.Connection, src: MailSource, sent: str, uids: list[int],
                max_scan_bytes: int) -> list[str] | None:  # fmt: skip
    """The text of each copy, leaving out replies ecf itself sent; None if one has gone."""
    texts: list[str] = []
    for uid in uids[:REPLY_READS]:
        raw = src.fetch_start_in(sent, uid, READ_BYTES)
        if raw is None:
            return None
        own = parse(raw, max_scan_bytes=max_scan_bytes).message_id
        if own and conn.execute("SELECT 1 FROM sent WHERE message_id_hash = ?",
                                (message_id_hash(own),)).fetchone():  # fmt: skip
            continue
        texts.append(body_text(raw, max_scan_bytes))
    return texts


def _update(conn: sqlite3.Connection, clock: Clock, r: sqlite3.Row, change: dict[str, Any]) -> None:
    now = to_ts(clock.now())
    fields = {"checked_at": now, **change}
    moved = change.get("outcome")
    if moved:
        fields["outcome_at"] = now
    sets = ", ".join(f"{k} = ?" for k in fields)
    with write_tx(conn):
        conn.execute(f"UPDATE draft_versions SET {sets} WHERE stable_id = ? AND version = ?",  # noqa: S608 - names above
                     (*fields.values(), r["stable_id"], r["version"]))  # fmt: skip
        if moved:
            conn.execute(
                "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
                " VALUES (?, ?, ?, 'draft.outcome', 'service', 'ok', ?)",
                (now, r["address_id"], r["stable_id"],
                 json.dumps({"version": r["version"], "outcome": moved})))  # fmt: skip


def rate(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, sid: str, rating: str, *,
         reason: str | None = None, note_chars: int = 0,
         actor: str = "os_user") -> None:  # fmt: skip
    """Rate the item's latest draft, once it is decided. The note's length is kept, never its
    text."""
    if rating not in ("up", "down"):
        raise InvalidInputError("rating: up or down")
    if reason is not None and (rating != "down" or reason not in REASONS):
        raise InvalidInputError("reason: one of the fixed reasons, on a 👎 only")
    now = to_ts(clock.now())
    with write_tx(conn):
        r = conn.execute("SELECT * FROM draft_versions WHERE stable_id = ? ORDER BY version DESC"
                         " LIMIT 1", (sid,)).fetchone()  # fmt: skip
        if r is None or r["outcome"] not in draft_versions.DECIDED:
            raise ConflictError("no decided draft to rate on this item")
        alert = reason == PROMISED and not r["alerted"]
        conn.execute("UPDATE draft_versions SET rating = ?, reason = ?, note_chars = ?,"
                     " alerted = alerted OR ? WHERE stable_id = ? AND version = ?",
                     (rating, reason, note_chars, alert, sid, r["version"]))  # fmt: skip
        conn.execute("INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
                     " VALUES (?, ?, ?, 'draft.rated', ?, 'ok', ?)",
                     (now, r["address_id"], sid, actor,
                      json.dumps({"version": r["version"], "rating": rating, "reason": reason,
                                  "note_chars": note_chars})))  # fmt: skip
    if alert:
        alerts.event(conn, clock, notifier, "draft_promised",
                     f"You rated a draft as promising something: item {sid[:8]}. Check what the"
                     f" draft says before it goes out: ecf item show {sid[:8]}")  # fmt: skip


# they register in IN_LEASE on import, so importing them first runs tracking after them
_RUN_FIRST = (mailbox_actions.run_in_check, digests)
checks.IN_LEASE.append(track_in_check)
