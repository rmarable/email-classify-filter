"""Text folding for trigger matching (SPEC §8.5): NFKC, format characters removed, then the UTS #39
confusable skeleton, so lookalike letters (Cyrillic small a, Greek capital epsilon, `rn` for `m`,
`0` for `O`) match the letters they imitate.

skeleton(X) per UTS #39 §4: NFD(X), replace each character by its prototype from confusables.txt,
NFD again. Unicode 18.0.0 data (see data/unicode/README.md). The mapping is built once, as a
`str.translate` table, so folding a 10 MB text part stays fast.

Keywords match the folded text with nonspacing marks removed as well (`keyword_fold`), and amounts,
IBANs and wallet addresses match `entity_text` (pre-classifier plan §3, P15): a mark such as U+034F
or U+0305 on a letter or digit is near-invisible but splits a keyword. Entities never use the
skeleton, which maps `1` to `l` and `0` to `O`.
"""

from __future__ import annotations

import re
import sys
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


@cache
def _chars(category: str) -> dict[int, None]:
    """A `str.translate` table that removes every character of one Unicode category."""
    return {c: None for c in range(sys.maxunicode + 1) if unicodedata.category(chr(c)) == category}


@cache
def _format_chars() -> re.Pattern[str]:
    """Format characters as a class of ranges: about 4x faster than the table on a 10 MiB text
    (measured 2026-10-10)."""
    runs: list[list[int]] = []
    for c in sorted(_chars("Cf")):
        if runs and runs[-1][1] == c - 1:
            runs[-1][1] = c
        else:
            runs.append([c, c])
    return re.compile("[" + "".join(f"{re.escape(chr(lo))}-{re.escape(chr(hi))}"
                                    for lo, hi in runs) + "]")  # fmt: skip


def strip_format(text: str) -> str:
    """Remove format characters (Unicode category Cf: zero-width spaces and joiners, bidi marks,
    soft hyphens), which hide inside words without being visible."""
    return _format_chars().sub("", text)


def strip_marks(text: str) -> str:
    """Remove nonspacing marks (Unicode category Mn: combining accents, U+034F, variation
    selectors). After NFD this also removes accents, so `é` matches `e`."""
    return text.translate(_chars("Mn"))


def skeleton(text: str) -> str:
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", text).translate(_table()))


def normalize(text: str) -> str:
    """NFKC with format characters removed: readable text, no skeleton (for extracting domains)."""
    return strip_format(unicodedata.normalize("NFKC", text))


def fold(text: str) -> str:
    """Normalized, then skeleton: case-sensitive (keywords use `keyword_fold`)."""
    return skeleton(normalize(text))


def fold_ci(text: str) -> str:
    """What domains and names match against (keyword phrases use `keyword_fold_ci`): normalized,
    casefolded, then skeleton. Case goes first because the skeleton is case-sensitive (it maps
    `I` to `l`)."""
    return skeleton(normalize(text).casefold())


def keyword_fold(text: str) -> str:
    """What case-sensitive keywords (acronyms) match against: `fold` without nonspacing marks."""
    return strip_marks(fold(text))


def keyword_fold_ci(text: str) -> str:
    """What case-insensitive keyword phrases match against: `fold_ci` without nonspacing marks."""
    return strip_marks(fold_ci(text))


def entity_text(text: str) -> str:
    """What amounts, IBANs and wallet addresses match against: `normalize` without nonspacing
    marks, no skeleton."""
    return strip_marks(normalize(text))
