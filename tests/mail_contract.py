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
    move_target: str  # a folder moves may go to (V1.3 step 5)

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

    def test_gmail_calls_are_empty_off_gmail(self, harness: Harness) -> None:
        """V1.6 (OD-438): neither test server is Gmail, so Gmail mode is off."""
        uids = self._fill(harness, 1)
        assert not harness.source.capabilities().gmail
        assert harness.source.gmail_labels(uids) == {}
        assert harness.source.gmail_inbox_counts() is None

    def test_structure(self, harness: Harness) -> None:
        harness.deliver(message(0, multipart=True), DAY1)
        harness.deliver(message(1), DAY1)
        multi, single = harness.source.uids_after(0)
        parts = harness.source.structure(multi)
        assert parts is not None
        shape = [(p.section, p.content_type, p.disposition, p.filename) for p in parts]
        assert shape == [
            ("1.1", "text/plain", None, None),
            ("1.2", "text/html", None, None),
            ("2", "application/pdf", "attachment", "inv.pdf"),
        ]
        assert parts[0].charset == "utf-8" and parts[2].encoding == "base64"
        assert all(p.size > 0 for p in parts)
        (only,) = harness.source.structure(single) or []
        assert (only.section, only.content_type) == ("1", "text/plain")
        assert harness.source.structure(single + 100) is None

    # -- V1.3 step 5: hide actions and their undo ------------------------------------------------

    def _archive(self, h: Harness) -> str:
        return h.move_target

    def test_set_seen(self, harness: Harness) -> None:
        [uid] = self._fill(harness, 1)
        harness.source.set_seen(uid, True)
        assert SEEN in harness.source.flags([uid])[uid]
        harness.source.set_seen(uid, False)
        assert SEEN not in harness.source.flags([uid])[uid]

    def test_move_find_fetch_and_move_back(self, harness: Harness) -> None:
        src = harness.source
        uids = self._fill(harness, 2)
        raw = src.fetch(uids[0])
        folder = self._archive(harness)
        src.move(uids[0], folder)
        assert src.uids_after(0) == [uids[1]]  # gone from INBOX, the other untouched
        [there] = src.find_in(folder, "<contract-0@synthetic.acme.example>")
        assert src.fetch_in(folder, there) == raw
        assert src.find_in(folder, "<contract-1@synthetic.acme.example>") == []
        src.move_back(folder, there)
        back = src.find_message_id("<contract-0@synthetic.acme.example>")
        assert len(back) == 1 and src.fetch(back[0]) == raw
        assert src.find_in(folder, "<contract-0@synthetic.acme.example>") == []

    def test_copy_back_puts_a_copy_in_inbox(self, harness: Harness) -> None:
        """V1.6 step 4: Gmail's archive Undo; generic IMAP COPY into INBOX elsewhere."""
        src = harness.source
        [uid] = self._fill(harness, 1)
        raw = src.fetch(uid)
        folder = self._archive(harness)
        src.move(uid, folder)
        assert src.gmail_msgid(uid) is None  # not Gmail, and gone from INBOX anyway
        [there] = src.find_in(folder, "<contract-0@synthetic.acme.example>")
        assert src.gmail_find(folder, 1) == []  # not Gmail
        src.copy_back(folder, there)
        [back] = src.find_message_id("<contract-0@synthetic.acme.example>")
        assert src.fetch(back) == raw and src.fetch_in(folder, there) == raw  # both kept

    def test_copy_leaves_the_message_in_inbox(self, harness: Harness) -> None:
        src = harness.source
        [uid] = self._fill(harness, 1)
        folder = self._archive(harness)
        src.copy(uid, folder)
        assert src.existing([uid]) == {uid}
        assert len(src.find_in(folder, "<contract-0@synthetic.acme.example>")) == 1

    def test_append_then_delete_in_a_folder(self, harness: Harness) -> None:
        src = harness.source
        folder = self._archive(harness)
        raw = message(7)
        src.append(folder, raw, ["\\Draft", "\\Seen"])
        [there] = src.find_in(folder, "<contract-7@synthetic.acme.example>")
        assert src.fetch_in(folder, there) == raw
        assert src.uids_after(0) == []  # nothing arrived in INBOX
        src.append(folder, message(8))
        src.delete_in(folder, there)
        assert src.find_in(folder, "<contract-7@synthetic.acme.example>") == []
        assert len(src.find_in(folder, "<contract-8@synthetic.acme.example>")) == 1  # untouched
