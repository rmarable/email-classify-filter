"""Large messages parsed and verified in a child process (OD-195)."""

from __future__ import annotations

import sqlite3
from email.message import EmailMessage
from pathlib import Path

import pytest

from ecf_server import checks, db, isolate
from ecf_server.clock import FakeClock
from ecf_server.dnscache import DnsCache
from ecf_server.isolate import IsolationError, subprocess_isolator
from ecf_server.message import parse
from ecf_server.senderauth import evaluate
from tests.test_senderauth import FakeDns, ed25519_key, mail, publish, sign


def invoice(from_header: str | None) -> bytes:
    m = EmailMessage()
    if from_header:
        m["From"] = from_header
    m["To"] = "ap@acme.example"
    m["Subject"] = "Invoice 7 due soon"
    m["Message-ID"] = "<i7@vendor-a.example>"
    m.set_content("Please pay invoice 7.\n")
    m.add_alternative("<p>Please <b>pay</b> invoice 7.</p>", subtype="html")
    m.add_attachment(
        b"%PDF-1.4 " + bytes(range(256)) * 40,
        maintype="application",
        subtype="pdf",
        filename="INV-7.pdf",
    )
    return m.as_bytes()


def test_the_child_matches_in_process_parsing(db_path: Path, conn: sqlite3.Connection) -> None:
    raw = invoice(None)  # no From: sender authentication answers without any DNS query
    parsed, auth = subprocess_isolator(db_path, dns_cap_s=600)(raw, 1000)
    assert parsed == parse(raw, max_scan_bytes=1000)
    assert (auth.result, auth.reason) == ("none", "no usable From address")


def test_two_from_headers_fail_in_the_child(db_path: Path, conn: sqlite3.Connection) -> None:
    raw = b"From: a@vendor-a.example\r\nFrom: b@vendor-a.example\r\nSubject: x\r\n\r\nbody\r\n"
    _, auth = subprocess_isolator(db_path, dns_cap_s=600)(raw, 1000)
    assert auth.result == "fail"


def test_a_failing_child_names_only_the_error_type(tmp_path: Path) -> None:
    run = subprocess_isolator(Path("/dev/null/no.db"), dns_cap_s=600)  # can't open a database
    with pytest.raises(IsolationError, match=r"^child exited 3: \w+Error$"):
        run(invoice(None), 1000)


def test_result_round_trip_keeps_signatures(conn: sqlite3.Connection, clock: FakeClock) -> None:
    key, fake = ed25519_key("vendor-a.example"), FakeDns()
    publish(fake, key)
    raw = sign(mail(), key)
    parsed = parse(raw)
    auth = evaluate(raw, parsed, DnsCache(conn, clock, lookup=fake))
    assert auth.result == "pass" and auth.signatures
    assert isolate.decode(isolate.encode(parsed, auth)) == (parsed, auth)


def test_checks_use_an_isolator_only_with_a_database_file(db_path: Path) -> None:
    file_conn = db.connect(db_path)
    try:
        assert checks._isolator(file_conn) is not None  # pyright: ignore[reportPrivateUsage]
    finally:
        file_conn.close()
    memory = sqlite3.connect(":memory:")
    memory.row_factory = sqlite3.Row
    assert checks._isolator(memory) is None  # pyright: ignore[reportPrivateUsage]
