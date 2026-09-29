"""Mail-health alerts, desktop notifications, `ecf address retry` and doctor (V1.1 step 13b)."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from ecf import doctor
from ecf.doctor import Level
from ecf.paths import Paths
from ecf_server import addresses, health, notify, schedule, service
from ecf_server.api import ServiceState, create_app
from ecf_server.checks import CheckReport
from ecf_server.clock import FakeClock, from_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier, NullNotifier
from ecf_server.service import Service
from tests.test_addresses import GOOD, Mail
from tests.test_schedule import DESKTOP


@pytest.fixture
def ap(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.execute(
        "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
        " VALUES ('ap', 'ap@acme.example', 'high', 'A', 'now')"
    )
    conn.execute(
        "INSERT INTO probe (address_id, host, probed_at) VALUES ('ap', 'imap.acme.example', 'now')"
    )
    with write_tx(conn):
        conn.execute("INSERT INTO check_state (address_id) VALUES ('ap')")
    return conn


def after(
    conn: sqlite3.Connection, clock: FakeClock, n: FakeNotifier, status: str, *, up: bool = True
) -> None:
    health.after_check(conn, clock, n, CheckReport("ap", status, "t"), resolve=lambda _h: up)


# ---- alerts ---------------------------------------------------------------------------------


def test_unreachable_after_15_minutes_while_the_network_is_up(
    ap: sqlite3.Connection, clock: FakeClock
) -> None:
    n = FakeNotifier()
    after(ap, clock, n, "error")
    clock.advance(14 * 60)
    after(ap, clock, n, "error")
    assert n.sent == [] and health.open_alerts(ap) == []
    clock.advance(60)
    after(ap, clock, n, "error", up=False)  # network down: no alert
    assert n.sent == []
    after(ap, clock, n, "error")
    assert n.sent == [
        (
            "ecf: Mail Provider Unreachable",
            "ap: can't reach imap.acme.example for 15 minutes while the network is up",
        )
    ]
    clock.advance(600)
    after(ap, clock, n, "error")
    assert len(n.sent) == 1  # notified once
    (alert,) = health.open_alerts(ap)
    assert alert["title"] == "Mail Provider Unreachable" and "25 minutes" in alert["detail"]
    after(ap, clock, n, "ok")
    assert n.sent[-1] == ("ecf: Resolved: Mail Provider Unreachable", "ap: working again")
    assert health.open_alerts(ap) == []
    events = [r["event"] for r in ap.execute("SELECT event FROM audit ORDER BY id")]
    assert events == ["alert.opened", "alert.resolved"]


def test_busy_changes_nothing(ap: sqlite3.Connection, clock: FakeClock) -> None:
    after(ap, clock, FakeNotifier(), "error")
    since = ap.execute("SELECT failing_since FROM check_state").fetchone()[0]
    clock.advance(60)
    after(ap, clock, FakeNotifier(), "busy")
    assert ap.execute("SELECT failing_since FROM check_state").fetchone()[0] == since


def test_login_rejected_three_times_then_hourly(ap: sqlite3.Connection, clock: FakeClock) -> None:
    n = FakeNotifier()
    for _ in range(2):
        after(ap, clock, n, "login_rejected")
    assert n.sent == [] and not health.login_backoff(ap, "ap")
    after(ap, clock, n, "login_rejected")
    assert n.sent[0][0] == "ecf: Mailbox Login Rejected" and "--app-password" in n.sent[0][1]
    assert health.login_backoff(ap, "ap")
    due = schedule.after_check(ap, clock, CheckReport("ap", "login_rejected", "t"), DESKTOP)
    assert due - clock.now() == timedelta(hours=1)
    addresses.retry(ap, clock, "ap", actor="os_user")
    row = ap.execute("SELECT next_due_at, login_failures FROM check_state").fetchone()
    assert from_ts(row["next_due_at"]) == clock.now() and row["login_failures"] == 3


def test_a_new_app_password_clears_the_backoff(ap: sqlite3.Connection, clock: FakeClock) -> None:
    from ecf_server.secretstore.memory import MemorySecretStore  # noqa: PLC0415

    ap.execute("UPDATE check_state SET login_failures = 5")
    addresses.set_app_password(ap, clock, MemorySecretStore(), Mail(), "ap", GOOD, actor="os_user")
    row = ap.execute("SELECT next_due_at, login_failures FROM check_state").fetchone()
    assert row["login_failures"] == 0 and from_ts(row["next_due_at"]) == clock.now()


# ---- notifications --------------------------------------------------------------------------


def test_mac_notifications_pass_text_as_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(notify, "_run", calls.append)
    notify.MacNotifier().notify('Title "x"', 'Body"; do shell script "rm -rf ~"')
    (args,) = calls
    script = " ".join(args[:-2])
    assert args[-2:] == ['Title "x"', 'Body"; do shell script "rm -rf ~"']
    assert "rm -rf" not in script and "item 2 of argv" in script


def test_notification_failures_are_not_raised() -> None:
    notify.LinuxNotifier("/nonexistent/notify-send").notify("t", "b")  # logs, doesn't raise


def test_service_notifier_choice(
    ap: sqlite3.Connection,
    db_path: Path,
    tmp_path: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = FakeNotifier()  # CI runners have no desktop, so the real host choice would be none
    monkeypatch.setattr(service, "host_notifier", lambda: host)
    svc = Service(Paths(install="t", root=tmp_path), clock)
    svc.state.db_path = db_path
    assert svc._notifier() is host  # pyright: ignore[reportPrivateUsage]
    with write_tx(ap):
        ap.execute(
            "INSERT INTO settings (key, value, updated_at, updated_by)"
            " VALUES ('notifications', '\"off\"', 'now', 't')"
        )
    assert isinstance(svc._notifier(), NullNotifier)  # pyright: ignore[reportPrivateUsage]


# ---- API ------------------------------------------------------------------------------------


def test_retry_route_and_alerts_in_status(
    ap: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    after(ap, clock, FakeNotifier(), "login_rejected")
    ap.execute("UPDATE check_state SET login_failures = 2")
    after(ap, clock, FakeNotifier(), "login_rejected")
    st = ServiceState(install="t", token="t", started_at="t", clock=clock, db_path=db_path)

    async def call(method: str, path: str) -> dict[str, Any]:
        transport = httpx.ASGITransport(app=create_app(st))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            r = await c.request(method, path, headers={"Authorization": "Bearer t"})
            data: dict[str, Any] = r.json()
            return data

    assert anyio.run(call, "POST", "/v1/addresses/ap/retry")["address_id"] == "ap"
    (alert,) = anyio.run(call, "GET", "/v1/status")["alerts"]
    assert alert["kind"] == "login_rejected"


# ---- doctor ---------------------------------------------------------------------------------


def test_doctor_reports_addresses_and_alerts() -> None:
    st: dict[str, Any] = {
        "addresses": [
            {
                "address_id": "ap",
                "last_status": "login_rejected",
                "last_error": "rejected",
                "last_finished_at": "t",
            },
            {
                "address_id": "billing",
                "last_status": "ok",
                "last_error": None,
                "last_finished_at": "2026-10-01T12:00",
            },
            {
                "address_id": "info",
                "last_status": None,
                "last_error": None,
                "last_finished_at": None,
            },
        ],
        "alerts": [{"title": "Mailbox Login Rejected", "detail": "ap: rejected 3 times"}],
    }
    checks = doctor.judge_addresses(st)
    assert [c.level for c in checks] == [Level.FAIL, Level.OK, Level.WARN, Level.FAIL]
    assert "--app-password" in checks[0].fix


def test_doctor_dns_check() -> None:
    assert doctor.check_dns(lambda _n: True).level is Level.OK
    bad = doctor.check_dns(lambda _n: False)
    assert bad.level is Level.FAIL and "unverified" in bad.fix


def test_doctor_org_domains_without_a_service(tmp_path: Path) -> None:
    c = doctor.check_org_domains(Paths(install="t", root=tmp_path))
    assert c.level is Level.WARN
