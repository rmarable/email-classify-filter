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

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ecf.errors import InvalidInputError
from ecf.yamlio import load_yaml

_STRICT = ConfigDict(extra="forbid", frozen=True)
DEFAULT_DATE = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
# Who a case is delivered to (OD-443): `org`, the fictitious org's AP mailbox (its domain is an org
# domain), or `freemail`, a personal account at `freemail.example`, the test-only stand-in for a
# public provider such as gmail.com. The eval scratch (ecf_server/ruletest.py) sets up each one.
PROFILE_TO = {"org": "ap@acme.example", "freemail": "pat-lee@freemail.example"}
# Facts of the internal set: always computed from the profile, never set by a card (OD-443).
INTERNAL_FACTS = frozenset({"sender_origin", "from_org_address", "impersonates_internal"})


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


class Safety(BaseModel):
    model_config = _STRICT
    must_escalate: bool = False
    must_not_hide: bool = False
    injection_target: str | None = None


class Expected(BaseModel):
    model_config = _STRICT
    labels: dict[str, Any] = Field(default_factory=dict[str, Any])
    facts: dict[str, Any] = Field(default_factory=dict[str, Any])
    rule: str | None = None
    safety: Safety = Field(default_factory=Safety)

    @field_validator("facts")
    @classmethod
    def _not_internal(cls, facts: dict[str, Any]) -> dict[str, Any]:
        if bad := sorted(INTERNAL_FACTS & facts.keys()):
            raise ValueError(f"{', '.join(bad)} come from the profile, not the card")
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
    author: Literal["hand", "claude", "gemma"] = "hand"
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

    @model_validator(mode="before")
    @classmethod
    def _default_to(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        fields = cast("dict[str, Any]", data)
        if fields.get("to"):
            return fields
        return {**fields, "to": [PROFILE_TO.get(str(fields.get("profile") or "org"), "")]}


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
