"""The per-address probe (V1.1 step 4)."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from typing import Any

import pytest

from ecf_server import addresses as ad
from ecf_server import probe
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.mail import Capabilities, Folder, MailSource
from ecf_server.mail.fake import DEFAULT_FOLDERS, FakeMailSource, GmailFakeSource
from ecf_server.mail.imap import _append_limit  # pyright: ignore[reportPrivateUsage]
from ecf_server.secretstore.memory import MemorySecretStore
from tests.mail_contract import message

FULL = Capabilities(custom_keywords=True, move=True, uidplus=True, condstore=True)


def test_known_provider_fills_what_imap_cant_tell() -> None:
    r = probe.probe(FakeMailSource(caps=FULL), "IMAP.purelymail.com")
    assert r.saves_sent is False and r.max_message_bytes == 51_200_000
    assert r.max_size_source is not None and "tested 2026-09-28" in r.max_size_source
    assert r.roles == {
        "\\Archive": "Archive",
        "\\Drafts": "Drafts",
        "\\Junk": "Junk",
        "\\Sent": "Sent",
        "\\Trash": "Trash",
    }
    assert r.warnings == []


def test_unknown_provider_warns_about_the_unknowns() -> None:
    r = probe.probe(FakeMailSource(caps=FULL), "imap.other.example")
    assert r.saves_sent is None and r.max_message_bytes is None
    assert any("size limit is unknown" in w for w in r.warnings)
    assert any("saves sent mail is unknown" in w for w in r.warnings)


def test_appendlimit_wins_over_the_table() -> None:
    caps = Capabilities(True, True, True, True, append_limit=10_000_000)
    r = probe.probe(FakeMailSource(caps=caps), "imap.purelymail.com")
    assert (r.max_message_bytes, r.max_size_source) == (10_000_000, "APPENDLIMIT")


def test_missing_features_become_warnings() -> None:
    caps = Capabilities(custom_keywords=False, move=False, uidplus=False, condstore=False)
    folders = (Folder("INBOX", frozenset()), Folder("Stuff", frozenset()))
    r = probe.probe(FakeMailSource(caps=caps, folders=folders), "imap.purelymail.com")
    text = " ".join(r.warnings)
    for needle in ("custom keywords", "no Archive", "no Junk", "no Sent", "no Drafts", "MOVE"):
        assert needle in text, needle


def test_first_folder_with_a_role_wins() -> None:
    folders = (*DEFAULT_FOLDERS, Folder("Archive2", frozenset({"\\Archive"})))
    r = probe.probe(FakeMailSource(caps=FULL, folders=folders), "imap.purelymail.com")
    assert r.roles["\\Archive"] == "Archive"


GMAIL_FOLDERS = (Folder("INBOX", frozenset()), Folder("[Gmail]/All Mail", frozenset({"\\All"})),
                 Folder("[Gmail]/Sent Mail", frozenset({"\\Sent"})),
                 Folder("[Gmail]/Drafts", frozenset({"\\Drafts"})),
                 Folder("[Gmail]/Spam", frozenset({"\\Junk"})))  # fmt: skip


def test_gmail_facts_follow_the_capability_not_the_host() -> None:
    """OD-438: X-GM-EXT-1 decides; Gmail saves sent mail; its APPENDLIMIT is an upload limit."""
    r = probe.probe(GmailFakeSource(folders=GMAIL_FOLDERS), "mail.example")
    assert r.gmail and r.saves_sent is True and r.gmail_inbox == (0, 0)
    assert (r.max_message_bytes, r.max_size_source) == (35_651_584, "APPENDLIMIT")
    assert not any("All Mail" in w or "folder size" in w for w in r.warnings)
    plain = probe.probe(FakeMailSource(caps=FULL), "imap.gmail.com")  # the host alone: not Gmail
    assert not plain.gmail and plain.saves_sent is None and plain.gmail_inbox is None


def test_gmail_warns_when_all_mail_is_hidden_or_the_inbox_is_limited() -> None:
    """R12 and OD-440: archive needs All Mail; a folder size limit hides inbox mail."""
    hidden = tuple(f for f in GMAIL_FOLDERS if "\\All" not in f.roles)
    r = probe.probe(GmailFakeSource(folders=hidden), "imap.gmail.com")
    assert probe.GMAIL_NO_ALL_MAIL in r.warnings and r.gmail_inbox is None
    src = GmailFakeSource(folders=GMAIL_FOLDERS)
    for i in range(30):
        src.deliver(message(i))
    src.inbox_limit = 30 - probe.LIMIT_SLACK - 1
    r = probe.probe(src, "imap.gmail.com")
    assert any(w.startswith("Gmail shows 19 of the 30 messages") for w in r.warnings)
    src.inbox_limit = 30 - probe.LIMIT_SLACK  # as if mail arrived between the two counts
    assert not any("folder size" in w for w in probe.probe(src, "imap.gmail.com").warnings)


def test_appendlimit_parsing() -> None:
    assert _append_limit(frozenset({"IMAP4REV1", "APPENDLIMIT=35651584"})) == 35_651_584
    assert _append_limit(frozenset({"APPENDLIMIT"})) is None  # no value: per-folder limits
    assert _append_limit(frozenset({"IDLE"})) is None


def test_store_and_load_round_trip(conn: sqlite3.Connection, clock: FakeClock) -> None:
    conn.execute(
        "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
        " VALUES ('ap', 'ap@acme.example', 'high', 'A', 'now')"
    )
    r = probe.probe(FakeMailSource(caps=FULL), "imap.other.example")
    with write_tx(conn):
        probe.store(conn, clock, "ap", "imap.other.example", r)
    loaded = probe.load(conn, "ap")
    assert loaded is not None
    assert loaded["roles"] == r.roles and loaded["warnings"] == r.warnings
    assert loaded["capabilities"] == {
        "append_limit": None,
        "condstore": True,
        "gmail": False,
        "gmail_inbox": None,
        "move": True,
        "uidplus": True,
    }
    assert loaded["saves_sent"] is None and loaded["custom_keywords"] is True
    assert probe.load(conn, "nope") is None


def test_adding_an_address_stores_its_probe(conn: sqlite3.Connection, clock: FakeClock) -> None:
    def factory(_h: str, _u: str, _p: Callable[[], str]) -> MailSource:
        return FakeMailSource(caps=FULL)

    req = ad.AddRequest(
        "ap@acme.example", "imap.purelymail.com", "high", "A", "pw", org_domains=["acme.example"]
    )
    a = ad.add_address(conn, clock, MemorySecretStore(), factory, req, actor="os_user")
    assert a["probe"]["saves_sent"] is False and a["probe"]["warnings"] == []
    assert ad.list_addresses(conn)[0]["probe"]["max_message_bytes"] == 51_200_000


@pytest.mark.imap
def test_probe_against_dovecot(dovecot_server: Any) -> None:
    from ecf_server.mail.imap import ImapSource  # noqa: PLC0415
    from tests import dovecot  # noqa: PLC0415

    dv: dovecot.Dovecot = dovecot_server
    src = ImapSource(
        dv.host,
        f"ecf-t-{uuid.uuid4().hex[:12]}",
        lambda: dovecot.PASSWORD,
        port=dv.port,
        ssl_context=dv.context(),
    )
    try:
        r = probe.probe(src, "imap.dovecot.example")
    finally:
        src.close()
    assert r.custom_keywords and r.move and r.uidplus
    assert r.max_message_bytes is None or r.max_size_source == "APPENDLIMIT"
