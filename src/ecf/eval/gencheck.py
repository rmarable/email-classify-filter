"""Checks on a generated email beyond the hygiene scan (eval generate plan, Checks; P14, P15,
P30). Nothing is edited: an email that fails is dropped, with the kind of failure recorded.

- Markup: HTML isn't accepted; any tag, `javascript:` or `on…=` handler in any field is refused.
- Brands and agencies: a sender display name naming a major brand or a government agency is
  refused (the safety rules allow them by role in the text only), and so is one in the sender or
  reply-to address: in a domain (its labels split on `.`, `_` and `-`, or joined, so
  `wells-fargo.example`, `pay-pal.example` and `wellsfargo.example` count) or anywhere in the
  value outside its addresses (a display-name form; OD-554). The list below is a deny list
  of common names, not content; real names in general can't be detected and are a stated limit.
- Near-duplicates: an email whose words match an earlier one's exactly, or whose word 5-grams
  overlap an earlier one's by `SHINGLE_LIMIT` or more, is dropped across the whole set. The
  overlap test needs `MIN_SHINGLES` on both sides, so two short emails sharing a stock sentence
  or footer aren't taken for copies. A shared opening line is not a drop: with a few fictitious
  recipients, greetings and stock first sentences repeat in unrelated emails (DEV run
  2026-10-20; OD-543). Repeated openings are counted and reported instead. A drop is counted
  against the later email's group. Long footers shared by one sender can still reach the
  overlap limit: a stated limit.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

from ecf.eval.hygiene import blank_addresses

_TAG = re.compile(r"</?[a-z][a-z0-9]*(?:\s[^<>]*)?/?>", re.I)
_SCRIPTISH = re.compile(r"<\s*(?:script|form|iframe|object|embed|style)\b|javascript\s*:"
                        r"|\bon[a-z]{3,20}\s*=", re.I)  # fmt: skip
BRANDS = (
    "adobe", "amazon", "american express", "amex", "apple", "bank of america", "barclays",
    "binance", "best buy", "cash app", "chase", "citibank", "coinbase", "costco", "dhl",
    "docusign", "dropbox", "ebay", "facebook", "fedex", "geek squad", "google", "gmail", "hsbc",
    "instagram", "intuit", "linkedin", "mastercard", "mcafee", "meta", "microsoft", "netflix",
    "norton", "office 365", "outlook", "paypal", "quickbooks", "royal mail", "slack", "ups",
    "usps", "venmo", "visa", "walmart", "wells fargo", "whatsapp", "zelle", "zoom",
)  # fmt: skip
AGENCIES = (
    "cbp", "dmv", "europol", "fbi", "ftc", "hmrc", "homeland security", "interpol", "irs",
    "medicare", "sec", "social security", "ssa", "treasury", "us marshals", "u.s. marshals",
)  # fmt: skip
_NAMES = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in (*BRANDS, *AGENCIES)) + r")\b", re.I)
# Each name with its spaces removed, for domains whose labels join into one (`pay-pal`).
_SPACELESS = {n.replace(" ", "").casefold() for n in (*BRANDS, *AGENCIES)}
_LONGEST = max(len(n) for n in _SPACELESS)
_DOMAIN = re.compile(r"@([\w.-]+)")
_LABEL_SPLIT = re.compile(r"[._-]+")
SHINGLE = 5
SHINGLE_LIMIT = 0.6
MIN_SHINGLES = 20  # both emails need this many 5-grams for the overlap test
_WORD = re.compile(r"[\w']+")  # Unicode letters and digits


def markup(texts: Iterable[str]) -> str | None:
    """The kind of markup found in any of `texts`, or None."""
    for t in texts:
        if _SCRIPTISH.search(t):
            return "script, form or handler"
        if _TAG.search(t):
            return "HTML markup"
    return None


def named_brand(display_name: str) -> bool:
    return _NAMES.search(display_name) is not None


def named_brand_address(value: str) -> bool:
    """A brand or agency in a sender or reply-to value: in a domain's labels, split or joined,
    or in the value with its addresses blanked (a display name in any form). The local part is
    not checked (the hygiene scan tests it)."""
    value = unicodedata.normalize("NFKC", value)
    for domain in _DOMAIN.findall(value):
        labels = [x for x in _LABEL_SPLIT.split(domain) if x]
        if named_brand(" ".join(labels)):
            return True
        for i in range(len(labels)):
            joined = ""
            for j in range(i, len(labels)):  # not `labels[i:]`: a copy per start is quadratic
                joined += labels[j].casefold()
                if len(joined) > _LONGEST:
                    break  # a run longer than every name can't match one: linear
                if joined in _SPACELESS:
                    return True
    return named_brand(blank_addresses(value))


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w.strip("_'")]


def opening(body: str) -> str:
    """The first non-empty line, words only."""
    for line in body.splitlines():
        if words := _words(line):
            return " ".join(words)
    return ""


def shingles(body: str) -> frozenset[tuple[str, ...]]:
    w = _words(body)
    return frozenset(tuple(w[i : i + SHINGLE]) for i in range(max(len(w) - SHINGLE + 1, 0)))


class NearDuplicates:
    """The set's accepted emails so far, to drop a near-duplicate of any of them."""

    def __init__(self) -> None:
        self._texts: set[str] = set()
        self._shingles: list[frozenset[tuple[str, ...]]] = []
        self.openings: Counter[str] = Counter()

    def seen(self, body: str) -> str | None:
        """Why `body` is a near-duplicate, or None (it is then remembered)."""
        words, sh = " ".join(_words(body)), shingles(body)
        if words and words in self._texts:
            return "identical text"
        if len(sh) >= MIN_SHINGLES:
            for other in self._shingles:
                if (
                    len(other) >= MIN_SHINGLES
                    and len(sh & other) / min(len(sh), len(other)) >= SHINGLE_LIMIT
                ):
                    return "overlapping text"
        if words:
            self._texts.add(words)
        self._shingles.append(sh)
        if first := opening(body):
            self.openings[first] += 1
        return None

    def repeated_openings(self) -> int:
        """Kept emails whose opening line an earlier kept email already had."""
        return sum(n - 1 for n in self.openings.values() if n > 1)
