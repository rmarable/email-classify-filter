"""Hygiene scan for the synthetic set (.claude/rules/eval-synthetic.md).

Fails on email addresses, URLs and dotted names outside the reserved names (a dotted name
counts unless it ends in a reserved name or a known file extension; defanged and non-ASCII
forms count);
phone numbers other than 555-01xx (with or without area code, plain 10-digit, international);
SSN-shaped numbers; Luhn-valid card numbers; IBANs and US routing numbers with valid checksums
(except published examples); and key or token shapes. PDFs are generated only from card fields,
so scanning the cards also covers PDF text. Real names can't be detected automatically; review
covers them. Known false positive: a missing space after a full stop ("report.Summary") reads as
a domain; write the space.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

RESERVED_TLDS = {"example", "test", "invalid", "localhost"}
RESERVED_DOMAINS = {"example.com", "example.net", "example.org"}
# A dotted name counts as a real domain unless it ends in a reserved name or one of these file
# extensions (so `invoice.pdf` passes but `ubs.ch` does not).
FILE_EXTENSIONS = {
    "pdf",
    "eml",
    "msg",
    "txt",
    "md",
    "rtf",
    "doc",
    "docx",
    "odt",
    "xls",
    "xlsx",
    "csv",
    "ppt",
    "pptx",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "tif",
    "tiff",
    "zip",
    "html",
    "htm",
    "json",
    "yaml",
    "yml",
    "xml",
    "ics",
    "py",
    "sql",
    "log",
}
# Authentication-Results property names look like dotted names but aren't hosts.
AR_PROPERTIES = {
    "header.from",
    "header.d",
    "header.i",
    "header.b",
    "header.s",
    "header.a",
    "smtp.mailfrom",
    "smtp.helo",
    "smtp.auth",
    "smtp.rcptto",
    "policy.dmarc",
    "policy.iprev",
}
_DEFANGED = [(re.compile(r"\s*(?:\[\.\]|\(\.\)|\[dot\]|\(dot\)|\s+dot\s+)\s*", re.I), ".")]

PUBLISHED_IBANS = {"GB82WEST12345698765432", "DE89370400440532013000"}

_EMAIL = re.compile(r"[\w.+-]+@((?:[^\W_][\w-]*\.)+[^\W\d_]{2,})")
_URL = re.compile(r"\bhttps?://([^\s/:?#]+)", re.I)
_HOST = re.compile(r"(?<![\w@.-])((?:[^\W_][\w-]*\.)+([^\W\d_]{2,}))(?![\w-])")
_PHONE_NANP = re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?(\d{3})\)?[-. ](\d{3})[-. ](\d{4})(?!\d)")
_PHONE_LOCAL = re.compile(r"(?<![\d-])(\d{3})[-. ](\d{4})(?![\d-])")
_PHONE_PLAIN = re.compile(r"(?<!\d)[2-9]\d{2}[2-9]\d{6}(?!\d)")
_PHONE_INTL = re.compile(r"(?<![\w+])\+(?!1[-. (])\d{1,3}[-. ]?\d[\d -]{6,}\d")
_SSN = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")
_DIGITS = re.compile(r"(?<!\d)(?:\d[ \-\u2013.]?){12,18}\d(?!\d)")
_IBAN = re.compile(r"([A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30})", re.I)
_ABA = re.compile(r"(?<!\d)(\d{9})(?!\d)")
_SECRETS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[abpr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{10,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{20}"),
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


def _real_host(host: str) -> bool:
    if host.lower() in AR_PROPERTIES:
        return False
    last = host.rsplit(".", 1)[-1].lower()
    return not _reserved(host) and (last not in FILE_EXTENSIONS or not host.isascii())


def _addresses(text: str, where: str) -> list[Finding]:
    for pat, repl in _DEFANGED:
        text = pat.sub(repl, text)
    out = [
        Finding(where, "email address", m.group(0))
        for m in _EMAIL.finditer(text)
        if not _reserved(m.group(1))
    ]
    out += [
        Finding(where, "URL", m.group(0)) for m in _URL.finditer(text) if not _reserved(m.group(1))
    ]
    for m in _HOST.finditer(text):
        host = m.group(1)
        if _real_host(host):
            kind = "domain" if host.isascii() else "non-ASCII domain (homoglyph or IDN)"
            out.append(Finding(where, kind, host))
    return out


def _numbers(text: str, where: str) -> list[Finding]:
    out = [
        Finding(where, "phone number", m.group(0))
        for m in _PHONE_NANP.finditer(text)
        if not (m.group(2) == "555" and 100 <= int(m.group(3)) <= 199)
    ]
    out += [
        Finding(where, "phone number", m.group(0))
        for m in _PHONE_LOCAL.finditer(text)
        if not (m.group(1) == "555" and 100 <= int(m.group(2)) <= 199)
    ]
    out += [
        Finding(where, "phone number", m.group(0))
        for m in _PHONE_PLAIN.finditer(text)
        if m.group(0)[3:6] != "555"
    ]
    out += [Finding(where, "phone number", m.group(0)) for m in _PHONE_INTL.finditer(text)]
    out += [Finding(where, "SSN-like number", m.group(0)) for m in _SSN.finditer(text)]
    for m in _DIGITS.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            out.append(Finding(where, "card number (Luhn-valid)", m.group(0)))
    for m in _IBAN.finditer(text):
        iban = m.group(1).replace(" ", "").upper()
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
