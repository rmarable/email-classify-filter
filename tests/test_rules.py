from typing import Any

import pytest

from ecf.errors import InvalidInputError
from ecf.schema import load_schema_v1
from ecf_server.rules import Action, Hide, RuleInput, compile_rules, load_starter_rules

SCHEMA = load_schema_v1()
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
        {"sender_type": "staff"},
        {"payment_related": True, "_facts": {"auth_result": "fail"}},
        {"_triggers": {"fraud"}},
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
    assert run(facts={"sender_origin": "internal"}, sender_type="staff").rule_id == "otherwise"


def test_fraud_weak() -> None:
    d = run(frozenset({"fraud_weak"}), payment_related=True)
    assert d.rule_id == "fraud_weak"
    assert names(d) == [("label", "suspicious"), ("flag", None)]
    assert not d.to_actor and d.hide is Hide.NEVER


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
