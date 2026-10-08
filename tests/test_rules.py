from typing import Any

import pytest

from ecf.errors import InvalidInputError
from ecf.schema import extend_schema, load_schema
from ecf_server.rules import Action, Hide, RuleInput, compile_rules, load_starter_rules

SCHEMA = load_schema()
RULES = load_starter_rules(SCHEMA)
BASE: dict[str, Any] = {
    "category": "other",
    "priority": "low",
    "requires_action": False,
    "requires_reply": False,
    "payment_related": False,
    "deadline_mentioned": False,
    "sender_type": "customer",
    "fraud_risk": "none",
}
FACTS = {"auth_result": "pass", "sender_origin": "external"}


def run(triggers: frozenset[str] = frozenset(), facts: dict[str, Any] | None = None, **cls: Any):
    return RULES.evaluate(RuleInput({**BASE, **cls}, {**FACTS, **(facts or {})}, triggers))


def names(d: Any) -> list[tuple[str, str | None]]:
    return [(a.name, a.target) for a in d.actions]


@pytest.mark.parametrize(
    "case",
    [
        {"fraud_risk": "medium"},
        {"fraud_risk": "high"},
        {"category": "vendor_change_request"},
        {"sender_type": "team", "payment_related": True},  # "team" from outside, about money
        {"sender_type": "team", "_facts": {"payment_keyword": True}},
        {"payment_related": True, "_facts": {"auth_result": "fail"}},
        {"_triggers": {"fraud"}},
        {"payment_related": True, "_facts": {"impersonates_internal": True}},  # OD-436
        {"_facts": {"impersonates_internal": True, "payment_keyword": True}},
    ],
)
def test_fraud_guard(case: dict[str, Any]) -> None:
    facts = case.pop("_facts", None)
    trig = frozenset(case.pop("_triggers", set()))
    d = run(trig, facts, **case)
    assert d.rule_id == "fraud_guard"
    assert names(d) == [("label", "suspicious"), ("flag", None), ("escalate", None)]
    assert not d.to_actor and d.hide is Hide.NEVER


def test_staff_internal_is_not_fraud() -> None:
    assert run(facts={"sender_origin": "internal"}, sender_type="team").rule_id == "otherwise"


def test_staff_from_outside_without_money_is_only_flagged() -> None:
    """OD-262 (V1.3 step 12a): a list email the model called "staff" (v1; "team" in v2) escalated
    as fraud."""
    d = run(sender_type="team")
    assert d.rule_id == "fraud_weak"
    assert names(d) == [("label", "suspicious"), ("flag", None)] and d.hide is Hide.NEVER


def test_fraud_weak() -> None:
    d = run(frozenset({"fraud_weak"}), payment_related=True)
    assert d.rule_id == "fraud_weak"
    assert names(d) == [("label", "suspicious"), ("flag", None)]
    assert not d.to_actor and d.hide is Hide.NEVER


def test_impersonation_without_money_is_only_flagged() -> None:
    """OD-436: the money split lives in rules 1 and 1b."""
    d = run(facts={"impersonates_internal": True})
    assert d.rule_id == "fraud_weak" and d.hide is Hide.NEVER


def test_an_executive_impersonation_opener_from_outside_escalates_without_money() -> None:
    """OD-479: "are you at your desk, quick favour, email only" escalates whatever fraud risk the
    model gives it; not from an internal sender or the account's own note to itself."""
    d = run(facts={"bec_opener": True}, sender_type="staff", fraud_risk="low")
    assert d.rule_id == "fraud_guard" and ("escalate", None) in names(d)
    assert d.hide is Hide.NEVER
    assert run(facts={"bec_opener": True, "sender_origin": "internal"}).rule_id == "otherwise"
    assert run(facts={"bec_opener": True, "self_sent": True}).rule_id == "otherwise"


def test_your_own_note_to_yourself_isnt_an_unverified_payment_sender() -> None:
    """OD-446: on Gmail the account's mail to itself is unsigned; Gmail's Sent label says it's
    yours."""
    mine = {"auth_result": "none", "self_sent": True}
    assert run(facts=mine, payment_related=True).rule_id != "unverified_payment_sender"


def test_unverified_payment_sender() -> None:
    d = run(facts={"auth_result": "none"}, payment_related=True)
    assert d.rule_id == "unverified_payment_sender"
    assert names(d) == [("label", "unverified_sender"), ("flag", None)]
    assert run(facts={"auth_result": "pass"}, payment_related=True).rule_id != d.rule_id


def test_regulatory() -> None:
    for d in (run(category="regulatory"), run(frozenset({"regulator"}), category="marketing")):
        assert d.rule_id == "regulatory" and d.hide is Hide.NEVER
        assert ("escalate", None) in names(d)


def test_fraud_beats_regulatory() -> None:
    assert run(frozenset({"regulator", "fraud"})).rule_id == "fraud_guard"


def test_bug_report_conditions() -> None:
    low = run(category="bug_report")
    assert names(low) == [("label", "bug_report")] and not low.to_actor
    high = run(category="bug_report", priority="high", requires_reply=True)
    assert names(high) == [("label", "bug_report"), ("flag", None)] and high.to_actor
    urgent = run(category="bug_report", priority="urgent")
    assert names(urgent) == [("label", "bug_report"), ("flag", None), ("escalate", None)]


def test_invoice() -> None:
    d = run(category="invoice", deadline_mentioned=True, requires_reply=True)
    assert names(d) == [("label", "invoice"), ("flag", None), ("leave", None)] and d.to_actor
    assert names(run(category="invoice")) == [("label", "invoice"), ("leave", None)]


def test_payment_confirmation_and_remittance_label_from_field() -> None:
    for cat in ("payment_confirmation", "remittance"):
        d = run(category=cat)
        assert d.rule_id == "payment_confirmation_remittance"
        assert d.actions == (Action("label", cat),)


def test_hiding_rules() -> None:
    assert names(run(category="spam_or_phishing")) == [
        ("label", "spam_or_phishing"),
        ("junk", None),
    ]
    assert names(run(category="marketing")) == [("label", "marketing"), ("archive", None)]
    assert run(category="marketing", requires_action=True).rule_id != "marketing"
    d = run(category="notification", sender_type="automated")
    assert names(d) == [("label", "notification"), ("mark_read", None), ("archive", None)]
    assert run(category="notification", sender_type="vendor").rule_id == "otherwise"
    for d in (run(category="spam_or_phishing"), run(category="marketing")):
        assert d.hide is Hide.CORROBORATED


def test_requires_reply_and_otherwise() -> None:
    d = run(category="customer_request", requires_reply=True)
    assert d.rule_id == "requires_reply" and d.to_actor
    assert names(d) == [("label", "customer_request"), ("flag", None)]
    d = run(category="sales_inquiry")
    assert d.rule_id == "otherwise" and d.actions == () and d.to_actor


def test_lte() -> None:
    rules = compile_rules(
        "version: 1\nrules:\n"
        "  - {id: calm, when: {field: fraud_risk, lte: low}, then: [leave]}\n"
        "  - {id: rest, actor: continue}\n",
        SCHEMA,
    )
    assert rules.evaluate(RuleInput({**BASE, "fraud_risk": "low"}, FACTS)).rule_id == "calm"
    assert rules.evaluate(RuleInput({**BASE, "fraud_risk": "medium"}, FACTS)).rule_id == "rest"


BAD = {
    "unknown field": "{id: a, when: {field: colour, eq: red}}",
    "gte on enum": "{id: a, when: {field: category, gte: invoice}}",
    "bad value": "{id: a, when: {field: category, eq: taxes}}",
    "boolean needs bool": "{id: a, when: {field: requires_reply, eq: 'yes'}}",
    "unknown fact": "{id: a, when: {fact: mood, eq: happy}}",
    "unknown trigger": "{id: a, when: {trigger: panic}}",
    "trigger with operator": "{id: a, when: {trigger: fraud, eq: true}}",
    "two operands": "{id: a, when: {field: category, fact: auth_result, eq: pass}}",
    "send action": "{id: a, then: [{reply_template: received}]}",
    "draft action": "{id: a, then: [draft_reply]}",
    "label without target": "{id: a, then: [label]}",
    "hide never but archive": "{id: a, then: [archive], hide: never}",
    "bad id": "{id: A-1, then: [leave]}",
    "extra key": "{id: a, then: [leave], priority: 1}",
}


@pytest.mark.parametrize("rule", BAD.values(), ids=BAD.keys())
def test_bad_rules_rejected(rule: str) -> None:
    with pytest.raises(InvalidInputError):
        compile_rules(f"version: 1\nrules:\n  - {rule}\n  - {{id: rest}}\n", SCHEMA)


def test_last_rule_must_catch_all_and_ids_unique() -> None:
    with pytest.raises(InvalidInputError, match="catch-all"):
        compile_rules("version: 1\nrules:\n  - {id: a, when: {trigger: fraud}}\n", SCHEMA)
    with pytest.raises(InvalidInputError, match="duplicate"):
        compile_rules("version: 1\nrules:\n  - {id: a}\n  - {id: a}\n", SCHEMA)


def test_strict_values() -> None:
    for rule in (
        "{id: a, when: {field: requires_reply, eq: 1}}",
        "{id: a, when: {field: category, in: [invoice, 2]}}",
    ):
        with pytest.raises(InvalidInputError):
            compile_rules(f"version: 1\nrules:\n  - {rule}\n  - {{id: rest}}\n", SCHEMA)


def test_label_from_a_field_the_item_lacks_is_dropped() -> None:
    """An item classified before a field existed is still planned, without that label (C2)."""
    rules = compile_rules(
        "version: 1\nrules:\n  - {id: a, then: [{label: {field: category}}, flag]}\n", SCHEMA
    )
    d = rules.evaluate(RuleInput({}, FACTS))
    assert [a.name for a in d.actions] == ["flag"]


def test_v1_values_in_a_rules_file_are_read_as_v2_and_named() -> None:
    """A rules file written for schema v1 still applies (OD-475): `staff` is read as `team`."""
    text = ("version: 1\nrules:\n  - {id: old, when: {field: sender_type, in: [staff]},"
            " then: [flag]}\n  - {id: rest, actor: continue}\n")  # fmt: skip
    rules = compile_rules(text, SCHEMA)
    assert rules.aliased == ("old",)
    assert rules.evaluate(RuleInput({"sender_type": "team"}, FACTS)).rule_id == "old"
    assert compile_rules(text.replace("staff", "team"), SCHEMA).aliased == ()


EXTENDED = extend_schema(SCHEMA, {
    "fields": {
        "contract_stage": {"type": "enum", "description": "Where a contract stands.",
                           "values": {"draft": "A draft.", "signature": "Waiting to sign."}},
        "legal_risk": {"type": "ordinal", "levels": ["none", "low", "high"],
                       "description": "Legal exposure."},
    },
    "category_values": {"legal_notice": "Letter from a lawyer."},
})  # fmt: skip
EXT_RULES = """version: 1
rules:
  - {id: legal, when: {field: legal_risk, gte: low}, then: [flag]}
  - {id: stage, when: {field: contract_stage, in: [draft, signature]},
     then: [{label: {field: contract_stage}}]}
  - {id: notice, when: {field: category, eq: legal_notice}, then: [escalate]}
  - {id: rest, then: [leave]}
"""


def test_rules_on_extension_fields() -> None:
    """Extension fields work in conditions and label-from-field rules; gte/lte use the compiled
    schema's levels (OD-478)."""
    rules = compile_rules(EXT_RULES, EXTENDED)
    full = {**BASE, "contract_stage": "draft", "legal_risk": "none"}
    d = rules.evaluate(RuleInput(full, FACTS))
    assert d.rule_id == "stage" and names(d) == [("label", "draft")]
    assert rules.evaluate(RuleInput({**full, "legal_risk": "high"}, FACTS)).rule_id == "legal"
    notice = {**full, "contract_stage": "none_of_these", "category": "legal_notice"}
    assert rules.evaluate(RuleInput(notice, FACTS)).rule_id == "notice"
    with pytest.raises(InvalidInputError, match="rule legal: unknown field 'legal_risk'"):
        compile_rules(EXT_RULES, SCHEMA)  # without the extension


def test_an_old_row_without_the_extension_fields_evaluates_false() -> None:
    """An item classified before the extension: comparisons on its fields are false, and a label
    from one is dropped (C2)."""
    rules = compile_rules(EXT_RULES, EXTENDED)
    assert rules.evaluate(RuleInput(BASE, FACTS)).rule_id == "rest"
    lbl = compile_rules("version: 1\nrules:\n  - {id: a, then: [{label: {field: contract_stage}},"
                        " flag]}\n", EXTENDED)  # fmt: skip
    assert [a.name for a in lbl.evaluate(RuleInput(BASE, FACTS)).actions] == ["flag"]


def test_not_over_a_missing_extension_field_never_matches_old_mail() -> None:
    """Unknown, not false: `not` over a field an older item lacks doesn't match, so a rule
    meant for new mail can't hide old mail; `and`/`or`/continue_if follow Kleene logic."""
    rules = compile_rules("""version: 1
rules:
  - {id: hide, when: {not: {field: contract_stage, eq: draft}}, then: [archive]}
  - {id: either, when: {or: [{field: priority, eq: high},
                             {not: {field: contract_stage, eq: draft}}]}, then: [flag]}
  - {id: both, when: {and: [{field: priority, eq: low},
                            {not: {field: legal_risk, gte: high}}]}, then: [escalate]}
  - {id: rest, then: [leave], actor: {continue_if: {not: {field: contract_stage, eq: draft}}}}
""", EXTENDED)  # fmt: skip
    old = rules.evaluate(RuleInput(BASE, FACTS))
    assert old.rule_id == "rest" and old.to_actor is False  # nothing matched on unknowns
    assert rules.evaluate(RuleInput({**BASE, "priority": "high"}, FACTS)).rule_id == "either"
    new = {**BASE, "contract_stage": "signature", "legal_risk": "none"}
    assert rules.evaluate(RuleInput(new, FACTS)).rule_id == "hide"  # new mail: known, matches


def test_a_level_the_ordinal_no_longer_has_is_no_match() -> None:
    """A stored value from an older extension's levels doesn't raise in decide or the gate."""
    rules = compile_rules(EXT_RULES, EXTENDED)
    row = {**BASE, "contract_stage": "none_of_these", "legal_risk": "medium"}  # not a level now
    assert rules.evaluate(RuleInput(row, FACTS)).rule_id == "rest"


def test_compile_refuses_a_rule_id_that_is_an_added_value() -> None:
    with pytest.raises(InvalidInputError, match="rule id legal_notice is a value"):
        compile_rules("version: 1\nrules:\n  - {id: legal_notice, then: [leave]}\n", EXTENDED)
    with pytest.raises(InvalidInputError, match="rule id draft is a value"):
        compile_rules("version: 1\nrules:\n  - {id: draft, then: [leave]}\n", EXTENDED)
    compile_rules("version: 1\nrules:\n  - {id: legal_notice, then: [leave]}\n", SCHEMA)
