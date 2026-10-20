"""`ecf eval expect`: a generated card's expected rule and safety, from ecf's rules engine on your
labels (eval generate plan decision 8; P2).

The service runs the rules in force on each built card, as `ecf rules test` does, with the card's
labels standing in for the classifier and its facts over the computed ones, and returns the rule
and the safety flags that outcome implies. Only cards whose 13 labels are all set get them; the
cards are rebuilt, which undoes their confirmations, and you confirm each with `ecf eval label`.
Nothing is filled for a card you haven't labelled.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ecf.errors import InvalidInputError
from ecf.eval import generate as gen
from ecf.eval.builder import build_all
from ecf.eval.cards import parse_card
from ecf.schema import load_schema

_EXPECTED_LINE = re.compile(r"^  (?:rule|safety): .*\n", re.MULTILINE)
_FACTS_LINE = re.compile(r"^(  facts: .*\n)", re.MULTILINE)


def labelled(root: Path) -> set[str]:
    """The cards whose 13 schema fields are all labelled."""
    fields = set(load_schema().fields)
    out: set[str] = set()
    for path in sorted((root / "cases").glob("*.md")):
        card = parse_card(path.read_text(encoding="utf-8"), source=path.name)
        if fields <= set(card.expected.labels):
            out.add(card.id)
    return out


def write(path: Path, rule: str | None, safety: dict[str, bool]) -> bool:
    """The card's `rule` and `safety` lines replaced; True when the card changed."""
    text = path.read_text(encoding="utf-8")
    head, sep, body = text[4:].partition("\n---\n")
    if not text.startswith("---\n") or not sep or not _FACTS_LINE.search(head + "\n"):
        raise InvalidInputError(f"{path.name}: not a generated card's layout")
    lines = f"  rule: {json.dumps(rule)}\n  safety: {json.dumps(safety, sort_keys=True)}\n"
    stripped = _EXPECTED_LINE.sub("", head + "\n")
    new_head = _FACTS_LINE.sub(lambda m: m.group(1) + lines, stripped, count=1).rstrip("\n")
    new = "---\n" + new_head + sep + body
    if new == text:
        return False
    parse_card(new, source=path.name)  # still a valid card
    path.write_text(new, encoding="utf-8")
    return True


def apply(root: Path, cases: list[dict[str, Any]]) -> dict[str, int]:
    """Fill the labelled cards from the service's answer, then rebuild the set."""
    gen.verify(root)
    ready = labelled(root)
    counts = {"filled": 0, "unchanged": 0, "unlabelled": 0, "errors": 0}
    for c in cases:
        if c.get("error"):
            counts["errors"] += 1
            continue
        if c["id"] not in ready:
            counts["unlabelled"] += 1
            continue
        changed = write(root / "cases" / f"{c['id']}.md", c["rule"], dict(c["safety"]))
        counts["filled" if changed else "unchanged"] += 1
    report = build_all(root)
    if report.findings:
        raise InvalidInputError("the rebuilt set failed the hygiene scan; nothing more written")
    return counts
