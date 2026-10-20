"""Checks on a generated email beyond the hygiene scan (eval generate plan, Checks; P14, P15,
P30). Nothing is edited: an email that fails is dropped, with the kind of failure recorded.

- Markup: HTML isn't accepted; any tag, `javascript:` or `on…=` handler in any field is refused.
- Brands and agencies: a sender display name naming a major brand or a government agency is
  refused (the safety rules allow them by role in the text only). The list below is a deny list
  of common names, not content; real names in general can't be detected and are a stated limit.
- Near-duplicates: an email whose opening line matches an earlier one, or whose word 5-grams
  overlap an earlier one's by `SHINGLE_LIMIT` or more, is dropped across the whole set.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

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
SHINGLE = 5
SHINGLE_LIMIT = 0.6


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


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


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
        self._openings: set[str] = set()
        self._shingles: list[frozenset[tuple[str, ...]]] = []

    def seen(self, body: str) -> str | None:
        """Why `body` is a near-duplicate, or None (it is then remembered)."""
        first, sh = opening(body), shingles(body)
        if first and first in self._openings:
            return "same opening line"
        for other in self._shingles:
            if sh and other and len(sh & other) / min(len(sh), len(other)) >= SHINGLE_LIMIT:
                return "overlapping text"
        if first:
            self._openings.add(first)
        self._shingles.append(sh)
        return None
