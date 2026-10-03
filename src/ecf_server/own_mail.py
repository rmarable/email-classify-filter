"""Recognizing ecf's own mail when it comes back in (SPEC §8.4, §8.5 trigger 9, §13.6; OD-318).

`classify` sorts an incoming message carrying ecf's marks:
- `own`: this install sent it and it came back unchanged: its Message-ID is in `sent`, it passes
  DMARC from the address that sent it, and its content hash is the one recorded (§8.4). Such mail
  is skipped (no item). A message with this install's own `X-ECF-Install` value (same ID and
  generation), passing DMARC from one of its monitored addresses, counts as `own` too: only this
  install and its mailbox can produce it, and treating it as a fraud trigger would turn a
  forwarded copy into a loop.
- `second_install`: an `X-ECF-Install` header, passing DMARC from one of this install's monitored
  addresses, whose value isn't this install's ID and generation: another install, or another
  running copy of this one (a restore beside a computer that was never stopped), is sending from
  this mailbox. The email becomes an item as usual, the address is paused and Operator Input
  Needed is raised (§13.6).
- None: anything else. An `X-ECF-Install` header there is fraud trigger 9 (anyone can forge one).
"""

from __future__ import annotations

import sqlite3

from ecf_server import install_identity
from ecf_server.message import ParsedMessage
from ecf_server.outbound_msg import message_id_hash

OWN = "own"
SECOND = "second_install"


def classify(conn: sqlite3.Connection, parsed: ParsedMessage, auth_result: str) -> str | None:
    if auth_result != "pass":
        return None
    sender = (parsed.from_addr or "").lower()
    monitored = {str(r[0]).lower(): str(r[1]) for r in conn.execute(
        "SELECT email, address_id FROM addresses WHERE removed_at IS NULL")}  # fmt: skip
    if sender not in monitored:
        return None
    if parsed.message_id:
        row = conn.execute("SELECT address_id, content_hash FROM sent WHERE message_id_hash = ?",
                           (message_id_hash(parsed.message_id),)).fetchone()  # fmt: skip
        if (row is not None and row["address_id"] == monitored[sender]
                and row["content_hash"] == parsed.content_hash):  # fmt: skip
            return OWN
    headers = parsed.headers.get("x-ecf-install") or ()
    if not headers:
        return None
    mine = (install_identity.install_id(conn), install_identity.generation(conn))
    if all(install_identity.parse_header(h) == mine for h in headers):
        return OWN
    return SECOND
