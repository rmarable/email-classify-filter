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

import base64
import binascii
import hashlib
import quopri
import re
from dataclasses import dataclass
from email import message_from_bytes, policy
from email.message import EmailMessage, Message
from email.parser import BytesHeaderParser
from email.utils import getaddresses

from ecf_server.htmltext import html_to_text, strip_data_uris, tidy
from ecf_server.mail import PartInfo

HASH_VERSION = 1
PARTIAL_HASH_VERSION = 0  # oversized messages: headers and part sizes only (see parse_partial)
CLASSIFIER_CHARS = 1500  # SPEC §5.1 step 4
ACTOR_CHARS = 4000
NAME_CHARS = 100  # attachment names are capped (§7.2)
DEFAULT_SCAN_BYTES = 10 * 1024 * 1024  # max_scan_bytes_per_part default (OD-027)
KEPT_HEADERS = (
    "from",
    "sender",
    "reply-to",
    "to",
    "cc",
    "subject",
    "date",
    "message-id",
    "in-reply-to",
    "references",
    "list-id",
    "list-unsubscribe",
    "precedence",
    "auto-submitted",
    "x-autoreply",
    "return-path",
    "x-ecf-install",
    "content-type",
    "mime-version",
    "content-transfer-encoding",
)
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
    from_ambiguous: bool = False  # the From header doesn't name exactly one clear address

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
    return _parsed(msg, texts, attachments, h.hexdigest(), defects, len(raw), HASH_VERSION)


def _parsed(
    msg: Message,
    texts: list[TextPart],
    attachments: list[Attachment],
    content_hash: str,
    defects: int,
    size: int,
    hash_version: int,
) -> ParsedMessage:
    from_values = _all(msg, "from")
    froms = [(n, a) for n, a in getaddresses(list(from_values)) if a]
    from_name, from_addr = froms[0] if froms else ("", "")
    # raw values: the parsed header object re-renders the address and hides the ambiguity
    raw_from = [str(v) for k, v in msg.raw_items() if k.lower() == "from"]
    ambiguous = len(froms) != 1 or any(_ats(v) > 1 for v in raw_from)
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
        content_hash=content_hash,
        defects=defects,
        hash_version=hash_version,
        size=size,
        from_ambiguous=bool(from_values) and ambiguous,
    )


def _ats(value: str) -> int:
    """`@` signs outside quoted strings and comments: more than one means parsers can disagree
    about which address is the sender (`ceo@acme.example <x@evil.test>`)."""
    without = re.sub(r'"(?:[^"\\]|\\.)*"', "", value)
    without = re.sub(r"\([^()]*\)", "", without)
    return without.count("@")


def parse_partial(
    header_block: bytes,
    parts: list[PartInfo],
    texts: dict[str, bytes],
    *,
    size: int,
    max_scan_bytes: int = DEFAULT_SCAN_BYTES,
) -> ParsedMessage:
    """A message over the size limit (SPEC §5.1): its headers and the first bytes of each text
    part (`texts`, keyed by section, as fetched), plus the attachment list from BODYSTRUCTURE.

    Its content can't be hashed without fetching it all, so the hash (`hash_version` 0) covers
    fields that stay the same when a message is delivered again (Message-ID, Date, From, Subject;
    not Received or Delivered-To) and each part's section, type and encoded size. Nobody can
    mistake it for a content hash.
    """
    msg = BytesHeaderParser(policy=policy.default).parsebytes(header_block)
    h = hashlib.sha256(b"ecf-partial-v1\0")
    for name in ("message-id", "date", "from", "subject"):
        value = "\n".join(_all(msg, name)).encode("utf-8", "replace")
        h.update(len(value).to_bytes(8, "big") + value)
    out_texts: list[TextPart] = []
    attachments: list[Attachment] = []
    for p in parts:
        h.update(f"{p.section}|{p.content_type}|{p.size}\n".encode())
        if p.content_type in ("text/plain", "text/html") and p.disposition != "attachment":
            fetched = texts.get(p.section, b"")
            decoded = normalize_text(_charset(_transfer_decode(fetched, p.encoding), p.charset))
            text = _text_part(p.content_type, decoded, max_scan_bytes)
            cut = p.size > len(fetched)
            out_texts.append(
                TextPart(text.content_type, text.full, text.visible, text.truncated or cut)
            )
        else:
            approx = p.size * 3 // 4 if p.encoding == "base64" else p.size
            name = (p.filename or "")[:NAME_CHARS]
            attachments.append(
                Attachment(name, p.content_type, approx, p.disposition != "attachment")
            )
    return _parsed(msg, out_texts, attachments, h.hexdigest(), 0, size, PARTIAL_HASH_VERSION)


def _transfer_decode(data: bytes, encoding: str) -> bytes:
    if encoding == "base64":
        compact = re.sub(rb"[^A-Za-z0-9+/=]", b"", data)
        compact = compact[: len(compact) - len(compact) % 4]  # a cut fetch ends mid-quantum
        try:
            return base64.b64decode(compact)
        except binascii.Error:
            return b""
    if encoding == "quoted-printable":
        return quopri.decodestring(data)
    return data


def _charset(data: bytes, charset: str | None) -> str:
    try:
        return data.decode(charset or "utf-8", "replace")
    except (LookupError, ValueError):
        return data.decode("utf-8", "replace")


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
