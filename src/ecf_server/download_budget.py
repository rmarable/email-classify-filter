"""Gmail's daily download budget (SPEC §14.3; V1.6 step 3b; OD-440).

Google documents an IMAP download limit for Workspace accounts, 2,500 MB a day (a reviewer's
source, not re-checked; R14); a limit for personal accounts, and how Google counts it, is
unverified. Going over it can lock IMAP for the account for up to a day, so in Gmail mode fetch
stops before a message would take the last 24 hours past `GMAIL_BYTES_PER_DAY` (decimal MB, the
cautious reading). The mail waits for a later check; nothing is skipped. What fetch downloads is
counted (whole messages, or the header and text parts of oversized ones); other reads (a
pre-check or an action looking at a message again) are small and aren't.

A corpus fetch (SPEC §16.7, OD-467) counts against the same budget. With `--address` it records
in `downloads`; from a one-off mailbox it records in `corpus_downloads`, keyed by the folded email,
and an address's budget includes those rows too, so the mailbox's live fetch sees them.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from ecf_server import internal
from ecf_server.clock import Clock
from ecf_server.db import write_tx

GMAIL_BYTES_PER_DAY = 2_500_000_000
WINDOW = timedelta(hours=24)
KEEP = timedelta(days=2)
_HOUR = "%Y-%m-%dT%H"


def _since(clock: Clock) -> str:
    return (clock.now() - WINDOW + timedelta(hours=1)).strftime(_HOUR)


def used(conn: sqlite3.Connection, clock: Clock, address_id: str) -> int:
    """Bytes downloaded in the current hour and the 23 before it: by fetch and `--address` corpus
    fetches, plus one-off corpus fetches of the same mailbox."""
    email = conn.execute("SELECT email FROM addresses WHERE address_id = ?",
                         (address_id,)).fetchone()  # fmt: skip
    corpus = _corpus_used(conn, clock, internal.fold(email[0])) if email else 0
    return _fetched(conn, clock, address_id) + corpus


def _fetched(conn: sqlite3.Connection, clock: Clock, address_id: str) -> int:
    row = conn.execute("SELECT coalesce(sum(bytes), 0) FROM downloads WHERE address_id = ?"
                       " AND hour >= ?", (address_id, _since(clock))).fetchone()  # fmt: skip
    return int(row[0])


def _corpus_used(conn: sqlite3.Connection, clock: Clock, email_norm: str) -> int:
    row = conn.execute("SELECT coalesce(sum(bytes), 0) FROM corpus_downloads WHERE email_norm = ?"
                       " AND hour >= ?", (email_norm, _since(clock))).fetchone()  # fmt: skip
    return int(row[0])


def used_email(conn: sqlite3.Connection, clock: Clock, email: str) -> int:
    """Bytes downloaded from a mailbox named by email: one-off corpus fetches, plus everything
    recorded for a watched address that folds to the same mailbox."""
    norm = internal.fold(email)
    ids = [r[0] for r in conn.execute("SELECT address_id, email FROM addresses")
           if internal.fold(r[1]) == norm]  # fmt: skip
    return _corpus_used(conn, clock, norm) + sum(_fetched(conn, clock, i) for i in ids)


def record(conn: sqlite3.Connection, clock: Clock, address_id: str, nbytes: int) -> None:
    if nbytes <= 0:
        return
    now = clock.now()
    with write_tx(conn):
        conn.execute("INSERT INTO downloads (address_id, hour, bytes) VALUES (?, ?, ?)"
                     " ON CONFLICT (address_id, hour) DO UPDATE SET bytes = bytes + excluded.bytes",
                     (address_id, now.strftime(_HOUR), nbytes))  # fmt: skip
        conn.execute("DELETE FROM downloads WHERE address_id = ? AND hour < ?",
                     (address_id, (now - KEEP).strftime(_HOUR)))  # fmt: skip


def record_email(conn: sqlite3.Connection, clock: Clock, email: str, nbytes: int) -> None:
    """A one-off corpus fetch's bytes (OD-467)."""
    if nbytes <= 0:
        return
    now, norm = clock.now(), internal.fold(email)
    with write_tx(conn):
        conn.execute("INSERT INTO corpus_downloads (email_norm, hour, bytes) VALUES (?, ?, ?)"
                     " ON CONFLICT (email_norm, hour) DO UPDATE SET bytes = bytes + excluded.bytes",
                     (norm, now.strftime(_HOUR), nbytes))  # fmt: skip
        conn.execute("DELETE FROM corpus_downloads WHERE hour < ?",
                     ((now - KEEP).strftime(_HOUR),))  # fmt: skip


def left_email(conn: sqlite3.Connection, clock: Clock, email: str, *, gmail: bool) -> int | None:
    """Bytes a one-off corpus fetch may still download now; None where there is no budget."""
    if not gmail:
        return None
    return max(GMAIL_BYTES_PER_DAY - used_email(conn, clock, email), 0)


def left(conn: sqlite3.Connection, clock: Clock, address_id: str, *, gmail: bool) -> int | None:
    """Bytes fetch may still download now; None where there is no budget (not Gmail)."""
    if not gmail:
        return None
    return max(GMAIL_BYTES_PER_DAY - used(conn, clock, address_id), 0)
