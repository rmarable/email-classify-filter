"""DKIM, DMARC and the DNS cache (V1.1 step 7). Messages are signed with dkimpy for real; DNS
answers come from a table, so no test touches the network."""

from __future__ import annotations

import base64
import email.policy
import importlib
import sqlite3
import subprocess
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from ecf_server import senderauth as sa
from ecf_server.clock import FakeClock
from ecf_server.dnscache import Answer, DnsCache
from ecf_server.message import parse, parse_partial

dkim: Any = importlib.import_module("dkim")
nacl_signing: Any = importlib.import_module("nacl.signing")


@dataclass
class FakeDns:
    table: dict[tuple[str, str], Answer] = field(default_factory=dict[tuple[str, str], Answer])
    asked: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    def __call__(self, name: str, rtype: str) -> Answer:
        self.asked.append((name, rtype))
        return self.table.get((name, rtype), Answer("nxdomain", ttl=300))

    def txt(self, name: str, *records: str, ttl: int = 3600) -> None:
        self.table[(name, "TXT")] = Answer("ok", records, ttl)


@dataclass
class Key:
    selector: str
    domain: str
    private: bytes
    record: str
    algorithm: bytes = b"ed25519-sha256"


def ed25519_key(domain: str, selector: str = "s1") -> Key:
    sk = nacl_signing.SigningKey.generate()
    public = base64.b64encode(bytes(sk.verify_key)).decode()
    return Key(selector, domain, base64.b64encode(bytes(sk)), f"v=DKIM1; k=ed25519; p={public}")


def rsa_key(domain: str, tmp: Path, selector: str = "r1") -> Key:
    pem = tmp / "k.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(pem), "2048"], check=True, capture_output=True)
    der = subprocess.run(
        ["openssl", "rsa", "-in", str(pem), "-pubout", "-outform", "DER"],
        check=True,
        capture_output=True,
    ).stdout
    return Key(
        selector,
        domain,
        pem.read_bytes(),
        f"v=DKIM1; k=rsa; p={base64.b64encode(der).decode()}",
        b"rsa-sha256",
    )


SIGNED = [
    b"from",
    b"subject",
    b"date",
    b"to",
    b"content-type",
    b"mime-version",
    b"content-transfer-encoding",
]


def mail(sender: str = "billing@vendor-a.example", body: str = "Invoice 42 attached.\n") -> bytes:
    m = EmailMessage()
    m["From"] = f"Vendor A <{sender}>"
    m["To"] = "ap@acme.example"
    m["Subject"] = "Invoice 42"
    m["Date"] = "Mon, 28 Sep 2026 12:00:00 +0000"
    m["Message-ID"] = "<i42@vendor-a.example>"
    m.set_content(body)
    return m.as_bytes(policy=m.policy.clone(linesep="\r\n"))


def sign(raw: bytes, key: Key, headers: list[bytes] | None = None, length: bool = False) -> bytes:
    sig: bytes = dkim.sign(
        raw,
        key.selector.encode(),
        key.domain.encode(),
        key.private,
        include_headers=headers or SIGNED,
        signature_algorithm=key.algorithm,
        length=length,
    )
    return sig + raw


def publish(dns: FakeDns, key: Key) -> None:
    dns.txt(f"{key.selector}._domainkey.{key.domain}", key.record)


@pytest.fixture
def dns() -> FakeDns:
    return FakeDns()


def check(conn: sqlite3.Connection, clock: FakeClock, fake: FakeDns, raw: bytes) -> sa.AuthOutcome:
    return sa.evaluate(raw, parse(raw), DnsCache(conn, clock, lookup=fake))


# ---- pass and alignment ---------------------------------------------------------------------


def test_aligned_signature_passes(conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns) -> None:
    key = ed25519_key("vendor-a.example")
    publish(dns, key)
    dns.txt("_dmarc.vendor-a.example", "v=DMARC1; p=reject")
    out = check(conn, clock, dns, sign(mail(), key))
    assert out.result == "pass" and out.mime_headers_signed is True
    facts = out.facts()
    assert facts["auth_result"] == "pass" and facts["auth"]["dkim_domains_valid"] == [
        "vendor-a.example"
    ]
    assert facts["auth"]["dmarc_policy"]["effective"] == "reject"


def test_rsa_signature_passes(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns, tmp_path: Path
) -> None:
    key = rsa_key("vendor-a.example", tmp_path)
    publish(dns, key)
    assert check(conn, clock, dns, sign(mail(), key)).result == "pass"


def test_relaxed_and_strict_alignment(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns
) -> None:
    key = ed25519_key("vendor-a.example")
    publish(dns, key)
    dns.txt("_dmarc.vendor-a.example", "v=DMARC1; p=reject")
    raw = sign(mail("billing@pay.vendor-a.example"), key)
    assert check(conn, clock, dns, raw).result == "pass"  # same Organizational Domain
    dns.txt("_dmarc.vendor-a.example", "v=DMARC1; p=reject; adkim=s")
    clock.advance(601)  # past the cache cap, so the changed record is read
    out = check(conn, clock, dns, raw)
    assert out.result == "none" and "aligned" in out.reason


def test_unrelated_signer_is_not_aligned(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns
) -> None:
    key = ed25519_key("mailer.test")  # a mail service signing as itself
    publish(dns, key)
    dns.txt("_dmarc.vendor-a.example", "v=DMARC1; p=reject")
    out = check(conn, clock, dns, sign(mail(), key))
    assert out.result == "none" and out.reason == "no DKIM signature aligned with the From domain"
    assert out.signatures[0].outcome == "valid" and not out.signatures[0].aligned


def test_no_dmarc_record_still_passes_on_exact_alignment(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns
) -> None:
    key = ed25519_key("vendor-a.example")
    publish(dns, key)
    out = check(conn, clock, dns, sign(mail(), key))
    assert out.result == "pass" and out.policy is None


# ---- strictness (OD-047, OD-187) ------------------------------------------------------------


def test_l_tag_is_not_a_pass(conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns) -> None:
    key = ed25519_key("vendor-a.example")
    publish(dns, key)
    out = check(conn, clock, dns, sign(mail(), key, length=True))
    assert out.signatures[0].outcome == "valid" and out.signatures[0].has_l
    assert out.result == "none" and "l=" in out.reason


def test_unsigned_subject_is_not_a_pass(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns
) -> None:
    key = ed25519_key("vendor-a.example")
    publish(dns, key)
    out = check(conn, clock, dns, sign(mail(), key, headers=[b"from", b"date", b"to"]))
    assert out.result == "none" and "subject" in out.reason


def test_an_extra_unsigned_copy_is_not_a_pass(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns
) -> None:
    key = ed25519_key("vendor-a.example")
    publish(dns, key)
    raw = sign(mail(), key).replace(
        b"Subject: Invoice 42", b"Subject: URGENT new bank\r\nSubject: Invoice 42"
    )
    out = check(conn, clock, dns, raw)
    assert out.result == "none" and "subject" in out.signatures[0].unsigned_required


def test_oversigning_is_fine(conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns) -> None:
    key = ed25519_key("vendor-a.example")
    publish(dns, key)
    headers = [*SIGNED, b"subject", b"reply-to"]  # listed more often than present: allowed
    assert check(conn, clock, dns, sign(mail(), key, headers=headers)).result == "pass"


def test_unsigned_mime_headers_still_pass_but_are_recorded(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns
) -> None:
    key = ed25519_key("vendor-a.example")
    publish(dns, key)
    out = check(conn, clock, dns, sign(mail(), key, headers=[b"from", b"subject", b"date", b"to"]))
    assert out.result == "pass" and out.mime_headers_signed is False


# ---- fail and none --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "want"),
    [
        ("v=DMARC1; p=reject", "fail"),
        ("v=DMARC1; p=quarantine", "fail"),
        ("v=DMARC1; p=none", "none"),
        ("v=DMARC1; p=quarantine; t=y", "none"),  # quarantine in test mode applies none
        ("v=DMARC1; p=reject; t=y", "fail"),  # reject in test mode applies quarantine
    ],
)
def test_tampered_mail(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns, record: str, want: str
) -> None:
    key = ed25519_key("vendor-a.example")
    publish(dns, key)
    dns.txt("_dmarc.vendor-a.example", record)
    raw = sign(mail(), key).replace(b"Invoice 42 attached.", b"Pay IBAN DE44 today.")
    out = check(conn, clock, dns, raw)
    assert out.signatures[0].outcome == "bad_body_hash"
    assert out.result == want


def test_forged_header_is_a_bad_signature(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns
) -> None:
    key = ed25519_key("vendor-a.example")
    publish(dns, key)
    dns.txt("_dmarc.vendor-a.example", "v=DMARC1; p=reject")
    raw = sign(mail(), key).replace(b"Subject: Invoice 42", b"Subject: Invoice 43")
    out = check(conn, clock, dns, raw)
    assert out.signatures[0].outcome == "bad_signature" and out.result == "fail"


def test_missing_key_or_dns_error_is_none(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns
) -> None:
    key = ed25519_key("vendor-a.example")
    dns.txt("_dmarc.vendor-a.example", "v=DMARC1; p=reject")
    out = check(conn, clock, dns, sign(mail(), key))  # key not published
    assert out.signatures[0].outcome == "no_key" and out.result == "none"
    dns.table[("s1._domainkey.vendor-a.example", "TXT")] = Answer("error")
    clock.advance(601)  # past the cached "no key" answer
    out = sa.evaluate(
        sign(mail(), key), parse(sign(mail(), key)), DnsCache(conn, clock, lookup=dns)
    )
    assert out.signatures[0].outcome == "dns_error" and out.result == "none"


def test_revoked_key_is_not_broken_signature(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns
) -> None:
    key = ed25519_key("vendor-a.example")
    dns.txt("s1._domainkey.vendor-a.example", "v=DKIM1; k=ed25519; p=")
    dns.txt("_dmarc.vendor-a.example", "v=DMARC1; p=reject")
    out = check(conn, clock, dns, sign(mail(), key))
    assert out.signatures[0].outcome in ("no_key", "bad_key") and out.result == "none"


def test_unsigned_mail_is_none_even_with_reject(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns
) -> None:
    dns.txt("_dmarc.vendor-a.example", "v=DMARC1; p=reject")
    out = check(conn, clock, dns, mail())  # could pass DMARC by SPF, which ecf can't check
    assert out.result == "none" and out.reason == "no DKIM signature"


def test_two_from_headers_fail(conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns) -> None:
    raw = mail().replace(b"To:", b"From: ceo@acme.example\r\nTo:", 1)
    assert check(conn, clock, dns, raw).result == "fail"


def test_oversized_is_none(conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns) -> None:
    p = parse_partial(b"From: a@vendor-a.example\r\n\r\n", [], {}, size=99)
    assert sa.evaluate(b"", p, DnsCache(conn, clock, lookup=dns)).result == "none"
    assert dns.asked == []


# ---- DMARC discovery ------------------------------------------------------------------------


def cache(conn: sqlite3.Connection, dns: FakeDns) -> DnsCache:
    return DnsCache(conn, FakeClock(), lookup=dns)


def test_policy_from_the_organizational_domain_uses_sp_and_np(
    conn: sqlite3.Connection, dns: FakeDns
) -> None:
    dns.txt("_dmarc.vendor-a.example", "v=DMARC1; p=none; sp=quarantine; np=reject")
    dns.table[("pay.vendor-a.example", "A")] = Answer("ok", ("192.0.2.1",))
    p = sa.discover_policy(cache(conn, dns), "pay.vendor-a.example")
    assert p is not None and p.level == "organizational" and p.effective == "quarantine"
    p = sa.discover_policy(cache(conn, dns), "ghost.vendor-a.example")  # NXDOMAIN everywhere
    assert p is not None and p.effective == "reject"


def test_tree_walk_limits_labels_and_stops_at_psd(conn: sqlite3.Connection, dns: FakeDns) -> None:
    deep = "a.b.c.d.e.f.g.h.i.vendor-a.example"  # 11 labels
    dns.txt("_dmarc.vendor-a.example", "v=DMARC1; p=reject")
    p = sa.discover_policy(cache(conn, dns), deep)
    assert p is not None and p.domain == "vendor-a.example"
    asked = [n for n, _t in dns.asked if n.startswith("_dmarc.")]
    assert asked[0] == f"_dmarc.{deep}"
    assert asked[1] == "_dmarc.e.f.g.h.i.vendor-a.example"  # shortened to 7 labels
    assert len(asked) <= 8
    dns2 = FakeDns()  # names not seen above, so nothing comes from the cache
    dns2.txt("_dmarc.test", "v=DMARC1; p=reject; psd=y")
    assert sa.org_domain(cache(conn, dns2), "mail.shop.test") == "shop.test"


def test_multiple_records_at_one_name_are_discarded(conn: sqlite3.Connection, dns: FakeDns) -> None:
    dns.txt("_dmarc.vendor-a.example", "v=DMARC1; p=reject", "v=DMARC1; p=none")
    assert sa.discover_policy(cache(conn, dns), "vendor-a.example") is None


# ---- the DNS cache --------------------------------------------------------------------------


def test_cache_ttl_cap_negative_and_errors(conn: sqlite3.Connection, clock: FakeClock) -> None:
    dns = FakeDns()
    dns.txt("x.example", "hello", ttl=86400)
    dns.table[("err.example", "TXT")] = Answer("error")
    c = DnsCache(conn, clock, lookup=dns, cap_s=600)
    assert c.get("x.example").records == ("hello",)
    assert c.get("gone.example").status == "nxdomain"
    assert c.get("err.example").status == "error"
    c2 = DnsCache(conn, clock, lookup=dns)
    c2.get("x.example")
    c2.get("gone.example")
    c2.get("err.example")
    assert c2.queries == 1  # only the error is asked again
    clock.advance(601)  # past the 10-minute cap despite the day-long TTL
    c3 = DnsCache(conn, clock, lookup=dns)
    c3.get("x.example")
    assert c3.queries == 1


def test_budget_and_prefetch(conn: sqlite3.Connection, clock: FakeClock) -> None:
    dns = FakeDns()
    for i in range(20):
        dns.txt(f"n{i}.example", str(i))
    c = DnsCache(conn, clock, lookup=dns)
    c.prefetch([(f"n{i}.example", "TXT") for i in range(20)])
    assert c.queries == 20 and c.get("n7.example").records == ("7",) and c.queries == 20
    clock.advance(31)
    assert c.get("late.example").status == "error" and c.queries == 20


def test_ambiguous_from_is_none_even_when_signed(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns
) -> None:
    key = ed25519_key("vendor-a.example")
    publish(dns, key)
    raw = sign(mail("billing@vendor-a.example"), key).replace(
        b"From: Vendor A <billing@vendor-a.example>",
        b"From: billing@vendor-a.example <mallory@mail.test>",
    )
    out = check(conn, clock, dns, raw)
    assert out.result == "none" and "ambiguous" in out.reason


# ---- V1.1 review (2026-09-29) ---------------------------------------------------------------


def test_bare_cr_in_the_headers_is_never_a_pass(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns
) -> None:
    """dkimpy splits header lines at CRLF/LF, Python's parser also at a lone CR: a replayed
    signed message could carry a Subject and Reply-To the signature never covered."""
    key = ed25519_key("vendor-a.example")
    publish(dns, key)
    dns.txt("_dmarc.vendor-a.example", "v=DMARC1; p=reject")
    signed = sign(mail(), key)
    assert check(conn, clock, dns, signed).result == "pass"
    attacked = b"X-Note: hi\rSubject: URGENT wire\rReply-To: ceo@evil.test\r\n" + signed
    parsed = parse(attacked)
    assert parsed.headers_ambiguous and parsed.reply_to == ("ceo@evil.test",)
    out = sa.evaluate(attacked, parsed, DnsCache(conn, clock, lookup=dns))
    assert out.result == "none" and "bare CR" in out.reason


@pytest.mark.parametrize(
    "bad_line", [b"This is not a header", b"X-\xc3\x9cber: 1", b": x", b"Subject : s"]
)
def test_malformed_headers_are_none_not_a_crash(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns, bad_line: bytes
) -> None:
    raw = mail().replace(b"To:", bad_line + b"\r\nTo:", 1)
    out = sa.evaluate(raw, parse(raw), DnsCache(conn, clock, lookup=dns))
    assert out.result == "none" and "malformed header block" in out.reason


def test_only_the_first_signatures_are_checked(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns
) -> None:
    """Each signature re-reads the whole message; related ones come first (RFC 6376 §6.1)."""
    other = ed25519_key("sender.test")
    publish(dns, other)
    raw = mail()
    for _ in range(sa.MAX_SIGNATURES + 5):
        raw = sign(raw, other)
    key = ed25519_key("vendor-a.example")
    publish(dns, key)
    dns.txt("_dmarc.vendor-a.example", "v=DMARC1; p=reject")
    raw = sign(raw, key)  # the aligned signature is on top, but sorted first regardless
    out = check(conn, clock, dns, raw)
    assert len(out.signatures) == sa.MAX_SIGNATURES and out.result == "pass"


def test_a_dns_error_on_the_policy_is_none(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns
) -> None:
    """Falling through to the parent's record would swap the author's strict alignment for a
    relaxed one: SPEC §7.3, a DNS failure is `none`, never `pass`."""
    key = ed25519_key("vendor-a.example")
    publish(dns, key)
    dns.table[("_dmarc.pay.vendor-a.example", "TXT")] = Answer("error")
    dns.txt("_dmarc.vendor-a.example", "v=DMARC1; p=reject")
    raw = sign(mail("billing@pay.vendor-a.example"), key)
    out = check(conn, clock, dns, raw)
    assert out.result == "none" and out.reason == "DNS error looking up the DMARC policy"


def test_an_idn_from_domain_is_compared_in_a_label_form(
    conn: sqlite3.Connection, clock: FakeClock, dns: FakeDns
) -> None:
    ascii_domain = "bücher.example".encode("idna").decode()
    key = ed25519_key(ascii_domain)
    publish(dns, key)
    dns.txt(f"_dmarc.{ascii_domain}", "v=DMARC1; p=reject")
    m = EmailMessage()
    m["From"] = "Shop <info@bücher.example>"
    m["To"] = "ap@acme.example"
    m["Subject"] = "Order"
    m["Date"] = "Mon, 28 Sep 2026 12:00:00 +0000"
    m.set_content("Thanks.\n")
    raw = sign(m.as_bytes(policy=email.policy.SMTPUTF8), key)
    out = check(conn, clock, dns, raw)
    assert out.from_domain == ascii_domain and out.result == "pass"
