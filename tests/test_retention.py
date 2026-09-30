"""Retention (V1.2 step 11a): the daily job and `ecf retention set`."""

from __future__ import annotations

import json
import sqlite3

import pytest

from ecf.errors import InvalidInputError, StepupRequiredError
from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import items, retention, slack_admin, stepup
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.state_machine import TransitionContext
from ecf_server.stepper import FakeStepper

DAY = 86400


def _setup(conn: sqlite3.Connection, clock: FakeClock) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('ap', 'ap@acme.example', 'high', 'A', ?)", (now,))  # fmt: skip
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", "U1"),
                     ("slack_summary_channel", "CSUM")):  # fmt: skip
            slack_admin.put_setting(conn, k, v, now, actor="test")


def _item(conn: sqlite3.Connection, clock: FakeClock, sid: str, facts: dict[str, object],
          *, close: bool = True) -> None:  # fmt: skip
    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                      content_hash="h", facts=json.dumps(facts))  # fmt: skip
    with write_tx(conn):
        conn.execute("INSERT INTO excerpts (stable_id, actor_text) VALUES (?, 'x')", (sid,))
        conn.execute("INSERT INTO slack_messages (key, channel, ts, created_at, updated_at)"
                     " VALUES (?, 'C1', '1.0', 'now', 'now')", (f"item:{sid}",))  # fmt: skip
    if close:
        items.transition(conn, clock, StableId(sid), Status.RESOLVED_MANUAL, TransitionContext(),
                         actor="test")  # fmt: skip


def _left(conn: sqlite3.Connection, table: str, col: str = "stable_id") -> set[str]:
    return {r[0] for r in conn.execute(f"SELECT {col} FROM {table}")}  # noqa: S608


def test_the_daily_job_deletes_old_finished_items_and_keeps_the_rest(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    plain, bare, fraud, weak, regulator, quarantined, still_open, recent = (
        c * 64 for c in "abcdef01")  # fmt: skip
    _item(conn, clock, plain, {"triggers": {"fraud": [], "regulator": []}})
    _item(conn, clock, bare, {})  # no trigger keys at all: NULL in SQL, must still go
    _item(conn, clock, fraud, {"triggers": {"fraud": ["bank details"]}})
    _item(conn, clock, weak, {"triggers": {"fraud_weak": ["first-time sender"]}})
    _item(conn, clock, regulator, {"triggers": {"regulator": ["SEC"]}})
    _item(conn, clock, quarantined, {"quarantined": True})
    _item(conn, clock, still_open, {}, close=False)
    clock.advance(91 * DAY)
    _item(conn, clock, recent, {})
    assert retention.due(conn, clock)
    counts = retention.run(conn, clock)
    assert counts["items"] == 2
    kept = {fraud, weak, regulator, quarantined, still_open, recent}
    assert _left(conn, "items") == kept
    assert plain not in _left(conn, "excerpts")  # cascades with the item
    assert f"item:{plain}" not in _left(conn, "slack_messages", "key")
    assert not retention.due(conn, clock)  # once a day
    clock.advance(DAY)
    assert retention.due(conn, clock)
    events = [r[0] for r in conn.execute("SELECT event FROM audit")]
    assert "retention.run" in events  # the audit log itself is never pruned
    assert retention.run(conn, clock)["items"] == 0


def test_old_finished_jobs_and_used_nonces_go_too(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        for jid, st in (("j1", "done"), ("j2", "dead"), ("j3", "queued")):
            conn.execute("INSERT INTO jobs (job_id, queue, address_id, timeout_s, visible_at,"
                         " state, created_at) VALUES (?, 'actions', 'ap', 30, ?, ?, ?)",
                         (jid, now, st, now))  # fmt: skip
    issued = stepup.issue(conn, clock, FakeStepper(), "test", {"label": "x"})
    clock.advance(91 * DAY)
    counts = retention.run(conn, clock)
    assert (counts["jobs"], counts["nonces"]) == (2, 1)
    assert _left(conn, "jobs", "job_id") == {"j3"}
    assert issued.nonce_id not in _left(conn, "nonces", "nonce_id")


def test_setting_needs_step_up_and_lowering_it_sends_a_notice(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    n = FakeNotifier()
    for bad in (0, 3651, True):
        with pytest.raises(InvalidInputError, match="1 to 3650"):
            retention.set_days(conn, clock, n, bad, nonce=None)
    assert retention.set_days(conn, clock, n, 90, nonce=None)["changed"] is False
    with pytest.raises(StepupRequiredError) as ei:
        retention.set_days(conn, clock, n, 30, nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "retention_set", ei.value.extra["target"])
    assert issued.prompt.startswith("ecf: keep finished items 30 days (was 90)")
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    assert retention.set_days(conn, clock, n, 30, nonce=issued.nonce_id)["was"] == 90
    assert retention.days(conn) == 30
    assert n.sent[-1][0] == "[ecf-alert] Security Notice" and "30 days" in n.sent[-1][1]
    with pytest.raises(StepupRequiredError) as ei:
        retention.set_days(conn, clock, n, 365, nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "retention_set", ei.value.extra["target"])
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    retention.set_days(conn, clock, n, 365, nonce=issued.nonce_id)
    assert len(n.sent) == 1  # raising it sends no notice
    data = [json.loads(r[0]) for r in conn.execute(
        "SELECT data FROM audit WHERE event = 'retention.changed' ORDER BY id")]  # fmt: skip
    assert data == [{"from": 90, "to": 30}, {"from": 30, "to": 365}]
