"""Stages, sensitivity and settings (V1.2 step 10a)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from ecf.errors import InvalidInputError, PolicyDeniedError, StepupRequiredError
from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import approvals, items, needs_you, schedule, settings, slack_admin, stages, stepup
from ecf_server.actions import Planned
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.state_machine import TransitionContext
from ecf_server.stepper import FakeStepper

ME = "U0ME1"


def _setup(conn: sqlite3.Connection, clock: FakeClock, *, sensitivity: str = "high") -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('ap', 'ap@acme.example', ?, 'A', ?)",
                     (sensitivity, now))  # fmt: skip
        conn.execute("INSERT INTO routes (address_id, surface, route_ref, name)"
                     " VALUES ('ap', 'slack', 'CAP', 'ecf-default-ap')")  # fmt: skip
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", ME),
                     ("slack_summary_channel", "CSUM")):  # fmt: skip
            slack_admin.put_setting(conn, k, v, now, actor="test")


def _nonce(conn: sqlite3.Connection, clock: FakeClock, exc: StepupRequiredError) -> str:
    issued = stepup.issue(conn, clock, FakeStepper(), exc.extra["purpose"], exc.extra["target"])
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    return issued.nonce_id


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")
    return [json.loads(r[0]) for r in rows]


# ---- settings -------------------------------------------------------------------------------


def test_parsers() -> None:
    assert settings.business_hours("Mon-Fri 08:00-17:00 America/New_York") == {
        "days": [0, 1, 2, 3, 4], "start": "08:00", "end": "17:00",
        "tz": "America/New_York"}  # fmt: skip
    assert settings.business_hours("fri-mon 10:00-12:00 UTC")["days"] == [4, 5, 6, 0]
    for bad in ("weekdays 9-5", "mon-fri 17:00-08:00 UTC", "mon-fri 08:00-17:00 Mars/Base",
                "mon-xyz 08:00-17:00 UTC", "mon-fri 25:00-26:00 UTC"):  # fmt: skip
        with pytest.raises(InvalidInputError):
            settings.business_hours(bad)
    assert settings.KEYS["max_message_bytes"].parse("48MB") == 48 * settings.MB
    assert settings.KEYS["max_message_bytes"].parse("64") == 64 * settings.MB
    for bad in ("0.5", "64.5", "200MB", "lots"):  # 1 to 64 MB (OD-220)
        with pytest.raises(InvalidInputError, match="from 1 to 64"):
            settings.KEYS["max_scan_bytes_per_part"].parse(bad)


def test_install_and_address_values_and_where_other_keys_live(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    settings.set_value(conn, clock, "mail_fetch_interval_workday", "15", address=None,
                       actor="os_user")  # fmt: skip
    settings.set_value(conn, clock, "mail_fetch_interval_workday", "5", address="ap",
                       actor="os_user")  # fmt: skip
    power = schedule.Power(laptop=False, on_ac=True)
    assert schedule.settings(conn, "ap", power)["mail_fetch_interval_workday"] == 5  # override
    assert settings.get(conn, "mail_fetch_interval_workday") == 15
    r = settings.set_value(conn, clock, "notifications", "off", address=None, actor="os_user")
    assert r["restart"] is True
    for name, addr, why in (
        ("slack_member_id", None, "set-member"),
        ("export_dir", None, "V1.5"),
        ("outbound", "ap", "outbound enable"),
        ("security_config_delay_minutes", None, "OD-074"),
        ("nonsense", None, "no setting"),
        ("notifications", "ap", "whole install"),
        ("approval_ttl_days", None, "per address"),
    ):
        with pytest.raises(InvalidInputError, match=why):
            settings.set_value(conn, clock, name, "1", address=addr, actor="os_user")
    with pytest.raises(InvalidInputError, match="5 to 120"):
        settings.set_value(conn, clock, "mail_fetch_interval_offhours", "1", address=None,
                           actor="os_user")  # fmt: skip
    events = [r[0] for r in conn.execute("SELECT event FROM audit")]
    assert events.count("settings.changed") == 3
    keys = {r["key"] for r in settings.show(conn)}
    assert "notifications" in keys and "approval_ttl_days" not in keys
    assert "approval_ttl_days" in {r["key"] for r in settings.show(conn, "ap")}


def test_settings_reach_their_readers(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = 'live'")
    settings.set_value(conn, clock, "stale_item_days", "7", address=None, actor="os_user")
    settings.set_value(conn, clock, "approval_ttl_days", "3", address="ap", actor="os_user")
    items.create_item(conn, clock, stable_id=StableId("a" * 64), address_id=AddressId("ap"),
                      content_hash="h", facts="{}")  # fmt: skip
    for to in (Status.CLASSIFIED, Status.PROPOSED):
        items.transition(conn, clock, StableId("a" * 64), to, TransitionContext(), actor="t")
    approvals.request(conn, clock, "a" * 64, [Planned("archive")])
    expires = conn.execute("SELECT expires_at FROM grants").fetchone()[0]
    assert expires == to_ts(clock.now() + approvals.timedelta(days=3))
    clock.advance(8 * 86400)
    assert needs_you.mark_stale(conn, clock) == ["a" * 64]  # 7 days, not 30


# ---- stages ---------------------------------------------------------------------------------


def test_assist_needs_step_up_live_waits_and_going_back_is_instant(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    with pytest.raises(StepupRequiredError) as ei:
        stages.set_stage(conn, clock, "ap", "assist", nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "stage_set", ei.value.extra["target"])
    assert issued.prompt.startswith("ecf: move ap@acme.example from shadow to assist")
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    r = stages.set_stage(conn, clock, "ap", "assist", reason="watch undo rate",
                         nonce=issued.nonce_id)  # fmt: skip
    assert r == {"address_id": "ap", "stage": "assist", "changed": True}
    assert _posts(conn)[-1]["card"]["title"] == "Stage: assist (Labels only)"
    with pytest.raises(PolicyDeniedError, match=r"V1\.3"):
        stages.set_stage(conn, clock, "ap", "live", nonce=None)
    clock.advance(3 * 86400)
    [s] = stages.status(conn, clock.now())
    assert (s["stage"], s["days"], s["label"]) == ("assist", 3, "Labels only")
    assert stages.set_stage(conn, clock, "ap", "shadow", nonce=None)["changed"]  # no step-up
    assert not stages.set_stage(conn, clock, "ap", "shadow", nonce=None)["changed"]
    data = [json.loads(r[0]) for r in conn.execute(
        "SELECT data FROM audit WHERE event = 'stage.changed' ORDER BY id")]  # fmt: skip
    assert data[0] == {"from": "shadow", "to": "assist", "reason": "watch undo rate"}
    with pytest.raises(InvalidInputError):
        stages.set_stage(conn, clock, "ap", "full", nonce=None)


def test_lowering_sensitivity_needs_a_reason_step_up_and_sends_a_notice(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    n = FakeNotifier()
    with pytest.raises(InvalidInputError, match="reason"):
        stages.set_sensitivity(conn, clock, n, "ap", "standard", reason="", nonce=None)
    with pytest.raises(StepupRequiredError) as ei:
        stages.set_sensitivity(conn, clock, n, "ap", "standard", reason="only receipts",
                               nonce=None)  # fmt: skip
    r = stages.set_sensitivity(conn, clock, n, "ap", "standard", reason="only receipts",
                               nonce=_nonce(conn, clock, ei.value))  # fmt: skip
    assert r["sensitivity"] == "standard"
    assert n.sent[-1][0] == "[ecf-alert] Security Notice" and "only receipts" in n.sent[-1][1]
    r = stages.set_sensitivity(conn, clock, n, "ap", "high", reason="", nonce=None)  # instant
    assert r == {"address_id": "ap", "sensitivity": "high", "changed": True}
    assert len(n.sent) == 1  # raising it needs no notice
