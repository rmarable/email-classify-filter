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
- **Attempts** (OD-236): an item is tried at most once per round and `MAX_ATTEMPTS` times in a row
  (a success resets the count, so a classifier timeout and a later actor timeout aren't added
  up); then it is marked `model_failed`. New mail stays at `new` and shows in "Needs you" marked
  so; mail the actor failed on goes ahead on its rule's plan without the actor. `FAILED_ALERT_AFTER`
  such items in an hour raise a System Error. `ecf item requeue` (or `ecf models install`, for
  all of them) gives them back to the model. A fault of the server itself (not running, missing,
  changed) ends the round without counting an attempt; so does Ollama saying it can't run the
  model now (out of memory, its runner stopped), which opens its own System Error until a call
  succeeds again.
- **Unloading:** when a round leaves nothing waiting, the model is unloaded (`keep_alive: 0`) unless
  `resident` is on or an eval holds the queue.

What a round does with an item is the `Work` it is given: the classifier from V1.3 step 3.
"""

from __future__ import annotations

import itertools
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
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.notify import Notifier
from ecf_server.ollama import Client, Metrics

ROUND_BUDGET_S = 360.0  # OD-028: one global 6-minute budget per round
BACKLOG_BATCH = 100  # §5.3: above this many waiting, approvals go to digests, not one card each
MAX_ATTEMPTS = 2  # OD-236, like the crash quarantine
FAILED_ALERT_AFTER = 5  # model_failed items in an hour before a System Error (OD-236)
ITEM_LEASE_S = 2 * int(ollama.TIMEOUT_S) + 60  # a call and its retry, with a margin
FAILED_ALERT = "model_failures"  # System Error; resolved when the hour is quiet again
SERVER_ALERT = "local_model_server"  # System Error; Ollama can't run the model now, until a call
# succeeds
FALLBACK_BACK = frozenset({"awaiting_claude", "clarified"})

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
    status: str  # done | budget | hot | not_ready | eval | busy | stopped (server: not_ready)
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
        self.since: str | None = None  # when it was taken (timestamp), for the Slack line

    def acquire(self, holder: str, since: str | None = None) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        self.holder, self.since = holder, since
        return True

    def release(self) -> None:
        self.holder = self.since = None
        self._lock.release()

    def held(self) -> bool:
        return self._lock.locked()


EXCLUSIVE = Exclusive()


# what waits for the local model: new mail for the classifier (presets A and B; C's waits for
# Claude), and the actor's items in preset A (a rule continued to it, or you answered its
# question; in B the actor is Claude, V1.4 step 1); in B and C, items the local fallback took
# (`fallback_at`, V1.4 step 8): from the Claude queue, and then for the local actor
_PRESET = "(SELECT preset FROM addresses WHERE address_id = i.address_id)"
_TO_ACTOR = ("(i.status = 'classified' AND i.decision_source = 'rule'"
             " AND json_extract(i.proposal, '$.plan.to_actor') = 1)")  # fmt: skip
WAITING = (f"i.model_failed = 0 AND ((i.status = 'new' AND {_PRESET} IN ('A', 'B'))"
           f" OR ({_PRESET} = 'A' AND (i.status = 'clarified' OR {_TO_ACTOR}))"
           " OR (i.fallback_at IS NOT NULL AND (i.status IN ('awaiting_claude', 'clarified')"
           f" OR {_TO_ACTOR})))")  # fmt: skip


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
    rolling median of recent ones; three slow calls in a row pause model work for `HEAT_PAUSE`,
    at most once per backlog (OD-248): a fanless Mac stays throttled after a short pause, and
    pausing again only slows the backlog (V1.3 load test). The median restarts with each
    backlog (OD-306). Timed-out, truncated or failed calls
    never enter the median, so a run of crafted slow emails can't drag it down or trip the
    pause on its own."""

    def __init__(self) -> None:
        self.speeds: deque[float] = deque(maxlen=WINDOW)
        self.slow_run = 0
        self.paused = False  # this backlog has had its heat pause
        self.on_ac: bool | None = None

    def power(self, on_ac: bool) -> None:
        """A change of power source starts the median again: on battery generation runs at
        about a third of the AC speed (§21.2), which is not heat."""
        if self.on_ac is not None and on_ac != self.on_ac:
            self.speeds.clear()
            self.slow_run = 0
        self.on_ac = on_ac

    def drained(self) -> None:
        """Nothing waits any more: the next backlog may pause for heat again, and its median
        starts from its own first calls (V1.4 step 12, OD-306). Slow calls never enter the
        median, so within a backlog it keeps the speed the Mac started at; carried over, it
        would judge every later backlog against that, and a Mac that settles at a slower
        speed would pause each one."""
        self.paused = False
        self.speeds.clear()
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
                if not self.paused:
                    self.paused = True
                    return True
            return False
        self.slow_run = 0
        self.speeds.append(tps)
        return False


class _Stop(Exception):
    pass


class _Busy(Exception):
    """Ollama answers but can't run the model now (`server`): no attempt counts."""

    def __init__(self, err: ollama.OllamaError) -> None:
        super().__init__(err.cause)
        self.err = err


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
    shadow: Work | None = None  # the local fallback's shadow runs (V1.4 step 8)


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
    shadow: Work | None = None,
) -> RoundReport:
    """`shadow`: the local fallback's shadow runs, taken only when nothing else waits."""
    if exclusive.held():
        return RoundReport("eval", waiting=sum(waiting(conn).values()))
    with _sole_round() as mine:
        if not mine:  # the worker's round, or `ecf check`'s: Ollama runs one at a time
            return RoundReport("busy", waiting=sum(waiting(conn).values()))
        return _round(
            conn,
            clock,
            notifier,
            client,
            work,
            resident=resident,
            budget_s=budget_s,
            exclusive=exclusive,
            check_kw=check_kw,
            stop=stop,
            throttle=throttle,
            shadow=shadow,
        )


ROUND_LOCK = threading.Lock()  # one round at a time; an eval waits for a running one to end


@contextmanager
def _sole_round() -> Generator[bool]:
    mine = ROUND_LOCK.acquire(blocking=False)
    try:
        yield mine
    finally:
        if mine:
            ROUND_LOCK.release()


def _round(  # noqa: PLR0913 - run_round's arguments
    conn: sqlite3.Connection, clock: Clock, notifier: Notifier, client: Client, work: Work, *,
    resident: bool, budget_s: float, exclusive: Exclusive, check_kw: dict[str, Any] | None,
    stop: threading.Event | None, throttle: Throttle | None, shadow: Work | None,
) -> RoundReport:  # fmt: skip
    ready = models.check(conn, clock, notifier, client, **(check_kw or {}))
    if ready is None:
        return RoundReport("not_ready", waiting=sum(waiting(conn).values()))
    r = _Round(conn, clock, notifier, client, ready, work, RoundReport("done"),
               throttle=throttle or Throttle(), shadow=shadow)  # fmt: skip
    try:
        _passes(r, budget_s, stop)
    except _Stop:
        r.report.status = "not_ready"
        models.check(conn, clock, notifier, client, **(check_kw or {}))  # opens the alert
    except _Busy as b:
        r.report.status = "not_ready"
        health.open_alert(conn, clock, notifier, SERVER_ALERT, None, models.fault_text(b.err))
    _resolve_quiet(conn, clock, notifier)
    r.report.waiting = sum(waiting(conn).values())
    idle = r.report.waiting == 0 and not (shadow and shadow_waiting(conn))
    if idle:  # the fallback's shadow runs are model work too: the backlog ends with them
        r.throttle.drained()
    if idle and not resident and not exclusive.held():
        try:
            client.unload(models_tag())
            r.report.unloaded = True
        except ollama.OllamaError as e:
            log.warning("model.unload_failed", cause=e.cause)
    return r.report


def _passes(r: _Round, budget_s: float, stop: threading.Event | None) -> None:
    """Round-robin passes over the addresses until nothing is worked on or the round ends; then,
    when nothing else waits, the local fallback's shadow runs (lowest priority, V1.4 step 8)."""
    started = r.clock.monotonic()
    shadow = False
    while r.report.status == "done":
        progressed = False
        pending = shadow_waiting(r.conn) if shadow else list(waiting(r.conn))
        for address_id in pending:
            if r.clock.monotonic() - started >= budget_s:
                r.report.status = "budget"
                break
            if stop is not None and stop.is_set():  # the service is stopping
                r.report.status = "stopped"
                break
            progressed = _one(r, address_id, shadow=shadow) or progressed
            if r.report.status != "done":  # heat: stop now, not after every address
                break
        if shadow and waiting(r.conn):
            shadow = False  # new mail came in meanwhile: it goes first
        elif not progressed:
            if shadow or r.shadow is None or waiting(r.conn):
                break
            shadow = True


def shadow_waiting(conn: sqlite3.Connection) -> list[str]:
    """Addresses with local-fallback shadow runs waiting."""
    from ecf_server import fallback  # noqa: PLC0415 - fallback imports this module

    return fallback.shadow_addresses(conn)


def _next_shadow(conn: sqlite3.Connection, address_id: str, tried: set[str]) -> sqlite3.Row | None:
    from ecf_server import fallback  # noqa: PLC0415 - fallback imports this module

    return fallback.next_shadow(conn, address_id, tried)


def models_tag() -> str:
    return ollama.load_pin().ecf_tag


def _one(r: _Round, address_id: str, *, shadow: bool = False) -> bool:
    """Work on the address's oldest untried item (or shadow run); True if one was worked on."""
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
            work = r.shadow if shadow and r.shadow is not None else r.work
            item = (_next_shadow if shadow else _next_item)(r.conn, address_id, r.tried)
            if item is None:
                return False
            r.tried.add(item["stable_id"])
            try:
                result = work(r.conn, r.clock, r.client, r.ready, item)
            except ollama.OllamaError as e:
                log.warning("model.item_error", address_id=address_id, cause=e.cause)
                if e.cause == "server":
                    raise _Busy(e) from e  # Ollama can't run the model now: not this item
                if e.cause not in ("timeout", "http"):
                    raise _Stop from e  # the server itself: no attempt counts against the item
                result = ItemResult("failed")
            if shadow:  # a shadow run changes nothing on the item; a failure is kept as such
                r.report.done += result.outcome == "ok"
                r.report.failed += result.outcome == "failed"
            else:
                _account(r.conn, r.clock, r.notifier, item, result, r.report)
            if r.throttle.record(result):
                r.report.status = "hot"  # three slow calls in a row: pause for HEAT_PAUSE
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
        health.resolve_alert(conn, clock, notifier, SERVER_ALERT, None)
        if int(item["model_attempts"]):  # attempts count in a row (a later role starts afresh)
            with write_tx(conn):
                conn.execute("UPDATE items SET model_attempts = 0 WHERE stable_id = ?",
                             (item["stable_id"],))  # fmt: skip
        return
    if result.outcome == "skipped":
        return
    report.failed += 1
    now = to_ts(clock.now())
    attempts = int(item["model_attempts"]) + 1
    failed = attempts >= MAX_ATTEMPTS
    # the local fallback gave up on an item still in the Claude queue: it goes back to Claude
    # (`model_failed` keeps the fallback from taking it again until `ecf item requeue`)
    back = failed and bool(item["fallback_at"]) and item["status"] in FALLBACK_BACK
    # attempts count at awaiting_claude too: an item the local fallback took (V1.4)
    with write_tx(conn):
        conn.execute(
            "UPDATE items SET model_attempts = ?, model_failed = ?, model_failed_at = ?,"
            " updated_at = ?, fallback_at = CASE WHEN ? THEN NULL ELSE fallback_at END"
            " WHERE stable_id = ?"
            " AND status IN ('new', 'classified', 'clarified', 'awaiting_claude')",
            (attempts, int(failed), now if failed else None, now, back, item["stable_id"]),
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
        if item["status"] != "new" and not back:
            _without_actor(conn, clock, item["stable_id"])


def _maybe_alert(conn: sqlite3.Connection, clock: Clock, notifier: Notifier) -> None:
    since = to_ts(clock.now() - timedelta(hours=1))
    n = conn.execute("SELECT count(*) FROM items WHERE model_failed_at >= ?", (since,)).fetchone()[
        0
    ]
    if n >= FAILED_ALERT_AFTER:
        health.open_alert(conn, clock, notifier, FAILED_ALERT, None,
                          f"the local model failed on {n} items in the last hour; they wait in"
                          " Needs you (ecf inbox). Model work goes on for other mail."
                          + _truncated_cause(conn, since))  # fmt: skip


def _truncated_cause(conn: sqlite3.Connection, since: str) -> str:
    """With a schema extension applied, a classifier prompt cut off near the context window
    may be the extension's text (OD-478): say so, and how to shorten it."""
    from ecf_server import config  # noqa: PLC0415 - config imports modules that import this one

    cut = conn.execute("SELECT count(*) FROM model_calls WHERE role = 'classifier' AND"
                       " outcome = 'truncated' AND ts >= ?", (since,)).fetchone()[0]  # fmt: skip
    if not cut or config.current_schema(conn).extension is None:
        return ""
    return (f" {cut} classifier prompts came near the context window; the schema extension adds"
            " prompt text, so shortening its descriptions or removing a field may help"
            " (ecf config apply).")  # fmt: skip


def _resolve_quiet(conn: sqlite3.Connection, clock: Clock, notifier: Notifier) -> None:
    since = to_ts(clock.now() - timedelta(hours=1))
    n = conn.execute("SELECT count(*) FROM items WHERE model_failed_at >= ?", (since,)).fetchone()[
        0
    ]
    if n < FAILED_ALERT_AFTER:
        health.resolve_alert(conn, clock, notifier, FAILED_ALERT, None)


def _without_actor(conn: sqlite3.Connection, clock: Clock, sid: str) -> None:
    """The actor gave up on a classified (or answered) item: its rule's own plan goes ahead
    without the actor, so the rule's labels, flags and escalations aren't lost. The plan is
    re-made from the item (an answered question's actor turn is dropped)."""
    from ecf_server import decide  # noqa: PLC0415 - decide imports this module

    item = conn.execute("SELECT * FROM items WHERE stable_id = ?", (sid,)).fetchone()
    if item is None or item["status"] not in ("classified", "clarified"):
        return
    try:
        _ctx, p = decide.plan_for(conn, item)
        p.to_actor = False
        decide.apply(conn, clock, sid, p, source="rule")
        with write_tx(conn):  # no longer stuck; model_failed_at keeps it in the hour's count
            conn.execute("UPDATE items SET model_failed = 0 WHERE stable_id = ?", (sid,))
    except Exception as exc:  # it stays in Needs you, marked
        log.error("model.without_actor_failed", stable_id=sid[:8], error_type=type(exc).__name__)


def retry(conn: sqlite3.Connection, clock: Clock, sid: str, *, actor: str) -> bool:
    """Give an item the model gave up on back to it (`ecf item requeue`); False if it isn't
    one."""
    now = to_ts(clock.now())
    with write_tx(conn):
        done = conn.execute(
            "UPDATE items SET model_failed = 0, model_attempts = 0, model_failed_at = NULL,"
            " updated_at = ? WHERE stable_id = ? AND model_failed = 1"
            " AND status IN ('new', 'classified', 'clarified', 'awaiting_claude')",
            (now, sid)).rowcount  # fmt: skip
        if done:
            conn.execute(
                "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome)"
                " SELECT ?, address_id, stable_id, 'model.item_retried', ?, 'ok' FROM items"
                " WHERE stable_id = ?", (now, actor, sid))  # fmt: skip
    return bool(done)


def retry_all(conn: sqlite3.Connection, clock: Clock, *, actor: str) -> int:
    """Every item the model gave up on, back to it (after `ecf models install`)."""
    return sum(retry(conn, clock, sid, actor=actor) for sid in failed_items(conn))


def failed_items(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT stable_id FROM items WHERE model_failed = 1"
        " AND status IN ('new', 'classified', 'clarified', 'awaiting_claude')"
        " ORDER BY model_failed_at")]  # fmt: skip


RoundRunner = Callable[[], RoundReport]


# ---------------------------------------------------------------------------- when rounds run

RETRY = timedelta(seconds=60)  # not ready, or an eval holds the queue
CATCH_UP = timedelta(seconds=30)  # §5.3: a round that ran out of budget continues soon
HEAT_PAUSE = timedelta(minutes=3)  # OD-248: a fixed cool-down after heat, not a check interval


class RoundSchedule:
    """When the next round may start (§5.2, §5.3). New mail wakes the worker; a round that used its
    whole budget continues after `CATCH_UP` on AC power (or a desktop); after heat, `HEAT_PAUSE`
    (OD-248); on battery, rounds run at most once per off-hours interval (OD-029)."""

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
    ) -> None:
        now = self.clock.now()
        if report.status in ("not_ready", "eval", "busy"):
            self.next_due = now + RETRY
        elif on_battery:
            self.next_due = now + offhours
        elif report.status == "hot":
            self.next_due = now + HEAT_PAUSE
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

ETA_SAMPLE = 200  # recent successful calls looked at
RUN_GAP = timedelta(minutes=10)  # longer than a heat pause: a gap this long ends a backlog run
RUN_MIN = 5  # emails in the latest run before its pace is trusted
# a gap in a run counts at most this many times the model time per email: a backlog's gaps are
# 3-4 times it (the actor and the work between calls, step 12a), mail arriving every few
# minutes is 50 or more times it
GAP_CAP = 10


def seconds_per_item(conn: sqlite3.Connection) -> float | None:
    """Wall seconds per email for the backlog estimate (§5.3). When recent emails were
    classified in one continuous run (gaps under `RUN_GAP`), that run's measured pace, which
    includes the actor's calls, the work between calls and heat pauses (the step 12a shadow run
    showed the per-call time alone underestimating fivefold). Otherwise the model time per email:
    every role's call time over the emails classified. Each gap in a run counts at most
    `GAP_CAP` times the model time per email, so mail trickling in every few minutes (no backlog)
    isn't taken for slow work."""
    rows = conn.execute("SELECT ts, role, total_ns FROM model_calls WHERE outcome = 'ok' AND"
                        " total_ns IS NOT NULL ORDER BY id DESC LIMIT ?",
                        (ETA_SAMPLE,)).fetchall()  # fmt: skip
    if not rows:
        return None
    stamps = [from_ts(r["ts"]) for r in rows if r["role"] == "classifier"]  # newest first
    emails = len(stamps)
    total = sum(r["total_ns"] for r in rows) / 1e9
    model = total / emails if emails else statistics.median(r["total_ns"] for r in rows) / 1e9
    run = stamps[:1]
    for t in stamps[1:]:
        if run[-1] - t > RUN_GAP:
            break
        run.append(t)
    if len(run) >= RUN_MIN and run[0] > run[-1]:
        gaps = [(a - b).total_seconds() for a, b in itertools.pairwise(run)]
        return sum(min(g, GAP_CAP * model) for g in gaps) / len(gaps)
    return model


def status(conn: sqlite3.Connection, *, laptop: bool, on_ac: bool) -> dict[str, Any]:
    """For `ecf status`: what waits for the local model, an estimate of how long it takes from
    measured speed, and whether it waits for AC power (on battery, model work runs only at the
    off-hours interval, OD-029)."""
    n = sum(waiting(conn).values())
    per = seconds_per_item(conn)
    return {"waiting": n, "eta_s": round(n * per) if per is not None and n else None,
            "on_battery": laptop and not on_ac, "eval": EXCLUSIVE.held()}  # fmt: skip
