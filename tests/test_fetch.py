"""Leases, the cursor and fetching one page (V1.1 step 6a)."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ecf_server import db, leases
from ecf_server.clock import FakeClock
from ecf_server.fetch import (
    LeaseLostError,
    PageResult,
    address_config,
    fetch_page,
    load_cursor,
)
from ecf_server.mail.fake import FakeMailSource
from ecf_server.message import ParsedMessage
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


def run(conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource, **kw: Any) -> PageResult:
    return fetch_page(conn, clock, src, address_config(conn, ADDR), take(conn, clock), **kw)


def started(conn: sqlite3.Connection, clock: FakeClock, src: FakeMailSource) -> None:
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

    r = run(setup, clock, src, analyze=slow)
    assert r.stopped == "time" and len(r.created) == 3 and r.remaining == 3


def test_analysis_is_merged_into_facts(setup: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0))

    def analyze(_p: ParsedMessage, _raw: bytes) -> dict[str, Any]:
        return {"auth_result": "pass"}

    r = run(setup, clock, src, analyze=analyze)
    row = setup.execute("SELECT facts FROM items WHERE stable_id = ?", (r.created[0],)).fetchone()
    assert json.loads(row["facts"])["auth_result"] == "pass"


def test_large_messages_are_deferred_not_skipped(
    setup: sqlite3.Connection, clock: FakeClock
) -> None:
    setup.execute(
        "UPDATE addresses SET overrides = ? WHERE address_id = ?",
        (json.dumps({"max_message_bytes": 600}), ADDR),
    )
    src = FakeMailSource()
    started(setup, clock, src)
    src.deliver(message(0))  # small
    src.deliver(message(1, multipart=True))  # over 600 bytes
    r = run(setup, clock, src)
    assert len(r.created) == 1 and r.deferred == [2]
    cur = load_cursor(setup, ADDR)
    assert cur is not None and cur.deferred == [2] and cur.last_uid == 2


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
            run(setup, clock, src, analyze=boom)
        assert setup.execute("SELECT count(*) FROM items").fetchone()[0] == 0
    r = run(setup, clock, src, analyze=boom)
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


def test_mailbox_reset_is_reported_not_handled(setup: sqlite3.Connection, clock: FakeClock) -> None:
    src = FakeMailSource(uidvalidity=1)
    started(setup, clock, src)
    src.deliver(message(0))
    src.reset(uidvalidity=2)
    r = run(setup, clock, src)
    assert r.reset_detected and r.created == []
