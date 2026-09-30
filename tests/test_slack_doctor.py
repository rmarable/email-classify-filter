"""`ecf doctor`'s Slack and step-up checks (V1.2 step 12a)."""

from __future__ import annotations

import sqlite3
from typing import Any

from ecf_server import _slack, slack_admin, slack_doctor
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.secretstore.memory import MemorySecretStore
from ecf_server.stepper import FakeStepper

ME = "U0ME1"


class Web:
    """A Slack stand-in: answers per method, or raises the error set for it."""

    def __init__(self, members: list[str], errors: dict[str, Exception] | None = None) -> None:
        self.members, self.errors = members, errors or {}

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        if method in self.errors:
            raise self.errors[method]
        return {
            "auth.test": {"team_id": "T1", "user_id": "UBOT"},
            "conversations.members": {"members": self.members},
            "conversations.open": {"channel": {"id": "D1"}},
        }[method]


def _installed(conn: sqlite3.Connection, clock: FakeClock, *, member: str | None = ME) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"),
                     ("slack_summary_channel", "CSUM"),
                     ("slack_summary_name", "ecf-t-summary")):  # fmt: skip
            slack_admin.put_setting(conn, k, v, now, actor="test")
        if member:
            slack_admin.put_setting(conn, "slack_member_id", member, now, actor="test")


def _store() -> MemorySecretStore:
    s = MemorySecretStore()
    s.set(slack_admin.BOT_SECRET, "xoxb-test")
    return s


def _run(conn: sqlite3.Connection, web: Web, *, connected: bool = True,
         store: MemorySecretStore | None = None) -> dict[str, dict[str, str]]:  # fmt: skip
    rows = slack_doctor.checks(conn, store or _store(), lambda _t: web,
                               {"connected": connected, "last_connected_at": None},
                               FakeStepper())  # fmt: skip
    return {r["name"]: r for r in rows}


def test_all_well(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _installed(conn, clock)
    got = _run(conn, Web([ME, "UBOT"]))
    assert {n: r["level"] for n, r in got.items()} == {
        "step-up": "ok", "slack member": "ok", "slack token": "ok", "slack connection": "ok",
        "channel #ecf-t-summary": "ok", "slack DM": "ok"}  # fmt: skip


def test_problems_say_how_to_fix_them(conn: sqlite3.Connection, clock: FakeClock) -> None:
    assert _run(conn, Web([]))["slack"]["level"] == "warn"  # not installed
    _installed(conn, clock, member=None)
    got = _run(conn, Web([]), connected=False)
    assert got["slack member"]["level"] == "FAIL" and "Confirm" in got["slack member"]["fix"]
    assert got["slack connection"]["level"] == "FAIL"
    assert "channel #ecf-t-summary" not in got  # membership needs a confirmed member
    _installed(conn, clock)
    got = _run(conn, Web(["USOMEONE"]))
    assert got["channel #ecf-t-summary"]["level"] == "FAIL"
    revoked = Web([ME], {"auth.test": _slack.SlackError("auth.test", "token_revoked")})
    got = _run(conn, revoked)
    assert got["slack token"]["level"] == "FAIL" and "token_revoked" in got["slack token"]["detail"]
    assert "set-tokens" in got["slack token"]["fix"]
    got = _run(conn, Web([ME]), store=MemorySecretStore())
    assert got["slack token"]["detail"] == "no bot token stored"
    no_dm = Web(
        [ME], {"conversations.open": _slack.SlackError("conversations.open", "missing_scope")}
    )
    assert _run(conn, no_dm)["slack DM"]["level"] == "FAIL"
    with write_tx(conn):
        conn.execute("INSERT INTO alerts (key, kind, detail, opened_at)"
                     " VALUES ('k', 'slack_delivery_failed', 'invalid_auth', 'then')")  # fmt: skip
    assert _run(conn, Web([ME]))["slack delivery"]["level"] == "FAIL"


def test_no_step_up_is_a_failure(conn: sqlite3.Connection) -> None:
    [row] = slack_doctor.checks(conn, None, lambda _t: Web([]), {}, None)[:1]
    assert row["name"] == "step-up" and row["level"] == "FAIL"


def test_a_refused_app_token_a_failing_loop_and_lost_posts_are_shown(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _installed(conn, clock)
    web, store = Web([ME]), _store()

    def run(runtime: dict[str, Any]) -> dict[str, dict[str, str]]:
        rows = slack_doctor.checks(conn, store, lambda _t: web, runtime, FakeStepper())
        return {r["name"]: r for r in rows}

    got = run({"connected": False, "connect_error": "invalid_auth"})
    assert got["slack connection"]["level"] == "FAIL"
    assert got["slack connection"]["fix"] == "ecf slack set-tokens"
    got = run({"connected": True, "error": "OperationalError"})
    assert "failing (OperationalError)" in got["slack connection"]["detail"]
    with write_tx(conn):
        conn.execute("INSERT INTO jobs (job_id, queue, address_id, payload, timeout_s,"
                     " visible_at, state, created_at) VALUES ('j', 'slack_out', 'C1',"
                     " '{\"key\": \"digest:ap\"}', 30, 'now', 'dead', 'now')")  # fmt: skip
    assert run({"connected": True})["slack posts"]["level"] == "warn"


def test_a_channel_problem_a_missing_channel_and_notifications_off_are_shown(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """doctor said all was well while escalations waited (V1.2 review, 2026-09-30)."""
    _installed(conn, clock)
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('ap', 'ap@acme.example', 'high', 'A', 'now')")  # fmt: skip
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by)"
                     " VALUES ('notifications', '\"off\"', 'now', 't')")  # fmt: skip
    rows = slack_doctor.checks(conn, _store(), lambda _t: Web([ME]),
                               {"connected": True, "channels": "Slack doesn't let apps create"
                                " channels in this workspace."}, FakeStepper())  # fmt: skip
    got = {r["name"]: r for r in rows}
    assert got["slack channels"]["level"] == "FAIL"
    assert got["channel for ap"]["level"] == "FAIL"
    assert got["notifications"]["level"] == "warn"
