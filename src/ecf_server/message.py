"""Reading one message: identity, text, attachments, excerpts (SPEC §5.1 step 4, §6.3, §7.2).

Pure functions over the raw bytes; nothing is written to disk (§12.4).

**content_hash, version 1** (SPEC §6.3): SHA-256 over every leaf MIME part in document order,
each framed as a kind byte (`t` text, `a` anything else) plus an 8-byte big-endian length plus the
data, after the prefix `ecf-content-v1\\0`. Text parts (text/plain and text/html not marked as
attachments) are decoded to Unicode, line endings turned into LF and trailing whitespace removed
from each line and from the end, then UTF-8 encoded; other parts contribute their decoded
(e.g. base64-decoded) bytes. So re-encoding a message (charset, transfer encoding, CRLF) keeps its
hash, while a swapped attachment changes it. Header bytes and the message size are not included.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from email import message_from_bytes, policy
from email.message import EmailMessage, Message
from email.utils import getaddresses

from ecf_server.htmltext import html_to_text, strip_data_uris, tidy

HASH_VERSION = 1
CLASSIFIER_CHARS = 1500  # SPEC §5.1 step 4
ACTOR_CHARS = 4000
NAME_CHARS = 100  # attachment names are capped (§7.2)
DEFAULT_SCAN_BYTES = 10 * 1024 * 1024  # max_scan_bytes_per_part default (OD-027)
KEPT_HEADERS = (
    "from", "sender", "reply-to", "to", "cc", "subject", "date", "message-id", "in-reply-to",
    "references", "list-id", "list-unsubscribe", "precedence", "auto-submitted", "x-autoreply",
    "return-path", "x-ecf-install", "content-type", "mime-version", "content-transfer-encoding",
)  # fmt: skip
_MSGID = re.compile(r"<[^<>\s]+>")


@dataclass(frozen=True)
class Attachment:
    name: str
    content_type: str
    size: int
    inline: bool


@dataclass(frozen=True)
class TextPart:
    content_type: str  # text/plain or text/html
    full: str
    visible: str
    truncated: bool  # longer than the scan limit: only the start was scanned


@dataclass(frozen=True)
class ParsedMessage:
    message_id: str | None
    from_count: int
    from_addr: str | None  # lowercased
    from_name: str
    reply_to: tuple[str, ...]
    to: tuple[str, ...]
    cc: tuple[str, ...]
    subject: str
    headers: dict[str, tuple[str, ...]]
    texts: tuple[TextPart, ...]
    attachments: tuple[Attachment, ...]
    content_hash: str
    defects: int
    hash_version: int = HASH_VERSION
    size: int = 0

    @property
    def any_truncated(self) -> bool:
        return any(t.truncated for t in self.texts)

    def full_text(self) -> str:
        return "\n\n".join(t.full for t in self.texts if t.full)

    def visible_text(self) -> str:
        return "\n\n".join(t.visible for t in self.texts if t.visible)

    def excerpt(self, limit: int) -> str:
        """Plain text preferred, else visible HTML text; cut at a word boundary."""
        plain = [t.visible for t in self.texts if t.content_type == "text/plain" and t.visible]
        html = [t.visible for t in self.texts if t.content_type == "text/html" and t.visible]
        text = (plain or html or [""])[0]
        if len(text) <= limit:
            return text
        cut = text[:limit]
        space = cut.rfind(" ")
        return (cut[:space] if space > limit * 0.8 else cut).rstrip() + "…"


def normalize_message_id(value: str | None) -> str | None:
    if value is None:
        return None
    m = _MSGID.search(value)
    if m:
        return m.group(0)
    v = value.strip()
    return v or None


def stable_id(
    address_id: str, message_id: str | None, content_hash: str, uidvalidity: int, uid: int
) -> str:
    """SPEC §6.3: sha256(address_id | Message-ID | content_hash), or by UIDVALIDITY and UID
    when there is no Message-ID."""
    if message_id:
        key = f"{address_id}|{message_id}|{content_hash}"
    else:
        key = f"{address_id}|{uidvalidity}|{uid}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(ln.rstrip() for ln in lines).rstrip()


def parse(raw: bytes, *, max_scan_bytes: int = DEFAULT_SCAN_BYTES) -> ParsedMessage:
    msg = message_from_bytes(raw, policy=policy.default)
    h = hashlib.sha256(b"ecf-content-v1\0")
    texts: list[TextPart] = []
    attachments: list[Attachment] = []
    defects = len(msg.defects)
    for part in msg.walk():
        if part.is_multipart():
            continue
        defects += len(part.defects)
        ctype = part.get_content_type()
        disposition = part.get_content_disposition()
        if ctype in ("text/plain", "text/html") and disposition != "attachment":
            decoded = normalize_text(_decode_text(part))
            data = decoded.encode("utf-8")
            h.update(b"t" + len(data).to_bytes(8, "big") + data)
            texts.append(_text_part(ctype, decoded, max_scan_bytes))
        else:
            payload = part.get_payload(decode=True)
            data = payload if isinstance(payload, bytes) else b""
            h.update(b"a" + len(data).to_bytes(8, "big") + data)
            name = part.get_filename() or ""
            attachments.append(
                Attachment(name[:NAME_CHARS], ctype, len(data), disposition != "attachment")
            )
    from_values = _all(msg, "from")
    froms = getaddresses(list(from_values))
    from_name, from_addr = froms[0] if froms else ("", "")
    return ParsedMessage(
        message_id=normalize_message_id(_first(msg, "message-id")),
        from_count=len(from_values),
        from_addr=from_addr.lower() or None,
        from_name=from_name,
        reply_to=_addresses(msg, "reply-to"),
        to=_addresses(msg, "to"),
        cc=_addresses(msg, "cc"),
        subject=_first(msg, "subject") or "",
        headers={k: v for k in KEPT_HEADERS if (v := _all(msg, k))},
        texts=tuple(texts),
        attachments=tuple(attachments),
        content_hash=h.hexdigest(),
        defects=defects,
        size=len(raw),
    )


def _text_part(ctype: str, decoded: str, max_scan_bytes: int) -> TextPart:
    if ctype == "text/html":
        source = strip_data_uris(decoded)
        scanned, truncated = _cap(source, max_scan_bytes)
        full, visible = html_to_text(scanned)
    else:
        scanned, truncated = _cap(decoded, max_scan_bytes)
        full = visible = tidy(scanned)
    return TextPart(ctype, full, visible, truncated)


def _cap(text: str, max_bytes: int) -> tuple[str, bool]:
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text, False
    return data[:max_bytes].decode("utf-8", "ignore"), True


def _decode_text(part: Message) -> str:
    """The part as Unicode; unknown or wrong charsets fall back to UTF-8 with replacement."""
    if isinstance(part, EmailMessage):
        try:
            content = part.get_content()
            if isinstance(content, str):
                return content
        except (LookupError, UnicodeError, ValueError, AssertionError):
            pass
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, "replace")
    except (LookupError, ValueError):  # unknown charset; a null in its name is a ValueError
        return payload.decode("utf-8", "replace")


def _all(msg: Message, name: str) -> tuple[str, ...]:
    out: list[str] = []
    for v in msg.get_all(name) or []:
        try:
            out.append(str(v))
        except (ValueError, UnicodeError, IndexError):
            out.append("")
    return tuple(out)


def _first(msg: Message, name: str) -> str | None:
    values = _all(msg, name)
    return values[0] if values else None


def _addresses(msg: Message, name: str) -> tuple[str, ...]:
    return tuple(a.lower() for _n, a in getaddresses(list(_all(msg, name))) if a)
