"""The MailSource contract (SPEC §3.3). Subclass MailSourceContract and provide a `harness`
fixture; the fake runs it in test_mail_fake.py, the IMAP adapter against Dovecot in step 2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Protocol

import pytest

from ecf.errors import InvalidInputError
from ecf_server.mail import FLAGGED, ROLES, SEEN, MailSource

KW = "$ecf_test_suspicious"


class Harness(Protocol):
    source: MailSource

    def deliver(self, raw: bytes, when: datetime) -> None: ...
    def expunge(self, uid: int) -> None: ...


def message(n: int, *, multipart: bool = False) -> bytes:
    m = EmailMessage()
    m["From"] = f"Sender {n} <s{n}@vendor-a.example>"
    m["To"] = "ap@acme.example"
    m["Subject"] = f"Test message {n}"
    m["Date"] = "Mon, 28 Sep 2026 12:00:00 +0000"
    m["Message-ID"] = f"<contract-{n}@synthetic.acme.example>"
    m.set_content(f"Plain body {n}\n")
    if multipart:
        m.add_alternative(f"<p>HTML body {n}</p>\n", subtype="html")
        m.add_attachment(
            b"%PDF-1.4 fake\n", maintype="application", subtype="pdf", filename="inv.pdf"
        )
    return m.as_bytes(policy=m.policy.clone(linesep="\r\n"))


DAY1 = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


class MailSourceContract:
    @pytest.fixture
    def harness(self) -> Harness:
        raise NotImplementedError

    def _fill(self, h: Harness, n: int = 3, *, multipart: bool = False) -> list[int]:
        for i in range(n):
            h.deliver(message(i, multipart=multipart), DAY1 + timedelta(days=i))
        return h.source.uids_after(0)

    def test_empty_inbox(self, harness: Harness) -> None:
        assert harness.source.uids_after(0) == []
        st = harness.source.inbox()
        assert st.uidvalidity > 0 and st.uidnext >= 1

    def test_uids_after_never_returns_the_last_uid(self, harness: Harness) -> None:
        uids = self._fill(harness)
        assert len(uids) == 3 and uids == sorted(uids)
        assert harness.source.uids_after(uids[-1]) == []  # the IMAP quirk is filtered
        assert harness.source.uids_after(uids[0]) == uids[1:]
        assert harness.source.inbox().uidnext > uids[-1]

    def test_meta_and_fetch(self, harness: Harness) -> None:
        uids = self._fill(harness)
        meta = harness.source.meta(uids)
        for i, u in enumerate(uids):
            raw = harness.source.fetch(u)
            assert raw is not None and raw == message(i)
            assert meta[u].size == len(raw)
            assert meta[u].message_id == f"<contract-{i}@synthetic.acme.example>"
            assert meta[u].internaldate.date() == (DAY1 + timedelta(days=i)).date()
            assert meta[u].internaldate.tzinfo is not None
        assert harness.source.fetch(uids[-1] + 100) is None

    def test_reads_never_set_seen(self, harness: Harness) -> None:
        (u, *_) = self._fill(harness, 1, multipart=True)
        harness.source.fetch(u)
        harness.source.fetch_part(u, "1", 100)
        harness.source.fetch_part(u, "HEADER", 100)
        assert SEEN not in harness.source.flags([u])[u]

    def test_fetch_part(self, harness: Harness) -> None:
        (u, *_) = self._fill(harness, 1, multipart=True)
        head = harness.source.fetch_part(u, "HEADER", 10_000)
        assert head is not None and b"Subject: Test message 0" in head and b"Plain body" not in head
        assert harness.source.fetch_part(u, "HEADER", 10) == head[:10]
        assert harness.source.fetch_part(u, "1.1", 1000) == b"Plain body 0\r\n"
        html = harness.source.fetch_part(u, "1.2", 1000)
        assert html is not None and b"HTML body 0" in html
        assert harness.source.fetch_part(u, "1.1", 5) == b"Plain"
        assert not harness.source.fetch_part(u, "9", 100)
        assert harness.source.fetch_part(u + 100, "HEADER", 100) is None

    def test_keywords_and_flag(self, harness: Harness) -> None:
        (u, *_) = self._fill(harness, 1)
        src = harness.source
        src.add_keyword(u, KW)
        src.set_flagged(u, True)
        assert {KW, FLAGGED} <= src.flags([u])[u]
        src.remove_keyword(u, KW)
        src.set_flagged(u, False)
        assert not {KW, FLAGGED} & src.flags([u])[u]
        for target in (u, u + 100):
            with pytest.raises(InvalidInputError):
                src.add_keyword(target, "bad keyword (x)")
        src.add_keyword(u + 100, KW)  # a vanished message is not an error
        src.set_flagged(u + 100, True)

    def test_existing_find_and_since(self, harness: Harness) -> None:
        uids = self._fill(harness)
        harness.expunge(uids[1])
        src = harness.source
        assert src.existing(uids) == {uids[0], uids[2]}
        assert src.find_message_id("<contract-2@synthetic.acme.example>") == [uids[2]]
        assert src.find_message_id("<nope@synthetic.acme.example>") == []
        assert src.uids_since(DAY1 + timedelta(days=2)) == [uids[2]]
        assert src.uids_since(DAY1) == [uids[0], uids[2]]

    def test_capabilities_and_folders(self, harness: Harness) -> None:
        src = harness.source
        caps = src.capabilities()
        assert caps.custom_keywords  # both test servers allow them; the probe reads it read-write
        assert src.inbox().permanent_flags <= {
            "\\Answered",
            "\\Deleted",
            "\\Draft",
            FLAGGED,
            SEEN,
            "\\*",
        }
        folders = src.folders()
        assert any(f.name.upper() == "INBOX" for f in folders)
        for f in folders:
            assert f.roles <= ROLES
