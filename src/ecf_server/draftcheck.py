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
                     "\u2014": "-", "\u2026": "..."}  # fmt: skip
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
        first character."""
        out: list[str] = []
        idx: list[int] = []
        pos = 0
        for start, end, new in sorted(spans):
            out.append(self.text[pos:start])
            idx.extend(self.idx[pos:start])
            out.append(new)
            idx.extend([self.idx[start]] * len(new))
            pos = end
        out.append(self.text[pos:])
        idx.extend(self.idx[pos:])
        return Mapped("".join(out), idx)

    def origin(self, start: int, end: int) -> tuple[int, int]:
        """The draft span a [start, end) of this text came from."""
        return self.idx[start], self.idx[max(start, end - 1)] + 1


def _phrase(words: Iterable[str]) -> re.Pattern[str]:
    """One regex for a list of words and phrases, at word boundaries, longest first."""
    parts: list[str] = []
    for w in sorted({str(x) for x in words}, key=len, reverse=True):
        folded = skeleton.keyword_fold_ci(w)
        parts.append(r"\s+".join(re.escape(p) for p in folded.split()))
    return re.compile(r"(?<![\w'])(?:" + "|".join(parts) + r")(?![\w])")


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
    return {
        "email": re.compile(r"(?<![\w.%+'-])[\w.%+'-]+@[\w-]+(?:\.[\w-]+)+"),
        "obfuscated": re.compile(
            r"(?i)\b[\w.+-]+\s*[(\[{]\s*at\s*[)\]}]\s*[\w-]+"
            r"(?:\s*[(\[{]\s*dot\s*[)\]}]\s*[\w-]+)+"
        ),
        "url": re.compile(r"(?i)\b(?:https?|hxxps?|ftp)://[^\s<>\"']+|\bwww\.[^\s<>\"']+"),
        "defanged": re.compile(r"(?i)\b[\w-]+(?:\[\.\]|\(\.\)|\{\.\})[\w.\-\[\]()/]+"),
        "domain": re.compile(
            r"(?i)(?<![\w@.\-/])(?:[a-z0-9][a-z0-9-]+\.)+(?:"
            + "|".join(L["link_tlds"])
            + r")(?:/[^\s<>\"']*)?(?![\w@])"
        ),
        "commit": _phrase(L["commitment_verbs"]),
        "contraction": re.compile(
            r"(?i)\w(?:'ll|'d)\b|\b(?:we're|i'm|they're|you're)\s+going\s+to\b"
        ),
        "request": _phrase(L["request_openers"]),
        "not_request": _phrase(L["not_requests"]),
        "pay": _phrase(L["pay_verbs"]),
        "inflection": _phrase(L["inflections"]),
        "payment_noun": _phrase(L["payment_nouns"]),
        "bank_word": _phrase(L["bank_words"]),
        "price_word": _phrase(L["price_words"]),
        "credential": _phrase(L["credential_nouns"]),
        "not_credential": _phrase(L["not_credentials"]),
        "credential_send": _phrase(L["credential_sends"]),
        "negation": _phrase(L["negations"]),
        "injection": _phrase(L["injection"]),
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
        "numeric_date": re.compile(r"(?<![\d.])(?:\d{1,4}[/.\-]\d{1,2}(?:[/.\-]\d{2,4})?)(?![\d])"),
        "time": re.compile(
            r"(?i)\b(?:[01]?\d|2[0-3])(?:[:.][0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)|\b(?:[01]?\d|2[0-3])[:.][0-5]\d\b"
        ),
        "clock": re.compile(r"(?<![\d:])(?:[01]?\d|2[0-3]):[0-5]\d(?![\d:])"),
        "amount": re.compile(
            rf"(?:[{sym}]\s?{num}|\b{num}\s?(?:{words})\b|\b(?:{codes})\.?\s?{num}\b"
            rf"|\b(?:\w+\s+)?(?:{'|'.join(map(str, L['number_words']))})\s+(?:{words})\b)"
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
        "digit_word": re.compile(r"(?i)\b(?:" + "|".join(L["digit_words"]) + r")\b"),
        "digits": re.compile(r"\d+"),
        # a + or 0 prefix, 3-3-4 or 3-4: a doubtful number is left to `bank` (AR1, AT8)
        "phone": re.compile(
            r"^(?:\+\d{1,3}[ \-.]?\(?\d{1,5}\)?(?:[ \-.]?\d{2,4}){2,3}"
            r"|\(?0\d{2,4}\)?[ \-.]?\d{3,4}[ \-.]?\d{3,4}"
            r"|\(?\d{3}\)?[ \-.]?\d{3}[ \-.]\d{4}|\d{3}[ \-.]\d{4})$"
        ),
        "street": re.compile(
            r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s+){1,3}(?:"
            + "|".join(w.capitalize() for w in map(str, L["street_suffixes"]))
            + r")\b\.?"
        ),
        "month_word": re.compile(rf"(?i)^\s+(?:{months})\.?,?\s+$"),
        "month_before": re.compile(rf"(?i)(?:{months})\.?\s+$"),
        "joiner": re.compile(r"^[\s\-\u2013\u2014/.,_:]{1,3}$"),
        "connector": re.compile(r"(?i)^\s+(?:" + "|".join(L["digit_connectors"]) + r")\s+$"),
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
        has_digit = any(ch.isdigit() for ch in tok)
        in_bank = bank_sentence[_sentence(starts, src.idx[m.start()])]
        if (has_digit or (in_bank and len(tok) >= 4)) and not tok.isdigit():
            reps.append((m.start(), m.end(), tok.translate(LOOKALIKE_DIGITS)))
    for m in pats["lookalike_date"].finditer(src.text):
        tok = m.group()
        if not any(ch.isdigit() for ch in tok) and any(ch in "Oo" for ch in tok):
            reps.append((m.start(), m.end(), tok.translate(LOOKALIKE_DIGITS)))
    reps = _no_overlap(reps)
    mapped = src.replaced(reps)
    words = list(pats["digit_word"].finditer(mapped.text))
    runs: list[tuple[int, int, str]] = []
    run: list[re.Match[str]] = []
    digit_of = {str(k): str(v) for k, v in _lists()["digit_words"].items()}
    for w in [*words, None]:
        if (
            w is not None
            and run
            and re.fullmatch(r"[\s,\-]+", mapped.text[run[-1].end() : w.start()])
        ):
            run.append(w)
            continue
        if len(run) >= 2:
            runs.append((run[0].start(), run[-1].end(),
                         "".join(digit_of[x.group().lower()] for x in run)))  # fmt: skip
        run = [w] if w is not None else []
    return mapped.replaced(runs)


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


@dataclass
class _Hit:
    start: int  # in the draft
    end: int
    sentence: int
    text: str  # as the view matched it


def _hits(view: Mapped, pat: re.Pattern[str], starts_orig: Sequence[int]) -> list[_Hit]:
    out: list[_Hit] = []
    for m in pat.finditer(view.text):
        if m.end() == m.start():
            continue
        s, e = view.origin(m.start(), m.end())
        out.append(_Hit(s, e, _sentence(starts_orig, s), m.group()))
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
    clocks = [(m.start(), m.end()) for m in pats["clock"].finditer(view.text)]
    groups: list[tuple[int, int, tuple[int, int] | None]] = []
    for m in pats["digits"].finditer(view.text):
        clock = next(((s, e) for s, e in clocks if s <= m.start() < e), None)
        groups.append((m.start(), m.end(), clock))
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
    pats = _patterns()
    base = _fold(text)
    masks = _addresses_and_links(base, text, email_links, allowed_addresses,
                                 refuse, warn)  # fmt: skip
    masked = base.replaced(masks)
    words = _words(masked)
    starts_folded = _sentences(masked.text)
    starts = [masked.idx[s] if s < len(masked.idx) else len(text) for s in starts_folded]
    bank_words = _hits(words, pats["bank_word"], starts)
    n = len(starts)
    bank_sentence = [False] * n
    for h in bank_words:
        bank_sentence[h.sentence] = True
    digits = _digits(masked, bank_sentence, starts)
    email = _email_facts(email_text)
    amounts = _hits(masked, pats["amount"], starts)
    _check_bank_and_contact(digits, masked, starts, bank_sentence, email, amounts, refuse)
    _check_dates_and_money(words, digits, masked, starts, email, amounts, refuse)
    _check_price(words, digits, starts, email, amounts, refuse)
    _check_credential_and_injection(words, starts, refuse)
    _check_language(masked.text, refuse)
    spans = {code: draft_for_spans[s:e] for code, (s, e) in found.items()}
    return Result(tuple(sorted(found)), tuple(sorted(warn)), spans, digest())


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
    for name in ("url", "defanged", "domain"):
        for m in pats[name].finditer(base.text):
            if any(s < m.end() and m.start() < e for s, e in taken):
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


def _clean_link(link: str) -> str:
    return link.rstrip(".,;:!?)]'\"")


@dataclass
class _EmailFacts:
    payment_term: bool
    bank_word: bool
    amounts: set[tuple[str, float]]


def _email_facts(email_text: str) -> _EmailFacts:
    pats = _patterns()
    base = _fold(email_text if _bad_character(email_text) is None else _ascii_only(email_text))
    words = _words(base).text
    amounts = {_amount_key(m.group()) for m in pats["amount"].finditer(base.text)}
    term = bool(pats["payment_noun"].search(words) or pats["pay"].search(words) or amounts)
    return _EmailFacts(term, bool(pats["bank_word"].search(words)), amounts)


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
    code = re.match(r"([A-Z][A-Za-z]{0,2})\.?\s?\d", t)
    if code:
        cur = code.group(1).lower()
    num = re.search(r"\d[\d ,.]*", t)
    value = 0.0
    if num:
        n = num.group().strip().replace(" ", "")
        if re.search(r"[.,]\d{1,2}$", n):
            whole, cents = n[: -3 if n[-3] in ".," else -2], n[-2:].lstrip(".,")
            value = float(re.sub(r"[.,]", "", whole) or 0) + float(cents) / 100
        else:
            value = float(re.sub(r"[.,]", "", n))
        if low.rstrip().endswith("k"):
            value *= 1000
    return cur, value


def _check_bank_and_contact(digits: Mapped, masked: Mapped, starts: Sequence[int],
                            bank_sentence: Sequence[bool], email: _EmailFacts,
                            amounts: Sequence[_Hit], refuse: Refuse) -> None:  # fmt: skip
    """`bank` (AR1, AS1, AT2, AU6, AY3, BW-D1) and `contact` (phone numbers, street addresses)."""
    pats = _patterns()
    for m in pats["iban"].finditer(masked.text):
        if sum(ch.isdigit() for ch in m.group().translate(LOOKALIKE_DIGITS)) >= 10:
            refuse("bank", masked.origin(m.start(), m.end()))
    for ch in _chains(digits):
        span = digits.origin(ch.start, ch.end)
        if _inside(span, amounts):
            continue  # an amount is `money`'s ("€1,180.00" isn't an account number)
        sentence = _sentence(starts, span[0])
        chain_text = digits.text[ch.start : ch.end]
        phone = not ch.clock and bool(pats["phone"].match(chain_text)) and ch.digits >= 7
        if phone:
            refuse("contact", span)
        if bank_sentence[sentence] and ch.digits >= 4:
            refuse("bank", span)
            continue
        if ch.clock or ch.digits < 6:
            continue
        if phone:
            continue
        before = digits.text[max(0, ch.start - 12) : ch.start]
        month = pats["month_before"].search(before)
        shown = (month.group() if month else "") + chain_text
        if _valid_date(shown) and not email.bank_word:
            continue
        refuse("bank", span)
    for m in pats["street"].finditer(masked.text):
        refuse("contact", masked.origin(m.start(), m.end()))


def _is_by_commitment(hit: _Hit, date_starts: Sequence[int]) -> bool:
    """ "by" is a commitment verb only right before a date ("by Friday", not "by Pat")."""
    k = bisect_left(date_starts, hit.end)
    return k < len(date_starts) and date_starts[k] - hit.end <= 2


def _check_dates_and_money(words: Mapped, digits: Mapped, masked: Mapped, starts: Sequence[int],
                           email: _EmailFacts, amounts: Sequence[_Hit],
                           refuse: Refuse) -> None:  # fmt: skip
    """`date` (P7, Q2, AP4, AQ8, AR2, AS2, AT1, AU3, AU5, AW2) and `money` (V12, AV1, AW1, AX1)."""
    pats = _patterns()
    plain = Mapped(masked.text.lower(), masked.idx)  # date words without the confusables
    dates: list[_Hit] = []
    for name in ("weekday_date", "month_date", "ordinal_date"):
        dates += _hits(plain, pats[name], starts)
    dates += _hits(words, pats["relative_date"], starts)
    dates += [h for h in _hits(digits, pats["numeric_date"], starts)
              if _plausible_date(h.text) and not _inside((h.start, h.end), amounts)]  # fmt: skip
    dates += _hits(digits, pats["time"], starts)
    dates.sort(key=lambda h: h.start)
    raw_commits = _hits(words, pats["commit"], starts) + _hits(words, pats["contraction"], starts)
    date_starts = [d.start for d in dates]
    commits = [h for h in raw_commits if h.text != "by" or _is_by_commitment(h, date_starts)]
    noun_numbers = _hits(masked, pats["noun_number"], starts) + _hits(
        masked, pats["noun_for_number"], starts
    )
    pays = _hits(words, pats["pay"], starts)
    infl = _hits(words, pats["inflection"], starts)
    nouns = _hits(words, pats["payment_noun"], starts)
    n = len(starts)
    by_sentence: list[dict[str, list[_Hit]]] = [
        {"date": [], "commit": [], "pay": [], "infl": [], "noun": [], "amount": []}
        for _ in range(n)
    ]
    for key, hits in (("date", dates), ("commit", commits), ("pay", pays), ("infl", infl),
                      ("noun", nouns), ("amount", [*amounts, *noun_numbers])):  # fmt: skip
        for h in hits:
            by_sentence[h.sentence][key].append(h)
    for i, s in enumerate(by_sentence):
        if s["date"] and s["commit"] and not _exempt(words, starts, i, s["commit"], s["date"]):
            refuse("date", (s["date"][0].start, s["date"][0].end))
        if s["pay"]:
            refuse("money", (s["pay"][0].start, s["pay"][0].end))
        term_here = bool(s["pay"] or s["noun"] or s["amount"])
        if s["infl"] and (term_here or email.payment_term):
            refuse("money", (s["infl"][0].start, s["infl"][0].end))
        if s["commit"] and term_here:
            term = (s["pay"] or s["noun"] or s["amount"])[0]
            refuse("money", (term.start, term.end))
    for h in amounts:
        key = _amount_key(masked.text[_view_pos(masked, h.start) : _view_pos(masked, h.end)])
        if key not in email.amounts:
            refuse("money", (h.start, h.end))
    for h in noun_numbers:
        refuse("money", (h.start, h.end))


def _view_pos(view: Mapped, draft_pos: int) -> int:
    """The first position in a view that came from a draft position at or after `draft_pos`."""
    return bisect_left(view.idx, draft_pos)


def _exempt(words: Mapped, starts: Sequence[int], i: int, commits: Sequence[_Hit],
            dates: Sequence[_Hit]) -> bool:  # fmt: skip
    """A question or request to the sender: the sentence opens with one, its only commitment verbs
    are the opener and the act it asks for (and a "by" before the act's date), and every date
    belongs to that act (AQ8, AR2, AS2, AT1, AU5, AW2)."""
    pats = _patterns()
    start = _view_pos(words, starts[i])
    end = _view_pos(words, starts[i + 1]) if i + 1 < len(starts) else len(words.text)
    sentence = words.text[start:end]
    lead = len(sentence) - len(sentence.lstrip(" \"'("))
    opener = pats["request"].match(sentence, lead)
    if opener is None or pats["not_request"].match(sentence, lead):
        return False
    act = re.compile(r"\s+(?:kindly\s+|please\s+)?([a-z]+)").match(sentence, opener.end())
    if act is None:
        return False
    act_end = start + act.end()
    lists = _lists()
    objects = "|".join(map(str, lists["act_objects"]))
    belongs = re.compile(rf"\s*(?:(?:{objects})\s+)?(?:by\s+)?\Z")
    for d in dates:
        between = words.text[act_end : _view_pos(words, d.start)]
        if not belongs.match(between):
            return False
    for c in commits:
        pos = _view_pos(words, c.start)
        if start + lead <= pos < start + opener.end():
            continue  # the opener's own modal
        if start + act.start(1) <= pos < act_end:
            continue  # the act it asks for
        if c.text == "by":
            continue  # "by" before the act's date: `belongs` checked it
        return False
    return True


def _check_price(words: Mapped, digits: Mapped, starts: Sequence[int], email: _EmailFacts,
                 amounts: Sequence[_Hit], refuse: Refuse) -> None:  # fmt: skip
    """`price` (AM1): a price, discount or quote word in a sentence, or the one before it, with a
    number that isn't an amount copied from the email."""
    pats = _patterns()
    price = _hits(words, pats["price_word"], starts)
    if not price:
        return
    numbers = [*amounts, *_hits(digits, pats["percent"], starts),
               *(h for h in _hits(digits, pats["bare_number"], starts)
                 if not _inside((h.start, h.end), amounts))]  # fmt: skip
    sentences = {h.sentence for h in price} | {h.sentence + 1 for h in price}
    for h in sorted(numbers, key=lambda x: x.start):
        if h.sentence not in sentences:
            continue
        if h in amounts and _amount_key(h.text) in email.amounts:
            continue
        refuse("price", (h.start, h.end))
        return


def _check_credential_and_injection(words: Mapped, starts: Sequence[int], refuse: Refuse) -> None:
    pats = _patterns()
    for h in _hits(words, pats["credential"], starts):
        p = _view_pos(words, h.start)
        e = _view_pos(words, h.end)
        around = words.text[max(0, p - 30) : e + 12]
        if pats["not_credential"].search(words.text[max(0, p - 12) : e]):
            continue
        sends = pats["credential_send"].search(words.text[max(0, p - 30) : p])
        stated = re.match(r"\s*(?:is|:|=)\s*\S", words.text[e : e + 12])
        if pats["negation"].search(around[:30]) and not stated:
            continue
        if sends or stated:
            refuse("credential", (h.start, h.end))
            break
    for h in _hits(words, pats["injection"], starts):
        refuse("injection", (h.start, h.end))
        break


def _check_language(text: str, refuse: Refuse) -> None:
    """`language` (P8, Q13): below 40 characters only the script test applies, which
    `characters` already made; above, another language's common words outnumbering English's."""
    if len(text.strip()) < 40:
        return
    tokens = re.findall(r"[a-z]+", text.lower())
    counts = {lang: sum(t in words for t in tokens) for lang, words in _stop_words().items()}
    english = counts.pop("en")
    best = max(counts.values())
    if best >= 3 and best > english:
        m = re.search(r"\S+(?:\s+\S+){0,3}", text)
        refuse("language", (0, m.end() if m else len(text)))


@cache
def _stop_words() -> dict[str, frozenset[str]]:
    return {lang: frozenset(map(str, ws)) for lang, ws in _lists()["stop_words"].items()}
