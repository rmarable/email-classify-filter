"""A fake `_imapclient.Conn` for the corpus reader (R195): one folder of messages, a 1 MB limit on a
response line like imaplib's, upper-cased response keys like imapclient, and dropped connections
on demand."""

from __future__ import annotations

import imaplib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ecf_server.mail import _imapclient as lib

MAXLINE = 1_000_000


@dataclass
class FakeMessage:
    raw: bytes
    labels: tuple[str, ...] = ()
    internaldate: datetime = field(default_factory=lambda: datetime(2026, 10, 1, tzinfo=UTC))


@dataclass
class FakeServer:
    """What every connection sees."""

    folders: dict[str, dict[int, FakeMessage]] = field(default_factory=lambda: {"INBOX": {}})
    flags: dict[str, frozenset[str]] = field(default_factory=dict[str, frozenset[str]])
    uidvalidity: int = 7
    gmail: bool = False
    drops: int = 0  # the next this many commands fail as a dropped connection
    login_errors: int = 0
    logins: int = 0
    commands: list[str] = field(default_factory=list[str])

    def uidnext(self, folder: str) -> int:
        return max(self.folders[folder], default=0) + 1


class FakeConn:
    def __init__(self, server: FakeServer) -> None:
        self.s = server
        self.selected: str | None = None

    def _cmd(self, name: str) -> None:
        self.s.commands.append(name)
        if self.s.drops > 0:
            self.s.drops -= 1
            raise OSError("connection reset")

    def login(self, user: str, password: str) -> None:
        self._cmd("LOGIN")
        if self.s.login_errors > 0:
            self.s.login_errors -= 1
            raise lib.LoginError("[UNAVAILABLE] too many connections")
        self.s.logins += 1

    def logout(self) -> None:
        pass

    def noop(self) -> None:
        self._cmd("NOOP")

    def capabilities(self) -> frozenset[str]:
        self._cmd("CAPABILITY")
        caps = {"IMAP4REV1", "UIDPLUS", "MOVE"}
        return frozenset(caps | ({"X-GM-EXT-1"} if self.s.gmail else set()))

    def list_folders(self) -> list[tuple[frozenset[str], str]]:
        self._cmd("LIST")
        return [(self.s.flags.get(n, frozenset()), n) for n in self.s.folders]

    def select(self, folder: str, *, readonly: bool) -> dict[str, Any]:
        self._cmd("EXAMINE" if readonly else "SELECT")
        assert readonly, "the corpus reader never selects read-write"
        self.selected = folder
        msgs = self.s.folders[folder]
        return {"UIDVALIDITY": self.s.uidvalidity, "UIDNEXT": self.s.uidnext(folder),
                "EXISTS": len(msgs)}  # fmt: skip

    def _msgs(self) -> dict[int, FakeMessage]:
        assert self.selected is not None
        return self.s.folders[self.selected]

    def search(self, criteria: Sequence[lib.Criterion]) -> list[int]:
        self._cmd("SEARCH")
        kind, spec = (str(c) for c in criteria)
        assert kind == "UID", "the reader searches by UID only"
        found = sorted(u for u in self._msgs() if _in(u, spec))
        if len(" ".join(map(str, found))) + 9 > MAXLINE:  # "* SEARCH " + UIDs on one line
            raise imaplib.IMAP4.error(f"got more than {MAXLINE} bytes")
        return found

    def fetch(self, uids: Sequence[int], items: Sequence[str]) -> dict[int, dict[str, Any]]:
        self._cmd("FETCH")
        return {u: self._item(u, items) for u in uids if u in self._msgs()}

    def _item(self, uid: int, items: Sequence[str]) -> dict[str, Any]:
        m = self._msgs()[uid]
        out: dict[str, Any] = {}
        for it in items:
            if it == "RFC822.SIZE":
                out[it] = len(m.raw)
            elif it == "INTERNALDATE":
                out[it] = m.internaldate
            elif it == "BODY.PEEK[]":
                out["BODY[]"] = m.raw
            elif it.startswith("BODY.PEEK[HEADER.FIELDS"):
                out[it.replace("BODY.PEEK", "BODY").upper()] = _fields(
                    m.raw, ("x-ecf-install", "from")
                )
            elif it == "X-GM-LABELS":
                out[it] = tuple(m.labels)
            elif it == "X-GM-MSGID":
                out[it] = 1_000_000 + uid
        return out


def _in(uid: int, spec: str) -> bool:
    for part in spec.split(","):
        lo, _, hi = part.partition(":")
        if (hi and int(lo) <= uid <= int(hi)) or (not hi and uid == int(lo)):
            return True
    return False


def _fields(raw: bytes, names: tuple[str, ...]) -> bytes:
    head = raw.split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0]
    lines = [ln for ln in head.replace(b"\r\n", b"\n").split(b"\n")
             if ln.split(b":", 1)[0].strip().lower().decode() in names]  # fmt: skip
    return b"".join(ln + b"\r\n" for ln in lines) + b"\r\n"
