"""Derived signals sent to the classifier (OD-484; ADR 0026, SPEC §7.2).

Each signal is the truth of a condition the starter rules already use (rule 1 and `fraud_weak`,
`starter_rules.yaml`), composed from computed facts, so the model sees what the rules were tuned
to; the two message signals (OD-489) are facts about the message's structure instead, and
`link_text_mismatch` is read by no rule (OD-488: recorded only). A signal is sent only when true,
in the fixed wording below, and nothing else from the facts goes with it: no trigger reasons,
addresses, keywords or sender details (P3, P18). `sent` is the one function that builds them, for
the local classifier and Claude alike (plan item 5); the allow-list test in `tests/test_signals.py`
pins `WORDING`.

For the local classifier they go in a block before the email, between delimiter lines carrying
the request's random token (P10); with no signal true there is no block, so the request is the
same as before. Claude's classifiers get them as `get_message`'s top-level `signals` object
(`as_object`), empty when none is true.
"""

from __future__ import annotations

from typing import Any

WORDING: dict[str, str] = {
    "bec_opener_outside": "An outside sender opens like an executive-impersonation scam.",
    "tax_form_request_outside": "An outside sender asks for employees' tax or payroll data.",
    "impersonates_internal": "The sender claims to be someone in your organization but isn't.",
    "impersonation_gift_cards": "Someone claiming to be in your organization mentions gift cards.",
    # OD-485 (plan item 4): signal only, no rule route. `credential_request` is a fact only (OD-488:
    # as a signal it pulled phishing toward account_security).
    "crypto_payment": "The email asks for payment to a cryptocurrency wallet.",
    # OD-489 (pre-classifier plan step 11, §6 D): message signals, from the message's own structure
    "link_text_mismatch": "A link's visible text names a different site than the link goes to.",
    "risky_attachment": "The email has an attachment that can run code or open a web page.",
}


def sent(facts: dict[str, Any]) -> tuple[str, ...]:
    """The names of the signals that are true for these facts, in `WORDING`'s order."""
    outside = facts.get("sender_origin") == "external" and not facts.get("self_sent")
    impersonates = bool(facts.get("impersonates_internal"))
    true = {
        "bec_opener_outside": bool(facts.get("bec_opener")) and outside,
        "tax_form_request_outside": bool(facts.get("tax_form_request")) and outside,
        "impersonates_internal": impersonates,
        "impersonation_gift_cards": impersonates and bool(facts.get("gift_card_keyword")),
        "crypto_payment": bool(facts.get("crypto_payment")),
        "link_text_mismatch": bool(facts.get("link_text_mismatch")),
        "risky_attachment": bool(facts.get("risky_attachment")),
    }
    return tuple(name for name in WORDING if true[name])


def block(names: tuple[str, ...], token: str) -> str:
    """The signals block for the classifier's user message; empty when no signal is true."""
    if not names:
        return ""
    lines = "".join(f"- {WORDING[n]}\n" for n in names)
    return f"<<<SIGNALS {token}>>>\n{lines}<<<END SIGNALS {token}>>>\n"


def as_object(names: tuple[str, ...]) -> dict[str, str]:
    """The signals as Claude's `get_message` returns them: name to wording, in `WORDING`'s order."""
    return {n: WORDING[n] for n in names}
