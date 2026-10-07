"""Gmail's daily download budget (V1.6 step 3b; SPEC §14.3; OD-440)."""

from __future__ import annotations

import sqlite3

import pytest

from ecf_server import db, download_budget, health, probe
from ecf_server.checks import CheckReport
from ecf_server.clock import FakeClock
from ecf_server.mail.fake import FakeMailSource, GmailFakeSource
from ecf_server.notify import FakeNotifier
from tests.mail_contract import message
from tests.test_fetch import ADDR, run, setup, started

__all__ = ["setup"]  # the fixture, used by name


def _gmail(conn: sqlite3.Connection, clock: FakeClock) -> GmailFakeSource:
    src = GmailFakeSource()
    with db.write_tx(conn):
        probe.store(conn, clock, ADDR, "imap.gmail.com", probe.probe(src, "imap.gmail.com"))
    started(conn, clock, src)
    return src


def test_the_window_is_the_last_24_hours_and_old_rows_go(
    setup: sqlite3.Connection, clock: FakeClock
) -> None:
    download_budget.record(setup, clock, ADDR, 100)
    clock.advance(3600)
    download_budget.record(setup, clock, ADDR, 50)
    download_budget.record(setup, clock, ADDR, 0)  # nothing to record
    assert download_budget.used(setup, clock, ADDR) == 150
    clock.advance(23 * 3600)  # the first hour is now 24 hours back
    assert download_budget.used(setup, clock, ADDR) == 50
    clock.advance(3 * 86400)
    download_budget.record(setup, clock, ADDR, 7)
    assert setup.execute("SELECT count(*) FROM downloads").fetchone()[0] == 1  # pruned
    left = download_budget.left(setup, clock, ADDR, gmail=True)
    assert left == download_budget.GMAIL_BYTES_PER_DAY - 7
    assert download_budget.left(setup, clock, ADDR, gmail=False) is None


def test_gmail_fetch_stops_at_the_budget_and_resumes_later(
    setup: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _gmail(setup, clock)
    size = len(message(0))
    monkeypatch.setattr(download_budget, "GMAIL_BYTES_PER_DAY", 2 * size + size // 2)
    for i in range(4):
        src.deliver(message(i))
    r = run(setup, clock, src)
    assert r.stopped == "download_budget" and len(r.created) == 2 and r.remaining == 2
    assert run(setup, clock, src).created == []  # still within the day: nothing read
    clock.advance(86400)
    r = run(setup, clock, src)
    assert len(r.created) == 2 and r.stopped == "done" and r.remaining == 0  # the other two


def test_no_budget_off_gmail(
    setup: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(download_budget, "GMAIL_BYTES_PER_DAY", 1)
    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0))
    assert len(run(setup, clock, src).created) == 1
    assert setup.execute("SELECT count(*) FROM downloads").fetchone()[0] == 0


def test_the_alert_opens_and_resolves(setup: sqlite3.Connection, clock: FakeClock) -> None:
    n = FakeNotifier()
    stopped = CheckReport(ADDR, "ok", "t", download_budget=True)
    health.after_check(setup, clock, n, stopped, resolve=lambda _h: True)
    assert n.sent[-1][0] == "[ecf-alert] Operator Input Needed: Gmail download limit reached"
    health.after_check(setup, clock, n, CheckReport(ADDR, "ok", "t"), resolve=lambda _h: True)
    assert n.sent[-1][0].startswith("[ecf-alert] Resolved: Operator Input Needed: Gmail download")


def test_a_one_off_corpus_fetch_counts_once_against_the_mailbox(
    setup: sqlite3.Connection, clock: FakeClock
) -> None:
    """OD-467 (R182): a one-off corpus fetch records by folded email only; the watched address's
    budget includes it, and each byte is counted exactly once."""
    download_budget.record(setup, clock, ADDR, 100)  # live fetch of ap@acme.example
    download_budget.record_email(setup, clock, "AP@acme.example", 1)  # one-off, other spelling
    assert download_budget.used(setup, clock, ADDR) == 101
    assert download_budget.used_email(setup, clock, "ap@acme.example") == 101
    assert setup.execute("SELECT count(*) FROM downloads").fetchone()[0] == 1  # not both tables
    left = download_budget.left_email(setup, clock, "ap@acme.example", gmail=True)
    assert left == download_budget.GMAIL_BYTES_PER_DAY - 101
    assert download_budget.left_email(setup, clock, "ap@acme.example", gmail=False) is None


def test_corpus_bytes_for_an_unwatched_mailbox_fold_gmail_spellings(
    setup: sqlite3.Connection, clock: FakeClock
) -> None:
    download_budget.record_email(setup, clock, "Pat.Lee+news@googlemail.com", 40)
    download_budget.record_email(setup, clock, "patlee@gmail.com", 2)
    assert download_budget.used_email(setup, clock, "pat.lee@gmail.com") == 42
    assert download_budget.used(setup, clock, ADDR) == 0  # another mailbox
    clock.advance(3 * 86400)
    download_budget.record_email(setup, clock, "patlee@gmail.com", 1)
    assert setup.execute("SELECT count(*) FROM corpus_downloads").fetchone()[0] == 1  # pruned
