"""Deterministic triggers (SPEC §8.5): computed by the service over every decoded text part
(visible and full text), the Subject, the display name and attachment names; no model involved.

Keywords come from data/keywords.yaml and match whole words after folding (skeleton.py), so
lookalike letters, zero-width characters and full-width forms don't hide them. Results:

- `fraud`: fraud triggers 1-9 that fired, as plain-language reasons (rule 1, the fraud guard).
- `fraud_weak`: a first-time sender with a payment keyword and no second signal (OD-062), or a
  Reply-To mismatch on a payment item with no other signal (OD-068) (rule 1b).
- `regulator`: regulator keywords found (rule 2).
- `unverified_payment`: a payment keyword and `auth_result = none`, counting a pass whose MIME
  headers were unsigned as none (OD-187) (rule 1a).

Not in V1.1: staff-name matching in display names (no staff list is configured yet), the
second-install exception for `X-ECF-Install` and loop suppression (both need V1.5's `sent` table).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any

from ecf.yamlio import load_yaml
from ecf_server.facts import domain_of
from ecf_server.message import ParsedMessage
from ecf_server.skeleton import fold, fold_ci, normalize

GROUPS = ("bank", "change", "payment", "regulator")
TYPO_MIN = 5  # a one-edit typo only counts for names of at least this many letters
_DOMAIN = re.compile(r"(?<![\w@.-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})(?![\w-])")
_ADDRESS = re.compile(r"[\w.+-]+@((?:[a-z0-9-]+\.)+[a-z]{2,})")


@dataclass(frozen=True)
class _Pattern:
    word: str
    regex: re.Pattern[str]
    case_sensitive: bool


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
    folded = fold(word) if case_sensitive else fold_ci(word)
    body = r"\s+".join(re.escape(part) for part in folded.split())
    return _Pattern(word, re.compile(rf"(?<!\w){body}(?!\w)"), case_sensitive)


def scan(texts: list[str]) -> dict[str, list[str]]:
    """Keywords found per group, in any of the texts."""
    folded = [(fold(t), fold_ci(t)) for t in texts if t]
    hits: dict[str, list[str]] = {g: [] for g in GROUPS}
    for group, pats in _patterns().items():
        for p in pats:
            if any(p.regex.search(cs if p.case_sensitive else ci) for cs, ci in folded):
                hits[group].append(p.word)
    return hits


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

    def facts(self) -> dict[str, Any]:
        return {
            "keywords": self.keywords,
            "payment_keyword": bool(self.keywords["payment"]),
            "triggers": {
                "fraud": self.fraud,
                "fraud_weak": self.fraud_weak,
                "regulator": self.regulator,
                "unverified_payment": self.unverified_payment,
                "lookalikes": self.lookalikes,
            },
        }


def evaluate(
    parsed: ParsedMessage,
    keywords: dict[str, list[str]],
    found: dict[str, Any],
    *,
    org_domains: list[str],
    known_vendors: list[str],
    duplicate_message_id: bool,
) -> Triggers:
    """`found` holds the auth and computed facts for this message (senderauth, facts)."""
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
            for k in [*org_domains, *known_vendors]
            if lookalike(d, k)
        }
    )

    t.fraud, t.fraud_weak = _money_triggers(keywords, found, bool(t.lookalikes))
    t.fraud += _other_triggers(parsed, found, t.lookalikes, duplicate_message_id, payment)

    t.regulator = list(keywords["regulator"])
    unsigned_mime = auth == "pass" and found["auth"].get("mime_headers_signed") is False
    t.unverified_payment = payment and (auth == "none" or unsigned_mime)
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
    if bank and (not found["sender_confirmed"] or change or rtm):
        fraud.append(
            "bank details from an unconfirmed sender, with change wording, or with a "
            "Reply-To mismatch"
        )
    signals = (
        ("bank or change wording", bank or change),
        ("DMARC fail", auth == "fail"),
        ("not addressed to this mailbox", found["recipient_mismatch"]),
        ("lookalike domain", lookalikes),
        ("Reply-To mismatch", rtm),
    )
    second = [name for name, hit in signals if hit]
    # 2. a first-time sender with a payment keyword needs a second signal; alone it is weak
    if payment and not found["sender_seen_before"]:
        if second:
            fraud.append(f"first-time sender with a payment keyword and {second[0]}")
        else:
            weak.append("first-time sender with a payment keyword")
    # a known sender: Reply-To mismatch on a payment item is a second signal only (OD-068)
    elif payment and rtm:
        others = [name for name in second if name != "Reply-To mismatch"]
        if others:
            fraud.append(f"Reply-To mismatch on a payment item and {others[0]}")
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
        (display is not None, f"display name shows {display}"),  # 7
        (parsed.from_count > 1, "more than one From header"),  # 8
        (
            parsed.from_ambiguous and parsed.from_count == 1,
            "ambiguous From header: parsers may disagree on the sender",
        ),  # 8
        (bool(parsed.headers.get("x-ecf-install")), "carries an X-ECF-Install header"),  # 9
    )
    return [reason for hit, reason in checks if hit]


# ---- domains --------------------------------------------------------------------------------


def lookalike(domain: str, known: str) -> bool:
    """True when `domain` imitates `known` without being it or one of its subdomains: the same
    skeleton (homoglyphs, `rn` for `m`), the same name under another top-level domain, a
    one-edit typo of a long enough name, or the known domain used as a subdomain elsewhere."""
    d, k = domain.lower().rstrip("."), known.lower().rstrip(".")
    if d == k or d.endswith("." + k):
        return False
    if fold_ci(d) == fold_ci(k):
        return True
    d_labels, k_labels = d.split("."), k.split(".")
    if len(d_labels) < 2 or len(k_labels) < 2:
        return False
    d_name, k_name = fold_ci(d_labels[-2]), fold_ci(k_labels[-2])
    if d_name == k_name:  # acme.co vs acme.com; a different registered domain
        return True
    if len(k_name) >= TYPO_MIN and _edits(d_name, k_name) <= 1:
        return True
    sub = d_labels[:-2]
    return k_labels[-2] in sub or _contains(sub, k_labels)


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
    """The first domain or address domain in the display name that isn't the From domain or one
    of its subdomains (trigger 7)."""
    for d in _display_domains(name):
        if from_domain is None or not (d == from_domain or d.endswith("." + from_domain)):
            return d
    return None
