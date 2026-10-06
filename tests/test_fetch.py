"""Leases, the cursor and fetching one page (V1.1 step 6a)."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ecf_server import db, fetch, leases, probe
from ecf_server.clock import FakeClock
from ecf_server.fetch import (
    LeaseLostError,
    PageResult,
    address_config,
    fetch_page,
    load_cursor,
)
from ecf_server.isolate import IsolationError
from ecf_server.mail import MailSource
from ecf_server.mail.fake import FakeMailSource, GmailFakeSource
from ecf_server.message import ParsedMessage, parse
from ecf_server.senderauth import AuthOutcome
from tests.mail_contract import message

ADDR = "ap"


@pytest.fixture
def setup(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute(
        "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
        " VALUES (?, 'ap@acme.example', 'high', 'A', '2026-10-01T12:00:00.000000Z')",
        (ADDR,),
    )
    yield conn


def take(conn: sqlite3.Connection, clock: FakeClock, holder: str = "w1") -> leases.Lease:
    lease = leases.acquire(conn, clock, ADDR, holder)
    assert lease is not None
    return lease


def run(conn: sqlite3.Connection, clock: FakeClock, src: MailSource, **kw: Any) -> PageResult:
    return fetch_page(conn, clock, src, address_config(conn, ADDR), take(conn, clock), **kw)


class Fn:
    """An Analyzer from a plain function (record does nothing)."""

    def __init__(self, fn: Callable[[ParsedMessage, bytes], dict[str, Any]]) -> None:
        self.fn = fn
        self.auths: list[AuthOutcome | None] = []
        self.labels: list[frozenset[str] | None] = []

    def analyze(
        self,
        parsed: ParsedMessage,
        raw: bytes,
        auth: AuthOutcome | None = None,
        gmail_labels: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        self.auths.append(auth)
        self.labels.append(gmail_labels)
        return self.fn(parsed, raw)

    def record(
        self, conn: sqlite3.Connection, parsed: ParsedMessage, facts: dict[str, Any]
    ) -> None:
        pass


def started(conn: sqlite3.Connection, clock: FakeClock, src: MailSource) -> None:
    assert run(conn, clock, src).first_run


# ---- leases ---------------------------------------------------------------------------------


def test_lease_holder_expiry_and_fencing(setup: sqlite3.Connection, clock: FakeClock) -> None:
    a = take(setup, clock, "w1")
    assert leases.acquire(setup, clock, ADDR, "w2") is None
    assert leases.renew(setup, clock, a) and leases.held(setup, clock, a)
    clock.advance(181)
    assert not leases.held(setup, clock, a)
    b = leases.acquire(setup, clock, ADDR, "w2")
    assert b is not None and b.token == a.token + 1
    assert not leases.renew(setup, clock, a)
    leases.release(setup, a)  # a stale holder can't release the new lease
    assert leases.held(setup, clock, b)
    leases.release(setup, b)
    assert leases.acquire(setup, clock, ADDR, "w3") is not None


def test_renewer_keeps_the_lease_and_reports_loss(
    setup: sqlite3.Connection, clock: FakeClock, db_path: Path
) -> None:
    lease = take(setup, clock)
    with leases.Renewer(lambda: db.connect(db_path), clock, lease, every_s=0.05) as r:
        time.sleep(0.2)
        assert not r.lost.is_set()
        with db.write_tx(setup):  # someone else takes it over
            setup.execute("UPDATE leases SET fencing_token = fencing_token + 1")
        deadline = time.monotonic() + 2
        while not r.lost.is_set() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert r.lost.is_set()


# ---- pages ----------------------------------------------------------------------------------


def test_first_run_starts_from_now(setup: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource(uidvalidity=9)
    src.deliver(message(0))
    started(setup, clock, src)
    cur = load_cursor(setup, ADDR)
    assert cur is not None and (cur.uidvalidity, cur.last_uid) == (9, 1)
    assert run(setup, clock, src).created == []  # the old message is not fetched


def test_page_creates_items_with_excerpts(setup: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    started(setup, clock, src)
    for i in range(3):
        src.deliver(message(i, multipart=True))
    r = run(setup, clock, src)
    assert len(r.created) == 3 and r.stopped == "done" and r.remaining == 0
    row = setup.execute("SELECT * FROM items WHERE stable_id = ?", (r.created[0],)).fetchone()
    assert row["status"] == "new" and row["uid"] == 1 and row["uidvalidity"] == 1
    assert row["message_id"] == "<contract-0@synthetic.acme.example>"
    assert (row["subject"], row["sender"], row["sender_name"]) == (
        "Test message 0",
        "s0@vendor-a.example",
        "Sender 0",
    )  # what its card shows (V1.2)
    assert json.loads(row["locator"])["uid"] == 1
    facts = json.loads(row["facts"])
    assert facts["attachments"][0]["name"] == "inv.pdf" and facts["from_count"] == 1
    ex = setup.execute("SELECT * FROM excerpts WHERE stable_id = ?", (r.created[0],)).fetchone()
    assert ex["classifier_text"].startswith("Plain body 0")
    assert setup.execute("SELECT count(*) FROM processing").fetchone()[0] == 0
    cur = load_cursor(setup, ADDR)
    assert cur is not None and cur.last_uid == 3
    assert run(setup, clock, src).created == []


def test_page_limit_and_the_rest_next_time(setup: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    started(setup, clock, src)
    for i in range(35):
        src.deliver(message(i))
    r = run(setup, clock, src)
    assert (len(r.created), r.stopped, r.remaining) == (30, "page_limit", 5)
    r = run(setup, clock, src)
    assert (len(r.created), r.stopped, r.remaining) == (5, "done", 0)


def test_time_budget_stops_the_page(setup: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    started(setup, clock, src)
    for i in range(6):
        src.deliver(message(i))

    def slow(_p: ParsedMessage, _raw: bytes) -> dict[str, Any]:
        clock.advance(8)  # FakeClock moves wall and monotonic time together
        return {}

    r = run(setup, clock, src, analyzer=Fn(slow))
    assert r.stopped == "time" and len(r.created) == 3 and r.remaining == 3


def test_analysis_is_merged_into_facts(setup: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0))

    def analyze(_p: ParsedMessage, _raw: bytes) -> dict[str, Any]:
        return {"auth_result": "pass"}

    r = run(setup, clock, src, analyzer=Fn(analyze))
    row = setup.execute("SELECT facts FROM items WHERE stable_id = ?", (r.created[0],)).fetchone()
    assert json.loads(row["facts"])["auth_result"] == "pass"


def test_gmail_labels_reach_the_analyzer(setup: sqlite3.Connection, clock: FakeClock) -> None:
    """V1.6: in Gmail mode (from the last probe) each message's labels go to the analysis, for
    `self_sent` (OD-446); off Gmail none are read."""
    src = GmailFakeSource()
    with db.write_tx(setup):
        probe.store(setup, clock, ADDR, "imap.gmail.com", probe.probe(src, "imap.gmail.com"))
    started(setup, clock, src)
    a, b = src.deliver(message(0)), src.deliver(message(1), labels=("\\Sent", "\\Inbox"))
    fn = Fn(lambda _p, _r: {})
    run(setup, clock, src, analyzer=fn)
    assert fn.labels == [frozenset({"\\Inbox"}), frozenset({"\\Sent", "\\Inbox"})]
    assert a < b


def test_no_labels_off_gmail(setup: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0))
    fn = Fn(lambda _p, _r: {})
    run(setup, clock, src, analyzer=fn)
    assert fn.labels == [None]


def _limit(conn: sqlite3.Connection, max_bytes: int) -> None:
    conn.execute(
        "UPDATE addresses SET overrides = ? WHERE address_id = ?",
        (json.dumps({"max_message_bytes": max_bytes}), ADDR),
    )


def _provider_limit(
    conn: sqlite3.Connection, max_bytes: int | None, source: str = "provider table (tested)"
) -> None:
    conn.execute(
        "INSERT INTO probe (address_id, max_message_bytes, host, probed_at, max_size_source)"
        " VALUES (?, ?, 'imap.example', 'now', ?)",
        (ADDR, max_bytes, source),
    )


@pytest.mark.parametrize(
    ("override", "provider", "expected"),
    [
        (None, 51_200_000, 51_200_000),  # high default 64 MB, provider smaller: capped
        (None, 80 * fetch.MB, 64 * fetch.MB),  # provider larger: ecf's own limit
        (None, None, 64 * fetch.MB),  # provider limit unknown
        (60 * fetch.MB, 51_200_000, 51_200_000),  # an explicit setting is capped too
        (20 * fetch.MB, 51_200_000, 20 * fetch.MB),  # a smaller setting is kept
    ],
)
def test_size_limit_is_capped_at_the_provider(
    setup: sqlite3.Connection, override: int | None, provider: int | None, expected: int
) -> None:
    if override is not None:
        _limit(setup, override)
    _provider_limit(setup, provider)
    assert address_config(setup, ADDR).max_message_bytes == expected


def test_appendlimit_is_not_a_receiving_limit(setup: sqlite3.Connection) -> None:
    """OD-200: APPENDLIMIT bounds uploads; Gmail's is 34 MB but it receives about 50 MB."""
    _provider_limit(setup, 35_651_584, source="APPENDLIMIT")
    assert address_config(setup, ADDR).max_message_bytes == 64 * fetch.MB


def test_a_network_error_mid_fetch_is_not_a_crash(
    setup: sqlite3.Connection, clock: FakeClock
) -> None:
    """V1.1 review: two connection drops quarantined an ordinary message, unread."""
    from ecf.errors import MailUnavailableError  # noqa: PLC0415

    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0))
    real = src.fetch
    drops = {"left": fetch.QUARANTINE_AFTER + 1}

    def flaky(uid: int) -> bytes | None:
        if drops["left"]:
            drops["left"] -= 1
            raise MailUnavailableError("IMAP command failed on imap.example: timeout")
        return real(uid)

    src.fetch = flaky  # type: ignore[method-assign]
    for _ in range(fetch.QUARANTINE_AFTER + 1):
        with pytest.raises(MailUnavailableError):
            run(setup, clock, src)
        setup.execute("DELETE FROM leases")
    assert setup.execute("SELECT count(*) FROM processing").fetchone()[0] == 0
    r = run(setup, clock, src)
    assert len(r.created) == 1 and r.quarantined == []


def test_oversized_mail_is_read_partially_after_the_page(
    setup: sqlite3.Connection, clock: FakeClock
) -> None:
    _limit(setup, 600)
    src = FakeMailSource()
    started(setup, clock, src)
    big = message(1, multipart=True)  # over 600 bytes
    src.deliver(message(0))  # small
    src.deliver(big)
    r = run(setup, clock, src)
    assert len(r.created) == 2 and r.large_done == [2] and r.deferred == []
    row = setup.execute("SELECT * FROM items WHERE uid = 2").fetchone()
    facts = json.loads(row["facts"])
    assert facts["oversized"] and facts["content_unscanned"] and row["hash_version"] == 0
    assert facts["attachments"][0]["name"] == "inv.pdf"
    ex = setup.execute(
        "SELECT classifier_text FROM excerpts WHERE stable_id = ?", (row["stable_id"],)
    ).fetchone()
    assert ex["classifier_text"].startswith("Plain body 1")
    cur = load_cursor(setup, ADDR)
    assert cur is not None and cur.deferred == [] and cur.last_uid == 2
    src.deliver(big)  # the same large message again
    src.deliver(b"Received: from relay2.example by mx.acme.example\r\n" + big)  # another route
    assert run(setup, clock, src).duplicates == 2


def test_large_mail_waits_for_budget_and_the_lock(
    setup: sqlite3.Connection, clock: FakeClock
) -> None:
    _limit(setup, 600)
    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0, multipart=True))
    lease = take(setup, clock)
    cfg = address_config(setup, ADDR)
    r = fetch_page(setup, clock, src, cfg, lease, deadline=clock.monotonic())  # no time left
    assert r.deferred == [1] and r.large_done == [] and r.created == []
    with fetch.LARGE_LOCK:  # another address is busy with a large message
        r = fetch_page(setup, clock, src, cfg, take(setup, clock))
    assert r.deferred == [1]
    r = fetch_page(setup, clock, src, cfg, take(setup, clock))
    assert r.large_done == [1] and r.deferred == []


def test_large_mail_within_the_limit_is_read_whole(
    setup: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch, "LARGE_BYTES", 600)  # "large" without building 16 MB messages
    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0, multipart=True))
    r = run(setup, clock, src)
    assert r.large_done == [1]
    row = setup.execute("SELECT facts, hash_version FROM items").fetchone()
    assert row["hash_version"] == 1 and "oversized" not in json.loads(row["facts"])


def test_every_message_goes_to_the_isolator(
    setup: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch, "LARGE_BYTES", 600)
    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0))  # small
    src.deliver(message(1, multipart=True))  # large: after the page, still isolated (OD-204)
    sizes: list[int] = []

    def isolator(raw: bytes, max_scan_bytes: int) -> tuple[ParsedMessage, AuthOutcome]:
        sizes.append(len(raw))
        return parse(raw, max_scan_bytes=max_scan_bytes), AuthOutcome("none", "isolated")

    analyzer = Fn(lambda _p, _r: {})
    r = run(setup, clock, src, analyzer=analyzer, isolator=isolator)
    assert len(r.created) == 2 and r.large_done == [2] and len(sizes) == 2 and sizes[1] > 600
    assert analyzer.auths == [AuthOutcome("none", "isolated")] * 2


def test_an_isolator_failure_counts_as_a_crash(
    setup: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch, "LARGE_BYTES", 600)
    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0, multipart=True))

    def broken(raw: bytes, max_scan_bytes: int) -> tuple[ParsedMessage, AuthOutcome]:
        raise IsolationError("child exited 3: MemoryError")

    for _ in range(fetch.QUARANTINE_AFTER):
        with pytest.raises(IsolationError):
            run(setup, clock, src, isolator=broken)
    r = run(setup, clock, src, isolator=broken)
    assert r.quarantined == [1]


def test_losing_the_lease_is_not_a_crash(setup: sqlite3.Connection, clock: FakeClock) -> None:
    """A check that loses its lease mid-message gives the attempt back, so repeated lease losses
    never quarantine the message (found in the V1.1 shadow run, 2026-09-29)."""
    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0))

    def taken(_p: ParsedMessage, _r: bytes) -> dict[str, Any]:
        setup.execute("UPDATE leases SET fencing_token = fencing_token + 1")  # another holder
        return {}

    for _ in range(fetch.QUARANTINE_AFTER + 1):
        with pytest.raises(LeaseLostError):
            run(setup, clock, src, analyzer=Fn(taken))
        setup.execute("DELETE FROM leases")
        assert setup.execute("SELECT count(*) FROM processing").fetchone()[0] == 0
    r = run(setup, clock, src)
    assert len(r.created) == 1 and r.quarantined == []


def test_deferred_mail_that_disappears_is_dropped(
    setup: sqlite3.Connection, clock: FakeClock
) -> None:
    _limit(setup, 600)
    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0, multipart=True))
    cfg = address_config(setup, ADDR)
    fetch_page(setup, clock, src, cfg, take(setup, clock), deadline=clock.monotonic())
    src.expunge(1)
    r = fetch_page(setup, clock, src, cfg, take(setup, clock))
    assert r.deferred == [] and r.large_done == []
    assert setup.execute("SELECT count(*) FROM items").fetchone()[0] == 0


def test_duplicate_delivery_and_reused_message_id(
    setup: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0))
    src.deliver(message(0))  # the same message again
    src.deliver(message(0).replace(b"Plain body 0", b"Other body"))  # same Message-ID
    r = run(setup, clock, src)
    assert len(r.created) == 2 and r.duplicates == 1
    flags = [
        x["duplicate_message_id"]
        for x in setup.execute("SELECT duplicate_message_id FROM items ORDER BY uid")
    ]
    assert flags == [0, 1]
    events = [x["event"] for x in setup.execute("SELECT event FROM audit")]
    assert events.count("item.duplicate_delivery") == 1


def test_two_crashes_quarantine_the_message(setup: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0))

    def boom(_p: ParsedMessage, _raw: bytes) -> dict[str, Any]:
        raise RuntimeError("crafted message crashes the parser")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            run(setup, clock, src, analyzer=Fn(boom))
        assert setup.execute("SELECT count(*) FROM items").fetchone()[0] == 0
    r = run(setup, clock, src, analyzer=Fn(boom))
    assert r.quarantined == [1] and r.created == []
    row = setup.execute("SELECT * FROM items").fetchone()
    assert json.loads(row["facts"]) == {"quarantined": True, "content_unscanned": True}
    assert (
        row["status"] == "new"
        and setup.execute("SELECT count(*) FROM processing").fetchone()[0] == 0
    )
    cur = load_cursor(setup, ADDR)
    assert cur is not None and cur.last_uid == 1


def test_a_stale_holder_writes_nothing(setup: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0))
    stale = take(setup, clock, "w1")
    clock.advance(181)
    assert leases.acquire(setup, clock, ADDR, "w2") is not None
    with pytest.raises(LeaseLostError):
        fetch_page(setup, clock, src, address_config(setup, ADDR), stale)
    assert setup.execute("SELECT count(*) FROM items").fetchone()[0] == 0
    cur = load_cursor(setup, ADDR)
    assert cur is not None and cur.last_uid == 0


def test_lost_event_stops_before_touching_anything(
    setup: sqlite3.Connection, clock: FakeClock
) -> None:
    import threading  # noqa: PLC0415

    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0))
    lost = threading.Event()
    lost.set()
    r = run(setup, clock, src, lost=lost)
    assert r.stopped == "lease_lost" and r.created == [] and r.remaining == 1


def test_mailbox_reset_recovers_known_messages(setup: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource(uidvalidity=1)
    started(setup, clock, src)
    for i in range(3):
        src.deliver(message(i), clock.now())
    assert len(run(setup, clock, src).created) == 3
    src.expunge(1)
    src.reset(uidvalidity=2)  # the provider renumbers: the two left become UIDs 1 and 2
    src.deliver(message(9), clock.now())
    r = run(setup, clock, src)
    assert r.reset_detected and r.relocated == 2 and len(r.created) == 1 and r.duplicates == 0
    locs = [json.loads(x["locator"]) for x in setup.execute("SELECT locator FROM items")]
    assert sorted(loc["uidvalidity"] for loc in locs) == [1, 2, 2, 2]  # the deleted one keeps 1
    cur = load_cursor(setup, ADDR)
    assert cur is not None and cur.uidvalidity == 2 and cur.recovering_until == 0
    events = [x["event"] for x in setup.execute("SELECT event FROM audit")]
    assert "mailbox.reset" in events
    assert run(setup, clock, src).created == []


def test_reset_matches_messages_without_a_message_id(
    setup: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource(uidvalidity=1)
    started(setup, clock, src)
    bare = message(0).replace(b"Message-ID: <contract-0@synthetic.acme.example>\r\n", b"")
    assert b"Message-ID" not in bare
    src.deliver(bare, datetime(2026, 10, 1, 9, 0, tzinfo=UTC))  # the test clock's day
    assert len(run(setup, clock, src).created) == 1
    src.reset(uidvalidity=5)
    r = run(setup, clock, src)
    assert r.relocated == 1 and r.created == []
    assert setup.execute("SELECT count(*) FROM items").fetchone()[0] == 1


def test_close_gone_resolves_items_whose_message_left(
    setup: sqlite3.Connection, clock: FakeClock
) -> None:
    src = FakeMailSource(uidvalidity=1)
    started(setup, clock, src)
    src.deliver(message(0))
    src.deliver(message(1))
    run(setup, clock, src)
    src.expunge(1)  # archived by the person
    lease = take(setup, clock)
    assert fetch.close_gone(setup, clock, src, lease, 1) == 1
    statuses = [x["status"] for x in setup.execute("SELECT status FROM items ORDER BY uid")]
    assert statuses == ["resolved_by_mailbox", "new"]
    assert fetch.close_gone(setup, clock, src, lease, 1) == 0  # closed items stay closed
    assert fetch.close_gone(setup, clock, src, lease, 99) == 0  # another UIDVALIDITY: skipped
    # ... unless the caller knows recovery after a reset is done (V1.1 review)
    assert fetch.close_gone(setup, clock, src, lease, 99, close_stale=True) == 1


@pytest.mark.imap
def test_oversized_path_against_dovecot(
    setup: sqlite3.Connection, clock: FakeClock, dovecot_server: Any
) -> None:
    import uuid  # noqa: PLC0415
    from datetime import UTC, datetime  # noqa: PLC0415

    from ecf_server.mail.imap import ImapSource  # noqa: PLC0415
    from tests import dovecot  # noqa: PLC0415

    dv: dovecot.Dovecot = dovecot_server
    user = f"ecf-t-{uuid.uuid4().hex[:12]}"
    src = ImapSource(
        dv.host, user, lambda: dovecot.PASSWORD, port=dv.port, ssl_context=dv.context()
    )
    admin = dv.admin(user)
    try:
        _limit(setup, 600)
        cfg = address_config(setup, ADDR)
        assert fetch_page(setup, clock, src, cfg, take(setup, clock)).first_run
        dovecot.append(admin, message(0), datetime.now(UTC))
        dovecot.append(admin, message(1, multipart=True), datetime.now(UTC))
        r = fetch_page(setup, clock, src, cfg, take(setup, clock))
        assert len(r.created) == 2 and r.large_done == [2] and r.deferred == []
        row = setup.execute("SELECT * FROM items WHERE uid = 2").fetchone()
        assert row["hash_version"] == 0 and json.loads(row["facts"])["oversized"]
        ex = setup.execute(
            "SELECT classifier_text FROM excerpts WHERE stable_id = ?", (row["stable_id"],)
        ).fetchone()
        assert ex["classifier_text"].startswith("Plain body 1")
        assert "\\Seen" not in src.flags([1, 2])[2]
    finally:
        src.close()
        admin.logout()


def test_a_resend_with_other_identity_headers_is_a_new_item(
    setup: sqlite3.Connection, clock: FakeClock
) -> None:
    """Same body and Message-ID, a forged sender: analyzed as a new item with trigger 5's flag,
    not filed as a repeat delivery (V1.1 review, 2026-09-29)."""
    src = FakeMailSource()
    started(setup, clock, src)
    first = message(0)
    src.deliver(first)
    src.deliver(first)  # a true repeat
    spoofed = first.replace(
        b"From: Sender 0 <s0@vendor-a.example>", b"From: CEO <ceo@acme.example>"
    )
    assert spoofed != first
    src.deliver(spoofed)
    r = run(setup, clock, src)
    assert len(r.created) == 2 and r.duplicates == 1
    rows = setup.execute("SELECT duplicate_message_id FROM items ORDER BY uid").fetchall()
    assert [x["duplicate_message_id"] for x in rows] == [0, 1]


def test_reset_recovery_finds_a_backlog_read_late(
    setup: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mail that waited days before a check read part of it, then a reset: the rest and a
    deferred message must still be read (V1.1 review, 2026-09-29)."""
    monkeypatch.setattr(fetch, "LARGE_BYTES", 600)
    src = FakeMailSource()
    started(setup, clock, src)
    old = clock.now() - timedelta(days=3)
    src.deliver(message(99, multipart=True), internaldate=old)  # large: deferred below
    for i in range(35):
        src.deliver(message(i), internaldate=old + timedelta(minutes=i + 1))
    with fetch.LARGE_LOCK:  # another address holds the large-message slot
        r = run(setup, clock, src)
    assert len(r.created) == 29 and r.remaining == 6 and r.deferred == [1]  # page of 30
    cur = load_cursor(setup, ADDR)
    assert cur is not None and cur.deferred_since is not None
    src.reset(uidvalidity=9)
    for _ in range(5):
        r = run(setup, clock, src)
        if r.remaining == 0 and not r.deferred:
            break
    assert setup.execute("SELECT count(*) FROM items").fetchone()[0] == 36


def test_own_mail_is_skipped_and_another_install_pauses(setup: sqlite3.Connection,
                                                        clock: FakeClock) -> None:  # fmt: skip
    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0))
    src.deliver(message(1))
    marks = iter(["own", "second_install"])
    r = run(setup, clock, src, analyzer=Fn(lambda _p, _r: {"ecf_mail": next(marks)}))
    assert r.own_skipped == 1 and len(r.created) == 1 and r.second_install
    assert setup.execute("SELECT paused FROM addresses").fetchone()[0] == 1
    events = [e[0] for e in setup.execute("SELECT event FROM audit ORDER BY id")]
    assert "mail.own_skipped" in events and "address.paused" in events
