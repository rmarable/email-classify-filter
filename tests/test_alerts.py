"""Alerts in Slack (V1.2 step 9): titles, routes, the sweep of open and resolved alerts, events,
`ecf alerts set|show|test`."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from ecf.errors import InvalidInputError, StepupRequiredError
from ecf.ids import AddressId
from ecf_server import alerts, breaker, health, jobs, slack_admin, stepup
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.stepper import FakeStepper

ME = "U0ME1"


def _slack(conn: sqlite3.Connection, clock: FakeClock) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", ME),
                     ("slack_summary_channel", "CSUM")):  # fmt: skip
            slack_admin.put_setting(conn, k, v, now, actor="test")


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")
    return [json.loads(r[0]) for r in rows]


def test_open_and_resolved_alerts_reach_slack_once_each(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    n = FakeNotifier()
    health.open_alert(conn, clock, n, "mail_unreachable", None, "imap.example.test: no answer")
    assert n.sent[0][0] == "[ecf-alert] Mail Provider Unreachable"
    assert alerts.sweep(conn, clock) == 0  # no summary channel yet: it waits
    _slack(conn, clock)
    assert alerts.sweep(conn, clock) == 1 and alerts.sweep(conn, clock) == 0
    [post] = _posts(conn)
    assert post["channel"] == "CSUM"
    assert post["card"]["title"] == "[ecf-alert] Mail Provider Unreachable"
    health.resolve_alert(conn, clock, n, "mail_unreachable", None)
    assert alerts.sweep(conn, clock) == 1
    assert _posts(conn)[-1]["card"]["title"] == "[ecf-alert] Resolved: Mail Provider Unreachable"
    clock.advance(60)
    health.open_alert(conn, clock, n, "mail_unreachable", None, "again")  # reopened: posted again
    assert alerts.sweep(conn, clock) == 1


def test_slack_delivery_failed_never_goes_through_slack(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _slack(conn, clock)
    n = FakeNotifier()
    health.open_alert(conn, clock, n, "slack_delivery_failed", None, "token revoked")
    assert alerts.sweep(conn, clock) == 0 and _posts(conn) == []
    assert n.sent[0][0] == "[ecf-alert] Slack Delivery Failed"  # the desktop still hears it
    assert alerts.routes(conn, "slack") == []


def test_routes_refuse_email_while_off_and_slack_for_slack_health() -> None:
    assert alerts.validate(None, ["Slack", "slack"]) == ["slack"]
    with pytest.raises(InvalidInputError, match="alert email is off"):
        alerts.validate(None, ["slack", "email"])
    assert alerts.validate(None, ["email", "slack"], email_on=True) == ["email", "slack"]
    with pytest.raises(InvalidInputError, match="desktop"):
        alerts.validate("slack", ["slack"])
    assert alerts.validate("slack", ["email"], email_on=True) == ["email"]
    with pytest.raises(InvalidInputError, match="at least one"):
        alerts.validate("mail", [" "])
    with pytest.raises(InvalidInputError, match="classes"):
        alerts.validate("fraud", ["slack"])
    with pytest.raises(InvalidInputError, match="not pager"):
        alerts.validate(None, ["pager"])


def test_setting_routes_needs_step_up_and_sends_a_security_notice(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _slack(conn, clock)
    n = FakeNotifier()
    with pytest.raises(StepupRequiredError) as ei:
        alerts.set_routes(conn, clock, n, "mail", ["slack"], nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "alerts_set", ei.value.extra["target"])
    assert issued.prompt.startswith("ecf: send mail alerts to slack")
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    r = alerts.set_routes(conn, clock, n, "mail", ["slack"], nonce=issued.nonce_id)
    assert r["classes"]["mail"] == ["slack"] and r["classes"]["slack"] == []
    assert n.sent[-1][0] == "[ecf-alert] Security Notice"
    notices = [p for p in _posts(conn) if p["key"].startswith("notice:")]
    assert {p["channel"] for p in notices} == {ME, "CSUM"}


def test_events_and_the_test_alert(conn: sqlite3.Connection, clock: FakeClock) -> None:
    n = FakeNotifier()
    assert alerts.test(conn, clock, n) == {"sent": ["desktop"]}  # no Slack yet
    _slack(conn, clock)
    assert alerts.test(conn, clock, n) == {"sent": ["desktop", "slack"]}
    alerts.event(conn, clock, n, "system_error", "ecf restarted after a crash")
    assert n.sent[-1] == ("[ecf-alert] System Error", "ecf restarted after a crash")
    assert _posts(conn)[-1]["card"]["title"] == "[ecf-alert] System Error"
    assert alerts.title("operator_input", "approval waiting (ap)") == (
        "[ecf-alert] Operator Input Needed: approval waiting (ap)")  # fmt: skip


def test_jobs_that_gave_up_are_reported_once(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _slack(conn, clock)
    n = FakeNotifier()
    for queue in (jobs.Queue.ACTIONS, jobs.Queue.SLACK_OUT):
        jobs.enqueue(conn, clock, queue, AddressId("ap"), {}, timeout_s=30, max_attempts=1)
        job = jobs.claim(conn, clock, queue, "w")
        assert job is not None
        jobs.fail(conn, clock, job.job_id, "w", "boom")
    assert alerts.dead_jobs(conn, clock, n) == 1  # Slack posts are Slack Delivery Failed's
    assert "1 background job(s) gave up" in n.sent[-1][1] and "actions" in n.sent[-1][1]
    assert alerts.dead_jobs(conn, clock, n) == 0  # already reported


def test_a_start_after_a_crash_is_flagged(tmp_path: Any, clock: FakeClock) -> None:
    state, marker = tmp_path / "crash.json", tmp_path / "running"
    assert not breaker.on_start(state, marker, clock.now()).crashed_before
    breaker.mark_running(marker)  # never cleaned up: the process died
    st = breaker.on_start(state, marker, clock.now())
    assert st.crashed_before and not st.tripped
