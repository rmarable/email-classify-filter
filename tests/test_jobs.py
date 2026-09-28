import sqlite3
import threading
from pathlib import Path

import pytest

from ecf.errors import ConflictError
from ecf.ids import AddressId, JobId
from ecf_server import db, jobs
from ecf_server.clock import FakeClock
from ecf_server.jobs import Queue

A, B = AddressId("a"), AddressId("b")


def test_fifo_and_one_in_flight_per_address(conn: sqlite3.Connection, clock: FakeClock) -> None:
    a1 = jobs.enqueue(conn, clock, Queue.ACTIONS, A, {"n": 1}, timeout_s=60)
    clock.advance(1)
    a2 = jobs.enqueue(conn, clock, Queue.ACTIONS, A, {"n": 2}, timeout_s=60)
    b1 = jobs.enqueue(conn, clock, Queue.ACTIONS, B, {"n": 3}, timeout_s=60)
    first = jobs.claim(conn, clock, Queue.ACTIONS, "w1")
    assert first is not None and first.job_id == a1 and first.attempts == 1
    second = jobs.claim(conn, clock, Queue.ACTIONS, "w2")
    assert second is not None and second.job_id == b1  # a2 waits behind a1
    assert jobs.claim(conn, clock, Queue.ACTIONS, "w3") is None
    jobs.complete(conn, a1, "w1")
    third = jobs.claim(conn, clock, Queue.ACTIONS, "w3")
    assert third is not None and third.job_id == a2


def test_queues_are_separate(conn: sqlite3.Connection, clock: FakeClock) -> None:
    jobs.enqueue(conn, clock, Queue.SLACK_OUT, A, {}, timeout_s=10)
    assert jobs.claim(conn, clock, Queue.ACTIONS, "w") is None
    assert jobs.claim(conn, clock, Queue.SLACK_OUT, "w") is not None


def test_backoff_then_dead(conn: sqlite3.Connection, clock: FakeClock) -> None:
    jid = jobs.enqueue(conn, clock, Queue.FETCH, A, {}, timeout_s=10)
    for delay in (*jobs.BACKOFF_S, None):
        job = jobs.claim(conn, clock, Queue.FETCH, "w")
        assert job is not None and job.job_id == jid
        state = jobs.fail(conn, clock, jid, "w", "boom")
        if delay is None:
            assert state == "dead"
            break
        assert state == "queued"
        clock.advance(delay - 1)
        assert jobs.claim(conn, clock, Queue.FETCH, "w") is None  # not visible yet
        clock.advance(1)
    row = conn.execute("SELECT state, attempts FROM jobs WHERE job_id = ?", (jid,)).fetchone()
    assert (row["state"], row["attempts"]) == ("dead", jobs.DEFAULT_MAX_ATTEMPTS)


def test_expired_claim_returns_to_queue(conn: sqlite3.Connection, clock: FakeClock) -> None:
    jid = jobs.enqueue(conn, clock, Queue.MODEL, A, {}, timeout_s=10)
    assert jobs.claim(conn, clock, Queue.MODEL, "crashed") is not None
    clock.advance(10 * jobs.CLAIM_FACTOR - 1)
    assert jobs.claim(conn, clock, Queue.MODEL, "w2") is None
    clock.advance(2)
    again = jobs.claim(conn, clock, Queue.MODEL, "w2")
    assert again is not None and again.job_id == jid and again.attempts == 2
    with pytest.raises(ConflictError):
        jobs.complete(conn, jid, "crashed")


def test_no_double_claims_under_concurrency(
    db_path: Path, conn: sqlite3.Connection, clock: FakeClock
) -> None:
    for i in range(40):
        jobs.enqueue(conn, clock, Queue.ACTIONS, AddressId(f"addr-{i % 5}"), {"i": i}, timeout_s=60)
    claimed: list[JobId] = []
    in_flight: dict[str, str] = {}
    lock = threading.Lock()
    errors: list[str] = []

    def worker(name: str) -> None:
        c = db.connect(db_path)
        try:
            while True:
                job = jobs.claim(c, clock, Queue.ACTIONS, name)
                if job is None:
                    with lock:
                        if len(claimed) >= 40:
                            return
                    continue
                with lock:
                    if job.address_id in in_flight:
                        errors.append(f"two in flight for {job.address_id}")
                    in_flight[job.address_id] = job.job_id
                    claimed.append(job.job_id)
                jobs.complete(c, job.job_id, name)
                with lock:
                    del in_flight[job.address_id]
        finally:
            c.close()

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors
    assert len(claimed) == 40 and len(set(claimed)) == 40


def test_poison_job_dead_letters_when_claims_keep_expiring(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    jid = jobs.enqueue(conn, clock, Queue.ACTIONS, A, {}, timeout_s=10, max_attempts=2)
    for _ in range(4):
        jobs.claim(conn, clock, Queue.ACTIONS, "hangs")
        clock.advance(10 * jobs.CLAIM_FACTOR + 1)
    jobs.claim(conn, clock, Queue.ACTIONS, "sweeper")  # the sweep runs on every claim
    row = conn.execute("SELECT state, attempts FROM jobs WHERE job_id = ?", (jid,)).fetchone()
    assert (row["state"], row["attempts"]) == ("dead", 2)


def test_backoff_holds_back_later_jobs_for_the_same_address(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    a1 = jobs.enqueue(conn, clock, Queue.ACTIONS, A, {"n": 1}, timeout_s=10)
    a2 = jobs.enqueue(conn, clock, Queue.ACTIONS, A, {"n": 2}, timeout_s=10)  # same timestamp
    b1 = jobs.enqueue(conn, clock, Queue.ACTIONS, B, {"n": 3}, timeout_s=10)
    first = jobs.claim(conn, clock, Queue.ACTIONS, "w")
    assert first is not None and first.job_id == a1
    jobs.fail(conn, clock, a1, "w", "boom")  # a1 now waits 30 s
    nxt = jobs.claim(conn, clock, Queue.ACTIONS, "w")
    assert nxt is not None and nxt.job_id == b1  # other addresses keep going
    assert jobs.claim(conn, clock, Queue.ACTIONS, "w") is None  # a2 waits behind a1
    jobs.complete(conn, b1, "w")
    clock.advance(jobs.BACKOFF_S[0])
    again = jobs.claim(conn, clock, Queue.ACTIONS, "w")
    assert again is not None and again.job_id == a1
    jobs.complete(conn, a1, "w")
    last = jobs.claim(conn, clock, Queue.ACTIONS, "w")
    assert last is not None and last.job_id == a2
