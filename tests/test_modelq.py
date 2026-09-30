"""The global model queue (V1.3 step 2a; SPEC §5.2, OD-028, OD-236): readiness first, round-robin
by address, the 6-minute budget, one try per item per round, attempt cap and `model_failed`,
leases and pause, unloading, eval exclusivity, round scheduling."""

from __future__ import annotations

import sqlite3
import subprocess
import threading
from datetime import timedelta
from typing import Any

import pytest

from ecf.ids import AddressId, StableId
from ecf_server import inbox, items, leases, modelq, ollama
from ecf_server.clock import Clock, FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.modelq import ItemResult, RoundSchedule
from ecf_server.notify import FakeNotifier
from ecf_server.ollama import Client, OllamaError
from ecf_server.state_machine import Status, TransitionContext
from tests.test_models import FakeOllama, check_kw


class RecordingOllama(FakeOllama):
    def __init__(self) -> None:
        super().__init__()
        self.unloads: list[str] = []

    def handler(self, req: Any) -> Any:
        import httpx  # noqa: PLC0415

        if req.url.path == "/api/generate":
            self.unloads.append(req.content.decode())
            return httpx.Response(200, json={"done": True})
        return super().handler(req)


def _address(conn: sqlite3.Connection, clock: FakeClock, aid: str, *, paused: bool = False) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at,"
                     " paused) VALUES (?, ?, 'standard', 'A', ?, ?)",
                     (aid, f"{aid}@acme.example", to_ts(clock.now()), int(paused)))  # fmt: skip


def _items(conn: sqlite3.Connection, clock: FakeClock, aid: str, n: int) -> list[str]:
    ids: list[str] = []
    for i in range(n):
        sid = f"{aid}-{i:02d}".ljust(64, "0")
        items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId(aid),
                          content_hash="h", facts="{}", subject="s",
                          sender="billing@vendor-a.example")  # fmt: skip
        clock.advance(1)
        ids.append(sid)
    return ids


class Work:
    """Records the items it's given; `outcome` per call; `seconds` of model time per call."""

    def __init__(
        self, outcome: str = "ok", seconds: float = 1.0, raise_: OllamaError | None = None
    ):
        self.seen: list[str] = []
        self.outcome, self.seconds, self.raise_ = outcome, seconds, raise_

    def __call__(self, conn: sqlite3.Connection, clock: Clock, client: Client,
                 ready: ollama.Ready, item: sqlite3.Row) -> ItemResult:  # fmt: skip
        self.seen.append(item["stable_id"][:5])
        assert isinstance(clock, FakeClock)
        clock.advance(self.seconds)
        if self.raise_ is not None:
            raise self.raise_
        if self.outcome == "ok":
            items.transition(conn, clock, StableId(item["stable_id"]), Status.CLASSIFIED,
                             TransitionContext(), actor="model")  # fmt: skip
        return ItemResult(self.outcome)  # type: ignore[arg-type]


def _round(conn: sqlite3.Connection, clock: FakeClock, work: Work, **kw: Any) -> modelq.RoundReport:
    fake = kw.pop("fake", None) or RecordingOllama()
    return modelq.run_round(conn, clock, kw.pop("notifier", FakeNotifier()), fake.client(), work,
                            check_kw=check_kw(**kw.pop("check", {})), **kw)  # fmt: skip


@pytest.fixture(autouse=True)
def _free_exclusive() -> None:
    if modelq.EXCLUSIVE.held():
        modelq.EXCLUSIVE.release()


def test_round_robin_by_address_until_nothing_waits(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    for aid in ("ap", "hr"):
        _address(conn, clock, aid)
    _items(conn, clock, "ap", 3)
    _items(conn, clock, "hr", 2)
    fake = RecordingOllama()
    work = Work()
    report = _round(conn, clock, work, fake=fake)
    assert work.seen == ["ap-00", "hr-00", "ap-01", "hr-01", "ap-02"]
    assert (report.status, report.done, report.waiting) == ("done", 5, 0)
    assert report.unloaded and len(fake.unloads) == 1 and '"keep_alive":0' in fake.unloads[0]


def test_resident_keeps_the_model_loaded(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock, "ap")
    _items(conn, clock, "ap", 1)
    fake = RecordingOllama()
    assert not _round(conn, clock, Work(), fake=fake, resident=True).unloaded
    assert fake.unloads == []


def test_the_budget_ends_a_round(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock, "ap")
    _items(conn, clock, "ap", 10)
    report = _round(conn, clock, Work(seconds=100), budget_s=360)
    assert report.status == "budget" and report.done == 4 and report.waiting == 6
    assert not report.unloaded  # work is still waiting


def test_not_ready_runs_nothing(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock, "ap")
    _items(conn, clock, "ap", 2)
    work = Work()
    nobody = subprocess.CalledProcessError(1, ["lsof"], output="")
    report = _round(conn, clock, work, check={"lsof": nobody})
    assert report.status == "not_ready" and work.seen == [] and report.waiting == 2


def test_failures_count_once_per_round_then_mark_model_failed(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, "ap")
    [sid] = _items(conn, clock, "ap", 1)
    work = Work(outcome="failed")
    r1 = _round(conn, clock, work)
    assert (r1.failed, r1.marked_failed, r1.waiting) == (1, 0, 1)  # tried once, not looped
    r2 = _round(conn, clock, work)
    assert (r2.failed, r2.marked_failed, r2.waiting) == (1, 1, 0)
    row = conn.execute("SELECT status, model_failed, model_attempts FROM items").fetchone()
    assert tuple(row) == ("new", 1, 2)
    assert [i["id"] for i in inbox.inbox(conn)] == [sid]  # waits on you now
    assert inbox.inbox(conn)[0]["model_failed"] is True
    assert work.seen == ["ap-00", "ap-00"]


def test_many_failures_in_an_hour_raise_a_system_error_that_resolves(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, "ap")
    _items(conn, clock, "ap", modelq.FAILED_ALERT_AFTER)
    n = FakeNotifier()
    for _ in range(modelq.MAX_ATTEMPTS):
        _round(conn, clock, Work(outcome="failed"), notifier=n)
    kinds = [r[0] for r in conn.execute("SELECT kind FROM alerts WHERE resolved_at IS NULL")]
    assert kinds == ["model_failures"]
    assert any("failed on 5 items" in body for _t, body in n.sent)
    clock.advance(3700)
    _address(conn, clock, "hr")
    _items(conn, clock, "hr", 1)
    _round(conn, clock, Work(), notifier=n)
    assert conn.execute("SELECT count(*) FROM alerts WHERE resolved_at IS NULL").fetchone()[0] == 0


def test_a_server_fault_mid_round_stops_without_counting_an_attempt(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, "ap")
    _items(conn, clock, "ap", 3)
    work = Work(raise_=OllamaError("not_running", "gone"))
    report = _round(conn, clock, work)
    assert report.status == "not_ready" and work.seen == ["ap-00"]
    assert conn.execute("SELECT max(model_attempts) FROM items").fetchone()[0] == 0


def test_a_timeout_counts_as_an_attempt(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock, "ap")
    _items(conn, clock, "ap", 1)
    report = _round(conn, clock, Work(raise_=OllamaError("timeout")))
    assert report.failed == 1
    assert conn.execute("SELECT model_attempts FROM items").fetchone()[0] == 1


def test_paused_addresses_and_busy_leases_are_skipped(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, "ap", paused=True)
    _address(conn, clock, "hr")
    _address(conn, clock, "it")
    for aid in ("ap", "hr", "it"):
        _items(conn, clock, aid, 1)
    assert leases.acquire(conn, clock, "it", "a-check") is not None  # a fetch check holds it
    work = Work()
    report = _round(conn, clock, work)
    assert work.seen == ["hr-00"] and report.busy >= 1
    assert modelq.waiting(conn) == {"it": 1}  # paused addresses aren't counted as waiting


def test_an_eval_holds_the_queue(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock, "ap")
    _items(conn, clock, "ap", 1)
    assert modelq.EXCLUSIVE.acquire("eval-1")
    work = Work()
    assert _round(conn, clock, work).status == "eval" and work.seen == []
    modelq.EXCLUSIVE.release()
    assert _round(conn, clock, work).done == 1


def test_a_stopping_service_ends_the_round(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock, "ap")
    _items(conn, clock, "ap", 3)
    stop = threading.Event()
    stop.set()
    assert _round(conn, clock, Work(), stop=stop).status == "stopped"


@pytest.mark.parametrize(
    ("status", "battery", "due_in"),
    [
        ("done", False, None),
        ("budget", False, 30),
        ("budget", True, 1800),
        ("done", True, 1800),
        ("not_ready", False, 60),
        ("eval", True, 60),
    ],
)
def test_when_the_next_round_is_due(
    clock: FakeClock, status: str, battery: bool, due_in: int | None
) -> None:
    s = RoundSchedule(clock)
    s.after(modelq.RoundReport(status), on_battery=battery, offhours=timedelta(minutes=30))
    if due_in is None:
        assert s.next_due is None and s.due()
    else:
        assert s.next_due == clock.now() + timedelta(seconds=due_in) and not s.due()
        clock.advance(due_in)
        assert s.due()
