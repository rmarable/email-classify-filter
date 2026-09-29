"""Text folding for trigger matching (SPEC §8.5): NFKC, format characters removed, then the UTS #39
confusable skeleton, so lookalike letters (Cyrillic small a, Greek capital epsilon, `rn` for `m`,
`0` for `O`) match the letters they imitate.

skeleton(X) per UTS #39 §4: NFD(X), replace each character by its prototype from confusables.txt,
NFD again. Unicode 18.0.0 data (see data/unicode/README.md). The mapping is built once, as a
`str.translate` table, so folding a 10 MB text part stays fast.
"""

from __future__ import annotations

import re
import unicodedata
from functools import cache
from importlib import resources

_LINE = re.compile(r"^([0-9A-F]{4,6})\s*;\s*([0-9A-F ]+?)\s*;\s*MA\b")


@cache
def _table() -> dict[int, str]:
    text = resources.files("ecf_server.data.unicode").joinpath("confusables.txt").read_text("utf-8")
    table: dict[int, str] = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            table[int(m.group(1), 16)] = "".join(chr(int(c, 16)) for c in m.group(2).split())
    return table


def strip_format(text: str) -> str:
    """Remove format characters (Unicode category Cf: zero-width spaces and joiners, bidi marks,
    soft hyphens), which hide inside words without being visible."""
    return "".join(c for c in text if unicodedata.category(c) != "Cf")


def skeleton(text: str) -> str:
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", text).translate(_table()))


def normalize(text: str) -> str:
    """NFKC with format characters removed: readable text, no skeleton (for extracting domains)."""
    return strip_format(unicodedata.normalize("NFKC", text))


def fold(text: str) -> str:
    """What case-sensitive keywords (acronyms) match against: normalized, then skeleton."""
    return skeleton(normalize(text))


def fold_ci(text: str) -> str:
    """What case-insensitive phrases and domains match against: normalized, casefolded, then
    skeleton. Case goes first because the skeleton is case-sensitive (it maps `I` to `l`)."""
    return skeleton(normalize(text).casefold())
