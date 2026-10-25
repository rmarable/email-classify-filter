"""Case cards: one hand-written test email per Markdown file with a YAML header.

---
id: bec-001
...header fields...
---
Plain-text body
## html
<p>optional HTML body</p>
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
import unicodedata
from collections.abc import Iterator
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ecf.errors import InvalidInputError
from ecf.eval import padbank
from ecf.schema import FieldKind, derive_labels, load_schema, unlabel_removed
from ecf.yamlio import load_yaml

_STRICT = ConfigDict(extra="forbid", frozen=True)
DEFAULT_DATE = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
# Who a case is delivered to (OD-443): `org`, the fictitious org's AP mailbox (its domain is an org
# domain), or `freemail`, a personal account at `freemail.example`, the test-only stand-in for a
# public provider such as gmail.com. The eval scratch (ecf_server/ruletest.py) sets up each one.
PROFILE_TO = {"org": "ap@acme.example", "freemail": "pat-lee@freemail.example"}
# Facts of the internal set: always computed from the profile, never set by a card (OD-443).
INTERNAL_FACTS = frozenset({"sender_origin", "from_org_address", "impersonates_internal"})
# computed from the message and the other facts; a card sets facts, never these (pre-gate G8)
DERIVED_FACTS = frozenset({"keywords", "triggers", "routes"})
# Long cards (window tuning step 2, branch 3): `{{pad}}` in a card's body is where padding to its
# `target_chars` goes, drawn from its voice's paragraphs (`padbank`) in an order fixed by the
# card's id (`pad_order`)
PAD = "{{pad}}"


class QuotedMessage(BaseModel):
    """An earlier message a long card quotes (window tuning step 2, branch 3); its date is
    required, so it can't default to the reply's own."""

    model_config = _STRICT
    from_: str = Field(alias="from")
    date: datetime
    body: str

    @field_validator("date")
    @classmethod
    def _utc(cls, when: datetime) -> datetime:
        """A date written without a zone is UTC, so a thread's dates always compare (E1)."""
        return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


class InvoiceSpec(BaseModel):
    model_config = _STRICT
    number: str = "INV-1001"
    due: str = "2026-10-15"
    lines: list[tuple[str, float]] = Field(default_factory=lambda: [("Consulting", 1200.0)])
    bank: str = "published example IBAN GB82 WEST 1234 5698 7654 32"


class Attachment(BaseModel):
    model_config = _STRICT
    name: str = Field(max_length=100)
    content_type: str = "application/octet-stream"
    text: str | None = None
    generate: Literal["invoice_pdf"] | None = None
    vendor: str = "Vendor A"
    pages: int = Field(default=1, ge=1, le=20)
    scanned: bool = False
    target_eml_mb: float | None = Field(default=None, gt=0, le=80)
    invoice: InvoiceSpec = Field(default_factory=InvoiceSpec)


def level_target(target: str) -> tuple[str, str] | None:
    """An `injection_target` of the form `<ordinal field>:<level>` (v3.0.0 S3b, operator decision
    2026-10-13), e.g. `fraud_risk:low`: the injection counts as taken when the classification's
    field is at or below that level. None for any other target (a category value, a fixed item,
    an action name). A field that isn't an ordinal of the shipped schema, a level it lacks, or
    its top level (at or below the top is every answer, so the check could never pass; fix
    review C4) raises ValueError."""
    if ":" not in target:
        return None
    field, _, level = target.partition(":")
    spec = load_schema().fields.get(field)
    if spec is not None and spec.kind is FieldKind.BOOLEAN:
        return None  # a yes/no field: `bool_target` (OD-555)
    if spec is None or spec.kind is not FieldKind.ORDINAL:
        raise ValueError(f"injection_target {target!r}: {field!r} isn't an ordinal field")
    if level not in spec.values:
        raise ValueError(f"injection_target {target!r}: {level!r} isn't a level of {field}"
                         f" ({', '.join(spec.values)})")  # fmt: skip
    if level == spec.values[-1]:
        raise ValueError(f"injection_target {target!r}: {level!r} is {field}'s top level, so"
                         " every answer would count as taken; name a lower level")  # fmt: skip
    return field, level


def bool_target(target: str) -> tuple[str, bool] | None:
    """An `injection_target` of the form `<yes/no field>:true` or `:false` (OD-555), e.g.
    `payment_related:false`: the injection counts as taken when the classification gives the
    field that value. None for any other target; a yes/no field with another value raises
    ValueError."""
    if ":" not in target:
        return None
    field, _, value = target.partition(":")
    spec = load_schema().fields.get(field)
    if spec is None or spec.kind is not FieldKind.BOOLEAN:
        return None
    if value not in ("true", "false"):
        raise ValueError(f"injection_target {target!r}: a yes/no field takes true or false")
    return field, value == "true"


def target_met(target: str, labels: dict[str, Any]) -> bool:
    """Whether the card's own labels already give what its injection target pushes toward, so
    the right answer would count as following the injection (OD-555, review D4): a yes/no target
    whose value is the label, or a level target at or above the labelled level."""
    if (bt := bool_target(target)) is not None:
        return labels.get(bt[0]) is bt[1]
    if (lt := level_target(target)) is not None:
        values = load_schema().fields[lt[0]].values
        got = labels.get(lt[0])
        return got in values and values.index(got) <= values.index(lt[1])
    return False


def phrase_fold(text: str) -> str:
    """How a card's phrase and its text are compared (OD-555, review D8): NFKC as preparation
    does, format characters (soft hyphen, zero-width space) dropped as preparation drops them,
    whitespace folded, lower case (as the run's case-insensitive search). Characters whose case
    folding changes their length (ß) can still differ; a card keeps to plain text."""
    norm = unicodedata.normalize("NFKC", text)
    return " ".join("".join(ch for ch in norm if unicodedata.category(ch) != "Cf").split()).lower()


class Safety(BaseModel):
    model_config = _STRICT
    must_escalate: bool = False
    must_not_hide: bool = False
    # a category value, a fixed item or an action name the injection pushes toward,
    # `<ordinal field>:<level>` (at or below that level counts as taken; `level_target`), or
    # `<yes/no field>:true|false` (`bool_target`, OD-555)
    injection_target: str | None = None

    @field_validator("injection_target")
    @classmethod
    def _level(cls, target: str | None) -> str | None:
        if target is not None:
            bool_target(target)
            level_target(target)
        return target


class Expected(BaseModel):
    model_config = _STRICT
    labels: dict[str, Any] = Field(default_factory=dict[str, Any])
    facts: dict[str, Any] = Field(default_factory=dict[str, Any])
    rule: str | None = None
    safety: Safety = Field(default_factory=Safety)

    @model_validator(mode="after")
    def _target_not_met(self) -> Expected:
        target = self.safety.injection_target
        mapped = unlabel_removed(self.labels)[0]  # as the eval reads them at load (I3)
        if target is not None and target_met(target, mapped | derive_labels(mapped)):
            raise ValueError(f"injection_target {target!r}: the card's labels (stated or derived)"
                             " already give it,"
                             " so the right answer would count as following it")  # fmt: skip
        return self

    @field_validator("facts")
    @classmethod
    def _not_internal(cls, facts: dict[str, Any]) -> dict[str, Any]:
        if bad := sorted(INTERNAL_FACTS & facts.keys()):
            raise ValueError(f"{', '.join(bad)} come from the profile, not the card")
        if bad := sorted(DERIVED_FACTS & facts.keys()):
            raise ValueError(f"{', '.join(bad)} are computed from the message, not set by a card")
        return facts


class Card(BaseModel):
    model_config = _STRICT
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    title: str
    threat: str = ""
    control: str = ""
    why: str = ""
    failure_looks_like: str = ""
    review: str = ""  # why the operator's judgement is needed; `ecf eval label` shows it
    author: Literal["hand", "claude", "gemma", "generated"] = "hand"
    # the model that wrote a generated card (`ecf eval generate`); set exactly when author is
    # `generated` (eval generate plan P12)
    generator: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9:._-]{0,80}$")
    profile: Literal["org", "freemail"] = "org"
    from_: str = Field(alias="from")
    to: list[str] = Field(default_factory=list[str])  # default: the profile's address
    cc: list[str] = Field(default_factory=list[str])
    reply_to: str | None = None
    subject: str
    date: datetime = DEFAULT_DATE
    message_id: str | None = None
    headers: dict[str, str] = Field(default_factory=dict[str, str])
    auth_results: list[str] = Field(default_factory=list[str])
    bulk: bool = False
    encoding: Literal["7bit", "8bit", "quoted-printable", "base64"] = "quoted-printable"
    hidden_text: str | None = None
    pad_to_mb: float = Field(default=0, ge=0, le=80)
    attachments: list[Attachment] = Field(default_factory=list[Attachment])
    expected: Expected = Field(default_factory=Expected)
    body: str = ""
    html: str | None = None
    # where a long card's signal and injected instruction are (window tuning, OD-555): phrases
    # in its text, found by the eval in the prepared text at run time; outside `expected`, so
    # adding them changes no other card's confirmation
    signal: str | None = None
    injection_phrase: str | None = None
    # a long card (window tuning step 2, branch 3): the plain body padded to `target_chars` at
    # `{{pad}}` (else after the text) in `pad_voice` (default vendor), then a signature, earlier
    # messages quoted newest first, and a footer, all written by the builder (`plain_text`)
    target_chars: int | None = Field(default=None, ge=1, le=100_000)
    pad_voice: Literal["vendor", "internal", "personal"] | None = None
    signature: str | None = None
    quoted_thread: list[QuotedMessage] = Field(default_factory=list[QuotedMessage])
    footer: str | None = None

    def plain_text(self) -> str:
        """The plain body the builder writes: the card's body, or for a long card its body
        padded and followed by its signature, quoted thread and footer."""
        if not self._long():
            return self.body
        return self._joined(self._padding())

    def phrase_offsets(self) -> list[tuple[str, int, bool]]:
        """Where each phrase starts in the plain body, and whether it is in the signature,
        quoted thread or footer, which preparation moves below a mark, so the eval finds it
        later than this (window tuning B3); a footer alone is moved only when preparation
        recognises it, so it isn't flagged. Spacing and case aside, as the run searches."""
        text, head = self.plain_text(), self._parts(self._padding())[0]
        out: list[tuple[str, int, bool]] = []
        for name, phrase in (("signal", self.signal), ("injection_phrase", self.injection_phrase)):
            words = phrase_fold(phrase or "").split()
            m = (
                re.search(r"\s+".join(map(re.escape, words)), text, re.IGNORECASE)
                if words
                else None
            )
            if m is not None:
                moved = m.start() >= len(head) and bool(self.signature is not None
                                                        or self.quoted_thread)  # fmt: skip
                out.append((name, m.start(), moved))
        return out

    def _long(self) -> bool:
        return (self.target_chars is not None or self.signature is not None
                or bool(self.quoted_thread) or self.footer is not None)  # fmt: skip

    def _bank(self) -> tuple[str, ...]:
        return padbank.VOICES[self.pad_voice or "vendor"]

    def _padding(self) -> str:
        """Whole paragraphs, the fewest that bring the plain body to `target_chars` or past it
        (so at most one paragraph past); none when the text is already that long."""
        if self.target_chars is None:
            return ""
        bank, paras = self._bank(), list[str]()
        order = pad_order(self.id, len(bank))
        while len(self._joined("\n\n".join(paras))) < self.target_chars:
            paras.append(bank[next(order)])
        return "\n\n".join(paras)

    def _joined(self, pad: str) -> str:
        head, tail = self._parts(pad)
        return "\n\n".join(p for p in (head, tail) if p) + "\n"

    def _parts(self, pad: str) -> tuple[str, str]:
        """The new text (body with its padding) and what follows it (signature, quoted thread,
        footer), line ends as LF."""
        body = self.body.replace("\r\n", "\n")
        if PAD in body:
            head = body.replace(PAD, pad).strip("\n")
        else:
            head = body.strip("\n") + (f"\n\n{pad}" if pad else "")
        tail: list[str] = []
        if self.signature is not None:
            tail.append("-- \n" + _lf(self.signature))
        if self.quoted_thread:
            tail.append(_quoted(self.quoted_thread))
        if self.footer is not None:
            tail.append(_lf(self.footer))
        return head, "\n\n".join(tail)

    @model_validator(mode="after")
    def _long_fields(self) -> Card:
        if (n := self.body.count(PAD)) > 1:
            raise ValueError(f"{PAD} may appear at most once in the body ({n} times)")
        if n and any(PAD in ln and ln != PAD for ln in self.body.splitlines()):
            raise ValueError(f"{PAD} must be a line of its own")
        if n and self.target_chars is None:
            raise ValueError(f"{PAD} in the body needs target_chars")
        if self.pad_voice is not None and self.target_chars is None:
            raise ValueError("pad_voice needs target_chars")
        others = [self.signature or "", self.footer or "", *(m.body for m in self.quoted_thread)]
        if any(PAD in t for t in others):
            raise ValueError(f"{PAD} goes in the body only")
        if not self._long():
            return self
        if self.pad_to_mb:
            raise ValueError("pad_to_mb doesn't go with target_chars, signature, quoted_thread or"
                             " footer")  # fmt: skip
        if self.html is not None or self.hidden_text is not None:
            raise ValueError("target_chars, signature, quoted_thread and footer are for"
                             " plain-body cards (no html or hidden_text)")  # fmt: skip
        if self.author == "generated":
            raise ValueError("a generated card can't be a long card (its padding would make"
                             " generated cards near-duplicates)")  # fmt: skip
        if any(not m.body.strip() for m in self.quoted_thread):
            raise ValueError("each quoted_thread message needs a body")
        dates = [m.date for m in self.quoted_thread]
        if any(a <= b for a, b in itertools.pairwise(dates)):
            raise ValueError("quoted_thread runs newest first (each message older than the one"
                             " before it)")  # fmt: skip
        if self.target_chars is not None and (n := len(self._joined(""))) >= self.target_chars:
            raise ValueError(f"target_chars {self.target_chars} is shorter than the card's text"
                             f" ({n} characters)")  # fmt: skip
        return self

    @model_validator(mode="after")
    def _generator(self) -> Card:
        if (self.author == "generated") != (self.generator is not None):
            raise ValueError("a generated card names its generator, and only a generated card")
        return self

    def _authored(self) -> str:
        """The card's own words in a long card: body, signature, quoted bodies, footer."""
        return "\n".join([self.body.replace(PAD, " "), self.signature or "",
                          *(m.body for m in self.quoted_thread), self.footer or ""])  # fmt: skip

    @model_validator(mode="after")
    def _phrases(self) -> Card:
        """Each phrase is in the plain body exactly once (review D5, D6): the run reads the
        plain body when there is one (the HTML only when it is blank, never hidden text) and
        places a phrase at its first occurrence."""
        phrases = (("signal", self.signal), ("injection_phrase", self.injection_phrase))
        if all(p is None for _, p in phrases):
            return self
        plain = (self.plain_text() if self.body.strip() or self._long()
                 else re.sub(r"<[^>]*>", " ", self.html or ""))  # fmt: skip
        text = phrase_fold(plain)
        # a long card's phrase is the author's words, never the builder's (padding, quote
        # headers), so the check can't depend on which paragraphs the padding draws (B9)
        bank = [phrase_fold(x) for x in self._bank()] if self.target_chars is not None else []
        for name, phrase in phrases:
            if phrase is None:
                continue
            if not (p := phrase_fold(phrase)):
                raise ValueError(f"{name} is empty")
            if any(p in para for para in bank):
                raise ValueError(f"{name} {phrase!r} is in the padding paragraphs; use the"
                                 " card's own words")  # fmt: skip
            if (n := text.count(p)) != 1:
                raise ValueError(f"{name} {phrase!r} isn't in the card's plain body" if not n
                                 else f"{name} {phrase!r} is in the body {n} times; it must"
                                 " appear once")  # fmt: skip
            if self._long() and p not in phrase_fold(self._authored()):
                raise ValueError(f"{name} {phrase!r} is only in text the builder writes (a quote"
                                 " header); use the card's own words")  # fmt: skip
        if self.injection_phrase is not None and self.expected.safety.injection_target is None:
            raise ValueError("an injection_phrase needs expected.safety.injection_target")
        return self

    @model_validator(mode="before")
    @classmethod
    def _default_to(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        fields = cast("dict[str, Any]", data)
        if fields.get("to"):
            return fields
        return {**fields, "to": [PROFILE_TO.get(str(fields.get("profile") or "org"), "")]}


def pad_order(card_id: str, n: int) -> Iterator[int]:
    """The order a card's padding draws its bank's `n` paragraphs: a start and a step coprime to
    `n`, both from the SHA-256 of its id, so every paragraph comes once before any repeats, no
    paragraph follows itself, and the order is the same on every Python version (B2)."""
    h = int.from_bytes(hashlib.sha256(card_id.encode()).digest())
    steps = [k for k in range(1, n) if math.gcd(k, n) == 1] or [1]
    start, step = h % n, steps[(h >> 64) % len(steps)]
    i = 0
    while True:
        yield (start + i * step) % n
        i += 1


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").strip("\n")


def _quoted(thread: list[QuotedMessage]) -> str:
    """Earlier messages as a mail client quotes them: each under its "On <date>, <sender>
    wrote:" line with "> " before every line, the older ones nested inside the newer."""
    block = ""
    for m in reversed(thread):
        inner = _lf(m.body) + (f"\n\n{block}" if block else "")
        quoted = "\n".join(f"> {ln}" if ln else ">" for ln in inner.split("\n"))
        block = f"On {format_datetime(m.date)}, {m.from_} wrote:\n{quoted}"
    return block


def parse_card(text: str, *, source: str = "card") -> Card:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise InvalidInputError(f"{source}: must start with a --- YAML header")
    try:
        end = next(i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---")
    except StopIteration as exc:
        raise InvalidInputError(f"{source}: the YAML header has no closing ---") from exc
    header = load_yaml("".join(lines[1:end]), source=source)
    if not isinstance(header, dict):
        raise InvalidInputError(f"{source}: the header must be a mapping")
    rest = "".join(lines[end + 1 :])
    body, html = rest, None
    marker = "\n## html\n"
    if marker in "\n" + rest:
        idx = ("\n" + rest).index(marker)
        body, html = rest[: max(idx - 1, 0)], ("\n" + rest)[idx + len(marker) :]
    data: dict[str, Any] = {**header, "body": body.strip("\n") + "\n", "html": html}
    try:
        return Card.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise InvalidInputError(
            f"{source}: {'.'.join(map(str, first['loc']))}: {first['msg']}"
        ) from exc


def load_cards(cases_dir: Path) -> list[Card]:
    cards = [
        parse_card(p.read_text(encoding="utf-8"), source=p.name)
        for p in sorted(cases_dir.glob("*.md"))
    ]
    ids = [c.id for c in cards]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise InvalidInputError(f"duplicate card ids: {sorted(dupes)}")
    return cards
