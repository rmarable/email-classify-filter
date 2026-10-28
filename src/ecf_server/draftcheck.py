"""The draft checker (M8 plan §1): a pure function that refuses a model-written reply draft
holding a commitment, a bank, contact or credential detail, a new recipient or link, planted
instructions, text outside the allowed characters, or a language other than English.

SPEC calls it a fixed-pattern layer, not a commitment detector (P3): the eval measures what it
misses. Its specification is tests/fixtures/draftcheck_cases.yaml, each case checked on its exact
codes and on the text each code matched; its word lists are data/draftcheck.yaml. It reads
`outbound_plan.clean_draft`'s output, the text saved to Drafts, and changes nothing.

How it reads a draft:
- `characters` runs first on the saved text; if it refuses, no other code is reported.
- Line separators become line breaks, then each allowed character is folded to ASCII (accented
  letters through `skeleton.folds`, curly quotes and dashes to their ASCII forms), keeping an
  index from every folded character back to the draft, so a code reports the draft's own text.
- Addresses and links are found and masked first, so a word inside them is never read.
- Words are read casefolded with the ASCII confusables applied (`m` and `rn` alike), as
  `triggers.scan` reads keywords; digits are read with the checker's own look-alike mapping
  (`l` for 1, `O` for 0 beside digits) and spelled-out digit runs.
- Sentences end at a full stop, question or exclamation mark followed by a space and a capital,
  never after a listed abbreviation; the splitter errs toward joining (AP1).
"""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from ecf.yamlio import load_yaml
from ecf_server import skeleton

# Every character str.splitlines breaks on becomes a line break, U+2029 a paragraph break (AE7,
# AF4); outbound_plan.clean_draft uses the same table.
LINE_BREAKS = str.maketrans(
    {c: "\n" for c in "\x0b\x0c\x1c\x1d\x1e\x85\u2028"} | {"\u2029": "\n\n"}
)
TYPOGRAPHIC_ASCII = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
                     "\u2014": "-", "\u2026": "...", "\u2022": "-", "\u00b0": " ", "\u2122": " ",
                     "\u00a9": " ", "\u2192": "-", "\u2010": "-"}  # fmt: skip
LOOKALIKE_DIGITS = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "i": "1", "S": "5",
                                  "s": "5", "B": "8", "Z": "2", "z": "2"})  # fmt: skip


Refuse = Callable[[str, tuple[int, int]], None]  # a code and the draft span it matched


@dataclass(frozen=True)
class Result:
    """What the checker found: refuse and warn codes, sorted, the draft's own text each refuse code
    matched, and the digest of the checker that decided."""

    refuse: tuple[str, ...]
    warn: tuple[str, ...]
    spans: dict[str, str] = field(default_factory=dict[str, str])
    digest: str = ""


@cache
def _lists() -> dict[str, Any]:
    raw = resources.files("ecf_server.data").joinpath("draftcheck.yaml").read_text("utf-8")
    data: dict[str, Any] = load_yaml(raw, source="draftcheck.yaml")
    return data


@cache
def digest() -> str:
    """sha256 of the lists and this module's source: any change to either is a new checker."""
    lists = resources.files("ecf_server.data").joinpath("draftcheck.yaml").read_bytes()
    return hashlib.sha256(lists + Path(__file__).read_bytes()).hexdigest()


def accented_letters() -> str:
    """The committed list of accented letters `characters` allows."""
    return str(_lists()["accented_letters"])


def generated_accented_letters() -> str:
    """The list as the plan defines it (AI3): letters in U+00C0-U+017F whose first two
    `skeleton.folds` forms are plain ASCII letters, less the dotless i, the dotted capital I and
    the long s (U+0131, U+0130, U+017F)."""
    out: list[str] = []
    for cp in range(0xC0, 0x180):
        c = chr(cp)
        if (
            c.isalpha()
            and c not in "\u0131\u0130\u017f"
            and all(f.isascii() and f.isalpha() for f in skeleton.folds(c)[:2])
        ):
            out.append(c)
    return "".join(out)


@cache
def _allowed() -> frozenset[str]:
    return frozenset(accented_letters()) | frozenset(_lists()["typographic"])


def _bad_character(text: str) -> str | None:
    """The first character outside the allow-list (AH1, AI3, AI4): printable ASCII and `\\n`, a
    tab only in a line's leading whitespace, the typographic list and the accented letters."""
    allowed = _allowed()
    leading = True
    for c in text:
        if c == "\n":
            leading = True
            continue
        if c == "\t":
            if not leading:
                return c
            continue
        if c != " ":
            leading = False
        if not ("\x20" <= c <= "\x7e" or c in allowed):
            return c
    return None


class Mapped:
    """A text with, for every character, the index of the draft character it came from."""

    def __init__(self, text: str, idx: Sequence[int]) -> None:
        self.text = text
        self.idx = list(idx)
        self._tokens: frozenset[str] | None = None

    @property
    def tokens(self) -> frozenset[str]:
        """The text's words, lowercased: a word pattern none of whose first words is here can't
        match, so `_hits` skips it."""
        if self._tokens is None:
            self._tokens = frozenset(re.findall(r"[\w']+", self.text.lower()))
        return self._tokens

    @classmethod
    def per_char(cls, src: Mapped, fold: dict[str, str]) -> Mapped:
        """Each character replaced by its fold: `str.translate` for the text, and the index
        copied in slices, repeated only where a character grows (`m` to `rn`)."""
        out = src.text.translate(str.maketrans(fold)) if fold else src.text
        if len(out) == len(src.text):
            return cls(out, src.idx)
        grows = "".join(re.escape(c) for c, r in fold.items() if len(r) != 1)
        idx: list[int] = []
        prev = 0
        for m in re.finditer(f"[{grows}]", src.text):
            p = m.start()
            idx.extend(src.idx[prev:p])
            idx.extend([src.idx[p]] * len(fold[m.group()]))
            prev = p + 1
        idx.extend(src.idx[prev:])
        return cls(out, idx)

    def replaced(self, spans: Iterable[tuple[int, int, str]]) -> Mapped:
        """The text with each (start, end, new) span replaced; new characters map to the span's
        first character, the last of them to its last."""
        out: list[str] = []
        idx: list[int] = []
        pos = 0
        for start, end, new in sorted(spans):
            out.append(self.text[pos:start])
            idx.extend(self.idx[pos:start])
            out.append(new)
            # the last new character maps to the span's last, so origin() spans the whole
            idx.extend([self.idx[start]] * (len(new) - 1) + [self.idx[end - 1]] * (len(new) > 0))
            pos = end
        out.append(self.text[pos:])
        idx.extend(self.idx[pos:])
        return Mapped("".join(out), idx)

    def origin(self, start: int, end: int) -> tuple[int, int]:
        """The draft span a [start, end) of this text came from."""
        return self.idx[start], self.idx[max(start, end - 1)] + 1


# A word pattern's first words, so `_hits` can skip it on a text that holds none of them.
_FIRST_WORDS: dict[re.Pattern[str], frozenset[str]] = {}


def _phrase(words: Iterable[str]) -> re.Pattern[str]:
    """One regex for a list of words and phrases, at word boundaries, longest first."""
    parts: list[str] = []
    firsts: set[str] = set()
    for w in sorted({str(x) for x in words}, key=len, reverse=True):
        folded = skeleton.keyword_fold_ci(w)
        parts.append(r"\s+".join(re.escape(p) for p in folded.split()))
        firsts.add(re.findall(r"[\w']+", folded)[0] if re.search(r"[\w']", folded) else "")
    pattern = re.compile(r"(?<![\w'])(?:" + "|".join(parts) + r")(?![\w])")
    if "" not in firsts:
        _FIRST_WORDS[pattern] = frozenset(firsts)
    return pattern


@cache
def _patterns() -> dict[str, re.Pattern[str]]:
    L = _lists()
    months = "|".join(sorted(map(str, L["months"]), key=len, reverse=True))
    day = r"(?:[12]\d|3[01]|0?[1-9])"
    weekday = "|".join(L["weekdays"])
    sym = "".join(re.escape(str(s)) for s in L["currency_symbols"])
    num = r"\d+(?:[ ,.]\d{3})*(?:[.,]\d{1,2})?k?"
    words = "|".join(re.escape(str(w)) for w in L["currency_words"])
    codes = "|".join(re.escape(str(c)) for c in L["currency_codes"])
    spelled = "|".join([*map(str, L["digit_words"]), *map(str, L["tens_words"])])
    readers = "|".join(map(str, L["readers"]))
    return {
        # every pattern here starts only where a token starts, so none rescans a long run (AR-F1)
        "email": re.compile(r"(?<![\w.%+'-])[\w.%+'-]++@[\w-]++(?:\.[\w-]++)*"),
        "obfuscated": re.compile(
            r"(?i)(?<![\w.+-])[\w.+-]++\s*+[(\[{]\s*at\s*[)\]}]\s*[\w-]++"
            r"(?:\s*[(\[{]\s*dot\s*[)\]}]\s*[\w-]++)+"
            r"|(?<![\w.+-])[\w.+-]++\s+at\s+[\w-]++(?:\s+dot\s+[\w-]++)+"
        ),
        "url": re.compile(r"(?i)\b(?:https?|hxxps?|ftp)://[^\s<>\"']+|\bwww\.[^\s<>\"']+"),
        "defanged": re.compile(r"(?i)(?<![\w-])[\w-]++(?:\[\.\]|\(\.\)|\{\.\})[\w.\-\[\]()/]++"),
        "domain": re.compile(
            r"(?i)(?<![\w@.\-/])(?:[a-z0-9][a-z0-9-]{0,62}+\.){1,10}([a-z]{2,24}+)(?:/[^\s<>\"']*+)?(?![\w@])"
        ),
        "spelled_link": re.compile(
            r"(?i)(?<![\w-])[\w-]++\s+dot\s+(?:" + "|".join(L["link_tlds"]) + r")\b"
            r"(?:\s+slash\s+[\w-]+)*"
        ),
        "spaced_link": re.compile(r"(?i)(?<![\w-])[\w-]++(?:\s+\.\s+[\w-]++)+"),
        "comma_link": re.compile(r"(?i)(?<![\w-])[\w-]++,[a-z]{2,24}+/[\w./-]+"),
        "commit": _phrase(L["commitment_verbs"]),
        # a suffix ("'ll") after any word, or a whole phrase ("we're going to")
        "contraction": re.compile(
            r"(?i)\w(?:"
            + "|".join(re.escape(str(c)) for c in L["contractions"] if str(c)[0] == "'")
            + r")\b|"
            + _phrase(str(c) for c in L["contractions"] if str(c)[0] != "'").pattern
        ),
        "request": _phrase(L["request_openers"]),
        "not_request": _phrase(L["not_requests"]),
        "pay": _phrase(L["pay_verbs"]),
        "not_money": _phrase(L["not_money"]),
        "inflection": _phrase(L["inflections"]),
        "payment_noun": _phrase(L["payment_nouns"]),
        "bank_word": _phrase(L["bank_words"]),
        "bank_detail": _phrase(L["bank_detail_phrases"]),
        "change_word": _phrase(L["change_words"]),
        "price_word": _phrase(L["price_words"]),
        # the word view is folded (`m` reads `rn`), so hand-written words are folded too
        "credential": re.compile(
            _phrase(L["credential_nouns"]).pattern
            + r"|\b\d-digit\s+(?:"
            + "|".join(skeleton.keyword_fold_ci(w) for w in ("code", "number", "pin"))
            + r")\b"
        ),
        "weak_credential": _phrase(L["weak_credentials"]),
        "weak_after": re.compile(
            r"(?:"
            + "|".join(re.escape(skeleton.keyword_fold_ci(str(w))) for w in L["weak_signs_after"])
            + r")\b"
        ),
        "weak_before": re.compile(
            r"(?:"
            + "|".join(re.escape(skeleton.keyword_fold_ci(str(w))) for w in L["weak_signs_before"])
            + r")\s+(?:the\s+)?\Z"
        ),
        "credential_send": _phrase(L["credential_sends"]),
        "negation": _phrase(L["negations"]),
        "injection": _phrase(L["injection"]),
        "reader_injection": re.compile(
            rf"(?i)\b(?:note|message|instructions?)\s+(?:to|for)\s+(?:the\s+)?(?:{readers})\b"
            r"|\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above"
            r"|earlier|preceding)\s+(?:instructions|messages|rules)\b"
            r"|\bdisregard\s+(?:everything|all)\s+(?:above|before)\b"
            rf"|(?:^|(?<=[.!?]\s))(?:{readers})\s*[,:]|\brate\s+this\s+(?:as|draft|reply|response)\b"
            r"|\bscore\s+it\s+\d+"
        ),
        "abbreviation": re.compile(
            r"(?i)(?:^|[\s(])(?:"
            + "|".join(re.escape(str(a)) for a in L["abbreviations"])
            + r")\.$"
        ),
        "weekday_date": re.compile(rf"(?i)\b(?:{weekday})\b"),
        "month_date": re.compile(
            rf"(?i)\b(?:{day}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{months})\b\.?"
            rf"(?:,?\s+\d{{4}})?|(?:{months})\.?\s+{day}(?:st|nd|rd|th)?\b"
            rf"(?:,?\s+\d{{4}})?)"
        ),
        "ordinal_date": re.compile(
            r"(?i)\bthe\s+(?:[12]?\d|3[01])(?:st|nd|rd|th)\b|\b(?:[12]?\d|3[01])(?:st|nd|rd|th)\b"
        ),
        "relative_date": _phrase(L["relative_dates"]),
        # deadlines that aren't a named date (AR-E10), a bare hour after "at" (AR-F11), a date
        # agreed to ("Friday it is", "Friday works"; AR-E4, AR-E9)
        "deadline": re.compile(
            rf"(?i)\b(?:in|by|until|before|end\s+of|early|late|mid)\s+(?:{months})\b"
            r"|\b(?:within|in)\s+(?:\d+|a|an|a\s+few|a\s+couple\s+of|" + spelled + r")"
            r"\s+(?:business\s+|working\s+)?(?:days?|weeks?|months?|hours?)\b"
        ),
        "bare_hour": re.compile(r"(?i)\bat\s+(?:[1-9]|1[0-2])\b(?!\s*(?:%|\d|[a-z])|[:.]\d)"),
        "date_agree": re.compile(
            r"(?i)^\s*(?:it\s+is|works|is\s+fine|is\s+good|suits|is\s+confirmed|it\s+is\s+then)\b"
        ),
        "numeric_date": re.compile(r"(?<![\d.])(?:\d{1,4}[/.\-]\d{1,2}(?:[/.\-]\d{2,4})?)(?![\d])"),
        "time": re.compile(
            r"(?i)\b(?:[01]?\d|2[0-3])(?:[:.][0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)|\b(?:[01]?\d|2[0-3])[:.][0-5]\d\b"
        ),
        "clock": re.compile(r"(?<![\d:])(?:[01]?\d|2[0-3]):[0-5]\d(?![\d:])"),
        "amount": re.compile(
            rf"(?:[{sym}]\s?{num}|\b{num}\s?(?:[{sym}]|(?:{codes})\b|(?:{words})\b)"
            rf"|\b(?:{codes})\.?\s?{num}\b"
            rf"|\b(?:{'|'.join(map(str, L['number_words']))})\s+(?:{words})\b"
            rf"|\b(?:a|{spelled})\s+grand\b|\b\d+(?:[.,]\d)?k\b)"
        ),
        "amount_hint": re.compile(
            rf"\d|[{sym}]|(?i:\bgrand\b|\bthousand\b|\bhundred\b|\bmillion\b)"
        ),
        "percent": re.compile(r"\b\d+(?:\.\d+)?\s?%"),
        "bare_number": re.compile(r"(?<![\w.,])(?:\d{1,3}(?:,\d{3})+|\d{3,})(?![\w])"),
        # "balance 4500" is an amount; "invoice 2231" is an invoice's number, "invoice for 4500"
        # an amount
        "noun_number": re.compile(
            r"(?i)\b(?:" + "|".join(map(str, L["amount_nouns"])) + r")\s+(\d{3,})\b"
        ),
        "noun_for_number": re.compile(
            r"(?i)\b(?:" + "|".join(map(str, L["payment_nouns"])) + r")\s+(?:of|for)\s+(\d{3,})\b"
        ),
        "iban": re.compile(
            r"(?i)(?<![a-z0-9])[a-z]{2}\d{2}(?:[ .\-]{0,2}[a-z0-9]{1,4}){3,8}(?![a-z0-9])"
        ),
        "lookalike_token": re.compile(r"(?<![A-Za-z0-9])[0-9OolISsBZz]{2,}(?![A-Za-z0-9])"),
        "lookalike_date": re.compile(
            r"(?<![A-Za-z0-9])[0-9OolI]{1,4}(?:[/.][0-9OolI]{1,4}){1,2}(?![A-Za-z0-9])"
        ),
        "number_token": re.compile(
            r"(?i)\b(?:\d+|double|triple|oh|"
            + "|".join([*map(str, L["digit_words"]), *map(str, L["tens_words"])])
            + r")\b"
        ),
        "digits": re.compile(r"\d+"),
        # a + or 0 prefix, 3-3-4 or 3-4: a doubtful number is left to `bank` (AR1, AT8)
        "phone": re.compile(
            r"^(?:\+\d{1,3}[ \-.]?\(?\d{1,5}\)?(?:[ \-.]?\d{2,4}){2,3}"
            r"|\(?0\d{2,4}\)?[ \-.]?\d{3,4}[ \-.]?\d{3,4}"
            r"|\(?\d{3}\)?[ \-.]?\d{3}[ \-.]\d{4}|\d{3}[ \-.]\d{4})$"
        ),
        # a suffix that is also an ordinary word ("to close", "a park") needs a capitalised name
        "street": re.compile(
            r"(?i:\b(?:unit|suite|apt|flat)\s+\d+[a-z]?\b|\bp\.?\s?o\.?\s+box\s+\d+"
            r"|\b\d{1,5}\s+(?:[a-z]+\s+){1,3}(?:"
            + "|".join(map(str, L["street_suffixes"]))
            + r")\b)"
            r"|\b\d{1,5}\s+(?:[A-Z][a-z]+\s+){1,3}(?:"
            + "|".join(w.capitalize() for w in map(str, L["word_street_suffixes"]))
            + r")\b"
        ),
        "month_word": re.compile(rf"(?i)^\s+(?:{months})\.?,?\s+$"),
        "month_before": re.compile(rf"(?i)(?:{months})\.?\s+$"),
        "joiner": re.compile(r"^[\s\-\u2013\u2014/.,_:|*+()]{1,4}$"),
        "connector": re.compile(
            r"(?i)^,?\s+(?:" + "|".join(map(str, L["digit_connectors"])) + r"),?\s+$"
        ),
    }


def _fold(text: str) -> Mapped:
    """The draft (allowed characters only) folded to ASCII, with its index map."""
    m = Mapped(text, range(len(text)))
    fold: dict[str, str] = dict(TYPOGRAPHIC_ASCII)
    for c in set(text):
        if c in fold or c.isascii() or c in "\u00a3\u20ac":
            continue
        fold[c] = skeleton.folds(c)[0]
    return Mapped.per_char(m, fold)


def _words(src: Mapped) -> Mapped:
    """Casefolded with the ASCII confusables (as `skeleton.keyword_fold_ci`)."""
    low = Mapped(src.text.lower(), src.idx)
    return Mapped.per_char(low, dict(skeleton.ascii_confusables()))


def _digits(src: Mapped, bank_sentence: Sequence[bool], starts: Sequence[int]) -> Mapped:
    """Look-alike letters read as digits beside digits (or alone in a bank-word sentence), and
    runs of two or more spelled-out digits as digits."""
    pats = _patterns()
    reps: list[tuple[int, int, str]] = []
    for m in pats["lookalike_token"].finditer(src.text):
        tok = m.group()
        if tok.isdigit():
            continue
        has_digit = any(ch.isdigit() for ch in tok)
        if has_digit or (len(tok) >= 4 and bank_sentence[_sentence(starts, src.idx[m.start()])]):
            reps.append((m.start(), m.end(), tok.translate(LOOKALIKE_DIGITS)))
    for m in pats["lookalike_date"].finditer(src.text):
        tok = m.group()
        if not any(ch.isdigit() for ch in tok) and any(ch in "Oo" for ch in tok):
            reps.append((m.start(), m.end(), tok.translate(LOOKALIKE_DIGITS)))
    reps = _no_overlap(reps)
    mapped = src.replaced(reps)
    if _number_words().isdisjoint(mapped.tokens):
        return mapped
    return mapped.replaced(_spelled_runs(mapped.text))


def _spelled_runs(text: str) -> list[tuple[int, int, str]]:
    """Runs of two or more number tokens, at least one spelled out, as digits (AR-E12): "four four
    seven one", "double four seven one", "forty-four seventy-one", "44 seventy one", and "oh" for
    0 inside such a run ("five five five oh one four two")."""
    lists = _lists()
    unit = {str(k): str(v) for k, v in lists["digit_words"].items()} | {"oh": "0"}
    tens = {str(k): str(v) for k, v in lists["tens_words"].items()}
    runs: list[tuple[int, int, str]] = []
    run: list[tuple[int, int, str, bool]] = []  # start, end, digits, spelled

    def close() -> None:
        if len(run) >= 2 and any(sp for *_, sp in run) and not all(d == "0" for _, _, d, _ in run):
            runs.append((run[0][0], run[-1][1], "".join(d for _, _, d, _ in run)))
        run.clear()

    pending = ""  # "double" or "triple" before a unit word
    for m in _patterns()["number_token"].finditer(text):
        word = m.group().lower()
        if run and not re.fullmatch(r"[\s,\-]+", text[run[-1][1] : m.start()]):
            close()
        if word in ("double", "triple"):
            pending = word
            run.append((m.start(), m.end(), "", True))
            continue
        if word.isdigit():
            digits, spelled = word, False
        elif word in tens and len(tens[word]) == 2:
            digits, spelled = tens[word], True
        elif word in tens:
            digits, spelled = tens[word] + "0", True
            nxt = re.match(r"[\s\-]+(\w+)", text[m.end() :])
            if nxt and nxt.group(1).lower() in unit and nxt.group(1).lower() != "oh":
                digits = tens[word]  # "seventy one": the unit word adds the last digit
        else:
            digits, spelled = unit[word], True
            if word == "oh" and not run:
                continue  # "oh" alone is a word
        if pending:
            digits = digits * (2 if pending == "double" else 3)
            pending = ""
        run.append((m.start(), m.end(), digits, spelled))
    close()
    return runs


def _no_overlap(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for s in sorted(spans):
        if not out or s[0] >= out[-1][1]:
            out.append(s)
    return out


def _sentences(text: str) -> list[int]:
    """Start offsets of the sentences of a folded, masked text (AP1: when unsure, join)."""
    pats = _patterns()
    starts = [0]
    for m in re.finditer(r"[.!?][\"')\]]*\s+(?=[A-Z\"(\[])|\n\s*\n", text):
        if m.group().startswith(".") and pats["abbreviation"].search(
            text[max(0, m.start() - 8) : m.start() + 1]
        ):
            continue
        if m.group().startswith(".") and re.search(
            r"(?:^|\s)[A-Za-z]\.$", text[max(0, m.start() - 2) : m.start() + 1]
        ):
            continue
        starts.append(m.end())
    return starts


def _sentence(starts: Sequence[int], pos: int) -> int:
    return bisect_right(starts, pos) - 1


@dataclass(slots=True)
class _Hit:
    start: int  # in the draft
    end: int
    sentence: int
    text: str  # as the view matched it


def _hits(view: Mapped, pat: re.Pattern[str], starts_orig: Sequence[int]) -> list[_Hit]:
    firsts = _FIRST_WORDS.get(pat)
    if firsts is not None and firsts.isdisjoint(view.tokens):
        return []
    idx = view.idx
    out: list[_Hit] = []
    for m in pat.finditer(view.text):
        a, b = m.span()
        if a == b:
            continue
        s = idx[a]
        out.append(_Hit(s, idx[b - 1] + 1, bisect_right(starts_orig, s) - 1, m.group()))
    return out


def _valid_date(text: str) -> bool:
    """One valid calendar date with a 4-digit year from 1900 to 2100 (separators or a month name),
    or a year range in that span (escape 1, AS1, AU6)."""
    months = [str(m) for m in _lists()["months"]]
    t = text.strip().rstrip(".,")
    rng = re.fullmatch(r"(\d{4})\s*[\-\u2013/]\s*(\d{4})", t)
    if rng:
        return all(1900 <= int(y) <= 2100 for y in rng.groups())
    nums = [int(x) for x in re.findall(r"\d+", t)]
    month_name = next(
        (i % 12 + 1 for i, m in enumerate(months[:12]) if re.search(rf"(?i)\b{m[:3]}", t)), None
    )
    if month_name is not None and len(nums) == 2:
        d, y = (nums[0], nums[1]) if nums[1] >= 1000 else (nums[1], nums[0])
        return 1900 <= y <= 2100 and _ok(y, month_name, d)
    if len(nums) == 3 and re.fullmatch(r"\d{1,4}([/.\-])\d{1,2}\1\d{1,4}", t):
        a, b, c = nums
        if a >= 1000:
            return 1900 <= a <= 2100 and _ok(a, b, c)
        if c >= 1000:
            return 1900 <= c <= 2100 and (_ok(c, b, a) or _ok(c, a, b))
    return False


def _inside(span: tuple[int, int], hits: Sequence[_Hit]) -> bool:
    """Whether `span` lies inside one of `hits`, which don't overlap and are in order."""
    k = _start_index(hits, span[0])
    return k >= 0 and span[1] <= hits[k].end


def _start_index(hits: Sequence[_Hit], pos: int) -> int:
    """The last hit starting at or before `pos`, or -1."""
    lo, hi = 0, len(hits)
    while lo < hi:
        mid = (lo + hi) // 2
        if hits[mid].start <= pos:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


def _plausible_date(text: str) -> bool:
    """A numeric date whose parts can be a day and a month (either order) or a year first:
    "10/11" and "2026-10-27" are dates, "180.00" isn't."""
    parts = [int(x) for x in re.findall(r"\d+", text)]
    if parts and parts[0] >= 1000:
        return len(parts) == 3 and 1 <= parts[1] <= 12 and 1 <= parts[2] <= 31
    a, b = parts[0], parts[1]
    return (1 <= a <= 31 and 1 <= b <= 12) or (1 <= a <= 12 and 1 <= b <= 31)


def _ok(y: int, m: int, d: int) -> bool:
    try:
        date(y, m, d)
    except ValueError:
        return False
    return True


@dataclass
class _Chain:
    start: int  # in the digit view
    end: int
    digits: int
    clock: bool


def _chains(view: Mapped) -> list[_Chain]:
    """Digit groups joined across separators, connector words and month names (AS1, AV4); a colon
    clock time never joins (AW4, AY3, BW-D1)."""
    pats = _patterns()
    clocks = [m.span() for m in pats["clock"].finditer(view.text)]
    clock_starts = [s for s, _ in clocks]

    def clock_of(pos: int) -> tuple[int, int] | None:
        k = bisect_right(clock_starts, pos) - 1
        return clocks[k] if k >= 0 and pos < clocks[k][1] else None

    groups: list[tuple[int, int, tuple[int, int] | None]] = [
        (m.start(), m.end(), clock_of(m.start())) for m in pats["digits"].finditer(view.text)
    ]
    chains: list[_Chain] = []
    for s, e, clock in groups:
        if clock is not None:
            if chains and chains[-1].clock and chains[-1].start == clock[0]:
                chains[-1].end = e
                chains[-1].digits += e - s
                continue
            chains.append(_Chain(clock[0], e, e - s, clock=True))
            continue
        if chains and not chains[-1].clock:
            gap = view.text[chains[-1].end : s]
            if (
                pats["joiner"].match(gap)
                or pats["connector"].match(gap)
                or pats["month_word"].match(gap)
            ):
                chains[-1].end = e
                chains[-1].digits += e - s
                continue
        chains.append(_Chain(s, e, e - s, clock=False))
    return chains


def check(draft: str, email_text: str, email_links: Sequence[str],
          allowed_addresses: Sequence[str]) -> Result:  # fmt: skip
    """Refuse and warn codes for a draft (`clean_draft`'s output) replying to an email whose
    actor excerpt is `email_text` with `email_links`, to be sent from or to `allowed_addresses`."""
    found: dict[str, tuple[int, int]] = {}
    warn: set[str] = set()

    def refuse(code: str, span: tuple[int, int]) -> None:
        found.setdefault(code, span)

    bad = _bad_character(draft)
    if bad is not None:
        return Result(("characters",), (), {"characters": bad}, digest())
    text = draft.replace("\r\n", "\n").replace("\r", "\n").translate(LINE_BREAKS)
    draft_for_spans = text  # same length as the draft: `characters` refused U+2029 already
    d = _read(text, email_text, email_links, allowed_addresses, refuse, warn)
    _check_bank(d, refuse)
    _check_contact(d, refuse)
    _check_dates_and_money(d, refuse)
    _check_price(d, refuse)
    _check_credential_and_injection(d, refuse)
    _check_language(d.masked.text, refuse)
    spans = {code: draft_for_spans[s:e].rstrip(".,;:") or draft_for_spans[s:e]
             for code, (s, e) in found.items()}  # fmt: skip
    return Result(tuple(sorted(found)), tuple(sorted(warn)), spans, digest())


@dataclass
class _Draft:
    """A draft's views, all mapping back to its characters, and what the checks share."""

    text: str  # the draft, line separators made line breaks
    masked: Mapped  # folded to ASCII, addresses and links masked
    words: Mapped  # casefolded, with the ASCII confusables
    plain: Mapped  # casefolded, without them (dates, requests, reader-addressed text)
    digits: Mapped  # look-alike letters and spelled numbers read as digits
    starts: list[int]  # each sentence's start in the draft
    bank_sentence: list[bool]  # the sentence holds a bank word
    email: _EmailFacts
    amounts: list[_Hit]


def _read(text: str, email_text: str, email_links: Sequence[str], allowed: Sequence[str],
          refuse: Refuse, warn: set[str]) -> _Draft:  # fmt: skip
    """The draft's views; `recipient`, `new_link` and the `link` warning on the way."""
    pats = _patterns()
    base = _fold(text)
    masked = base.replaced(_addresses_and_links(base, text, email_links, allowed, refuse, warn))
    words = _words(masked)
    starts = [masked.idx[s] if s < len(masked.idx) else len(text) for s in _sentences(masked.text)]
    bank_sentence = [False] * len(starts)
    for h in _hits(words, pats["bank_word"], starts):
        bank_sentence[h.sentence] = True
    digits = _digits(masked, bank_sentence, starts)
    plain = Mapped(masked.text.lower(), masked.idx)
    amounts = (
        _hits(masked, pats["amount"], starts) if pats["amount_hint"].search(masked.text) else []
    )
    return _Draft(text, masked, words, plain, digits, starts, bank_sentence,
                  _email_facts(email_text), amounts)  # fmt: skip


def _addresses_and_links(base: Mapped, text: str, email_links: Sequence[str],
                         allowed: Sequence[str], refuse: Refuse,
                         warn: set[str]) -> list[tuple[int, int, str]]:  # fmt: skip
    """`recipient`, `new_link` and the `link` warning; the spans to mask in the folded text."""
    pats = _patterns()
    ok = {a.lower() for a in allowed}
    links = {_clean_link(x).lower() for x in email_links}
    masks: list[tuple[int, int, str]] = []
    for name in ("email", "obfuscated"):
        for m in pats[name].finditer(base.text):
            s, e = base.origin(m.start(), m.end())
            raw = text[s:e]
            if name == "obfuscated" or not raw.isascii() or raw.lower() not in ok:
                refuse("recipient", (s, e))
            masks.append((m.start(), m.end(), " " * (m.end() - m.start())))
    masks = _no_overlap(masks)
    taken = [(s, e) for s, e, _ in masks]
    not_tlds = frozenset(map(str, _lists()["not_tlds"]))
    abbreviations = frozenset(map(str, _lists()["abbreviations"]))
    for name in ("url", "defanged", "domain", "spelled_link", "spaced_link", "comma_link"):
        for m in pats[name].finditer(base.text):
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue
            if name == "domain" and _not_a_domain(m, not_tlds, abbreviations):
                continue
            if name == "spaced_link" and m.group().count(".") < 2:
                continue
            link = _clean_link(m.group())
            end = m.start() + len(link)
            s, e = base.origin(m.start(), end)
            raw = text[s:e]
            if raw.isascii() and name != "defanged" and link.lower() in links:
                warn.add("link")
            else:
                refuse("new_link", (s, e))
            taken.append((m.start(), end))
            masks.append((m.start(), end, " " * (end - m.start())))
    return _no_overlap(masks)


def _not_a_domain(
    m: re.Match[str], not_tlds: frozenset[str], abbreviations: frozenset[str]
) -> bool:
    """A file name ("report.pdf"), an abbreviation ("e.g", "p.m", "Mr.Lee") or a dotted number
    ("IE29.AIBK.9311") that the domain pattern also matches."""
    host = m.group().split("/")[0].lower()
    first = host.split(".")[0]
    more_digits = re.match(r"\.\d", m.string[m.end() : m.end() + 2])  # "IE29.AIBK.9311..."
    return (
        m.group(1).lower() in not_tlds
        or host in abbreviations
        or first in abbreviations
        or bool(more_digits)
    )


def _clean_link(link: str) -> str:
    return link.rstrip(".,;:!?)]'\"")


@dataclass
class _EmailFacts:
    payment_term: bool
    bank_word: bool
    bank_details: bool  # names bank details (AR-E12)
    amounts: set[tuple[str, float]]


def _email_facts(email_text: str) -> _EmailFacts:
    pats = _patterns()
    base = _fold(email_text if _bad_character(email_text) is None else _ascii_only(email_text))
    words = _words(base).text
    amounts = {_amount_key(m.group()) for m in pats["amount"].finditer(base.text)}
    term = bool(pats["payment_noun"].search(words) or pats["pay"].search(words) or amounts)
    return _EmailFacts(term, bool(pats["bank_word"].search(words)),
                       bool(pats["bank_detail"].search(words)), amounts)  # fmt: skip


def _ascii_only(text: str) -> str:
    allowed = _allowed()
    return "".join(c if c.isascii() or c in allowed else " " for c in text)


def _amount_key(text: str) -> tuple[str, float]:
    """An amount as (currency, value): "EUR 2,000", "EUR 2 000" and "2,000 euros" are equal."""
    t = text.strip()
    cur = "?"
    low = t.lower()
    for sym, name in (("\u20ac", "eur"), ("$", "usd"), ("\u00a3", "gbp")):
        if sym in t:
            cur = name
    for word, name in (("euro", "eur"), ("dollar", "usd"), ("buck", "usd"), ("pound", "gbp"),
                       ("quid", "gbp"), ("yen", "jpy")):  # fmt: skip
        if word in low:
            cur = name
    code = re.match(r"([A-Z][A-Za-z]{0,2})\.?\s?\d", t) or re.search(r"\d\s?([A-Z]{3})\b", t)
    if code:
        cur = code.group(1).lower()
    num = re.search(r"\d[\d ,.]*", t)
    value = 0.0
    if num:
        n = num.group().strip().replace(" ", "").rstrip(".,")
        decimal = re.fullmatch(r"(.*?)[.,](\d{1,2})", n)
        if decimal:
            whole, frac = decimal.groups()
            value = float(re.sub(r"[.,]", "", whole) or 0) + int(frac) / 10 ** len(frac)
        else:
            value = float(re.sub(r"[.,]", "", n))
        if low.rstrip().endswith("k"):
            value *= 1000
    return cur, value


def _check_bank(d: _Draft, refuse: Refuse) -> None:
    """`bank` (AR1, AS1, AT2, AU6, AY3, BW-D1, AR-E1, AR-E12): IBANs, digit runs, and a bank-change
    confirmation; a phone number goes to `contact` (escape 2)."""
    pats = _patterns()
    for m in pats["iban"].finditer(d.masked.text):
        if sum(ch.isdigit() for ch in m.group().translate(LOOKALIKE_DIGITS)) >= 10:
            refuse("bank", d.masked.origin(m.start(), m.end()))
    for ch in _chains(d.digits):
        span = d.digits.origin(ch.start, ch.end)
        if not _inside(span, d.amounts):  # an amount is `money`'s ("€1,180.00")
            _check_chain(d, ch, span, refuse)
    details = {h.sentence: h for h in reversed(_hits(d.words, pats["bank_detail"], d.starts))}
    for h in _hits(d.words, pats["change_word"], d.starts):  # a bank-change confirmation (AR-E1)
        if h.sentence in details:
            refuse("bank", (details[h.sentence].start, details[h.sentence].end))


def _check_chain(d: _Draft, ch: _Chain, span: tuple[int, int], refuse: Refuse) -> None:
    """One digit run: `contact` if it is phone-shaped; `bank` if a bank word shares its sentence
    or the email names bank details and it has 4 or more digits, or if it has 6 or more and isn't
    a date (escape 1) or a phone number (escape 2)."""
    pats = _patterns()
    chain_text = d.digits.text[ch.start : ch.end]
    spelled = not any(c.isdigit() for c in d.text[span[0] : span[1]])
    shaped = bool(pats["phone"].match(chain_text)) or (spelled and ch.digits <= 11)
    phone = not ch.clock and ch.digits >= 7 and shaped
    if phone:
        refuse("contact", span)
    in_bank = d.bank_sentence[_sentence(d.starts, span[0])]
    if ch.digits >= 4 and (in_bank or (d.email.bank_details and not ch.clock)):
        refuse("bank", span)
        return
    if ch.clock or ch.digits < 6 or phone:
        return
    month = pats["month_before"].search(d.digits.text[max(0, ch.start - 12) : ch.start])
    if _valid_date((month.group() if month else "") + chain_text) and not d.email.bank_word:
        return
    refuse("bank", span)


def _check_contact(d: _Draft, refuse: Refuse) -> None:
    """`contact`'s street addresses (Q2, AR-E17); phone numbers are found with the digit runs."""
    for m in _patterns()["street"].finditer(d.masked.text):
        refuse("contact", d.masked.origin(m.start(), m.end()))


def _is_by_commitment(hit: _Hit, date_starts: Sequence[int]) -> bool:
    """ "by" is a commitment verb only right before a date ("by Friday", not "by Pat")."""
    k = bisect_left(date_starts, hit.end)
    return k < len(date_starts) and date_starts[k] - hit.end <= 2


def _dates(d: _Draft) -> list[_Hit]:
    """Every date, time and deadline in the draft, in order."""
    pats = _patterns()
    has_digit = any(c.isdigit() for c in d.digits.text)
    has_month = not _month_words().isdisjoint(d.plain.tokens)
    names = ["weekday_date"]
    names += ["ordinal_date", "bare_hour"] if has_digit else []
    names += ["month_date", "deadline"] if has_month else []
    found: list[_Hit] = []
    for name in names:
        found += _hits(d.plain, pats[name], d.starts)
    found += _hits(d.words, pats["relative_date"], d.starts)
    if has_digit:
        numeric = _hits(d.digits, pats["numeric_date"], d.starts)
        found += [
            h
            for h in numeric
            if _plausible_date(h.text) and not _inside((h.start, h.end), d.amounts)
        ]
        found += _hits(d.digits, pats["time"], d.starts)
    return sorted(found, key=lambda h: h.start)


_AGREE = re.compile(r"(?i)\b(?:it\s+is|works|is\s+fine|is\s+good|suits|is\s+confirmed)\b")


def _commits(d: _Draft, dates: Sequence[_Hit]) -> list[_Hit]:
    """The commitment verbs: "by" only before a date, and a date agreed to ("Friday it is")."""
    pats = _patterns()
    raw = _hits(d.words, pats["commit"], d.starts) + _hits(d.words, pats["contraction"], d.starts)
    date_starts = [h.start for h in dates]
    out = [h for h in raw if h.text != "by" or _is_by_commitment(h, date_starts)]
    for h in dates:
        if pats["date_agree"].match(d.words.text[_view_pos(d.words, h.end) :]):
            out.append(_Hit(h.start, h.end, h.sentence, h.text))
    return out


def _check_dates_and_money(d: _Draft, refuse: Refuse) -> None:
    """`date` (P7, Q2, AP4, AQ8, AR2, AS2, AT1, AU3, AU5, AW2) and `money` (V12, AV1, AW1, AX1),
    sentence by sentence; an amount not copied from the email refuses `money` anywhere."""
    pats = _patterns()
    # dates matter only beside a commitment verb or a date agreed to ("Friday it is")
    agree = _AGREE.search(d.words.text)
    raw_commits = _hits(d.words, pats["commit"], d.starts) or agree
    dates = _dates(d) if raw_commits or _hits(d.words, pats["contraction"], d.starts) else []
    money_words = d.words.replaced(  # the exempt phrases out of the money words (AR-F5)
        (m.start(), m.end(), " " * (m.end() - m.start()))
        for m in pats["not_money"].finditer(d.words.text)
    )
    noun_numbers = [
        h
        for name in ("noun_number", "noun_for_number")
        if any(c.isdigit() for c in d.masked.text)
        for h in _hits(d.masked, pats[name], d.starts)
    ]
    by_sentence: list[dict[str, list[_Hit]]] = [
        {"date": [], "commit": [], "pay": [], "infl": [], "noun": [], "amount": []}
        for _ in d.starts
    ]
    for key, hits in (
        ("date", dates),
        ("commit", _commits(d, dates)),
        ("pay", _hits(money_words, pats["pay"], d.starts)),
        ("infl", _hits(money_words, pats["inflection"], d.starts)),
        ("noun", _hits(money_words, pats["payment_noun"], d.starts)),
        ("amount", [*d.amounts, *noun_numbers]),
    ):
        for h in hits:
            by_sentence[h.sentence][key].append(h)
    for i, s in enumerate(by_sentence):
        if s["date"] and s["commit"] and not _exempt(d.plain, d.starts, i, s["commit"], s["date"]):
            refuse("date", (s["date"][0].start, s["date"][0].end))
        _check_money_sentence(s, d.email, refuse)
    for h in d.amounts:
        if _amount_key(h.text) not in d.email.amounts:
            refuse("money", (h.start, h.end))
    for h in noun_numbers:
        refuse("money", (h.start, h.end))


def _check_money_sentence(s: dict[str, list[_Hit]], email: _EmailFacts, refuse: Refuse) -> None:
    """A pay verb alone; an inflection with a payment term in the sentence or the email; a
    commitment verb with a payment term in the sentence (AV1, AW1, AX1)."""
    if s["pay"]:
        refuse("money", (s["pay"][0].start, s["pay"][0].end))
    terms = s["pay"] or s["noun"] or s["amount"]
    if s["infl"] and (terms or email.payment_term):
        refuse("money", (s["infl"][0].start, s["infl"][0].end))
    if s["commit"] and terms:
        refuse("money", (terms[0].start, terms[0].end))


def _view_pos(view: Mapped, draft_pos: int) -> int:
    """The first position in a view that came from a draft position at or after `draft_pos`."""
    return bisect_left(view.idx, draft_pos)


def _exempt(words: Mapped, starts: Sequence[int], i: int, commits: Sequence[_Hit],
            dates: Sequence[_Hit]) -> bool:  # fmt: skip
    """A question or request to the sender: the sentence opens with one, its only commitment verbs
    are the opener and the act it asks for (and a "by" before the act's date), and every date
    belongs to that act: after "by", or right after the act or its pronoun object ("send it on
    Friday", "call me tomorrow"), never after a noun ("confirm the delivery on Friday") (AQ8, AR2,
    AS2, AT1, AU5, AW2, AR-F2)."""
    pats = _patterns()
    start = _view_pos(words, starts[i])
    end = _view_pos(words, starts[i + 1]) if i + 1 < len(starts) else len(words.text)
    sentence = words.text[start:end]
    lead = len(sentence) - len(sentence.lstrip(" \"'("))
    opener = pats["request"].match(sentence, lead)
    if opener is None or pats["not_request"].match(sentence, lead):
        return False
    act = re.compile(r"\s+(?:kindly\s+|please\s+)?(let\s+(?:me|us)\s+know|[a-z]+)").match(
        sentence, opener.end()
    )
    if act is None:
        return False
    act_end = start + act.end()
    objects = "|".join(map(str, _lists()["act_objects"]))
    right_after = re.compile(rf"\s*(?:(?:{objects})\s+)?(?:(?:on|at|for)\s+)?\Z")
    for d in dates:
        between = words.text[act_end : _view_pos(words, d.start)]
        if not (re.search(r"\bby\s+\Z", between) or right_after.match(between)):
            return False
    for c in commits:
        pos = _view_pos(words, c.start)
        if start + lead <= pos < start + opener.end():
            continue  # the opener's own modal
        if start + act.start(1) <= pos < act_end:
            continue  # the act it asks for
        if c.text == "by":
            continue  # "by" before the act's date: checked above
        return False
    return True


def _check_price(d: _Draft, refuse: Refuse) -> None:
    """`price` (AM1, AR-E8, AR-F6): a number that isn't an amount copied from the email, in a
    sentence with a price, discount, quote or fee word (or the sentence after one); a percentage
    "off"; or "for" and a number after a commitment verb ("we can do the job for 4500"). A year
    and a count ("150 stores") aren't prices."""
    pats = _patterns()
    words, digits, starts, amounts = d.words, d.digits, d.starts, d.amounts
    price = _hits(words, pats["price_word"], starts)
    commits = {h.sentence for h in _hits(words, pats["commit"], starts)}
    if not price and not commits and "%" not in digits.text:
        return
    numbers = [  # an amount is `money`'s; a copied one with no commitment verb passes both
        *_hits(digits, pats["percent"], starts),
        *(
            h
            for h in _hits(digits, pats["bare_number"], starts)
            if not _inside((h.start, h.end), amounts) and not _year_or_count(digits, h)
        ),
    ]
    sentences = {h.sentence for h in price} | {h.sentence + 1 for h in price}
    for h in sorted(numbers, key=lambda x: x.start):
        before = digits.text[max(0, _view_pos(digits, h.start) - 5) : _view_pos(digits, h.start)]
        after = digits.text[_view_pos(digits, h.end) : _view_pos(digits, h.end) + 5]
        off = h.text.rstrip().endswith("%") and _OFF.match(after)
        bid = h.sentence in commits and _FOR_END.search(before)
        if h.sentence in sentences or off or bid:
            refuse("price", (h.start, h.end))
            return


_OFF = re.compile(r"(?i)\s*off\b")
_FOR_END = re.compile(r"(?i)\bfor\s+\Z")
_YEAR = re.compile(r"(?:19|20)\d\d|2100")
_NEXT_WORD = re.compile(r"(?i)\s+([a-z]+)")


def _year_or_count(view: Mapped, h: _Hit) -> bool:
    """A year from 1900 to 2100, or a number followed by a word that isn't a price word: "in 2026",
    "150 stores", "500 emails" (AR-F6)."""
    if _YEAR.fullmatch(h.text):
        return True
    after = view.text[_view_pos(view, h.end) : _view_pos(view, h.end) + 20]
    word = _NEXT_WORD.match(after)
    return word is not None and not _patterns()["price_word"].match(after.lstrip().lower())


def _check_credential_and_injection(d: _Draft, refuse: Refuse) -> None:
    """`credential` (AR-E13, AR-F7): a strong noun (a password, PIN, token, 2FA code) asked for,
    sent or stated; a bare "code" or "number" only with a sign it is one. `injection` (P5, S10,
    AR-E16): the listed phrases and wording addressed to a reader other than the sender."""
    pats = _patterns()
    words, starts = d.words, d.starts
    text = words.text
    candidates = [
        *((h, True) for h in _hits(words, pats["credential"], starts)),
        *((h, False) for h in _hits(words, pats["weak_credential"], starts)),
    ]
    for h, strong in candidates:  # fmt: skip
        p, e = _view_pos(words, h.start), _view_pos(words, h.end)
        before, after = text[max(0, p - 30) : p], text[e : e + 40]
        if strong and h.text == "login":
            hit = re.match(r"(?:\s+\w+){0,3}\s+is\s+\S", after)
        elif strong:
            stated = re.match(r"(?:'s)?\s*(?:is\b|:|=|\s+\S*[\d!@#$%^&*])", after)
            asked = pats["credential_send"].search(before)
            if pats["negation"].search(before) and not stated:
                continue
            hit = stated or asked
        else:
            hit = pats["weak_after"].match(after.lstrip()) or pats["weak_before"].search(before)
        if hit:
            refuse("credential", (h.start, h.end))
            break
    readers = not _reader_words().isdisjoint(d.plain.tokens)
    for name, view in (("injection", words), ("reader_injection", d.plain if readers else None)):
        if view is None:
            continue
        for h in _hits(view, pats[name], starts):
            refuse("injection", (h.start, h.end))
            break


def _check_language(text: str, refuse: Refuse) -> None:
    """`language` (P8, Q13): below 40 characters only the script test applies, which
    `characters` already made; above, another language's common words outnumbering English's."""
    if len(text.strip()) < 40:
        return
    seen = Counter(re.findall(r"[a-z]+", text.lower()))
    counts = {lang: sum(seen[w] for w in words) for lang, words in _stop_words().items()}
    english = counts.pop("en")
    best = max(counts.values())
    if best >= 3 and best > english:
        m = re.search(r"\S+(?:\s+\S+){0,3}", text)
        refuse("language", (0, m.end() if m else len(text)))


@cache
def _number_words() -> frozenset[str]:
    L = _lists()
    return frozenset([*map(str, L["digit_words"]), *map(str, L["tens_words"]), "double", "triple"])


@cache
def _month_words() -> frozenset[str]:
    return frozenset([*map(str, _lists()["months"]), "within", "in"])


@cache
def _reader_words() -> frozenset[str]:
    return frozenset(
        [*map(str, _lists()["readers"]), "ignore", "disregard", "forget", "rate", "score"]
    )


@cache
def _stop_words() -> dict[str, frozenset[str]]:
    return {lang: frozenset(map(str, ws)) for lang, ws in _lists()["stop_words"].items()}
