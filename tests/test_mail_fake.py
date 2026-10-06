from datetime import datetime

import pytest

from ecf.errors import MailUnavailableError
from ecf_server.mail import SEEN, MailSource
from ecf_server.mail.fake import (
    GMAIL_ALL,
    GMAIL_CAPS,
    GMAIL_FOLDERS,
    GMAIL_SPAM,
    GMAIL_TRASH,
    FakeMailSource,
    GmailFakeSource,
)
from tests.mail_contract import Harness, MailSourceContract, message


class FakeHarness:
    def __init__(self) -> None:
        self.fake = FakeMailSource(uidvalidity=7)
        self.source: MailSource = self.fake
        self.move_target = "Archive"

    def deliver(self, raw: bytes, when: datetime) -> None:
        self.fake.deliver(raw, when)

    def expunge(self, uid: int) -> None:
        self.fake.expunge(uid)


class TestFakeContract(MailSourceContract):
    @pytest.fixture
    def harness(self) -> Harness:
        return FakeHarness()


def test_reset_renumbers_under_a_new_uidvalidity() -> None:
    f = FakeMailSource(uidvalidity=7)
    f.deliver(message(0))
    f.deliver(message(1))
    f.expunge(1)
    f.reset(8)
    assert f.inbox().uidvalidity == 8
    assert f.uids_after(0) == [1]
    assert f.fetch(1) == message(1)


# ---- GmailFakeSource (V1.6 step 6, OD-438): the contract, and Gmail's ways from step 0 ----------

LABEL = "Receipts"  # a user label folder


class GmailHarness:
    def __init__(self) -> None:
        self.fake = GmailFakeSource(user_labels=[LABEL])
        self.source: MailSource = self.fake
        self.move_target = LABEL

    def deliver(self, raw: bytes, when: datetime) -> None:
        self.fake.deliver(raw, when)

    def expunge(self, uid: int) -> None:
        self.fake.expunge(uid)


class TestGmailFakeContract(MailSourceContract):
    @pytest.fixture
    def harness(self) -> Harness:
        return GmailHarness()


MID0 = "<contract-0@synthetic.acme.example>"


def test_gmail_archive_keeps_one_message_and_undo_gives_a_new_inbox_uid() -> None:
    g = GmailFakeSource()
    uid = g.deliver(message(0))
    g.add_keyword(uid, "$ecf_t_x")
    msgid = g.gmail_msgid(uid)
    assert msgid is not None
    g.move(uid, GMAIL_ALL)
    assert g.uids_after(0) == [] and g.message(msgid) == (frozenset(), frozenset({"$ecf_t_x"}))
    [there] = g.gmail_find(GMAIL_ALL, msgid)
    g.copy_back(GMAIL_ALL, there)
    [back] = g.uids_after(0)
    assert back != uid and g.gmail_msgid(back) == msgid and g.flags([back])[back] == {"$ecf_t_x"}
    assert g.gmail_find(GMAIL_ALL, msgid) == [there]  # no duplicate in All Mail


def test_gmail_label_folder_swaps_labels_and_inbox_expunge_archives() -> None:
    g = GmailFakeSource(user_labels=[LABEL])
    uid = g.deliver(message(0))
    msgid = g.gmail_msgid(uid) or 0
    g.move(uid, LABEL)
    assert g.message(msgid) == (frozenset({LABEL}), frozenset())
    [there] = g.find_in(LABEL, MID0)
    g.move_back(LABEL, there)
    [back] = g.uids_after(0)
    assert g.gmail_labels([back])[back] == {"\\Inbox"} and g.find_in(LABEL, MID0) == []
    g.delete_in("INBOX", back)  # \Deleted + UID EXPUNGE in INBOX: archived, not deleted
    assert g.uids_after(0) == [] and len(g.find_in(GMAIL_ALL, MID0)) == 1


def test_gmail_append_merges_by_message_id() -> None:
    """A send to oneself is one message labelled Sent and Inbox; ecf's own copy appended to Sent
    merges into it under a new Sent UID (step 0, phase D)."""
    g = GmailFakeSource()
    sent = "[Gmail]/Sent Mail"
    uid = g.deliver(message(0), labels=("\\Sent", "\\Inbox"))
    [before] = g.find_in(sent, MID0)
    g.append(sent, message(0), ["\\Seen"])
    [after] = g.find_in(sent, MID0)
    assert after != before and len(g.find_in(GMAIL_ALL, MID0)) == 1
    assert g.gmail_labels([uid])[uid] == {"\\Sent", "\\Inbox"} and g.flags([uid])[uid] == {SEEN}


def test_gmail_drafts_delete_for_good_and_all_mail_expunge() -> None:
    g = GmailFakeSource()
    drafts = "[Gmail]/Drafts"
    g.append(drafts, message(7), ["\\Draft"])
    [d] = g.find_in(drafts, "<contract-7@synthetic.acme.example>")
    g.delete_in(drafts, d)
    assert g.find_in(GMAIL_ALL, "<contract-7@synthetic.acme.example>") == []
    assert g.find_in(GMAIL_TRASH, "<contract-7@synthetic.acme.example>") == []
    uid = g.deliver(message(0))
    [a] = g.find_in(GMAIL_ALL, MID0)
    g.user_expunge(GMAIL_ALL, a)  # also in INBOX: no effect (step 0)
    assert g.uids_after(0) == [uid] and g.find_in(GMAIL_ALL, MID0) == [a]
    g.move(uid, GMAIL_ALL)
    g.user_expunge(GMAIL_ALL, a)  # only in All Mail: to Trash (unverified)
    assert g.find_in(GMAIL_ALL, MID0) == [] and len(g.find_in(GMAIL_TRASH, MID0)) == 1


def test_gmail_port_refuses_all_mail_and_trash() -> None:
    g = GmailFakeSource()
    g.move(g.deliver(message(0)), GMAIL_ALL)
    [there] = g.find_in(GMAIL_ALL, MID0)
    for call in (lambda: g.move_back(GMAIL_ALL, there), lambda: g.delete_in(GMAIL_ALL, there),
                 lambda: g.delete_in(GMAIL_TRASH, 1)):  # fmt: skip
        with pytest.raises(MailUnavailableError, match="Gmail: ecf never"):
            call()


def test_gmail_junk_and_starred_are_views() -> None:
    g = GmailFakeSource()
    uid = g.deliver(message(0))
    g.set_flagged(uid, True)
    assert len(g.find_in("[Gmail]/Starred", MID0)) == 1
    g.move(uid, GMAIL_SPAM)
    assert g.find_in(GMAIL_ALL, MID0) == [] and g.find_in("[Gmail]/Starred", MID0) == []
    [spam] = g.find_in(GMAIL_SPAM, MID0)
    g.move_back(GMAIL_SPAM, spam)
    assert len(g.uids_after(0)) == 1 and g.find_in(GMAIL_SPAM, MID0) == []


def test_gmail_inbox_limit_and_hidden_all_mail() -> None:
    """OD-440: the folder size limit shows only the newest; R12: All Mail hidden from IMAP."""
    g = GmailFakeSource()
    uids = [g.deliver(message(i)) for i in range(3)]
    g.inbox_limit = 2
    g.deliver(message(3))
    assert g.gmail_inbox_counts() == (2, 4) and uids[0] not in g.uids_after(0)
    hidden = GmailFakeSource(folders=tuple(f for f in GMAIL_FOLDERS if f.name != GMAIL_ALL))
    hidden.deliver(message(0))
    assert hidden.gmail_inbox_counts() is None


def test_plain_fake_refuses_gmail_caps() -> None:
    with pytest.raises(ValueError, match="GmailFakeSource"):
        FakeMailSource(caps=GMAIL_CAPS)
