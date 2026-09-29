"""A typed facade over the parts of `imapclient` ecf uses (SPEC §17.2: one facade per untyped
library). imapclient ships no type information, so it is imported as `Any` here and every result
is converted to typed values before it leaves this module.

Library facts relied on (imapclient 4.1.0; checked against its source and the V1.1 real-service
test, 2026-09-28): full fetches come back under `b"BODY[]"`, partial ones under
`b"BODY[<section>]<0>"`; with `normalise_times = False` INTERNALDATE is timezone-aware.
"""

from __future__ import annotations

import importlib
import ssl
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

_lib: Any = importlib.import_module("imapclient")
_exc: Any = importlib.import_module("imapclient.exceptions")

# Everything the library raises for protocol, login and state problems.
IMAPClientError: type[Exception] = _exc.IMAPClientError
LoginError: type[Exception] = _exc.LoginError

Criterion = str | int | date


class Conn:
    def __init__(
        self, host: str, port: int, ctx: ssl.SSLContext, connect_s: float, read_s: float
    ) -> None:
        timeout = _lib.SocketTimeout(connect=connect_s, read=read_s)
        self._c: Any = _lib.IMAPClient(host, port=port, ssl=True, ssl_context=ctx, timeout=timeout)
        self._c.normalise_times = False

    def login(self, user: str, password: str) -> None:
        self._c.login(user, password)

    def logout(self) -> None:
        self._c.logout()

    def noop(self) -> None:
        self._c.noop()

    def capabilities(self) -> frozenset[str]:
        return frozenset(_s(c).upper() for c in self._c.capabilities())

    def list_folders(self) -> list[tuple[frozenset[str], str]]:
        return [
            (frozenset(_s(f) for f in flags), str(name))
            for flags, _d, name in self._c.list_folders()
        ]

    def select(self, folder: str, *, readonly: bool) -> dict[str, Any]:
        return {_s(k): v for k, v in self._c.select_folder(folder, readonly=readonly).items()}

    def status_uidnext(self, folder: str) -> int:
        return int(self._c.folder_status(folder, ["UIDNEXT"])[b"UIDNEXT"])

    def search(self, criteria: Sequence[Criterion]) -> list[int]:
        return sorted(int(u) for u in self._c.search(list(criteria)))

    def fetch(self, uids: Sequence[int], items: Sequence[str]) -> dict[int, dict[str, Any]]:
        raw = self._c.fetch(list(uids), list(items))
        return {int(u): {_s(k): v for k, v in d.items()} for u, d in raw.items()}

    def add_flags(self, uids: Sequence[int], flags: Sequence[str]) -> None:
        self._c.add_flags(list(uids), list(flags), silent=True)

    def remove_flags(self, uids: Sequence[int], flags: Sequence[str]) -> None:
        self._c.remove_flags(list(uids), list(flags), silent=True)


def envelope_message_id(envelope: Any) -> str | None:
    mid = getattr(envelope, "message_id", None)
    return None if mid is None else _s(mid).strip() or None


def as_datetime(v: Any) -> datetime:
    if not isinstance(v, datetime):
        raise TypeError(f"expected datetime, got {type(v).__name__}")
    return v


def as_bytes(v: Any) -> bytes | None:
    return v if isinstance(v, bytes) else None


def flag_set(v: Any) -> frozenset[str]:
    return frozenset(_s(f) for f in v or ())


def _s(v: Any) -> str:
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
