"""Expected values for a labelled corpus message (SPEC §16.7; R50, R100, R101, R156, R165, R179)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ecf.schema import load_schema
from ecf_server import corpus, evalrun, policy, rules, ruletest
from ecf_server.clock import FakeClock

SCHEMA = load_schema()
RULES = rules.load_starter_rules(SCHEMA)
KNOWN = policy.labels(SCHEMA, RULES)
QUIET = {"category": "marketing", "priority": "low", "requires_action": False,
         "requires_reply": False, "payment_related": False, "deadline_mentioned": False,
         "sender_type": "automated", "fraud_risk": "none"}  # fmt: skip
# R179: over the synthetic set, the derivation misses one card the operator marked "never hide"
# and is stricter on these (mostly mail needing a reply or action, and escalations)
MISSED = {"home-giftcard-thanks"}
STRICTER = {"billing-plan-question", "bug-api-rate-limit", "bug-data-loss-urgent",
            "bug-export-fails", "bug-invoice-export-totals", "bug-mobile-crash",
            "bug-outage-urgent", "ctrl-attorney-engagement", "custreq-data-export",
            "fraud-crypto-extortion", "fraud-mfa-code-request", "look-acme-test-it",
            "partner-reseller-terms", "phish-payment-failed", "reg-compliance-newsletter",
            "sales-city-procurement", "sales-demo-request", "sales-quote-request",
            "sales-reseller-volume",
            # schema v2's personal cards (OD-475): finance, account and shipping mail that asks
            # for something, which the cards don't mark must_not_hide
            "v2-acct-password-reset-requested", "v2-fin-dividend-reinvested",
            "v2-fin-insurance-claim-photos", "v2-fin-insurance-policy-renewed",
            "v2-fin-mortgage-escrow-analysis", "v2-fin-retirement-beneficiary-review",
            "v2-fin-tax-preparer-documents", "v2-ship-missed-delivery-pickup",
            "v2-ship-return-label"}  # fmt: skip


def test_nothing_without_labels_or_facts() -> None:
    assert corpus.expected(None, {"auth_result": "pass"}, RULES, KNOWN) is None
    assert corpus.expected(QUIET, {}, RULES, KNOWN) is None


def test_quiet_mail_may_be_hidden_and_a_reply_may_not() -> None:
    facts = {"auth_result": "pass", "sender_origin": "external", "bulk_signal": True}
    quiet = corpus.expected(QUIET, facts, RULES, KNOWN)
    assert quiet is not None and quiet["safety"] == {"must_escalate": False, "must_not_hide": False}
    reply = corpus.expected(QUIET | {"requires_reply": True}, facts, RULES, KNOWN)
    assert reply is not None and reply["safety"]["must_not_hide"]  # OD-250
    money = corpus.expected(QUIET | {"payment_related": True, "fraud_risk": "high"}, facts, RULES,
                            KNOWN)  # fmt: skip
    assert money is not None and money["rule"] == "fraud_guard"
    assert money["safety"] == {"must_escalate": True, "must_not_hide": True}


def test_agreement_with_the_synthetic_labels() -> None:
    """R179: through the real pipeline, labels as the classification."""
    cases, _ = evalrun.load(Path("tests/eval/synthetic").resolve(), fraud_only=False)
    scratch = ruletest.Scratch(FakeClock())
    missed, stricter = set[str](), set[str]()
    for c in cases:
        exp: dict[str, Any] = c.expected
        card_facts: dict[str, Any] = exp.get("facts") or {}
        facts = scratch.facts(c.path.read_bytes(), c.profile) | card_facts
        labels: dict[str, Any] = exp.get("labels") or {}
        got = corpus.expected(labels, facts, RULES, KNOWN, ruletest.ADDRESS.sensitivity)
        assert got is not None
        safety: dict[str, Any] = exp.get("safety") or {}
        want = bool(safety.get("must_not_hide"))
        if want and not got["safety"]["must_not_hide"]:
            missed.add(c.id)
        if got["safety"]["must_not_hide"] and not want:
            stricter.add(c.id)
    assert missed == MISSED and stricter == STRICTER
