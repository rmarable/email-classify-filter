"""Hygiene scan for the synthetic set (.claude/rules/eval-synthetic.md).

Fails on non-reserved domains, URLs and email addresses; phone numbers other than 555-01xx;
Luhn-valid card numbers; IBANs and US routing numbers with valid checksums (except published
examples); and key or token shapes. PDFs are generated only from card fields, so scanning the
cards also covers PDF text. Real names can't be detected automatically; review covers them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

RESERVED_TLDS = {"example", "test", "invalid", "localhost"}
RESERVED_DOMAINS = {"example.com", "example.net", "example.org"}
# Top-level domains that mean "this is a real, registrable name" when they end a hostname.
REAL_TLDS = {
    "com",
    "net",
    "org",
    "io",
    "co",
    "uk",
    "de",
    "us",
    "gov",
    "edu",
    "info",
    "biz",
    "ai",
    "app",
    "dev",
    "me",
    "fr",
    "ca",
    "au",
    "eu",
    "xyz",
    "online",
    "site",
    "shop",
    "cloud",
    "email",
}
PUBLISHED_IBANS = {"GB82WEST12345698765432", "DE89370400440532013000"}

_EMAIL = re.compile(r"[\w.+-]+@((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)
_URL = re.compile(r"\bhttps?://([a-z0-9.-]+)", re.I)
_HOST = re.compile(r"\b((?:[a-z0-9-]+\.)+([a-z]{2,}))\b", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?(\d{3})\)?[-. ](\d{3})[-. ](\d{4})(?!\d)")
_DIGITS = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30})\b")
_ABA = re.compile(r"(?<!\d)(\d{9})(?!\d)")
_SECRETS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[abpr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
]


@dataclass(frozen=True)
class Finding:
    where: str
    kind: str
    value: str


def _reserved(host: str) -> bool:
    host = host.lower().rstrip(".")
    return host.rsplit(".", 1)[-1] in RESERVED_TLDS or any(
        host == d or host.endswith("." + d) for d in RESERVED_DOMAINS
    )


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _iban_valid(iban: str) -> bool:
    s = iban[4:] + iban[:4]
    return int("".join(str(int(c, 36)) for c in s)) % 97 == 1


def _aba_valid(n: str) -> bool:
    d = [int(c) for c in n]
    return (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 == 0


def _addresses(text: str, where: str) -> list[Finding]:
    out = [
        Finding(where, "email address", m.group(0))
        for m in _EMAIL.finditer(text)
        if not _reserved(m.group(1))
    ]
    out += [
        Finding(where, "URL", m.group(0)) for m in _URL.finditer(text) if not _reserved(m.group(1))
    ]
    out += [
        Finding(where, "domain", m.group(1))
        for m in _HOST.finditer(text)
        if m.group(2).lower() in REAL_TLDS and not _reserved(m.group(1))
    ]
    return out


def _numbers(text: str, where: str) -> list[Finding]:
    out = [
        Finding(where, "phone number", m.group(0))
        for m in _PHONE.finditer(text)
        if not (m.group(2) == "555" and 100 <= int(m.group(3)) <= 199)
    ]
    for m in _DIGITS.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            out.append(Finding(where, "card number (Luhn-valid)", m.group(0)))
    for m in _IBAN.finditer(text):
        iban = m.group(1).replace(" ", "")
        if iban not in PUBLISHED_IBANS and _iban_valid(iban):
            out.append(Finding(where, "IBAN (valid checksum)", m.group(1)))
    out += [
        Finding(where, "routing number (valid checksum)", m.group(1))
        for m in _ABA.finditer(text)
        if _aba_valid(m.group(1))
    ]
    return out


def scan_text(text: str, where: str) -> list[Finding]:
    secrets = [
        Finding(where, "secret-like token", m.group(0)[:12] + "…")
        for pat in _SECRETS
        for m in pat.finditer(text)
    ]
    return _addresses(text, where) + _numbers(text, where) + secrets


def dedupe(findings: list[Finding]) -> list[Finding]:
    """A value can match several patterns (an email contains a domain); keep one per value."""
    seen: set[tuple[str, str]] = set()
    out: list[Finding] = []
    for f in findings:
        key = (f.where, f.value.split("@")[-1].lower())
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out
