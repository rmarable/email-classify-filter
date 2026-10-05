"""An in-memory MailSource for tests and `ecf-server dev`. Passes tests/mail_contract.py."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import Message
from typing import cast

from ecf.errors import MailUnavailableError
from ecf_server.mail import (
    FLAGGED,
    SEEN,
    Capabilities,
    Folder,
    InboxState,
    MessageMeta,
    PartInfo,
    check_keyword,
)
from ecf_server.mail.smtp import SmtpInfo

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
        self.elsewhere: dict[str, dict[int, _Stored]] = {}  # other folders (moves, copies)
        self._next = 1
        self.closed = False
        # Gmail mode (caps.gmail): labels per INBOX UID (default `\\Inbox`), and the counts
        # `gmail_inbox_counts` reports (default: INBOX shows everything). V1.6 step 3; the full
        # Gmail fake (one store, folders as views) comes in step 6.
        self.labels: dict[int, frozenset[str]] = {}
        self.inbox_counts: tuple[int, int] | None = None

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

    def structure(self, uid: int) -> list[PartInfo] | None:
        s = self._msgs.get(uid)
        if s is None:
            return None
        out: list[PartInfo] = []
        linesep = "\r\n" if b"\r\n" in s.raw else "\n"

        def walk(part: Message, prefix: str) -> None:
            if part.is_multipart() and part.get_content_type() != "message/rfc822":
                for i, sub in enumerate(_children(part), 1):
                    walk(sub, f"{prefix}.{i}" if prefix else str(i))
                return
            body = _split(part.as_bytes(policy=policy.compat32.clone(linesep=linesep)))[1]
            out.append(
                PartInfo(
                    section=prefix or "1",
                    content_type=part.get_content_type(),
                    disposition=part.get_content_disposition(),
                    filename=part.get_filename(),
                    encoding=str(part.get("Content-Transfer-Encoding", "7bit")).strip().lower(),
                    charset=part.get_content_charset(),
                    size=len(body),
                )
            )

        walk(_parse(s.raw), "")
        return out

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

    def set_seen(self, uid: int, seen: bool) -> None:
        if (s := self._msgs.get(uid)) is not None:
            if seen:
                s.flags.add(SEEN)
            else:
                s.flags.discard(SEEN)

    def _folder(self, folder: str) -> dict[int, _Stored]:
        if folder not in {f.name for f in self._folders}:
            raise MailUnavailableError(f"no folder {folder!r}")
        return self.elsewhere.setdefault(folder, {})

    def move(self, uid: int, folder: str) -> None:
        target = self._folder(folder)
        if (s := self._msgs.pop(uid, None)) is not None:
            target[self._next] = s  # a new UID in the target folder, as a server gives
            self._next += 1

    def copy(self, uid: int, folder: str) -> None:
        target = self._folder(folder)
        if (s := self._msgs.get(uid)) is not None:
            target[self._next] = _Stored(s.raw, s.internaldate, set(s.flags))
            self._next += 1

    def find_in(self, folder: str, message_id: str) -> list[int]:
        return sorted(u for u, s in self._folder(folder).items()
                      if _message_id(_parse(s.raw)) == message_id)  # fmt: skip

    def fetch_in(self, folder: str, uid: int) -> bytes | None:
        s = self._folder(folder).get(uid)
        return None if s is None else s.raw

    def move_back(self, folder: str, uid: int) -> None:
        if (s := self._folder(folder).pop(uid, None)) is not None:
            self._msgs[self._next] = s
            self._next += 1

    def gmail_labels(self, uids: Iterable[int]) -> dict[int, frozenset[str]]:
        if not self._caps.gmail:
            return {}
        return {u: self.labels.get(u, frozenset({"\\Inbox"})) for u in uids if u in self._msgs}

    def gmail_inbox_counts(self) -> tuple[int, int] | None:
        if not self._caps.gmail or not any("\\All" in f.roles for f in self._folders):
            return None
        return self.inbox_counts or (len(self._msgs), len(self._msgs))

    def append(self, folder: str, raw: bytes, flags: Iterable[str] = ()) -> None:
        target = self._folder(folder)
        target[self._next] = _Stored(raw, datetime.now(UTC), set(flags))
        self._next += 1

    def delete_in(self, folder: str, uid: int) -> None:
        if not self._caps.uidplus:
            raise MailUnavailableError("no UIDPLUS: can't delete just one message")
        self._folder(folder).pop(uid, None)

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


class FakeSender:
    """A Sender for tests and `ecf-server dev`: records what it would send. `fail` makes the next
    sends fail: "temp" (not sent, retryable), "refused" (not sent), "unknown" (outcome unknown),
    "login" (login rejected)."""

    def __init__(self, *, size: int | None = 51_200_000) -> None:
        self.sent: list[tuple[str, tuple[str, ...], bytes]] = []
        self.fail: str | None = None
        self.size = size
        self.connects = 0

    def _connect(self) -> None:
        from ecf_server.mail.imap import MailLoginRejectedError  # noqa: PLC0415
        from ecf_server.mail.smtp import SendNotSentError  # noqa: PLC0415

        self.connects += 1
        if self.fail == "login":
            raise MailLoginRejectedError("fake: login rejected")
        if self.fail == "temp":
            raise SendNotSentError("fake: try later", retryable=True)

    def check(self) -> SmtpInfo:
        self._connect()
        return SmtpInfo(host="smtp.fake", port=465, size=self.size, eight_bit=True)

    def send(self, raw: bytes, mail_from: str, rcpt: Sequence[str]) -> None:
        from ecf_server.mail.smtp import SendNotSentError, SendOutcomeUnknownError  # noqa: PLC0415

        self._connect()
        if self.fail == "refused":
            raise SendNotSentError("fake: 550 refused", retryable=False)
        if self.fail == "unknown":
            self.sent.append((mail_from, tuple(rcpt), raw))  # it may well have gone
            raise SendOutcomeUnknownError("fake: no final reply")
        self.sent.append((mail_from, tuple(rcpt), raw))
