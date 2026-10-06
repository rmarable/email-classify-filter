"""The internal set and Gmail address folding (SPEC §7.2; V1.6; ADR 0021).

The internal set is `org_domains` (by domain) plus `org_addresses` (exact addresses, each with an
optional name; `ecf config apply`, §9.7), install-wide (OD-431). A monitored address is internal
only when one of the two covers it.

Gmail folding (OD-432) applies to gmail.com and googlemail.com only: lower-case, googlemail.com
becomes gmail.com, everything from the first `+` is dropped and dots in the local part are
removed. It identifies the same account for trigger 6 and impersonation, never for
`sender_origin`.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from typing import Any, cast

from ecf_server.skeleton import fold_ci

ORG_ADDRESSES_KEY = "config.org_addresses"
GMAIL_DOMAINS = ("gmail.com", "googlemail.com")
# where a Google account's app passwords are made and removed; they need 2-Step Verification and
# Google revokes them when the account password changes (support.google.com/accounts/answer/185833,
# fetched 2026-10-05)
GOOGLE_APP_PASSWORDS = "https://myaccount.google.com/apppasswords"
MIN_LOCAL = 5  # local parts compared for impersonation have at least this many characters
_WORD = re.compile(r"[^\W_]+")


@dataclass(frozen=True)
class OrgAddress:
    address: str  # lower-cased
    name: str | None = None


def org_addresses(conn: sqlite3.Connection) -> tuple[OrgAddress, ...]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (ORG_ADDRESSES_KEY,)).fetchone()
    entries = cast("list[dict[str, Any]]", json.loads(row["value"]) if row else [])
    return tuple(OrgAddress(str(e["address"]).lower(), e.get("name")) for e in entries)


def split(addr: str) -> tuple[str, str]:
    local, _, domain = addr.strip().lower().rpartition("@")
    return local, domain.rstrip(".")


def is_gmail(domain: str) -> bool:
    return domain in GMAIL_DOMAINS


def fold(addr: str) -> str:
    """The Gmail account an address delivers to; other addresses only lower-cased."""
    local, domain = split(addr)
    if is_gmail(domain):
        return f"{local.split('+', 1)[0].replace('.', '')}@gmail.com"
    return f"{local}@{domain}"


def bare_local(addr: str) -> str:
    """The local part lower-cased, without a `+tag` or dots: how impersonation compares local
    parts across providers (OD-448), whatever each provider does with dots."""
    return split(addr)[0].split("+", 1)[0].replace(".", "")


def name_words(text: str) -> tuple[str, ...]:
    """Words of letters and digits, NFKC-normalized, casefolded and skeleton-folded, accents
    dropped, so "José" and "Jose" are one word (OD-433)."""
    folded = "".join(c for c in fold_ci(text) if not unicodedata.combining(c))
    return tuple(_WORD.findall(folded))


def listed(addr: str | None, entries: tuple[OrgAddress, ...]) -> OrgAddress | None:
    """The entry for this address, exactly or as the same Gmail account (`from_org_address`)."""
    if not addr:
        return None
    exact = addr.lower()
    folded = fold(addr)
    return next((e for e in entries if e.address == exact or fold(e.address) == folded), None)


def exactly_listed(addr: str | None, entries: tuple[OrgAddress, ...]) -> bool:
    """Exactly in `org_addresses`, no folding: the test for `sender_origin` (OD-432)."""
    return bool(addr) and any(e.address == str(addr).lower() for e in entries)
