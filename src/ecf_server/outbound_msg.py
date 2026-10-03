"""Messages ecf writes: template replies, internal forwards and drafts (SPEC §8.4; OD-317,
OD-318, OD-320, OD-321).

Everything taken from an email (its subject, sender, Message-IDs) is untrusted: control and format
characters (NUL, CR, LF, escape sequences, right-to-left overrides, zero-width marks) are removed
before any of it reaches a header, recipients are bare addresses (no display name, which a client
could show instead of the real address), and non-ASCII header text is RFC 2047-encoded. The output
is 7-bit with CRLF line ends, ready for SMTP.

- Replies and forwards carry `X-ECF-Install: <id>.<generation>` and `Auto-Submitted`
  (`auto-replied` on replies, `auto-generated` on forwards; OD-320). Drafts carry neither: you
  send a draft yourself.
- A forward attaches the original unmodified: as `message/rfc822` with 7bit encoding when it is
  7-bit clean (ASCII, CRLF line ends, lines of at most 998 octets), otherwise as
  `application/octet-stream` named `original.eml` in base64, which decodes to the exact bytes.
  The cover text names the address and item only, never model text (OD-321).
- Message-IDs are random and pre-generated on the sending address's domain, so `sent` can record
  one before the send and the Sent folder can be searched for it after (OD-322).
- `content_hash` is the inbound function's (message.parse), so ecf's own mail coming back in is
  recognized by the same hash (§8.4).
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime

from ecf.errors import InvalidInputError
from ecf_server.message import parse

SUBJECT_CHARS = 200
_ADDR = re.compile(r"^[^\s<>()\[\],;:\\\"@]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_MSGID = re.compile(r"<[^<>\s]{1,250}>")
_REFERENCES = 10  # Message-IDs kept in References
_POLICY = policy.SMTP.clone(max_line_length=78)


@dataclass(frozen=True)
class Built:
    raw: bytes
    message_id: str
    content_hash: str


def clean(text: str, limit: int = SUBJECT_CHARS) -> str:
    """Header text from an email: control and format characters removed, spaces collapsed."""
    kept = "".join(" " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in text)
    return " ".join(kept.split())[:limit]


def addr_spec(value: str) -> str:
    """A bare recipient address; anything with a display name, spaces or controls is refused."""
    v = value.strip()
    if not _ADDR.fullmatch(v):
        raise InvalidInputError("not a plain email address")
    local, _, domain = v.rpartition("@")
    return f"{local}@{domain.lower()}"


def message_id_hash(message_id: str) -> str:
    """The `sent` table's key for a Message-ID."""
    return hashlib.sha256(message_id.encode("utf-8")).hexdigest()


def new_message_id(domain: str) -> str:
    return f"<{secrets.token_hex(16)}.ecf@{domain.lower()}>"


def message_ids(*values: str | None) -> list[str]:
    """Well-formed Message-IDs from sender-controlled header values, in order, deduplicated."""
    out: list[str] = []
    for v in values:
        for m in _MSGID.findall(v or ""):
            if m not in out:
                out.append(m)
    return out


def prefixed(prefix: str, subject: str) -> str:
    s = clean(subject)
    return s if s.lower().startswith(prefix.lower()) else f"{prefix} {s}".strip()


def _headers(m: EmailMessage, *, from_addr: str, to_addr: str, subject: str, date: datetime,
             message_id: str) -> None:  # fmt: skip
    m["From"] = addr_spec(from_addr)
    m["To"] = addr_spec(to_addr)
    m["Subject"] = clean(subject)
    m["Date"] = format_datetime(date)
    m["Message-ID"] = message_id
    m["MIME-Version"] = "1.0"


def _threading(m: EmailMessage, in_reply_to: str | None, references: str | None) -> None:
    parent = message_ids(in_reply_to)
    if parent:
        m["In-Reply-To"] = parent[0]
        chain = [*message_ids(references), parent[0]]
        m["References"] = " ".join(list(dict.fromkeys(chain))[-_REFERENCES:])


def _built(m: EmailMessage, message_id: str) -> Built:
    raw = m.as_bytes(policy=_POLICY)
    return _check(raw, message_id)


def _check(raw: bytes, message_id: str) -> Built:
    if any(b > 0x7F for b in raw):
        raise InvalidInputError("the message isn't 7-bit")  # the builder's own guarantee
    return Built(raw, message_id, parse(raw).content_hash)


def build_reply(  # noqa: PLR0913 - keyword-only parts of one message
    *,
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    in_reply_to: str | None,
    references: str | None,
    install_header: str,
    date: datetime,
    message_id: str | None = None,
) -> Built:
    """A template reply to the sender (`Re:`), threaded under the original. `message_id`: the
    one already recorded for this grant (send.message_id_for), so every attempt uses the same."""
    mid = message_id or new_message_id(from_addr.rpartition("@")[2])
    m = EmailMessage(policy=_POLICY)
    _headers(m, from_addr=from_addr, to_addr=to_addr, subject=prefixed("Re:", subject),
             date=date, message_id=mid)  # fmt: skip
    _threading(m, in_reply_to, references)
    m["Auto-Submitted"] = "auto-replied"
    m["X-ECF-Install"] = install_header
    m.set_content(body)
    return _built(m, mid)


def build_draft(
    *,
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    in_reply_to: str | None,
    references: str | None,
    date: datetime,
    message_id: str | None = None,
) -> Built:
    """A draft reply saved to Drafts for you to edit and send; no ecf headers."""
    mid = message_id or new_message_id(from_addr.rpartition("@")[2])
    m = EmailMessage(policy=_POLICY)
    _headers(m, from_addr=from_addr, to_addr=to_addr, subject=prefixed("Re:", subject),
             date=date, message_id=mid)  # fmt: skip
    _threading(m, in_reply_to, references)
    m.set_content(body)
    return _built(m, mid)


def seven_bit_clean(raw: bytes) -> bool:
    """ASCII without NUL, CRLF line ends only, lines of at most 998 octets (RFC 5322 §2.1.1)."""
    if any(b > 0x7F or b == 0 for b in raw):
        return False
    if re.search(rb"\r(?!\n)|(?<!\r)\n", raw):
        return False
    return all(len(line) <= 998 for line in raw.split(b"\r\n"))


def build_forward(
    *,
    from_addr: str,
    to_addr: str,
    original: bytes,
    original_subject: str,
    cover: str,
    install_header: str,
    date: datetime,
    message_id: str | None = None,
) -> Built:
    """An internal forward: a short cover note and the original attached unmodified."""
    mid = message_id or new_message_id(from_addr.rpartition("@")[2])
    boundary = f"ecf-{secrets.token_hex(16)}"
    if boundary.encode() in original:
        raise InvalidInputError("the original contains the boundary; try again")
    head = EmailMessage(policy=_POLICY)
    _headers(head, from_addr=from_addr, to_addr=to_addr,
             subject=prefixed("Fwd:", original_subject), date=date, message_id=mid)  # fmt: skip
    head["Auto-Submitted"] = "auto-generated"
    head["X-ECF-Install"] = install_header
    head["Content-Type"] = f'multipart/mixed; boundary="{boundary}"'
    note = EmailMessage(policy=_POLICY)
    note.set_content(cover)
    del note["MIME-Version"]
    if seven_bit_clean(original):
        attached = (b"Content-Type: message/rfc822\r\nContent-Transfer-Encoding: 7bit\r\n"
                    b"Content-Disposition: attachment\r\n\r\n" + original)  # fmt: skip
    else:
        encoded = base64.encodebytes(original).replace(b"\n", b"\r\n")
        attached = (b"Content-Type: application/octet-stream; name=\"original.eml\"\r\n"
                    b"Content-Transfer-Encoding: base64\r\n"
                    b"Content-Disposition: attachment; filename=\"original.eml\"\r\n\r\n"
                    + encoded)  # fmt: skip
    attached += b"\r\n"  # the CRLF before a boundary belongs to it (RFC 2046 §5.1.1), not the part
    b = boundary.encode()
    top = b"".join(_POLICY.fold_binary(k, v) for k, v in head.items()) + b"\r\n"
    raw = (top + b"--" + b + b"\r\n" + note.as_bytes(policy=_POLICY)
           + b"\r\n--" + b + b"\r\n" + attached + b"--" + b + b"--\r\n")  # fmt: skip
    return _check(raw, mid)
