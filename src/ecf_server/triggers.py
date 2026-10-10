"""Deterministic triggers (SPEC §8.5): computed by the service over every decoded text part
(visible and full text), the Subject, the display name and attachment names; no model involved.

Keywords come from data/keywords.yaml and match whole words after folding (skeleton.py), so
lookalike letters, zero-width characters and full-width forms don't hide them. Results:

- `fraud`: fraud triggers 1-9 that fired, as plain-language reasons (rule 1, the fraud guard).
- `fraud_weak`: a first-time sender with a payment keyword and no second signal (OD-062), or a
  Reply-To mismatch on a payment item with no other signal (OD-068) (rule 1b).
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
from ecf_server.message import ParsedMessage
from ecf_server.skeleton import entity_text, fold_ci, keyword_fold, keyword_fold_ci, normalize

GROUPS = ("bank", "change", "payment", "gift_card", "gift_card_ask", "bec_opener", "tax_form",
          "data_request", "credential", "credential_ask", "credential_never", "crypto_pay",
          "regulator", "injection")  # fmt: skip
# Cryptocurrency wallet addresses (OD-485), matched on `entity_text`, not the folded text (the
# skeleton would change their letters), so a zero-width character or a mark inside one doesn't
# hide it: Bitcoin (bech32, legacy), Ethereum, and the synthetic set's placeholder
# (`TEST-WALLET-0001`; real shapes fail its hygiene scan). A hit is recorded as "wallet address",
# never the address.
WALLETS = (
    re.compile(r"(?<![\w-])bc1[ac-hj-np-z02-9]{11,71}(?![\w-])"),
    re.compile(r"(?<![\w-])[13][a-km-zA-HJ-NP-Z1-9]{25,34}(?![\w-])"),
    re.compile(r"(?<![\w-])0x[0-9a-fA-F]{40}(?![\w-])"),
    re.compile(r"(?<![\w-])TEST-WALLET-\d{4}(?![\w-])"),
)
# IBANs (pre-classifier plan §3): country code, check digits, then letters and digits, each
# optionally after one space or hyphen; bounded, and never starting inside a word. Matched on
# `entity_text`; a hit is recorded as "valid IBAN", never the number.
IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}[0-9]{2}(?:[ -]?[A-Z0-9]){11,30}")
IBAN_HIT = "valid IBAN"
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
DISPLAY_TLDS = frozenset(
    {"com", "net", "org", "co", "io", "us", "uk", "de", "fr", "ca", "au", "nl", "es", "it",
     "info", "biz", "gov", "edu", "app", "dev", "ai", "me", "online", "site", "xyz"}
)  # fmt: skip
_DOMAIN = re.compile(r"(?<![\w@.-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})(?![\w-])")
_ADDRESS = re.compile(r"[\w.+-]+@((?:[a-z0-9-]+\.)+[a-z]{2,})")


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


def scan(texts: list[str]) -> dict[str, list[str]]:
    """Keywords found per group, in any of the texts. Each distinct text is read once (a plain
    part's visible text is its full text)."""
    distinct = list(dict.fromkeys(t for t in texts if t))
    folded = [(keyword_fold(t), keyword_fold_ci(t)) for t in distinct]
    hits: dict[str, list[str]] = {g: [] for g in GROUPS}
    for group, pats in _patterns().items():
        found: set[_Pattern] = set()
        for case_sensitive in (True, False):
            todo = tuple(p for p in pats if p.case_sensitive is case_sensitive)
            for cs, ci in folded:
                if todo:
                    found |= _found_in(cs if case_sensitive else ci, todo)
                    todo = tuple(p for p in todo if p not in found)
        hits[group] = [p.word for p in pats if p in found]
    entities = [entity_text(t) for t in distinct]
    if any(valid_iban(t) for t in entities):
        hits["bank"].append(IBAN_HIT)
    hits["crypto_wallet"] = ["wallet address"] if any(w.search(t) for w in WALLETS
                                                      for t in entities) else []  # fmt: skip
    return hits


def valid_iban(text: str) -> bool:
    """A checksum-valid IBAN (ISO 13616 mod-97) in `text`, written whole or in groups split by
    single spaces or hyphens. It is cut at its country's length, and the cut must not fall inside
    a word."""
    for m in IBAN.finditer(text):
        size = IBAN_LENGTHS.get(m.group()[:2])
        where = [i for i, c in enumerate(m.group()) if c not in " -"]
        if size is None or len(where) < size:
            continue
        end = m.start() + where[size - 1] + 1
        if end < len(text) and text[end].isalnum():
            continue
        code = "".join(m.group()[i] for i in where[:size])
        if int((code[4:] + code[:4]).translate(_IBAN_DIGITS)) % 97 == 1:
            return True
    return False


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
    """A cryptocurrency wallet address and a payment verb (OD-485). A signal only."""
    return bool(keywords.get("crypto_wallet")) and bool(keywords.get("crypto_pay"))


INJECTION_MARK = "[text removed by ecf: text addressed to an automated reader]"
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")


def _injected(text: str) -> bool:
    pats = _patterns()["injection"]
    return any(
        _any_of(group).search(keyword_fold(text) if case_sensitive else keyword_fold_ci(text))
        for case_sensitive in (True, False)
        if (group := tuple(p for p in pats if p.case_sensitive is case_sensitive))
    )


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
    out: list[str] = []
    removed = False
    for para in _PARAGRAPH_BREAK.split(text):
        if not _injected(para):
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
    """Everything triggers look at: both texts of every part, Subject, display name, filenames."""
    out = [parsed.subject, parsed.from_name]
    for t in parsed.texts:
        out += [t.visible, t.full]
    out += [a.name for a in parsed.attachments]
    return out


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
    auth = found["auth_result"]
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
    if keywords.get("injection"):  # 10 (OD-252)
        t.fraud.append(f'text addressed to an automated reader: "{keywords["injection"][0]}"')

    t.regulator = list(keywords["regulator"])
    unsigned_mime = auth == "pass" and found["auth"].get("mime_headers_signed") is False
    verified = bool(found.get("sender_verified"))  # `ecf sender set-verified` (OD-065)
    t.unverified_payment = (payment and (auth == "none" or unsigned_mime) and not verified
                            and not found.get("self_sent"))  # OD-446  # fmt: skip
    return t


def _money_triggers(
    keywords: dict[str, list[str]], found: dict[str, Any], lookalikes: bool
) -> tuple[list[str], list[str]]:
    """Fraud triggers 1 and 2, and the weak cases (OD-060, OD-061, OD-062, OD-068)."""
    fraud: list[str] = []
    weak: list[str] = []
    bank, change, payment = (bool(keywords[g]) for g in ("bank", "change", "payment"))
    auth, rtm = found["auth_result"], found["reply_to_mismatch"]
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
    signals = (
        ("bank or change wording", bank or change),
        ("DMARC fail", auth == "fail"),
        # a replayed signed message: only then (OD-201); mail to an alias or list is ordinary
        ("not addressed to this mailbox", found["recipient_mismatch"] and auth == "pass"),
        ("lookalike domain", lookalikes),
        ("Reply-To mismatch", rtm),
    )
    second = [name for name, hit in signals if hit]
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


def _other_triggers(
    parsed: ParsedMessage,
    found: dict[str, Any],
    lookalikes: list[str],
    duplicate: bool,
    payment: bool,
) -> list[str]:
    """Fraud triggers 3 to 9."""
    auth = found["auth_result"]
    display = _display_mismatch(parsed.from_name, found["from_domain"])
    checks = (
        (bool(lookalikes), f"lookalike domain: {lookalikes[0]}" if lookalikes else ""),  # 3
        (auth == "fail" and payment, "DMARC fail on a payment item"),  # 4
        (duplicate, "Message-ID reused with different content"),  # 5
        (
            found["from_org_domain"] and auth != "pass",
            "From uses your organization's domain but isn't authenticated",
        ),  # 6
        (  # 6 for a listed address; not for the account's own note to itself (OD-446)
            not found["from_org_domain"]
            and found.get("from_org_address")
            and auth != "pass"
            and not found.get("self_sent"),
            f"From is {parsed.from_addr}, one of your org addresses, but isn't authenticated",
        ),
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


# ---- impersonation (trigger 7, V1.6) --------------------------------------------------------


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


# ---- domains --------------------------------------------------------------------------------


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
