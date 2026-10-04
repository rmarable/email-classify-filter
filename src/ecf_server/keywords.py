"""ecf's IMAP keywords on incoming mail (SPEC §6.3, §13.6; OD-318, OD-372; V1.5 step 10b).

ecf labels mail with `$ecf_<install name>_<label>` (actions.keyword). Each check reads the flags of
the messages it fetches (one request a page) and sorts ecf's keywords:

- **another install's**: that install acts on this mailbox too; the address is paused and
  `Operator Input Needed: possible second install` raised, as for its `X-ECF-Install` header;
- **this install's, within 7 days of a restore, on mail newer than the restored cursor**: a copy
  of this install may still be running elsewhere; the address is paused and `Operator Input
  Needed: ecf's labels on new mail after a restore` raised (OD-318, OD-372);
- **this install's otherwise** (ecf handled it before but has no record, e.g. a crash): adopted
  silently: the email gets an item as usual, with `ecf_keywords` in its facts, and no reply,
  draft or forward is made for it again (policy.send_refusal); labels already there stay.

Two installs with the same name on one mailbox can't be told apart by keywords; the
`X-ECF-Install` header (own_mail.py) still tells them apart on ecf's own sent mail.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

from ecf_server.clock import Clock, from_ts

PREFIX = "$ecf_"
RESTORE_AT_KEY = "restore.at"  # written by restore.py
RESTORE_CURSORS_KEY = "restore.cursors"  # {address_id: [uidvalidity, last_uid]} as restored
WINDOW = timedelta(days=7)  # OD-318, OD-372


def sort(flags: frozenset[str], install: str) -> tuple[list[str], list[str]]:
    """(this install's labels, other installs' names) among `flags`; case-insensitive."""
    me = install.lower()
    mine: set[str] = set()
    others: set[str] = set()
    for f in flags:
        low = f.lower()
        if not low.startswith(PREFIX):
            continue
        name, sep, label = low[len(PREFIX) :].partition("_")
        if not sep or not name:
            continue
        if name == me:
            mine.add(label)
        else:
            others.add(name)
    return sorted(mine), sorted(others)


def in_restore_window(conn: sqlite3.Connection, clock: Clock, address_id: str, uidvalidity: int,
                      uid: int) -> bool:  # fmt: skip
    """Within 7 days of a restore, on mail newer than the address's restored cursor (OD-372)."""
    at = conn.execute("SELECT value FROM settings WHERE key = ?", (RESTORE_AT_KEY,)).fetchone()
    if at is None or clock.now() - from_ts(json.loads(at[0])) > WINDOW:
        return False
    row = conn.execute("SELECT value FROM settings WHERE key = ?",
                       (RESTORE_CURSORS_KEY,)).fetchone()  # fmt: skip
    entry = json.loads(row[0]).get(address_id) if row else None
    return entry is not None and int(entry[0]) == uidvalidity and uid > int(entry[1])
