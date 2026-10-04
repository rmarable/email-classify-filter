"""Messages ecf writes (V1.5 step 1b; OD-317, OD-318, OD-320, OD-321) and the install ID."""

from __future__ import annotations

import base64
import sqlite3
from datetime import UTC, datetime
from email import message_from_bytes, policy

import pytest

from ecf.errors import InvalidInputError
from ecf_server import install_identity as ident
from ecf_server import outbound_msg as om
from ecf_server.message import parse

WHEN = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
HEADER = "0123456789abcdef0123456789abcdef.0"


def _reply(**kw: object) -> om.Built:
    args: dict[str, object] = {
        "from_addr": "ap@acme.example",
        "to_addr": "billing@vendor.example",
        "subject": "Invoice 42",
        "body": "Hello Pat,\n\nThank you, we received your message.\n",
        "in_reply_to": "<orig-1@vendor.example>",
        "references": "<root@vendor.example> <mid@vendor.example>",
        "install_header": HEADER,
        "date": WHEN,
    }
    return om.build_reply(**(args | kw))  # type: ignore[arg-type]


def _headers(raw: bytes) -> dict[str, str]:
    m = message_from_bytes(raw, policy=policy.default)
    return {k: str(v) for k, v in m.items()}


def test_reply_headers_threading_and_hash() -> None:
    b = _reply()
    h = _headers(b.raw)
    assert h["From"] == "ap@acme.example" and h["To"] == "billing@vendor.example"
    assert h["Subject"] == "Re: Invoice 42"
    assert h["Auto-Submitted"] == "auto-replied" and h["X-ECF-Install"] == HEADER
    assert h["In-Reply-To"] == "<orig-1@vendor.example>"
    assert h["References"] == "<root@vendor.example> <mid@vendor.example> <orig-1@vendor.example>"
    assert h["Message-ID"] == b.message_id and b.message_id.endswith(".ecf@acme.example>")
    assert b"\r\n" in b.raw and b"\n" not in b.raw.replace(b"\r\n", b"")
    assert all(c < 0x80 for c in b.raw)
    assert b.content_hash == parse(b.raw).content_hash


def test_untrusted_header_text_is_cleaned_and_encoded() -> None:
    b = _reply(subject="Inv\r\nBcc: victim@x.example\x00 \u202eexe.pdf caf\u00e9")
    h = _headers(b.raw)
    assert "Bcc" not in h
    assert h["Subject"] == "Re: Inv Bcc: victim@x.example exe.pdf caf\u00e9"
    assert b"=?utf-8?" in b.raw  # RFC 2047 for the non-ASCII subject


def test_references_keep_only_message_ids_and_at_most_ten() -> None:
    refs = " ".join(f"<r{i}@v.example>" for i in range(20)) + " junk\r\nX-Evil: 1 <bad id>"
    h = _headers(_reply(references=refs).raw)
    ids = h["References"].split()
    assert len(ids) == 10 and ids[-1] == "<orig-1@vendor.example>"
    assert "X-Evil" not in h


def test_no_parent_means_no_threading_headers() -> None:
    h = _headers(_reply(in_reply_to="not an id", references=None).raw)
    assert "In-Reply-To" not in h and "References" not in h


@pytest.mark.parametrize(
    "value",
    ['"Boss" <ceo@acme.example> <x@evil.example>', "a b@x.example", "x@evil.example\r\nBcc: y@z",
     "Boss <ceo@acme.example>", "noat.example", ""],
)  # fmt: skip
def test_recipients_must_be_bare_addresses(value: str) -> None:
    with pytest.raises(InvalidInputError):
        _reply(to_addr=value)


def test_draft_has_no_ecf_headers() -> None:
    b = om.build_draft(from_addr="ap@acme.example", to_addr="pat@vendor.example",
                       subject="Re: Invoice 42", body="Draft text\n", in_reply_to=None,
                       references=None, date=WHEN)  # fmt: skip
    h = _headers(b.raw)
    assert h["Subject"] == "Re: Invoice 42"  # no second Re:
    assert "Auto-Submitted" not in h and "X-ECF-Install" not in h


ORIGINAL = (b"From: pat@vendor.example\r\nTo: ap@acme.example\r\nSubject: Invoice 42\r\n"
            b"Message-ID: <orig-1@vendor.example>\r\n\r\nPlease pay.\r\n.dot line\r\n")  # fmt: skip


def _forward(original: bytes) -> om.Built:
    return om.build_forward(from_addr="ap@acme.example", to_addr="lead@acme.example",
                            original=original, original_subject="Invoice 42",
                            cover="Forwarded by ecf from ap (item 1a2b3c).\n",
                            install_header=HEADER, date=WHEN)  # fmt: skip


def test_forward_attaches_a_clean_original_as_message_rfc822_unmodified() -> None:
    b = _forward(ORIGINAL)
    h = _headers(b.raw)
    assert h["Subject"] == "Fwd: Invoice 42" and h["Auto-Submitted"] == "auto-generated"
    assert h["X-ECF-Install"] == HEADER
    assert b"Content-Type: message/rfc822\r\nContent-Transfer-Encoding: 7bit" in b.raw
    assert ORIGINAL in b.raw  # byte for byte
    m = message_from_bytes(b.raw, policy=policy.default)
    parts = [p.get_content_type() for p in m.walk()]
    assert parts[:3] == ["multipart/mixed", "text/plain", "message/rfc822"]
    attached = b.raw.split(b"Content-Disposition: attachment\r\n\r\n", 1)[1]
    assert attached.rsplit(b"\r\n--ecf-", 1)[0] == ORIGINAL  # exactly, final CRLF included
    assert b.content_hash == parse(b.raw).content_hash


def test_forward_of_an_8bit_original_is_an_eml_file_with_the_exact_bytes() -> None:
    original = ORIGINAL.replace(b"Please pay.", "Bitte zahlen \u00fcber IBAN.".encode())
    b = _forward(original)
    assert all(c < 0x80 for c in b.raw)
    m = message_from_bytes(b.raw, policy=policy.default)
    [att] = [p for p in m.walk() if p.get_filename() == "original.eml"]
    assert att.get_content_type() == "application/octet-stream"
    assert att.get_payload(decode=True) == original
    assert base64.b64encode(original)[:40] in b.raw.replace(b"\r\n", b"")


@pytest.mark.parametrize("raw", [b"a\nb\r\n", b"a\rb\r\n", b"x" * 999 + b"\r\n", b"a\x00\r\n"])
def test_not_seven_bit_clean(raw: bytes) -> None:
    assert not om.seven_bit_clean(raw)


def test_install_id_and_generation(conn: sqlite3.Connection) -> None:
    iid = ident.install_id(conn)
    assert len(iid) == 32 and int(iid, 16) >= 0
    assert ident.generation(conn) == 0
    assert ident.header_value(conn) == f"{iid}.0"
    assert ident.parse_header(f" {iid}.3 ") == (iid, 3)
    assert ident.parse_header("yes") is None and ident.parse_header(f"{iid}") is None
