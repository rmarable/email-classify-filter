"""How Jev's probabilities may add caution (v3.0.0 step 3, `planning-docs/v3.0.0-STEP3.md` §3;
step 4's P1): the mapping file's checks and digest, the unsure and below-bar fields, the cautious
view the risk readers get, and the hold, whose output is never less cautious than the plan it
was given (P26)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ecf.errors import InternalError
from ecf_server import jev_mapping, policy, rules
from ecf_server.jev_mapping import Derived, JevMapping
from ecf_server.rules import HIDE_ACTIONS
from tests.test_policy import CONTEXTS, SCHEMA, STARTER

SHIPPED_DIGEST = "756d5a46eeb1a3848103d414122191af8b6a0d2c6ea13d1cc185b7705d01aefa"
EMPTY = JevMapping()


def _doc(**over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {"version": 1, "bands": {}, "bars": {}, "none_bars": {},
                           "score_tie": "higher"}  # fmt: skip
    return doc | over


# The mapping file: what it may hold, and a digest that only its values move.


def test_the_shipped_mapping_is_the_operators_choice() -> None:
    """Chosen 2026-10-14 (OD-512) from the sweep's tables: the four thin safety items 0.30,
    `requires_action` 0.40, no band for `phishing` or `payment_related`; bars `category` 0.6 and
    `sender_type` 0.5; no `fraud_risk` bar. The digest is pinned so a change to the file is a
    deliberate one (it moves Jev's gate key)."""
    m = jev_mapping.load()
    assert dict(m.bands) == {"bank_detail_change": 0.3, "claims_internal": 0.3,
                             "private_individual": 0.3, "regulator_claim": 0.3,
                             "requires_action": 0.4}  # fmt: skip
    assert dict(m.bars) == {"category": 0.6, "sender_type": 0.5} and not m.none_bars
    assert jev_mapping.digest() == SHIPPED_DIGEST
    assert jev_mapping.digest(EMPTY) != SHIPPED_DIGEST


@pytest.mark.parametrize(("over", "why"), [
    ({"bands": {"phishing": 0.5}}, "below 0.5"),  # a band only adds caution under today's yes
    ({"bands": {"phishing": 0.0}}, "above 0.0"),
    ({"bands": {"priority": 0.4}}, "can't name 'priority'"),
    ({"bars": {"category": 1.5}}, "at most 1.0"),
    ({"bars": {"fraud_risk": 0.5}}, "can't name 'fraud_risk'"),
    ({"none_bars": {"category": 0.5}}, "can't name 'category'"),
    ({"bands": {"phishing": True}}, "above 0.0"),
    ({"version": 2}, "version must be 1"),
    ({"score_tie": "lower"}, "higher level only"),
    ({"thresholds": {}}, "unknown keys"),
])  # fmt: skip
def test_a_mapping_that_could_lower_caution_or_names_a_wrong_field_is_refused(
    over: dict[str, Any], why: str
) -> None:
    with pytest.raises(InternalError, match=why):
        jev_mapping.parse(_doc(**over))


def test_the_digest_covers_values_not_formatting() -> None:
    plain = jev_mapping.parse(_doc(bands={"phishing": 0.35}))
    noisy = jev_mapping.parse(json.loads(json.dumps(
        _doc(bands={"phishing": 0.35}, _comment="Changed."), indent=4)))  # fmt: skip
    assert jev_mapping.digest(plain) == jev_mapping.digest(noisy)
    assert jev_mapping.digest(plain) != jev_mapping.digest(EMPTY)
    assert jev_mapping.digest(plain) != jev_mapping.digest(
        jev_mapping.parse(_doc(bands={"phishing": 0.4})))  # fmt: skip


# Unsure and below the bar: only under today's yes, only upward.


CLS = {"phishing": False, "payment_related": True, "requires_action": False,
       "fraud_risk": "none", "category": "marketing", "sender_type": "company"}  # fmt: skip


def _probs(**p: float) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {"fraud_risk": {"none": 0.7, "low": 0.3},
                                        "category": {"marketing": 0.6},
                                        "sender_type": {"company": 0.9}}  # fmt: skip
    for k, v in p.items():
        out[k] = {"true": v}
    return out


@pytest.mark.parametrize(("p", "unsure"), [(0.29, False), (0.3, True), (0.49, True),
                                           (0.5, False), (0.9, False)])  # fmt: skip
def test_a_yes_no_field_is_unsure_only_from_its_bound_up_to_today_s_yes(
    p: float, unsure: bool
) -> None:
    m = jev_mapping.parse(_doc(bands={"phishing": 0.3}))
    d = jev_mapping.derive(CLS | {"phishing": p >= 0.5}, _probs(phishing=p), m)
    assert ("phishing" in d.unsure) is unsure
    assert not d.below_bar


def test_fraud_risk_none_under_its_bar_is_unsure_and_never_lowered() -> None:
    m = jev_mapping.parse(_doc(none_bars={"fraud_risk": 0.8}))
    assert jev_mapping.derive(CLS, _probs(), m).unsure == {"fraud_risk"}  # p(none) 0.7
    assert not jev_mapping.derive(CLS | {"fraud_risk": "low"}, _probs(), m).unsure
    assert jev_mapping.cautious(CLS, Derived(frozenset({"fraud_risk"})))["fraud_risk"] == "low"


def test_a_choice_under_its_bar_or_without_a_probability_is_below_it() -> None:
    m = jev_mapping.parse(_doc(bars={"category": 0.7, "sender_type": 0.7}))
    assert jev_mapping.derive(CLS, _probs(), m).below_bar == {"category"}
    no_probs = jev_mapping.derive(CLS, {}, m)
    assert no_probs.below_bar == {"category", "sender_type"}


def test_a_banded_field_without_a_probability_counts_as_unsure_unless_it_is_yes() -> None:
    m = jev_mapping.parse(_doc(bands={"phishing": 0.3, "payment_related": 0.3}))
    assert jev_mapping.derive(CLS, {}, m).unsure == {"phishing"}  # payment_related is yes


def test_the_cautious_view_reads_every_unsure_field_as_yes() -> None:
    d = Derived(frozenset({"phishing", "requires_action"}))
    view = jev_mapping.cautious(CLS, d)
    assert view["phishing"] is True and view["requires_action"] is True
    assert CLS["phishing"] is False  # the stored classification isn't touched


# The hold: hides dropped, the email left and flagged, the risk readers' flags raised.


def _ctx(cls: dict[str, Any], rs: rules.CompiledRules = STARTER, **facts: Any) -> policy.Context:
    base = {n: (False if s.kind.value == "boolean" else s.values[0])
            for n, s in SCHEMA.fields.items()}  # fmt: skip
    f: dict[str, Any] = {"auth_result": "pass", "sender_seen_before": True,
                         "bulk_corroborates": True, "sender_origin": "external",
                         "triggers": dict[str, Any]()}  # fmt: skip
    f.update(facts)
    return policy.Context(base | cls, f, "standard", rs, {}, frozenset())


def _hold(ctx: policy.Context, d: Derived) -> tuple[policy.Plan, policy.Plan]:
    known = policy.labels(SCHEMA, ctx.rules)
    before = policy.plan(ctx, known)
    after = jev_mapping.hold(ctx, policy.plan(ctx, known), d, known, ctx.rules)
    return before, after


def test_an_unsure_field_drops_every_hide_and_flags() -> None:
    ctx = _ctx({"category": "marketing", "requires_action": False, "fraud_risk": "none"})
    before, after = _hold(ctx, Derived(frozenset({"claims_internal"})))
    assert before.rule_id == after.rule_id == "marketing" and before.hides
    assert not after.hides
    assert {"flag", "leave"} <= {a.name for a in after.actions}
    assert all(d.why == "unsure: claims_internal" for d in after.dropped
               if d.name in HIDE_ACTIONS)  # fmt: skip


def test_unsure_requires_action_keeps_a_marketing_email() -> None:
    _before, after = _hold(_ctx({"category": "marketing", "requires_action": False}),
                           Derived(frozenset({"requires_action"})))  # fmt: skip
    assert not after.hides


def test_unsure_phishing_fires_no_rule_but_blocks_a_users_junk() -> None:
    text = """
version: 1
rules:
  - id: promos_to_junk
    when: {field: category, eq: marketing}
    then: [{label: marketing}, junk]
  - id: otherwise
    actor: continue
"""
    rs = rules.compose(SCHEMA, text, source="custom")
    ctx = _ctx({"category": "marketing", "phishing": False}, rs)
    before, after = _hold(ctx, Derived(frozenset({"phishing"})))
    assert before.rule_id == after.rule_id == "promos_to_junk"  # not the fixed phishing rule
    assert any(a.name == "junk" for a in before.actions)
    assert not any(a.name == "junk" for a in after.actions)
    assert any(d.name == "junk" for d in after.dropped)


def test_unsure_payment_related_counts_as_payment() -> None:
    ctx = _ctx({"payment_related": False, "fraud_risk": "none"})
    before, after = _hold(ctx, Derived(frozenset({"payment_related"})))
    assert before.payment_or_fraud is False and after.payment_or_fraud is True
    assert after.high_risk is True


def test_a_category_under_its_bar_holds_only_the_hides_of_a_rule_that_reads_it() -> None:
    ctx = _ctx({"category": "marketing", "requires_action": False})
    _b, held = _hold(ctx, Derived(below_bar=frozenset({"category"})))
    assert not held.hides
    text = """
version: 1
rules:
  - id: no_opener_to_archive
    when: {fact: bec_opener, eq: false}
    then: [{label: marketing}, archive]
  - id: otherwise
    actor: continue
"""
    facts_only = _ctx({"category": "marketing"}, rules.compose(SCHEMA, text, source="custom"),
                      bec_opener=False)  # fmt: skip
    _b, kept = _hold(facts_only, Derived(below_bar=frozenset({"category"})))
    assert kept.rule_id == "no_opener_to_archive" and kept.hides


def test_a_sender_type_under_its_bar_holds_every_hide() -> None:
    """D4: the hide blockers read `sender_type` (a regulator or a private individual), so below
    its bar it holds the hides of any rule, read by it or not."""
    ctx = _ctx({"category": "marketing", "requires_action": False})
    _b, held = _hold(ctx, Derived(below_bar=frozenset({"sender_type"})))
    assert not held.hides and "flag" in {a.name for a in held.actions}
    assert "sender_type" in jev_mapping.BLOCKER_FIELDS


def test_nothing_derived_leaves_the_plan_as_it_was() -> None:
    ctx = _ctx({"category": "marketing", "requires_action": False})
    before, after = _hold(ctx, jev_mapping.NO_BAND)
    assert before == after


FIELDS = sorted(jev_mapping.BANDABLE | jev_mapping.NONE_BARRABLE)
DERIVED = st.builds(
    Derived,
    unsure=st.frozensets(st.sampled_from(FIELDS)),
    below_bar=st.frozensets(st.sampled_from(sorted(jev_mapping.BARRABLE))),
)


@settings(max_examples=3000, deadline=None)
@given(CONTEXTS, DERIVED)
def test_p26_the_hold_is_never_less_cautious(ctx: policy.Context, d: Derived) -> None:
    """Over the whole space of facts, classifications and rule sets: the hold adds no hide,
    loses no escalation, flag or label (an alert echo planned again may lose its own label) and
    never lowers high risk or payment; an unsure field leaves no hide at all."""
    before, after = _hold(ctx, d)
    names = {a.name for a in after.actions}
    assert {a.name for a in after.hides} <= {a.name for a in before.hides}
    for kept in ("escalate", "flag"):
        assert kept not in {a.name for a in before.actions} or kept in names
    if before.rule_id != "alert_echo":
        assert {a.target for a in before.actions if a.name == "label"} <= {
            a.target for a in after.actions if a.name == "label"}  # fmt: skip
    assert after.high_risk >= before.high_risk
    assert after.payment_or_fraud >= before.payment_or_fraud
    if d.unsure:
        assert not after.hides and "flag" in names
