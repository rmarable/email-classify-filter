"""The MailSource port (SPEC §3.3): one mailbox, INBOX only (§5.6), seen through semantic calls.

Every implementation passes the same contract tests (tests/mail_contract.py). Reads never set
`\\Seen` (fetches use PEEK). Adapters hide provider quirks: `uids_after` never returns a UID at
or below the one asked about, even though IMAP's `UID SEARCH UID n:*` can (§5.1). Writes cover
keywords and `\\Flagged` (label, flag and their undo; OD-189), and from V1.3 `\\Seen`, moving to a
folder and back, and copying (the hide actions and `label_folder`), and from V1.5 appending to a
folder (drafts, sent copies) and deleting one message there (undoing a draft). Sending is its own
port, `Sender` (ecf_server/mail/smtp.py).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ecf.errors import InvalidInputError

FLAGGED = "\\Flagged"
SEEN = "\\Seen"
# Folder roles ecf uses (RFC 6154 flags). Read from each folder's LIST flags, whether or not the
# server advertises SPECIAL-USE (Purelymail doesn't; real-service test 2026-09-28).
ROLES = frozenset({"\\Archive", "\\Drafts", "\\Junk", "\\Sent", "\\Trash", "\\All", "\\Flagged"})
# An IMAP keyword is an atom; ecf's own keywords are `$ecf_<install>_<t>` (SPEC §6.4). Install
# names may contain `-` (allowed in atoms) but never `_`, so the install can be read back.
_KEYWORD = re.compile(r"^\$?[A-Za-z0-9_-]{1,64}$")


def check_keyword(keyword: str) -> str:
    if not _KEYWORD.fullmatch(keyword):
        raise InvalidInputError(f"not a keyword ecf writes: {keyword!r}")
    return keyword


@dataclass(frozen=True)
class Capabilities:
    custom_keywords: bool  # `\*` in PERMANENTFLAGS
    move: bool
    uidplus: bool
    condstore: bool
    append_limit: int | None = None  # RFC 7889 APPENDLIMIT=n, when the server advertises it


@dataclass(frozen=True)
class Folder:
    name: str
    roles: frozenset[str]  # subset of ROLES


@dataclass(frozen=True)
class InboxState:
    uidvalidity: int
    uidnext: int
    # as reported on a read-only EXAMINE, which may be empty; use Capabilities for keywords
    permanent_flags: frozenset[str]


@dataclass(frozen=True)
class PartInfo:
    """One leaf MIME part, from BODYSTRUCTURE (SPEC §5.1: oversized messages)."""

    section: str  # IMAP section number, e.g. "1", "2.1"
    content_type: str  # lowercase type/subtype
    disposition: str | None  # "attachment", "inline" or None
    filename: str | None
    encoding: str  # content-transfer-encoding, lowercase
    charset: str | None
    size: int  # encoded size in bytes


@dataclass(frozen=True)
class MessageMeta:
    uid: int
    size: int  # RFC822.SIZE
    internaldate: datetime  # timezone-aware
    message_id: str | None  # as in the envelope, angle brackets kept


class MailSource(Protocol):
    def capabilities(self) -> Capabilities: ...
    def folders(self) -> list[Folder]: ...
    def inbox(self) -> InboxState: ...
    def uids_after(self, last_uid: int) -> list[int]:
        """INBOX UIDs strictly greater than `last_uid`, ascending."""
        ...

    def uids_since(self, when: datetime) -> list[int]:
        """INBOX UIDs whose INTERNALDATE is on or after `when`'s date (IMAP SINCE is by day)."""
        ...

    def find_message_id(self, message_id: str) -> list[int]: ...
    def existing(self, uids: Iterable[int]) -> set[int]:
        """The subset of `uids` still in INBOX (moved, archived or deleted ones are gone)."""
        ...

    def meta(self, uids: Iterable[int]) -> dict[int, MessageMeta]: ...
    def fetch(self, uid: int) -> bytes | None:
        """The full raw message (`BODY.PEEK[]`), or None if the UID no longer exists."""
        ...

    def fetch_part(self, uid: int, section: str, limit: int) -> bytes | None:
        """The first `limit` bytes of a body section (`"HEADER"`, `"1"`, `"2.1"`, …)."""
        ...

    def structure(self, uid: int) -> list[PartInfo] | None:
        """Leaf parts in document order; an attached message counts as one part. None if gone."""
        ...

    def flags(self, uids: Iterable[int]) -> dict[int, frozenset[str]]: ...
    def add_keyword(self, uid: int, keyword: str) -> None: ...
    def remove_keyword(self, uid: int, keyword: str) -> None: ...
    def set_flagged(self, uid: int, flagged: bool) -> None: ...
    def set_seen(self, uid: int, seen: bool) -> None: ...
    def move(self, uid: int, folder: str) -> None:
        """Move an INBOX message to `folder` (UID MOVE, or COPY + UID EXPUNGE with UIDPLUS)."""
        ...

    def copy(self, uid: int, folder: str) -> None:
        """Copy an INBOX message to `folder`; it stays in INBOX."""
        ...

    def find_in(self, folder: str, message_id: str) -> list[int]:
        """UIDs in `folder` whose Message-ID is exactly `message_id`."""
        ...

    def fetch_in(self, folder: str, uid: int) -> bytes | None: ...
    def move_back(self, folder: str, uid: int) -> None:
        """Move a message from `folder` back to INBOX (undo)."""
        ...

    def append(self, folder: str, raw: bytes, flags: Iterable[str] = ()) -> None:
        """Store a message in `folder` (IMAP APPEND), e.g. a draft with `\\Draft` or a sent copy
        with `\\Seen`. Find it again by Message-ID with `find_in`."""
        ...

    def delete_in(self, folder: str, uid: int) -> None:
        """Delete one message from `folder`: `\\Deleted` then UID EXPUNGE of just that UID
        (UIDPLUS); refused without UIDPLUS, since a plain EXPUNGE could remove other mail."""
        ...

    def close(self) -> None: ...
