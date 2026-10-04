"""Sending with a record and settling the outcome (V1.5 step 1c; OD-322): one Message-ID per grant,
nothing sent twice, the sent copy, and unknown outcomes settled from the Sent folder."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from ecf.status import Status
from ecf_server import approvals, execute, probe, send
from ecf_server.actions import Planned
from ecf_server.clock import Clock, FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.mail.fake import FakeMailSource, FakeSender
from ecf_server.mail.smtp import SendNotSentError, SendOutcomeUnknownError
from ecf_server.notify import FakeNotifier
from ecf_server.outbound_msg import build_reply
from tests.test_approvals import (  # pyright: ignore[reportPrivateUsage]
    _grants,  # pyright: ignore[reportPrivateUsage]
    _proposed,  # pyright: ignore[reportPrivateUsage]
    _setup,  # pyright: ignore[reportPrivateUsage]
    _status,  # pyright: ignore[reportPrivateUsage]
    _verified_nonce,  # pyright: ignore[reportPrivateUsage]
)

WHEN = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _address(conn: sqlite3.Connection, clock: FakeClock, *, saves: bool | None,
             sent_folder: bool = True) -> None:  # fmt: skip
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('ap', 'ap@acme.example', 'standard', 'A', 't')")  # fmt: skip
        r = probe.ProbeResult(roles={"\\Sent": "Sent"} if sent_folder else {},
                              custom_keywords=True, move=True, uidplus=True, condstore=True,
                              append_limit=None, saves_sent=saves, max_message_bytes=None,
                              max_size_source=None)  # fmt: skip
        probe.store(conn, clock, "ap", "imap.acme.example", r)


def _out(conn: sqlite3.Connection, grant: str = "g1") -> send.Outgoing:
    mid = send.message_id_for(conn, grant, "acme.example")
    built = build_reply(from_addr="ap@acme.example", to_addr="pat@vendor.example",
                        subject="Invoice", body="Thanks\n", in_reply_to=None, references=None,
                        install_header="0" * 32 + ".0", date=WHEN, message_id=mid)  # fmt: skip
    return send.Outgoing("ap", "reply", "ap@acme.example", ("pat@vendor.example",), built,
                         stable_id="s1", grant_id=grant)  # fmt: skip


def _row(conn: sqlite3.Connection) -> sqlite3.Row:
    [row] = conn.execute("SELECT * FROM sent").fetchall()
    return row


@pytest.mark.parametrize(("saves", "copies", "copy"), [(False, 1, "ecf"), (None, 1, "check"),
                                                        (True, 0, "provider")])  # fmt: skip
def test_a_send_is_recorded_then_copied_to_sent(
    conn: sqlite3.Connection, clock: FakeClock, saves: bool | None, copies: int, copy: str
) -> None:
    _address(conn, clock, saves=saves)
    mail, smtp = FakeMailSource(), FakeSender()
    out = _out(conn)
    send.submit(conn, clock, smtp, out, mail)
    assert [(f, r) for f, r, _ in smtp.sent] == [("ap@acme.example", ("pat@vendor.example",))]
    row = _row(conn)
    assert (row["status"], row["copy"], row["grant_id"]) == ("sent", copy, "g1")
    assert row["message_id"] == out.built.message_id and row["settled_at"] is not None
    assert len(mail.find_in("Sent", out.built.message_id)) == copies
    send.submit(conn, clock, smtp, out, mail)  # a crash before the item moved: never twice
    assert len(smtp.sent) == 1


def test_no_sent_folder_means_no_copy(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock, saves=False, sent_folder=False)
    send.submit(conn, clock, FakeSender(), _out(conn), FakeMailSource())
    assert _row(conn)["copy"] == "none"


def test_a_temporary_failure_retries_with_the_same_message_id(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, saves=False)
    smtp = FakeSender()
    smtp.fail = "temp"
    first = _out(conn)
    with pytest.raises(SendNotSentError):
        send.submit(conn, clock, smtp, first, FakeMailSource())
    assert _row(conn)["status"] == "pending"
    smtp.fail = None
    again = _out(conn)
    assert again.built.message_id == first.built.message_id
    send.submit(conn, clock, smtp, again, FakeMailSource())
    assert _row(conn)["status"] == "sent" and len(smtp.sent) == 1


def test_a_refusal_is_final(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock, saves=False)
    smtp = FakeSender()
    smtp.fail = "refused"
    with pytest.raises(SendNotSentError):
        send.submit(conn, clock, smtp, _out(conn))
    assert _row(conn)["status"] == "failed"
    smtp.fail, before = None, smtp.connects
    with pytest.raises(SendNotSentError):
        send.submit(conn, clock, smtp, _out(conn))
    assert smtp.connects == before and smtp.sent == []  # the server isn't asked again


def test_an_unknown_outcome_is_never_sent_again(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock, saves=True)
    smtp = FakeSender()
    smtp.fail = "unknown"
    with pytest.raises(SendOutcomeUnknownError):
        send.submit(conn, clock, smtp, _out(conn))
    assert (_row(conn)["status"], _row(conn)["settled_at"]) == ("unknown", None)
    smtp.fail = None
    with pytest.raises(SendOutcomeUnknownError):
        send.submit(conn, clock, smtp, _out(conn))
    assert len(smtp.sent) == 1


def test_settle_learns_that_the_provider_saves_sent_mail(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, saves=None)
    mail = FakeMailSource()
    out = _out(conn)
    send.submit(conn, clock, FakeSender(), out, mail)
    mail.append("Sent", out.built.raw)  # the provider's own copy, saved a little later
    r = send.settle(conn, clock, mail, "ap")
    assert r.copies == 1 and len(mail.find_in("Sent", out.built.message_id)) == 1
    assert probe.load(conn, "ap")["saves_sent"] is True  # type: ignore[index]
    assert _row(conn)["copy"] == "provider"
    assert send.settle(conn, clock, mail, "ap").copies == 0  # settled once


def test_settle_learns_that_the_provider_does_not(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, saves=None)
    mail = FakeMailSource()
    send.submit(conn, clock, FakeSender(), _out(conn), mail)
    send.settle(conn, clock, mail, "ap")
    assert probe.load(conn, "ap")["saves_sent"] is False  # type: ignore[index]
    assert _row(conn)["copy"] == "ecf"


def test_settle_finds_an_unknown_send_in_sent(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock, saves=True)
    mail, smtp = FakeMailSource(), FakeSender()
    smtp.fail = "unknown"
    out = _out(conn)
    with pytest.raises(SendOutcomeUnknownError):
        send.submit(conn, clock, smtp, out, mail)
    mail.append("Sent", out.built.raw)  # the provider saved it: it went
    assert send.settle(conn, clock, mail, "ap").sent == 1
    assert _row(conn)["status"] == "sent"


def test_settle_fails_an_unknown_send_missing_twice(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, saves=True)
    mail, smtp = FakeMailSource(), FakeSender()
    smtp.fail = "unknown"
    with pytest.raises(SendOutcomeUnknownError):
        send.submit(conn, clock, smtp, _out(conn), mail)
    assert send.settle(conn, clock, mail, "ap").failed == 0
    assert _row(conn)["searches"] == 1
    assert send.settle(conn, clock, mail, "ap").failed == 1
    assert _row(conn)["status"] == "failed"


def test_unknown_stays_unknown_where_the_provider_keeps_no_copy(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, saves=False)
    smtp = FakeSender()
    smtp.fail = "unknown"
    with pytest.raises(SendOutcomeUnknownError):
        send.submit(conn, clock, smtp, _out(conn))
    for _ in range(3):
        send.settle(conn, clock, FakeMailSource(), "ap")
    assert (_row(conn)["status"], _row(conn)["searches"]) == ("unknown", 0)


# ---- the action runner ------------------------------------------------------------------------

SEND = [Planned("reply_template", "received")]


class Raises:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def __call__(self, conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row,
                 actions: list[Planned]) -> list[str]:  # fmt: skip
        raise self.exc


def _approved_send(conn: sqlite3.Connection, clock: FakeClock) -> str:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    approvals.request(conn, clock, sid, SEND)
    try:
        approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")
    except Exception as exc:
        approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user",
                          nonce=_verified_nonce(conn, clock, exc))  # type: ignore[arg-type]  # fmt: skip
    return sid


def test_runner_unknown_outcome_is_failed_unknown_with_the_grant_used(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _approved_send(conn, clock)
    assert execute.run_once(conn, clock, Raises(SendOutcomeUnknownError("no reply")))
    assert _status(conn, sid) == Status.FAILED_UNKNOWN and _grants(conn, sid) == ["consumed"]
    assert not execute.run_once(conn, clock, Raises(AssertionError("not run again")))


def test_runner_final_refusal_fails_at_once(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _approved_send(conn, clock)
    assert execute.run_once(conn, clock, Raises(SendNotSentError("550", retryable=False)))
    assert _status(conn, sid) == Status.FAILED and _grants(conn, sid) == ["voided"]


def test_runner_temporary_refusal_retries(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _approved_send(conn, clock)
    assert execute.run_once(conn, clock, Raises(SendNotSentError("451", retryable=True)))
    assert _status(conn, sid) == Status.EXECUTING and _grants(conn, sid) == ["approved"]
    row = conn.execute("SELECT attempts, visible_at FROM jobs WHERE queue = 'actions'").fetchone()
    assert row["attempts"] == 1 and row["visible_at"] > to_ts(clock.now())


def test_settle_runs_in_every_check() -> None:
    from ecf_server import checks  # noqa: PLC0415

    assert send.settle_in_check in checks.IN_LEASE
    assert send.settle_in_check(None, None, None, "ap", "i", 0, lambda: True) == send.Settled(  # type: ignore[arg-type]
        0, 0, 0
    )  # a lost lease: nothing touched
