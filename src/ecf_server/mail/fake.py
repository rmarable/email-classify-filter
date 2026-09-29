"""An in-memory MailSource for tests and `ecf-server dev`. Passes tests/mail_contract.py."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import Message
from typing import cast

from ecf_server.mail import (
    FLAGGED,
    Capabilities,
    Folder,
    InboxState,
    MessageMeta,
    check_keyword,
)

DEFAULT_FOLDERS = (
    Folder("INBOX", frozenset()),
    Folder("Archive", frozenset({"\\Archive"})),
    Folder("Drafts", frozenset({"\\Drafts"})),
    Folder("Junk", frozenset({"\\Junk"})),
    Folder("Sent", frozenset({"\\Sent"})),
    Folder("Trash", frozenset({"\\Trash"})),
)


@dataclass
class _Stored:
    raw: bytes
    internaldate: datetime
    flags: set[str] = field(default_factory=set[str])


class FakeMailSource:
    def __init__(
        self,
        *,
        uidvalidity: int = 1,
        caps: Capabilities | None = None,
        folders: tuple[Folder, ...] = DEFAULT_FOLDERS,
    ) -> None:
        self._uidvalidity = uidvalidity
        self._caps = caps or Capabilities(
            custom_keywords=True, move=True, uidplus=True, condstore=True
        )
        self._folders = list(folders)
        self._msgs: dict[int, _Stored] = {}
        self._next = 1
        self.closed = False

    # -- test helpers ---------------------------------------------------------------------------
    def deliver(self, raw: bytes, internaldate: datetime | None = None) -> int:
        uid = self._next
        self._next += 1
        self._msgs[uid] = _Stored(raw, internaldate or datetime.now(UTC))
        return uid

    def expunge(self, uid: int) -> None:
        """Simulates the user moving, archiving or deleting a message in their mail client."""
        self._msgs.pop(uid, None)

    def reset(self, uidvalidity: int) -> None:
        """Simulates a mailbox reset: same messages, new UIDVALIDITY, renumbered UIDs."""
        old = [self._msgs[u] for u in sorted(self._msgs)]
        self._uidvalidity, self._msgs, self._next = uidvalidity, {}, 1
        for s in old:
            self._msgs[self._next] = s
            self._next += 1

    # -- MailSource -----------------------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return self._caps

    def folders(self) -> list[Folder]:
        return list(self._folders)

    def inbox(self) -> InboxState:
        perm = {"\\Answered", "\\Deleted", "\\Draft", FLAGGED, "\\Seen"}
        if self._caps.custom_keywords:
            perm.add("\\*")
        return InboxState(self._uidvalidity, self._next, frozenset(perm))

    def uids_after(self, last_uid: int) -> list[int]:
        return sorted(u for u in self._msgs if u > last_uid)

    def uids_since(self, when: datetime) -> list[int]:
        day = when.date()
        return sorted(u for u, s in self._msgs.items() if s.internaldate.date() >= day)

    def find_message_id(self, message_id: str) -> list[int]:
        return sorted(u for u, s in self._msgs.items() if _message_id(_parse(s.raw)) == message_id)

    def existing(self, uids: Iterable[int]) -> set[int]:
        return {u for u in uids if u in self._msgs}

    def meta(self, uids: Iterable[int]) -> dict[int, MessageMeta]:
        out: dict[int, MessageMeta] = {}
        for u in uids:
            if (s := self._msgs.get(u)) is not None:
                out[u] = MessageMeta(u, len(s.raw), s.internaldate, _message_id(_parse(s.raw)))
        return out

    def fetch(self, uid: int) -> bytes | None:
        s = self._msgs.get(uid)
        return None if s is None else s.raw

    def fetch_part(self, uid: int, section: str, limit: int) -> bytes | None:
        s = self._msgs.get(uid)
        if s is None:
            return None
        if section == "HEADER":
            return _split(s.raw)[0][:limit]
        part: Message = _parse(s.raw)
        for n in section.split("."):
            if not part.is_multipart():
                if n != "1":
                    return None
                continue
            subs = _children(part)
            i = int(n) - 1
            if not 0 <= i < len(subs):
                return None
            part = subs[i]
        if part.is_multipart():
            return None
        linesep = "\r\n" if b"\r\n" in s.raw else "\n"
        return _split(part.as_bytes(policy=policy.compat32.clone(linesep=linesep)))[1][:limit]

    def flags(self, uids: Iterable[int]) -> dict[int, frozenset[str]]:
        return {u: frozenset(self._msgs[u].flags) for u in uids if u in self._msgs}

    def add_keyword(self, uid: int, keyword: str) -> None:
        check_keyword(keyword)
        if (s := self._msgs.get(uid)) is not None:
            s.flags.add(keyword)

    def remove_keyword(self, uid: int, keyword: str) -> None:
        check_keyword(keyword)
        if (s := self._msgs.get(uid)) is not None:
            s.flags.discard(keyword)

    def set_flagged(self, uid: int, flagged: bool) -> None:
        if (s := self._msgs.get(uid)) is not None:
            if flagged:
                s.flags.add(FLAGGED)
            else:
                s.flags.discard(FLAGGED)

    def close(self) -> None:
        self.closed = True


def _parse(raw: bytes) -> Message:
    return message_from_bytes(raw, policy=policy.compat32)


def _children(part: Message) -> list[Message]:
    payload = part.get_payload()
    if not isinstance(payload, list):
        return []
    return [p for p in cast("list[object]", payload) if isinstance(p, Message)]


def _message_id(msg: Message) -> str | None:
    v = msg.get("Message-ID")
    return str(v).strip() if v is not None else None


def _split(raw: bytes) -> tuple[bytes, bytes]:
    """(header block including the blank line, body), for CRLF or LF line endings."""
    for sep in (b"\r\n\r\n", b"\n\n"):
        head, found, body = raw.partition(sep)
        if found:
            return head + sep, body
    return raw, b""
