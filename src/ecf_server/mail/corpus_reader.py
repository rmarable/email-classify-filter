"""The read-only IMAP reader for `ecf corpus fetch` (SPEC §16.7; OD-466, OD-467).

`ImapSource` reads INBOX only and selects it read-write for some calls; a corpus reads one named
folder (All Mail by default on Gmail) and never changes anything. So this reader has its own small
connection over `_imapclient.Conn`: it EXAMINEs the folder once, reads by `BODY.PEEK` (no `\\Seen`),
and after a dropped connection makes a fresh one, logs in again and checks UIDVALIDITY before
going on. UID searches are bounded windows (never `UID SEARCH ALL`, whose single response line
overruns imaplib's 1 MB limit on a large All Mail; R48), and the metadata fetch is lean: size,
INTERNALDATE, the `X-ECF-Install` and `From` header lines, and on Gmail the labels and message ID.
Retries, sleeps and budget belong to the caller (`ecf_server.corpus`).
"""

from __future__ import annotations

import imaplib
import ssl
from array import array
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.parser import BytesHeaderParser
from email.utils import getaddresses
from typing import Any, Protocol

from ecf.errors import InvalidInputError, MailUnavailableError
from ecf_server import install_identity, internal
from ecf_server.mail import _imapclient as lib
from ecf_server.mail.imap import (
    COMMAND_S,
    CONNECT_S,
    GMAIL_CAPABILITY,
    GMAIL_LABELS,
    MailLoginRejectedError,
    tls_context,
)

WINDOW = 50_000  # UIDs per UID SEARCH window; a response stays near 450 KB (OD-467)
LIST_OCTETS = 900  # a UID list in one command stays near this many octets (R159)
HEADER_ITEM = "BODY.PEEK[HEADER.FIELDS (X-ECF-Install From)]"
_HEADER_KEY = "BODY[HEADER.FIELDS"  # the response key, upper-cased by imapclient (R132)
ROLE_NAMES = {"inbox": "INBOX", "all-mail": "\\All"}
REFUSED_ROLES = frozenset({"\\Trash", "\\Junk"})  # only with --allow-spam (R37)
DRAFTS = frozenset({"\\Draft", "\\Drafts"})  # Gmail's label spelling is unverified (R192)


class ConnLike(Protocol):
    def login(self, user: str, password: str) -> None: ...
    def logout(self) -> None: ...
    def noop(self) -> None: ...
    def capabilities(self) -> frozenset[str]: ...
    def list_folders(self) -> list[tuple[frozenset[str], str]]: ...
    def select(self, folder: str, *, readonly: bool) -> dict[str, Any]: ...
    def search(self, criteria: Sequence[lib.Criterion]) -> list[int]: ...
    def fetch(self, uids: Sequence[int], items: Sequence[str]) -> dict[int, dict[str, Any]]: ...


class UidValidityChangedError(MailUnavailableError):
    """The folder was renumbered while the corpus was read; the UIDs no longer name the same
    messages, so the run stops with what it has (R25)."""


@dataclass(frozen=True)
class FolderState:
    name: str
    uidvalidity: int
    uidnext: int
    exists: int


@dataclass(frozen=True)
class LeanMeta:
    uid: int
    size: int
    internaldate: datetime
    labels: frozenset[str]  # Gmail only
    gmail_msgid: int | None
    install_stamps: tuple[str, ...]  # X-ECF-Install values
    from_addrs: tuple[str, ...]  # every address in every From header, lower-cased


def resolve_folder(
    folders: Iterable[tuple[frozenset[str], str]], request: str, *, gmail: bool, allow_spam: bool
) -> str:
    """The exact folder name for `--folder` (a role, a name or `default`; R26, R37, R73)."""
    listed = list(folders)
    want = request.strip() or "default"
    if want == "default":
        want = "all-mail" if gmail else "inbox"
    role = ROLE_NAMES.get(want.lower())
    if role == "INBOX":
        return "INBOX"
    if role is not None:
        for flags, name in listed:
            if role in flags:
                return name
        hint = " (in Gmail, turn on Show in IMAP for All Mail)" if gmail else ""
        raise InvalidInputError(f"this mailbox has no {want} folder{hint}; give --folder inbox")
    for flags, name in listed:
        if name == want:
            if flags & REFUSED_ROLES and not allow_spam:
                raise InvalidInputError(f"{name} holds spam or deleted mail; add --allow-spam")
            return name
    raise InvalidInputError(f"no folder named {want!r} in this mailbox")


def is_own(meta: LeanMeta, *, source: str, mine: tuple[str, int] | None, include_own: bool) -> bool:
    """Drafts, this install's own mail, and sent mail other than notes to self (R22, R148,
    R191, R192). Forged or another install's `X-ECF-Install` stays in the corpus."""
    if include_own:
        return False
    if meta.labels & DRAFTS:
        return True
    stamps = [install_identity.parse_header(v) for v in meta.install_stamps]
    if stamps and mine is not None and all(s == mine for s in stamps):
        return True
    if "\\Sent" in meta.labels:
        note_to_self = "\\Inbox" in meta.labels and internal.fold(source) in {
            internal.fold(a) for a in meta.from_addrs
        }
        return not note_to_self
    return False


def header_fields(block: bytes) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(X-ECF-Install values, From addresses) from a HEADER.FIELDS response. A missing or
    malformed From gives no address (R192)."""
    msg = BytesHeaderParser(policy=policy.compat32).parsebytes(block)
    stamps = tuple(str(v).strip() for v in msg.get_all("X-ECF-Install", []))
    froms = [str(v) for v in msg.get_all("From", [])]
    addrs = tuple(a.lower() for _n, a in getaddresses(froms, strict=True) if "@" in a)
    return stamps, addrs


class CorpusReader:
    def __init__(
        self,
        host: str,
        user: str,
        password: Callable[[], str],
        *,
        port: int = 993,
        ssl_context: ssl.SSLContext | None = None,
        connect: Callable[[], ConnLike] | None = None,
    ) -> None:
        """`password` is called at each (re)connect; `connect` replaces the network in tests."""
        self._host, self._user, self._password = host, user, password
        ctx = ssl_context or tls_context()
        if ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname:
            raise ValueError("IMAP TLS context must verify certificates and host names")
        self._new: Callable[[], ConnLike] = connect or (
            lambda: lib.Conn(host, port, ctx, CONNECT_S, COMMAND_S)
        )
        self._conn: ConnLike | None = None
        self._logged_in_once = False
        self._folder: FolderState | None = None
        self.caps: frozenset[str] = frozenset()

    @property
    def gmail(self) -> bool:
        return GMAIL_CAPABILITY in self.caps

    @property
    def folder(self) -> FolderState:
        if self._folder is None:
            raise RuntimeError("examine a folder first")
        return self._folder

    # -- connection ------------------------------------------------------------------------------
    def open(self) -> frozenset[str]:
        """Connect and log in; returns the server's capabilities (Gmail mode comes from them,
        never from the host; OD-438)."""
        conn = self._connect()
        self.caps = self._call(conn.capabilities)
        return self.caps

    def _connect(self) -> ConnLike:
        if self._conn is not None:
            return self._conn
        try:
            conn = self._new()
        except (OSError, lib.IMAPClientError, imaplib.IMAP4.error) as exc:
            raise MailUnavailableError(f"cannot connect to {self._host}: {_why(exc)}") from None
        secret = ""
        try:
            secret = self._password()
            conn.login(self._user, secret)
        except lib.LoginError as exc:
            _quiet(conn.logout)
            why = server_reason(exc, secret)
            if self._logged_in_once:  # it worked before: a provider limit, not the password
                raise MailUnavailableError(f"{self._host} refused a new login{why}") from None
            raise MailLoginRejectedError(
                f"{self._host} rejected the login for {self._user}{why}"
            ) from None
        except (OSError, lib.IMAPClientError, imaplib.IMAP4.error) as exc:
            _quiet(conn.logout)
            raise MailUnavailableError(f"login to {self._host} failed: {_why(exc)}") from None
        except BaseException:
            _quiet(conn.logout)
            raise
        self._conn, self._logged_in_once = conn, True
        if self._folder is not None:  # a fresh connection after a drop: the same folder again
            before = self._folder
            self._folder = None
            if self.examine(before.name).uidvalidity != before.uidvalidity:
                raise UidValidityChangedError(f"{before.name} was renumbered (UIDVALIDITY changed)")
        return conn

    def _call[T](self, fn: Callable[[], T]) -> T:
        try:
            return fn()
        except (OSError, lib.IMAPClientError, imaplib.IMAP4.error) as exc:
            self.drop()
            raise MailUnavailableError(
                f"IMAP command failed on {self._host}: {_why(exc)}"
            ) from None

    def drop(self) -> None:
        if self._conn is not None:
            _quiet(self._conn.logout)
        self._conn = None

    def close(self) -> None:
        self.drop()

    def reconnect(self) -> None:
        """A fresh connection after a drop (R80): log in, read capabilities, EXAMINE the same
        folder and refuse to go on if UIDVALIDITY changed (R25). Any call after a drop does the
        same through `_connect`."""
        self.drop()
        self.open()

    def noop(self) -> None:
        conn = self._connect()
        self._call(conn.noop)

    # -- folder ----------------------------------------------------------------------------------
    def folders(self) -> list[tuple[frozenset[str], str]]:
        conn = self._connect()
        return self._call(conn.list_folders)

    def examine(self, name: str) -> FolderState:
        conn = self._connect()
        info = self._call(lambda: conn.select(name, readonly=True))
        uidnext = info.get("UIDNEXT")
        if uidnext is None:
            raise MailUnavailableError(f"{self._host} gave no UIDNEXT for {name}")
        self._folder = FolderState(
            name, int(info["UIDVALIDITY"]), int(uidnext), int(info.get("EXISTS") or 0)
        )
        return self._folder

    # -- reads -----------------------------------------------------------------------------------
    def window(self, lo: int, hi: int) -> list[int]:
        """UIDs present in [lo, hi]; at most WINDOW apart."""
        if lo > hi:
            return []
        if hi - lo + 1 > WINDOW:
            raise ValueError("window too wide")
        conn = self._connect()
        found = self._call(lambda: conn.search(["UID", f"{lo}:{hi}"]))
        return [u for u in found if lo <= u <= hi]  # a range past the last UID can return it

    def all_uids(self) -> array[int]:
        """Every UID in the folder, by windows (for `random`; R149). About 4 MB per million."""
        out: array[int] = array("I")
        for lo in range(1, self.folder.uidnext, WINDOW):
            out.extend(self.window(lo, min(lo + WINDOW - 1, self.folder.uidnext - 1)))
        return out

    def meta_range(self, lo: int, hi: int) -> dict[int, LeanMeta]:
        """Lean metadata for every message with a UID in [lo, hi]. imapclient keeps only the
        UIDs it was given by number, so the window is searched first."""
        return self.meta(self.window(lo, hi))

    def meta(self, uids: Iterable[int]) -> dict[int, LeanMeta]:
        """Lean metadata for scattered UIDs, in commands of about LIST_OCTETS octets."""
        conn = self._connect()
        out: dict[int, LeanMeta] = {}
        for chunk in _by_octets(sorted(set(uids))):
            data = self._call(lambda c=chunk: conn.fetch(c, self._items()))
            out.update((u, self._lean(u, d)) for u, d in data.items() if u in set(chunk))
        return out

    def fetch(self, uid: int) -> bytes | None:
        conn = self._connect()
        data = self._call(lambda: conn.fetch([uid], ["BODY.PEEK[]"]))
        return lib.as_bytes(data[uid].get("BODY[]")) if uid in data else None

    def _items(self) -> list[str]:
        items = ["RFC822.SIZE", "INTERNALDATE", HEADER_ITEM]
        return [*items, GMAIL_LABELS, "X-GM-MSGID"] if self.gmail else items

    def _lean(self, uid: int, d: dict[str, Any]) -> LeanMeta:
        block = next((v for k, v in d.items() if k.upper().startswith(_HEADER_KEY)), b"")
        stamps, froms = header_fields(lib.as_bytes(block) or b"")
        msgid = d.get("X-GM-MSGID")
        return LeanMeta(
            uid=uid,
            size=int(d["RFC822.SIZE"]),
            internaldate=lib.as_datetime(d["INTERNALDATE"]),
            labels=lib.flag_set(d.get(GMAIL_LABELS)),
            gmail_msgid=int(msgid) if msgid is not None else None,
            install_stamps=stamps,
            from_addrs=froms,
        )


def _by_octets(uids: Sequence[int]) -> Iterable[list[int]]:
    chunk: list[int] = []
    size = 0
    for u in uids:
        n = len(str(u)) + 1
        if chunk and size + n > LIST_OCTETS:
            yield chunk
            chunk, size = [], 0
        chunk.append(u)
        size += n
    if chunk:
        yield chunk


REASON_CHARS = 160


def server_reason(exc: BaseException, secret: str) -> str:
    """The server's own words for a refused login (such as Gmail's `[AUTHENTICATIONFAILED]
    Invalid credentials`), as `": <text>"`: printable characters only, capped, and never the
    password even if a server echoed it."""
    text = str(exc)
    if "b'" in text:  # imapclient wraps imaplib's bytes repr
        text = text[text.index("b'") + 2 :].rstrip("'")
    if secret:
        text = text.replace(secret, "[password removed]")
    text = "".join(c for c in text if c.isprintable()).strip()[:REASON_CHARS]
    return f": {text}" if text else ""


def _why(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:200]


def _quiet(fn: Callable[[], object]) -> None:
    with suppress(Exception):
        fn()
