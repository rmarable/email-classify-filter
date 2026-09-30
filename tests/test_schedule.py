"""The scheduler: business hours, intervals, catch-up, sleep, and the service's checks worker
(V1.1 step 11b)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ecf.paths import Paths
from ecf_server import schedule
from ecf_server.checks import CheckReport
from ecf_server.clock import FakeClock, from_ts, to_ts
from ecf_server.schedule import Power, Scheduler
from ecf_server.service import Service
from tests.test_checks import Box, secrets
from tests.test_precheck import BEC

AC_LAPTOP = Power(laptop=True, on_ac=True)
BATTERY = Power(laptop=True, on_ac=False)
DESKTOP = Power(laptop=False, on_ac=True)
BH = schedule.DEFAULTS["business_hours"]


@pytest.fixture
def conn_ap(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.execute(
        "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
        " VALUES ('ap', 'ap@acme.example', 'high', 'A', 'now')"
    )
    conn.execute(
        "INSERT INTO probe (address_id, host, probed_at) VALUES ('ap', 'imap.acme.example', 'now')"
    )
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by)"
        " VALUES ('org_domains', '[\"acme.example\"]', 'now', 'test')"
    )
    return conn


def report(status: str = "ok", remaining: int = 0) -> CheckReport:
    return CheckReport("ap", status, "t", remaining=remaining)


def due_in(conn: sqlite3.Connection, clock: FakeClock, r: CheckReport, power: Power) -> timedelta:
    return schedule.after_check(conn, clock, r, power) - clock.now()


# ---- business hours and intervals -----------------------------------------------------------


@pytest.mark.parametrize(
    ("when", "inside"),
    [
        (datetime(2026, 10, 1, 12, 0, tzinfo=UTC), True),  # Thu 08:00 New York (EDT)
        (datetime(2026, 10, 1, 20, 59, tzinfo=UTC), True),  # Thu 16:59
        (datetime(2026, 10, 1, 21, 0, tzinfo=UTC), False),  # Thu 17:00
        (datetime(2026, 10, 3, 15, 0, tzinfo=UTC), False),  # Saturday
        (datetime(2026, 12, 1, 13, 30, tzinfo=UTC), True),  # Tue 08:30 EST (winter time)
        (datetime(2026, 12, 1, 12, 30, tzinfo=UTC), False),  # Tue 07:30 EST
    ],
)
def test_business_hours(when: datetime, inside: bool) -> None:
    assert schedule.in_business_hours(when, BH) is inside


def test_intervals_settings_and_overrides(conn_ap: sqlite3.Connection, clock: FakeClock) -> None:
    assert due_in(conn_ap, clock, report(), DESKTOP) == timedelta(minutes=10)  # business hours
    clock.advance(10 * 3600)  # 18:00 New York
    assert due_in(conn_ap, clock, report(), DESKTOP) == timedelta(minutes=30)
    conn_ap.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by)"
        " VALUES ('mail_fetch_interval_offhours', '60', 'now', 't')"
    )
    assert due_in(conn_ap, clock, report(), DESKTOP) == timedelta(minutes=60)
    conn_ap.execute(
        "UPDATE addresses SET overrides = ?", (json.dumps({"mail_fetch_interval_offhours": 500}),)
    )
    assert due_in(conn_ap, clock, report(), DESKTOP) == timedelta(minutes=120)  # clamped (OD-033)


# ---- catch-up -------------------------------------------------------------------------------


def test_catch_up_runs_then_cools_down(conn_ap: sqlite3.Connection, clock: FakeClock) -> None:
    waiting = report(remaining=40)
    assert due_in(conn_ap, clock, waiting, DESKTOP) == timedelta(seconds=30)
    clock.advance(59 * 60)
    assert due_in(conn_ap, clock, waiting, DESKTOP) == timedelta(seconds=30)  # 60-min cap
    clock.advance(60)
    assert due_in(conn_ap, clock, waiting, DESKTOP) == timedelta(minutes=10)  # cap reached
    clock.advance(5 * 60)
    assert due_in(conn_ap, clock, waiting, DESKTOP) == timedelta(minutes=10)  # cooling down
    clock.advance(11 * 60)
    assert due_in(conn_ap, clock, waiting, DESKTOP) == timedelta(seconds=30)  # cooldown over


def test_catch_up_on_battery_and_when_off(conn_ap: sqlite3.Connection, clock: FakeClock) -> None:
    waiting = report(remaining=5)
    assert due_in(conn_ap, clock, waiting, BATTERY) == timedelta(minutes=10)
    conn_ap.execute(
        "UPDATE addresses SET overrides = ?", (json.dumps({"catch_up_on_battery": True}),)
    )
    assert due_in(conn_ap, clock, waiting, BATTERY) == timedelta(seconds=30)
    conn_ap.execute("UPDATE addresses SET overrides = ?", (json.dumps({"catch_up": "off"}),))
    assert due_in(conn_ap, clock, waiting, AC_LAPTOP) == timedelta(minutes=10)


def test_catch_up_ends_when_the_backlog_does(conn_ap: sqlite3.Connection, clock: FakeClock) -> None:
    due_in(conn_ap, clock, report(remaining=5), DESKTOP)
    due_in(conn_ap, clock, report(remaining=0), DESKTOP)
    assert conn_ap.execute("SELECT catch_up_since FROM check_state").fetchone()[0] is None


def test_busy_retries_in_a_minute(conn_ap: sqlite3.Connection, clock: FakeClock) -> None:
    assert due_in(conn_ap, clock, report("busy"), DESKTOP) == timedelta(seconds=60)


def test_mac_laptops_get_the_shorter_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schedule.sys, "platform", "darwin")
    assert schedule.default_catch_up_max(AC_LAPTOP) == 30
    assert schedule.default_catch_up_max(DESKTOP) == 60
    monkeypatch.setattr(schedule.sys, "platform", "linux")
    assert schedule.default_catch_up_max(AC_LAPTOP) == 60


# ---- the tick -------------------------------------------------------------------------------


def queued(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT count(*) FROM jobs WHERE state IN ('queued', 'claimed')"
    ).fetchone()[0]


def test_tick_enqueues_due_addresses_once(conn_ap: sqlite3.Connection, clock: FakeClock) -> None:
    s = Scheduler(clock, lambda: DESKTOP)
    assert s.tick(conn_ap) == ["ap"]  # never checked: due now
    assert s.tick(conn_ap) == [] and queued(conn_ap) == 1  # already queued
    conn_ap.execute("UPDATE jobs SET state = 'done'")
    schedule.after_check(conn_ap, clock, report(), DESKTOP)
    assert s.tick(conn_ap) == []  # due in 10 minutes
    clock.advance(10 * 60)
    assert s.tick(conn_ap) == ["ap"]


def test_waking_from_sleep_makes_everything_due(
    conn_ap: sqlite3.Connection, clock: FakeClock
) -> None:
    s = Scheduler(clock, lambda: DESKTOP)
    s.tick(conn_ap)
    conn_ap.execute("UPDATE jobs SET state = 'done'")
    schedule.after_check(conn_ap, clock, report(), DESKTOP)
    clock.advance(60)
    assert s.tick(conn_ap) == []
    clock.sleep(5 * 60)  # 5 minutes asleep: still before the 10-minute interval
    assert s.tick(conn_ap) == ["ap"]


# ---- the service's worker -------------------------------------------------------------------


def test_service_tick_and_worker_run_a_check(
    conn_ap: sqlite3.Connection, db_path: Path, tmp_path: Path, clock: FakeClock
) -> None:
    box = Box()
    svc = Service(Paths(install="t", root=tmp_path), clock)
    svc.scheduler = Scheduler(clock, lambda: DESKTOP)
    svc.state.db_path, svc.state.secrets, svc.state.mail_factory = db_path, secrets(), box.factory
    svc.tick()
    assert svc.work.is_set()
    assert svc._one_check(conn_ap)  # pyright: ignore[reportPrivateUsage]
    assert not svc._one_check(conn_ap)  # pyright: ignore[reportPrivateUsage]  # queue empty
    row = conn_ap.execute("SELECT last_status, next_due_at FROM check_state").fetchone()
    assert row["last_status"] == "first_run"
    assert from_ts(row["next_due_at"]) - clock.now() == timedelta(minutes=10)
    box.fake.deliver(BEC)
    clock.advance(10 * 60)
    svc.tick()
    svc._one_check(conn_ap)  # pyright: ignore[reportPrivateUsage]
    assert conn_ap.execute("SELECT count(*) FROM items").fetchone()[0] == 1
    assert (
        conn_ap.execute("SELECT state FROM jobs ORDER BY created_at DESC").fetchone()[0] == "done"
    )


def test_a_crashing_check_is_recorded_and_rescheduled(
    conn_ap: sqlite3.Connection, db_path: Path, tmp_path: Path, clock: FakeClock
) -> None:
    """A bug no longer skips the bookkeeping: the check is recorded as `internal_error` (type
    only), shown by `ecf status`, and the next one is scheduled (V1.1 review, 2026-09-29)."""

    def broken(_h: str, _u: str, _p: object) -> object:
        raise RuntimeError("bug")

    svc = Service(Paths(install="t", root=tmp_path), clock)
    svc.scheduler = Scheduler(clock, lambda: DESKTOP)
    svc.state.db_path, svc.state.secrets = db_path, secrets()
    svc.state.mail_factory = broken  # pyright: ignore[reportAttributeAccessIssue]
    svc.tick()
    assert svc._one_check(conn_ap)  # pyright: ignore[reportPrivateUsage]
    assert conn_ap.execute("SELECT state FROM jobs").fetchone()["state"] == "done"
    state = conn_ap.execute(
        "SELECT last_status, last_error, next_due_at FROM check_state"
    ).fetchone()
    assert state["last_status"] == "internal_error"
    assert state["last_error"] == "internal error (RuntimeError)"
    assert state["next_due_at"] > to_ts(clock.now())


def test_a_due_time_set_during_the_check_is_kept(
    conn_ap: sqlite3.Connection, clock: FakeClock
) -> None:
    """An Undo click during a check used to wait a whole interval (V1.2 review, 2026-09-30)."""
    started = to_ts(clock.now())
    clock.advance(20)
    asked = clock.now()
    with schedule.write_tx(conn_ap):  # what an Undo click does
        conn_ap.execute("INSERT INTO check_state (address_id, next_due_at) VALUES ('ap', ?)",
                        (to_ts(asked),))  # fmt: skip
    clock.advance(10)
    due = schedule.after_check(conn_ap, clock, CheckReport("ap", "ok", started), DESKTOP)
    assert due == asked
