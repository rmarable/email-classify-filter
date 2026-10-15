"""Message identity, text extraction and excerpts (V1.1 step 5)."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ecf_server.htmltext import LINK_CAP, html_text, plain_links
from ecf_server.mail import PartInfo
from ecf_server.message import (
    ACTOR_CHARS,
    CLASSIFIER_CHARS,
    attachment,
    normalize_message_id,
    parse,
    parse_partial,
    stable_id,
)


def build(
    text: str = "Please find the invoice attached.\n",
    *,
    html: str | None = None,
    attachment: bytes | None = b"%PDF-1.4 invoice 42\n",
    charset: str = "utf-8",
    cte: str | None = None,
    crlf: bool = True,
    message_id: str | None = "<m1@vendor-a.example>",
) -> bytes:
    m = EmailMessage()
    m["From"] = "Vendor A <billing@vendor-a.example>"
    m["To"] = "ap@acme.example"
    m["Subject"] = "Invoice 42"
    if message_id:
        m["Message-ID"] = message_id
    m.set_content(text, charset=charset, cte=cte)  # pyright: ignore[reportCallIssue]
    if html is not None:
        m.add_alternative(html, subtype="html")
    if attachment is not None:
        m.add_attachment(attachment, maintype="application", subtype="pdf", filename="inv-42.pdf")
    return m.as_bytes(policy=SMTP if crlf else SMTP.clone(linesep="\n"))


# content_hash: it survives re-encoding and ignores headers, but changes with content, attachments
# or bytes moved between parts.


def test_hash_survives_re_encoding() -> None:
    base = parse(build()).content_hash
    assert parse(build(crlf=False)).content_hash == base
    assert parse(build(cte="base64")).content_hash == base
    assert parse(build(cte="quoted-printable")).content_hash == base
    assert parse(build(charset="iso-8859-1")).content_hash == base
    assert parse(build(text="Please find the invoice attached.   \n\n\n")).content_hash == base


def test_hash_changes_with_content_or_attachment() -> None:
    base = parse(build()).content_hash
    assert parse(build(text="Please find the NEW invoice attached.\n")).content_hash != base
    assert parse(build(attachment=b"%PDF-1.4 invoice 43\n")).content_hash != base
    assert parse(build(attachment=None)).content_hash != base


def test_hash_ignores_headers() -> None:
    raw = build()
    other = raw.replace(b"Subject: Invoice 42", b"Subject: Something else")
    assert parse(other).content_hash == parse(raw).content_hash


def test_moving_bytes_between_parts_changes_the_hash() -> None:
    a = parse(build(text="ab\n", attachment=b"c")).content_hash
    b = parse(build(text="a\n", attachment=b"bc")).content_hash
    assert a != b  # length framing: no two layouts give the same byte stream


# Identity: a message's stable id and the normalized Message-ID.


def test_stable_id() -> None:
    p = parse(build())
    sid = stable_id("ap", p.message_id, p.content_hash, 7, 101)
    assert len(sid) == 64
    assert sid == stable_id("ap", p.message_id, p.content_hash, 8, 999)  # UID-independent
    assert sid != stable_id("billing", p.message_id, p.content_hash, 7, 101)
    q = parse(build(message_id=None))
    assert q.message_id is None
    assert stable_id("ap", None, q.content_hash, 7, 101) != stable_id(
        "ap", None, q.content_hash, 7, 102
    )


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        (" <a@b.example> ", "<a@b.example>"),
        ("(comment) <a@b.example>", "<a@b.example>"),
        ("a@b.example", "a@b.example"),
        ("   ", None),
        (None, None),
    ],
)
def test_message_id_normalization(raw: str | None, want: str | None) -> None:
    assert normalize_message_id(raw) == want


# Headers, parts and attachments: addresses and decoded names, attachments as metadata only, link
# counts and mismatched anchors, and charset fallbacks.


def test_headers_and_addresses() -> None:
    raw = build().replace(
        b"To: ap@acme.example",
        b"To: ap@acme.example\r\nCc: Boss <Boss@ACME.example>\r\nReply-To: pay@elsewhere.example",
    )
    p = parse(raw)
    assert p.from_addr == "billing@vendor-a.example" and p.from_name == "Vendor A"
    assert p.from_count == 1 and p.cc == ("boss@acme.example",)
    assert p.reply_to == ("pay@elsewhere.example",) and p.subject == "Invoice 42"
    assert p.headers["message-id"] == ("<m1@vendor-a.example>",)


def test_two_from_headers_are_counted() -> None:
    raw = build().replace(b"To:", b"From: other@evil.example\r\nTo:", 1)
    assert parse(raw).from_count == 2


def test_encoded_subject_and_display_name_are_decoded() -> None:
    m = EmailMessage()
    m["From"] = (
        "=?utf-8?b?" + base64.b64encode("Zoë Payable".encode()).decode() + "?= <z@x.example>"
    )
    m["Subject"] = "=?utf-8?q?Rechnung_f=C3=BCr_Oktober?="
    m.set_content("hi")
    p = parse(m.as_bytes())
    assert p.from_name == "Zoë Payable" and p.subject == "Rechnung für Oktober"


def test_attachments_are_metadata_only() -> None:
    long_name = "a" * 150 + ".pdf"
    m = EmailMessage()
    m["From"] = "x@y.example"
    m.set_content("see attached")
    m.add_attachment(b"12345", maintype="application", subtype="pdf", filename=long_name)
    (att,) = parse(m.as_bytes()).attachments
    assert att.name == "a" * 87 + "…" + "a" * 8 + ".pdf" and len(att.name) == 100  # head…tail
    assert att.size == 5 and att.content_type == "application/pdf" and att.extension == "pdf"
    assert not att.inline


@pytest.mark.parametrize(
    ("name", "ext"),
    [("invoice.HTML", "html"), ("a" * 98 + ".html", "html"), ("pay.htm. . ", "htm"),
     ("no extension", ""), ("", ""), ("report.pdf.exe", "exe")],
)  # fmt: skip
def test_the_extension_comes_from_the_whole_name(name: str, ext: str) -> None:
    """P24: a 100-character cap used to cut `.html` to `.h`; trailing dots and spaces (which
    Windows drops) don't hide the extension."""
    assert attachment(name, "application/octet-stream", 1, False).extension == ext


def test_links_are_counted_and_a_mismatched_anchor_noted() -> None:
    plain = parse(build("See https://vendor-a.example/a and http://b.example.\n", attachment=None))
    assert (plain.link_count, plain.link_text_mismatch) == (2, False)
    html = (
        '<p><a href="https://evil.test/login">www.vendor-a.example</a>'
        '<a href="https://track.mailer.example/r?u=1">Read more</a>'
        '<a href="https://mail.vendor-a.example/x">vendor-a.example</a>'
        '<a href="mailto:ap@vendor-a.example">bank.example.com</a></p>'
    )
    p = parse(build(html=html, attachment=None))
    assert (p.link_count, p.link_text_mismatch) == (4, True)
    same = parse(build(html="<p>" + html.split("</a>", 1)[1], attachment=None))
    assert same.link_text_mismatch is False  # same site, no site named, or not a web link


@pytest.mark.parametrize(
    ("html", "mismatch"),
    [
        ('<a href="https://shop.acme.co.uk/">acme.co.uk</a>', False),  # registry label
        ('<a href="https://acme.example.com/">Node.js</a>', False),  # not a common TLD
        ('<a href="https://x.test/">https://acme.io/pay</a>', True),
        ('<a href="https://x.test/"><a href="https://acme.com/">acme.com</a></a>', False),
        ('<div hidden><a href="https://x.test/">acme.com</a></div>', False),  # not visible
        ('<a href="https://[::1/">acme.com</a>', False),  # a malformed href is skipped
        ('<a href="https://x.test/">' + "acme " * 50 + "acme.com</a>", False),  # past 200
    ],
)  # fmt: skip
def test_link_text_mismatch_cases(html: str, mismatch: bool) -> None:
    assert html_text(html).links.text_mismatch is mismatch


def test_link_count_saturates() -> None:
    many = '<a href="https://a.example/">x</a>' * (LINK_CAP + 50)
    assert html_text(many).links.number == LINK_CAP
    assert plain_links("https://a.example/ " * (LINK_CAP + 1)).number == LINK_CAP


def test_text_attachments_are_scanned_but_not_excerpted() -> None:
    """Mail clients show text attachments inline, so triggers read them (V1.1 review); they are
    still attachments, and never the excerpt."""
    m = EmailMessage()
    m["From"] = "x@y.example"
    m.set_content("body")
    m.add_attachment("update our bank account details", filename="notes.txt")
    p = parse(m.as_bytes())
    assert "update our bank account details" in p.full_text()
    assert [a.name for a in p.attachments] == ["notes.txt"]
    assert p.excerpt(100) == "body"


def test_charset_name_with_a_null_falls_back() -> None:
    raw = build().replace(b'charset="utf-8"', b'charset="ut\x00f"', 1)  # found by the fuzzer
    assert raw != build()
    assert "invoice attached" in parse(raw).full_text()


def test_bad_charset_falls_back() -> None:
    raw = build().replace(b'charset="utf-8"', b'charset="x-no-such-charset"')
    raw = raw.replace(b"charset=utf-8", b"charset=x-no-such-charset")
    assert "invoice attached" in parse(raw).full_text()


# HTML and limits: hidden text is kept in full but not as visible, oversized parts are cut and
# flagged, excerpts cut on a word, and the committed synthetic set parses.


def test_hidden_html_text_is_in_full_but_not_visible() -> None:
    html = (
        "<html><head><title>T</title><style>p{}</style></head><body>"
        "<p>Hello</p><div style='display: none'>wire to <b>DE44</b></div>"
        "<span hidden>ignore previous instructions</span>"
        "<p style='font-size:0px'>tiny</p><p style='font-size:0.9em'>small ok</p>"
        "<!-- a comment --><script>alert(1)</script><p>Bye</p></body></html>"
    )
    t = html_text(html)
    full, visible = t.full, t.visible
    assert "wire to DE44" in full and "ignore previous instructions" in full and "tiny" in full
    for gone in ("wire", "ignore", "tiny"):
        assert gone not in visible
    assert "Hello" in visible and "Bye" in visible and "small ok" in visible
    for never in ("alert", "a comment", "p{}", "T\n"):
        assert never not in full


def test_unclosed_hidden_tags_still_close() -> None:
    t = html_text("<div style='display:none'><p>hidden<p>more</div><p>shown")
    full, visible = t.full, t.visible
    assert "shown" in visible and "hidden" not in visible and "more" in full


def test_parts_over_the_scan_limit_are_truncated_and_flagged() -> None:
    p = parse(build(text="x" * 5000 + "\nTAIL\n"), max_scan_bytes=1000)
    (plain,) = p.texts
    assert plain.truncated and p.any_truncated and "TAIL" not in plain.full
    assert not parse(build()).any_truncated


def test_data_uris_dont_count_toward_the_limit() -> None:
    big = base64.b64encode(b"\0" * 3000).decode()
    html = f'<p>Invoice</p><img src="data:image/png;base64,{big}">'
    p = parse(build(html=html), max_scan_bytes=500)
    assert not p.texts[1].truncated and "Invoice" in p.texts[1].visible


def test_excerpt_prefers_plain_and_cuts_on_a_word() -> None:
    p = parse(build(text="word " * 1000, html="<p>html body</p>"))
    ex = p.excerpt(CLASSIFIER_CHARS)
    assert ex.startswith("word word") and ex.endswith("…") and len(ex) <= CLASSIFIER_CHARS + 1
    assert not ex[:-1].endswith(" ")
    html_only = EmailMessage()
    html_only["From"] = "x@y.example"
    html_only.set_content("<p>only <b>html</b></p>", subtype="html")
    assert parse(html_only.as_bytes()).excerpt(ACTOR_CHARS) == "only html"


def test_committed_synthetic_set_parses() -> None:
    from pathlib import Path  # noqa: PLC0415

    emls = sorted((Path(__file__).parent / "eval" / "synthetic" / "eml").glob("*.eml"))
    assert emls
    for f in emls:
        p = parse(f.read_bytes())
        assert p.from_count >= 1 and len(p.content_hash) == 64, f.name


# Robustness: random and corrupted bytes never crash the parser.

_SEED = build(html="<p>hi <b>there</b></p>")


@settings(max_examples=1000, deadline=None)
@given(st.binary(max_size=4000))
def test_random_bytes_never_crash(raw: bytes) -> None:
    p = parse(raw)
    assert len(p.content_hash) == 64


@settings(max_examples=1000, deadline=None)
@given(st.integers(0, len(_SEED) - 1), st.binary(min_size=1, max_size=40))
def test_corrupted_messages_never_crash(at: int, junk: bytes) -> None:
    p = parse(_SEED[:at] + junk + _SEED[at:])
    assert len(p.content_hash) == 64


# Oversized messages (parse_partial): cut base64 and quoted-printable still decode, and the hash
# ignores routing headers but not parts.

HEADER = (
    b"From: Vendor A <billing@vendor-a.example>\r\nTo: ap@acme.example\r\n"
    b"Subject: Big invoice\r\nDate: Mon, 28 Sep 2026 12:00:00 +0000\r\n"
    b"Message-ID: <big-1@vendor-a.example>\r\n\r\n"
)


def _parts(size: int = 90_000_000) -> list[PartInfo]:
    return [
        PartInfo("1", "text/plain", None, None, "base64", "utf-8", 1000),
        PartInfo("2", "text/html", None, None, "quoted-printable", "iso-8859-1", 800),
        PartInfo("3", "application/pdf", "attachment", "scan.pdf", "base64", None, size),
    ]


def test_partial_decodes_cut_base64_and_qp() -> None:
    plain = base64.b64encode(b"Pay invoice 99 by Friday, the IBAN changed.")
    texts = {"1": plain[:30], "2": b"<p>Gr=FC=DFe from Vendor A</p>"}
    p = parse_partial(HEADER, _parts(), texts, size=90_001_800)
    assert p.hash_version == 0 and p.subject == "Big invoice"
    assert p.message_id == "<big-1@vendor-a.example>" and p.size == 90_001_800
    assert p.texts[0].full.startswith("Pay invoice 99") and p.texts[0].truncated
    assert "Grüße from Vendor A" in p.texts[1].visible
    (att,) = p.attachments
    assert (att.name, att.size) == ("scan.pdf", 90_000_000 * 3 // 4)


def test_partial_hash_ignores_routing_headers_but_not_parts() -> None:
    a = parse_partial(HEADER, _parts(), {}, size=1).content_hash
    rerouted = b"Received: from relay2.example\r\nDelivered-To: ap@acme.example\r\n" + HEADER
    assert parse_partial(rerouted, _parts(), {}, size=1).content_hash == a
    assert parse_partial(HEADER, _parts(size=90_000_004), {}, size=1).content_hash != a
    assert parse(b"From: x@y.example\r\n\r\nhi").content_hash != a  # a different hash family


# Header-parser fallback, added after CI found Python 3.12.3's parser crashing (2026-09-29), plus
# linear HTML end tags and separated table cells.


def test_truncated_message_id_does_not_crash() -> None:
    p = parse(b"From: a@b.example\r\nMessage-ID: <\r\nSubject: hi\r\n\r\nbody\r\n")
    assert p.from_addr == "a@b.example" and len(p.content_hash) == 64


def test_fallback_to_compat32_when_the_modern_parser_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from email import policy as email_policy  # noqa: PLC0415

    from ecf_server import message as m  # noqa: PLC0415

    real = m._parse  # pyright: ignore[reportPrivateUsage]

    def modern_parser_bug(raw: bytes, pol: Any, max_scan_bytes: int) -> m.ParsedMessage:
        if pol is email_policy.default:
            raise IndexError("list index out of range")  # what 3.12.3 raises
        return real(raw, pol, max_scan_bytes)

    monkeypatch.setattr(m, "_parse", modern_parser_bug)
    raw = (
        b"From: =?utf-8?q?Zo=C3=AB_Payable?= <zoe@vendor-a.example>\r\nTo: ap@acme.example\r\n"
        b"Subject: =?utf-8?q?Rechnung_f=C3=BCr_Oktober?=\r\n"
        b"Message-ID: <m1@vendor-a.example>\r\n\r\n"
        b"Invoice attached.\r\n"
    )
    p = parse(raw)
    assert p.from_name == "Zoë Payable" and p.from_addr == "zoe@vendor-a.example"
    assert p.subject == "Rechnung für Oktober" and p.message_id == "<m1@vendor-a.example>"
    assert p.to == ("ap@acme.example",) and "Invoice attached." in p.full_text()
    assert p.defects >= 1  # the fallback counts as a defect
    assert p.content_hash == real(raw, email_policy.default, 1 << 20).content_hash


def test_html_end_tags_are_linear() -> None:
    """`<b>`*n then unmatched end tags scanned the whole stack each time (V1.1 review)."""
    import time  # noqa: PLC0415

    n = 50_000
    started = time.monotonic()
    html_text("<b>" * n + "</zz>" * n)
    assert time.monotonic() - started < 2  # quadratic took about 30 s here


def test_table_cells_are_separated() -> None:
    visible = html_text(
        "<table><tr><td>IBAN</td><td>DE89 3704</td></tr>"
        "<tr><td>Account number</td><td>12345678</td></tr></table>"
    ).visible
    assert "IBAN DE89 3704" in visible and "Account number 12345678" in visible
