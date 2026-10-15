"""What Jev's caution left on an item, as every reader sees it (v3.0.0 step 4 Phase B; OD-511;
plan planning-docs/v3.0.0-STEP4.md §4).

Imports nothing of ecf's: policy, precheck, the queue, review and alert readers import it, and
`jev_mapping` (which imports policy) builds on it, so nothing here can loop back.

- **`facts.jev`**, stored by Jev's drain beside the classification (never inside it: the
  classification is `extra="forbid"`): `unsure` and `below_bar` (field names), `probabilities`
  and `confidence` (numbers only, keyed by field and option name). `jev_mapping.derive` fills the
  two lists; the stored answer stays a yes at p >= 0.5.
- **The cautious view** (`risk_view`): the classification the risk readers read, each unsure
  field as yes (`fraud_risk` as `low`, only ever raised). Rules read the stored classification,
  so an unsure field fires no rule.
- **A reader that finds no `facts.jev`** (preset C, mail from before step 4, a bad shape) reads
  the classification as stored: today's behaviour, never below it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

KEY = "jev"  # `facts.jev`
CAUTIOUS = {"fraud_risk": "low"}  # an unsure field's value for the risk readers; else True
_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class Derived:
    unsure: frozenset[str] = frozenset()
    below_bar: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.unsure or self.below_bar)


NO_BAND = Derived()


def _names(v: Any) -> frozenset[str]:
    return frozenset(x for x in cast("list[Any]", v) if isinstance(x, str)) if isinstance(
        v, list) else frozenset()  # fmt: skip


def derived(facts: Mapping[str, Any] | None) -> Derived:
    """`facts.jev`'s unsure and below-bar fields (none when it is missing or malformed)."""
    doc: Any = (facts or {}).get(KEY)
    if not isinstance(doc, dict):
        return NO_BAND
    d = cast("dict[str, Any]", doc)
    return Derived(_names(d.get("unsure")), _names(d.get("below_bar")))


def cautious(classification: Mapping[str, Any], d: Derived) -> dict[str, Any]:
    """The classification the risk readers see: each unsure field as yes (`fraud_risk` raised to
    `low`, never lowered). Rules read the stored classification, never this."""
    out = dict(classification)
    for name in d.unsure:
        if name in CAUTIOUS:
            want = CAUTIOUS[name]
            if _RANK.get(str(out.get(name)), 0) < _RANK[want]:
                out[name] = want
        else:
            out[name] = True
    return out


def risk_view(classification: Mapping[str, Any], facts: Mapping[str, Any] | None
              ) -> Mapping[str, Any]:  # fmt: skip
    """The classification as the risk readers read it, from the item's own facts."""
    d = derived(facts)
    return cautious(classification, d) if d.unsure else classification


def record(d: Derived, probabilities: Mapping[str, Mapping[str, float]] | None,
           confidence: Mapping[str, float] | None) -> dict[str, Any]:  # fmt: skip
    """`facts.jev` for one answer: names and numbers only (I5, OD-165)."""

    def num(v: Any) -> float | None:
        return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None

    probs = {f: {str(k): x for k, v in (opts or {}).items() if (x := num(v)) is not None}
             for f, opts in (probabilities or {}).items()}  # fmt: skip
    conf = {f: x for f, v in (confidence or {}).items() if (x := num(v)) is not None}
    return {"unsure": sorted(d.unsure), "below_bar": sorted(d.below_bar),
            "probabilities": probs, "confidence": conf}  # fmt: skip


def card_line(facts: Mapping[str, Any] | None) -> str:
    """The card's words for Jev's caution: "unsure: phishing; below bar: category" (or "")."""
    d = derived(facts)
    parts = [f"unsure: {', '.join(sorted(d.unsure))}"] if d.unsure else []
    parts += [f"below bar: {', '.join(sorted(d.below_bar))}"] if d.below_bar else []
    return "; ".join(parts)
