"""Which eval cards are prompt-injection cards (planning-docs/EVAL-PRIVATE.md X1, OD-633).

A card is marked when its front matter, at any level, has `injection_target`, `injection_phrase`
or `hidden_text`; a `threat:` containing "inject" (any case; "evasion" is not a marker, V4); or
`expected.draft.group: injection`. Front matter only, so the check is cheap and needs no parsed
`Card`: it also reads raw card blobs that today's card model can't parse. Content is checked by
trigger 10 on the built email (`scripts/check_card_injection.py`).

Marked cards belong in the private set (`eval/private/<set>`), except the one pinned public card
behind the shipped `injection` template (V3, OD-654). Callers import this module directly."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.yamlio import load_yaml

# Keys that mark a card wherever they sit in its front matter
MARKER_KEYS = frozenset({"injection_target", "injection_phrase", "hidden_text"})
# The rules, in one place, for the builder, the sdist test, the move script and the scans
INJECTION_MARKERS: dict[str, Any] = {
    "keys_any_level": MARKER_KEYS,
    "threat_contains": "inject",
    "draft_group": "injection",
}

# V3: the public card behind the `injection` template, pinned by the bytes of its file
STARTER_ID = "starter-injection"
STARTER_SHA256 = "afebb16654e9867261b6dc457941ec5a8e2c2bb0093d63d5a8f35f7201d19a28"


def front_matter(text: str, *, source: str = "card") -> dict[str, Any]:
    """The YAML header between a card's two `---` lines, as a mapping."""
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
    return dict(header)  # pyright: ignore[reportUnknownArgumentType]


def _keys(node: Any) -> list[str]:
    """Every mapping key at any depth, lists included."""
    out: list[str] = []
    if isinstance(node, Mapping):
        for k, v in node.items():  # pyright: ignore[reportUnknownVariableType]
            out.append(str(k))  # pyright: ignore[reportUnknownArgumentType]
            out.extend(_keys(v))
    elif isinstance(node, list):
        for v in node:  # pyright: ignore[reportUnknownVariableType]
            out.extend(_keys(v))
    return out


def reasons(header: Mapping[str, Any]) -> list[str]:
    """Why a card's front matter marks it, empty when it doesn't."""
    found = sorted({k for k in _keys(header) if k in MARKER_KEYS})
    threat = header.get("threat")
    if isinstance(threat, str) and INJECTION_MARKERS["threat_contains"] in threat.lower():
        found.append("threat")
    expected: object = header.get("expected")
    draft: object = (
        cast("Mapping[str, Any]", expected).get("draft") if isinstance(expected, Mapping) else None
    )
    if isinstance(draft, Mapping):
        group: object = cast("Mapping[str, Any]", draft).get("group")
        if group == INJECTION_MARKERS["draft_group"]:
            found.append("draft.group")
    return found


def is_injection_card(text: str, *, source: str = "card") -> bool:
    """Whether the card text's front matter marks it as an injection card."""
    return bool(reasons(front_matter(text, source=source)))


def is_pinned_starter(card_id: str, data: bytes) -> bool:
    """The exact ID `starter-injection` with its file's pinned bytes (not a `starter-*` prefix)."""
    return card_id == STARTER_ID and hashlib.sha256(data).hexdigest() == STARTER_SHA256
