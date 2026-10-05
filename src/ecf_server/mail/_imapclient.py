"""A typed facade over the parts of `imapclient` ecf uses (SPEC §17.2: one facade per untyped
library). imapclient ships no type information, so it is imported as `Any` here and every result
is converted to typed values before it leaves this module.

Library facts relied on (imapclient 4.1.0; checked against its source and the V1.1 real-service
test, 2026-09-28): full fetches come back under `b"BODY[]"`, partial ones under
`b"BODY[<section>]<0>"`; with `normalise_times = False` INTERNALDATE is timezone-aware.

imapclient sets the wrapped imaplib connection's `debug` to 5 and routes it to the
`imapclient.imaplib` logger (imapclient.py, `__init__`). At that level imaplib formats every command
and response with `%r` whether or not the logger is enabled: the LOGIN line with the app password,
and each fetched message in full. That cost about 3 times the message size per fetch, which the
process never gave back (measured 2026-09-29 on macOS 27: six 46 MB fetches grew the footprint by
about 144 MB each), and at DEBUG it would log secrets and content. `Conn` sets it to 0 (OD-195).
"""

from __future__ import annotations

import importlib
import ssl
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, cast

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
        self._c._imap.debug = 0  # see the module docstring

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

    def gmail_search(self, query: str) -> list[int]:
        """`X-GM-RAW`: Gmail's own search syntax, in the selected folder."""
        return sorted(int(u) for u in self._c.gmail_search(query))

    def fetch(self, uids: Sequence[int], items: Sequence[str]) -> dict[int, dict[str, Any]]:
        raw = self._c.fetch(list(uids), list(items))
        return {int(u): {_s(k): v for k, v in d.items()} for u, d in raw.items()}

    def add_flags(self, uids: Sequence[int], flags: Sequence[str]) -> None:
        self._c.add_flags(list(uids), list(flags), silent=True)

    def remove_flags(self, uids: Sequence[int], flags: Sequence[str]) -> None:
        self._c.remove_flags(list(uids), list(flags), silent=True)

    def move(self, uids: Sequence[int], folder: str) -> None:
        """RFC 6851 UID MOVE on the selected folder (needs the MOVE capability)."""
        self._c.move(list(uids), folder)

    def copy(self, uids: Sequence[int], folder: str) -> None:
        self._c.copy(list(uids), folder)

    def append(self, folder: str, raw: bytes, flags: Sequence[str]) -> None:
        self._c.append(folder, raw, flags=list(flags))

    def uid_expunge(self, uids: Sequence[int]) -> None:
        """RFC 4315 UID EXPUNGE: only these UIDs (needs UIDPLUS)."""
        self._c.uid_expunge(list(uids))


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


def leaf_parts(bs: Any) -> list[dict[str, Any]]:
    """Flatten a BODYSTRUCTURE into leaf parts with IMAP section numbers.

    Layout as imapclient returns it (checked against Dovecot 2.4.5, 2026-09-28): a multipart body
    is `([parts...], subtype, params, ...)`; a single part is `(type, subtype, params, id, desc,
    encoding, size, ...)` whose disposition sits at index 9 for text parts, 11 for message/rfc822
    and 8 otherwise (RFC 3501 body-type-1part plus extension fields). An attached message is one
    leaf; its own parts are not listed.
    """
    out: list[dict[str, Any]] = []

    def walk(node: Any, prefix: str) -> None:
        if node and isinstance(node[0], list):
            for i, child in enumerate(node[0], 1):
                walk(child, f"{prefix}.{i}" if prefix else str(i))
            return
        ctype = f"{_s(node[0]).lower()}/{_s(node[1]).lower()}"
        dsp_at = 9 if ctype.startswith("text/") else 11 if ctype == "message/rfc822" else 8
        raw_dsp: Any = node[dsp_at] if len(node) > dsp_at else None
        dsp: tuple[Any, ...] | None = (
            cast("tuple[Any, ...]", raw_dsp) if isinstance(raw_dsp, tuple) else None
        )
        params = _pairs(node[2])
        dsp_params = _pairs(dsp[1]) if dsp is not None and len(dsp) > 1 else {}
        out.append(
            {
                "section": prefix or "1",
                "content_type": ctype,
                "disposition": _s(dsp[0]).lower() if dsp else None,
                "filename": dsp_params.get("filename") or params.get("name"),
                "encoding": _s(node[5]).lower() if node[5] is not None else "7bit",
                "charset": params.get("charset"),
                "size": int(node[6] or 0),
            }
        )

    walk(bs, "")
    return out


def _pairs(v: Any) -> dict[str, str]:
    """A flat IMAP parameter list (k1, v1, k2, v2, ...) as a dict with lowercase keys."""
    if not isinstance(v, tuple):
        return {}
    items: list[Any] = list(cast("tuple[Any, ...]", v))
    return {_s(items[i]).lower(): _s(items[i + 1]) for i in range(0, len(items) - 1, 2)}
