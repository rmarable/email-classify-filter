"""The IMAP MailSource (SPEC §5.1, §12.3): TLS only (TLS 1.2 or later, certificate verified,
no plaintext or STARTTLS), app-password login, INBOX only, reads by PEEK so `\\Seen` is never set.

Timeouts follow SPEC §15.4 (connect 15 s, command 60 s). Retries and backoff belong to the caller
(the fetch job), not this adapter. Errors surface as MailUnavailableError, or its subclass
MailLoginRejectedError for rejected credentials; the password never appears in either.
"""

from __future__ import annotations

import imaplib
import ssl
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from datetime import datetime
from typing import Any

from ecf.errors import MailUnavailableError
from ecf_server.mail import (
    FLAGGED,
    ROLES,
    Capabilities,
    Folder,
    InboxState,
    MessageMeta,
    PartInfo,
    check_keyword,
)
from ecf_server.mail import _imapclient as lib

CONNECT_S = 15.0
COMMAND_S = 60.0
INBOX = "INBOX"
CHUNK = 500  # UIDs per command, to keep command lines short


class MailLoginRejectedError(MailUnavailableError):
    """The provider rejected the app password (SPEC §13.3: Mailbox Login Rejected)."""


def tls_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class ImapSource:
    def __init__(
        self,
        host: str,
        user: str,
        password: Callable[[], str],
        *,
        port: int = 993,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        """`password` is called at each (re)connect, so the secret isn't kept on this object."""
        self._host, self._port, self._user, self._password = host, port, user, password
        ctx = ssl_context or tls_context()
        if ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname:
            raise ValueError("IMAP TLS context must verify certificates and host names")
        ctx.minimum_version = max(ctx.minimum_version, ssl.TLSVersion.TLSv1_2)
        self._ctx = ctx
        self._conn: lib.Conn | None = None
        self._readonly: bool | None = None

    # -- connection ------------------------------------------------------------------------------
    def _connect(self) -> lib.Conn:
        if self._conn is not None:
            return self._conn
        try:
            conn = lib.Conn(self._host, self._port, self._ctx, CONNECT_S, COMMAND_S)
        except (OSError, lib.IMAPClientError, imaplib.IMAP4.error) as exc:
            raise MailUnavailableError(f"cannot connect to {self._host}: {_why(exc)}") from None
        try:
            conn.login(self._user, self._password())
        except lib.LoginError:
            _quiet(conn.logout)
            raise MailLoginRejectedError(
                f"{self._host} rejected the login for {self._user}"
            ) from None
        except (OSError, lib.IMAPClientError, imaplib.IMAP4.error) as exc:
            _quiet(conn.logout)
            raise MailUnavailableError(f"login to {self._host} failed: {_why(exc)}") from None
        except BaseException:  # e.g. no password stored: never leave the connection open
            _quiet(conn.logout)
            raise
        self._conn, self._readonly = conn, None
        return conn

    def _inbox(self, *, readonly: bool = True) -> tuple[lib.Conn, dict[str, Any]]:
        conn = self._connect()
        info = self._call(lambda: conn.select(INBOX, readonly=readonly))
        self._readonly = readonly
        return conn, info

    def _call[T](self, fn: Callable[[], T]) -> T:
        try:
            return fn()
        except (OSError, lib.IMAPClientError, imaplib.IMAP4.error) as exc:
            self._drop()
            raise MailUnavailableError(
                f"IMAP command failed on {self._host}: {_why(exc)}"
            ) from None

    def _drop(self) -> None:
        if self._conn is not None:
            _quiet(self._conn.logout)
        self._conn, self._readonly = None, None

    def _reads(self) -> lib.Conn:
        if self._readonly is None or self._conn is None:
            return self._inbox(readonly=True)[0]
        return self._conn

    def _fresh(self) -> lib.Conn:
        """For calls about what INBOX holds now: a NOOP lets the server report changes made by
        other sessions (Dovecot holds back expunges until then; tested 2026-09-28)."""
        conn = self._reads()
        self._call(conn.noop)
        return conn

    def _writes(self) -> lib.Conn:
        if self._readonly is not False or self._conn is None:
            return self._inbox(readonly=False)[0]
        return self._conn

    # -- MailSource ------------------------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        conn = self._connect()
        caps = self._call(conn.capabilities)
        # PERMANENTFLAGS from a read-write SELECT: servers may report none on a read-only
        # EXAMINE (Dovecot does; tested 2026-09-28). Selecting read-write changes no message.
        _conn, info = self._inbox(readonly=False)
        return Capabilities(
            custom_keywords="\\*" in lib.flag_set(info.get("PERMANENTFLAGS")),
            move="MOVE" in caps,
            uidplus="UIDPLUS" in caps,
            condstore="CONDSTORE" in caps,
            append_limit=_append_limit(caps),
        )

    def folders(self) -> list[Folder]:
        conn = self._connect()
        return [Folder(name, flags & ROLES) for flags, name in self._call(conn.list_folders)]

    def inbox(self) -> InboxState:
        conn, info = self._inbox(readonly=True)
        uidnext = info.get("UIDNEXT")
        if uidnext is None:
            uidnext = self._call(lambda: conn.status_uidnext(INBOX))
        return InboxState(
            uidvalidity=int(info["UIDVALIDITY"]),
            uidnext=int(uidnext),
            permanent_flags=lib.flag_set(info.get("PERMANENTFLAGS")),
        )

    def uids_after(self, last_uid: int) -> list[int]:
        conn = self._fresh()
        found = self._call(lambda: conn.search(["UID", f"{last_uid + 1}:*"]))
        return [u for u in found if u > last_uid]  # `n:*` can return the highest UID (§5.1)

    def uids_since(self, when: datetime) -> list[int]:
        conn = self._fresh()
        return self._call(lambda: conn.search(["SINCE", when.date()]))

    def find_message_id(self, message_id: str) -> list[int]:
        conn = self._fresh()
        hits = self._call(lambda: conn.search(["HEADER", "Message-ID", message_id]))
        # HEADER search is a substring match; keep exact matches only.
        return sorted(u for u, m in self.meta(hits).items() if m.message_id == message_id)

    def existing(self, uids: Iterable[int]) -> set[int]:
        conn = self._fresh()
        out: set[int] = set()
        for chunk in _chunks(sorted(set(uids))):
            found = self._call(lambda c=chunk: conn.search(["UID", ",".join(map(str, c))]))
            out.update(u for u in found if u in chunk)
        return out

    def meta(self, uids: Iterable[int]) -> dict[int, MessageMeta]:
        conn = self._reads()
        out: dict[int, MessageMeta] = {}
        for chunk in _chunks(sorted(set(uids))):
            data = self._call(
                lambda c=chunk: conn.fetch(c, ["RFC822.SIZE", "INTERNALDATE", "ENVELOPE"])
            )
            for u, d in data.items():
                out[u] = MessageMeta(
                    uid=u,
                    size=int(d["RFC822.SIZE"]),
                    internaldate=lib.as_datetime(d["INTERNALDATE"]),
                    message_id=lib.envelope_message_id(d.get("ENVELOPE")),
                )
        return out

    def fetch(self, uid: int) -> bytes | None:
        conn = self._reads()
        data = self._call(lambda: conn.fetch([uid], ["BODY.PEEK[]"]))
        return lib.as_bytes(data[uid].get("BODY[]")) if uid in data else None

    def fetch_part(self, uid: int, section: str, limit: int) -> bytes | None:
        if not _valid_section(section) or limit <= 0:
            raise ValueError(f"bad section or limit: {section!r}, {limit}")
        conn = self._reads()
        data = self._call(lambda: conn.fetch([uid], [f"BODY.PEEK[{section}]<0.{limit}>"]))
        if uid not in data:
            return None
        return lib.as_bytes(data[uid].get(f"BODY[{section}]<0>")) or None

    def structure(self, uid: int) -> list[PartInfo] | None:
        conn = self._reads()
        data = self._call(lambda: conn.fetch([uid], ["BODYSTRUCTURE"]))
        if uid not in data or data[uid].get("BODYSTRUCTURE") is None:
            return None
        return [PartInfo(**p) for p in lib.leaf_parts(data[uid]["BODYSTRUCTURE"])]

    def flags(self, uids: Iterable[int]) -> dict[int, frozenset[str]]:
        conn = self._reads()
        out: dict[int, frozenset[str]] = {}
        for chunk in _chunks(sorted(set(uids))):
            data = self._call(lambda c=chunk: conn.fetch(c, ["FLAGS"]))
            out.update({u: lib.flag_set(d.get("FLAGS")) for u, d in data.items()})
        return out

    def add_keyword(self, uid: int, keyword: str) -> None:
        self._store(uid, check_keyword(keyword), add=True)

    def remove_keyword(self, uid: int, keyword: str) -> None:
        self._store(uid, check_keyword(keyword), add=False)

    def set_flagged(self, uid: int, flagged: bool) -> None:
        self._store(uid, FLAGGED, add=flagged)

    def _store(self, uid: int, flag: str, *, add: bool) -> None:
        conn = self._writes()
        if add:
            self._call(lambda: conn.add_flags([uid], [flag]))
        else:
            self._call(lambda: conn.remove_flags([uid], [flag]))

    def close(self) -> None:
        self._drop()


def _valid_section(section: str) -> bool:
    return section == "HEADER" or all(p.isdigit() and p != "0" for p in section.split("."))


def _append_limit(caps: frozenset[str]) -> int | None:
    for c in caps:
        name, _, value = c.partition("=")
        if name == "APPENDLIMIT" and value.isdigit():
            return int(value)
    return None


def _chunks(uids: Sequence[int]) -> Iterable[list[int]]:
    for i in range(0, len(uids), CHUNK):
        yield list(uids[i : i + CHUNK])


def _why(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:200]


def _quiet(fn: Callable[[], object]) -> None:
    with suppress(Exception):  # best-effort logout on an already failing connection
        fn()
