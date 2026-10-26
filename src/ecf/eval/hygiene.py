"""Hygiene scan for the synthetic set (.claude/rules/eval-synthetic.md).

Fails on email addresses, URLs and dotted names outside the reserved names (a dotted name
counts unless it ends in a reserved name or a known file extension; defanged and non-ASCII
forms count);
phone numbers other than 555-01xx (with or without area code, plain 10-digit, international);
SSN-shaped numbers; Luhn-valid card numbers; IBANs and US routing numbers with valid checksums
(except published examples); cryptocurrency wallet shapes (Bitcoin, Ethereum, Monero; eval
generate plan P15); and key or token shapes. PDFs are generated only from card fields,
so scanning the cards also covers PDF text. Real names can't be detected automatically; review
covers them. Known false positive: a missing space after a full stop ("report.Summary") reads as
a domain; write the space. A card is scanned field by field (`builder.scan_card`): each header
field on its own, so a pattern anchored at a line start works in hidden text and attachment text.

Addresses (OD-554). The part of an address before the `@` is never read as a domain: every
address span is blanked before the host scan. A local part that imitates a domain
(`paypal.com@…`, a non-ASCII dotted name) is its own finding, `address local part looks like a
domain`. The other forms reuse existing kinds: a dotted handle after a bare `@` that imitates one
is a `domain` (a non-ASCII one a `non-ASCII domain (homoglyph or IDN)`), a second address glued
to a reserved one (`x@y.example+tag@paypal.com`) an `email address`, and a URL with a dotted
name before the `@` of its host part (`https://chase.bank@evil.example/`) a `URL`.
All-zero card and routing numbers are placeholders, not findings.

Each field's text is NFKC-normalised once, before every pattern, so fullwidth, mathematical and
look-alike-dot forms are read as their plain ASCII forms. Effects: NBSP-grouped numbers and the
fullwidth forms of `+41...`, `0x...` and `AKIA...` are findings; compatibility forms of reserved
names, file extensions and Authentication-Results names pass as their plain forms do; a bare
fullwidth host is a `domain`; values are the normalised text (a fullwidth `paypal.com` prints
`paypal.com`). A compatibility character that NFKC maps to `.`, `-` or letters glued to a host
hides it (a horizontal ellipsis before `paypal.com`). NFKC can expand text up to 18 times
(U+FDFA), the scan's only non-constant factor: every pattern is linear (one start per run, or
bounded quantifiers).
"""

from __future__ import annotations

import re
import unicodedata
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
    # added 2026-10-25 (OD-554): Office macro and template files, images, archives, programs
    "docm",
    "dotm",
    "dotx",
    "xlsm",
    "xltm",
    "xltx",
    "pptm",
    "potm",
    "ppsx",
    "ppsm",
    "heic",
    "webp",
    "bmp",
    "svg",
    "rar",
    "gz",
    "tar",
    "exe",
    "msi",
    "iso",
    "dmg",
    "apk",
    "js",
    "vbs",
    "bat",
    "lnk",
    "img",
}
# Extensions that are also real top-level domains: `paypal.com.zip` is still a name to flag.
TLD_EXTENSIONS = {"zip", "md", "py"}
# Endings a lookalike local part or handle imitates (OD-554, fixed 2026-10-25; no outside list).
ENDINGS = {"com", "net", "org", "gov", "edu", "mil", "co", "io", "biz", "info"} | {
    "us", "uk", "de", "ch", "fr", "eu", "ca", "au",
}  # fmt: skip
# Whole labels that mark a lookalike when they sit between the first label and the last.
MIDDLE_WORDS = {"com", "net", "org", "gov", "edu", "mil", "biz", "info"}
# Second-level labels before a country ending (`x.co.jp`, `oxford.ac.uk`).
SECOND_LEVEL = {"co", "ac"}
# For a name that ends in a file extension: the middle labels that make it a lookalike anyway.
_EXT_WORDS = {"com", "gov", "mil"}
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
# Defanged dots, colons and `://`; a leading whitespace run is taken only from its start, so a
# run of blanks is read once (the earlier form backtracked over it, cubic).
_DEFANGED = [
    (
        re.compile(r"(?:(?<!\s)\s+|)(?:\[\.\]|\(\.\)|\[dot\]|\(dot\))\s*|(?<!\s)\s+dot\s+", re.I),
        ".",
    ),
    (re.compile(r"(?:(?<!\s)\s+|)(?:\[:\]|\(:\))\s*"), ":"),
    (re.compile(r"(?:(?<!\s)\s+|)\[://\]\s*"), "://"),
]

PUBLISHED_IBANS = {"GB82WEST12345698765432", "DE89370400440532013000"}

# Address patterns start only where no address character precedes, so each is linear.
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@((?:[^\W_][\w-]*\.)+[^\W\d_]{2,})")
_ADDR_SPAN = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]*")
# A second address glued to the end of a span: glue, `@`, then an `_EMAIL`-shaped host.
_SECOND = re.compile(r"[\w.+-]*@((?:[^\W_][\w-]*\.)+[^\W\d_]{2,})")
_HANDLE = re.compile(r"(?<![\w.+-])@([\w.-]+)")
_URL = re.compile(r"\bhttps?://([^\s/:?#]+)", re.I)
# Any scheme; the authority is read in a look-ahead so the scan resumes after the slashes.
_AUTHORITY = re.compile(r"(?<![a-z0-9+.-])[a-z0-9+.-]*:[/\\]+(?=([a-z0-9._~:@%+-]*))", re.I | re.A)
_USERINFO_SPLIT = re.compile(r"[:@+]")
_DOT_LETTER = re.compile(r"\.[a-z]", re.I | re.A)
_HOST = re.compile(r"(?<![\w@.-])((?:[^\W_][\w-]*\.)+([^\W\d_]{2,}))(?![\w-])")
_LEAD = re.compile(r"[a-z]+")
VALUE_CAP = 200
_PHONE_NANP = re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?(\d{3})\)?[-. ](\d{3})[-. ](\d{4})(?!\d)")
_PHONE_LOCAL = re.compile(r"(?<![\d-])(\d{3})[-. ](\d{4})(?![\d-])")
_PHONE_PLAIN = re.compile(r"(?<!\d)[2-9]\d{2}[2-9]\d{6}(?!\d)")
_PHONE_INTL = re.compile(r"(?<![\w+])\+(?!1[-. (])\d{1,3}[-. ]?\d[\d -]{6,}\d")
_SSN = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")
_DIGITS = re.compile(r"(?<!\d)(?:\d[ \-\u2013.]?){12,18}\d(?!\d)")
_IBAN = re.compile(r"([A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30})", re.I)
_ABA = re.compile(r"(?<!\d)(\d{9})(?!\d)")
_WALLETS = [
    re.compile(r"(?<![\w])[13][a-km-zA-HJ-NP-Z1-9]{25,34}(?![\w])"),  # Bitcoin base58
    re.compile(r"(?<![\w])bc1[ac-hj-np-z02-9]{11,71}(?![\w])", re.I),  # Bitcoin bech32
    re.compile(r"(?<![\w])0x[a-fA-F0-9]{40}(?![\w])"),  # Ethereum
    re.compile(r"(?<![\w])[48][0-9AB][1-9A-HJ-NP-Za-km-z]{93}(?![\w])"),  # Monero
]
_SECRETS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[abpr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{10,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
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


def _all_zero(digits: str) -> bool:
    """A placeholder number (OD-554): every digit the ASCII `0`."""
    return digits.strip("0") == ""


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
    labels = host.lower().split(".")
    if _reserved(host):
        return False
    if labels[-1] not in FILE_EXTENSIONS or not host.isascii():
        return True
    # a file name, unless a middle label is a domain word (`paypal.com.zip`, `paypal.com.exe`)
    words = MIDDLE_WORDS if labels[-1] in TLD_EXTENSIONS else _EXT_WORDS
    return any(label in words for label in labels[1:-1])


def _lead(label: str, dash_ok: bool = False) -> str | None:
    """A label's leading letters, when the rest is empty, digits or (not for two letters) `-…`."""
    m = _LEAD.match(label)
    if m is None:
        return None
    rest = label[m.end() :]
    if rest == "" or rest.isdigit():
        return m.group()
    if rest.startswith("-") and (dash_ok or len(m.group()) != 2):
        return m.group()
    return None


def _lookalike(name: str) -> bool:
    """A dotted name imitating a domain, by the local-part rules (OD-554)."""
    if "." not in name or _reserved(name):
        return False
    if not name.isascii():
        return True  # rule 1, before casefold: `İ` and `ß` fold to ASCII-looking text
    labels = name.casefold().split(".")
    if _lead(labels[-1]) in ENDINGS:  # rule 2a
        return True
    for i in range(1, len(labels) - 1):  # rule 2b: `x.co.jp`, `paypal.co.uk-support`
        if labels[i] in SECOND_LEVEL and len(_lead(labels[i + 1], dash_ok=True) or "") == 2:
            return True
    return any(label in MIDDLE_WORDS for label in labels[1:-1])  # rule 2c


def _authority(text: str, where: str) -> list[Finding]:
    """A URL of any scheme with a dotted name before the `@` of its host part (OD-554)."""
    out: list[Finding] = []
    for m in _AUTHORITY.finditer(text):
        auth = m.group(1)
        userinfo, at, _ = auth.rpartition("@")
        if not at:
            continue
        pieces = _USERINFO_SPLIT.split(userinfo)
        if any(_DOT_LETTER.search(p) and not _reserved(p) for p in pieces):
            out.append(Finding(where, "URL", m.group(0) + auth))
    return out


def blank_addresses(text: str) -> str:
    """`text` with every address span replaced by spaces (the brand check's display-name text)."""
    return _ADDR_SPAN.sub(lambda m: " " * len(m.group()), text)


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
    out += _authority(text, where)
    # address spans: second addresses and lookalike local parts; then the host scan reads the
    # text with every span blanked
    skip: set[int] = set()
    for m in _ADDR_SPAN.finditer(text):
        second = _SECOND.match(text, m.end())
        if second is not None and not _reserved(second.group(1)):
            out.append(Finding(where, "email address", "@" + second.group(1)))
            skip.add(second.start(1) - 1)
        local = m.group(0).split("@", 1)[0]
        if any(_lookalike(piece) for piece in local.split("+")):
            out.append(Finding(where, "address local part looks like a domain", local))
    rest = blank_addresses(text)
    for m in _HANDLE.finditer(rest):
        name = m.group(1).strip(".-")
        if m.start() in skip or name.lower() in AR_PROPERTIES or not _lookalike(name):
            continue
        kind = "domain" if name.isascii() else "non-ASCII domain (homoglyph or IDN)"
        out.append(Finding(where, kind, name))
    for m in _HOST.finditer(rest):
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
        if 13 <= len(digits) <= 19 and not _all_zero(digits) and _luhn(digits):
            out.append(Finding(where, "card number (Luhn-valid)", m.group(0)))
    for m in _IBAN.finditer(text):
        iban = m.group(1).replace(" ", "").upper()
        if iban not in PUBLISHED_IBANS and _iban_valid(iban):
            out.append(Finding(where, "IBAN (valid checksum)", m.group(1)))
    out += [
        Finding(where, "routing number (valid checksum)", m.group(1))
        for m in _ABA.finditer(text)
        if not _all_zero(m.group(1)) and _aba_valid(m.group(1))
    ]
    return out


def scan_text(text: str, where: str) -> list[Finding]:
    text = unicodedata.normalize("NFKC", text)
    secrets = [
        Finding(where, "secret-like token", m.group(0)[:12] + "…")
        for pat in _SECRETS
        for m in pat.finditer(text)
    ]
    wallets = [
        Finding(where, "crypto wallet address", m.group(0)[:12] + "…")
        for pat in _WALLETS
        for m in pat.finditer(text)
    ]
    return _addresses(text, where) + _numbers(text, where) + wallets + secrets


def cut(findings: list[Finding]) -> list[Finding]:
    """Values over `VALUE_CAP` characters cut, with `…` (after `dedupe`, so keys don't change)."""
    return [
        Finding(f.where, f.kind, f.value[:VALUE_CAP] + "…") if len(f.value) > VALUE_CAP else f
        for f in findings
    ]


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
