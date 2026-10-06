"""In-memory MailSources for tests and `ecf-server dev`. Both pass tests/mail_contract.py.

`FakeMailSource` is a plain IMAP mailbox: each folder holds its own copies. `GmailFakeSource` (V1.6
step 6, OD-438) is Gmail: one store of messages, each with its labels and flags, and every folder a
view of that store. Its behaviour follows the step 0 real-service test (SPEC §18, 2026-10-05);
what that test didn't check is marked unverified where it is coded.
"""

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
        if self._caps.gmail:
            raise ValueError("FakeMailSource isn't Gmail: use GmailFakeSource")
        self._folders = list(folders)
        self._msgs: dict[int, _Stored] = {}
        self.elsewhere: dict[str, dict[int, _Stored]] = {}  # other folders (moves, copies)
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
        return None if s is None else _fetch_part(s.raw, section, limit)

    def structure(self, uid: int) -> list[PartInfo] | None:
        s = self._msgs.get(uid)
        return None if s is None else _structure(s.raw)

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

    def copy_back(self, folder: str, uid: int) -> None:
        if (s := self._folder(folder).get(uid)) is not None:
            self._msgs[self._next] = _Stored(s.raw, s.internaldate, set(s.flags))
            self._next += 1

    def gmail_msgid(self, uid: int) -> int | None:
        return None

    def gmail_find(self, folder: str, msgid: int) -> list[int]:
        return []

    def gmail_labels(self, uids: Iterable[int]) -> dict[int, frozenset[str]]:
        return {}

    def gmail_inbox_counts(self) -> tuple[int, int] | None:
        return None

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


GMAIL_ALL = "[Gmail]/All Mail"
GMAIL_SPAM = "[Gmail]/Spam"
GMAIL_TRASH = "[Gmail]/Trash"
# As a personal account lists them (step 0, 2026-10-05); [Gmail]/Important has no role flag and
# isn't modelled.
GMAIL_FOLDERS = (
    Folder("INBOX", frozenset()),
    Folder(GMAIL_ALL, frozenset({"\\All"})),
    Folder("[Gmail]/Drafts", frozenset({"\\Drafts"})),
    Folder("[Gmail]/Sent Mail", frozenset({"\\Sent"})),
    Folder(GMAIL_SPAM, frozenset({"\\Junk"})),
    Folder("[Gmail]/Starred", frozenset({"\\Flagged"})),
    Folder(GMAIL_TRASH, frozenset({"\\Trash"})),
)
GMAIL_CAPS = Capabilities(custom_keywords=True, move=True, uidplus=True, condstore=True,
                          append_limit=35_651_584, gmail=True)  # fmt: skip
# The Gmail label a role's folder shows. All Mail shows every message outside Spam and Trash;
# Starred shows `\\Flagged`; a folder with no role is a user label of the same name.
_ROLE_LABEL = {"\\Drafts": "\\Draft", "\\Sent": "\\Sent", "\\Junk": "\\Spam", "\\Trash": "\\Trash"}
_OUT = frozenset({"\\Spam", "\\Trash"})  # a message with either shows only in that folder


@dataclass
class _GmailMessage:
    raw: bytes
    internaldate: datetime
    msgid: int  # X-GM-MSGID
    labels: set[str] = field(default_factory=set[str])  # X-GM-LABELS, `\\Inbox` included
    flags: set[str] = field(default_factory=set[str])  # one set, seen from every folder


class GmailFakeSource:
    """Gmail over IMAP (OD-438). Each message is stored once; a folder lists the messages with its
    label, each under that folder's own UID. A message gaining a label gets a new UID there (every
    return to INBOX gets a new INBOX UID); its X-GM-MSGID and keywords never change. `folders`
    are the ones shown over IMAP (Gmail's "Show in IMAP" setting); `inbox_limit` is Gmail's IMAP
    folder size limit: INBOX then shows only the newest that many (OD-440)."""

    def __init__(self, *, folders: tuple[Folder, ...] = GMAIL_FOLDERS,
                 user_labels: Iterable[str] = ()) -> None:  # fmt: skip
        self._folders = [*folders, *(Folder(n, frozenset()) for n in user_labels)]
        self._store: dict[int, _GmailMessage] = {}
        self._views: dict[str, dict[int, int]] = {f.name: {} for f in self._folders}  # uid->msgid
        self._uidnext = dict.fromkeys(self._views, 1)
        self._uidvalidity = {f.name: 1 if f.name == "INBOX" else 10 + i
                             for i, f in enumerate(self._folders)}  # fmt: skip
        self._next_msgid = 1_878_227_840_400_856_218  # the size of a real one
        self._inbox_limit: int | None = None
        self.closed = False

    # -- test helpers ---------------------------------------------------------------------------
    @property
    def inbox_limit(self) -> int | None:
        return self._inbox_limit

    @inbox_limit.setter
    def inbox_limit(self, limit: int | None) -> None:
        self._inbox_limit = limit
        self._sync()

    def deliver(self, raw: bytes, internaldate: datetime | None = None, *,
                labels: Iterable[str] = ("\\Inbox",)) -> int:  # fmt: skip
        """A message arriving; `labels` e.g. `\\Sent` and `\\Inbox` for mail sent to oneself.
        Returns its INBOX UID (0 if it isn't in INBOX). Unlike APPEND, never merged by
        Message-ID here (whether Gmail drops a delivered duplicate: unverified)."""
        m = self._new(raw, internaldate or datetime.now(UTC))
        m.labels.update(labels)
        self._sync()
        return self._uid("INBOX", m)

    def expunge(self, uid: int) -> None:
        """The user deleting a message from INBOX in their mail client: Gmail archives it."""
        if (m := self._get("INBOX", uid)) is not None:
            self._expunge(self._folder("INBOX"), m)

    def user_expunge(self, folder: str, uid: int) -> None:
        """The user's mail client expunging a message in any folder, All Mail and Trash too."""
        if (m := self._get(folder, uid)) is not None:
            self._expunge(self._folder(folder), m)

    def message(self, msgid: int) -> tuple[frozenset[str], frozenset[str]] | None:
        """(labels, flags) of a stored message, or None once it is deleted for good."""
        m = self._store.get(msgid)
        return None if m is None else (frozenset(m.labels), frozenset(m.flags))

    # -- MailSource -----------------------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return GMAIL_CAPS

    def folders(self) -> list[Folder]:
        return list(self._folders)

    def inbox(self) -> InboxState:
        # Gmail lists no PERMANENTFLAGS on a read-only EXAMINE (step 0)
        return InboxState(self._uidvalidity["INBOX"], self._uidnext["INBOX"], frozenset())

    def uids_after(self, last_uid: int) -> list[int]:
        return sorted(u for u in self._views["INBOX"] if u > last_uid)

    def uids_since(self, when: datetime) -> list[int]:
        day = when.date()
        return sorted(u for u, m in self._in("INBOX") if m.internaldate.date() >= day)

    def find_message_id(self, message_id: str) -> list[int]:
        return self.find_in("INBOX", message_id)

    def existing(self, uids: Iterable[int]) -> set[int]:
        return {u for u in uids if u in self._views["INBOX"]}

    def meta(self, uids: Iterable[int]) -> dict[int, MessageMeta]:
        out: dict[int, MessageMeta] = {}
        for u in uids:
            if (m := self._get("INBOX", u)) is not None:
                out[u] = MessageMeta(u, len(m.raw), m.internaldate, _message_id(_parse(m.raw)))
        return out

    def fetch(self, uid: int) -> bytes | None:
        return self.fetch_in("INBOX", uid)

    def fetch_part(self, uid: int, section: str, limit: int) -> bytes | None:
        m = self._get("INBOX", uid)
        return None if m is None else _fetch_part(m.raw, section, limit)

    def structure(self, uid: int) -> list[PartInfo] | None:
        m = self._get("INBOX", uid)
        return None if m is None else _structure(m.raw)

    def flags(self, uids: Iterable[int]) -> dict[int, frozenset[str]]:
        return {u: frozenset(m.flags) for u in uids if (m := self._get("INBOX", u)) is not None}

    def add_keyword(self, uid: int, keyword: str) -> None:
        self._set_flag(uid, check_keyword(keyword), on=True)

    def remove_keyword(self, uid: int, keyword: str) -> None:
        self._set_flag(uid, check_keyword(keyword), on=False)

    def set_flagged(self, uid: int, flagged: bool) -> None:
        self._set_flag(uid, FLAGGED, on=flagged)

    def set_seen(self, uid: int, seen: bool) -> None:
        self._set_flag(uid, SEEN, on=seen)

    def move(self, uid: int, folder: str) -> None:
        """INBOX to All Mail archives (removes `\\Inbox`); to a label folder swaps `\\Inbox` for
        that label (step 0)."""
        target = self._folder(folder)
        if (m := self._get("INBOX", uid)) is not None:
            self._label(m, target, on=True)
            m.labels.discard("\\Inbox")
            self._sync()

    def copy(self, uid: int, folder: str) -> None:
        target = self._folder(folder)
        if (m := self._get("INBOX", uid)) is not None:
            self._label(m, target, on=True)
            self._sync()

    def find_in(self, folder: str, message_id: str) -> list[int]:
        self._folder(folder)
        return sorted(u for u, m in self._in(folder) if _message_id(_parse(m.raw)) == message_id)

    def fetch_in(self, folder: str, uid: int) -> bytes | None:
        self._folder(folder)
        m = self._get(folder, uid)
        return None if m is None else m.raw

    def move_back(self, folder: str, uid: int) -> None:
        """A label folder to INBOX swaps the label for `\\Inbox`, with a new INBOX UID (step 0)."""
        source = self._refuse(folder, "move a message back out of")
        if (m := self._get(folder, uid)) is not None:
            self._label(m, source, on=False)
            m.labels.add("\\Inbox")
            self._sync()

    def copy_back(self, folder: str, uid: int) -> None:
        """Adds `\\Inbox`: All Mail to INBOX gives a new INBOX UID and no duplicate (step 0). Out
        of Spam or Trash it also takes that label off (unverified)."""
        self._folder(folder)
        if (m := self._get(folder, uid)) is not None:
            m.labels -= _OUT
            m.labels.add("\\Inbox")
            self._sync()

    def gmail_msgid(self, uid: int) -> int | None:
        m = self._get("INBOX", uid)
        return None if m is None else m.msgid

    def gmail_find(self, folder: str, msgid: int) -> list[int]:
        self._folder(folder)
        return sorted(u for u, m in self._in(folder) if m.msgid == msgid)

    def gmail_labels(self, uids: Iterable[int]) -> dict[int, frozenset[str]]:
        return {u: frozenset(m.labels) for u in uids if (m := self._get("INBOX", u)) is not None}

    def gmail_inbox_counts(self) -> tuple[int, int] | None:
        if not any("\\All" in f.roles for f in self._folders):
            return None
        return len(self._views["INBOX"]), sum(map(self._inbox_label, self._store.values()))

    def append(self, folder: str, raw: bytes, flags: Iterable[str] = ()) -> None:
        """Gmail merges an APPEND into the stored message with the same Message-ID: one message
        (one X-GM-MSGID), now also in `folder` under a new UID (step 0, an identical copy to
        Sent; a different message reusing the Message-ID is unverified)."""
        target = self._folder(folder)
        mid = _message_id(_parse(raw))
        m = next((m for m in self._store.values() if mid and _message_id(_parse(m.raw)) == mid),
                 None)  # fmt: skip
        if m is None:
            m = self._new(raw, datetime.now(UTC))
        else:
            self._views[folder] = {u: i for u, i in self._views[folder].items() if i != m.msgid}
        self._label(m, target, on=True)
        m.flags.update(flags)
        self._sync()

    def delete_in(self, folder: str, uid: int) -> None:
        source = self._refuse(folder, "delete a message in")
        if (m := self._get(folder, uid)) is not None:
            self._expunge(source, m)

    def close(self) -> None:
        self.closed = True

    # -- the store ------------------------------------------------------------------------------
    def _new(self, raw: bytes, internaldate: datetime) -> _GmailMessage:
        m = _GmailMessage(raw, internaldate, self._next_msgid)
        self._next_msgid += 1
        self._store[m.msgid] = m
        return m

    def _folder(self, folder: str) -> Folder:
        f = next((f for f in self._folders if f.name == folder), None)
        if f is None:
            raise MailUnavailableError(f"no folder {folder!r}")
        return f

    def _refuse(self, folder: str, what: str) -> Folder:
        f = self._folder(folder)
        if f.roles & {"\\All", "\\Trash"}:
            raise MailUnavailableError(f"Gmail: ecf never tries to {what} {folder}")
        return f

    def _get(self, folder: str, uid: int) -> _GmailMessage | None:
        msgid = self._views.get(folder, {}).get(uid)
        return None if msgid is None else self._store.get(msgid)

    def _in(self, folder: str) -> list[tuple[int, _GmailMessage]]:
        return [(u, self._store[i]) for u, i in self._views[folder].items()]

    def _uid(self, folder: str, m: _GmailMessage) -> int:
        return next((u for u, i in self._views[folder].items() if i == m.msgid), 0)

    def _set_flag(self, uid: int, flag: str, *, on: bool) -> None:
        if (m := self._get("INBOX", uid)) is not None:
            (m.flags.add if on else m.flags.discard)(flag)
            self._sync()  # Starred follows `\\Flagged`

    def _label(self, m: _GmailMessage, f: Folder, *, on: bool) -> None:
        """Put the label `f` shows on `m`, or take it off. All Mail has none."""
        if f.name == "INBOX":
            label = "\\Inbox"
        elif "\\All" in f.roles:
            return
        elif "\\Flagged" in f.roles:
            (m.flags.add if on else m.flags.discard)(FLAGGED)
            return
        else:
            label = next((_ROLE_LABEL[r] for r in f.roles if r in _ROLE_LABEL), f.name)
        (m.labels.add if on else m.labels.discard)(label)

    def _expunge(self, f: Folder, m: _GmailMessage) -> None:
        """`\\Deleted` and EXPUNGE of `m` in `f`, as Gmail does it (account settings at their
        defaults; step 0 checked Auto-Expunge on and off): in INBOX or a label folder it takes the
        label off and the message stays in All Mail (archived); in Drafts and Trash it is deleted
        for good. In All Mail it does nothing while the message is in another folder; otherwise
        it moves to Trash (unverified). In Sent and Spam it takes the label off (unverified)."""
        if f.roles & {"\\Drafts", "\\Trash"}:
            del self._store[m.msgid]
        elif "\\All" in f.roles:
            if not m.labels:
                m.labels.add("\\Trash")
        else:
            self._label(m, f, on=False)
        self._sync()

    def _inbox_label(self, m: _GmailMessage) -> bool:
        return "\\Inbox" in m.labels and not m.labels & _OUT

    def _shows(self, f: Folder, m: _GmailMessage) -> bool:
        if f.name == "INBOX":
            return self._inbox_label(m)
        out = m.labels & _OUT
        if "\\All" in f.roles:
            return not out
        if "\\Flagged" in f.roles:
            return FLAGGED in m.flags and not out
        label = next((_ROLE_LABEL[r] for r in f.roles if r in _ROLE_LABEL), f.name)
        return label in m.labels and (label in _OUT or not out)

    def _sync(self) -> None:
        """Bring every folder's view in line with the store: messages that lost the folder's label
        leave it; ones that gained it get the folder's next UID."""
        for f in self._folders:
            want = sorted(i for i, m in self._store.items() if self._shows(f, m))
            if f.name == "INBOX" and self._inbox_limit is not None:
                want = want[-self._inbox_limit :] if self._inbox_limit else []
            keep = set(want)
            view = {u: i for u, i in self._views[f.name].items() if i in keep}
            have = set(view.values())
            for i in want:
                if i not in have:
                    view[self._uidnext[f.name]] = i
                    self._uidnext[f.name] += 1
            self._views[f.name] = view


def _fetch_part(raw: bytes, section: str, limit: int) -> bytes | None:
    """`BODY.PEEK[section]<0.limit>` of a raw message."""
    if section == "HEADER":
        return _split(raw)[0][:limit]
    part: Message = _parse(raw)
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
    linesep = "\r\n" if b"\r\n" in raw else "\n"
    return _split(part.as_bytes(policy=policy.compat32.clone(linesep=linesep)))[1][:limit]


def _structure(raw: bytes) -> list[PartInfo]:
    out: list[PartInfo] = []
    linesep = "\r\n" if b"\r\n" in raw else "\n"

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

    walk(_parse(raw), "")
    return out


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
