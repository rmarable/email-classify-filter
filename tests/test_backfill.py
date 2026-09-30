"""`ecf backfill` (V1.2 step 11b): older mail through the check path; records only unless --act."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from ecf.errors import ConflictError, InvalidInputError, PolicyDeniedError
from ecf.status import Status
from ecf_server import backfill, checks, fetch, schedule
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from tests.test_checks import Box, run
from tests.test_precheck import BEC
from tests.test_triggers import mail

Env = tuple[sqlite3.Connection, Path]
PLAIN = mail("Lunch on Friday?", sender="Pat <pat@friends.example>")


@pytest.fixture
def env(conn: sqlite3.Connection, db_path: Path) -> Env:
    """One `high` address with a probed host (as tests/test_checks.py)."""
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                     " VALUES ('ap', 'ap@acme.example', 'high', 'A', 'now')")  # fmt: skip
        conn.execute("INSERT INTO probe (address_id, host, probed_at)"
                     " VALUES ('ap', 'imap.acme.example', 'now')")  # fmt: skip
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by)"
                     " VALUES ('org_domains', '[\"acme.example\"]', 'now', 'test')")  # fmt: skip
    return conn, db_path


def _old(box: Box, clock: FakeClock, raw: bytes, days: int) -> int:
    return box.fake.deliver(raw, clock.now() - timedelta(days=days))


def _since(clock: FakeClock, days: int) -> str:
    return (clock.now() - timedelta(days=days)).date().isoformat()


def _items(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    return {json.loads(r["locator"])["uid"]: r for r in conn.execute("SELECT * FROM items")}


def test_records_only_decides_and_closes_without_acting(env: Env, clock: FakeClock) -> None:
    conn, _ = env
    box = Box()
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = 'assist'")  # it would act on new mail
    old_fraud, old_plain = _old(box, clock, BEC, 5), _old(box, clock, PLAIN, 3)
    _old(box, clock, PLAIN, 40)  # before --since: never read
    assert run(env, clock, box).status == "first_run"  # starts from now
    new = box.fake.deliver(PLAIN)
    r = backfill.start(conn, clock, "ap", _since(clock, 10), act=False)
    assert r == {"address_id": "ap", "since": _since(clock, 10), "act": False}
    report = run(env, clock, box)
    assert (report.created, report.backfill_created, report.backfill_remaining) == (1, 2, 0)
    got = _items(conn)
    assert set(got) == {new, old_fraud, old_plain}
    assert got[new]["status"] == Status.NEW  # new mail: the ordinary path
    for uid in (old_fraud, old_plain):
        assert got[uid]["status"] == Status.OBSERVED
        facts = json.loads(got[uid]["facts"])
        assert facts["backfill"] == 1 and facts["precheck"]["stage"] == "backfill"
    fraud_facts = json.loads(got[old_fraud]["facts"])
    assert fraud_facts["precheck"]["escalate"] and fraud_facts["precheck"]["escalation"] is None
    assert conn.execute("SELECT count(*) FROM escalations").fetchone()[0] == 0
    assert not box.fake.flags([old_fraud])[old_fraud]  # nothing done to the mailbox
    assert backfill.load(conn, "ap") is None  # finished
    [done] = [json.loads(x[0]) for x in conn.execute(
        "SELECT data FROM audit WHERE event = 'backfill.finished'")]  # fmt: skip
    assert done["outcome"] == "done"


def test_act_runs_the_pre_check_as_for_new_mail(env: Env, clock: FakeClock) -> None:
    conn, _ = env
    box = Box()
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = 'assist'")
    old_fraud = _old(box, clock, BEC, 2)
    run(env, clock, box)
    backfill.start(conn, clock, "ap", _since(clock, 7), act=True)
    report = run(env, clock, box)
    assert report.backfill_created == 1 and report.escalations == 1
    item = _items(conn)[old_fraud]
    assert item["status"] == Status.NEW and item["prechecked"] == 1
    assert box.fake.flags([old_fraud])[old_fraud]  # labelled and flagged (assist)
    assert conn.execute("SELECT count(*) FROM escalations").fetchone()[0] == 1


def test_a_long_backfill_goes_page_by_page_and_counts_as_backlog(
    env: Env, clock: FakeClock
) -> None:
    conn, _ = env
    box = Box()
    for i in range(fetch.PAGE_MESSAGES + 5):
        _old(box, clock, mail(f"Note {i}", sender="Pat <pat@friends.example>"), 3)
    run(env, clock, box)
    backfill.start(conn, clock, "ap", _since(clock, 7), act=False)
    first = run(env, clock, box)
    assert (first.backfill_created, first.backfill_remaining) == (fetch.PAGE_MESSAGES, 5)
    assert first.more  # `ecf check --until-empty` and catch-up keep going
    assert backfill.load(conn, "ap") is not None
    second = run(env, clock, box)
    assert (second.backfill_created, second.backfill_remaining) == (5, 0)
    assert backfill.load(conn, "ap") is None
    assert run(env, clock, box).backfill_created == 0  # nothing read twice


def test_a_mailbox_reset_ends_it(env: Env, clock: FakeClock) -> None:
    conn, _ = env
    box = Box()
    _old(box, clock, PLAIN, 3)
    run(env, clock, box)
    backfill.start(conn, clock, "ap", _since(clock, 7), act=False)
    box.fake.reset(uidvalidity=2)
    report = run(env, clock, box)
    assert report.backfill_created == 0 and backfill.load(conn, "ap") is None
    row = conn.execute("SELECT data FROM audit WHERE event = 'backfill.finished'").fetchone()
    assert json.loads(row[0])["outcome"] == "mailbox reset"


def test_refusals(env: Env, clock: FakeClock) -> None:
    conn, _ = env
    box = Box()
    with pytest.raises(ConflictError, match="ecf check"):
        backfill.start(conn, clock, "ap", _since(clock, 7), act=False)
    run(env, clock, box)
    for bad in ("last week", "2026-13-01"):
        with pytest.raises(InvalidInputError, match="date like"):
            backfill.start(conn, clock, "ap", bad, act=False)
    with pytest.raises(InvalidInputError, match="future"):
        backfill.start(conn, clock, "ap", _since(clock, -2), act=False)
    backfill.start(conn, clock, "ap", _since(clock, 7), act=False)
    with pytest.raises(ConflictError, match="already running"):
        backfill.start(conn, clock, "ap", _since(clock, 3), act=True)
    assert backfill.status(conn) == [
        {"address_id": "ap", "since": _since(clock, 7), "act": False, "items": 0}]  # fmt: skip
    queued = conn.execute("SELECT payload FROM jobs WHERE queue = 'fetch'").fetchall()
    assert [json.loads(q[0]) for q in queued] == [{"reason": "backfill"}]


def test_only_backfill_may_close_a_new_item_as_observed() -> None:
    from ecf_server.state_machine import TransitionContext, check_transition  # noqa: PLC0415

    check_transition(Status.NEW, Status.OBSERVED, TransitionContext(backfill=True))
    with pytest.raises(PolicyDeniedError, match="guard refused new -> observed"):
        check_transition(Status.NEW, Status.OBSERVED, TransitionContext())


def test_check_report_line_mentions_backfill() -> None:
    from ecf.cli import _check_line  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]

    r = checks.CheckReport("ap", "ok", "now", backfill_created=3, backfill_remaining=2)
    assert _check_line(r.to_json()).endswith("3 backfilled, 2 older still to backfill")


def test_remaining_backfill_schedules_a_catch_up_pause(env: Env, clock: FakeClock) -> None:
    conn, _ = env
    power = schedule.Power(laptop=False, on_ac=True)
    r = checks.CheckReport("ap", "ok", "now", backfill_remaining=5)
    assert schedule.after_check(conn, clock, r, power) == clock.now() + schedule.CATCH_UP_PAUSE
    r = checks.CheckReport("ap", "ok", "now")
    assert schedule.after_check(conn, clock, r, power) > clock.now() + schedule.CATCH_UP_PAUSE


def test_a_pass_that_failed_to_decide_is_picked_up_next_time(
    env: Env, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Progress was saved before the decision, stranding items at `new` (V1.2 review)."""
    conn, _ = env
    box = Box()
    old = _old(box, clock, PLAIN, 3)
    run(env, clock, box)
    backfill.start(conn, clock, "ap", _since(clock, 7), act=False)
    real = backfill.decide

    def fail(*_a: object, **_k: object) -> list[object]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(backfill, "decide", fail)
    assert run(env, clock, box).status == "internal_error"
    assert _items(conn)[old]["status"] == Status.NEW  # created, not decided
    monkeypatch.setattr(backfill, "decide", real)
    run(env, clock, box)
    assert _items(conn)[old]["status"] == Status.OBSERVED
    assert backfill.load(conn, "ap") is None
