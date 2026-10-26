"""The hygiene scan's Stage B fix (OD-554): an
address's local part is never read as a domain; lookalike local parts, handles, glued second
addresses and URLs with a dotted name before the `@` are their own findings; NFKC; file
extensions; all-zero numbers; linear patterns; the sender-address brand check; and the
generator's retry advice and refusal-on-retry outcome. No network."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ecf import cli_evalgen
from ecf.errors import InvalidInputError
from ecf.eval import gencheck, tools
from ecf.eval import generate as gen
from ecf.eval import mix as mixmod
from ecf.eval import report as rep
from ecf.eval.builder import BuildReport, scan_card
from ecf.eval.cards import parse_card
from ecf.eval.hygiene import Finding, dedupe, scan_text

LOCAL = "address local part looks like a domain"


def found(text: str) -> set[tuple[str, str]]:
    return {(f.kind, f.value) for f in dedupe(scan_text(text, "b"))}


def kinds(text: str) -> set[str]:
    return {k for k, _v in found(text)}


# B1: an address's local part is never a host


@pytest.mark.parametrize("text", [
    "mark.ellison@lovelink.example", "j.holloway.deployed@x.example", "orders.desk@acme.example",
    "tyler.help@x.example", "tyler.help+promo@x.example", "a.b.c@x.example",
    "Mark Ellison <mark.ellison@lovelink.example>",
])  # fmt: skip
def test_a_dotted_local_part_is_no_finding(text: str) -> None:
    assert found(text) == set()  # each a `domain` finding before OD-554 (the bug)


def test_a_real_address_and_a_sentence_ending_host_stay_findings() -> None:
    assert found("write to a.b@realsite.com") == {("email address", "a.b@realsite.com")}
    assert found("see ubs.ch.") == {("domain", "ubs.ch")}


# A local part that imitates a domain


@pytest.mark.parametrize("local", [
    "paypal.com", "paypal[.]com", "irs.gov", "hmrc.gov.uk", "santander.com.br",
    "paypal.com.security", "account.paypal.com.verify", "paypal.com+tag", "irs.gov+refund",
    "tag+paypal.com", "+paypal.com", "paypal.com-support", "paypal.com2", "santander.com.br2",
    "x.co.jp", "oxford.ac.uk", "paypal.co.uk.support", "paypal.co.uk-support",
    "amazon.co.uk.orders", "co.uk", "paypal.com\u00b2",  # NFKC: `paypal.com2`, rule 2a
])  # fmt: skip
def test_a_lookalike_local_part_is_its_own_finding(local: str) -> None:
    assert LOCAL in kinds(f"{local}@x.example")


def test_the_finding_value_is_the_whole_local_part() -> None:
    assert found("santander.com.br2@x.example") == {(LOCAL, "santander.com.br2")}
    assert found("tag+paypal.com@x.example") == {(LOCAL, "tag+paypal.com")}
    assert found("mailto:paypal.com@x.example") == {(LOCAL, "paypal.com")}


@pytest.mark.parametrize("local", [
    "p\u0430ypal.c\u043em", "canada.\u0441\u0430", "paypal.\u010bom", "paypal.c\u00f8m",
    "irs.\u01e5ov", "paypal.\u0130nfo",
    "paypal.\u00dfcom", "paypal.ne\u1e97", "paypal.c\u0660m",
])  # fmt: skip
def test_rule_1_any_non_ascii_character_before_casefold(local: str) -> None:
    assert LOCAL in kinds(f"{local}@x.example")


@pytest.mark.parametrize("local", [
    "tyler.help", "mark.ellison", "john.co.smith", "de.vries", "anna.de.souza",
    "press.gov_relations", "ops.net_admin", "team.biz_dev", "sam.info_desk", "jane.co-founder",
    "sales.co-ordinator", "sgt.mark.us-army", "jo.de-vries", "maria.de-la-cruz",
    "example.com", "a.example.com", "paypal.com.example",
])  # fmt: skip
def test_ordinary_or_reserved_local_parts_pass(local: str) -> None:
    assert LOCAL not in kinds(f"{local}@x.example")


@pytest.mark.parametrize("local", [
    "paypal.com_support", "paypal.c0m", "gov.br", "co.jp", "paypal.us.security", "paypal.me",
    "chase.bank", "paypal.con", "www.paypal", "\ufb01ona.oneill", "paypal.c\u217f",
])  # fmt: skip
def test_stated_misses_pass(local: str) -> None:
    assert LOCAL not in kinds(f"{local}@x.example")


@pytest.mark.parametrize("local", [
    "sales.uk", "tom.us2", "maria.edu.silva", "support.mil-spec", "anna.co.de-souza",
    "jos\u00e9.garc\u00eda", "jose\u0301.garci\u0301a",  # NFD; composed by NFKC
    "\u674e.\u4f1f", "anna.\u00e9",
])  # fmt: skip
def test_stated_false_alarms_are_findings(local: str) -> None:
    assert LOCAL in kinds(f"{local}@x.example")


def test_a_lookalike_and_a_real_domain_both_count() -> None:
    assert found("paypal.com@realsite.com") == {
        ("email address", "paypal.com@realsite.com"), (LOCAL, "paypal.com")}  # fmt: skip


def test_a_bare_domain_collapses_with_the_local_part_but_a_tagged_one_does_not() -> None:
    assert len(dedupe(scan_text("paypal.com@x.example and paypal.com", "b"))) == 1
    assert len(dedupe(scan_text("paypal.com+tag@x.example and paypal.com", "b"))) == 2


@pytest.mark.parametrize(
    "text",
    [
        "paypal\u0378.com@x.example",
        "paypal\u200b.com@x.example",
        "o'paypal.com@x.example",
        "~paypal.com@x.example",
    ],
)
def test_a_character_outside_the_span_class_ends_the_span(text: str) -> None:
    assert LOCAL in kinds(text)


# NFKC


def test_nfkc_normalises_before_every_pattern() -> None:
    assert (LOCAL, "paypal.com") in found(
        "\uff50\uff41\uff59\uff50\uff41\uff4c\uff0e\uff43\uff4f\uff4d@x.example"
    )
    assert (LOCAL, "paypal.com") in found(
        "\U0001d429\U0001d41a\U0001d432\U0001d429\U0001d41a\U0001d425.\U0001d41c\U0001d428\U0001d426@x.example"
    )
    for dot in ("\u2024", "\ufe52", "\uff0e"):
        assert LOCAL in kinds(f"paypal{dot}com@x.example")
    assert kinds("call 555\u00a00234") == {"phone number"}
    assert "card number (Luhn-valid)" in kinds("4111\u00a01111\u00a01111\u00a01111")
    assert kinds("call \uff12\uff11\uff12-\uff15\uff15\uff15-\uff11\uff12\uff13\uff14") == {
        "phone number"
    }
    assert (
        found("x@\uff50\uff41\uff59\uff50\uff41\uff4c.\uff45\uff58\uff41\uff4d\uff50\uff4c\uff45")
        == set()
    )
    assert found("invoice.\uff50\uff44\uff46") == set()
    assert found("\uff50\uff41\uff59\uff50\uff41\uff4c\uff0e\uff43\uff4f\uff4d") == {
        ("domain", "paypal.com")
    }
    assert found("\u2122paypal.com") == {("domain", "TMpaypal.com")}


@pytest.mark.parametrize(
    "text",
    ["\u2026paypal.com", "\uff0dpaypal.com", "paypal.com\ufe63", "paypal.co\u2122@x.example"],
)
def test_nfkc_stated_limits_pass(text: str) -> None:
    assert found(text) == set()


# Handles after a bare `@`


@pytest.mark.parametrize(
    "text",
    [
        "@paypal.com",
        "@paypal.com2",
        "@irs.gov.uk",
        "header.i=@paypal.com",
        "@mark.ellison.co",
        "@jos\u00e9.garc\u00eda",
    ],
)
def test_a_lookalike_handle_is_a_finding(text: str) -> None:
    assert kinds(text) & {"domain", "non-ASCII domain (homoglyph or IDN)"}


@pytest.mark.parametrize(
    "text",
    [
        "@anna.k.photos",
        "@mark.ellison.art",
        "@tyler.help",
        "@x.example.com",
        "header.i=@x.example",
        "header.i=@mx.example.net",
        "user@realsite.com",
    ],
)
def test_ordinary_handles_pass_and_an_address_is_one_finding(text: str) -> None:
    assert kinds(text) <= {"email address"}
    assert len(found(text)) <= 1


def test_handle_values_strip_dots_and_hyphens_and_stop_at_plus() -> None:
    assert found("@-paypal.com-") == {("domain", "paypal.com")}
    assert found("@paypal.com+tag") == {("domain", "paypal.com")}


# A second address glued to the first


@pytest.mark.parametrize("text, host", [
    ("x@y.example+tag@paypal.com", "paypal.com"),
    ("mailto:a@x.example+b@evil-bank.com", "evil-bank.com"),
    ("x@y.example2@paypal.photos", "paypal.photos"),
    ("x@y.example-@paypal.photos", "paypal.photos"),
    ("x@y.example_@paypal.photos", "paypal.photos"),
    ("x@y.example.@paypal.photos", "paypal.photos"),
    ("x@y.example+@realsite.com", "realsite.com"),
    ("x@y.example+a@paypal.me+bob@z.example", "paypal.me"),
    ("x@y.example+tag@paypal.zip", "paypal.zip"),
    ("x@y.example@paypal.photos", "paypal.photos"),
    ("x@y.example@tyler.help", "tyler.help"),
    ("x@y.example@paypal.com", "paypal.com"),
    ("x@y.example@header.from", "header.from"),
    ("x@@paypal.com", "paypal.com"),
])  # fmt: skip
def test_a_glued_second_address_is_an_email_address_finding(text: str, host: str) -> None:
    assert ("email address", f"@{host}") in found(text)


@pytest.mark.parametrize("text", ["x@y.example@z.example", "x@y.example@home", "x@y.example@2026"])
def test_a_reserved_or_hostless_second_address_passes(text: str) -> None:
    assert found(text) == set()


def test_the_handle_scan_skips_an_at_sign_read_as_a_second_address() -> None:
    assert scan_text("x@y.example@paypal.com2", "b") == [
        Finding("b", "email address", "@paypal.com")]  # fmt: skip
    assert found("x@y.example@x.example.com2") == {("domain", "x.example.com2")}
    assert found("x@y.example@paypal.com.example2") == {("domain", "paypal.com.example2")}


def test_glue_stays_for_the_host_scan() -> None:
    assert found("x@y.example+paypal.com") == {("domain", "paypal.com")}
    assert found("x@y.example+mark.ellison@z.example") == {("domain", "mark.ellison")}
    assert ("email address", "z.example@paypal.com") in found(
        "x@y.example+tag@z.example@paypal.com"
    )
    assert found("x@y.example\u200b@paypal.com") == {("domain", "paypal.com")}


@pytest.mark.parametrize(
    "text",
    [
        "x@y.example+paypal.com2@z.example",
        "bob@x.example.com2",
        "x@y.example+a@z.example+chase.bank@evil.example",
        "com.paypal.exe",
    ],
)
def test_stated_address_misses_pass(text: str) -> None:
    assert found(text) == set()


# URLs with a dotted name before the `@`


@pytest.mark.parametrize("text", [
    "https://chase.bank@evil.example/", "ftp://chase.bank@evil.example/",
    "+https://chase.bank@evil.example/", "-https://chase.bank@evil.example/",
    "1https://chase.bank@evil.example/", "a" * 33 + "https://chase.bank@evil.example/",
    "foo.ftp://u.v@h", "https:///chase.bank@evil.example/", "https:\\\\chase.bank@evil.example",
    "https:/chase.bank@evil.example/", "https[:]//chase.bank@evil.example/",
    "https [:] //chase.bank@evil.example/", "https(:)//chase.bank@evil.example/",
    "hxxps[://]chase.bank@evil.example/", "ftp://ftp://chase.bank@evil.example/",
    "https://x.example:https://chase.bank@evil.example/", "a://b://c.d@e",
    "https://x.example:chase.bank@evil.example/", "https://chase.bank@x.example@evil.example/",
    "https://x.example+chase.bank@evil.example/", "https://chase.bank2@evil.example/",
    "https://evil.example/?next=https://chase.bank@x.example/",
    "https://john.smith@mail.example/", "Re:/Fwd:/john.smith@acme.example", "x.example:/a.b@c",
    "https://j.d@mail.example/", "https://invoice.pdf@evil.example/", "https://.a@evil.example/",
    "https://chase.bank.pdf@evil.example/", "https://x.example~bob@x.example",
])  # fmt: skip
def test_a_url_with_a_dotted_name_before_the_at_sign_is_a_url_finding(text: str) -> None:
    assert "URL" in kinds(text)


@pytest.mark.parametrize("text", [
    "https://evil.example/", "ftp://files.example/", "https://x.example/a@b",
    "ssh://git@acme.example/repo", "https://alice@mail.example/", "C:\\a@b", "note:/x@y",
    "<https://x.example>bob@x.example", "(https://x.example)bob@x.example",
    "https://x.example&bob@x.example", "https://x.example:pw@evil.example/",
    "https://x.example@y.example/", "https://x.example+bob@x.example",
    "see https://x.example,me@y.example",
    "see https://portal.acme.example\u2014billing@acme.example now",
    "https://1.2@evil.example/", "Price:/2.50@acme.example", "Ref:/order.2026@acme.example",
    "https://paypal@evil.example/", "(sftp://u@h", "://a@b", "a://b://c@d",
])  # fmt: skip
def test_urls_without_a_dotted_userinfo_pass(text: str) -> None:
    assert "URL" not in kinds(text)


def test_url_kinds_and_values() -> None:
    assert ("URL", "https://chase.bank@ev") in found("https://chase.bank@ev\u0130l.example/")
    assert "URL" not in kinds("https://chase.bank\u0130@evil.example/")
    assert LOCAL in kinds("https://chase.bank\u0130@evil.example/")
    two = [f for f in scan_text("[https://chase.bank@evil.example]", "b") if f.kind == "URL"]
    assert any("]" not in f.value for f in two)


# The JWT pattern


def test_a_jwt_is_a_secret_whatever_its_length() -> None:
    token = "eyJ" + "a" * 20 + ".eyJ" + "b" * 20 + "." + "c" * 20
    assert "secret-like token" in kinds(f"Bearer {token}")
    long = "eyJ" + "a" * 5000 + ".eyJ" + "b" * 20 + "." + "c" * 20
    assert "secret-like token" in kinds(f"token={long}")
    assert "secret-like token" not in kinds(f"token-{token}")


# File extensions


@pytest.mark.parametrize("name", [
    "report.xlsm", "setup.exe", "macro.docm", "photo.heic", "pack.tar.gz", "Q3.report.zip",
    "tests.test_x.py", "CHANGELOG.2026-10.md", "Invoice.Q3.2026.pdf", "net.py", "info.md",
    "contact.info.pdf", "the.org.chart.pdf", "q3.net.income.xlsx", "thesis.edu.pdf",
    "login.chase.zip", "paypal.net.exe",
])  # fmt: skip
def test_file_names_pass(name: str) -> None:
    assert found(name) == set()


@pytest.mark.parametrize("name", [
    "paypal.com.zip", "irs.gov.zip", "chase.com.md", "www.paypal.com.zip", "paypal.com.exe",
    "irs.gov.docm", "chase.com.js", "paypal.com.tar.gz", "paypal.com.pdf", "my.info.zip",
])  # fmt: skip
def test_a_domain_word_in_a_file_name_is_a_finding(name: str) -> None:
    assert kinds(name) == {"domain"}


# All-zero numbers


@pytest.mark.parametrize(
    "text",
    [
        "routing 000000000",
        "card 0000 0000 0000 0000",
        "card 0000-0000-0000-0000",
        "routing \uff10\uff10\uff10\uff10\uff10\uff10\uff10\uff10\uff10",
        "card " + "0" * 13,
    ],
)
def test_an_all_zero_number_is_no_finding(text: str) -> None:
    assert found(text) == set()


def test_an_all_zero_iban_fails_its_checksum() -> None:
    assert found("GB00 0000 0000 0000 0000 00") == set()  # check digits 00 never pass


def test_real_checksum_numbers_stay_findings() -> None:
    assert kinds("routing 021000021") == {"routing number (valid checksum)"}
    assert "card number (Luhn-valid)" in kinds("4111 1111 1111 1111")
    assert "routing number (valid checksum)" in kinds(
        "routing \u0660\u0660\u0660\u0660\u0660\u0660\u0660\u0660\u0660"
    )
    assert "card number (Luhn-valid)" in kinds("0000 0000 0000 0000 4111 1111 1111 1111")


# Defang


@pytest.mark.parametrize("text, host", [
    ("paypal[.]com", "paypal.com"), ("paypal [.] com", "paypal.com"),
    ("paypal (dot) com", "paypal.com"), ("paypal  dot  com", "paypal.com"),
    ("paypal\ndot\ncom", "paypal.com"),
])  # fmt: skip
def test_defanged_forms_read_as_before(text: str, host: str) -> None:
    assert found(text) == {("domain", host)}


def test_defang_chains_give_the_text_before_od_554() -> None:
    assert found("paypal[.] [.]com") == set()  # `paypal..com`: no host
    assert found("paypal[.]co [.] uk") == {("domain", "paypal.co.uk")}
    assert found("paypal dot co [.] uk") == {("domain", "paypal.co.uk")}
    assert found("the dot com era") == {("domain", "the.com")}  # as before OD-554


# The value cut


def _card(body: str) -> str:
    return ("---\nid: t-1\ntitle: t\nauthor: claude\nprofile: org\n"
            "from: Billing <billing@vendor-a.example>\nsubject: s\ndate: 2026-10-05T09:00:00Z\n"
            "auth_results: []\nbulk: false\nattachments: []\nexpected:\n  labels: {}\n"
            "  facts: {}\n---\n" + body + "\n")  # fmt: skip


def test_values_are_cut_at_200_after_dedupe_in_scan_card_only() -> None:
    text = "https://" + "a.b@" * 300 + "x.example/"
    card = parse_card(_card(text), source="t-1")
    (f,) = [f for f in scan_card(card, "t-1") if f.kind == "URL"]
    assert len(f.value) == 201 and f.value.endswith("\u2026")
    assert max(len(g.value) for g in scan_text(text, "b")) > 1000
    assert len(dedupe(scan_text("a" * 300 + "@realsite.com and realsite.com", "b"))) == 1


# Linear time: each shape at 100 KB, the whole scan; a bound ten times the slowest time measured
# at the build (0.2 s, 2026-10-26) or 0.5 s, whichever is larger (CI's 3.12.6 job runs up to 2.1x
# slower). Before OD-554 the scan took 6-29 s on six of these shapes and didn't finish 4,000
# spaces in 90 s.

N = 100_000
SHAPES: dict[str, Callable[[], str]] = {
    "dotted run": lambda: "ab." * (N // 3),
    "hyphen run": lambda: "ab-" * (N // 3),
    "plus pieces": lambda: "a.+" * (N // 3),
    "extension labels": lambda: "a." * (N // 2) + "zip",
    "one local part": lambda: "a." * (N // 2) + "@x.example",
    "at runs": lambda: "@" * N,
    "at before a dotted run": lambda: "@" + "b." * (N // 2),
    "url userinfo": lambda: "https://" + "a@" * (N // 2),
    "scheme runs": lambda: "-https://" * (N // 9),
    "glued spans": lambda: "a@x.example+" * (N // 12),
    "second addresses": lambda: "x@y.example@" * (N // 12),
    "jwt runs": lambda: "-eyJ" * (N // 4),
    "spaces": lambda: " " * N,
    "newlines": lambda: "\n" * N,
    "nbsp": lambda: "\u00a0" * N,
    "dot runs": lambda: " dot" * (N // 4),
}
BOUND = 2.0


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_whole_scan_is_linear(shape: str) -> None:
    text = SHAPES[shape]()
    t = time.perf_counter()
    scan_text(text, "b")
    assert time.perf_counter() - t < BOUND


def test_an_nfkc_expanding_input_stays_bounded() -> None:
    t = time.perf_counter()
    scan_text("\ufdfa" * (N // 2), "b")  # 18x after NFKC
    assert time.perf_counter() - t < 8.0


def test_the_brand_check_on_a_long_dotted_domain_is_linear() -> None:
    t = time.perf_counter()
    gencheck.named_brand_address("x@" + "ab." * (N // 3) + "example")
    assert time.perf_counter() - t < BOUND


# Brands in the sender and reply-to addresses


@pytest.mark.parametrize("value", [
    "x@paypal.example", "x@wells-fargo.example", "x@bank-of-america.example",
    "x@american-express.example", "x@royal-mail.example", "x@social-security.example",
    "x@us-marshals.example", "x@cash-app.example", "x@paypal_support.example",
    "x@pay-pal.example", "x@micro-soft.example", "x@wellsfargo.example", "x@cost-co.example",
    "x@i.r.s.example", "Pay Team <x@paypal.example>", "a@vendor-a.example, b@paypal.example",
    "PayPal <x@vendor-a.example>", '"PayPal" <x@vendor-a.example>', "PayPal x@vendor-a.example",
    "PayPal: x@vendor-a.example", "PayPal Support",
    "\uff58@\uff50\uff41\uff59\uff50\uff41\uff4c.example",
    "x@paypal\u200b.example", "x@start-ups.example", "x@meta-analysis.example",
    "research@sec-researcher.example", "case-review@treasury-notice.example",
    "x@vendor-a.example+PayPal",
])  # fmt: skip
def test_a_brand_in_a_sender_address_drops(value: str) -> None:
    assert gencheck.named_brand_address(value)


@pytest.mark.parametrize(
    "value",
    [
        "treasury@vendor-a.example",
        "research@acme.example",
        "x@us-team.example",
        "x@paypal2.example",
        "Accounts team",
        "x@pay\u200bpal.example",
        "paypal-support@vendor-a.example",
    ],
)
def test_ordinary_sender_addresses_pass(value: str) -> None:
    assert not gencheck.named_brand_address(value)


# The generator's retry advice and refusal on the retry, with an in-process fake generator

LABELS = {"category": "other", "priority": "low", "requires_action": False,
          "requires_reply": False, "payment_related": False, "deadline_mentioned": False,
          "sender_type": "unknown", "fraud_risk": "none", "bank_detail_change": False,
          "regulator_claim": False, "phishing": False, "claims_internal": False,
          "private_individual": False}  # fmt: skip


class Fake:
    """Seeds as asked; emails whose first pass breaks a rule (`bad`), and a retry answered as
    `retry` says: ok, refuse (the whole reply), refuse_email, error, missing or bad."""

    provider, model = "fake", "fake-model-1"

    def __init__(self, bad: dict[str, str], retry: str) -> None:
        self.bad, self.retry, self.prompts = bad, retry, list[str]()

    def describe(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model}

    def ask(self, prompt: str, schema: dict[str, Any], workdir: Path) -> tools.Reply:
        del schema, workdir
        self.prompts.append(prompt)
        if "Propose" in prompt:
            wanted = re.findall(r"(\d+) with delivery ([a-z-]+)", prompt)
            m = re.search(r"Propose (\d+) seeds", prompt)
            assert m is not None
            total = int(m.group(1))
            types = re.findall(r"^- ([a-z-]+): ", prompt, re.M)
            plan = [d for n, d in wanted for _ in range(int(n))] or ["none"] * total
            seeds = [{"sender_role": f"role {i}", "asks": "pay", "amount": "none",
                      "urgency": "low", "profile": "org", "auth_result": "none",
                      "sender_seen_before": False, "bulk": False, "delivery": d,
                      "ic3_type": types[0] if types else "none"}
                     for i, d in enumerate(plan)]  # fmt: skip
            return tools.Reply("ok", {"refused": False, "seeds": seeds})
        retry = "broke the rules" in prompt
        if retry and self.retry == "refuse":
            return tools.Reply("refused")
        if retry and self.retry == "error":
            return tools.Reply("failed", detail="timeout")
        m = re.search(r"^Seeds: (.*)$", prompt, re.M)
        assert m is not None
        seeds: list[dict[str, Any]] = json.loads(m.group(1))
        emails: list[dict[str, Any]] = []
        for s in seeds:
            if retry and self.retry == "missing":
                continue
            h = hashlib.sha256((json.dumps(s) + str(len(self.prompts))).encode()).hexdigest()
            words = " ".join("".join(chr(97 + int(c, 16)) for c in h)[i:i + 6]
                             for i in range(0, 60, 6))  # fmt: skip
            refuse = retry and self.retry == "refuse_email"
            email: dict[str, Any] = {"seed": s["seed"], "refused": refuse,
                     "from_name": "Billing Desk", "from_address": "billing@vendor-a.example",
                     "reply_to": None, "subject": "Account " + words[:8],
                     "date": "2026-10-05T09:00:00Z",
                     "body": f"Opening {words}.\nPlease reply about {words[::-1]}.",
                     "hidden_text": None, "attachments": [], "labels": LABELS}  # fmt: skip
            if not retry or self.retry == "bad":
                for k, v in self.bad.items():
                    email[k] = (email[k] + v) if k == "body" else v
            emails.append(email)
        return tools.Reply("ok", {"emails": emails})


def _gen(tmp_path: Path, bad: dict[str, str], retry: str) -> tuple[gen.Summary, Path, Fake]:
    root = tmp_path / "set"
    gen.start(root, resume=False)
    fake = Fake(bad, retry)
    summary = gen.Run(root, mixmod.default(), 20, fake, "dev").run(resume=False)
    return summary, root, fake


def _rows(root: Path) -> list[dict[str, Any]]:
    return gen.Work(root).read("outcomes")


def _all_text(root: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file()
                     and p.suffix in {".json", ".jsonl", ".md"})  # fmt: skip


@pytest.mark.parametrize("field", ["from_address", "reply_to"])
@pytest.mark.parametrize("value", [
    "Billing Desk <billing@vendor-a.example>", "<billing@vendor-a.example>", "billing",
    "billing@vendor-a", '"x" billing@vendor-a.example', "billing@vendor-a.example (Desk)",
    "=?utf-8?q?x?= billing@vendor-a.example", "call me",
])  # fmt: skip
def test_an_address_that_isnt_bare_is_a_schema_misfit(tmp_path: Path, field: str,
                                                      value: str) -> None:  # fmt: skip
    summary, _root, _fake = _gen(tmp_path, {field: value}, "bad")
    assert summary.kept == 0 and set(summary.dropped) == {"schema misfit"}


@pytest.mark.parametrize(
    "value", ["billing.desk+q4@vendor-a.example", "A_B%c@mail.vendor-a.example"]
)
def test_a_bare_reply_to_is_kept(tmp_path: Path, value: str) -> None:
    summary, _root, _fake = _gen(tmp_path, {"reply_to": value}, "bad")
    assert summary.kept == 20 and not summary.dropped


def test_a_reply_to_is_written_stripped(tmp_path: Path) -> None:
    summary, root, _fake = _gen(tmp_path, {"reply_to": " billing@vendor-a.example\n"}, "bad")
    assert summary.kept == 20
    assert all('reply_to: "billing@vendor-a.example"' in p.read_text()
               for p in (root / "cases").glob("*.md"))  # fmt: skip


def test_the_generate_gate_names_field_and_kind_but_no_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = BuildReport([], [], [Finding("dev-x-001:body", "domain", "realsite.com")])

    def failed(root: Path) -> BuildReport:
        del root
        return bad

    monkeypatch.setattr(gen, "build_all", failed)
    with pytest.raises(
        InvalidInputError, match="after building the set: dev-x-001:body: domain;"
    ) as e:
        _gen(tmp_path, {}, "ok")
    assert "realsite" not in str(e.value)


def test_safety_rules_and_retry_texts_are_digested() -> None:
    assert "IBANs, card numbers and routing numbers must fail their checksum" in gen.SAFETY_RULES
    assert set(gen.PROMPT_DIGESTS) >= {"retry", "retry_numbers", "retry_ssn", "retry_local"}


@pytest.mark.parametrize("label, value", [
    ("Routing", "021000021"), ("Card", "4111111111111111"), ("IBAN", "DE62370400440532013001"),
])  # fmt: skip
@pytest.mark.parametrize("retry", ["ok", "refuse", "refuse_email", "error", "missing", "bad"])
def test_a_number_retry_quotes_the_value_in_the_prompt_only(tmp_path: Path, retry: str, label: str,
                                                            value: str) -> None:  # fmt: skip
    _s, root, fake = _gen(tmp_path, {"body": f" {label} {value}."}, retry)
    retries = [p for p in fake.prompts if "broke the rules" in p]
    assert retries and all(f"These numbers pass their checksum: {value}." in p for p in retries)
    assert all("Social Security" not in p.split("Seeds:")[-1] for p in retries)
    assert value not in _all_text(root)  # logs, manifest and cases keep kinds only
    m = gen.read_manifest(root)
    for key in ("retry", "retry_numbers", "retry_ssn", "retry_local"):
        assert m["prompts"][key] == gen.PROMPT_DIGESTS[key]


def test_ssn_and_local_part_retries_carry_their_advice(tmp_path: Path) -> None:
    _s, _root, fake = _gen(tmp_path / "a", {"body": " SSN 123-45-6789."}, "ok")
    assert any(gen.RETRY_SSN in p for p in fake.prompts)
    _s, _root, fake = _gen(tmp_path / "b", {"from_address": "paypal.com@vendor-a.example"}, "ok")
    assert any(gen.RETRY_LOCAL in p for p in fake.prompts)
    assert not any("These numbers" in p for p in fake.prompts)


@pytest.mark.parametrize("retry", ["refuse", "refuse_email"])
def test_a_refusal_on_the_retry_is_a_hygiene_drop(tmp_path: Path, retry: str) -> None:
    summary, root, _fake = _gen(tmp_path, {"body": " Routing 021000021."}, retry)
    rows = [r for r in _rows(root) if r.get("refused_on_retry")]
    assert rows and all(r["outcome"] == "dropped" and r["reason"] == "hygiene" for r in rows)
    assert all(set(r["findings"]) == {"first", "retry"} and r["findings"]["retry"] == []
               for r in rows)  # fmt: skip
    m = gen.read_manifest(root)
    assert m["refused_on_retry"] == {
        "count": len(rows),
        "kinds": {"routing number (valid checksum)": len(rows)},
    }
    assert summary.refused_on_retry == len(rows) and not summary.refused
    assert summary.dropped["hygiene"] >= len(rows)


@pytest.mark.parametrize("retry, reason, flag", [
    ("error", "call timeout", "call_failed_on_retry"),
    ("missing", "missing from the answer", "missing_on_retry"),
])  # fmt: skip
def test_a_retry_without_an_answer_keeps_its_reason(tmp_path: Path, retry: str, reason: str,
                                                    flag: str) -> None:  # fmt: skip
    _s, root, _fake = _gen(tmp_path, {"body": " Routing 021000021."}, retry)
    rows = [r for r in _rows(root) if r.get(flag)]
    assert rows and all(r["reason"] == reason for r in rows)
    assert all(set(r["findings"]) == {"first"} for r in rows)


def test_resume_rebuilds_the_refused_on_retry_count(tmp_path: Path) -> None:
    _s, root, _fake = _gen(tmp_path, {"body": " Routing 021000021."}, "refuse")
    m = gen.read_manifest(root)
    run = gen.Run(root, mixmod.default(), 20, Fake({}, "ok"), "dev")
    run._restore()  # pyright: ignore[reportPrivateUsage]
    assert run.refused_on_retry == m["refused_on_retry"]["count"]
    assert dict(run.refused_on_retry_kinds) == m["refused_on_retry"]["kinds"]


def test_an_old_work_folder_resumes(tmp_path: Path) -> None:
    root = tmp_path / "set"
    (root / "work").mkdir(parents=True)
    gen.Work(root).append(
        "outcomes",
        [
            {
                "seed": "x-s000",
                "outcome": "dropped",
                "reason": "hygiene",
                "findings": {"first": ["domain"], "retry": ["domain"]},
            }
        ],
    )
    run = gen.Run(root, mixmod.default(), 20, Fake({}, "ok"), "dev")
    run._restore()  # pyright: ignore[reportPrivateUsage]
    assert run.refused_on_retry == 0


def test_the_cli_line_names_refusals_on_the_retry_only_when_there_are_some() -> None:
    s = gen.Summary("complete", 18, 20, {"hygiene": 2}, {}, "", refused_on_retry=1)
    assert cli_evalgen.closing_line(s) == (
        "complete: kept 18 of 20; refused 0; dropped 2 (1 refused on the retry)"
    )
    assert cli_evalgen.closing_line(gen.Summary("complete", 20, 20, {}, {})).endswith("dropped 0")


def test_the_report_prints_refusals_on_the_retry() -> None:
    doc = {"generator": {"provider": "fake", "model": "m"}, "calls": 1, "seconds": 1.0,
           "groups": {"g": {"kept": 0}}, "count": 1, "dropped": {"hygiene": {"g": 2}},
           "refused_on_retry": {"count": 2, "kinds": {"SSN-like number": 2}}}  # fmt: skip
    lines = rep._plumbing(doc)  # pyright: ignore[reportPrivateUsage]
    assert any("refused on the hygiene retry" in x and "SSN-like number 2" in x for x in lines)
