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

`alert_echo` finds mail that answers or repeats one of this install's alert emails (§8.5 loop
suppression; OD-330, OD-337, OD-338; V1.5 step 7b), with no DMARC pass needed: the random
Message-ID it cites is known only to the alert's recipient.
- `copy`: the alert itself (its Message-ID is a recorded alert's, and any `X-ECF-Install` value is
  this install's), e.g. forwarded into a watched mailbox with DKIM broken; fraud trigger 9 doesn't
  fire on it (OD-338).
- `bounce`: a delivery report (`multipart/report; report-type=delivery-status`) whose
  `In-Reply-To`, `References` or returned headers cite a recorded alert.
- `auto_reply`: an automatic reply (`Auto-Submitted` other than `no`, or `X-Autoreply`) whose
  `In-Reply-To` or `References` cite one.
Such mail is classified as usual, then labelled `alert_echo` and left unless a fraud or regulatory
signal or an escalation says otherwise (policy.py), and it never raises an alert email itself.
"""

from __future__ import annotations

import re
import sqlite3

from ecf_server import install_identity
from ecf_server.message import ParsedMessage
from ecf_server.outbound_msg import message_id_hash, message_ids

OWN = "own"
SECOND = "second_install"
ECHO_COPY, ECHO_BOUNCE, ECHO_AUTO = "copy", "bounce", "auto_reply"
_RETURNED_ID = re.compile(rb"^Message-ID:[ \t]*(<[^<>\s]{1,250}>)", re.IGNORECASE | re.MULTILINE)
MAX_CITED = 50


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


def alert_echo(conn: sqlite3.Connection, parsed: ParsedMessage, raw: bytes) -> str | None:
    """`copy`, `bounce`, `auto_reply` or None (module docstring)."""
    if parsed.message_id and _is_alert(conn, [parsed.message_id]):
        mine = (install_identity.install_id(conn), install_identity.generation(conn))
        stamps = parsed.headers.get("x-ecf-install") or ()
        if all(install_identity.parse_header(h) == mine for h in stamps):
            return ECHO_COPY
    h = parsed.headers
    cited = message_ids(*(h.get("in-reply-to") or ()), *(h.get("references") or ()))
    ctype = " ".join(h.get("content-type") or ()).lower()
    if "multipart/report" in ctype and "delivery-status" in ctype:
        returned = [m.decode("ascii", "replace") for m in _RETURNED_ID.findall(raw)]
        own = parsed.message_id
        if _is_alert(conn, [*cited, *(m for m in returned if m != own)]):
            return ECHO_BOUNCE
        return None
    auto = [v.strip().lower() for v in h.get("auto-submitted") or ()]
    if (any(v and not v.startswith("no") for v in auto) or h.get("x-autoreply")) and _is_alert(
            conn, cited):  # fmt: skip
        return ECHO_AUTO
    return None


def _is_alert(conn: sqlite3.Connection, ids: list[str]) -> bool:
    hashes = [message_id_hash(m) for m in dict.fromkeys(ids)][:MAX_CITED]
    if not hashes:
        return False
    marks = ", ".join("?" * len(hashes))
    row = conn.execute(f"SELECT 1 FROM sent WHERE kind = 'alert' AND message_id_hash IN ({marks})"  # noqa: S608
                       " LIMIT 1", hashes).fetchone()  # fmt: skip
    return row is not None
