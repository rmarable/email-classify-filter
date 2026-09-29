"""The SQLite job queue (SPEC §11.3): same ordering, retry and dead-letter semantics as the M1 SQS
FIFO queues. Per (queue, address) only the oldest unfinished job is eligible, so a job waiting in
backoff holds back later jobs for that address (like an SQS FIFO message group). Claims expire
after 6x the job's timeout; an expired claim counts as an attempt and dead-letters at the limit."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any

from ecf.errors import ConflictError, NotFoundError
from ecf.ids import AddressId, JobId, new_job_id
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx

BACKOFF_S = (30, 120, 600, 1800)
DEFAULT_MAX_ATTEMPTS = 5
CLAIM_FACTOR = 6


class Queue(StrEnum):
    ACTIONS = "actions"
    SLACK_OUT = "slack_out"
    FETCH = "fetch"
    MODEL = "model"


@dataclass(frozen=True)
class Job:
    job_id: JobId
    queue: Queue
    address_id: AddressId
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    timeout_s: int


def enqueue(
    conn: sqlite3.Connection,
    clock: Clock,
    queue: Queue,
    address_id: AddressId,
    payload: dict[str, Any],
    *,
    timeout_s: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    delay_s: int = 0,
) -> JobId:
    now = clock.now()
    job_id = new_job_id()
    with write_tx(conn):
        conn.execute(
            "INSERT INTO jobs (job_id, queue, address_id, payload, max_attempts, timeout_s, "
            "visible_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                queue.value,
                address_id,
                json.dumps(payload),
                max_attempts,
                timeout_s,
                to_ts(now + timedelta(seconds=delay_s)),
                to_ts(now),
            ),
        )
    return job_id


_NEXT = """
SELECT j.job_id, j.timeout_s FROM jobs j
WHERE j.queue = :queue AND j.state = 'queued' AND j.visible_at <= :now
  AND NOT EXISTS (
    SELECT 1 FROM jobs k
    WHERE k.queue = j.queue AND k.address_id = j.address_id AND k.state IN ('queued', 'claimed')
      AND (k.created_at < j.created_at OR (k.created_at = j.created_at AND k.rowid < j.rowid))
  )
ORDER BY j.visible_at, j.created_at, j.rowid
LIMIT 1
"""
_CLAIM = """
UPDATE jobs SET state = 'claimed', claimed_by = ?, attempts = attempts + 1, claim_expires = ?
WHERE job_id = ? AND state = 'queued'
RETURNING job_id, queue, address_id, payload, attempts, max_attempts, timeout_s
"""


def claim(conn: sqlite3.Connection, clock: Clock, queue: Queue, worker: str) -> Job | None:
    """Claim the next ready job, or None. Expired claims are first returned to the queue.

    Runs inside one BEGIN IMMEDIATE transaction, so the pick and the claim are atomic.
    """
    now_dt = clock.now()
    now = to_ts(now_dt)
    with write_tx(conn):
        conn.execute(
            "UPDATE jobs SET claimed_by = NULL, claim_expires = NULL, "
            "last_error = 'claim expired', "
            "state = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'queued' END "
            "WHERE queue = ? AND state = 'claimed' AND claim_expires < ?",
            (queue.value, now),
        )
        pick = conn.execute(_NEXT, {"queue": queue.value, "now": now}).fetchone()
        if pick is None:
            return None
        expires = to_ts(now_dt + timedelta(seconds=pick["timeout_s"] * CLAIM_FACTOR))
        row = conn.execute(_CLAIM, (worker, expires, pick["job_id"])).fetchone()
    return Job(
        JobId(row["job_id"]),
        Queue(row["queue"]),
        AddressId(row["address_id"]),
        json.loads(row["payload"]),
        row["attempts"],
        row["max_attempts"],
        row["timeout_s"],
    )


def release_claims(conn: sqlite3.Connection) -> int:
    """At service start: jobs a previous process had claimed go back to the queue, runnable now
    (one process in v1, like the leases). Otherwise an address waited for the claim to time out,
    42 minutes for a check (V1.1 review, 2026-09-29). The attempt already counted stays."""
    with write_tx(conn):
        return conn.execute(
            "UPDATE jobs SET state = 'queued', claimed_by = NULL, claim_expires = NULL"
            " WHERE state = 'claimed'"
        ).rowcount


def complete(conn: sqlite3.Connection, job_id: JobId, worker: str) -> None:
    with write_tx(conn):
        _owned(conn, job_id, worker)
        conn.execute(
            "UPDATE jobs SET state = 'done', claim_expires = NULL WHERE job_id = ?", (job_id,)
        )


def fail(conn: sqlite3.Connection, clock: Clock, job_id: JobId, worker: str, error: str) -> str:
    """Record a failure. Returns the new state: 'queued' (retry after backoff) or 'dead'."""
    now = clock.now()
    with write_tx(conn):
        row = _owned(conn, job_id, worker)
        attempts, max_attempts = int(row["attempts"]), int(row["max_attempts"])
        if attempts >= max_attempts:
            state, visible = "dead", to_ts(now)
        else:
            delay = BACKOFF_S[min(attempts, len(BACKOFF_S)) - 1]
            state, visible = "queued", to_ts(now + timedelta(seconds=delay))
        conn.execute(
            "UPDATE jobs SET state = ?, visible_at = ?, claimed_by = NULL, claim_expires = NULL, "
            "last_error = ? WHERE job_id = ?",
            (state, visible, error[:500], job_id),
        )
    return state


def _owned(conn: sqlite3.Connection, job_id: JobId, worker: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT state, claimed_by, attempts, max_attempts FROM jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError(f"no job {job_id[:8]}")
    if row["state"] != "claimed" or row["claimed_by"] != worker:
        raise ConflictError("job is not claimed by this worker")
    return row
