"""Gmail's daily download budget (SPEC §14.3; V1.6 step 3b; OD-440).

Google documents an IMAP download limit for Workspace accounts, 2,500 MB a day (a reviewer's
source, not re-checked; R14); a limit for personal accounts, and how Google counts it, is
unverified. Going over it can lock IMAP for the account for up to a day, so in Gmail mode fetch
stops before a message would take the last 24 hours past `GMAIL_BYTES_PER_DAY` (decimal MB, the
cautious reading). The mail waits for a later check; nothing is skipped. What fetch downloads is
counted (whole messages, or the header and text parts of oversized ones); other reads (a
pre-check or an action looking at a message again) are small and aren't.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from ecf_server.clock import Clock
from ecf_server.db import write_tx

GMAIL_BYTES_PER_DAY = 2_500_000_000
WINDOW = timedelta(hours=24)
KEEP = timedelta(days=2)
_HOUR = "%Y-%m-%dT%H"


def used(conn: sqlite3.Connection, clock: Clock, address_id: str) -> int:
    """Bytes downloaded in the current hour and the 23 before it."""
    since = (clock.now() - WINDOW + timedelta(hours=1)).strftime(_HOUR)
    row = conn.execute("SELECT coalesce(sum(bytes), 0) FROM downloads WHERE address_id = ?"
                       " AND hour >= ?", (address_id, since)).fetchone()  # fmt: skip
    return int(row[0])


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


def left(conn: sqlite3.Connection, clock: Clock, address_id: str, *, gmail: bool) -> int | None:
    """Bytes fetch may still download now; None where there is no budget (not Gmail)."""
    if not gmail:
        return None
    return max(GMAIL_BYTES_PER_DAY - used(conn, clock, address_id), 0)
