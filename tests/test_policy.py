"""Rules and policy after classification (V1.3 step 4a): the security invariants I1-I4 tested with
Hypothesis over the whole classification space and every combination of the facts that matter,
plus the concrete cases of SPEC §8.2, §8.3 and §8.6."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ecf.errors import InvalidInputError
from ecf.schema import (
    FIXED_ITEMS,
    FieldKind,
    extend_schema,
    fixed_item,
    from_v2,
    load_schema,
    load_schema_v2,
    with_fixed,
)
from ecf_server import policy, precheck, rules
from ecf_server.policy import Context, Dropped, Planned
from ecf_server.rules import HIDE_ACTIONS, compile_rules, load_starter_rules

REASONS = ["high_sensitivity", "first_time_sender", "fraud_signal", "payment"]
SCHEMA = load_schema()
STARTER = load_starter_rules(SCHEMA)
LABELS = policy.labels(SCHEMA, STARTER)
FOLDERS = frozenset({"Receipts"})


def _field(spec: Any) -> st.SearchStrategy[Any]:
    if spec.kind is FieldKind.BOOLEAN:
        return st.booleans()
    return st.sampled_from(spec.values)


CLASSIFICATIONS = st.fixed_dictionaries({n: _field(s) for n, s in SCHEMA.fields.items()})
TRIGGERS = st.fixed_dictionaries({
    "fraud": st.sampled_from([[], ["bank_change"]]),
    "fraud_weak": st.sampled_from([[], ["first_time_payment"]]),
    "regulator": st.sampled_from([[], ["fda"]]),
    "unverified_payment": st.booleans(),
    "lookalikes": st.sampled_from([[], ["acme-example.example"]]),
})  # fmt: skip
FACTS = st.fixed_dictionaries({
    "triggers": TRIGGERS,
    "auth_result": st.sampled_from(["pass", "fail", "none"]),
    "sender_origin": st.sampled_from(["internal", "external"]),
    "sender_seen_before": st.booleans(),
    "sender_verified": st.booleans(),
    "bulk_signal": st.booleans(),
    "bulk_corroborates": st.booleans(),
    "content_unscanned": st.booleans(),
    "quarantined": st.booleans(),
    "payment_keyword": st.booleans(),
    # every fact rule 1 reads (pre-classifier plan step 4, P17)
    "impersonates_internal": st.booleans(),
    "bec_opener": st.booleans(),
    "tax_form_request": st.booleans(),
    "self_sent": st.booleans(),
    "gift_card_keyword": st.booleans(),
    "risky_attachment": st.booleans(),  # a hide blocker (pre-classifier plan step 5)
})  # fmt: skip
# A hand-written custom set that tries to hide mail rule 1 escalates: the invariants hold for any
# rules, not only the starter ones (v2.1.0's starter rules are v2.0.0's, unchanged).
CUSTOM_TEXT = """
version: 1
rules:
  - id: impersonators_to_junk
    when: {fact: impersonates_internal, eq: true}
    then: [{label: spam_or_phishing}, junk]
  - id: hide_the_rest
    when: {fact: bec_opener, eq: false}
    then: [{label: marketing}, mark_read, archive]
  - id: otherwise
    actor: continue
"""
CUSTOM = compile_rules(CUSTOM_TEXT, SCHEMA, source="custom")  # on its own, not composed
# composed as `ecf config apply` would put it in force: the fixed rules first (OD-500)
COMPOSED = rules.compose(SCHEMA, CUSTOM_TEXT, source="custom")
RULE_SETS = (STARTER, CUSTOM, COMPOSED)
CONTEXTS = st.builds(
    Context,
    classification=CLASSIFICATIONS,
    facts=FACTS,
    sensitivity=st.sampled_from(["standard", "high"]),
    rules=st.sampled_from(RULE_SETS),
    action_policy=st.sampled_from([{}, {"archive": "approve"}, {"junk": "approve"}]),
    move_folders=st.just(FOLDERS),
    confirmed_category=st.one_of(st.none(), st.sampled_from(SCHEMA.fields["category"].values)),
)


# ---- invariants over the whole space --------------------------------------------------------


@settings(max_examples=3000, deadline=None)
@given(CONTEXTS)
def test_i1_a_hide_survives_only_when_facts_allow_it(ctx: Context) -> None:
    p = policy.plan(ctx, policy.labels(SCHEMA, ctx.rules))
    if p.hides:
        assert ctx.sensitivity == "standard"
        assert not ctx.facts["content_unscanned"] and not ctx.facts["quarantined"]
        assert not ctx.facts["risky_attachment"]
        t = ctx.facts["triggers"]
        assert not (t["fraud"] or t["fraud_weak"] or t["regulator"] or t["lookalikes"])
        assert ctx.classification["fraud_risk"] == "none"
        assert not fixed_item(ctx.classification, "regulator_claim")  # the item or regulator
        assert ctx.facts["bulk_corroborates"] or (
            ctx.facts["auth_result"] == "pass"
            and ctx.confirmed_category == ctx.classification["category"]
        )


@settings(max_examples=2000, deadline=None)
@given(CONTEXTS)
def test_i2_a_pre_check_escalation_always_survives_classification(ctx: Context) -> None:
    p = policy.plan(ctx, policy.labels(SCHEMA, ctx.rules))
    names = {(a.name, a.target) for a in p.actions}
    t = ctx.facts["triggers"]
    # The pre-check's own escalation is queued at fetch and nothing removes it; the plan
    # escalating again is the starter rules' doing, which a custom set may leave out.
    if ctx.rules is STARTER and (t["fraud"] or ctx.facts["quarantined"]):
        assert ("escalate", None) in names and ("label", "suspicious") in names
        assert p.rule_id == "fraud_guard" and not p.to_actor
    elif ctx.rules is STARTER and t["regulator"]:  # rule 2 before 1b and 1a (OD-253)
        assert ("escalate", None) in names and p.rule_id in ("fraud_guard", "regulatory")
    if t["fraud_weak"] or t["regulator"] or t["fraud"]:
        assert not p.hides


@settings(max_examples=2000, deadline=None)
@given(CONTEXTS)
def test_high_risk_is_having_a_reason(ctx: Context) -> None:
    """v2.2.0: the eval's routing figures read the reasons; routing itself is unchanged."""
    reasons = policy.high_risk_reasons(ctx)
    assert policy.high_risk(ctx) is bool(reasons)
    assert list(reasons) == sorted(set(reasons), key=REASONS.index)
    assert ("high_sensitivity" in reasons) is (ctx.sensitivity == "high")
    assert ("first_time_sender" in reasons) is (not ctx.facts.get("sender_seen_before"))
    assert ("fraud_signal" in reasons) is policy.fraud_signal(ctx)
    assert ("payment" in reasons) is policy.payment_or_fraud(ctx)


@settings(max_examples=2000, deadline=None)
@given(CONTEXTS)
def test_i3_risk_is_facts_or_classification(ctx: Context) -> None:
    p = policy.plan(ctx, policy.labels(SCHEMA, ctx.rules))
    if precheck.payment_or_fraud(ctx.facts) or ctx.classification["payment_related"]:
        assert p.payment_or_fraud
    if (ctx.sensitivity == "high" or not ctx.facts["sender_seen_before"]
            or precheck.payment_or_fraud(ctx.facts)):  # fmt: skip
        assert p.high_risk
    if p.high_risk:
        assert all(a.mode == "approve" for a in p.hides)


@settings(max_examples=2000, deadline=None)
@given(CONTEXTS)
def test_i4_every_target_is_known(ctx: Context) -> None:
    labels = policy.labels(SCHEMA, ctx.rules)
    for a in policy.plan(ctx, labels).actions:
        if a.name == "label":
            assert a.target in labels
        if a.name == "move":
            assert a.target in FOLDERS
        assert a.name in {"label", "flag", "escalate", "leave", *HIDE_ACTIONS}


# ---- concrete cases -------------------------------------------------------------------------


def _ctx(**kw: Any) -> Context:
    classification = {"category": "marketing", "priority": "low", "requires_action": False,
                      "requires_reply": False, "payment_related": False,
                      "deadline_mentioned": False, "sender_type": "vendor",
                      "fraud_risk": "none"} | kw.pop("classification", {})  # fmt: skip
    facts = {"triggers": {}, "auth_result": "pass", "sender_seen_before": True,
             "bulk_corroborates": True, "from_count": 1} | kw.pop("facts", {})  # fmt: skip
    return Context(classification, facts, kw.pop("sensitivity", "standard"),
                   kw.pop("rules", STARTER), kw.pop("action_policy", {}), FOLDERS,
                   kw.pop("confirmed_category", None), **kw)  # fmt: skip


def _names(p: policy.Plan) -> list[tuple[str, str | None, str]]:
    return [(a.name, a.target, a.mode) for a in p.actions]


def test_labels_are_dropped_where_the_provider_cant_keep_them() -> None:
    """OD-439: a dropped action with its reason, never an item failure; the rest stands."""
    p = policy.plan(_ctx(keywords_stored=False), LABELS)
    assert _names(p) == [("archive", None, "auto")]
    assert [(d.name, d.why) for d in p.dropped] == [("label", policy.NOT_STORED)]


def test_corroborated_marketing_is_archived_automatically() -> None:
    p = policy.plan(_ctx(), LABELS)
    assert _names(p) == [("label", "marketing", "auto"), ("archive", None, "auto")]


def test_a_risky_attachment_blocks_hiding_and_link_facts_dont() -> None:
    """Pre-classifier plan §5 C (P13): a risky attachment is a hide blocker; the link facts are
    recorded only, since tracking redirects would make most newsletters mismatch."""
    p = policy.plan(_ctx(facts={"risky_attachment": True}), LABELS)
    assert _names(p) == [("label", "marketing", "auto"), ("leave", None, "auto")]
    links = policy.plan(_ctx(facts={"link_text_mismatch": True, "link_count": 40}), LABELS)
    assert ("archive", None, "auto") in _names(links)


def test_the_action_policy_can_ask_for_approval() -> None:
    p = policy.plan(_ctx(action_policy={"archive": "approve"}), LABELS)
    assert ("archive", None, "approve") in _names(p)


def test_uncorroborated_hiding_becomes_label_and_leave_with_an_offer() -> None:
    p = policy.plan(_ctx(facts={"bulk_corroborates": False}), LABELS)
    assert _names(p) == [("label", "marketing", "auto"), ("leave", None, "auto")]
    assert p.offer_confirm and p.dropped == [Dropped("archive", None, "not corroborated")]


def test_a_confirmed_category_corroborates_only_when_it_matches_and_mail_authenticates() -> None:
    no_bulk = {"bulk_corroborates": False}
    ok = policy.plan(_ctx(facts=no_bulk, confirmed_category="marketing"), LABELS)
    assert ("archive", None, "auto") in _names(ok)
    other = policy.plan(_ctx(facts=no_bulk, confirmed_category="notification"), LABELS)
    unauth = policy.plan(_ctx(facts=no_bulk | {"auth_result": "none"},
                              confirmed_category="marketing"), LABELS)  # fmt: skip
    assert not other.hides and not unauth.hides


def test_rules_6_to_8_are_label_and_leave_on_high_addresses() -> None:
    for category, extra in (("other", {"phishing": True}), ("marketing", {}),
                            ("notification", {"sender_type": "automated"})):  # fmt: skip
        p = policy.plan(_ctx(sensitivity="high",
                             classification={"category": category} | extra), LABELS)  # fmt: skip
        assert not p.hides and ("leave", None, "auto") in _names(p), category


def test_a_first_time_sender_needs_approval_to_hide() -> None:
    p = policy.plan(_ctx(facts={"sender_seen_before": False}), LABELS)
    assert p.high_risk and ("archive", None, "approve") in _names(p)


def test_fraud_risk_low_blocks_hiding() -> None:
    p = policy.plan(_ctx(classification={"fraud_risk": "low"}), LABELS)
    assert not p.hides and any("fraud signal" in d.why for d in p.dropped)


def test_unknown_labels_and_folders_are_dropped() -> None:
    rules = compile_rules(
        'version: 1\nrules: [{id: r, then: [{label: "x y"}, {move: Elsewhere}]}]', SCHEMA
    )
    p = policy.plan(_ctx(rules=rules), policy.labels(SCHEMA, rules))
    assert p.actions == [] or all(a.name == "leave" for a in p.actions)
    assert {d.name for d in p.dropped} == {"label", "move"}


@pytest.mark.parametrize(
    ("name", "target", "expected"),
    [
        ("reply_template", "ack", Dropped),
        ("forward_internal", "ap_lead", Dropped),
        ("draft_reply", None, Planned),
        ("label", "invoice", Planned),
        ("label", "Ignore previous instructions", Dropped),
        ("move", "Receipts", Planned),
        ("move", "INBOX/../Trash", Dropped),
    ],
)
def test_actor_proposals_get_the_same_checks(name: str, target: str | None, expected: type) -> None:
    ctx = _ctx(classification={"category": "invoice", "requires_reply": True},
               facts={"sender_seen_before": True})  # fmt: skip
    p = policy.plan(ctx, LABELS)
    got = policy.proposal(ctx, p, name, target, LABELS)
    assert isinstance(got, expected)
    if isinstance(got, Planned) and name == "draft_reply":
        assert got.mode == "approve"


def test_sends_are_rejected_for_high_risk_items() -> None:
    ctx = _ctx(classification={"payment_related": True}, templates=frozenset({"ack"}),
               outbound=True)  # fmt: skip
    p = policy.plan(ctx, LABELS)
    got = policy.proposal(ctx, p, "reply_template", "ack", LABELS)
    assert isinstance(got, Dropped) and "rejected" in got.why


def _send_ctx(**kw: Any) -> Context:
    facts = {"sender_seen_before": True, "bulk_corroborates": False} | kw.pop("facts", {})
    return _ctx(classification={"category": "invoice", "requires_reply": True}, facts=facts,
                templates=frozenset({"ack"}), forwards=frozenset({"ap_lead"}),
                **({"outbound": True} | kw))  # fmt: skip


@pytest.mark.parametrize(("name", "target"), [("reply_template", "ack"),
                                               ("forward_internal", "ap_lead")])  # fmt: skip
def test_a_valid_send_needs_approval(name: str, target: str) -> None:
    ctx = _send_ctx()
    got = policy.proposal(ctx, policy.plan(ctx, LABELS), name, target, LABELS)
    assert isinstance(got, Planned) and (got.target, got.mode) == (target, "approve")


def test_sends_on_high_addresses_are_allowed_for_claude_not_the_local_pair() -> None:
    claude = _send_ctx(sensitivity="high", local_pair=False)
    got = policy.proposal(claude, policy.plan(claude, LABELS), "reply_template", "ack", LABELS)
    assert isinstance(got, Planned)
    local = _send_ctx(sensitivity="high")
    got = policy.proposal(local, policy.plan(local, LABELS), "reply_template", "ack", LABELS)
    assert isinstance(got, Dropped) and "local_high_risk" in got.why


@pytest.mark.parametrize(
    ("name", "target", "facts", "why"),
    [
        ("reply_template", "nope", {}, "isn't an enabled template"),
        ("forward_internal", "nope", {}, "allow-list"),
        ("reply_template", "ack", {"auth_result": "none"}, "authenticated"),
        ("reply_template", "ack", {"from_count": 2}, "one From"),
        ("draft_reply", None, {"from_count": 0}, "one From"),
        ("forward_internal", "ap_lead", {"bulk_signal": True}, "bulk"),
        ("forward_internal", "ap_lead", {"content_unscanned": True}, "scanned"),
        ("reply_template", "ack", {"triggers": {"fraud_weak": ["x"]}}, "fraud signal"),
    ],
)  # fmt: skip
def test_send_guardrails(name: str, target: str | None, facts: dict[str, Any], why: str) -> None:
    ctx = _send_ctx(facts=facts, local_pair=False)
    got = policy.proposal(ctx, policy.plan(ctx, LABELS), name, target, LABELS)
    assert isinstance(got, Dropped) and why in got.why, got


def test_outbound_off_suppresses_and_flags() -> None:
    ctx = _send_ctx(outbound=False)
    p = policy.plan(ctx, LABELS)
    got = policy.proposal(ctx, p, "reply_template", "ack", LABELS)
    assert isinstance(got, Dropped) and "suppressed" in got.why
    assert p.suppressed == "reply_template" and ("flag", None, "auto") in _names(p)
    draft = policy.proposal(ctx, p, "draft_reply", None, LABELS)  # drafts never wait for it
    assert isinstance(draft, Planned) and draft.mode == "approve"


def test_labels_include_what_the_extension_adds() -> None:
    """Added category values, and the values of extension enum fields a rule labels from, are
    labels ecf may write; a shipped field's values are not (OD-478)."""
    ext = extend_schema(SCHEMA, {
        "fields": {"contract_stage": {"type": "enum", "description": "Where a contract stands.",
                                      "values": {"draft": "A draft.", "signature": "To sign."}},
                   "region": {"type": "enum", "description": "Region.",
                              "values": {"emea": "EMEA.", "apac": "APAC."}}},
        "category_values": {"legal_notice": "Letter from a lawyer."},
    })  # fmt: skip
    rules = compile_rules("version: 1\nrules:\n  - {id: a, then: [{label: {field: contract_stage}},"
                          " {label: {field: sender_type}}]}\n", ext)  # fmt: skip
    got = policy.labels(ext, rules)
    assert {"draft", "signature", "legal_notice"} <= got
    assert not {"emea", "apac"} & got  # no rule labels from region
    assert "vendor" not in got  # sender_type isn't an extension field
    assert "legal_notice" not in LABELS and "draft" not in LABELS


# ---- v3.0.0 S3: fixed items, fixed rules (OD-500; plan §5 invariants) -------------------------

FIXED = rules.load_fixed_rules(SCHEMA)
# preset A's path (S3b): the local model answers v2, which still has the removed values; the
# composed rules compiled on v2 against the v2 starter rules
V2 = load_schema_v2()
V2_COMPOSED = load_starter_rules(V2)
V2_STARTER = rules.load_v2_starter(V2)
KEPT_SOURCES = (
    "regulator",
    "friend",
    "family",
)  # the fixed rules' new kept-source clauses (P1, P2)


def _blocked_by_items(ctx: Context) -> bool:
    c = ctx.classification
    return (
        fixed_item(c, "bank_detail_change")
        or fixed_item(c, "regulator_claim")
        or (fixed_item(c, "claims_internal") and ctx.facts["sender_origin"] == "external")
    )


@settings(max_examples=3000, deadline=None)
@given(CONTEXTS)
def test_s3_inv1_the_fixed_items_block_every_hide(ctx: Context) -> None:
    """Invariant 1: a bank detail change, a regulator claim (or sender_type regulator), or a
    claimed colleague from outside: no hide survives, for any rule set and any proposal."""
    p = policy.plan(ctx, policy.labels(SCHEMA, ctx.rules))
    if _blocked_by_items(ctx):
        assert not p.hides
        for name in sorted(HIDE_ACTIONS):
            got = policy.proposal(ctx, p, name, "Receipts" if name == "move" else None,
                                  policy.labels(SCHEMA, ctx.rules))  # fmt: skip
            assert isinstance(got, Dropped), name


@settings(max_examples=3000, deadline=None)
@given(CONTEXTS)
def test_s3_inv2_phishing_is_only_ever_junked_with_approval(ctx: Context) -> None:
    """Invariant 2 (P6): phishing: no mark_read, archive or move survives; junk only when
    corroborated, with no other blocker, and in mode approve."""
    if not fixed_item(ctx.classification, "phishing"):
        return
    labels = policy.labels(SCHEMA, ctx.rules)
    p = policy.plan(ctx, labels)
    proposed = [policy.proposal(ctx, p, n, "Receipts" if n == "move" else None, labels)
                for n in sorted(HIDE_ACTIONS)]  # fmt: skip
    for a in [*p.hides, *(x for x in proposed if isinstance(x, Planned))]:
        assert a.name == "junk" and a.mode == "approve"
        assert policy.corroborated(ctx) and policy.hide_blockers(ctx, "junk") == []


@settings(max_examples=2000, deadline=None)
@given(CLASSIFICATIONS, FACTS, st.sampled_from([STARTER, COMPOSED]))
def test_s3_inv3_a_fixed_rule_that_matches_decides(
    cls: dict[str, Any], facts: dict[str, Any], rule_set: rules.CompiledRules
) -> None:
    """Invariant 3 (and 4): in every composed set, the first fixed rule whose `when` holds
    decides; every composed set ends in a catch-all, so evaluating never raises."""
    inp = policy.rule_input(_ctx(classification=cls, facts=facts))
    assert rule_set.rules[-1].when is None
    got = rule_set.evaluate(inp)
    try:
        first = FIXED.evaluate(inp).rule_id
    except InvalidInputError:  # no fixed rule matched
        assert got.rule_id not in rules.fixed_ids()
    else:
        assert got.rule_id == first


@settings(max_examples=2000, deadline=None)
@given(CONTEXTS, st.sampled_from(FIXED_ITEMS))
def test_s3_inv5_a_missing_fixed_item_is_false(ctx: Context, item: str) -> None:
    """Invariant 5 (step 1c P13): a classification without a fixed item is planned as with it
    false."""
    labels = policy.labels(SCHEMA, ctx.rules)
    absent = {k: v for k, v in ctx.classification.items() if k != item}
    a = policy.plan(dataclasses.replace(ctx, classification=absent), labels)
    b = policy.plan(dataclasses.replace(ctx, classification=absent | {item: False}), labels)
    assert (a.rule_id, a.actions, a.dropped) == (b.rule_id, b.actions, b.dropped)


V2_CLASSIFICATIONS = st.fixed_dictionaries({n: _field(s) for n, s in V2.fields.items()})


@settings(max_examples=3000, deadline=None)
@given(V2_CLASSIFICATIONS, FACTS)
def test_s3_inv6_the_composed_rules_decide_as_the_v2_starter_rules(
    cls: dict[str, Any], facts: dict[str, Any]
) -> None:
    """Invariant 6 (S3a; from S3b on preset A's path): when the items are derived from a v2
    answer (`with_fixed`), the composed rules (no bridge clauses from S3b) decide exactly as the
    v2 starter rules did, but for the expected causes: the kept-source clauses on sender_type
    regulator, friend and family."""
    if cls["sender_type"] in KEPT_SOURCES:
        return
    inp = policy.rule_input(_ctx(classification=with_fixed(cls), facts=facts, rules=V2_COMPOSED))
    assert V2_COMPOSED.evaluate(inp) == V2_STARTER.evaluate(inp)


@settings(max_examples=2000, deadline=None)
@given(V2_CLASSIFICATIONS, FACTS)
def test_s3b_from_v2_plans_as_the_v2_answer_on_v3(cls: dict[str, Any], facts: dict[str, Any]
                                                 ) -> None:  # fmt: skip
    """S3b (D3): a v2 answer brought to v3 (`from_v2`: items derived, removed values read as
    other/unknown) reaches the same fixed rule on the v3 rules as the v2 answer with its items
    on v2's: reading a removed value as `other` or `unknown` loses no fixed-rule decision."""
    on_v2 = V2_COMPOSED.evaluate(
        policy.rule_input(_ctx(classification=with_fixed(cls), facts=facts, rules=V2_COMPOSED))
    )
    on_v3 = STARTER.evaluate(policy.rule_input(_ctx(classification=from_v2(cls), facts=facts)))
    if on_v2.rule_id in rules.fixed_ids():
        assert on_v3.rule_id == on_v2.rule_id
    else:
        assert on_v3.rule_id not in rules.fixed_ids()


@pytest.mark.parametrize(
    ("cls", "facts", "why"),
    [
        ({"bank_detail_change": True}, {}, "bank detail change"),
        (from_v2({"category": "vendor_change_request"}), {}, "bank detail change"),  # S3b
        ({"regulator_claim": True}, {}, "regulatory"),
        ({"sender_type": "regulator"}, {}, "regulatory"),  # kept source
        ({"claims_internal": True}, {"sender_origin": "external"},
         "claims to be a colleague, from outside"),
        (from_v2({"sender_type": "team"}), {"sender_origin": "external"},
         "claims to be a colleague, from outside"),  # S3b: the item from_v2 derives
        ({"phishing": True}, {}, "phishing"),
    ],
)  # fmt: skip
def test_s3_each_fixed_item_is_a_hide_blocker(cls: dict[str, Any], facts: dict[str, Any],
                                              why: str) -> None:  # fmt: skip
    ctx = _ctx(classification=cls, facts=facts, rules=CUSTOM)  # tries to hide everything
    assert why in policy.hide_blockers(ctx, "archive")
    got = policy.proposal(ctx, policy.plan(ctx, LABELS), "archive", None, LABELS)
    assert isinstance(got, Dropped) and why in got.why


def test_s3_a_claimed_colleague_from_inside_doesnt_block() -> None:
    ctx = _ctx(classification={"claims_internal": True}, facts={"sender_origin": "internal"})
    assert policy.hide_blockers(ctx, "archive") == []


def test_s3_phishing_can_be_junked_but_only_with_approval() -> None:
    """P6: a corroborated phishing email (a compromised, confirmed sender) is junked only with
    approval; the other hides are refused."""
    ctx = _ctx(classification={"category": "account_security", "phishing": True},
               facts={"sender_origin": "external"})  # fmt: skip
    p = policy.plan(ctx, LABELS)
    assert p.rule_id == "spam_or_phishing"  # before the default account_security rule
    assert ("junk", None, "approve") in _names(p)
    assert "phishing" not in policy.hide_blockers(ctx, "junk")
    got = policy.proposal(ctx, p, "archive", None, LABELS)
    assert isinstance(got, Dropped) and "phishing" in got.why
    plain = policy.plan(_ctx(classification={"category": "marketing"}), LABELS)
    assert ("archive", None, "auto") in _names(plain)  # no phishing: unchanged


def test_s3_labels_without_a_category_field() -> None:
    """A schema without `category` (S4): no category labels, and nothing raises."""
    no_cat = dataclasses.replace(SCHEMA, fields={k: v for k, v in SCHEMA.fields.items()
                                                 if k != "category"})  # fmt: skip
    got = policy.labels(no_cat, STARTER)
    assert "suspicious" in got and "shipping" in got  # a rule's own label stays
    assert "remittance" not in got
