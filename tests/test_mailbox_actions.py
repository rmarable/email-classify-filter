"""Carrying out actions in the mailbox (V1.3 step 5b; SPEC §6.4, §8.3, §9.5): the checks at
execution, the order of writes, where a message went, label_folder, Undo, and the runner inside a
check. Against the fake mailbox, and once against Dovecot."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

import pytest

from ecf.errors import MailUnavailableError
from ecf.ids import AddressId, StableId
from ecf_server import decide, digests, execute, items, jobs, mailbox_actions, probe
from ecf_server.actions import MessageChangedError, keyword
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.mail import SEEN, Capabilities, Folder, MailSource
from ecf_server.mail.fake import FakeMailSource
from ecf_server.message import parse
from ecf_server.state_machine import Stage, Status, TransitionContext
from tests.mail_contract import message
from tests.test_decide import KNOWN_BULK, MARKETING, item_row

INSTALL = "t"
MB = 1024 * 1024


def _address(conn: sqlite3.Connection, clock: FakeClock, stage: str = "live",
             overrides: dict[str, Any] | None = None) -> None:  # fmt: skip
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at,"
                     " stage, overrides) VALUES ('ap', 'ap@acme.example', 'standard', 'A', ?, ?,"
                     " ?)", (to_ts(clock.now()), stage, json.dumps(overrides or {})))  # fmt: skip


def _mail_item(conn: sqlite3.Connection, clock: FakeClock, src: MailSource, n: int,
               classification: dict[str, Any], facts: dict[str, Any],
               deliver: Callable[[bytes], object]) -> str:  # fmt: skip
    raw = message(n)
    deliver(raw)
    uid = max(src.uids_after(0))
    sid = f"{n:02x}".ljust(64, "e")
    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                      content_hash=parse(raw, max_scan_bytes=10 * MB).content_hash,
                      facts=json.dumps(facts), subject="s", sender="news@vendor-a.example",
                      message_id=f"<contract-{n}@synthetic.acme.example>",
                      locator=json.dumps({"uid": uid,
                                          "uidvalidity": src.inbox().uidvalidity}))  # fmt: skip
    with write_tx(conn):
        conn.execute("UPDATE items SET classification = ? WHERE stable_id = ?",
                     (json.dumps(classification), sid))  # fmt: skip
    items.transition(conn, clock, StableId(sid), Status.CLASSIFIED, TransitionContext(),
                     actor="classifier")  # fmt: skip
    return sid


def _run(conn: sqlite3.Connection, clock: FakeClock, src: MailSource,
         lost: Callable[[], bool] = lambda: False) -> int:  # fmt: skip
    return mailbox_actions.run_in_check(conn, clock, src, "ap", INSTALL, 10 * MB, lost)


@pytest.fixture
def fake() -> FakeMailSource:
    return FakeMailSource()


def _archived(fake: FakeMailSource, n: int) -> list[int]:
    return fake.find_in("Archive", f"<contract-{n}@synthetic.acme.example>")


def test_live_archive_runs_in_the_check_and_records_where_it_went(
    conn: sqlite3.Connection, clock: FakeClock, fake: FakeMailSource
) -> None:
    _address(conn, clock)
    sid = _mail_item(conn, clock, fake, 0, MARKETING, KNOWN_BULK, fake.deliver)
    assert decide.apply(conn, clock, sid) is Status.EXECUTING
    assert conn.execute("SELECT next_due_at FROM check_state").fetchone()[0] == to_ts(clock.now())
    assert _run(conn, clock, fake) == 1
    assert fake.uids_after(0) == [] and len(_archived(fake, 0)) == 1
    [moved] = _archived(fake, 0)
    assert keyword(INSTALL, "marketing") in fake.elsewhere["Archive"][moved].flags
    row = item_row(conn, sid)
    assert row["status"] == "executed"
    assert json.loads(row["proposal"])["done"] == [
        {"name": "label", "target": "marketing"},
        {"name": "archive", "folder": "Archive"},
    ]


def test_mark_read_comes_before_the_move(conn: sqlite3.Connection, clock: FakeClock,
                                         fake: FakeMailSource) -> None:  # fmt: skip
    _address(conn, clock)
    notification = MARKETING | {"category": "notification", "sender_type": "automated"}
    sid = _mail_item(conn, clock, fake, 0, notification, KNOWN_BULK, fake.deliver)
    decide.apply(conn, clock, sid)
    _run(conn, clock, fake)
    [moved] = _archived(fake, 0)
    assert SEEN in fake.elsewhere["Archive"][moved].flags


@pytest.mark.parametrize(
    ("stage", "why"),
    [
        ("assist", "in assist: hiding mail needs live"),
        ("shadow", "in shadow: hiding mail needs live"),
    ],
)
def test_a_drop_from_live_refuses_at_execution(
    conn: sqlite3.Connection, clock: FakeClock, fake: FakeMailSource, stage: str, why: str
) -> None:
    _address(conn, clock)
    sid = _mail_item(conn, clock, fake, 0, MARKETING, KNOWN_BULK, fake.deliver)
    decide.apply(conn, clock, sid)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = ?", (stage,))
    _run(conn, clock, fake)
    assert item_row(conn, sid)["status"] == "failed"  # at once, not after 3 attempts
    assert fake.uids_after(0) != []  # nothing touched
    audit = conn.execute("SELECT data FROM audit WHERE event = 'action.failed'").fetchone()[0]
    assert why in audit
    assert conn.execute("SELECT status FROM grants WHERE stable_id = ?",
                        (sid,)).fetchone()[0] == "voided"  # fmt: skip


def test_a_folder_that_stopped_being_allowed_refuses(
    conn: sqlite3.Connection, clock: FakeClock, fake: FakeMailSource
) -> None:
    from ecf_server.actions import Planned  # noqa: PLC0415

    _address(conn, clock)
    sid = _mail_item(conn, clock, fake, 0, MARKETING, KNOWN_BULK, fake.deliver)
    why = mailbox_actions.refusal(conn, item_row(conn, sid), [Planned("move", "Receipts")],
                                  {f.name: f.roles for f in fake.folders()})  # fmt: skip
    assert why is not None and "isn't an allowed folder" in why
    no_junk = mailbox_actions.refusal(conn, item_row(conn, sid), [Planned("junk")],
                                      {"INBOX": frozenset()})  # fmt: skip
    assert no_junk == "the mailbox has no folder marked \\Junk"
    drafts = mailbox_actions.refusal(conn, item_row(conn, sid), [Planned("draft_reply")], {})
    assert drafts == "the mailbox has no folder marked \\Drafts"
    sends = mailbox_actions.refusal(conn, item_row(conn, sid), [Planned("reply_template")], {})
    assert sends is None  # a send's own checks are send_actions.refusal (V1.5 step 3a)


def test_a_lost_lease_writes_nothing_and_retries(
    conn: sqlite3.Connection, clock: FakeClock, fake: FakeMailSource
) -> None:
    _address(conn, clock)
    sid = _mail_item(conn, clock, fake, 0, MARKETING, KNOWN_BULK, fake.deliver)
    decide.apply(conn, clock, sid)
    run = mailbox_actions.executor_for(fake, INSTALL, 10 * MB, lambda: True)
    assert execute.run_once(conn, clock, run, address_id="ap")
    assert item_row(conn, sid)["status"] == "executing"  # retried later
    [uid] = fake.uids_after(0)
    assert fake.flags([uid])[uid] == frozenset()  # nothing written without the lease
    assert conn.execute("SELECT status FROM grants WHERE stable_id = ?",
                        (sid,)).fetchone()[0] == "approved"  # usable again  # fmt: skip


def test_a_changed_message_fails_without_a_retry(
    conn: sqlite3.Connection, clock: FakeClock, fake: FakeMailSource
) -> None:
    _address(conn, clock)
    sid = _mail_item(conn, clock, fake, 0, MARKETING, KNOWN_BULK, fake.deliver)
    decide.apply(conn, clock, sid)
    fake.expunge(fake.uids_after(0)[0])  # you moved it yourself
    _run(conn, clock, fake)
    assert item_row(conn, sid)["status"] == "failed"


def test_label_folder_gets_a_copy_of_suspicious_mail(
    conn: sqlite3.Connection, clock: FakeClock, fake: FakeMailSource
) -> None:
    _address(conn, clock, overrides={"label_folder": "Junk"})
    risky = MARKETING | {"category": "invoice", "fraud_risk": "high", "payment_related": True}
    sid = _mail_item(conn, clock, fake, 0, risky, KNOWN_BULK, fake.deliver)
    decide.apply(conn, clock, sid)
    _run(conn, clock, fake)
    assert fake.uids_after(0) != []  # still in INBOX: suspicious mail is never hidden
    assert len(fake.find_in("Junk", "<contract-0@synthetic.acme.example>")) == 1
    assert {"name": "copy", "folder": "Junk"} in json.loads(item_row(conn, sid)["proposal"])["done"]


def test_undo_moves_it_back_and_removes_what_ecf_added(
    conn: sqlite3.Connection, clock: FakeClock, fake: FakeMailSource
) -> None:
    _address(conn, clock)
    notification = MARKETING | {"category": "notification", "sender_type": "automated"}
    sid = _mail_item(conn, clock, fake, 0, notification, KNOWN_BULK, fake.deliver)
    decide.apply(conn, clock, sid)
    _run(conn, clock, fake)
    assert mailbox_actions.undoable(item_row(conn, sid))
    done = mailbox_actions.undo_item(conn, clock, fake, item_row(conn, sid), install=INSTALL,
                                     lost=lambda: False)  # fmt: skip
    assert done == ["archive back to INBOX", "label notification", "mark_read"]
    [back] = fake.uids_after(0)
    assert fake.flags([back])[back] == frozenset()
    assert item_row(conn, sid)["status"] == "undone"


def test_undo_refuses_when_the_message_id_is_ambiguous(
    conn: sqlite3.Connection, clock: FakeClock, fake: FakeMailSource
) -> None:
    _address(conn, clock)
    sid = _mail_item(conn, clock, fake, 0, MARKETING, KNOWN_BULK, fake.deliver)
    decide.apply(conn, clock, sid)
    _run(conn, clock, fake)
    fake.deliver(message(0))  # a second message with the same Message-ID (a sender controls it)
    fake.move(max(fake.uids_after(0)), "Archive")
    with pytest.raises(MessageChangedError, match="2 messages with this Message-ID"):
        mailbox_actions.undo_item(conn, clock, fake, item_row(conn, sid), install=INSTALL,
                                  lost=lambda: False)  # fmt: skip
    assert item_row(conn, sid)["status"] == "undo_failed"


def test_undo_never_touches_another_message_with_the_same_message_id(
    conn: sqlite3.Connection, clock: FakeClock, fake: FakeMailSource
) -> None:
    _address(conn, clock)
    notification = MARKETING | {"category": "notification", "sender_type": "automated"}
    sid = _mail_item(conn, clock, fake, 0, notification, KNOWN_BULK, fake.deliver)
    decide.apply(conn, clock, sid)
    _run(conn, clock, fake)  # archived, labelled, read
    fake.deliver(message(0).replace(b"Plain body 0", b"Other body 0"))  # reuses the Message-ID
    [other] = fake.uids_after(0)
    fake.set_seen(other, True)
    done = mailbox_actions.undo_item(conn, clock, fake, item_row(conn, sid), install=INSTALL,
                                     lost=lambda: False)  # fmt: skip
    assert done == ["archive back to INBOX", "label notification", "mark_read"]
    [back] = [u for u in fake.uids_after(0) if u != other]
    assert fake.flags([back])[back] == frozenset()
    assert fake.flags([other])[other] == frozenset({"\\Seen"})  # left alone


def test_the_digest_undo_button_queues_it_for_the_next_check(
    conn: sqlite3.Connection, clock: FakeClock, fake: FakeMailSource
) -> None:
    from ecf_server.slack_in import HANDLERS, Click  # noqa: PLC0415

    _address(conn, clock)
    sid = _mail_item(conn, clock, fake, 0, MARKETING, KNOWN_BULK, fake.deliver)
    decide.apply(conn, clock, sid)
    _run(conn, clock, fake)
    HANDLERS[digests.UNDO](conn, clock, Click("button", digests.UNDO, sid, "CAP", "U0ME1"))
    assert json.loads(item_row(conn, sid)["proposal"])["undo"] == "queued"
    assert digests.run_model_undos(conn, clock, fake, "ap", INSTALL, lambda: False) == 1
    assert item_row(conn, sid)["status"] == "undone" and fake.uids_after(0) != []


def test_an_undo_that_hits_a_mail_error_is_recorded_not_left_queued(
    conn: sqlite3.Connection, clock: FakeClock, fake: FakeMailSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    from ecf_server.slack_in import HANDLERS, Click  # noqa: PLC0415

    _address(conn, clock)
    sid = _mail_item(conn, clock, fake, 0, MARKETING, KNOWN_BULK, fake.deliver)
    decide.apply(conn, clock, sid)
    _run(conn, clock, fake)
    HANDLERS[digests.UNDO](conn, clock, Click("button", digests.UNDO, sid, "CAP", "U0ME1"))

    def dropped(*_a: object) -> list[int]:
        raise ConnectionResetError("connection reset")

    monkeypatch.setattr(fake, "find_in", dropped)
    assert digests.run_model_undos(conn, clock, fake, "ap", INSTALL, lambda: False) == 1
    row = item_row(conn, sid)
    assert row["status"] == "undo_failed" and json.loads(row["proposal"])["undo"] == "failed"


def test_jobs_run_only_in_their_own_address_check(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    assert jobs.claim(conn, clock, jobs.Queue.ACTIONS, "w", address_id="nobody") is None


# ---- against Dovecot ------------------------------------------------------------------------


@pytest.mark.imap
def test_archive_and_undo_on_a_real_server(conn: sqlite3.Connection, clock: FakeClock,
                                           dovecot_server: Any) -> None:  # fmt: skip
    from tests.test_mail_imap import DovecotHarness  # noqa: PLC0415

    h = DovecotHarness(dovecot_server)
    try:
        with write_tx(conn):
            conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                         " ('config.move_folders', ?, 't', 't')",
                         (json.dumps(["Archive"]),))  # fmt: skip
        _address(conn, clock)
        rules_move = MARKETING | {"category": "notification", "sender_type": "automated"}
        sid = _mail_item(conn, clock, h.source, 0, rules_move, KNOWN_BULK,
                         lambda raw: h.deliver(raw, clock.now()))  # fmt: skip
        # the image has no \\Archive folder: plan a move to the created "Archive" instead
        doc = {"actions": [{"name": "label", "target": "notification"},
                           {"name": "mark_read", "target": None},
                           {"name": "move", "target": "Archive"}]}  # fmt: skip
        with write_tx(conn):
            conn.execute("UPDATE items SET proposal = ? WHERE stable_id = ?",
                         (json.dumps(doc), sid))  # fmt: skip
        items.transition(conn, clock, StableId(sid), Status.PROPOSED,
                         TransitionContext(), actor="t")  # fmt: skip
        from ecf_server.actions import Planned  # noqa: PLC0415

        decide._run(conn, clock, sid, [Planned("label", "notification"), Planned("mark_read"),  # pyright: ignore[reportPrivateUsage]
                                       Planned("move", "Archive")],
                    TransitionContext(stage=Stage.LIVE))  # fmt: skip
        assert _run(conn, clock, h.source) == 1
        assert item_row(conn, sid)["status"] == "executed"
        assert h.source.uids_after(0) == []
        [there] = h.source.find_in("Archive", "<contract-0@synthetic.acme.example>")
        del there
        done = mailbox_actions.undo_item(conn, clock, h.source, item_row(conn, sid),
                                         install=INSTALL, lost=lambda: False)  # fmt: skip
        assert done == ["move back to INBOX", "label notification", "mark_read"]
        [back] = h.source.uids_after(0)
        assert h.source.flags([back])[back] - {"\\Recent"} == frozenset()  # the server's own
    finally:
        h.close()


# ---- Gmail (V1.6 step 4, OD-438) ----------------------------------------------------------------

GMAIL_CAPS = Capabilities(custom_keywords=True, move=True, uidplus=True, condstore=True, gmail=True)
ALL, SPAM, TRASH = "[Gmail]/All Mail", "[Gmail]/Spam", "[Gmail]/Trash"
GMAIL_FOLDERS = (Folder("INBOX", frozenset()), Folder(ALL, frozenset({"\\All"})),
                 Folder(SPAM, frozenset({"\\Junk"})), Folder(TRASH, frozenset({"\\Trash"})),
                 Folder("[Gmail]/Drafts", frozenset({"\\Drafts"})))  # fmt: skip
SPAM_CLS = MARKETING | {"category": "spam_or_phishing"}


def _gmail(conn: sqlite3.Connection, clock: FakeClock, folders: tuple[Folder, ...] = GMAIL_FOLDERS
           ) -> FakeMailSource:  # fmt: skip
    _address(conn, clock)
    src = FakeMailSource(caps=GMAIL_CAPS, folders=folders)
    with write_tx(conn):
        probe.store(conn, clock, "ap", "imap.gmail.com", probe.probe(src, "imap.gmail.com"))
    return src


def test_gmail_archive_goes_to_all_mail_and_undo_copies_it_back(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = _gmail(conn, clock)
    notification = MARKETING | {"category": "notification", "sender_type": "automated"}
    sid = _mail_item(conn, clock, src, 0, notification, KNOWN_BULK, src.deliver)
    [uid] = src.uids_after(0)
    msgid = src.gmail_msgid(uid)
    decide.apply(conn, clock, sid)
    _run(conn, clock, src)
    assert src.uids_after(0) == []
    done = json.loads(item_row(conn, sid)["proposal"])["done"]
    assert done[-1] == {"name": "archive", "folder": ALL, "gm_msgid": msgid}
    src.deliver(message(0))  # a sender reusing the Message-ID: Undo goes by Gmail's ID instead
    src.move(max(src.uids_after(0)), ALL)
    undone = mailbox_actions.undo_item(conn, clock, src, item_row(conn, sid), install=INSTALL,
                                       lost=lambda: False)  # fmt: skip
    assert undone == ["archive back to INBOX", "label notification", "mark_read"]
    [back] = src.uids_after(0)
    assert src.gmail_msgid(back) == msgid and src.flags([back])[back] == frozenset()
    assert len(src.gmail_find(ALL, int(msgid or 0))) == 1  # copied back, never moved out


def test_gmail_archive_is_refused_without_all_mail(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """R12: All Mail hidden from IMAP; there is no Archive folder to fall back on."""
    src = _gmail(conn, clock, tuple(f for f in GMAIL_FOLDERS if f.name != ALL))
    sid = _mail_item(conn, clock, src, 0, MARKETING, KNOWN_BULK, src.deliver)
    decide.apply(conn, clock, sid)
    _run(conn, clock, src)
    row = item_row(conn, sid)
    assert row["status"] == "failed" and len(src.uids_after(0)) == 1


def test_gmail_junk_undo_moves_it_back_by_gmail_id(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = _gmail(conn, clock)
    sid = _mail_item(conn, clock, src, 0, SPAM_CLS, KNOWN_BULK, src.deliver)
    decide.apply(conn, clock, sid)
    _run(conn, clock, src)
    assert src.uids_after(0) == [] and json.loads(item_row(conn, sid)["proposal"])["done"][-1][
        "folder"] == SPAM  # fmt: skip
    mailbox_actions.undo_item(conn, clock, src, item_row(conn, sid), install=INSTALL,
                              lost=lambda: False)  # fmt: skip
    assert len(src.uids_after(0)) == 1 and src.elsewhere[SPAM] == {}


def test_gmail_port_never_moves_out_of_or_deletes_in_all_mail_or_trash() -> None:
    """OD-438: a message there may be in no other folder."""
    src = FakeMailSource(caps=GMAIL_CAPS, folders=GMAIL_FOLDERS)
    uid = src.deliver(message(0))
    src.move(uid, ALL)
    [there] = src.find_in(ALL, "<contract-0@synthetic.acme.example>")
    for call in (lambda: src.move_back(ALL, there), lambda: src.delete_in(ALL, there),
                 lambda: src.delete_in(TRASH, 1)):  # fmt: skip
        with pytest.raises(MailUnavailableError, match="Gmail: ecf never"):
            call()


def test_a_label_the_provider_cant_keep_is_skipped_and_not_undone(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """OD-439: planned before a probe showed it (or the probe changed), the executor skips it."""
    _address(conn, clock)
    src = FakeMailSource()
    notification = MARKETING | {"category": "notification", "sender_type": "automated"}
    sid = _mail_item(conn, clock, src, 0, notification, KNOWN_BULK, src.deliver)
    decide.apply(conn, clock, sid)  # planned with labels: no probe yet
    with write_tx(conn):
        probe.store(conn, clock, "ap", "imap.proton.example", probe.probe(
            FakeMailSource(caps=Capabilities(custom_keywords=False, move=True, uidplus=True,
                                             condstore=True)), "imap.proton.example"))  # fmt: skip
    _run(conn, clock, src)
    row = item_row(conn, sid)
    assert row["status"] == "executed"
    assert [r["name"] for r in json.loads(row["proposal"])["done"]] == ["mark_read", "archive"]
    undone = mailbox_actions.undo_item(conn, clock, src, item_row(conn, sid), install=INSTALL,
                                       lost=lambda: False)  # fmt: skip
    assert undone == ["archive back to INBOX", "mark_read"]
