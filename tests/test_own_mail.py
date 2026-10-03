"""ecf's own mail and other installs (V1.5 step 3c; SPEC §8.4, §8.5 trigger 9, §13.6; OD-318),
and requeueing a send only once it surely failed (OD-322, OD-329)."""

from __future__ import annotations

import sqlite3
from email.message import EmailMessage

import pytest

from ecf.errors import ConflictError, StepupRequiredError
from ecf.ids import StableId
from ecf.status import Status
from ecf_server import approvals, health, install_identity, items, own_mail
from ecf_server.checks import CheckReport
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.fetch import _pause_second_install  # pyright: ignore[reportPrivateUsage]
from ecf_server.message import parse
from ecf_server.notify import FakeNotifier
from ecf_server.outbound_msg import message_id_hash
from ecf_server.state_machine import TransitionContext
from tests.test_approvals import (
    _proposed,  # pyright: ignore[reportPrivateUsage]
    _setup,  # pyright: ignore[reportPrivateUsage]
    _status,  # pyright: ignore[reportPrivateUsage]
    _verified_nonce,  # pyright: ignore[reportPrivateUsage]
)
from tests.test_outbound_switch import SEND
from tests.test_triggers import fire, mail


def _msg(sender: str = "ap@acme.example", header: str | None = None,
         body: str = "Thanks.\n") -> bytes:  # fmt: skip
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, "pat@vendor.example", "Re: Invoice"
    m["Message-ID"] = "<abc.ecf@acme.example>"
    if header is not None:
        m["X-ECF-Install"] = header
    m.set_content(body)
    return m.as_bytes(policy=m.policy.clone(linesep="\r\n"))


@pytest.fixture
def ap(conn: sqlite3.Connection, clock: FakeClock) -> str:
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('ap', 'ap@acme.example', 'standard', 'A', 't')")  # fmt: skip
    return f"{install_identity.install_id(conn)}.0"


def _record_sent(conn: sqlite3.Connection, raw: bytes) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO sent (message_id_hash, address_id, content_hash, kind, sent_at,"
                     " message_id, status) VALUES (?, 'ap', ?, 'reply', 't', ?, 'sent')",
                     (message_id_hash("<abc.ecf@acme.example>"), parse(raw).content_hash,
                      "<abc.ecf@acme.example>"))  # fmt: skip


def test_own_mail_needs_sent_record_dmarc_and_same_content(conn: sqlite3.Connection,
                                                           ap: str) -> None:  # fmt: skip
    raw = _msg(header=ap)
    _record_sent(conn, raw)
    assert own_mail.classify(conn, parse(raw), "pass") == own_mail.OWN
    assert own_mail.classify(conn, parse(raw), "none") is None  # not authenticated
    changed = _msg(header="0" * 32 + ".0", body="Pay this instead.\n")  # same Message-ID
    assert own_mail.classify(conn, parse(changed), "pass") == own_mail.SECOND
    stranger = _msg(sender="pat@vendor.example", header=ap)
    assert own_mail.classify(conn, parse(stranger), "pass") is None  # not our address


def test_this_installs_header_from_its_own_address_is_its_own(conn: sqlite3.Connection,
                                                              ap: str) -> None:  # fmt: skip
    assert own_mail.classify(conn, parse(_msg(header=ap)), "pass") == own_mail.OWN
    assert own_mail.classify(conn, parse(_msg(header=ap[:-1] + "1")), "pass") == own_mail.SECOND
    assert own_mail.classify(conn, parse(_msg(header="yes")), "pass") == own_mail.SECOND
    assert own_mail.classify(conn, parse(_msg()), "pass") is None  # no marks at all


def test_trigger_9_fires_only_without_a_classification() -> None:
    tagged = mail("hi", extra={"X-ECF-Install": "other"})
    assert "carries an X-ECF-Install header" in fire("", raw=tagged).fraud
    assert "carries an X-ECF-Install header" not in fire("", raw=tagged, ecf_mail="own").fraud


def test_a_second_install_pauses_once_and_raises_operator_input_needed(
    conn: sqlite3.Connection, clock: FakeClock, ap: str
) -> None:
    del ap
    assert _pause_second_install(conn, clock, "ap") is True
    assert _pause_second_install(conn, clock, "ap") is False  # already paused
    notifier = FakeNotifier()
    report = CheckReport("ap", "ok", to_ts(clock.now()), second_install=True)
    health.after_check(conn, clock, notifier, report, resolve=lambda _h: True)
    [(title, detail)] = notifier.sent
    assert title == "[ecf-alert] Operator Input Needed: possible second install"
    assert "ecf resume ap" in detail
    health.after_check(conn, clock, notifier, CheckReport("ap", "ok", to_ts(clock.now())),
                       resolve=lambda _h: True)  # fmt: skip
    assert len(notifier.sent) == 1  # still paused: stays open
    with write_tx(conn):
        conn.execute("UPDATE addresses SET paused = 0")
    health.after_check(conn, clock, notifier, CheckReport("ap", "ok", to_ts(clock.now())),
                       resolve=lambda _h: True)  # fmt: skip
    assert notifier.sent[-1][0].startswith("[ecf-alert] Resolved:")


# ---- requeueing a send ---------------------------------------------------------------------------


def _failed_send(conn: sqlite3.Connection, clock: FakeClock, status: str, sent: str | None,
                 sensitivity: str = "standard") -> str:  # fmt: skip
    _setup(conn, clock, sensitivity=sensitivity)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET sensitivity = 'standard'")  # no delay on the way in
    sid = _proposed(conn, clock, "a" * 64)
    approvals.request(conn, clock, sid, SEND)
    with pytest.raises(StepupRequiredError) as ei:
        approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")
    approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user",
                      nonce=_verified_nonce(conn, clock, ei.value))  # fmt: skip
    with write_tx(conn):
        conn.execute("UPDATE addresses SET sensitivity = ?", (sensitivity,))
        g = conn.execute("SELECT grant_id FROM grants WHERE stable_id = ?", (sid,)).fetchone()[0]
        conn.execute("UPDATE grants SET status = 'consumed' WHERE grant_id = ?", (g,))
        conn.execute("DELETE FROM jobs WHERE queue = 'actions'")
        if sent:
            conn.execute("INSERT INTO sent (message_id_hash, address_id, content_hash, kind,"
                         " sent_at, message_id, stable_id, grant_id, status) VALUES ('m', 'ap',"
                         " 'h', 'reply', 't', '<m@x>', ?, ?, ?)", (sid, g, sent))  # fmt: skip
    items.transition(conn, clock, StableId(sid), Status(status), TransitionContext(),
                     actor="service", expected=Status.EXECUTING)  # fmt: skip
    return sid


@pytest.mark.parametrize(("status", "sent"), [("failed_unknown", "unknown"), ("failed", "sent"),
                                              ("failed", None)])  # fmt: skip
def test_a_send_that_may_have_gone_is_never_requeued(
    conn: sqlite3.Connection, clock: FakeClock, status: str, sent: str | None
) -> None:
    sid = _failed_send(conn, clock, status, sent)
    with pytest.raises(ConflictError, match="may already have gone out"):
        approvals.requeue(conn, clock, sid, actor="os_user", nonce=None)


def test_a_send_that_surely_failed_is_requeued_with_step_up(conn: sqlite3.Connection,
                                                            clock: FakeClock) -> None:  # fmt: skip
    sid = _failed_send(conn, clock, "failed", "failed")
    with pytest.raises(StepupRequiredError) as ei:
        approvals.requeue(conn, clock, sid, actor="os_user", nonce=None)
    r = approvals.requeue(conn, clock, sid, actor="os_user",
                          nonce=_verified_nonce(conn, clock, ei.value))  # fmt: skip
    assert r["status"] == "executing"


def test_a_requeued_send_on_a_high_address_waits_the_delay_again(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _failed_send(conn, clock, "failed", "failed", sensitivity="high")
    with pytest.raises(StepupRequiredError) as ei:
        approvals.requeue(conn, clock, sid, actor="os_user", nonce=None)
    r = approvals.requeue(conn, clock, sid, actor="os_user",
                          nonce=_verified_nonce(conn, clock, ei.value))  # fmt: skip
    assert r["status"] == "delayed" and _status(conn, sid) == "delayed"
    assert conn.execute("SELECT remaining_s FROM delays WHERE stable_id = ?",
                        (sid,)).fetchone()[0] == approvals.SEND_DELAY_S  # fmt: skip
