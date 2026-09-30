"""`ecf replay <eml-dir>` (SPEC §10.2, §16.6; OD-238; V1.3 step 8d): a development tool that
appends `.eml` files into an IMAP mailbox (`--via append`, IMAP APPEND), for end-to-end runs and
the load test against a test server (the Dovecot container). Not for real mailboxes.

Each message gets a fresh Message-ID by default, so replaying the same files again makes new items
instead of duplicates (§16.6: "load-test replay uses fresh Message-IDs"); `--count` cycles through
the files until that many are appended. The password comes from a hidden prompt, never an argument
or the environment (CLAUDE.md, credentials). `--via smtp` (Postfix and OpenDMARC) isn't built.
"""

from __future__ import annotations

import contextlib
import imaplib
import re
import ssl
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ecf.errors import InvalidInputError, MailUnavailableError

_MID = re.compile(rb"(?im)^Message-ID:[^\r\n]*(\r?\n[ \t][^\r\n]*)*\r?\n")


def fresh_message_id(raw: bytes, token: str) -> bytes:
    """Replace (or add) the Message-ID header with one made from `token`."""
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        head, sep, body = raw.partition(b"\n\n")
        nl = b"\n"
    else:
        nl = b"\r\n"
    new = f"Message-ID: <replay-{token}@replay.acme.example>".encode() + nl
    head_nl = head + nl
    head_nl = _MID.sub(b"", head_nl) if _MID.search(head_nl) else head_nl
    return new + head_nl.rstrip(nl) + sep + body


def files(folder: Path) -> list[Path]:
    found = sorted(p for p in folder.glob("*.eml") if p.is_file())
    if not found:
        raise InvalidInputError(f"no .eml files in {folder}")
    return found


def replay(  # noqa: PLR0913 - keyword-only connection and options
    folder: Path,
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    count: int | None = None,
    fresh_ids: bool = True,
    cafile: Path | None = None,
    mailbox: str = "INBOX",
) -> int:
    """Append the files (cycled up to `count`); returns how many were appended."""
    paths = files(folder)
    total = count if count is not None else len(paths)
    ctx = ssl.create_default_context(cafile=str(cafile) if cafile else None)
    try:
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
        conn.login(user, password)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailUnavailableError(f"can't log in to {host}:{port}: {type(exc).__name__}") from None
    try:
        when = imaplib.Time2Internaldate(datetime.now(UTC).timestamp())
        for i in range(total):
            raw = paths[i % len(paths)].read_bytes()
            if fresh_ids:
                raw = fresh_message_id(raw, uuid.uuid4().hex[:16])
            typ, data = conn.append(mailbox, "", when, raw)
            if typ != "OK":
                raise MailUnavailableError(f"APPEND refused: {data!r:.100}")
    finally:
        with contextlib.suppress(OSError, imaplib.IMAP4.error):
            conn.logout()
    return total
