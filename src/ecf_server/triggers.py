"""Deterministic triggers (SPEC §8.5): computed by the service over every decoded text part
(visible and full text), the Subject, the display name and attachment names; no model involved.

Keywords come from data/keywords.yaml and match whole words after folding (skeleton.py), so
lookalike letters, zero-width characters and full-width forms don't hide them. Results:

- `fraud`: fraud triggers 1-9 that fired, as plain-language reasons (rule 1, the fraud guard).
- `fraud_weak`: a first-time sender with a payment keyword and no second signal (OD-062), a
  Reply-To mismatch on a payment item with no other signal (OD-068), or a risky attachment
  (`facts.risky`) with a payment, credential or gift-card keyword (pre-classifier plan §5 C)
  (rule 1b).
- `regulator`: regulator keywords found (rule 2).
- Fraud trigger 10 (OD-252): text addressed to an automated reader ("note to the classifier",
  "ignore previous instructions"); an email that tells the model what to conclude goes to a person.
- `bec_opener` (a fact, not a trigger; OD-479): two or more different phrases from the
  `bec_opener` list, the opening message of an executive-impersonation scam before any money is
  named; rule 1 escalates it from an outside sender.
- `tax_form_request` (a fact, not a trigger; OD-482): a `tax_form` phrase (W-2, payroll records)
  and a `data_request` phrase (send me, as one PDF), the W-2 scam; rule 1 escalates it from an
  outside sender.
- `gift_card_codes_request` (a fact; pre-classifier plan §3): a `gift_card` phrase and a
  `gift_card_ask` phrase ("send me the codes", "photo of the back"), the gift-card scam.
- `amount_mentioned` (a fact, recorded for measurement only; pre-classifier plan §5 A2): a
  currency sign or code next to a digit.
- A checksum-valid IBAN counts as a `bank` keyword ("valid IBAN"), so trigger 1 covers it.
- `unverified_payment`: a payment keyword and `auth_result = none`, counting a pass whose MIME
  headers were unsigned as none (OD-187) (rule 1a); not for a human-verified sender (OD-065).

Impersonation (V1.6, OD-433, OD-436, OD-448; ADR 0021) is trigger 7's "org address or staff
name" clause: someone outside the internal set using a listed name, an internal address in the
display name, a one-edit Gmail typo of a listed address, or a listed local part at another public
provider. It is the fact `impersonates_internal`, which rules 1 and 1b split by money (with the
classifier's `payment_related` too); its reasons also go in `fraud` with a payment keyword and in
`fraud_weak` without one, as trigger 2's do, so everything that counts fraud signals counts it
(OD-449) and the model-free pre-check acts on it.

Since V1.5 trigger 9 skips ecf's own mail and another install's (own_mail.py); loop suppression
for alert mail arrives with email alerts.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache, lru_cache
from importlib import resources
from typing import Any

from ecf.yamlio import load_yaml
from ecf_server import internal
from ecf_server.facts import PUBLIC_DOMAINS, domain_of
from ecf_server.htmltext import COMMON_TLDS
from ecf_server.message import ParsedMessage
from ecf_server.skeleton import fold_ci, folds, keyword_fold, keyword_fold_ci, normalize

GROUPS = ("bank", "change", "payment", "gift_card", "gift_card_ask", "bec_opener", "tax_form",
          "data_request", "credential", "credential_ask", "credential_never", "crypto_pay",
          "regulator", "injection")  # fmt: skip
# Cryptocurrency wallet addresses (OD-485), matched on `entity_text`, not the folded text (the
# skeleton would change their letters), so a zero-width character or a mark inside one doesn't
# hide it: Bitcoin (bech32, legacy), Ethereum, and the synthetic set's placeholder
# (`TEST-WALLET-0001`; real shapes fail its hygiene scan). A hit is recorded as "wallet address",
# never the address. One alternation, so the text is read once (R3-P16).
WALLET = re.compile(
    r"(?<![\w-])(?:bc1[ac-hj-np-z02-9]{11,71}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|0x[0-9a-fA-F]{40}"
    r"|TEST-WALLET-\d{4})(?![\w-])"
)
# IBANs (pre-classifier plan §3): country code, check digits, then letters and digits, each
# optionally after one space or hyphen; bounded, and never starting inside a word. Matched on
# `entity_text`; a hit is recorded as "valid IBAN", never the number.
# A money amount (pre-classifier plan §5 A2, `amount_mentioned`): a currency sign or code next to
# a digit, either side ("$50", "USD 1,200", "1.234,56 €", "4,800 dollars"), or a bare number with
# two decimals ("1,180.00", not "9.50%", "1.0000" or "10.10.2026"); a bare whole number doesn't
# count. Matched on `entity_text`, so a no-break space or a full-width sign counts. Four searches,
# each starting with a fixed character or word so the regex engine skips ahead to it, each fixed
# width or bounded, and the bare form never starts inside a number: linear on any input (§4).
# Recorded for measurement only: not a route, not a signal.
_CODES = ("USDC", "USDT", "USD", "EUR", "GBP", "CAD", "AUD", "NZD", "CHF", "JPY", "INR")
_SIGN = "[$€£¥₹]"
AMOUNTS = (
    re.compile(rf"{_SIGN} ?[0-9]"),
    re.compile(rf"[0-9] ?(?:{_SIGN}|(?:{'|'.join(_CODES)}|(?i:dollars?|euros?))(?![A-Za-z]))"),
    # each code checks the letter before it after matching, so the search starts on its letters
    re.compile(rf"(?:{'|'.join(f'{c}(?<![A-Za-z]{c})' for c in _CODES)})(?![A-Za-z]) ?[0-9]"),
    re.compile(r"[0-9](?<![\w.,][0-9])[0-9]{0,2}(?:,[0-9]{3}){0,5}\.[0-9]{2}(?![\w%]|[.,][0-9])"),
)
AMOUNT_HIT = "amount"
IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}[0-9]{2}(?:[ -]?[A-Z0-9]){11,30}")
# Where an IBAN can start: tried first, so a head naming no country costs no full match.
IBAN_HEAD = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}[0-9]{2}")
IBAN_HIT = "valid IBAN"
# IBAN-shaped candidates checked per text (R3-P12): a crafted text of IBAN heads costs a checksum
# each. Past this many with none valid, the scan says so (`unscanned`), so decoys can't hide one.
IBAN_CANDIDATES = 256
# Full matches tried per text, counted or not (v2.2.2): every head of a listed country is tried, so
# heads too few to reach their country's length would cost a match each without this cap.
IBAN_ATTEMPTS = 16 * IBAN_CANDIDATES
# IBAN length per country, from the SWIFT IBAN registry (unverified against its current release;
# a country not listed never matches).
IBAN_LENGTHS = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22,
    "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "DO": 28,
    "EE": 20, "EG": 29, "ES": 24, "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23,
    "GL": 18, "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23, "IS": 26,
    "IT": 27, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20,
    "LV": 21, "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18,
    "NO": 15, "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "SA": 24,
    "SC": 31, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "ST": 25, "SV": 28, "TL": 23, "TN": 24,
    "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20,
}  # fmt: skip
_IBAN_DIGITS = str.maketrans({chr(c): str(c - 55) for c in range(ord("A"), ord("Z") + 1)})
BEC_OPENER_MIN = 2  # different `bec_opener` phrases for the fact (OD-479)
TYPO_MIN = 5  # a one-edit typo only counts for names of at least this many letters
# Shared services that give each customer a subdomain; initial list, unverified which domains
# each sends from (OD-203).
SAAS_TENANT_PARENTS = ("zendesk.com", "freshdesk.com", "atlassian.net", "service-now.com")
# Top-level domains a bare domain in a display name must end in to count for trigger 7; an
# address always counts. Common ones only, so "Node.js" or "Vue.js" don't (V1.1 review).
DISPLAY_TLDS = COMMON_TLDS
_DOMAIN = re.compile(r"(?<![\w@.-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})(?![\w-])")
_ADDRESS = re.compile(r"[\w.+-]+@((?:[a-z0-9-]+\.)+[a-z]{2,})")
# Text scanned per message, over all its distinct texts, in bytes as Python holds it (1, 2 or 4
# per character, so a non-BMP character can't make the bound 4x larger; pre-gate review G7)
# (R3-P11): the scan runs in the check thread with no time limit, and a message may have many
# parts of up to `max_scan_bytes_per_part` each. The rest is not scanned and the message is
# `content_unscanned`.
SCAN_BYTES = 24 * 1024 * 1024  # one maximal part, HTML too (operator decision 2026-10-10)
UNSCANNED_TEXT = "text over the message's scan limit"
UNSCANNED_IBANS = "too many IBAN-like numbers to check"


_START = r"(?<!\w)"  # every keyword pattern starts at a word start


@dataclass(frozen=True)
class _Pattern:
    word: str
    regex: re.Pattern[str]
    case_sensitive: bool


def keywords_digest() -> str:
    """SHA-256 of the shipped keywords.yaml, recorded beside a result's prompt digest (Q3)."""
    raw = resources.files("ecf_server.data").joinpath("keywords.yaml").read_bytes()
    return hashlib.sha256(raw).hexdigest()


@cache
def _patterns() -> dict[str, tuple[_Pattern, ...]]:
    text = resources.files("ecf_server.data").joinpath("keywords.yaml").read_text("utf-8")
    data: dict[str, Any] = load_yaml(text, source="keywords.yaml")
    out: dict[str, tuple[_Pattern, ...]] = {}
    for group in GROUPS:
        spec: dict[str, list[str]] = data[group]
        pats = [_compile(w, case_sensitive=True) for w in spec.get("acronyms") or []]
        pats += [_compile(w, case_sensitive=False) for w in spec.get("phrases") or []]
        out[group] = tuple(pats)
    return out


def pattern_count() -> int:
    """How many keyword patterns the scan compiles, a measured figure (pre-classifier plan §11)."""
    return sum(len(pats) for pats in _patterns().values())


def _compile(word: str, *, case_sensitive: bool) -> _Pattern:
    folded = keyword_fold(word) if case_sensitive else keyword_fold_ci(word)
    body = r"\s+".join(re.escape(part) for part in folded.split())
    return _Pattern(word, re.compile(rf"{_START}{body}(?!\w)"), case_sensitive)


# Alternations kept: one per remaining set of patterns. A message that hits every keyword makes
# one per hit, so the cache only needs to outlast one message.
ALTERNATION_CACHE = 256


def _alternation(pats: tuple[_Pattern, ...]) -> re.Pattern[str]:
    """One regex that matches where any of `pats` does: the shared lookbehind factored out, so
    the alternation is tried only at word starts (pre-classifier plan §4)."""
    return re.compile(_START + "(?:" + "|".join(p.regex.pattern[len(_START) :] for p in pats) + ")")


_any_of = cache(_alternation)  # a whole group's patterns: a fixed, small set
_rest_of = lru_cache(maxsize=ALTERNATION_CACHE)(_alternation)  # what a message left unfound


def _found_in(text: str, pats: tuple[_Pattern, ...]) -> set[_Pattern]:
    """The patterns in `pats` that match somewhere in `text`, as `p.regex.search` would find
    them. The alternation finds the leftmost place where any still-unfound pattern matches; every
    unfound pattern is tried there, and the search goes on past it with those left. So the text
    is read about once however many patterns there are, and a keyword repeated a million times
    costs one hit, not a million."""
    found: set[_Pattern] = set()
    rest: tuple[_Pattern, ...] = pats
    pos = 0
    while rest:
        m = _rest_of(rest).search(text, pos)
        if m is None:
            break
        pos = m.start()
        found.update(p for p in rest if p.regex.match(text, pos))
        rest = tuple(p for p in rest if p not in found)
        pos += 1
    return found


def _width(text: str) -> int:
    """Bytes per character as Python holds `text` (PEP 393): 1, 2 or 4."""
    if text.isascii():
        return 1
    top = ord(max(text))
    return 1 if top <= 0xFF else 2 if top <= 0xFFFF else 4


def _within_limit(texts: list[str]) -> tuple[list[str], bool]:
    """The distinct texts, in order, cut to SCAN_BYTES in all; True when something was cut."""
    out: list[str] = []
    left = SCAN_BYTES
    for t in dict.fromkeys(t for t in texts if t):
        width = _width(t)
        if len(t) * width > left:
            if left >= width:
                out.append(t[: left // width])
            return out, True
        out.append(t)
        left -= len(t) * width
    return out, False


def scan(texts: list[str]) -> dict[str, list[str]]:
    """Keywords found per group, in any of the texts. Each distinct text is read once (a plain
    part's visible text is its full text), normalized once for its three forms, and the texts
    are read up to SCAN_BYTES in all. Every group's patterns of one case are searched together,
    one search per text form, and one text's forms are held at a time (pre-gate review G6, G7);
    a keyword two groups list is one pattern, found for both. Besides the groups:
    `crypto_wallet`, `amount`, and `unscanned`, why the scan stopped short (a reason for
    `content_unscanned`)."""
    distinct, cut = _within_limit(texts)
    pats = _patterns()
    todo = {case: tuple(dict.fromkeys(p for ps in pats.values() for p in ps
                                      if p.case_sensitive is case))
            for case in (True, False)}  # fmt: skip
    found: set[_Pattern] = set()
    iban: set[bool | None] = set()
    wallet = money = False
    for t in distinct:
        cs, ci, entity = folds(t)
        for case, form in ((True, cs), (False, ci)):
            if todo[case]:
                found |= _found_in(form, todo[case])
                todo[case] = tuple(p for p in todo[case] if p not in found)
        del cs, ci  # one text's forms at a time
        iban.add(_iban(entity))
        wallet = wallet or bool(WALLET.search(entity))
        money = money or amount(entity)
    hits: dict[str, list[str]] = {g: [p.word for p in pats[g] if p in found] for g in GROUPS}
    if True in iban:
        hits["bank"].append(IBAN_HIT)
    hits["crypto_wallet"] = ["wallet address"] if wallet else []
    hits["amount"] = [AMOUNT_HIT] if money else []
    hits["unscanned"] = [r for r, on in ((UNSCANNED_TEXT, cut),
                                         (UNSCANNED_IBANS, None in iban)) if on]  # fmt: skip
    return hits


def amount(text: str) -> bool:
    """A money amount in `text` (`AMOUNTS`)."""
    return any(p.search(text) for p in AMOUNTS)


def valid_iban(text: str) -> bool:
    """A checksum-valid IBAN (ISO 13616 mod-97) in `text`, written whole or in groups split by
    single spaces or hyphens. It is cut at its country's length, and the cut must not fall inside
    a word."""
    return _iban(text) is True


def _iban(text: str) -> bool | None:
    """`valid_iban`, or None when IBAN_CANDIDATES candidates (or IBAN_ATTEMPTS full matches) were
    checked and none was valid."""
    checked = attempts = pos = 0
    # Every start is tried, not just those after the last candidate: a failed candidate can run
    # into a real IBAN ("GB00 WEST ... 32 DE89 ..."), which `finditer` would skip.
    while h := IBAN_HEAD.search(text, pos):
        pos = h.end()
        size = IBAN_LENGTHS.get(h.group()[:2])
        if size is None:
            continue
        attempts += 1
        if attempts > IBAN_ATTEMPTS:
            return None
        if (m := IBAN.match(text, h.start())) is None:
            continue
        g = m.group()
        code = g.replace(" ", "").replace("-", "")
        if len(code) < size:
            continue
        cut = size if len(code) == len(g) else _nth_character(g, size)
        end = m.start() + cut
        # ASCII, as the start's lookbehind is: an invisible Hangul filler (category Lo) after an
        # IBAN would otherwise hide it
        if end < len(text) and text[end].isascii() and text[end].isalnum():
            continue
        checked += 1  # only candidates that reach the checksum (a head inside a run isn't one)
        if checked > IBAN_CANDIDATES:
            return None
        code = code[:size]
        if int((code[4:] + code[:4]).translate(_IBAN_DIGITS)) % 97 == 1:
            return True
    return False


def _nth_character(group: str, n: int) -> int:
    """The index just past the n-th character of `group` that isn't a separator."""
    seen = 0
    for i, c in enumerate(group):
        if c not in " -":
            seen += 1
            if seen == n:
                return i + 1
    return len(group)


def bec_opener(keywords: dict[str, list[str]]) -> bool:
    """At least two different phrases of an executive-impersonation opener ("are you at your
    desk", "quick favour", "email only"; OD-479): one alone is ordinary mail."""
    return len(set(keywords.get("bec_opener", []))) >= BEC_OPENER_MIN


def tax_form_request(keywords: dict[str, list[str]]) -> bool:
    """Employee tax or payroll data ("W-2", "wage and tax statements", "pay stubs") and a request
    to send it to the writer ("send me", "as one PDF"; OD-482): the W-2 scam. A form alone is a
    payroll provider's notice."""
    return bool(keywords.get("tax_form")) and bool(keywords.get("data_request"))


def gift_card_codes_request(keywords: dict[str, list[str]]) -> bool:
    """Gift cards and an ask for their codes or a photo of them ("send me the codes", "photo of
    the back"; pre-classifier plan §3): the gift-card scam. Gift cards alone are a receipt or a
    balance notice."""
    return bool(keywords.get("gift_card")) and bool(keywords.get("gift_card_ask"))


def credential_request(keywords: dict[str, list[str]]) -> bool:
    """A code, password or login and an ask to send or read it back ("reply with the code",
    "reply with your current password"; OD-485), unless the text says such a request is never
    made ("we will never ask you to reply with your password"). A signal to the classifier only."""
    return (bool(keywords.get("credential")) and bool(keywords.get("credential_ask"))
            and not keywords.get("credential_never"))  # fmt: skip


def crypto_payment(keywords: dict[str, list[str]]) -> bool:
    """A cryptocurrency wallet address and a payment verb (OD-485), unless the text says such a
    request is never made ("we will never ask you to send funds"), as `credential_request`."""
    return (bool(keywords.get("crypto_wallet")) and bool(keywords.get("crypto_pay"))
            and not keywords.get("credential_never"))  # fmt: skip


INJECTION_MARK = "[text removed by ecf: text addressed to an automated reader]"
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")


# Paragraphs tested together before one by one (R3-P20): a text of many tiny paragraphs cost a
# test each (about 13 s per 10 MiB). Joined by a line holding only a NUL, which no keyword
# pattern can match across, a batch matches exactly when one of its paragraphs does.
INJECTION_BATCH = 256
_BATCH_JOIN = "\n\0\n"


@cache
def _injection() -> tuple[re.Pattern[str] | None, re.Pattern[str] | None]:
    """The injection alternations: case-sensitive (acronyms), then case-insensitive."""
    pats = _patterns()["injection"]
    cs = tuple(p for p in pats if p.case_sensitive)
    ci = tuple(p for p in pats if not p.case_sensitive)
    return (_any_of(cs) if cs else None), (_any_of(ci) if ci else None)


def _injected(text: str) -> bool:
    cs, ci = _injection()
    return bool((cs and cs.search(keyword_fold(text))) or (ci and ci.search(keyword_fold_ci(text))))


def _injected_paragraphs(paras: list[str]) -> set[int]:
    """The indexes of the paragraphs `_injected` holds for, testing them in batches first."""
    out: set[int] = set()
    for lo in range(0, len(paras), INJECTION_BATCH):
        batch = paras[lo : lo + INJECTION_BATCH]
        if len(batch) == 1 or _injected(_BATCH_JOIN.join(batch)):
            out.update(lo + i for i, para in enumerate(batch) if _injected(para))
    return out


def _first(hi: int, test: Callable[[int], bool]) -> int:
    """The least i in [0, hi] with test(i), for a test that stays True once True and is True at
    hi."""
    lo = 0
    while lo < hi:
        mid = (lo + hi) // 2
        if test(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


def _match_start(lines: list[str]) -> int:
    """The line where the paragraph's first injection match starts. Binary searches, not scans:
    a match in a prefix or suffix stays a match when lines are added, so each bound is the first
    True of a monotone test, and a crafted paragraph of many lines costs O(n log n) instead of
    O(n^2) (R150)."""
    end = _first(len(lines) - 1, lambda k: _injected("\n".join(lines[: k + 1])))
    return end - _first(end, lambda d: _injected("\n".join(lines[end - d : end + 1])))


# In a longer paragraph, only this head is searched for the line where the match starts; a match
# that starts later removes the paragraph from the end of the head (more, never less). A crafted
# 10 MiB paragraph took over a minute to search whole (R180).
SEARCH_CHARS = 65_536


def _head(lines: list[str]) -> list[str]:
    """The paragraph's leading lines up to SEARCH_CHARS characters (at least one line)."""
    size = 0
    for k, line in enumerate(lines):
        size += len(line) + 1
        if size > SEARCH_CHARS:
            return lines[: max(k, 1)]
    return lines


def redact_injection(text: str) -> str:
    """Model input without what fraud trigger 10 matched (OD-254): in each paragraph where a
    phrase matched, the line where the first match starts and every line after it in that
    paragraph are replaced by INJECTION_MARK, so the model never reads the instruction and keeps
    the text before it. A phrase split across a blank line still fires the trigger but isn't
    removed."""
    if not _injected(text):  # the whole text first: most mail has no match (R3-P20)
        return text
    paras = _PARAGRAPH_BREAK.split(text)
    injected = _injected_paragraphs(paras)
    out: list[str] = []
    removed = False
    for i, para in enumerate(paras):
        if i not in injected:
            out.append(para)
            continue
        removed = True
        lines = para.split("\n")
        head = _head(lines)
        if head is lines:
            start = _match_start(lines)
        else:
            start = _match_start(head) if _injected("\n".join(head)) else len(head)
        kept = "\n".join(lines[:start]).rstrip()
        if kept:
            out.append(f"{kept}\n{INJECTION_MARK}")
        elif not out or not out[-1].endswith(INJECTION_MARK):
            out.append(INJECTION_MARK)
    return "\n\n".join(out) if removed else text


def texts_of(parsed: ParsedMessage) -> list[str]:
    """Everything triggers look at: Subject, display name, filenames, then every part's visible
    text, then every part's full text, the order `scan` reads them in up to its limit."""
    out = [parsed.subject, parsed.from_name, *(a.name for a in parsed.attachments)]
    return out + [t.visible for t in parsed.texts] + [t.full for t in parsed.texts]


@dataclass
class Triggers:
    keywords: dict[str, list[str]]
    fraud: list[str] = field(default_factory=list[str])
    fraud_weak: list[str] = field(default_factory=list[str])
    regulator: list[str] = field(default_factory=list[str])
    unverified_payment: bool = False
    lookalikes: list[str] = field(default_factory=list[str])
    impersonation: list[str] = field(default_factory=list[str])

    def facts(self) -> dict[str, Any]:
        return {
            "keywords": self.keywords,
            "payment_keyword": bool(self.keywords["payment"]),
            "gift_card_keyword": bool(self.keywords["gift_card"]),
            "gift_card_codes_request": gift_card_codes_request(self.keywords),
            "bec_opener": bec_opener(self.keywords),
            "tax_form_request": tax_form_request(self.keywords),
            "credential_request": credential_request(self.keywords),
            "crypto_payment": crypto_payment(self.keywords),
            # recorded for measurement only (pre-classifier plan §5 A2, P9)
            "amount_mentioned": bool(self.keywords.get("amount")),
            "impersonates_internal": bool(self.impersonation),
            "triggers": {
                "fraud": self.fraud,
                "fraud_weak": self.fraud_weak,
                "regulator": self.regulator,
                "unverified_payment": self.unverified_payment,
                "lookalikes": self.lookalikes,
                "impersonation": self.impersonation,
            },
        }


def evaluate(  # noqa: PLR0913 - the message, its facts and the install's sets
    parsed: ParsedMessage,
    keywords: dict[str, list[str]],
    found: dict[str, Any],
    *,
    org_domains: list[str],
    known_vendors: list[str],
    duplicate_message_id: bool,
    org_addresses: tuple[internal.OrgAddress, ...] = (),
    watched_providers: tuple[str, ...] = (),
    public_domains: frozenset[str] = PUBLIC_DOMAINS,
) -> Triggers:
    """`found` holds the auth and computed facts for this message (senderauth, facts).
    `watched_providers` are the public provider domains this install watches an address at: they
    are lookalike targets too (OD-434). `public_domains` is `PUBLIC_DOMAINS` except in the eval
    scratch, which adds its stand-in `freemail.example` (OD-443)."""
    t = Triggers(keywords)
    payment = bool(keywords["payment"])
    from_domain: str | None = found["from_domain"]
    candidates = [d for d in [from_domain, *(domain_of(r) for r in parsed.reply_to)] if d]
    candidates += _display_domains(parsed.from_name)
    t.lookalikes = sorted(
        {
            f"{d} looks like {k}"
            for d in candidates
            for k in [*org_domains, *known_vendors, *watched_providers]
            if lookalike(d, k)
            and not (k in org_domains and saas_tenant(d, k))
            and not (d in public_domains and k in public_domains)  # OD-430
        }
    )

    t.fraud, t.fraud_weak = _money_triggers(keywords, found, bool(t.lookalikes))
    t.fraud += _other_triggers(parsed, found, t.lookalikes, duplicate_message_id, payment)
    t.impersonation = impersonation(parsed, found, org_domains, org_addresses, public_domains)
    money = payment or bool(keywords["gift_card"])  # gift cards: money only here (OD-457)
    (t.fraud if money else t.fraud_weak).extend(t.impersonation)  # module docstring
    if (
        found.get("risky_attachment")
        and not found.get("self_sent")
        and any(keywords.get(g) for g in ("payment", "credential", "gift_card"))
    ):
        t.fraud_weak.append(RISKY_ATTACHMENT)  # rule 1b (pre-classifier plan §5 C, P13)
    if keywords.get("injection"):  # 10 (OD-252)
        t.fraud.append(f'text addressed to an automated reader: "{keywords["injection"][0]}"')

    t.regulator = list(keywords["regulator"])
    t.unverified_payment = _unverified_payment(payment, found)
    return t


def _unverified_payment(payment: bool, found: dict[str, Any]) -> bool:
    auth = found["auth_result"]
    unsigned_mime = auth == "pass" and found["auth"].get("mime_headers_signed") is False
    verified = bool(found.get("sender_verified"))  # `ecf sender set-verified` (OD-065)
    return (payment and (auth == "none" or unsigned_mime) and not verified
            and not found.get("self_sent"))  # OD-446  # fmt: skip


def _money_triggers(
    keywords: dict[str, list[str]], found: dict[str, Any], lookalikes: bool
) -> tuple[list[str], list[str]]:
    """Fraud triggers 1 and 2, and the weak cases (OD-060, OD-061, OD-062, OD-068)."""
    fraud: list[str] = []
    weak: list[str] = []
    bank, change, payment = (bool(keywords[g]) for g in ("bank", "change", "payment"))
    rtm = found["reply_to_mismatch"]
    # 1. bank details with an unconfirmed sender (OD-044), change wording or a Reply-To mismatch
    held = [
        what
        for what, hit in (
            ("a sender you haven't confirmed", not found["sender_confirmed"]),
            ("change wording", change),
            ("a Reply-To mismatch", rtm),
        )
        if hit
    ]  # only what held, so the card says why (V1.2 shadow run, 2026-09-30)
    if bank and held:
        fraud.append(f"bank details with {' and '.join(held)}")
    second = second_signals(keywords, found, lookalikes)
    # 2. a first-time sender with a payment keyword needs a second signal; alone it is weak
    if payment and not found["sender_seen_before"]:
        if second:
            fraud.append(f"first-time sender with a payment keyword and {' and '.join(second)}")
        else:
            weak.append("first-time sender with a payment keyword")
    # a known sender: Reply-To mismatch on a payment item is a second signal only (OD-068)
    elif payment and rtm:
        others = [name for name in second if name != "Reply-To mismatch"]
        if others:
            fraud.append(f"Reply-To mismatch on a payment item and {' and '.join(others)}")
        else:
            weak.append("Reply-To mismatch on a payment item")
    return fraud, weak


def second_signals(
    keywords: dict[str, list[str]], found: dict[str, Any], lookalikes: bool
) -> list[str]:
    """Trigger 2's second signals (OD-062), shared with the routes (OD-490)."""
    auth = found["auth_result"]
    signals = (
        ("bank or change wording", bool(keywords.get("bank") or keywords.get("change"))),
        ("DMARC fail", auth == "fail"),
        # a replayed signed message: only then (OD-201); mail to an alias or list is ordinary
        (NOT_ADDRESSED, bool(found["recipient_mismatch"]) and auth == "pass"),
        ("lookalike domain", lookalikes),
        ("Reply-To mismatch", bool(found["reply_to_mismatch"])),
    )
    return [name for name, hit in signals if hit]


# Routes (OD-490, pre-classifier plan step 4): which fraud route applies to a message and
# its second signals, giving the route reasons.

NO_HISTORY = "a sender with no established history"
UNAUTHENTICATED_KNOWN = "a known sender without authentication"
OTHER_FRAUD = "another fraud signal"
INTERNAL = "an internal sender"
IMPERSONATION = "impersonation of someone in your organization"
CREDENTIAL = "a request for a code or password"
GIFT_CODES = "a request for gift-card codes"
CRYPTO = "a wallet address with a payment request"
AUTH_REGRESSED = "a known sender's mail failed DMARC"
RISKY_ATTACHMENT = "a web page, script or program attached to a payment, code or gift-card item"
NOT_ADDRESSED = "not addressed to this mailbox"  # trigger 2's signal; not the routes' (G2)
ROUTE_WHATS = (CREDENTIAL, GIFT_CODES, CRYPTO)
# a route reason whose only second signal is a sender with no history: it doesn't stop the
# sender's history from forming, or a genuine sender would stay unknown (pre-gate review G1)
HISTORY_ONLY = frozenset(f"{what} and {NO_HISTORY}" for what in ROUTE_WHATS)


@dataclass(frozen=True)
class RouteHit:
    """One route that applies to a message (OD-490): what it is, the second signals that hold
    for it (none: weak, or nothing), and whether it is weak without one."""

    what: str
    signals: tuple[str, ...]
    weak_too: bool


def route_hits(
    found: dict[str, Any], *, public_domains: frozenset[str] = PUBLIC_DOMAINS
) -> list[RouteHit]:
    """The routes that apply to a message, each with its second signals (`routes` turns them
    into reasons; the eval's route report reads them, pre-classifier plan step 7)."""
    if found.get("self_sent"):
        return []
    kw: dict[str, list[str]] = found.get("keywords") or {}
    t: dict[str, Any] = found.get("triggers") or {}
    auth = found.get("auth_result")
    known = bool(found.get("sender_confirmed")) or (
        bool(found.get("sender_seen_before")) and found.get("from_domain") not in public_domains
    )
    # alias and list mail is ordinary for these requests: not their signal (pre-gate review G2)
    shared = [s for s in (second_signals(kw, found, bool(t.get("lookalikes"))) if kw else [])
              if s != NOT_ADDRESSED]  # fmt: skip
    shared += [name for name, hit in (
        (OTHER_FRAUD, bool(t.get("fraud"))),
        (NO_HISTORY, not known),
        (UNAUTHENTICATED_KNOWN, known and auth == "none" and not found.get("bulk_signal")),
    ) if hit]  # fmt: skip
    posing = [IMPERSONATION] if found.get("impersonates_internal") else []
    inside = [INTERNAL] if found.get("from_org_domain") or found.get("from_org_address") else []
    hits: list[RouteHit] = []
    if kw.get("credential") and kw.get("credential_ask"):  # the veto only without a signal
        hits.append(RouteHit(CREDENTIAL, (*shared, *posing), not kw.get("credential_never")))
    if gift_card_codes_request(kw):
        hits.append(RouteHit(GIFT_CODES, (*shared, *inside), True))
    if kw.get("crypto_wallet") and kw.get("crypto_pay"):
        hits.append(RouteHit(CRYPTO, (*shared, *posing), False))
    return hits


def routes(
    found: dict[str, Any], *, public_domains: frozenset[str] = PUBLIC_DOMAINS
) -> tuple[list[str], list[str]]:
    """Fraud and weak fraud reasons for credential requests, gift-card code requests and crypto
    payment demands, and for a known sender whose mail fails DMARC (OD-490). Pure, over the
    facts after they are computed (and, in the eval, after a card's expected facts are laid
    over), with `found["triggers"]` holding the other triggers' reasons only.

    A route fires as a fraud reason with a second signal: trigger 2's (`second_signals`), any
    other fraud reason, a sender with no established history, a known sender whose mail isn't
    authenticated (bulk mail excepted); impersonation for credentials and crypto, an internal
    sender for gift cards (a compromised colleague's account; impersonation with gift cards
    already escalates). Without one: weak for credentials (unless the text says such a request
    is never made) and gift cards, nothing for crypto (genuine deposit notices). A public
    provider's sender is never "known" here unless confirmed. Never on the account's own mail
    to itself. Reasons use fixed wording, never a value from the message."""
    if found.get("self_sent"):
        return [], []
    fraud: list[str] = []
    weak: list[str] = []
    for h in route_hits(found, public_domains=public_domains):
        if h.signals:
            fraud.append(f"{h.what} and {' and '.join(h.signals)}")
        elif h.weak_too:
            weak.append(f"{h.what} from a known sender, with no second signal")
    t: dict[str, Any] = found.get("triggers") or {}
    other: list[str] = t.get("fraud") or []
    if found.get("known_sender_auth_regressed") and not any(
        "DMARC fail" in r for r in (*fraud, *other)
    ):
        fraud.append(AUTH_REGRESSED)  # one reason, not two (R3-P21, pre-gate review G11)
    return fraud, weak


def with_routes(
    found: dict[str, Any], *, public_domains: frozenset[str] = PUBLIC_DOMAINS
) -> dict[str, Any]:
    """`found` with its route reasons (re)computed: any earlier ones (`found["routes"]`, and any
    reason worded as a route's, so stored facts from an earlier wording don't count as another
    fraud signal; pre-gate review G10) are taken out of the triggers first, so the eval can
    route again after laying a card's expected facts over."""
    prior: dict[str, list[str]] = found.get("routes") or {}
    t: dict[str, Any] = dict(found.get("triggers") or {})
    for k in ("fraud", "fraud_weak"):
        t[k] = [r for r in t.get(k, []) if r not in prior.get(k, []) and not is_route_reason(r)]
    fraud, weak = routes(found | {"triggers": t}, public_domains=public_domains)
    t["fraud"] = t["fraud"] + fraud
    t["fraud_weak"] = t["fraud_weak"] + weak
    return found | {"triggers": t, "routes": {"fraud": fraud, "fraud_weak": weak}}


def is_route_reason(reason: str) -> bool:
    """A reason `routes` words: a route's, or the known-sender DMARC one."""
    return reason.startswith(ROUTE_WHATS) or reason == AUTH_REGRESSED


def _other_triggers(
    parsed: ParsedMessage,
    found: dict[str, Any],
    lookalikes: list[str],
    duplicate: bool,
    payment: bool,
) -> list[str]:
    """Fraud triggers 3 to 9."""
    display = _display_mismatch(parsed.from_name, found["from_domain"])
    dmarc_fail, org_domain, org_address = _auth_checks(parsed.from_addr, found, payment)
    checks = (
        (bool(lookalikes), f"{LOOKALIKE}{lookalikes[0]}" if lookalikes else ""),  # 3
        dmarc_fail,  # 4
        (duplicate, "Message-ID reused with different content"),  # 5
        org_domain,  # 6
        org_address,  # 6 for a listed address
        (display is not None, f"display name shows {display}"),  # 7
        (parsed.from_count > 1, "more than one From header"),  # 8
        (
            parsed.from_ambiguous and parsed.from_count == 1,
            "ambiguous From header: parsers may disagree on the sender",
        ),  # 8
        (parsed.headers_ambiguous, "bare CR in the headers: parsers may disagree on them"),  # 8
        # 9: not on ecf's own mail or another install's from this mailbox, nor on a copy of
        # one of this install's alert emails (own_mail; V1.5, OD-338)
        (
            bool(parsed.headers.get("x-ecf-install"))
            and not found.get("ecf_mail")
            and found.get("alert_echo") != "copy",
            "carries an X-ECF-Install header",
        ),
    )
    return [reason for hit, reason in checks if hit]


# The reasons that read the sender's authentication or history, worded so the eval can find
# them again: it recomputes them after laying a card's expected facts over (`with_money`).
LOOKALIKE = "lookalike domain: "
MONEY_REASONS = ("bank details with ", "first-time sender with a payment keyword",
                 "Reply-To mismatch on a payment item")  # fmt: skip
DMARC_FAIL_PAYMENT = "DMARC fail on a payment item"
ORG_DOMAIN_UNAUTH = "From uses your organization's domain but isn't authenticated"
ORG_ADDRESS_UNAUTH = ", one of your org addresses, but isn't authenticated"


def _auth_checks(
    from_addr: str | None, found: dict[str, Any], payment: bool
) -> tuple[tuple[bool, str], tuple[bool, str], tuple[bool, str]]:
    """Fraud triggers 4, 6 and 6b, each as (fired, reason)."""
    auth = found["auth_result"]
    return (
        (auth == "fail" and payment, DMARC_FAIL_PAYMENT),
        (bool(found["from_org_domain"]) and auth != "pass", ORG_DOMAIN_UNAUTH),
        (  # not for the account's own note to itself (OD-446)
            not found["from_org_domain"]
            and bool(found.get("from_org_address"))
            and auth != "pass"
            and not found.get("self_sent"),
            f"From is {from_addr}{ORG_ADDRESS_UNAUTH}",
        ),
    )


def _authish(reason: str) -> bool:
    return (reason.startswith(MONEY_REASONS) or reason in (DMARC_FAIL_PAYMENT, ORG_DOMAIN_UNAUTH)
            or (reason.startswith("From is ") and reason.endswith(ORG_ADDRESS_UNAUTH)))  # fmt: skip


def with_money(found: dict[str, Any], *, from_addr: str | None) -> dict[str, Any]:
    """`found` with fraud triggers 1, 2, 4, 6 and 6b and `unverified_payment` computed again from
    its facts, for the eval after a card's expected facts are laid over (M8 plan R2): they read
    `auth_result` and the sender's history, which `evaluate` saw before the card's. The earlier
    reasons are taken out by their fixed wording first, and the new ones go where `evaluate`
    puts them; a message whose reasons come out the same is returned unchanged. Run it before
    `with_routes`, which reads the fraud reasons as "another fraud signal"."""
    t: dict[str, Any] = dict(found.get("triggers") or {})
    keywords: dict[str, list[str]] | None = t.get("keywords") or found.get("keywords")
    if keywords is None:
        return found
    payment = bool(keywords["payment"])
    money, money_weak = _money_triggers(keywords, found, bool(t.get("lookalikes")))
    auth = [reason for hit, reason in _auth_checks(from_addr, found, payment) if hit]
    before: list[str] = t.get("fraud") or []
    before_weak: list[str] = t.get("fraud_weak") or []
    other = [r for r in before if not _authish(r)]
    lead = 1 if other and other[0].startswith(LOOKALIKE) else 0  # 3 comes before 4
    fraud = money + other[:lead] + auth + other[lead:]
    weak = money_weak + [r for r in before_weak if not _authish(r)]
    unverified = _unverified_payment(payment, found)
    if (fraud, weak, unverified) == (t.get("fraud"), t.get("fraud_weak"),
                                     t.get("unverified_payment")):  # fmt: skip
        return found
    return found | {"triggers": t | {"fraud": fraud, "fraud_weak": weak,
                                     "unverified_payment": unverified}}  # fmt: skip


# Impersonation (trigger 7, V1.6): a sender claiming to be someone in the internal set
# without being in it.


def impersonation(
    parsed: ParsedMessage,
    found: dict[str, Any],
    org_domains: list[str],
    entries: tuple[internal.OrgAddress, ...],
    public_domains: frozenset[str] = PUBLIC_DOMAINS,
) -> list[str]:
    """Why the sender claims to be someone in the internal set without being in it (module
    docstring); empty when it doesn't, or when the sender is internal."""
    sender = parsed.from_addr
    if not sender or found.get("from_org_domain") or found.get("from_org_address"):
        return []
    out: list[str] = []
    words = set(internal.name_words(parsed.from_name))
    for e in entries:  # 1. a listed name, every word in any order (OD-433)
        need = internal.name_words(e.name or "")
        if len(need) >= 2 and words.issuperset(need):  # names have at least 2 words
            out.append(f"display name matches {e.name} ({e.address}), one of your org"
                       f" addresses, but the sender is {sender}")  # fmt: skip
            break
    shown = _display_addresses(parsed.from_name)  # 2. an internal address in the display name
    inside = [a for a in shown if internal.listed(a, entries)
              or any(domain_of(a) == d or str(domain_of(a)).endswith("." + d)
                     for d in org_domains)]  # fmt: skip
    if inside:
        out.append(f"display name shows {inside[0]}, an internal address, but the sender is"
                   f" {sender}")  # fmt: skip
    near = _near_address(sender, entries, public_domains)  # 3 and 4
    if near:
        out.append(f"{sender} looks like {near}, one of your org addresses")
    return out


def _near_address(sender: str, entries: tuple[internal.OrgAddress, ...],
                  public_domains: frozenset[str]) -> str | None:  # fmt: skip
    """A listed address the sender imitates: a one-edit or same-skeleton Gmail typo (OD-432), or
    the same local part at another public provider (OD-448); local parts of 5 or more."""
    local, domain = internal.bare_local(sender), internal.split(internal.fold(sender))[1]
    if domain not in public_domains or len(local) < internal.MIN_LOCAL:
        return None
    for e in entries:
        e_local = internal.bare_local(e.address)
        e_domain = internal.split(internal.fold(e.address))[1]
        if len(e_local) < internal.MIN_LOCAL:
            continue
        if e_domain != domain and local == e_local:
            return e.address
        typo = 0 < _edits(local, e_local) <= 1 or (
            local != e_local and fold_ci(local) == fold_ci(e_local))  # fmt: skip
        if domain == e_domain == "gmail.com" and typo:
            return e.address
    return None


def _display_addresses(name: str) -> list[str]:
    text = normalize(name).casefold()
    return sorted({m.group(0) for m in _ADDRESS.finditer(text)})


# Domains: lookalikes, SaaS tenants and domains written in a display name.


def lookalike(domain: str, known: str) -> bool:
    """True when `domain` imitates `known` without being it, a subdomain, a parent or a sibling
    under the same parent (a vendor's `em.` and `billing.` senders): the same skeleton
    (homoglyphs, `rn` for `m`, punycode decoded first), the same name under another top-level
    domain, a one-edit typo of a long enough name, or the known domain used as a subdomain
    elsewhere (V1.1 review, 2026-09-29)."""
    d, k = _unicode(domain), _unicode(known)
    if d == k or d.endswith("." + k) or k.endswith("." + d):
        return False
    if fold_ci(d) == fold_ci(k):
        return True
    d_labels, k_labels = d.split("."), k.split(".")
    if len(d_labels) < 2 or len(k_labels) < 2:
        return False
    # the labels just left of the part both share: `vendor` in acme vs vendor under .com,
    # `em` and `pay` for siblings under vendor.com (siblings only count as a one-edit typo)
    common = _common_suffix(d_labels, k_labels)
    left = max(common, 1) + 1  # never the top-level label itself
    d_name, k_name = fold_ci(d_labels[-left]), fold_ci(k_labels[-left])
    if common == 0 and d_name == k_name:  # acme.co vs acme.com; another registered domain
        return True
    if len(k_name) >= TYPO_MIN and 0 < _edits(d_name, k_name) <= 1:
        return True
    sub = d_labels[:-2]
    return k_labels[-2] in sub or _contains(sub, k_labels)


def saas_tenant(domain: str, known: str) -> bool:
    """`acme.zendesk.com` for org domain `acme.com`: a per-customer address on a shared service,
    not a lookalike (operator decision 2026-09-29, OD-203)."""
    d, k = _unicode(domain), _unicode(known)
    name = k.split(".")[0]
    return any(d == f"{name}.{p}" or d.endswith(f".{name}.{p}") for p in SAAS_TENANT_PARENTS)


def _unicode(domain: str) -> str:
    """Lowercase, trailing dot removed, punycode (`xn--`) labels decoded, so homoglyphs in
    A-label form are compared like the Unicode form (V1.1 review, 2026-09-29)."""
    return ".".join(_u_label(x) for x in domain.lower().rstrip(".").split("."))


def _u_label(label: str) -> str:
    if not label.startswith("xn--"):
        return label
    try:
        return label.encode("ascii").decode("idna")
    except UnicodeError:
        return label


def _common_suffix(a: list[str], b: list[str]) -> int:
    n = 0
    while n < min(len(a), len(b)) - 1 and a[-1 - n] == b[-1 - n]:
        n += 1
    return n


def _contains(labels: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return any(labels[i : i + n] == needle for i in range(len(labels) - n + 1))


def _edits(a: str, b: str) -> int:
    """Optimal string alignment distance (insertions, deletions, substitutions, swaps)."""
    rows = [list(range(len(b) + 1))]
    for i, ca in enumerate(a, 1):
        row = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = int(ca != cb)
            row[j] = min(rows[-1][j] + 1, row[j - 1] + 1, rows[-1][j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                row[j] = min(row[j], rows[-2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


def _display_domains(name: str) -> list[str]:
    """Domains and address domains written in a display name. Extracted from normalized text,
    not the skeleton, which would turn `m` into `rn`; lookalike() compares skeletons later."""
    text = normalize(name).casefold()
    found = {m.group(1) for m in _ADDRESS.finditer(text)}
    found |= {m.group(1) for m in _DOMAIN.finditer(text)}
    return sorted(found)


def _display_mismatch(name: str, from_domain: str | None) -> str | None:
    """The first address domain, or domain name ending in a common top-level domain, in the
    display name that isn't the From domain, a subdomain or a parent of it (trigger 7). Parents
    count ("Booking.com" from mailer.booking.com), and product names like "Node.js" don't
    (V1.1 review, 2026-09-29)."""
    text = normalize(name).casefold()
    addresses = {m.group(1) for m in _ADDRESS.finditer(text)}
    bare = {m.group(1) for m in _DOMAIN.finditer(text)}
    bare = {d for d in bare if d.rsplit(".", 1)[-1] in DISPLAY_TLDS}
    for d in sorted(addresses | bare):
        if from_domain is None:
            return d
        f = from_domain.lower()
        if not (d == f or d.endswith("." + f) or f.endswith("." + d)):
            return d
    return None
