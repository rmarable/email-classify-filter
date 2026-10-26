"""The card scan reads each header field on its own (eval generate plan, Checks; hygiene fix
Stage A0): a value at a line start in hidden text or an attachment's text is found as written,
and every finding names its field. Before, one JSON dump of the header turned each newline into
the characters `\\n`, so line-start patterns missed and a value there took the `n`."""

from __future__ import annotations

from typing import Any

import pytest

from ecf.eval.builder import scan_card
from ecf.eval.cards import Card


def _card(**fields: Any) -> Card:
    return Card.model_validate({"id": "t-1", "title": "test", "from": "A <a@vendor-a.example>",
                                "subject": "Hello", **fields})  # fmt: skip


def _found(card: Card) -> list[tuple[str, str, str]]:
    return [(f.where, f.kind, f.value) for f in scan_card(card, "t-1")]


LINE_START = [
    ("+41 44 123 45 67", "phone number"),
    ("1BoatSLRHtKNngkdXEeobR76b53LETtpyT", "crypto wallet address"),
    ("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", "crypto wallet address"),
    ("sk-" + "A" * 24, "secret-like token"),
    ("sk_live_" + "B" * 16, "secret-like token"),
    ("eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.abcdefgh1234", "secret-like token"),
]


@pytest.mark.parametrize(("value", "kind"), LINE_START)
@pytest.mark.parametrize("field", ["hidden_text", "attachment text"])
def test_a_value_at_a_line_start_is_found(value: str, kind: str, field: str) -> None:
    text = f"Hello there\n{value}\nThanks"
    if field == "hidden_text":
        card, where = _card(hidden_text=text), "t-1:hidden_text"
    else:
        card = _card(attachments=[{"name": "note.txt", "text": text}])
        where = "t-1:attachments[0].text"
    assert (where, kind) in {(w, k) for w, k, _v in _found(card)}


def test_a_value_after_a_newline_is_read_without_the_n() -> None:
    assert _found(_card(hidden_text="See below\nsetup.vendorx")) == [
        ("t-1:hidden_text", "domain", "setup.vendorx")]  # fmt: skip


def test_a_reserved_url_ending_a_header_field_is_no_finding() -> None:
    assert _found(_card(subject="Your statement https://vendor-a.example")) == []
    assert _found(_card(hidden_text="open https://acme.example")) == []


def test_one_domain_in_two_fields_is_two_findings() -> None:
    got = _found(_card(**{"from": "Help <help@ubs.ch>"}, subject="Your UBS.ch statement",
                       hidden_text="ubs.ch"))  # fmt: skip
    assert ("t-1:from", "email address", "help@ubs.ch") in got
    assert ("t-1:subject", "domain", "UBS.ch") in got
    assert ("t-1:hidden_text", "domain", "ubs.ch") in got  # the same kind, another field


def test_header_names_are_still_scanned() -> None:
    # a regression guard: the old dump scanned dict keys too (header names, label names)
    assert _found(_card(headers={"X-Origin.host": "ok"})) == [
        ("t-1:headers", "domain", "X-Origin.host")]  # fmt: skip


def test_the_body_and_html_keep_their_names() -> None:
    card = _card(body="Call 212-555-1234 today\n", html="<p>visit ubs.ch</p>")
    assert {(w, k) for w, k, _v in _found(card)} == {("t-1:body", "phone number"),
                                                    ("t-1:html", "domain")}  # fmt: skip
