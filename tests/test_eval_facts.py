"""The eval's facts as production computes them (M8 plan Step 1, R2): the fetch's own facts in the
scratch, and the fraud triggers that read authentication and sender history recomputed after a
card's expected facts."""

from __future__ import annotations

from typing import Any

import pytest

from ecf.eval.results import ResultFile, input_warnings
from ecf_server import policy, ruletest
from ecf_server import triggers as tr
from ecf_server.clock import FakeClock
from tests.test_triggers import mail

INVOICE = "Please pay the attached invoice by Friday."
KNOWN = {"sender_seen_before": True, "auth_result": "pass"}


def _facts(raw: bytes, profile: str = "org") -> tuple[dict[str, Any], str | None]:
    scratch = ruletest.Scratch(FakeClock())
    try:
        read = ruletest.read_case(raw)
        return scratch.analyze(read, profile), read.parsed.from_addr
    finally:
        scratch.close()


def test_the_scratch_has_the_fetchs_own_facts() -> None:
    """`from_count`, `thread_root` and the rest, as `fetch._store` records them."""
    found, _ = _facts(mail("Hello", extra={"References": "<a@acme.example> <b@acme.example>"}))
    assert found["from_count"] == 1
    assert found["thread_root"] == "<a@acme.example>"
    assert {"size", "mime_defects", "text_truncated", "attachments"} <= found.keys()


def test_a_draft_isnt_refused_for_a_missing_from_count() -> None:
    """Before, every actor draft in the eval was refused as "not exactly one From address"."""
    found, addr = _facts(mail("Can you send the form?"))
    facts = ruletest.with_expected(found, KNOWN, from_addr=addr)
    assert policy.send_refusal("draft_reply", facts, fraud_signal=False) is None


def test_a_known_authenticated_sender_gets_no_first_time_trigger() -> None:
    """R2: the card's known, authenticated sender reaches trigger 2 and `unverified_payment`."""
    found, addr = _facts(mail(INVOICE))
    assert "first-time sender with a payment keyword" in found["triggers"]["fraud_weak"]
    assert found["triggers"]["unverified_payment"] is True
    known = ruletest.with_expected(found, KNOWN, from_addr=addr)
    assert known["triggers"]["fraud_weak"] == []
    assert known["triggers"]["unverified_payment"] is False


def test_a_first_time_sender_keeps_its_trigger() -> None:
    found, addr = _facts(mail(INVOICE))
    again = ruletest.with_expected(found, {"auth_result": "pass"}, from_addr=addr)
    assert again["triggers"]["fraud_weak"] == ["first-time sender with a payment keyword"]
    assert again["triggers"]["unverified_payment"] is False  # authenticated now


def test_dmarc_fail_on_a_payment_item_follows_the_card() -> None:
    """Trigger 4, and trigger 2's second signal with it."""
    found, addr = _facts(mail(INVOICE))
    failed = ruletest.with_expected(found, {"auth_result": "fail"}, from_addr=addr)
    assert tr.DMARC_FAIL_PAYMENT in failed["triggers"]["fraud"]
    assert "first-time sender with a payment keyword and DMARC fail" in failed["triggers"]["fraud"]


def test_the_org_domain_trigger_follows_the_card() -> None:
    """Trigger 6: From uses the organization's domain."""
    found, addr = _facts(mail("Hello", sender="Dana <dana@acme.example>"))
    assert tr.ORG_DOMAIN_UNAUTH in found["triggers"]["fraud"]  # the scratch has no DNS: none
    passed = ruletest.with_expected(found, {"auth_result": "pass"}, from_addr=addr)
    assert tr.ORG_DOMAIN_UNAUTH not in passed["triggers"]["fraud"]


def test_the_org_address_trigger_names_the_from_address() -> None:
    """Trigger 6b, both ways."""
    raw = mail("Hello", sender="Sam <sam-rivera@freemail.example>")
    found, addr = _facts(raw, "freemail")
    named = f"From is {addr}{tr.ORG_ADDRESS_UNAUTH}"
    assert named in found["triggers"]["fraud"]
    passed = ruletest.with_expected(found, {"auth_result": "pass"}, from_addr=addr)
    assert named not in passed["triggers"]["fraud"]
    back = ruletest.with_expected(passed, {"auth_result": "none"}, from_addr=addr)
    assert named in back["triggers"]["fraud"]


def test_reasons_keep_the_order_evaluate_gives_them() -> None:
    """Money first, then lookalike (3), then 4, then the rest."""
    found: dict[str, Any] = {
        "auth_result": "pass", "sender_seen_before": False, "sender_confirmed": False,
        "reply_to_mismatch": False, "recipient_mismatch": False, "from_org_domain": False,
        "auth": {}, "keywords": {"payment": ["invoice"], "bank": [], "change": []},
        "triggers": {"fraud": [f"{tr.LOOKALIKE}vend0r-a.example looks like vendor-a.example",
                               "display name shows x"],
                     "fraud_weak": [], "unverified_payment": False,
                     "lookalikes": ["vend0r-a.example looks like vendor-a.example"]},
    }  # fmt: skip
    out = tr.with_money(found | {"auth_result": "fail"}, from_addr="a@vend0r-a.example")
    assert out["triggers"]["fraud"] == [
        "first-time sender with a payment keyword and DMARC fail and lookalike domain",
        f"{tr.LOOKALIKE}vend0r-a.example looks like vendor-a.example",
        tr.DMARC_FAIL_PAYMENT,
        "display name shows x",
    ]


@pytest.mark.parametrize("body", [INVOICE, "Hello", "Your new bank details are attached."])
def test_unchanged_facts_come_back_unchanged(body: str) -> None:
    """With no card facts, the recompute agrees with `evaluate`: the same object back."""
    found, addr = _facts(mail(body))
    assert tr.with_money(found, from_addr=addr) is found


def _result(**summary: Any) -> ResultFile:
    return ResultFile(run_id="r", pair="p", set_version="v", created_at="t", cases=[],
                      digest="d", summary=summary)  # fmt: skip


def test_compare_warns_across_a_facts_version() -> None:
    """A run from before this change recorded its keywords but no `facts_version`: version 1."""
    old, new = _result(keywords_digest="k"), _result(keywords_digest="k", facts_version=2)
    assert any("facts_version" in w for w in input_warnings(old, new))
    assert input_warnings(new, new) == []
    grades = _result()  # a judges' file records no keywords: nothing to compare
    assert not any("facts_version" in w for w in input_warnings(grades, new))
