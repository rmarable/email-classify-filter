"""Outbound reminders and `ecf outbound report|snooze|dismiss` (V1.5 step 6; SPEC §9.8)."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from ecf.cli_outbound import report_lines
from ecf.errors import ConflictError, InvalidInputError
from ecf.ids import AddressId, StableId
from ecf_server import checks, items, outbound, outbound_remind, slack_admin
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from tests.test_addresses import TOKEN, call, make_state
from tests.test_approvals import ME, _setup  # pyright: ignore[reportPrivateUsage]

DAY = 86400


def _ready(conn: sqlite3.Connection, clock: FakeClock, **kw: Any) -> None:
    _setup(conn, clock, **kw)
    with write_tx(conn):
        slack_admin.put_setting(conn, slack_admin.SUMMARY_CHANNEL, "CSUM", to_ts(clock.now()),
                                actor="test")  # fmt: skip


def _suppress(conn: sqlite3.Connection, clock: FakeClock, n: int,
              verdicts: tuple[str | None, ...] = ()) -> None:  # fmt: skip
    for i in range(n):
        sid = f"r{i:03d}".ljust(64, "0")
        items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                          content_hash="h", subject=f"Order {i}",
                          sender="pat@vendor.example")  # fmt: skip
        v = verdicts[i] if i < len(verdicts) else None
        with write_tx(conn):
            conn.execute("UPDATE items SET suppressed_action = 'reply_template', review = ?"
                         " WHERE stable_id = ?",
                         (json.dumps({"verdict": v}) if v else None, sid))  # fmt: skip


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")
    return [json.loads(r[0]) for r in rows]


def _events(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT event FROM audit WHERE event LIKE 'outbound.%'"
                                       " ORDER BY id")]  # fmt: skip


def test_day_7_then_weekly_five_then_monthly(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _ready(conn, clock)
    _suppress(conn, clock, 3, ("correct", "fixed", "asked"))
    n = FakeNotifier()
    clock.advance(7 * DAY - 60)
    assert outbound_remind.remind(conn, clock, n) == 0  # not yet
    clock.advance(60)
    assert outbound_remind.remind(conn, clock, n) == 1
    assert outbound_remind.remind(conn, clock, n) == 0  # once
    head, text = n.sent[0]
    assert head == "[ecf-alert] Operator Input Needed: outbound off (ap)"
    assert "live 7 days" in text and "3 template reply or forward" in text
    assert "(2 reviewed, 1 marked correct)" in text and "ecf outbound report ap" in text
    assert [p["channel"] for p in _posts(conn)] == [ME, "CSUM"]  # a DM and the summary channel
    for _ in range(4):  # weekly, 5 in all
        clock.advance(7 * DAY - 60)
        assert outbound_remind.remind(conn, clock, n) == 0
        clock.advance(60)
        assert outbound_remind.remind(conn, clock, n) == 1
    assert len(n.sent) == 5 and "Reminder 5 of 5" in n.sent[-1][1]
    clock.advance(7 * DAY)
    assert outbound_remind.remind(conn, clock, n) == 0  # monthly from now on
    clock.advance(23 * DAY)
    assert outbound_remind.remind(conn, clock, n) == 1
    assert len(n.sent) == 5  # the monthly line isn't a desktop notification or a DM
    last = _posts(conn)[-1]
    assert last["channel"] == "CSUM" and last["card"]["title"] == "Outbound still off (ap)"
    assert "0 send proposal(s) held back this month, 3 in all" in last["card"]["text"]
    assert _events(conn) == ["outbound.reminded"] * 6


def test_a_late_wake_sends_one_not_the_missed_ones(conn: sqlite3.Connection,
                                                   clock: FakeClock) -> None:  # fmt: skip
    _ready(conn, clock)
    n = FakeNotifier()
    clock.advance(40 * DAY)
    assert outbound_remind.remind(conn, clock, n) == 1
    assert outbound_remind.remind(conn, clock, n) == 0
    s = outbound_remind.schedule(conn, "ap")
    assert s["sent"] == 1 and s["next_at"] == to_ts(clock.now() + timedelta(days=7))


def test_counts_from_first_going_live(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _ready(conn, clock)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = 'shadow'")
    assert outbound_remind.schedule(conn, "ap")["next_at"] is None  # never live
    clock.advance(30 * DAY)
    live = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = 'live'")
        for to in ("live", "assist", "live"):  # a model change dropped it and it came back
            conn.execute("INSERT INTO audit (ts, address_id, event, actor, outcome, data) VALUES"
                         " (?, 'ap', 'stage.changed', 'os_user', 'ok', ?)",
                         (to_ts(clock.now()), json.dumps({"to": to})))  # fmt: skip
            clock.advance(DAY)
    assert outbound_remind.live_since(conn, "ap") == live
    assert outbound_remind.schedule(conn, "ap")["next_at"] == to_ts(
        clock.now() - timedelta(days=3) + timedelta(days=7))  # fmt: skip


def test_no_reminders_paused_dismissed_or_ever_enabled(conn: sqlite3.Connection,
                                                       clock: FakeClock) -> None:  # fmt: skip
    _ready(conn, clock)
    n = FakeNotifier()
    clock.advance(8 * DAY)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET paused = 1")
    assert outbound_remind.remind(conn, clock, n) == 0
    with write_tx(conn):
        conn.execute("UPDATE addresses SET paused = 0")
        conn.execute("INSERT INTO audit (ts, address_id, event, actor, outcome, data) VALUES"
                     " (?, 'ap', 'outbound.enabled', 'os_user', 'ok', '{}')",
                     (to_ts(clock.now()),))  # fmt: skip
    assert outbound_remind.remind(conn, clock, n) == 0  # turned off again later: a choice
    assert outbound_remind.schedule(conn, "ap")["next_at"] is None


def test_snooze_and_dismiss(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _ready(conn, clock)
    n = FakeNotifier()
    clock.advance(6 * DAY)
    with pytest.raises(InvalidInputError, match="1-90"):
        outbound_remind.snooze(conn, clock, "ap", 91, actor="os_user")
    s = outbound_remind.snooze(conn, clock, "ap", 10, actor="os_user")
    assert s["snoozed_until"] == s["next_at"] == to_ts(clock.now() + timedelta(days=10))
    clock.advance(9 * DAY)
    assert outbound_remind.remind(conn, clock, n) == 0
    clock.advance(DAY)
    assert outbound_remind.remind(conn, clock, n) == 1
    assert outbound_remind.schedule(conn, "ap")["snoozed_until"] is None  # used up
    d = outbound_remind.dismiss(conn, clock, "ap", actor="os_user")
    assert d["dismissed_at"] and d["next_at"] is None
    clock.advance(60 * DAY)
    assert outbound_remind.remind(conn, clock, n) == 0
    assert _events(conn) == ["outbound.reminders_snoozed", "outbound.reminded",
                             "outbound.reminders_dismissed"]  # fmt: skip


def test_snooze_refused_while_outbound_is_on(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _ready(conn, clock)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET outbound = 1")
    with pytest.raises(ConflictError, match="outbound is on"):
        outbound_remind.dismiss(conn, clock, "ap", actor="os_user")


def test_track_record_counts_only_correct_and_fixed(conn: sqlite3.Connection,
                                                    clock: FakeClock) -> None:  # fmt: skip
    _ready(conn, clock)
    _suppress(conn, clock, 5, ("correct", "fixed", "asked", "not_sampled"))
    rec = outbound.track_record(conn, "ap")
    assert (rec["suppressed"], rec["reviewed"], rec["correct"]) == (5, 2, 1)


def test_report(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _ready(conn, clock, sensitivity="high")
    _suppress(conn, clock, 2, ("correct", "asked"))
    r = outbound_remind.report(conn, clock, "ap")
    assert r["outbound"] is False and r["record"]["reviewed"] == 1
    assert r["high_gate"] is not None and "20 reviewed" in r["high_gate"]
    assert [s["review"] for s in r["suppressed"]] == ["correct", None]
    assert r["suppressed"][0]["action"] == "reply_template"
    assert r["limits"] == {"max_sends_per_hour": 25, "max_sends_per_day": 250}
    lines = report_lines(r)
    assert lines[0] == "ap@acme.example (ap): outbound off, live, high"
    assert lines[1] == "suppressed sends: 2; reviewed 1, 1 marked correct (100%)"
    assert any("not reviewed" in ln and "Order 1" in ln for ln in lines)
    assert lines[-1].startswith("reminders: 0 sent, next ")


def test_status_rows_show_outbound(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _ready(conn, clock)
    _suppress(conn, clock, 2)
    [row] = checks.states(conn)
    assert row["outbound"] is False and row["suppressed"] == 2


def test_the_routes(conn: sqlite3.Connection, db_path: Path, clock: FakeClock) -> None:
    _ready(conn, clock)
    st = make_state(db_path, None)
    r = call(st, "GET", "/v1/addresses/ap/outbound", None, TOKEN)
    assert r.status_code == 200 and r.json()["record"]["suppressed"] == 0
    r = call(st, "POST", "/v1/addresses/ap/outbound", {"value": "snooze", "days": "3"}, TOKEN)
    assert r.status_code == 400
    r = call(st, "POST", "/v1/addresses/ap/outbound", {"value": "snooze", "days": 3}, TOKEN)
    assert r.status_code == 200 and r.json()["snoozed_until"]
    r = call(st, "POST", "/v1/addresses/ap/outbound", {"value": "dismiss"}, TOKEN)
    assert r.status_code == 200 and r.json()["dismissed_at"]
