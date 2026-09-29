"""A typed facade over the parts of `dkimpy` ecf uses (SPEC §17.2: one facade per untyped library).

dkimpy 1.1.8 behaviour relied on (read from its source, 2026-09-29): `DKIM(msg).verify(idx=i,
dnsfunc=f)` calls `f(name, timeout=...)` for the key; problems loading the key (missing, malformed,
revoked, DNS timeout) are logged and `verify` returns False; a header-hash mismatch returns False
without logging; a body-hash mismatch raises `ValidationError`; a key that loads but is unusable
raises `KeyFormatError`. `verify` reports `l=` signatures valid; ecf checks the tag itself.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

_dkim: Any = importlib.import_module("dkim")
_util: Any = importlib.import_module("dkim.util")

Outcome = Literal[
    "valid", "bad_signature", "bad_body_hash", "no_key", "bad_key", "dns_error", "format"
]
DnsFunc = Callable[..., bytes | None]


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.errors: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.errors.append(record.getMessage())


@dataclass(frozen=True)
class Signature:
    index: int
    tags: dict[str, str]  # lowercase tag -> value; values decoded as ASCII with replacement


def signatures(raw: bytes) -> list[Signature]:
    """Every DKIM-Signature header, top first; unparsable ones get empty tags."""
    d: Any = _dkim.DKIM(raw)
    out: list[Signature] = []
    headers: list[tuple[bytes, bytes]] = list(d.headers)
    for name, value in headers:
        if name.lower() != b"dkim-signature":
            continue
        try:
            parsed: dict[bytes, bytes] = _util.parse_tag_value(value)
        except Exception:
            parsed = {}
        out.append(
            Signature(
                len(out),
                {
                    k.decode("ascii", "replace").lower(): v.decode("ascii", "replace")
                    for k, v in parsed.items()
                },
            )
        )
    return out


def header_names(raw: bytes) -> list[str]:
    """Lowercase names of the message's header fields, in order, as dkimpy sees them."""
    d: Any = _dkim.DKIM(raw)
    return [name.decode("ascii", "replace").lower() for name, _v in d.headers]


def verify(raw: bytes, index: int, dnsfunc: DnsFunc, key_status: Callable[[], str]) -> Outcome:
    """Verify one signature. `key_status()` reports what the DNS function saw for its key:
    "ok", "missing" or "error"."""
    capture = _Capture()
    logger = logging.getLogger(f"ecf.dkim.{id(capture)}")
    logger.propagate = False
    logger.addHandler(capture)
    try:
        d: Any = _dkim.DKIM(raw, logger=logger, timeout=3)
        try:
            ok = bool(d.verify(idx=index, dnsfunc=dnsfunc))
        except _dkim.ValidationError as exc:
            return "bad_body_hash" if "body hash mismatch" in str(exc) else "format"
        except (_dkim.KeyFormatError, _dkim.UnknownKeyTypeError):
            return "bad_key"
        except _dkim.DKIMException:
            return "format"
    finally:
        logger.removeHandler(capture)
    if ok:
        return "valid"
    if not capture.errors:
        return "bad_signature"
    status = key_status()
    return "dns_error" if status == "error" else "no_key" if status == "missing" else "bad_key"
