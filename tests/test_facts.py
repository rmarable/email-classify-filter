"""Computed facts and the analysis pipeline (V1.1 step 8)."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from email.message import EmailMessage
from typing import Any

import pytest

from ecf_server import facts, leases, probe
from ecf_server.analysis import MessageAnalyzer
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.dnscache import DnsCache
from ecf_server.fetch import address_config, fetch_page
from ecf_server.internal import OrgAddress
from ecf_server.mail.fake import FakeMailSource, GmailFakeSource
from ecf_server.message import parse, parse_partial
from tests.test_senderauth import FakeDns, ed25519_key, publish, sign

AP = facts.AddressInfo("ap", "ap@acme.example", "high")
STD = facts.AddressInfo("ap", "ap@acme.example", "standard")


def mail(
    sender: str = "billing@vendor-a.example",
    *,
    to: str = "ap@acme.example",
    extra: dict[str, str] | None = None,
    attachment: tuple[str, str] | None = None,
) -> bytes:
    m = EmailMessage()
    m["From"] = sender
    m["To"] = to
    m["Subject"] = "Invoice"
    m["Date"] = "Mon, 28 Sep 2026 12:00:00 +0000"
    for k, v in (extra or {}).items():
        m[k] = v
    m.set_content("Please see the invoice.\n")
    if attachment:
        maintype, subtype = attachment[1].split("/")
        m.add_attachment(b"data", maintype=maintype, subtype=subtype, filename=attachment[0])
    return m.as_bytes()


def compute(
    conn: sqlite3.Connection,
    raw: bytes,
    *,
    auth: str = "none",
    address: facts.AddressInfo = STD,
    payment: bool = False,
) -> dict[str, Any]:
    return facts.compute(conn, address, parse(raw), auth, ["acme.example"], payment_keyword=payment)


def history(conn: sqlite3.Connection, sender: str, days: list[int], clock: FakeClock) -> None:
    start = clock.now()
    for d in days:
        with write_tx(conn):
            facts.record_sender(conn, "ap", sender, "pass", to_ts(start + _days(d)))


def _days(n: int) -> timedelta:
    return timedelta(days=n)


# ---- origin and history ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sender", "auth", "want"),
    [
        ("ceo@acme.example", "pass", "internal"),
        ("ceo@payroll.acme.example", "pass", "internal"),  # subdomains count (OD-193)
        ("ceo@acme.example", "none", "external"),  # the org's name alone isn't enough
        ("ceo@acme.example.evil.test", "pass", "external"),
        ("ceo@notacme.example", "pass", "external"),
    ],
)
def test_sender_origin(conn: sqlite3.Connection, sender: str, auth: str, want: str) -> None:
    f = compute(conn, mail(sender), auth=auth)
    assert f["sender_origin"] == want
    assert f["from_org_domain"] == sender.endswith(("@acme.example", ".acme.example"))


def test_seen_needs_three_passes_over_fourteen_days(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    history(conn, "billing@vendor-a.example", [0, 5, 13], clock)
    assert not compute(conn, mail())["sender_seen_before"]  # only 13 days apart
    history(conn, "billing@vendor-a.example", [14], clock)
    f = compute(conn, mail())
    assert f["sender_seen_before"] and not f["sender_confirmed"]


def test_only_passing_mail_builds_history(conn: sqlite3.Connection, clock: FakeClock) -> None:
    for d in (0, 7, 20):
        with write_tx(conn):
            facts.record_sender(
                conn, "ap", "billing@vendor-a.example", "none", to_ts(clock.now() + _days(d))
            )
    assert conn.execute("SELECT count(*) FROM senders").fetchone()[0] == 0


def test_confirmed_and_shared_platform_senders(conn: sqlite3.Connection) -> None:
    for sender in ("billing@vendor-a.example", "dse@docusign.net", "invoice@mail.stripe.com"):
        conn.execute(
            "INSERT INTO senders (address_id, sender_hash, confirmed_category)"
            " VALUES ('ap', ?, 'invoice')",
            (facts.sender_hash(sender),),
        )
    f = compute(conn, mail())
    assert f["sender_seen_before"] and f["sender_confirmed"]
    for sender in ("dse@docusign.net", "invoice@mail.stripe.com"):
        g = compute(conn, mail(sender))
        assert g["shared_platform"] and not g["sender_seen_before"] and not g["sender_confirmed"]


# ---- mismatches and bulk --------------------------------------------------------------------


def test_reply_to_mismatch_and_expected_domain(conn: sqlite3.Connection) -> None:
    assert not compute(conn, mail())["reply_to_mismatch"]
    same = mail(extra={"Reply-To": "ar@vendor-a.example"})
    assert not compute(conn, same)["reply_to_mismatch"]
    other = mail(extra={"Reply-To": "pay@vendor-a-payments.test"})
    assert compute(conn, other)["reply_to_mismatch"]
    conn.execute(
        "INSERT INTO senders (address_id, sender_hash, expected_reply_to_domain)"
        " VALUES ('ap', ?, 'vendor-a-payments.test')",
        (facts.sender_hash("billing@vendor-a.example"),),
    )
    assert not compute(conn, other)["reply_to_mismatch"]


def test_recipient_mismatch(conn: sqlite3.Connection) -> None:
    assert not compute(conn, mail(to="AP@acme.example"))["recipient_mismatch"]
    assert not compute(conn, mail(to="x@acme.example", extra={"Cc": "ap@acme.example"}))[
        "recipient_mismatch"
    ]
    assert compute(conn, mail(to="someone@else.example"))["recipient_mismatch"]


@pytest.mark.parametrize(
    "extra",
    [
        {"List-Id": "<news.vendor-a.example>"},
        {"List-Unsubscribe": "<mailto:u@vendor-a.example>"},
        {"Auto-Submitted": "auto-generated"},
        {"Precedence": "bulk"},
        {"X-Autoreply": "yes"},
        {"Return-Path": "<>"},
    ],
)
def test_bulk_signals(conn: sqlite3.Connection, extra: dict[str, str]) -> None:
    assert compute(conn, mail(extra=extra))["bulk_signal"]


def test_not_bulk(conn: sqlite3.Connection) -> None:
    assert not compute(conn, mail(extra={"Auto-Submitted": "no"}))["bulk_signal"]
    assert compute(conn, mail("no-reply@vendor-a.example"))["bulk_signal"]
    assert compute(conn, mail("noreply+billing@vendor-a.example"))["bulk_signal"]
    assert not compute(conn, mail("norah@vendor-a.example"))["bulk_signal"]


def test_bulk_corroborates_only_when_authenticated_and_known(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    raw = mail(extra={"List-Id": "<n.vendor-a.example>"})
    assert not compute(conn, raw, auth="pass")["bulk_corroborates"]  # first-time sender
    history(conn, "billing@vendor-a.example", [0, 7, 20], clock)
    assert not compute(conn, raw, auth="none")["bulk_corroborates"]
    assert compute(conn, raw, auth="pass")["bulk_corroborates"]


# ---- content_unscanned ----------------------------------------------------------------------


def test_unscanned_reasons(conn: sqlite3.Connection, clock: FakeClock) -> None:
    assert compute(conn, mail())["unscanned_reasons"] == []
    pdf = mail(attachment=("inv.pdf", "application/pdf"))
    assert compute(conn, pdf)["unscanned_reasons"] == ["a document from a first-time sender"]
    assert "an attachment on a high address" in compute(conn, pdf, address=AP)["unscanned_reasons"]
    assert "attachments on a payment item" in compute(conn, pdf, payment=True)["unscanned_reasons"]
    history(conn, "billing@vendor-a.example", [0, 7, 20], clock)
    assert compute(conn, pdf)["content_unscanned"] is False  # a known sender's PDF
    big = parse_partial(b"From: a@vendor-a.example\r\n\r\n", [], {}, size=99)
    f = facts.compute(conn, STD, big, "none", [])
    assert f["unscanned_reasons"] == ["over the size limit"]


LISTED = (OrgAddress("patlee@gmail.com", "Pat Lee"),)


def test_an_exact_org_address_is_internal_and_a_folded_one_is_the_same_account(
    conn: sqlite3.Connection,
) -> None:
    """OD-431, OD-432: internal needs DMARC pass and the exact address; a folded variant is the
    same account (`from_org_address`, so trigger 6 applies) but not internal."""
    for sender, origin in (("patlee@gmail.com", "internal"), ("pat.lee@gmail.com", "external")):
        f = facts.compute(conn, STD, parse(mail(sender)), "pass", [], org_addresses=LISTED)
        assert (f["sender_origin"], f["from_org_address"], f["from_org_domain"]) == (
            origin, True, False), sender  # fmt: skip
    f = facts.compute(conn, STD, parse(mail("patlee@gmail.com")), "none", [], org_addresses=LISTED)
    assert f["sender_origin"] == "external"
    f = facts.compute(conn, STD, parse(mail("pat@else.example")), "pass", [], org_addresses=LISTED)
    assert f["from_org_address"] is False and f["sender_origin"] == "external"


def test_gmails_sent_label_marks_the_accounts_own_note_to_itself(conn: sqlite3.Connection) -> None:
    """OD-446: only on Gmail (labels given), only From the watched address, only with \\Sent."""
    me = facts.AddressInfo("me", "Pat.Lee@gmail.com", "standard")
    note = parse(mail("pat.lee@gmail.com", to="pat.lee@gmail.com"))
    sent = frozenset({"\\Sent", "\\Inbox"})
    assert facts.compute(conn, me, note, "none", [], gmail_labels=sent)["self_sent"] is True
    assert facts.compute(conn, me, note, "none", [], gmail_labels=frozenset({"\\Inbox"}))[
        "self_sent"] is False  # fmt: skip
    assert facts.compute(conn, me, note, "none", [])["self_sent"] is False  # not Gmail
    other = parse(mail("someone@gmail.com", to="pat.lee@gmail.com"))
    assert facts.compute(conn, me, other, "none", [], gmail_labels=sent)["self_sent"] is False


def test_unnamed_inline_images_are_not_attachments(conn: sqlite3.Connection) -> None:
    m = EmailMessage()
    m["From"] = "billing@vendor-a.example"
    m["To"] = "ap@acme.example"
    m.set_content("hi")
    m.add_alternative("<p>hi <img src='cid:logo'></p>", subtype="html")
    html = next(p for p in m.iter_parts() if p.get_content_type() == "text/html")
    html.add_related(b"\x89PNG", maintype="image", subtype="png", cid="<logo>")
    f = facts.compute(conn, AP, parse(m.as_bytes()), "none", ["acme.example"])
    assert f["unscanned_reasons"] == []


# ---- the pipeline, end to end ---------------------------------------------------------------


def test_fetch_with_the_analyzer_builds_sender_history(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    conn.execute(
        "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
        " VALUES ('ap', 'ap@acme.example', 'standard', 'A', 'now')"
    )
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by)"
        " VALUES ('org_domains', '[\"acme.example\"]', 'now', 'test')"
    )
    dns = FakeDns()
    key = ed25519_key("vendor-a.example")
    publish(dns, key)
    dns.txt("_dmarc.vendor-a.example", "v=DMARC1; p=reject")
    src = FakeMailSource()
    seen: list[bool] = []

    def one_check() -> list[str]:
        lease = leases.acquire(conn, clock, "ap", "w")
        assert lease is not None
        analyzer = MessageAnalyzer.for_address(conn, clock, "ap", DnsCache(conn, clock, lookup=dns))
        return fetch_page(
            conn, clock, src, address_config(conn, "ap"), lease, analyzer=analyzer
        ).created

    assert one_check() == []  # first run: start from now
    for day in range(4):
        src.deliver(sign(mail(), key))
        (sid,) = one_check()
        f = json.loads(
            conn.execute("SELECT facts FROM items WHERE stable_id = ?", (sid,)).fetchone()[0]
        )
        assert f["auth_result"] == "pass" and f["auth"]["dmarc_policy"]["effective"] == "reject"
        seen.append(f["sender_seen_before"])
        clock.advance(7 * 86400 + day)
    assert seen == [False, False, False, True]  # the 4th, after 3 passes over 14+ days


def test_a_gmail_note_to_yourself_isnt_trigger_6_but_a_forged_one_is(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """V1.6 end to end (OD-446): Gmail delivers the account's mail to itself unsigned and labels it
    Sent; the same unsigned mail without the label is a forgery of your own listed address."""
    me = "pat.lee@gmail.com"
    conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                 " VALUES ('pat', ?, 'standard', 'A', 'now')", (me,))  # fmt: skip
    conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                 " ('config.org_addresses', ?, 'now', 'test')",
                 (json.dumps([{"address": me, "name": "Pat Lee"}]),))  # fmt: skip
    src = GmailFakeSource()
    with write_tx(conn):
        probe.store(conn, clock, "pat", "imap.gmail.com", probe.probe(src, "imap.gmail.com"))

    def one_check() -> list[str]:
        lease = leases.acquire(conn, clock, "pat", "w")
        assert lease is not None
        analyzer = MessageAnalyzer.for_address(conn, clock, "pat",
                                               DnsCache(conn, clock, lookup=FakeDns()))  # fmt: skip
        created = fetch_page(conn, clock, src, address_config(conn, "pat"), lease,
                             analyzer=analyzer).created  # fmt: skip
        leases.release(conn, lease)
        return created

    one_check()  # first run: start from now
    src.deliver(mail(me, to=me), labels=("\\Sent", "\\Inbox"))
    src.deliver(mail(me, to=me, extra={"Message-ID": "<forged@x.example>"}))
    facts_of = [json.loads(conn.execute("SELECT facts FROM items WHERE stable_id = ?",
                                        (sid,)).fetchone()[0]) for sid in one_check()]  # fmt: skip
    mine, forged = facts_of
    assert mine["self_sent"] and mine["from_org_address"] and mine["triggers"]["fraud"] == []
    assert not forged["self_sent"]
    why = "From is one of your org addresses but isn't authenticated"
    assert why in forged["triggers"]["fraud"]


@pytest.mark.parametrize(
    ("domain", "shared"),
    [
        ("notification.intuit.com", True),  # Intuit's documented sender
        ("email.pandadoc.net", True),
        ("mail.hellosign.com", True),
        ("sender.zohoinvoice.com", True),
        ("quickbooks.com", False),  # removed: not a sending domain (OD-197)
        ("pandadoc.com", False),
    ],
)
def test_shared_platform_list(domain: str, shared: bool) -> None:
    assert facts.in_domains(domain, facts.SHARED_PLATFORMS) is shared


def test_dns_ttl_zero_is_not_cached_and_prefetch_keeps_to_the_budget(
    conn: sqlite3.Connection,
) -> None:
    from ecf_server.clock import FakeClock  # noqa: PLC0415
    from ecf_server.dnscache import MAX_PREFETCH, Answer, DnsCache  # noqa: PLC0415

    asked: list[str] = []

    def lookup(name: str, rtype: str) -> Answer:
        asked.append(name)
        return Answer("ok", ("v=DMARC1; p=none",), ttl=0)

    clock = FakeClock()
    DnsCache(conn, clock, lookup=lookup).get("_dmarc.a.example")
    DnsCache(conn, clock, lookup=lookup).get("_dmarc.a.example")
    assert asked == ["_dmarc.a.example"] * 2  # TTL 0: asked again (RFC 1035 §3.2.1)
    asked.clear()
    cache = DnsCache(conn, clock, lookup=lookup)
    cache.prefetch([(f"n{i}.example", "TXT") for i in range(200)])
    assert len(asked) == MAX_PREFETCH
    asked.clear()
    spent = DnsCache(conn, clock, budget_s=0, lookup=lookup)
    clock.advance(1)
    spent.prefetch([("late.example", "TXT")])
    assert asked == []
