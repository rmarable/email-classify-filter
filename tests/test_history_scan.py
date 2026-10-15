"""Historical scans (OD-507, plan v3.0.0-STEP5 §3): ranges bounded once, the boundary-day cut,
the `--act` count, estimate and cap, and the CLI's step-up."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from ecf import cli_history
from ecf.errors import ConflictError, InvalidInputError, StepupRequiredError
from ecf.timerange import stamp
from ecf_server import backfill, fetch, stepup
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.mail.fake import FakeMailSource
from ecf_server.stepper import FakeStepper
from tests.test_backfill import PLAIN, Env, counted, items_by_uid
from tests.test_backfill import env as env  # noqa: PLC0414 - the fixture
from tests.test_checks import Box, run
from tests.test_triggers import mail

ZONE = timezone(timedelta(hours=-10))  # a server keeping dates ten hours behind UTC


def _note(i: int) -> bytes:
    return mail(f"Note {i}", sender="Pat <pat@friends.example>")


# ---- the range, bounded once (P7) ------------------------------------------------------------


def test_scan_range_cuts_the_boundary_days_to_the_second_in_the_servers_zone() -> None:
    src = FakeMailSource(server_zone=ZONE)
    since = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
    until = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    at = {
        "before": since - timedelta(hours=1),  # same UTC day, earlier: out
        "late_utc": since - timedelta(hours=9),  # the server's date is a day before: out
        "start": since + timedelta(hours=1),
        "zone_edge": since + timedelta(hours=17),  # still 9-10 in the server's zone: in
        "middle": since + timedelta(days=5),
        "end": until - timedelta(minutes=1),
        "after": until + timedelta(hours=1),
        "next_day": until + timedelta(hours=20),
    }
    uid = {k: src.deliver(_note(i), when) for i, (k, when) in enumerate(at.items())}
    got = fetch.scan_range(src, since, until, end_uid=1000)
    assert got == sorted(uid[k] for k in ("start", "zone_edge", "middle", "end"))
    assert fetch.scan_range(src, since, None, end_uid=uid["end"]) == got  # new mail is not old


def test_pack_round_trips() -> None:
    uids = [1, 2, 3, 5, 7, 8, 20]
    assert fetch.pack(uids) == "1:3,5,7:8,20" and fetch.unpack(fetch.pack(uids)) == uids
    assert fetch.unpack(fetch.pack([])) == []


class CountingSource(FakeMailSource):
    searches = 0

    def uids_since(self, when: datetime, before: datetime | None = None) -> list[int]:
        CountingSource.searches += 1
        return super().uids_since(when, before)


def test_the_range_is_searched_once_and_pages_walk_the_stored_uids(
    env: Env, clock: FakeClock
) -> None:
    conn, _ = env
    box = Box()
    box.fake = CountingSource()
    for i in range(fetch.PAGE_MESSAGES + 5):
        box.fake.deliver(_note(i), clock.now() - timedelta(days=3))
    run(env, clock, box)
    backfill.start(conn, clock, "ap", stamp(clock.now() - timedelta(days=7)), act=False)
    CountingSource.searches = 0
    first = run(env, clock, box)
    assert first.backfill_remaining == 5 and CountingSource.searches == 2  # outer + inner
    bf = backfill.load(conn, "ap")
    assert bf is not None and bf.first_uid == 1 and bf.last_uid == fetch.PAGE_MESSAGES + 5
    run(env, clock, box)
    assert CountingSource.searches == 2  # no SEARCH on later pages
    assert backfill.load(conn, "ap") is None


def test_an_older_row_without_bounds_reads_as_before(env: Env, clock: FakeClock) -> None:
    conn, _ = env
    box = Box()
    old = box.fake.deliver(PLAIN, clock.now() - timedelta(days=3))
    run(env, clock, box)
    cur = fetch.load_cursor(conn, "ap")
    assert cur is not None
    row = {"since": (clock.now() - timedelta(days=7)).date().isoformat(), "act": False,
           "uidvalidity": cur.uidvalidity, "end_uid": cur.last_uid, "after_uid": 0}  # fmt: skip
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by)"
                     " VALUES ('backfill.ap', ?, 'now', 'service')",
                     (json.dumps(row),))  # fmt: skip
    assert run(env, clock, box).backfill_created == 1
    assert json.loads(items_by_uid(conn)[old]["facts"])["backfill"] == 1


def test_until_bounds_the_scan_and_held_mail_is_skipped_and_counted(
    env: Env, clock: FakeClock
) -> None:
    conn, _ = env
    box = Box()
    a = box.fake.deliver(_note(1), clock.now() - timedelta(days=9))
    b = box.fake.deliver(_note(2), clock.now() - timedelta(days=5))
    c = box.fake.deliver(_note(3), clock.now() - timedelta(days=2))
    run(env, clock, box)
    backfill.start(conn, clock, "ap", stamp(clock.now() - timedelta(days=6)), act=False,
                   until=stamp(clock.now() - timedelta(days=4)))  # fmt: skip
    run(env, clock, box)
    assert set(items_by_uid(conn)) == {b}
    backfill.start(conn, clock, "ap", stamp(clock.now() - timedelta(days=10)), act=False)
    run(env, clock, box)
    assert set(items_by_uid(conn)) == {a, b, c}
    [last] = backfill.status(conn)
    assert last["outcome"] == "done"
    started = [json.loads(r[0]) for r in conn.execute(
        "SELECT data FROM audit WHERE event = 'backfill.started' ORDER BY id")]  # fmt: skip
    assert started[-1]["typed"]["since"].endswith("Z") and "uids" not in started[-1]
    finished = [json.loads(r[0]) for r in conn.execute(
        "SELECT data FROM audit WHERE event = 'backfill.finished' ORDER BY id")]  # fmt: skip
    assert finished[-1]["held"] == 1  # b, read by the first scan


def test_the_service_resolves_ranges_and_refuses_bad_ones(clock: FakeClock) -> None:
    assert backfill.resolve_range(clock, "2026-09-01", None) == ("2026-09-01", None)  # N-1
    since, until = backfill.resolve_range(clock, "2026-09-01T00:00:00.000000Z", "2d")
    assert since == "2026-09-01T00:00:00.000000Z" and until is not None
    for bad in ("3 days", "2026-13-01"):
        with pytest.raises(InvalidInputError, match="use a date"):
            backfill.resolve_range(clock, bad, None)
    with pytest.raises(InvalidInputError, match="future"):
        backfill.resolve_range(clock, stamp(clock.now() + timedelta(days=1)), None)


# ---- --act: count, estimate, cap (P1, P11) ---------------------------------------------------


def test_the_count_runs_under_the_lease_minus_held(env: Env, clock: FakeClock) -> None:
    conn, _ = env
    box = Box()
    for i in range(3):
        box.fake.deliver(_note(i), clock.now() - timedelta(days=2))
    run(env, clock, box)
    since = stamp(clock.now() - timedelta(days=3))
    n = counted(env, clock, box, since)
    assert (n["state"], n["in_range"], n["held"], n["to_classify"]) == ("done", 3, 0, 3)
    backfill.start(conn, clock, "ap", since, act=False)
    run(env, clock, box)
    n = counted(env, clock, box, since)
    assert (n["in_range"], n["held"], n["to_classify"]) == (3, 3, 0)


def test_act_needs_a_count_of_exactly_that_range(env: Env, clock: FakeClock) -> None:
    conn, _ = env
    box = Box()
    run(env, clock, box)
    since = stamp(clock.now() - timedelta(days=3))
    with pytest.raises(ConflictError, match="count of this range first"):
        backfill.start(conn, clock, "ap", since, act=True)
    counted(env, clock, box, since)
    with pytest.raises(ConflictError, match="count of this range first"):
        backfill.start(conn, clock, "ap", stamp(clock.now() - timedelta(days=4)), act=True)
    assert backfill.start(conn, clock, "ap", since, act=True)["act"]
    with pytest.raises(ConflictError, match="no count"):  # used up by the start (L2)
        backfill.count(conn, "ap")


def test_over_the_cap_act_needs_a_step_up_bound_to_the_range_and_count(
    env: Env, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, _ = env
    box = Box()
    monkeypatch.setattr(backfill, "ACT_CAP", 1)
    for i in range(2):
        box.fake.deliver(_note(i), clock.now() - timedelta(days=2))
    run(env, clock, box)
    since = stamp(clock.now() - timedelta(days=3))
    counted(env, clock, box, since)
    with pytest.raises(StepupRequiredError) as ei:
        backfill.start(conn, clock, "ap", since, act=True)
    target = ei.value.extra["target"]
    assert ei.value.extra["purpose"] == "backfill_act"
    assert target == {"address_id": "ap", "since": since, "until": None, "act": True, "count": 2}
    with pytest.raises(InvalidInputError, match="count changed"):  # bound to the service's K
        stepup.issue(conn, clock, FakeStepper(), "backfill_act", target | {"count": 1})
    issued = stepup.issue(conn, clock, FakeStepper(), "backfill_act", target)
    assert "classify 2 old emails of ap@acme.example" in issued.prompt
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    assert backfill.start(conn, clock, "ap", since, act=True, nonce=issued.nonce_id)["act"]


def _session(conn: sqlite3.Connection, clock: FakeClock, sid: str, items: int, cost: float,
             minutes: float) -> None:  # fmt: skip
    began = clock.now() - timedelta(days=1)
    with write_tx(conn):
        conn.execute("INSERT INTO claude_sessions (session_id, started_at, ended_at, items)"
                     " VALUES (?, ?, ?, ?)", (sid, to_ts(began),
                                             to_ts(began + timedelta(minutes=minutes)),
                                             items))  # fmt: skip
        conn.execute("INSERT INTO claude_calls (ts, session_id, model, source, cost_usd)"
                     " VALUES (?, ?, 'claude-sonnet-5-5', 'agent', ?)",
                     (to_ts(clock.now() - timedelta(days=1)), sid, cost))  # fmt: skip


def test_the_estimate_uses_this_installs_sessions_else_specs_figures(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    e = backfill.estimate(conn, clock.now(), 100)
    assert (e["usd_low"], e["usd_high"]) == (2.4, 3.8) and "SPEC §21.2" in e["source"]
    assert e["minutes"] is None  # not measured yet
    _session(conn, clock, "s1", 40, 1.2, 20)
    _session(conn, clock, "s2", 20, 0.6, 20)
    _session(conn, clock, "eval", 0, 100.0, 30)  # an /ecf-eval session reviews no item (L2)
    e = backfill.estimate(conn, clock.now(), 100)
    assert e["usd"] == 3.0 and e["per_email_usd"] == 0.03 and "60 emails" in e["source"]
    assert e["minutes"] == 75  # median pace: 0.5 and 1 minute per email -> 0.75


# ---- the CLI: --yes never skips the step-up ---------------------------------------------------


class FakeClient:
    def __init__(self, count: int) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.count = count

    def get(self, path: str) -> dict[str, Any]:
        self.calls.append(("GET", path, {}))
        if path.startswith("/v1/backfill/count"):
            asked = next(b for _, p, b in reversed(self.calls) if p == "/v1/backfill/count")
            return {"state": "done", "since": asked["since"], "until": asked["until"],
                    "in_range": self.count, "held": 0, "to_classify": self.count,
                    "estimate": {"emails": self.count, "usd_low": 1.0, "usd_high": 2.0,
                                 "source": "SPEC", "minutes": None}}  # fmt: skip
        return {"addresses": [{"address_id": "ap", "stage": "assist"}]}

    def request(self, method: str, path: str, body: dict[str, Any] | None = None,
                **_: Any) -> dict[str, Any]:  # fmt: skip
        self.calls.append((method, path, body or {}))
        if path == "/v1/backfill" and not (body or {}).get("nonce_id"):
            raise StepupRequiredError("this needs step-up", purpose="backfill_act",
                                      target={"count": self.count})  # fmt: skip
        if path == "/v1/stepup/nonces":
            return {"nonce_id": "n1", "prompt_text": "ecf: classify", "code": "ABCD",
                    "needs_password": False}  # fmt: skip
        if path.endswith("/verify"):
            return {"verified": True, "outcome": "verified"}
        return {"address_id": "ap", "act": True, "run_id": "r1"}


def test_yes_skips_the_question_never_the_step_up(capsys: pytest.CaptureFixture[str]) -> None:
    c = FakeClient(600)
    r = cli_history.run_backfill(c, "ap", "3d", None, act=True, yes=True)  # type: ignore[arg-type]
    assert r["run_id"] == "r1"
    paths = [p for _, p, _ in c.calls]
    assert "/v1/stepup/nonces" in paths and c.calls[-1][2]["nonce_id"] == "n1"
    out = capsys.readouterr().out
    assert "600 in range, 0 already held, 600 to classify" in out and "step-up follows" in out
    assert "not measured yet" in out and "new mail is reviewed first" in out
    assert out.startswith("range (UTC): ")


def test_new_mail_read_during_a_scan_is_not_marked(env: Env, clock: FakeClock) -> None:
    """Review M7: the marker is per item: a check's page of new mail during a scan is ordinary
    new mail."""
    conn, _ = env
    box = Box()
    old = box.fake.deliver(_note(1), clock.now() - timedelta(days=3))
    run(env, clock, box)
    backfill.start(conn, clock, "ap", stamp(clock.now() - timedelta(days=7)), act=False)
    new = box.fake.deliver(_note(2))
    run(env, clock, box)
    got = items_by_uid(conn)
    assert json.loads(got[old]["facts"])["backfill"] == 1
    assert "backfill" not in json.loads(got[new]["facts"])
    assert "scan_run" not in json.loads(got[new]["facts"])
