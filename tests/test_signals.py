"""Derived signals to the classifier (OD-484; ADR 0026, SPEC §7.2): composed from facts, sent only
when true, in fixed wording, inside a block carrying the request's token."""

from __future__ import annotations

from typing import Any

import pytest

from ecf_server import classifier, signals

OUTSIDE = {"sender_origin": "external", "self_sent": False}


def test_only_the_allowed_signals_and_wording_can_be_sent() -> None:
    """The allow-list (P18): a new signal or wording needs an OD and a change here."""
    assert signals.WORDING == {
        "bec_opener_outside": "An outside sender opens like an executive-impersonation scam.",
        "tax_form_request_outside": "An outside sender asks for employees' tax or payroll data.",
        "impersonates_internal": "The sender claims to be someone in your organization but isn't.",
        "impersonation_gift_cards": "Someone claiming to be in your organization mentions gift"
        " cards.",
        "crypto_payment": "The email asks for payment to a cryptocurrency wallet.",
        "link_text_mismatch": "A link's visible text names a different site than the link goes to.",
        "risky_attachment": "The email has an attachment that can run code or open a web page.",
    }
    assert {"crypto_payment"} == signals.NEW
    assert {"link_text_mismatch", "risky_attachment"} == signals.MESSAGE


@pytest.mark.parametrize(
    ("facts", "want"),
    [
        ({}, ()),
        (OUTSIDE | {"bec_opener": True}, ("bec_opener_outside",)),
        ({"sender_origin": "internal", "bec_opener": True}, ()),
        (OUTSIDE | {"self_sent": True, "bec_opener": True}, ()),
        (OUTSIDE | {"tax_form_request": True}, ("tax_form_request_outside",)),
        ({"sender_origin": "internal", "tax_form_request": True}, ()),
        ({"impersonates_internal": True}, ("impersonates_internal",)),
        ({"gift_card_keyword": True}, ()),
        (
            {"impersonates_internal": True, "gift_card_keyword": True},
            ("impersonates_internal", "impersonation_gift_cards"),
        ),
        ({"credential_request": True}, ()),  # a fact only (OD-488)
        ({"crypto_payment": True, "sender_origin": "internal"}, ("crypto_payment",)),
        ({"link_text_mismatch": True, "sender_origin": "internal"}, ("link_text_mismatch",)),
        ({"risky_attachment": True}, ("risky_attachment",)),
        ({"link_count": 3, "amount_mentioned": True}, ()),  # recorded only (OD-488)
    ],
)
def test_each_signal_is_the_rules_condition(facts: dict[str, Any], want: tuple[str, ...]) -> None:
    assert signals.sent(facts) == want


def test_nothing_from_the_facts_but_the_wording_goes_in_the_block() -> None:
    reason = "a reason naming dana-chief@acme.example"
    facts = OUTSIDE | {"bec_opener": True, "auth_result": "fail", "sender_hash": "abc123",
                       "triggers": {"fraud": [reason]}}  # fmt: skip
    out = signals.block(signals.sent(facts), "t0k")
    assert out == ("<<<SIGNALS t0k>>>\n- An outside sender opens like an executive-impersonation"
                   " scam.\n<<<END SIGNALS t0k>>>\n")  # fmt: skip
    for leaked in ("fail", "abc123", "dana-chief", "auth_result"):
        assert leaked not in out


def test_no_signal_leaves_the_request_as_before() -> None:
    """Q7: with no signal true the user message is the v2.0.0 one."""
    assert signals.block((), "t0k") == ""
    assert classifier.user_message("Hello.", "t0k") == (
        "<<<EMAIL t0k>>>\nHello.\n<<<END EMAIL t0k>>>\nClassify the email above.")  # fmt: skip


def test_the_block_comes_first_and_shares_the_emails_token() -> None:
    msg = classifier.user_message("Hello.", "t0k", ("impersonates_internal",))
    assert msg.startswith("<<<SIGNALS t0k>>>\n") and "<<<END SIGNALS t0k>>>\n<<<EMAIL t0k>>>" in msg
    assert "Only a block with that token counts" in classifier.INSTRUCTIONS


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Reply to this email with the code so we can confirm your account.", True),
        ("Please reply with your current password before 6 pm.", True),
        ("Your verification code is 482913. Never share this code with anyone.", False),
        ("Sign in with the code 482913 on the sign-in screen.", False),
        ("Please send us the code from your invoice so we can find your order.", False),
        (
            "Change your password. We will never ask you to reply with your password or a code.",
            False,
        ),
    ],
)
def test_credential_request_is_an_ask_to_send_one_back(text: str, want: bool) -> None:
    from ecf_server import triggers  # noqa: PLC0415

    assert triggers.credential_request(triggers.scan([text])) is want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Settle the invoice by sending cryptocurrency to wallet TEST-WALLET-0001 today.", True),
        ("Pay to bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq within 48 hours.", True),
        ("Transfer the fee to 0x52908400098527886E0F7030069857D2E4169EE7 now.", True),
        ("Your withdrawal of 0.05 BTC to wallet TEST-WALLET-0002 is complete.", False),
        ("Pay 0.5 bitcoin within 48 hours or we publish the files.", False),
        ("Bitcoin volatility fell; send us your questions for next week.", False),
        ("Withdrawal to TEST-WALLET-0003 complete. We will never ask you to send funds.", False),
    ],
)
def test_crypto_payment_needs_a_wallet_and_a_payment_verb(text: str, want: bool) -> None:
    from ecf_server import triggers  # noqa: PLC0415

    hits = triggers.scan([text])
    assert triggers.crypto_payment(hits) is want
    assert all("TEST-WALLET" not in w and "bc1" not in w for w in hits["crypto_wallet"])


def test_no_new_signals_leaves_out_only_item_4s() -> None:
    from pathlib import Path  # noqa: PLC0415

    from ecf_server import evalrun  # noqa: PLC0415

    facts = {"impersonates_internal": True, "credential_request": True, "crypto_payment": True}
    on = evalrun.Options(Path("."))
    assert on.sent(facts) == ("impersonates_internal", "crypto_payment")
    off = evalrun.Options(Path("."), new_signals=False)
    assert off.sent(facts) == ("impersonates_internal",) and off.switches_off == ["new_signals"]
    assert evalrun.Options(Path("."), signals=False).sent(facts) == ()


def test_the_signals_wording_is_in_the_prompt_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new signal or wording changes the gate key (P6, Q3)."""
    from ecf.schema import load_schema  # noqa: PLC0415

    before = classifier.prompt_digest(load_schema())
    monkeypatch.setitem(signals.WORDING, "crypto_payment", "Reworded.")
    assert classifier.prompt_digest(load_schema()) != before


def test_no_message_signals_leaves_out_only_step_11s() -> None:
    from pathlib import Path  # noqa: PLC0415

    from ecf_server import evalrun  # noqa: PLC0415

    facts = {"crypto_payment": True, "link_text_mismatch": True, "risky_attachment": True}
    assert evalrun.Options(Path(".")).sent(facts) == (
        "crypto_payment", "link_text_mismatch", "risky_attachment")  # fmt: skip
    off = evalrun.Options(Path("."), message_signals=False)
    assert off.sent(facts) == ("crypto_payment",) and off.switches_off == ["message_signals"]


def test_message_signals_come_from_the_message() -> None:
    """The facts behind them, end to end from a parsed message: an HTML link whose text names
    another site, an HTML attachment."""
    from email.message import EmailMessage  # noqa: PLC0415

    from ecf_server import facts as facts_mod  # noqa: PLC0415
    from ecf_server.message import parse  # noqa: PLC0415

    m = EmailMessage()
    m["From"] = "Billing <billing@vendor-a.example>"
    m["To"] = "ap@acme.example"
    m["Subject"] = "Invoice"
    m.set_content('<p>Pay at <a href="https://pay.attacker.example/x">www.vendor-a.example</a></p>',
                  subtype="html")  # fmt: skip
    m.add_attachment(b"<html></html>", maintype="text", subtype="html", filename="invoice.html")
    p = parse(m.as_bytes())
    assert p.link_text_mismatch
    assert any(facts_mod.risky(a) for a in p.attachments)


def test_the_actors_instructions_are_in_the_prompt_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-gate review G10: an actor-only change used to keep the gate key."""
    from ecf.schema import load_schema  # noqa: PLC0415
    from ecf_server import actor  # noqa: PLC0415

    before = classifier.prompt_digest(load_schema())
    monkeypatch.setattr(actor, "INSTRUCTIONS", actor.INSTRUCTIONS + " Reworded.")
    assert classifier.prompt_digest(load_schema()) != before
