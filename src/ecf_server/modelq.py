"""The global model queue (SPEC §5.2; OD-028; V1.3 step 2a).

Ollama is one shared resource, so all local-model work goes through one queue with one budget: a
**round** runs up to `ROUND_BUDGET_S` (6 minutes, fixed; `max_per_check` bounds IMAP and rules work
only, OD-228). Inside a round, work is round-robin by address: one item per address per pass, so a
backlog on one address can't starve the others.

- **Before a round** the model check runs (`models.check`: listener, environment, digest; I6). If it
  fails, nothing runs and the alert stays open. An eval holds the queue exclusively (V1.3 step 8);
  rounds then wait, and the pre-check continues.
- **Per item** the address's in-process lock and its lease are taken, as a check does, so the
  pre-check skips an address while the model works on it (§5.4). A paused address is skipped.
- **Attempts** (OD-236): an item is tried at most once per round and `MAX_ATTEMPTS` times in all;
  then it is marked `model_failed`, stays at `new`, and shows in "Needs you". `FAILED_ALERT_AFTER`
  such items in an hour raise a System Error. A fault of the server itself (not running, missing,
  changed) ends the round without counting an attempt.
- **Unloading:** when a round leaves nothing waiting, the model is unloaded (`keep_alive: 0`) unless
  `resident` is on or an eval holds the queue.

What a round does with an item is the `Work` it is given: the classifier from V1.3 step 3.
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
import subprocess
import sys
import threading
import uuid
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from ecf_server import health, leases, models, ollama
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.notify import Notifier
from ecf_server.ollama import Client, Metrics

ROUND_BUDGET_S = 360.0  # OD-028: one global 6-minute budget per round
MAX_ATTEMPTS = 2  # OD-236, like the crash quarantine
FAILED_ALERT_AFTER = 5  # model_failed items in an hour before a System Error (OD-236)
ITEM_LEASE_S = 2 * int(ollama.TIMEOUT_S) + 60  # a call and its retry, with a margin
FAILED_ALERT = "model_failures"  # System Error; resolved when the hour is quiet again

Outcome = Literal["ok", "failed", "skipped"]


@dataclass(frozen=True)
class ItemResult:
    outcome: Outcome  # ok: done with this item; failed: counts an attempt; skipped: no attempt
    metrics: Metrics | None = None


class Work(Protocol):
    def __call__(
        self, conn: sqlite3.Connection, clock: Clock, client: Client, ready: ollama.Ready,
        item: sqlite3.Row,
    ) -> ItemResult: ...  # fmt: skip


@dataclass
class RoundReport:
    status: str  # done | budget | hot | not_ready | eval | stopped
    done: int = 0
    failed: int = 0
    marked_failed: int = 0
    busy: int = 0
    unloaded: bool = False
    waiting: int = 0
    per_address: dict[str, int] = field(default_factory=dict[str, int])


class Exclusive:
    """Held by an eval run (V1.3 step 8): rounds don't start while it's held."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.holder: str | None = None

    def acquire(self, holder: str) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        self.holder = holder
        return True

    def release(self) -> None:
        self.holder = None
        self._lock.release()

    def held(self) -> bool:
        return self._lock.locked()


EXCLUSIVE = Exclusive()


# what waits for the local model: new mail for the classifier, and the actor's items (a rule
# continued to it, or you answered its question)
WAITING = ("i.model_failed = 0 AND (i.status = 'new' OR i.status = 'clarified'"
           " OR (i.status = 'classified' AND i.decision_source = 'rule'"
           " AND json_extract(i.proposal, '$.plan.to_actor') = 1))")  # fmt: skip


def waiting(conn: sqlite3.Connection) -> dict[str, int]:
    """Items waiting for the local model, per address (not paused, not removed)."""
    rows = conn.execute(
        "SELECT i.address_id, count(*) FROM items i JOIN addresses a USING (address_id)"  # noqa: S608 - WAITING is a constant
        f" WHERE {WAITING} AND a.removed_at IS NULL AND a.paused = 0"
        " GROUP BY i.address_id ORDER BY i.address_id"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _next_item(conn: sqlite3.Connection, address_id: str, tried: set[str]) -> sqlite3.Row | None:
    marks = ",".join("?" * len(tried))
    skip = f" AND stable_id NOT IN ({marks})" if tried else ""
    row: sqlite3.Row | None = conn.execute(
        f"SELECT * FROM items i WHERE address_id = ? AND {WAITING}"  # noqa: S608 - constants
        + skip
        + " ORDER BY created_at, stable_id LIMIT 1",
        (address_id, *sorted(tried)),
    ).fetchone()
    return row


def _holder() -> str:
    return f"model-{os.getpid()}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------- heat and power

SLOW_FACTOR = 0.7  # a call more than 30% below the rolling median is slow (OD-029)
SLOW_IN_A_ROW = 3  # amended by OD-243: three slow calls in a row, not one
WINDOW = 20
MIN_WINDOW = 5  # no judgement until this many normal calls are known


class Throttle:
    """Heat (§5.2, OD-029 as amended by OD-243): generation speed of each normal call against a
    rolling median of recent ones; three slow calls in a row pause model work until the next
    interval. Timed-out, truncated or failed calls never enter the median, so a run of crafted
    slow emails can't drag it down or trip the pause on its own."""

    def __init__(self) -> None:
        self.speeds: deque[float] = deque(maxlen=WINDOW)
        self.slow_run = 0

    def median(self) -> float | None:
        if len(self.speeds) < MIN_WINDOW:
            return None
        return statistics.median(self.speeds)

    def record(self, result: ItemResult) -> bool:
        """Count one call; True when model work should pause for heat."""
        if result.outcome != "ok" or result.metrics is None:
            return False
        tps = result.metrics.generation_tps
        if tps is None:
            return False
        med = self.median()
        if med is not None and tps < SLOW_FACTOR * med:
            self.slow_run += 1
            if self.slow_run >= SLOW_IN_A_ROW:
                self.slow_run = 0
                return True
            return False
        self.slow_run = 0
        self.speeds.append(tps)
        return False


class _Stop(Exception):
    pass


@dataclass
class _Round:
    conn: sqlite3.Connection
    clock: Clock
    notifier: Notifier
    client: Client
    ready: ollama.Ready
    work: Work
    report: RoundReport
    holder: str = field(default_factory=_holder)
    tried: set[str] = field(default_factory=set[str])  # each item at most once per round
    throttle: Throttle = field(default_factory=Throttle)


def run_round(  # noqa: PLR0913 - keyword-only options after the collaborators
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    client: Client,
    work: Work,
    *,
    resident: bool = False,
    budget_s: float = ROUND_BUDGET_S,
    exclusive: Exclusive = EXCLUSIVE,
    check_kw: dict[str, Any] | None = None,
    stop: threading.Event | None = None,
    throttle: Throttle | None = None,
) -> RoundReport:
    if exclusive.held():
        return RoundReport("eval", waiting=sum(waiting(conn).values()))
    ready = models.check(conn, clock, notifier, client, **(check_kw or {}))
    if ready is None:
        return RoundReport("not_ready", waiting=sum(waiting(conn).values()))
    started = clock.monotonic()
    r = _Round(conn, clock, notifier, client, ready, work, RoundReport("done"),
               throttle=throttle or Throttle())  # fmt: skip
    try:
        while r.report.status == "done":
            progressed = False
            for address_id in waiting(conn):
                if clock.monotonic() - started >= budget_s:
                    r.report.status = "budget"
                    break
                if stop is not None and stop.is_set():  # the service is stopping
                    r.report.status = "stopped"
                    break
                progressed = _one(r, address_id) or progressed
            if not progressed:
                break
    except _Stop:
        r.report.status = "not_ready"
        models.check(conn, clock, notifier, client, **(check_kw or {}))  # opens the alert
    _resolve_quiet(conn, clock, notifier)
    r.report.waiting = sum(waiting(conn).values())
    if r.report.waiting == 0 and not resident and not exclusive.held():
        try:
            client.unload(models_tag())
            r.report.unloaded = True
        except ollama.OllamaError as e:
            log.warning("model.unload_failed", cause=e.cause)
    return r.report


def models_tag() -> str:
    return ollama.load_pin().ecf_tag


def _one(r: _Round, address_id: str) -> bool:
    """Work on the address's oldest untried item; True if an item was worked on."""
    lock = leases.local_lock(address_id)
    if not lock.acquire(blocking=False):
        r.report.busy += 1
        return False
    try:
        lease = leases.acquire(r.conn, r.clock, address_id, r.holder, ttl_s=ITEM_LEASE_S)
        if lease is None:
            r.report.busy += 1
            return False
        try:
            item = _next_item(r.conn, address_id, r.tried)
            if item is None:
                return False
            r.tried.add(item["stable_id"])
            try:
                result = r.work(r.conn, r.clock, r.client, r.ready, item)
            except ollama.OllamaError as e:
                log.warning("model.item_error", address_id=address_id, cause=e.cause)
                if e.cause not in ("timeout", "http"):
                    raise _Stop from e  # the server itself: no attempt counts against the item
                result = ItemResult("failed")
            _account(r.conn, r.clock, r.notifier, item, result, r.report)
            if r.throttle.record(result):
                r.report.status = "hot"  # three slow calls in a row: pause until the next interval
            r.report.per_address[address_id] = r.report.per_address.get(address_id, 0) + 1
            return True
        finally:
            leases.release(r.conn, lease)
    finally:
        lock.release()


def _account(
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    item: sqlite3.Row,
    result: ItemResult,
    report: RoundReport,
) -> None:
    if result.outcome == "ok":
        report.done += 1
        return
    if result.outcome == "skipped":
        return
    report.failed += 1
    now = to_ts(clock.now())
    attempts = int(item["model_attempts"]) + 1
    failed = attempts >= MAX_ATTEMPTS
    with write_tx(conn):
        conn.execute(
            "UPDATE items SET model_attempts = ?, model_failed = ?, model_failed_at = ?,"
            " updated_at = ? WHERE stable_id = ? AND status IN ('new', 'classified', 'clarified')",
            (attempts, int(failed), now if failed else None, now, item["stable_id"]),
        )
        if failed:
            conn.execute(
                "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
                " VALUES (?, ?, 'model.item_failed', 'service', 'error',"
                " json_object('stable_id', ?, 'attempts', ?))",
                (now, item["address_id"], item["stable_id"], attempts),
            )
    if failed:
        report.marked_failed += 1
        _maybe_alert(conn, clock, notifier)


def _maybe_alert(conn: sqlite3.Connection, clock: Clock, notifier: Notifier) -> None:
    since = to_ts(clock.now() - timedelta(hours=1))
    n = conn.execute(
        "SELECT count(*) FROM items WHERE model_failed = 1 AND model_failed_at >= ?", (since,)
    ).fetchone()[0]
    if n >= FAILED_ALERT_AFTER:
        health.open_alert(conn, clock, notifier, FAILED_ALERT, None,
                          f"the local model failed on {n} items in the last hour; they wait in"
                          " Needs you (ecf inbox). Model work goes on for other mail.")  # fmt: skip


def _resolve_quiet(conn: sqlite3.Connection, clock: Clock, notifier: Notifier) -> None:
    since = to_ts(clock.now() - timedelta(hours=1))
    n = conn.execute(
        "SELECT count(*) FROM items WHERE model_failed = 1 AND model_failed_at >= ?", (since,)
    ).fetchone()[0]
    if n < FAILED_ALERT_AFTER:
        health.resolve_alert(conn, clock, notifier, FAILED_ALERT, None)


def failed_items(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT stable_id FROM items WHERE model_failed = 1"
        " AND status IN ('new', 'classified', 'clarified')"
        " ORDER BY model_failed_at")]  # fmt: skip


RoundRunner = Callable[[], RoundReport]


# ---------------------------------------------------------------------------- when rounds run

RETRY = timedelta(seconds=60)  # not ready, or an eval holds the queue
CATCH_UP = timedelta(seconds=30)  # §5.3: a round that ran out of budget continues soon


class RoundSchedule:
    """When the next round may start (§5.2, §5.3). New mail wakes the worker; a round that used its
    whole budget continues after `CATCH_UP` on AC power (or a desktop); on battery, rounds run at
    most once per off-hours interval (OD-029)."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.next_due: datetime | None = None  # None: as soon as there is work

    def due(self) -> bool:
        return self.next_due is None or self.clock.now() >= self.next_due

    def after(
        self,
        report: RoundReport,
        *,
        on_battery: bool,
        offhours: timedelta,
        interval: timedelta | None = None,
    ) -> None:
        """`interval`: the current check interval, for a pause after heat (else off-hours)."""
        interval = interval or offhours
        now = self.clock.now()
        if report.status in ("not_ready", "eval"):
            self.next_due = now + RETRY
        elif on_battery:
            self.next_due = now + offhours
        elif report.status == "hot":
            self.next_due = now + interval
        elif report.status == "budget":
            self.next_due = now + CATCH_UP
        else:
            self.next_due = None


def resident(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key = 'resident'").fetchone()
    return bool(row and json.loads(row["value"]) is True)


@contextmanager
def awake(on_ac: bool, platform: str = sys.platform) -> Generator[None]:
    """On AC power, hold a PreventUserIdleSystemSleep assertion for the round (§5.2, OD-029):
    `caffeinate -i` on macOS, tied to this process. Never on battery. Other platforms: none."""
    proc: subprocess.Popen[bytes] | None = None
    if on_ac and platform == "darwin" and os.path.exists(CAFFEINATE):
        try:
            proc = subprocess.Popen(  # noqa: S603 - fixed path and arguments
                [CAFFEINATE, "-i", "-w", str(os.getpid())],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )  # fmt: skip
        except OSError as e:
            log.warning("model.awake_failed", error_type=type(e).__name__)
    try:
        yield
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


CAFFEINATE = "/usr/bin/caffeinate"


# ---------------------------------------------------------------------------- status (§5.3)

ETA_SAMPLE = 50


def seconds_per_item(conn: sqlite3.Connection) -> float | None:
    """The median wall time of recent successful model calls, from `model_calls`."""
    rows = conn.execute("SELECT total_ns FROM model_calls WHERE outcome = 'ok' AND total_ns IS"
                        " NOT NULL ORDER BY id DESC LIMIT ?", (ETA_SAMPLE,)).fetchall()  # fmt: skip
    if not rows:
        return None
    return statistics.median(r[0] for r in rows) / 1e9


def status(conn: sqlite3.Connection, *, laptop: bool, on_ac: bool) -> dict[str, Any]:
    """For `ecf status`: what waits for the local model, an estimate of how long it takes from
    measured speed, and whether it waits for AC power (on battery, model work runs only at the
    off-hours interval, OD-029)."""
    n = sum(waiting(conn).values())
    per = seconds_per_item(conn)
    return {"waiting": n, "eta_s": round(n * per) if per is not None and n else None,
            "on_battery": laptop and not on_ac, "eval": EXCLUSIVE.held()}  # fmt: skip
