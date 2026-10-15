"""How Jev's probabilities may add caution (v3.0.0 step 3, `planning-docs/v3.0.0-STEP3.md` §3;
step 4's P1, OD-511; schema doc §4). No transport: the classifier's import closure may use
it (step 4's service readers), so it never imports `jev`, `evaljev`, `evalrun` or httpx (an
import-linter contract).

- **The stored answer doesn't move.** A yes/no field is yes at p >= 0.5 (`systemone.NOUL_TRUE`),
  a score is its most probable level, a choice is Jev's choice, as before step 3. Rules read that.
- **Unsure** (`bands`): a banded yes/no field answered with `lo` <= p < 0.5 is unsure; `fraud_risk`
  answered `none` with p(none) under its bar (`none_bars`) is unsure too. An unsure field fires no
  rule (the stored value is today's), but the readers that test it for risk treat it as yes
  (`fraud_risk` as `low`): `cautious` is the classification they read.
- **Below the bar** (`bars`): a choice field whose chosen value's probability is under its bar.
- **The hold:** on a case with an unsure field, or a below-bar field the matched rule (or the actor,
  or the hide blockers) reads, every hide is dropped and the email is left and flagged, `junk`
  included; never an approve-mode hide. A probability only ever adds caution (P26): nothing here
  adds a hide or removes a flag or escalation. The one label given up: an alert echo (`alert_echo`)
  a cautious reader would refuse is planned again as its rule plans it, so it loses that label.
- **The mapping** is `data/jev_mapping.json`, shipped with the wheel; its digest is in Jev's gate
  key and prompt digest, so changing it restarts gates.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import cache
from importlib import resources
from typing import Any, cast

from ecf.errors import InternalError
from ecf.schema import FIXED_ITEMS, KEPT_SOURCES
from ecf_server import jev_facts, policy, systemone
from ecf_server.jev_facts import NO_BAND, Derived
from ecf_server.rules import HIDE_ACTIONS, CompiledRules, ContinueIf, LabelFrom, Rule, compares

FILE = "jev_mapping.json"
# the yes/no fields a band may cover: the fixed safety items, `payment_related`, and
# `requires_action`, which default rules hide on when it is false (step 3 P11)
BANDABLE = frozenset({*FIXED_ITEMS, "payment_related", "requires_action"})
BARRABLE = frozenset({"category", "sender_type"})  # the choice fields hide-capable rules read
NONE_BARRABLE = frozenset({"fraud_risk"})  # a raise-only bar on p(none) (step 4 P17)
CAUTIOUS = jev_facts.CAUTIOUS  # an unsure field's value for the risk readers; else True
KEYS = frozenset({"_comment", "version", "bands", "bars", "none_bars", "score_tie"})
# the choice fields every hide decision reads, through the hide blockers' fixed items
# (`policy.hide_blockers` -> `fixed_item` -> KEPT_SOURCES: `sender_type`); below its bar, such a
# field holds every hide, whatever rule matched (step 3 diff review D4)
BLOCKER_FIELDS = frozenset(f for sources in KEPT_SOURCES.values() for f, _vals in sources)


@dataclass(frozen=True)
class JevMapping:
    bands: Mapping[str, float] = field(default_factory=dict[str, float])  # field -> lo (< 0.5)
    bars: Mapping[str, float] = field(default_factory=dict[str, float])  # field -> bar (0-1]
    none_bars: Mapping[str, float] = field(default_factory=dict[str, float])  # field -> bar


def _number(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _table(doc: Mapping[str, Any], key: str, allowed: frozenset[str], low: float,
           high: float, *, high_open: bool) -> dict[str, float]:  # fmt: skip
    raw = doc.get(key, {})
    if not isinstance(raw, dict):
        raise InternalError(f"{FILE}: {key} must be an object")
    out: dict[str, float] = {}
    for name, v in cast("dict[str, Any]", raw).items():
        x = _number(v)
        if name not in allowed:
            raise InternalError(f"{FILE}: {key} can't name {name!r} (only {sorted(allowed)})")
        if x is None or not low < x or (x >= high if high_open else x > high):
            bound = f"below {high}" if high_open else f"at most {high}"
            raise InternalError(f"{FILE}: {key}.{name} must be above {low} and {bound}")
        out[name] = x
    return out


def parse(doc: Any) -> JevMapping:
    """A mapping document checked: a band's lower bound is above 0 and below 0.5 (a band may only
    add caution under today's yes, step 4 P1), a bar is above 0 and at most 1, and only the fields
    each may cover."""
    if not isinstance(doc, dict):
        raise InternalError(f"{FILE} must be a JSON object")
    d = cast("dict[str, Any]", doc)
    if unknown := sorted(set(d) - KEYS):
        raise InternalError(f"{FILE}: unknown keys {unknown}")
    if d.get("version", 1) != 1:
        raise InternalError(f"{FILE}: version must be 1")
    if d.get("score_tie", "higher") != "higher":
        raise InternalError(f"{FILE}: score ties go to the higher level only")
    yes = systemone.NOUL_TRUE
    return JevMapping(bands=_table(d, "bands", BANDABLE, 0.0, yes, high_open=True),
                      bars=_table(d, "bars", BARRABLE, 0.0, 1.0, high_open=False),
                      none_bars=_table(d, "none_bars", NONE_BARRABLE, 0.0, 1.0,
                                       high_open=False))  # fmt: skip


def _shipped_doc() -> Any:
    return json.loads(resources.files("ecf_server.data").joinpath(FILE).read_text("utf-8"))


@cache
def load() -> JevMapping:
    """The shipped mapping (`data/jev_mapping.json`)."""
    return parse(_shipped_doc())


def canonical(m: JevMapping) -> dict[str, Any]:
    """What the digest covers: the parsed values, plus the fixed yes threshold and tie rule."""
    return {"noul_true": systemone.NOUL_TRUE, "score_tie": "higher",
            "bands": dict(sorted(m.bands.items())),
            "bars": dict(sorted(m.bars.items())),
            "none_bars": dict(sorted(m.none_bars.items()))}  # fmt: skip


def digest(m: JevMapping | None = None) -> str:
    """SHA-256 of the canonical parsed mapping, so formatting or the comment doesn't move it."""
    raw = json.dumps(canonical(m or load()), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def derive(classification: Mapping[str, Any] | None,
           probabilities: Mapping[str, Mapping[str, float]] | None,
           m: JevMapping | None = None) -> Derived:  # fmt: skip
    """The unsure and below-bar fields of one answer. A field with no probability recorded is
    unsure (or below its bar) when the mapping covers it: it can only add caution."""
    if classification is None:
        return NO_BAND
    m = m or load()
    probs = probabilities or {}
    unsure: set[str] = set()
    for name, lo in m.bands.items():
        if name not in classification:
            continue
        p = _number((probs.get(name) or {}).get("true"))
        yes = systemone.NOUL_TRUE
        if (lo <= p < yes) if p is not None else classification.get(name) is not True:
            unsure.add(name)
    for name, bar in m.none_bars.items():
        if classification.get(name) == "none":
            p = _number((probs.get(name) or {}).get("none"))
            if p is None or p < bar:
                unsure.add(name)
    below: set[str] = set()
    for name, bar in m.bars.items():
        value = classification.get(name)
        if value is None:
            continue
        p = _number((probs.get(name) or {}).get(str(value)))
        if p is None or p < bar:
            below.add(name)
    return Derived(frozenset(unsure), frozenset(below))


def cautious(classification: Mapping[str, Any], derived: Derived) -> dict[str, Any]:
    """The classification the risk readers see: each unsure field as yes (`fraud_risk` as
    `low`, never lowered; `jev_facts.cautious`). Rules read the stored classification, never
    this."""
    return jev_facts.cautious(classification, derived)


def rule_fields(r: Rule) -> set[str]:
    """Every classification field a rule reads: its condition, its actions' conditions, its
    actor condition and any label it takes from a field."""
    conds = [r.when, *(a.if_ for a in r.then)]
    if isinstance(r.actor, ContinueIf):
        conds.append(r.actor.continue_if)
    out = {c.field for cond in conds for c in compares(cond) if c.field is not None}
    return out | {a.target.field for a in r.then if isinstance(a.target, LabelFrom)}


def hide_fields(rules: CompiledRules) -> set[str]:
    """Every classification field a rule that can hide mail (`HIDE_ACTIONS`) reads."""
    out: set[str] = set()
    for r in rules.rules:
        if any(a.action in HIDE_ACTIONS for a in r.then):
            out |= rule_fields(r)
    return out


def reasons(plan: policy.Plan, derived: Derived, rules: CompiledRules) -> list[str]:
    """Why this case's hides are dropped: each unsure field, and each below-bar field the matched
    rule or the hide blockers read (or any, when the actor decides)."""
    why = [f"unsure: {n}" for n in sorted(derived.unsure)]
    if derived.below_bar:
        rule = next((r for r in rules.rules if r.id == plan.rule_id), None)
        read = rule_fields(rule) if rule is not None else set[str]()
        hit = derived.below_bar if plan.to_actor else derived.below_bar & (read | BLOCKER_FIELDS)
        why += [f"below bar: {n}" for n in sorted(hit)]
    return why


def hold(ctx: policy.Context, plan: policy.Plan, derived: Derived, known: frozenset[str],
         rules: CompiledRules) -> policy.Plan:  # fmt: skip
    """`plan` with the caution `derived` asks for: the risk flags as the cautious readers see
    them, and, on a held case, every hide dropped and the email left and flagged. `plan` is
    `policy.plan` over the stored classification (the actor's proposals already added, if any).
    An `alert_echo` plan a cautious reader would refuse is planned again without it."""
    if not derived:
        return plan
    cctx = replace(ctx, classification=cautious(ctx.classification, derived))
    base = plan
    if (
        plan.rule_id == "alert_echo"
        and derived.unsure
        and ("regulatory" in policy.hide_blockers(cctx) or policy.fraud_signal(cctx))
    ):
        base = policy.plan(replace(ctx, facts={**ctx.facts, "alert_echo": False}), known)
    why = reasons(base, derived, rules)
    out = replace(
        base,
        actions=list(base.actions),
        dropped=list(base.dropped),
        high_risk=base.high_risk or policy.high_risk(cctx),
        payment_or_fraud=base.payment_or_fraud or policy.payment_or_fraud(cctx),
    )
    if not why:
        return out
    reason = ", ".join(why)
    kept = [a for a in out.actions if a.name not in HIDE_ACTIONS]
    out.dropped += [policy.Dropped(a.name, a.target, reason)
                    for a in out.actions if a.name in HIDE_ACTIONS]  # fmt: skip
    out.actions = kept
    for name in ("flag", "leave"):
        if all(a.name != name for a in out.actions):
            out.actions.append(policy.Planned(name, None, "auto"))
    return out
