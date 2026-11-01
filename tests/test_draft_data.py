"""Draft data (M8 plan §7, OD-583): each version, what became of it, bounded tracking, ratings, the
stats, the weekly post and pruning. Drafts go through `actor.decide_one`, as Claude's do."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import click
import httpx
import pytest
from typer.testing import CliRunner

from ecf import cli_drafts
from ecf.cli import app
from ecf.errors import ConflictError, InvalidInputError, MailUnavailableError
from ecf.eval.metrics import newcombe_unpaired
from ecf.ids import StableId
from ecf.status import Status
from ecf_server import (
    actor,
    approvals,
    checks,
    decide,
    digests,
    draft_data,
    draft_stats,
    draft_versions,
    drafts_weekly,
    items,
    mailbox_actions,
    outbound_msg,
    retention,
    slack_admin,
)
from ecf_server.clock import FakeClock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.mail import Folder
from ecf_server.mail.fake import FakeMailSource
from ecf_server.notify import FakeNotifier
from ecf_server.state_machine import Origin, TransitionContext
from tests.test_decide import item_row
from tests.test_drafts import FACTS, REPLY
from tests.test_mailbox_actions import (  # pyright: ignore[reportPrivateUsage]
    MB,
    _address,  # pyright: ignore[reportPrivateUsage]
    _mail_item,  # pyright: ignore[reportPrivateUsage]
    _run,  # pyright: ignore[reportPrivateUsage]
)

DRAFT = ("Hello,\n\nThanks for your note. I will look at this for [topic] and reply soon.\n\n"
         "Best regards,\n[Your Name]")  # fmt: skip
SECRET_WORD = "zebrafinch"


def _propose(conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource, n: int = 0,
             text: str = DRAFT, facts: dict[str, Any] | None = None) -> str:  # fmt: skip
    sid = _mail_item(conn, clock, src, n, REPLY, FACTS if facts is None else facts, src.deliver)
    with write_tx(conn):
        conn.execute("INSERT INTO excerpts (stable_id, actor_text) VALUES (?, 'Please confirm.')",
                     (sid,))  # fmt: skip
    _decide(conn, clock, sid, text)
    return sid


def _decide(conn: sqlite3.Connection, clock: FakeClock, sid: str, text: str,
            action: str = "draft_reply") -> None:  # fmt: skip
    item = item_row(conn, sid)
    ctx, p = decide.plan_for(conn, item)
    actor.decide_one(conn, clock, item, ctx, p,
                     {"action": action, "target": "", "reason": "r", "text": text,
                      "model": "claude-opus-5-5"},
                     frozenset())  # fmt: skip


def _saved(conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource, n: int = 0,
           text: str = DRAFT) -> str:  # fmt: skip
    sid = _propose(conn, clock, src, n, text)
    approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")
    assert _run(conn, clock, src) == 1
    return sid


def _row(conn: sqlite3.Connection, sid: str, version: int = 1) -> sqlite3.Row:
    return conn.execute("SELECT * FROM draft_versions WHERE stable_id = ? AND version = ?",
                        (sid, version)).fetchone()  # fmt: skip


def _mid(conn: sqlite3.Connection, sid: str) -> str:
    doc = json.loads(item_row(conn, sid)["proposal"])
    return next(r["message_id"] for r in doc["done"] if r["name"] == "draft_reply")


def _copy(mid: str, body: str) -> bytes:
    return outbound_msg.build_draft(
        from_addr="ap@acme.example", to_addr="news@vendor-a.example", subject="s", body=body,
        in_reply_to=None, references=None, date=datetime(2026, 10, 31, tzinfo=UTC), message_id=mid,
    ).raw  # fmt: skip


def _send(src: FakeMailSource, mid: str, body: str, *, leave_draft: bool = False) -> None:
    if not leave_draft:
        for uid in src.find_in("Drafts", mid):
            src.delete_in("Drafts", uid)
    src.append("Sent", _copy(mid, body), [])


def _track(conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource,
           lost: Callable[[], bool] = lambda: False) -> int:  # fmt: skip
    return draft_data.track_in_check(conn, clock, src, "ap", "install", 10 * MB, lost)


FILLED = ("Hello,\n\nThanks for your note. I will look at this for the renewal and reply soon.\n\n"
          "Best regards,\nPat Example")  # fmt: skip


@pytest.fixture
def addr(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock)


# What counts as the same text.


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("a  b\n\n c", "a b c"),
        ("mine\n> quoted\n>> more", "mine"),
        ("mine\nOn Mon, 2 Nov 2026 at 10:00, Pat <p@acme.example> wrote:\n> q", "mine"),
        ("mine\nOn Mon, 2 Nov 2026 at 10:00, Pat\n<p@acme.example> wrote:\n> q", "mine"),
        ("mine\n-- \nPat\nTitle", "mine"),
        ("mine\n--\nPat", "mine"),
        ("mine\n-----Original Message-----\nFrom: x", "mine"),
        ("mine\nFrom: Pat\nSent: Monday\nTo: Lee", "mine"),
        (
            "From: the desk of Pat, greetings\nnext line",
            "From: the desk of Pat, greetings next line",
        ),
        ("mine\r\nsecond", "mine second"),
    ],
)
def test_normalise_keeps_only_the_reply(raw: str, want: str) -> None:
    assert draft_versions.normalise(raw) == want


@pytest.mark.parametrize(
    ("sent", "same"),
    [
        (DRAFT, True),  # nothing filled in
        (FILLED, True),
        (FILLED + "\nFinance Lead\nAcme\n+1 555 0100", True),  # a sign-off of a few lines
        (FILLED.replace("\n\n", "\n"), True),  # re-wrapped
        (FILLED + "\n\nOn Mon, Pat wrote:\n> the email", True),  # the client quotes
        (FILLED.replace("reply soon", "reply on Friday"), False),
        (FILLED.replace("Thanks for your note. ", ""), False),
        ("Added first line.\n" + FILLED, False),
        (FILLED + " " + "x" * (draft_versions.CLOSING_ROOM + 1), False),  # more than a sign-off
        (DRAFT.replace("[topic]", "y" * (draft_versions.INLINE_ROOM + 1)), False),
        ("", False),
    ],
)
def test_filled_placeholders_count_as_unchanged(sent: str, same: bool) -> None:
    assert draft_versions.unchanged(DRAFT, sent) is same


def test_a_placeholder_mid_text_with_text_after_it() -> None:
    d = "See you on [date] at the office."
    assert draft_versions.unchanged(d, "See you on Friday at the office.")
    assert not draft_versions.unchanged(d, "See you on Friday at the office. Extra.")
    assert not draft_versions.unchanged(d, "See you at the office.")
    long = "y" * (draft_versions.INLINE_ROOM + 1)
    assert not draft_versions.unchanged(d, f"See you on {long} at the office.")


def test_no_placeholder_needs_the_same_text() -> None:
    assert draft_versions.unchanged("Thanks.", "  Thanks.\n")
    assert not draft_versions.unchanged("Thanks.", "Thanks!")


# A version for each draft, with no text in it.


@pytest.mark.usefixtures("addr")
def test_a_draft_records_a_version_of_numbers_and_hashes(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _propose(conn, clock, FakeMailSource(), text=f"Thanks, {SECRET_WORD}.\n\n[Your Name]")
    r = _row(conn, sid)
    assert r["outcome"] is None
    assert (r["category"], r["sender_type"], r["sender_seen_before"]) == ("invoice", "vendor", 1)
    assert r["drafter_model"] == "claude-opus-5-5"
    assert r["draft_chars"] == len(f"Thanks, {SECRET_WORD}.\n\n[Your Name]")
    assert r["email_chars"] == len("Please confirm.")
    assert len(r["normalised_hash"]) == 64
    assert SECRET_WORD not in json.dumps(dict(r))
    events = conn.execute("SELECT data FROM audit WHERE event = 'draft.created'").fetchall()
    assert len(events) == 1
    assert SECRET_WORD not in events[0]["data"]


@pytest.mark.usefixtures("addr")
def test_a_refused_draft_records_its_codes_and_no_text(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _propose(conn, clock, FakeMailSource(),
                   text=f"{SECRET_WORD}: I will pay the 2,000 invoice on 2026-11-30."
                        "\n\n[Your Name]")  # fmt: skip
    r = _row(conn, sid)
    assert r["outcome"] == "refused"
    assert "date" in json.loads(r["refuse_codes"])
    assert SECRET_WORD not in json.dumps(dict(r))
    [event] = conn.execute("SELECT data FROM audit WHERE event = 'draft.refused'").fetchall()
    assert "date" in json.loads(event["data"])["codes"]
    assert SECRET_WORD not in event["data"]


@pytest.mark.usefixtures("addr")
def test_copied_links_are_counted(conn: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    sid = _mail_item(conn, clock, src, 0, REPLY, FACTS, src.deliver)
    with write_tx(conn):
        conn.execute("INSERT INTO excerpts (stable_id, actor_text) VALUES (?, ?)",
                     (sid, "See https://acme.example/terms please."))  # fmt: skip
    _decide(conn, clock, sid, "The terms are at https://acme.example/terms.\n\n[Your Name]")
    assert _row(conn, sid)["links_copied"] == 1


@pytest.mark.usefixtures("addr")
def test_the_same_text_is_the_same_version_and_new_text_replaces_it(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sid = _propose(conn, clock, src)
    item = item_row(conn, sid)
    ctx = decide.context(conn, item)
    assert draft_versions.record(conn, clock, item, ctx, DRAFT, model="m", refused=()) == 1
    assert draft_versions.record(conn, clock, item, ctx, DRAFT + " Also.", model="m",
                                 refused=()) == 2  # fmt: skip
    assert _row(conn, sid, 1)["outcome"] == "closed"
    assert _row(conn, sid, 2)["outcome"] is None


# Outcomes follow the item.


@pytest.mark.usefixtures("addr")
def test_a_rejected_draft(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _propose(conn, clock, FakeMailSource())
    clock.advance(30)
    approvals.reject(conn, clock, sid, actor="os_user")
    r = _row(conn, sid)
    assert r["outcome"] == "rejected"
    assert r["decided_after_s"] == pytest.approx(30)


@pytest.mark.usefixtures("addr")
def test_expired_then_offered_again_waits_and_expired_again_stays(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = StableId(_propose(conn, clock, FakeMailSource()))

    def move(to: Status) -> None:
        items.transition(conn, clock, sid, to, TransitionContext(origin=Origin.APPROVAL),
                         actor="service")  # fmt: skip

    move(Status.EXPIRED)
    assert _row(conn, sid)["outcome"] == "expired"
    move(Status.AWAITING_APPROVAL)
    assert _row(conn, sid)["outcome"] is None
    move(Status.EXPIRED)
    assert _row(conn, sid)["outcome"] == "expired"


@pytest.mark.usefixtures("addr")
def test_saving_records_the_hash_not_the_message_id_and_starts_the_watch(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sid = _propose(conn, clock, src)
    clock.advance(45)
    approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")
    assert _run(conn, clock, src) == 1
    r = _row(conn, sid)
    assert r["outcome"] == "saved"
    assert r["decided_after_s"] == pytest.approx(45)
    mid = _mid(conn, sid)
    assert r["saved_message_id_hash"] == outbound_msg.message_id_hash(mid)
    assert mid not in json.dumps(dict(r))
    doc = json.loads(item_row(conn, sid)["proposal"])
    assert r["content_hash"] == next(x["content_hash"] for x in doc["done"]
                                     if x["name"] == "draft_reply")  # fmt: skip
    assert r["watch_until"] == to_ts(clock.now() + timedelta(days=draft_versions.WATCH_DAYS))


@pytest.mark.usefixtures("addr")
def test_undo_marks_a_saved_draft_undone_but_not_a_sent_one(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    now = to_ts(clock.now())
    with write_tx(conn):
        draft_versions.on_transition(conn, sid, Status.UNDONE, now)
    assert _row(conn, sid)["outcome"] == "undone"
    with write_tx(conn):
        conn.execute("UPDATE draft_versions SET outcome = 'sent_edited'")
        draft_versions.on_transition(conn, sid, Status.UNDONE, now)
    assert _row(conn, sid)["outcome"] == "sent_edited"


@pytest.mark.usefixtures("addr")
@pytest.mark.parametrize("to", [Status.OBSERVED, Status.CANCELLED, Status.RESOLVED_MANUAL,
                                Status.RESOLVED_BY_MAILBOX])  # fmt: skip
def test_closed_another_way_is_closed_but_saved_stays_saved(
    conn: sqlite3.Connection, clock: FakeClock, to: Status
) -> None:
    src = FakeMailSource()
    waiting, saved = _propose(conn, clock, src, 1), _saved(conn, clock, src, 2)
    now = to_ts(clock.now())
    with write_tx(conn):
        draft_versions.on_transition(conn, waiting, to, now)
        draft_versions.on_transition(conn, saved, to, now)
    assert _row(conn, waiting)["outcome"] == "closed"
    assert _row(conn, saved)["outcome"] == "saved"


@pytest.mark.usefixtures("addr")
def test_only_the_latest_version_moves(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _propose(conn, clock, FakeMailSource())
    item = item_row(conn, sid)
    draft_versions.record(conn, clock, item, decide.context(conn, item), "Other.\n\n[Your Name]",
                          model="m", refused=())  # fmt: skip
    approvals.reject(conn, clock, sid, actor="os_user")
    assert _row(conn, sid, 1)["outcome"] == "closed"
    assert _row(conn, sid, 2)["outcome"] == "rejected"


@pytest.mark.usefixtures("addr")
def test_an_item_with_no_draft_moves_as_before(conn: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    sid = _mail_item(conn, clock, src, 0, REPLY, FACTS, src.deliver)
    _decide(conn, clock, sid, "", action="flag")
    assert conn.execute("SELECT count(*) FROM draft_versions").fetchone()[0] == 0


@pytest.mark.usefixtures("addr")
def test_the_actor_writing_no_draft_this_time_closes_the_waiting_one(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _propose(conn, clock, FakeMailSource())
    draft_versions.close_waiting(conn, clock, sid)
    assert _row(conn, sid)["outcome"] == "closed"


# Tracking.


@pytest.mark.usefixtures("addr")
def test_sent_with_placeholders_filled_is_sent_unchanged(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    clock.advance(600)
    _send(src, _mid(conn, sid), FILLED)
    assert _track(conn, clock, src) == 1
    r = _row(conn, sid)
    assert (r["outcome"], r["edited"]) == ("sent_unchanged", 0)
    assert r["sent_after_s"] == pytest.approx(600)
    [event] = conn.execute("SELECT data FROM audit WHERE event = 'draft.outcome'").fetchall()
    assert json.loads(event["data"]) == {"version": 1, "outcome": "sent_unchanged"}


@pytest.mark.usefixtures("addr")
def test_sent_with_changed_words_is_sent_edited(conn: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    _send(src, _mid(conn, sid), FILLED.replace("reply soon", "call you"))
    _track(conn, clock, src)
    r = _row(conn, sid)
    assert (r["outcome"], r["edited"]) == ("sent_edited", 1)


@pytest.mark.usefixtures("addr")
def test_a_sent_draft_is_never_looked_at_again(conn: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    _send(src, _mid(conn, sid), FILLED)
    _track(conn, clock, src)
    clock.advance(7200)
    assert _track(conn, clock, src) == 0


@pytest.mark.usefixtures("addr")
def test_a_draft_in_drafts_stays_saved_and_is_never_read(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)

    def no_read(*_args: object) -> bytes | None:
        raise AssertionError("read a draft in Drafts")

    monkeypatch.setattr(src, "fetch_in", no_read)
    monkeypatch.setattr(src, "fetch_start_in", no_read)
    assert _track(conn, clock, src) == 1
    r = _row(conn, sid)
    assert (r["outcome"], r["edited"], r["missing_checks"]) == ("saved", None, 0)


@pytest.mark.usefixtures("addr")
def test_a_sent_copy_is_read_only_up_to_the_limit(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    _send(src, _mid(conn, sid), FILLED)
    limits: list[int] = []
    real = src.fetch_start_in

    def counted(folder: str, uid: int, limit: int) -> bytes | None:
        limits.append(limit)
        return real(folder, uid, limit)

    monkeypatch.setattr(src, "fetch_start_in", counted)
    _track(conn, clock, src)
    assert limits == [draft_data.READ_BYTES]
    assert _row(conn, sid)["outcome"] == "sent_unchanged"


@pytest.mark.usefixtures("addr")
@pytest.mark.parametrize("role", ["Sent", "Drafts"])
def test_without_both_folders_a_missing_draft_isnt_called_deleted(
    conn: sqlite3.Connection, clock: FakeClock, role: str
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    src.delete_in("Drafts", src.find_in("Drafts", _mid(conn, sid))[0])
    kept = [f for f in src.folders() if f.name != role]
    src.folders = lambda: kept  # type: ignore[method-assign]
    for _ in range(3):
        _track(conn, clock, src)
        clock.advance(3600)
    r = _row(conn, sid)
    assert (r["outcome"], r["missing_checks"]) == ("saved", 0)


@pytest.mark.usefixtures("addr")
def test_gone_from_both_takes_two_checks_and_a_late_send_still_counts(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    mid = _mid(conn, sid)
    src.delete_in("Drafts", src.find_in("Drafts", mid)[0])
    _track(conn, clock, src)
    assert _row(conn, sid)["outcome"] == "saved"
    clock.advance(3600)
    _track(conn, clock, src)
    assert _row(conn, sid)["outcome"] == "deleted_unsent"
    clock.advance(3600)
    _send(src, mid, FILLED)
    _track(conn, clock, src)
    assert _row(conn, sid)["outcome"] == "sent_unchanged"


def _email_id(conn: sqlite3.Connection, sid: str) -> str:
    return str(conn.execute("SELECT message_id FROM items WHERE stable_id = ?",
                            (sid,)).fetchone()["message_id"])  # fmt: skip


def _reply_copy(replying_to: str, body: str, mid: str = "<new@mail.gmail.com>") -> bytes:
    """As Gmail in a browser leaves a draft it sent or re-saved: a new Message-ID, the same
    In-Reply-To (real test, 2026-11-01)."""
    return outbound_msg.build_draft(
        from_addr="ap@acme.example", to_addr="news@vendor-a.example", subject="s", body=body,
        in_reply_to=replying_to, references=None, date=datetime(2026, 10, 31, tzinfo=UTC),
        message_id=mid,
    ).raw  # fmt: skip


def _drop_draft(conn: sqlite3.Connection, src: FakeMailSource, sid: str) -> None:
    src.delete_in("Drafts", src.find_in("Drafts", _mid(conn, sid))[0])


@pytest.mark.usefixtures("addr")
@pytest.mark.parametrize(("text", "outcome", "edited"), [
    (FILLED, "sent_unchanged", 0),
    (FILLED.replace("reply soon", "call on Friday"), "sent_edited", 1),
])  # fmt: skip
def test_a_send_under_a_new_message_id_is_found_as_the_reply_to_the_email(
    conn: sqlite3.Connection, clock: FakeClock, text: str, outcome: str, edited: int
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    _drop_draft(conn, src, sid)
    src.append("Sent", _reply_copy(_email_id(conn, sid), text), [])
    _track(conn, clock, src)
    r = _row(conn, sid)
    assert (r["outcome"], r["edited"]) == (outcome, edited)


@pytest.mark.usefixtures("addr")
def test_a_draft_resaved_under_a_new_message_id_is_still_saved(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    _drop_draft(conn, src, sid)
    src.append("Drafts", _reply_copy(_email_id(conn, sid), "changed"), [])
    for _ in range(3):
        _track(conn, clock, src)
        clock.advance(3600)
    r = _row(conn, sid)
    assert (r["outcome"], r["missing_checks"], r["edited"]) == ("saved", 0, None)


@pytest.mark.usefixtures("addr")
def test_ecfs_own_reply_to_the_email_is_not_the_draft_being_sent(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    _drop_draft(conn, src, sid)
    src.append("Sent", _reply_copy(_email_id(conn, sid), "template", "<own@acme.example>"), [])
    with write_tx(conn):
        conn.execute("INSERT INTO sent (message_id_hash, address_id, content_hash, kind, sent_at,"
                     " status) VALUES (?, 'ap', 'c', 'reply', 't', 'sent')",
                     (outbound_msg.message_id_hash("<own@acme.example>"),))  # fmt: skip
    for _ in range(2):
        _track(conn, clock, src)
        clock.advance(3600)
    assert _row(conn, sid)["outcome"] == "deleted_unsent"


@pytest.mark.usefixtures("addr")
def test_of_two_replies_the_one_that_is_the_draft_counts(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    _drop_draft(conn, src, sid)
    email = _email_id(conn, sid)
    src.append("Sent", _reply_copy(email, "A short note of my own.", "<first@x.example>"), [])
    src.append("Sent", _reply_copy(email, FILLED, "<second@x.example>"), [])
    _track(conn, clock, src)
    assert _row(conn, sid)["outcome"] == "sent_unchanged"


@pytest.mark.usefixtures("addr")
def test_replies_read_are_bounded(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    _drop_draft(conn, src, sid)
    for n in range(draft_data.REPLY_READS + 2):
        src.append("Sent", _reply_copy(_email_id(conn, sid), "mine", f"<{n}@x.example>"), [])
    reads: list[int] = []
    real = src.fetch_start_in

    def counted(folder: str, uid: int, limit: int) -> bytes | None:
        reads.append(uid)
        return real(folder, uid, limit)

    monkeypatch.setattr(src, "fetch_start_in", counted)
    _track(conn, clock, src)
    assert len(reads) == draft_data.REPLY_READS
    assert _row(conn, sid)["outcome"] == "sent_edited"


@pytest.mark.usefixtures("addr")
def test_an_email_with_no_message_id_is_never_searched_as_a_reply(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    with write_tx(conn):
        conn.execute("UPDATE items SET message_id = NULL WHERE stable_id = ?", (sid,))

    def searched(_folder: str, _mid: str, _since: datetime) -> list[int]:
        pytest.fail("searched")

    monkeypatch.setattr(src, "find_replies_in", searched)
    _track(conn, clock, src)
    assert _row(conn, sid)["outcome"] == "saved"


@pytest.mark.usefixtures("addr")
def test_a_reply_from_before_the_draft_was_saved_isnt_its_send(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    clock.advance(10 * 365 * 86400)  # the fake mailbox stamps today: "earlier" than this clock
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    _drop_draft(conn, src, sid)
    src.append("Sent", _reply_copy(_email_id(conn, sid), "My reply this morning."), [])
    for _ in range(2):
        _track(conn, clock, src)
        clock.advance(3600)
    assert _row(conn, sid)["outcome"] == "deleted_unsent"


@pytest.mark.usefixtures("addr")
def test_the_reply_search_starts_just_before_the_draft_was_saved(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    asked: list[datetime] = []

    def noted(_folder: str, _mid: str, since: datetime) -> list[int]:
        asked.append(since)
        return []

    monkeypatch.setattr(src, "find_replies_in", noted)
    _track(conn, clock, src)
    assert asked == [from_ts(_row(conn, sid)["saved_at"]) - draft_data.SKEW]


@pytest.mark.usefixtures("addr")
@pytest.mark.parametrize("hostile", [
    "<\u00fc@x.example>", "<a\r\nA1 LOGOUT\r\n@x.example>", "a", "<a@x.example> <b@x.example>",
    "<a)b@x.example>", "<a(b@x.example>", "<a*@x.example>", "<a%@x.example>", "<a\x00@x.example>",
    "<a\x7f@x.example>", '<a"b@x.example>', "<a\\b@x.example>", "<a{5}@x.example>",
    "<a]@x.example>", "<a b@x.example>", f"<{'a' * 401}@x.example>",
])  # fmt: skip
def test_an_odd_message_id_from_the_email_is_never_put_in_a_search(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch, hostile: str
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    with write_tx(conn):
        conn.execute("UPDATE items SET message_id = ? WHERE stable_id = ?", (hostile, sid))

    def searched(_folder: str, _mid: str, _since: datetime) -> list[int]:
        pytest.fail("searched")

    monkeypatch.setattr(src, "find_replies_in", searched)
    _track(conn, clock, src)
    assert _row(conn, sid)["outcome"] == "saved"


def test_ordinary_message_ids_can_be_searched() -> None:
    for mid in ("<CAFfNkcQwNmtoGL=WSmEUtbiZ4GTEn7o_ep-vkC8e0PWHqi6=_g@mail.gmail.com>",
                "<8dc05f5b.ecf@gmail.com>", "<x@mail_relay.corp>", "<1@bugs.vendor-a.example/>",
                "<a+b/c$d&e!f#g'h?i^j`k|l~m@mail.vendor-a.example>"):  # fmt: skip
        assert draft_data.searchable(mid)


@pytest.mark.usefixtures("addr")
@pytest.mark.parametrize(("newer", "outcome"), [("closed", "deleted_unsent"),
                                                ("undone", "sent_unchanged")])  # fmt: skip
def test_only_the_newest_saved_version_is_matched_as_a_reply(
    conn: sqlite3.Connection, clock: FakeClock, newer: str, outcome: str
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    _drop_draft(conn, src, sid)
    cols = [c["name"] for c in conn.execute("PRAGMA table_info(draft_versions)")
            if c["name"] not in ("version", "saved_message_id_hash", "outcome")]  # fmt: skip
    names = ", ".join(cols)
    with write_tx(conn):
        conn.execute(
            f"INSERT INTO draft_versions (version, saved_message_id_hash, outcome, {names})"  # noqa: S608 - the table's own columns
            f" SELECT 2, 'other', ?, {names} FROM draft_versions WHERE stable_id = ?",
            (newer, sid),
        )  # fmt: skip
    src.append("Sent", _reply_copy(_email_id(conn, sid), FILLED), [])
    for _ in range(2):
        _track(conn, clock, src)
        clock.advance(3600)
    assert _row(conn, sid)["outcome"] == outcome


@pytest.mark.usefixtures("addr")
def test_a_draft_back_in_drafts_is_saved_again(conn: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    mid = _mid(conn, sid)
    raw = _copy(mid, DRAFT)
    src.delete_in("Drafts", src.find_in("Drafts", mid)[0])
    _track(conn, clock, src)
    clock.advance(3600)
    _track(conn, clock, src)
    assert _row(conn, sid)["outcome"] == "deleted_unsent"
    src.append("Drafts", raw, [])
    clock.advance(3600)
    _track(conn, clock, src)
    r = _row(conn, sid)
    assert (r["outcome"], r["missing_checks"]) == ("saved", 0)


@pytest.mark.usefixtures("addr")
def test_each_draft_at_most_once_an_hour(conn: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    _saved(conn, clock, src)
    assert _track(conn, clock, src) == 1
    clock.advance(3599)
    assert _track(conn, clock, src) == 0
    clock.advance(1)
    assert _track(conn, clock, src) == 1


@pytest.mark.usefixtures("addr")
def test_at_most_ten_a_check_oldest_checked_first(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    assert draft_data.PER_CHECK == 10
    sids = [_saved(conn, clock, src, n) for n in range(draft_data.PER_CHECK + 2)]
    assert _track(conn, clock, src) == draft_data.PER_CHECK
    left = [s for s in sids if _row(conn, s)["checked_at"] is None]
    assert len(left) == 2
    clock.advance(3600)
    assert _track(conn, clock, src) == draft_data.PER_CHECK
    assert all(_row(conn, s)["checked_at"] == to_ts(clock.now()) for s in left)


@pytest.mark.usefixtures("addr")
def test_the_pass_stops_at_its_time_budget(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    for n in range(3):
        _saved(conn, clock, src, n)
    real = src.find_in

    def slow(folder: str, mid: str) -> list[int]:
        clock.advance(draft_data.BUDGET_S / 2)
        return real(folder, mid)

    monkeypatch.setattr(src, "find_in", slow)
    assert _track(conn, clock, src) == 1  # Sent and Drafts use the whole budget


@pytest.mark.usefixtures("addr")
def test_after_the_watch_it_stays_still_waiting_and_isnt_searched(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    clock.advance(draft_versions.WATCH_DAYS * 86400 + 1)
    assert _track(conn, clock, src) == 0
    assert _row(conn, sid)["outcome"] == "still_waiting"
    [event] = conn.execute("SELECT data FROM audit WHERE event = 'draft.outcome'").fetchall()
    assert json.loads(event["data"])["outcome"] == "still_waiting"


@pytest.mark.usefixtures("addr")
def test_a_lost_lease_looks_at_nothing(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    _saved(conn, clock, src)

    def no_list() -> list[Folder]:
        raise AssertionError("listed folders without the lease")

    monkeypatch.setattr(src, "folders", no_list)
    assert _track(conn, clock, src, lambda: True) == 0


@pytest.mark.usefixtures("addr")
def test_a_mail_error_ends_the_pass_and_doesnt_fail_the_check(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)

    def down(_folder: str, _mid: str) -> list[int]:
        raise MailUnavailableError("down")

    monkeypatch.setattr(src, "find_in", down)
    assert _track(conn, clock, src) == 0
    assert _row(conn, sid)["checked_at"] is None


@pytest.mark.usefixtures("addr")
def test_a_pruned_item_is_left_to_end_as_still_waiting(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    with write_tx(conn):
        conn.execute("UPDATE draft_versions SET saved_message_id_hash = 'other'")
    searched: list[str] = []

    def counted(folder: str, _mid: str) -> list[int]:
        searched.append(folder)
        return []

    monkeypatch.setattr(src, "find_in", counted)
    _track(conn, clock, src)
    assert searched == []
    assert _row(conn, sid)["checked_at"] is not None


def test_tracking_runs_after_actions_and_undos_in_every_check() -> None:
    at = checks.IN_LEASE.index(draft_data.track_in_check)
    assert at > checks.IN_LEASE.index(mailbox_actions.run_in_check)
    assert at > checks.IN_LEASE.index(digests._undos_in_check)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.usefixtures("addr")
def test_an_expired_draft_stays_expired_when_a_new_one_replaces_it(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _propose(conn, clock, FakeMailSource())
    item = item_row(conn, sid)
    ctx = decide.context(conn, item)
    items.transition(conn, clock, StableId(sid), Status.EXPIRED,
                     TransitionContext(origin=Origin.APPROVAL), actor="service")  # fmt: skip
    assert draft_versions.record(conn, clock, item, ctx, DRAFT, model="m", refused=()) == 1
    assert draft_versions.record(conn, clock, item, ctx, DRAFT + " Also.", model="m",
                                 refused=()) == 2  # fmt: skip
    assert _row(conn, sid, 1)["outcome"] == "expired"


def test_rates_count_only_settled_drafts_and_undone_isnt_saved(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    now = to_ts(clock.now())
    for n, outcome in enumerate(("saved", "rejected", "undone", None, "closed", "refused")):
        _version(conn, n, prompt="p", created=now, outcome=outcome)
    clock.advance(1)
    r = draft_stats.report(conn, _since(clock))
    assert (r["headline"]["saved"]["k"], r["headline"]["saved"]["n"]) == (1, 3)
    assert r["totals"]["offered"] == 4


def test_html_only_mail_is_read_as_text() -> None:
    raw = (b"From: a@acme.example\r\nTo: b@acme.example\r\nSubject: s\r\n"
           b"Message-ID: <m@acme.example>\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
           b"<p>Hello there</p>")  # fmt: skip
    assert draft_data.body_text(raw, 10 * MB).strip() == "Hello there"


def test_plain_text_comes_before_html() -> None:
    raw = (b"From: a@acme.example\r\nTo: b@acme.example\r\nSubject: s\r\n"
           b"Message-ID: <m@acme.example>\r\nMIME-Version: 1.0\r\n"
           b'Content-Type: multipart/alternative; boundary="b"\r\n\r\n'
           b"--b\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>From html</p>\r\n"
           b"--b\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nFrom plain\r\n"
           b"--b--\r\n")  # fmt: skip
    assert draft_data.body_text(raw, 10 * MB).strip() == "From plain"


@pytest.mark.usefixtures("addr")
def test_a_database_error_recording_the_save_keeps_one_draft(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _propose(conn, clock, src)
    approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")

    def locked(*_args: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(draft_versions, "saved", locked)
    assert _run(conn, clock, src) == 1
    assert item_row(conn, sid)["status"] == "executed"
    assert len(src.elsewhere["Drafts"]) == 1


@pytest.mark.usefixtures("addr")
def test_a_sent_copy_with_no_readable_text_is_unread_and_not_an_edit(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    _send(src, _mid(conn, sid), FILLED)
    monkeypatch.setattr(draft_data, "body_text", lambda *_a: "")  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    _track(conn, clock, src)
    r = _row(conn, sid)
    assert (r["outcome"], r["edited"]) == ("sent_unread", None)
    clock.advance(1)
    h = draft_stats.report(conn, _since(clock))["headline"]
    assert h["sent_unchanged"]["n"] == 0
    assert h["saved"]["k"] == 1


@pytest.mark.usefixtures("addr")
def test_a_copy_gone_before_its_read_isnt_a_miss(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _saved(conn, clock, src)
    _send(src, _mid(conn, sid), FILLED)

    def gone(*_args: object) -> bytes | None:
        return None

    monkeypatch.setattr(src, "fetch_start_in", gone)
    _track(conn, clock, src)
    r = _row(conn, sid)
    assert (r["outcome"], r["missing_checks"]) == ("saved", 0)


@pytest.mark.usefixtures("addr")
def test_a_draft_a_reprocess_didnt_add_is_closed(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = FakeMailSource()
    sid = _mail_item(conn, clock, src, 0, REPLY, FACTS, src.deliver)
    with write_tx(conn):
        conn.execute("INSERT INTO excerpts (stable_id, actor_text) VALUES (?, 'Please confirm.')",
                     (sid,))  # fmt: skip
    real = decide.apply

    def no_draft(c: sqlite3.Connection, k: FakeClock, s: str, p: Any, **kw: Any) -> Status:
        p.actions = [a for a in p.actions if a.name != "draft_reply"]
        return real(c, k, s, p, **kw)

    monkeypatch.setattr(decide, "apply", no_draft)
    _decide(conn, clock, sid, DRAFT)
    assert _row(conn, sid)["outcome"] == "closed"


@pytest.mark.usefixtures("addr")
def test_only_an_expired_draft_offered_again_reopens(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _propose(conn, clock, FakeMailSource())
    now = to_ts(clock.now())
    with write_tx(conn):
        draft_versions.on_transition(conn, sid, Status.EXPIRED, now, Status.AWAITING_APPROVAL)
        draft_versions.on_transition(conn, sid, Status.AWAITING_APPROVAL, now, Status.PROPOSED)
    assert _row(conn, sid)["outcome"] == "expired"


# Ratings.


@pytest.mark.usefixtures("addr")
def test_a_rating_needs_a_decided_draft(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _propose(conn, clock, FakeMailSource())
    with pytest.raises(ConflictError):
        draft_data.rate(conn, clock, FakeNotifier(), sid, "up")


@pytest.mark.usefixtures("addr")
def test_a_rating_is_kept_with_the_notes_length_only(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _saved(conn, clock, FakeMailSource())
    draft_data.rate(conn, clock, FakeNotifier(), sid, "down", reason="tone", note_chars=12)
    r = _row(conn, sid)
    assert (r["rating"], r["reason"], r["note_chars"]) == ("down", "tone", 12)
    draft_data.rate(conn, clock, FakeNotifier(), sid, "up")
    r = _row(conn, sid)
    assert (r["rating"], r["reason"]) == ("up", None)


@pytest.mark.usefixtures("addr")
@pytest.mark.parametrize(("rating", "reason"), [("sideways", None), ("down", "free text"),
                                                ("up", "tone")])  # fmt: skip
def test_ratings_are_fixed_values(
    conn: sqlite3.Connection, clock: FakeClock, rating: str, reason: str | None
) -> None:
    sid = _saved(conn, clock, FakeMailSource())
    with pytest.raises(InvalidInputError):
        draft_data.rate(conn, clock, FakeNotifier(), sid, rating, reason=reason)


@pytest.mark.usefixtures("addr")
def test_promised_something_alerts_once_per_version(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _saved(conn, clock, FakeMailSource())
    n = FakeNotifier()
    for _ in range(2):
        draft_data.rate(conn, clock, n, sid, "down", reason=draft_data.PROMISED)
    draft_data.rate(conn, clock, n, sid, "up")
    draft_data.rate(conn, clock, n, sid, "down", reason=draft_data.PROMISED)
    assert len(n.sent) == 1
    assert "Promising" in n.sent[0][0]
    assert sid[:8] in n.sent[0][1]


@pytest.mark.usefixtures("addr")
def test_another_reason_doesnt_alert(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _saved(conn, clock, FakeMailSource())
    n = FakeNotifier()
    draft_data.rate(conn, clock, n, sid, "down", reason="tone")
    assert n.sent == []


@pytest.mark.usefixtures("addr")
def test_a_withdrawn_draft_doesnt_count_as_got_a_draft(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _propose(conn, clock, FakeMailSource())
    draft_versions.close_waiting(conn, clock, sid)
    clock.advance(1)
    c = draft_stats.report(conn, _since(clock))["coverage"]
    assert c["drafted"]["k"] == 0
    assert c["rest"] == {"draft withdrawn or not offered": 1}


@pytest.mark.usefixtures("addr")
def test_coverage_leaves_out_items_not_yet_reviewed(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    _mail_item(conn, clock, src, 0, REPLY, FACTS, src.deliver)  # classified, no review yet
    clock.advance(1)
    assert draft_stats.report(conn, _since(clock))["coverage"]["reply_needed"] == 0


# The report.


def _since(clock: FakeClock) -> datetime:
    return clock.now() - timedelta(days=1)


@pytest.mark.usefixtures("addr")
def test_the_report_counts_outcomes_rates_and_codes(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    a = _saved(conn, clock, src, 1)
    _send(src, _mid(conn, a), FILLED)
    _track(conn, clock, src)
    draft_data.rate(conn, clock, FakeNotifier(), a, "up")
    b = _propose(conn, clock, src, 2)
    approvals.reject(conn, clock, b, actor="os_user")
    draft_data.rate(conn, clock, FakeNotifier(), b, "down", reason="tone")
    _propose(conn, clock, src, 3, "I will pay the 2,000 invoice on 2026-11-30.\n\n[Your Name]")
    _propose(conn, clock, src, 4)
    clock.advance(1)
    r = draft_stats.report(conn, _since(clock))
    assert r["outcomes"] == {"sent_unchanged": 1, "rejected": 1, "refused": 1, "waiting": 1}
    assert (r["totals"]["drafts"], r["totals"]["offered"]) == (4, 3)
    assert r["headline"]["saved"]["k"] == 1
    assert r["headline"]["saved"]["n"] == 2
    assert r["headline"]["sent_unchanged"]["share"] == 1.0
    assert r["headline"]["thumbs_up"]["n"] == 2
    assert r["reasons"] == {"tone": 1}
    assert "date" in r["safety"]["refused_by_code"]


@pytest.mark.usefixtures("addr")
def test_coverage_counts_each_reply_needed_item_once(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    _propose(conn, clock, src, 1)
    _propose(conn, clock, src, 2, "I will pay the 2,000 invoice on 2026-11-30.\n\n[Your Name]")
    none = _mail_item(conn, clock, src, 3, REPLY, FACTS, src.deliver)
    _decide(conn, clock, none, "", action="flag")
    assert item_row(conn, none)["status"] != "classified"
    # P-a withholds a draft from a first-time sender's payment mail, or here unauthenticated mail
    held = _mail_item(conn, clock, src, 4, REPLY, FACTS | {"auth_result": "none",
                                                          "sender_seen_before": False},
                      src.deliver)  # fmt: skip
    decide.apply(conn, clock, held, source="rule", actor=False)
    other = _mail_item(conn, clock, src, 5, REPLY | {"requires_reply": False}, FACTS, src.deliver)
    assert other
    clock.advance(1)
    c = draft_stats.report(conn, _since(clock))["coverage"]
    assert c["reply_needed"] == 4
    assert (c["drafted"]["k"], c["drafted"]["n"]) == (1, 4)
    rest = c["rest"]
    assert rest.pop("refused by the checker") == 1
    assert rest.pop("actor wrote none") == 1
    assert sum(rest.values()) == 1  # policy's code, whichever it is
    assert c["seen_before"]["no"]["n"] == 1
    assert c["seen_before"]["yes"]["n"] == 3


@pytest.mark.usefixtures("addr")
@pytest.mark.parametrize(("by", "name"), [("model", "claude-opus-5-5"), ("category", "invoice"),
                                          ("address", "ap"), ("seen_before", "yes")])  # fmt: skip
def test_the_report_splits_by_each_field(
    conn: sqlite3.Connection, clock: FakeClock, by: str, name: str
) -> None:
    _propose(conn, clock, FakeMailSource())
    clock.advance(1)
    [g] = draft_stats.report(conn, _since(clock), by=by)["by"]["groups"]
    assert (g["name"], g["drafts"]) == (name, 1)


def _version(conn: sqlite3.Connection, n: int, *, prompt: str, created: str, outcome: str | None,
             rating: str | None = None) -> None:  # fmt: skip
    with write_tx(conn):
        conn.execute(
            "INSERT INTO draft_versions (stable_id, version, address_id, actor_prompt, checker,"
            " created, draft_chars, normalised_hash, outcome, rating)"
            " VALUES (?, 1, 'ap', ?, 'c', ?, 10, 'h', ?, ?)",
            (f"s{n}", prompt, created, outcome, rating),
        )  # fmt: skip


def test_before_and_after_says_when_there_arent_enough(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    now = to_ts(clock.now())
    _version(conn, 1, prompt="old-prompt-digest", created=now, outcome="saved")
    _version(conn, 2, prompt="new-prompt-digest", created=now, outcome="saved")
    clock.advance(1)
    b = draft_stats.report(conn, _since(clock))["before_after"]
    assert b["saved"] == {"enough": False, "before_n": 1, "after_n": 1}


def test_before_and_after_compares_the_two_newest_prompts(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    first, second, third = (to_ts(clock.now() - timedelta(hours=h)) for h in (3, 2, 1))
    n = 0
    for prompt, at, saved, total in (("oldest", first, 0, 25), ("before", second, 10, 25),
                                     ("after", third, 20, 25)):  # fmt: skip
        for i in range(total):
            n += 1
            _version(
                conn, n, prompt=prompt, created=at, outcome="saved" if i < saved else "rejected"
            )
    b = draft_stats.report(conn, _since(clock))["before_after"]
    assert (b["before"], b["after"]) == ("before", "after")
    s = b["saved"]
    assert s["enough"] is True
    assert s["difference"] == pytest.approx(0.4)
    lo, hi = newcombe_unpaired(20, 25, 10, 25)
    assert (s["low"], s["high"]) == (pytest.approx(lo, abs=0.001), pytest.approx(hi, abs=0.001))
    assert b["thumbs_up"]["enough"] is False


def test_newcombe_unpaired_matches_the_published_example() -> None:
    # Newcombe 1998, method 10, example 56/70 v 48/80: difference 0.20, 0.0524 to 0.3339
    lo, hi = newcombe_unpaired(56, 70, 48, 80)
    assert lo == pytest.approx(0.0524, abs=0.0005)
    assert hi == pytest.approx(0.3339, abs=0.0005)


def test_the_weekly_trend_groups_by_iso_week(conn: sqlite3.Connection, clock: FakeClock) -> None:
    now = clock.now()
    _version(conn, 1, prompt="p", created=to_ts(now - timedelta(days=8)), outcome="saved")
    _version(conn, 2, prompt="p", created=to_ts(now), outcome="rejected")
    clock.advance(1)
    weeks = draft_stats.report(conn, now - timedelta(days=20))["weeks"]
    assert [w["drafts"] for w in weeks] == [1, 1]
    assert weeks[0]["week"] < weeks[1]["week"]


# The weekly post.


def _summary(conn: sqlite3.Connection, clock: FakeClock) -> None:
    with write_tx(conn):
        slack_admin.put_setting(conn, "slack_summary_channel", "CSUM", to_ts(clock.now()),
                                actor="test")  # fmt: skip


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [json.loads(r[0]) for r in conn.execute(
        "SELECT payload FROM jobs WHERE payload LIKE '%drafts-weekly%'")]  # fmt: skip


def test_the_weekly_post_goes_once_with_counts_only(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _summary(conn, clock)
    last = drafts_weekly.week_of(clock.now() - timedelta(days=7))
    _version(conn, 1, prompt=SECRET_WORD, created=to_ts(clock.now() - timedelta(days=7)),
             outcome="saved", rating="up")  # fmt: skip
    # whichever weekday the test clock is on, last week's Monday-to-Sunday holds that row
    assert drafts_weekly.run(conn, clock) is True
    assert drafts_weekly.run(conn, clock) is False
    [post] = _posts(conn)
    text = json.dumps(post)
    assert last in text
    assert SECRET_WORD not in text
    assert drafts_weekly.card(last, draft_stats.week_counts(
        conn, *drafts_weekly._bounds(clock.now()))).fields[0] == ("Written", "1")  # pyright: ignore[reportPrivateUsage]  # fmt: skip


def test_a_week_with_no_drafts_posts_nothing(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _summary(conn, clock)
    assert drafts_weekly.run(conn, clock) is False
    assert _posts(conn) == []
    assert slack_admin.setting(conn, drafts_weekly.LAST)


def test_no_summary_channel_waits(conn: sqlite3.Connection, clock: FakeClock) -> None:
    assert drafts_weekly.run(conn, clock) is False
    assert not slack_admin.setting(conn, drafts_weekly.LAST)


# Pruning, and leaving it out of exports.


def test_retention_prunes_by_created(conn: sqlite3.Connection, clock: FakeClock) -> None:
    now = clock.now()
    _version(conn, 1, prompt="p", created=to_ts(now - timedelta(days=91)), outcome="saved")
    _version(conn, 2, prompt="p", created=to_ts(now - timedelta(days=89)), outcome="saved")
    assert retention.run(conn, clock)["draft_versions"] == 1
    assert [r[0] for r in conn.execute("SELECT stable_id FROM draft_versions")] == ["s2"]


# The route and the command.


def test_the_route_and_the_cli_screen(conn: sqlite3.Connection, db_path: Path, clock: FakeClock,
                                      capsys: pytest.CaptureFixture[str]) -> None:  # fmt: skip
    from ecf_server.api import ServiceState, create_app  # noqa: PLC0415

    _version(conn, 1, prompt="p", created=to_ts(clock.now()), outcome="saved", rating="down")
    clock.advance(1)
    state = ServiceState(install="t", token="tok", started_at="2026-10-01T12:00:00.000000Z",
                         clock=clock, db_path=db_path)  # fmt: skip

    async def get(path: str) -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.get(path, headers={"Authorization": "Bearer tok"})

    r = anyio.run(get, "/v1/drafts/stats?by=model")
    assert r.status_code == 200, r.text
    cli_drafts.show(r.json())
    out = capsys.readouterr().out
    assert "1 draft(s) written" in out
    assert "By model" in out
    assert anyio.run(get, "/v1/drafts/stats?by=sender").status_code == 400
    assert anyio.run(get, "/v1/drafts/stats?since=soon").status_code == 400


def test_the_command_refuses_an_unknown_split() -> None:
    result = CliRunner().invoke(app, ["drafts", "stats", "--by", "sender"])
    assert result.exit_code != 0
    assert "--by" in click.unstyle(result.output)  # CI forces colour, which splits the option


def test_every_split_the_command_offers_is_a_routes_split() -> None:
    assert set(cli_drafts.BY) == set(draft_stats.BY)
