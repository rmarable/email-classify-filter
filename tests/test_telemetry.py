"""The telemetry receiver and the Claude model check (V1.4 step 6; SPEC §7.5, §11.5, §13.4;
OD-307): what the receiver keeps, judging a submission's window, the agent token and agent names,
held submissions, refusals, plan usage from the status line, and Claude's figures in stats, the
daily summary, retention and doctor."""

from __future__ import annotations

import io
import json
import sqlite3
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from ecf import doctor
from ecf import statusline as sl
from ecf.doctor import Level
from ecf_server import claude_pins, claude_usage, retention, telemetry, telemetry_app
from ecf_server.api import ServiceState, create_app
from ecf_server.clock import FakeClock, to_ts
from ecf_server.notify import FakeNotifier
from ecf_server.telemetry import SUBAGENT, ApiCall, Hold, Seen, Swap, Telemetry
from tests.test_claude_review import REQUEST, add, waiting_item

HAIKU = "claude-haiku-4-5-20251001"  # any model ID: parsing and judging don't look at the pins
CLASSIFIER = claude_pins.load_lock()["classifier"]  # what the model check expects of ecf-classifier


def attr(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}  # OTLP JSON: int64 as text
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": value}}


def record(**attrs: Any) -> dict[str, Any]:
    return {"attributes": [attr(k.replace("__", "."), v) for k, v in attrs.items()]}


def logs(*records: dict[str, Any]) -> dict[str, Any]:
    resource = {"attributes": [attr("user.email", "someone@example.com"),
                               attr("organization.id", "org-1")]}  # fmt: skip
    return {"resourceLogs": [{"resource": resource,
                              "scopeLogs": [{"logRecords": list(records)}]}]}  # fmt: skip


NOW = FakeClock().now()


def iso(offset_s: float = 0.0) -> str:
    """An `event.timestamp` this many seconds after FakeClock's start."""
    return (NOW + timedelta(seconds=offset_s)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def api_request(end_s: float = 1.0, model: str = CLASSIFIER, source: str = SUBAGENT,
                duration_ms: int = 3000, **more: Any) -> dict[str, Any]:  # fmt: skip
    """An API request ending `end_s` seconds after FakeClock's start."""
    return record(event__name="claude_code.api_request", event__timestamp=iso(end_s), model=model,
                  query_source=source, duration_ms=duration_ms,
                  user__email="someone@example.com", **more)  # fmt: skip


def at(offset_s: float) -> float:
    return NOW.timestamp() + offset_s


def hold(at_s: float, read_s: float | None = None) -> Hold:
    return Hold("s1", "item", 1, "act", "ecf-actor", {}, at=at(at_s),
                read_at=None if read_s is None else at(read_s))  # fmt: skip


# ---- parsing and judging -------------------------------------------------------------------


def test_only_numbers_models_times_and_codes_are_kept() -> None:
    body = logs(api_request(1, input_tokens=1200, output_tokens=80, cache_read_tokens=900,
                            duration_ms=2100, cost_usd=0.0123, prompt="<REDACTED>"),
                record(event__name="tool_decision", tool_use_id="toolu_A", tool_name="mcp_tool"),
                record(event__name="subagent_completed", event__timestamp=iso(9), duration_ms=4000,
                       model_swapped=True, final_model="claude-sonnet-5-5"),
                record(event__name="subagent_completed", event__timestamp=iso(9),
                       model_swapped=False, final_model=HAIKU),
                record(event__name="api_request", model="evil model\nname", query_source=7),
                {"attributes": "not a list"})  # fmt: skip
    calls, swaps = telemetry.parse_logs(body)
    assert calls[0] == ApiCall(at(1), CLASSIFIER, SUBAGENT, 1200, 80, 900, None, 2100, 0.0123)
    assert calls[0].start == pytest.approx(at(1) - 2.1)
    assert calls[1].model == "unknown" and calls[1].source == "unknown" and calls[1].end is None
    assert swaps == [Swap(at(5), at(9), "claude-sonnet-5-5")]  # only the swapped run
    assert "someone@example.com" not in repr((calls, swaps))
    assert telemetry.parse_logs({"resourceLogs": "x"}) == ([], [])


def test_a_submission_is_judged_by_the_subagent_work_in_its_window() -> None:
    tel = Telemetry()
    tel.open("s1")
    sub = [ApiCall(at(-5), HAIKU, SUBAGENT, duration_ms=2000)]  # ended before the read
    tel.add("s1", [*sub, ApiCall(at(-1), "claude-opus-5-5", "repl_main_thread")], [])
    assert tel.judge(hold(0, -10)) is None  # telemetry hasn't caught up with the submission
    tel.add("s1", [ApiCall(at(0.4), HAIKU, SUBAGENT, duration_ms=3000)], [])
    assert tel.judge(hold(0, -10)) == Seen(HAIKU)  # the main session's request doesn't count
    assert tel.judge(hold(0, -2)) == Seen(HAIKU)  # only the request overlapping the window
    tel.add("s1", [ApiCall(at(1), "claude-sonnet-5-5", SUBAGENT, duration_ms=1500)], [])
    assert tel.judge(hold(0, -10)) == Seen(f"{HAIKU} + claude-sonnet-5-5")  # never accepted
    assert tel.judge(hold(0, -10)) != Seen(HAIKU)
    assert tel.judge(Hold("s2", "x", 1, "act", "ecf-actor", {}, at=at(0))) is None


def test_a_swapped_subagent_run_counts_in_the_window() -> None:
    tel = Telemetry()
    tel.open("s1")
    tel.add(
        "s1",
        [ApiCall(at(1), HAIKU, SUBAGENT, duration_ms=3000)],
        [Swap(at(-3), at(2), "claude-sonnet-5-5")],
    )
    assert tel.judge(hold(0, -1)) == Seen(f"{HAIKU} + claude-sonnet-5-5")


def test_at_session_end_a_hold_is_judged_on_what_came() -> None:
    tel = Telemetry()
    tel.open("s1")
    early, empty = hold(0, -2), hold(30, 29)
    tel.add("s1", [ApiCall(at(-0.5), HAIKU, SUBAGENT)], [])  # overlaps, but hasn't caught up
    assert tel.hold(early) and tel.hold(empty)
    assert tel.take_bound("s1") == []
    assert tel.take_all("s1") == [(early, Seen(HAIKU)), (empty, None)]


def test_judge_waits_for_the_events_to_arrive() -> None:
    tel = Telemetry()
    tel.open("s1")
    later = threading.Timer(
        0.1, lambda: tel.add("s1", [ApiCall(at(1), HAIKU, SUBAGENT, duration_ms=3000)], [])
    )
    later.start()
    started = time.monotonic()
    assert tel.judge(hold(0), wait_s=5) == Seen(HAIKU)
    assert time.monotonic() - started < 4
    later.join()


def test_plan_numbers_are_checked() -> None:
    p = telemetry.parse_plan({"five_hour": 12.5, "seven_day": 140,
                              "five_hour_resets_at": 1790000000,
                              "seven_day_resets_at": "2026-10-09T00:00:00Z"})  # fmt: skip
    assert p == telemetry.Plan(12.5, None, "1790000000", "2026-10-09T00:00:00Z")
    assert telemetry.parse_plan(
        {"five_hour": True, "seven_day_resets_at": "a b"}
    ) == telemetry.Plan(None, None, None, None)


# ---- the receiver --------------------------------------------------------------------------


@pytest.fixture
def state(conn: sqlite3.Connection, db_path: Path) -> ServiceState:  # conn: migrated
    st = ServiceState(install="t", token="cli-token", started_at="2026-10-02T09:00:00Z",
                      clock=FakeClock(), db_path=db_path, notifier=FakeNotifier())  # fmt: skip
    st.telemetry_wait_s = 0
    return st


CLI = "cli-token"


def api(state: ServiceState, method: str, path: str, body: Any = None,
        token: str = CLI) -> httpx.Response:  # fmt: skip
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.request(method, path, json=body,
                                   headers={"Authorization": f"Bearer {token}"})  # fmt: skip

    return anyio.run(go)


def export(state: ServiceState, bearer: str, body: Any, path: str = "/v1/logs",
           content_type: str = "application/json") -> httpx.Response:  # fmt: skip
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=telemetry_app.create_receiver(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            return await c.post(path, content=json.dumps(body),
                                headers={"Authorization": f"Bearer {bearer}",
                                         "Content-Type": content_type})  # fmt: skip

    return anyio.run(go)


def test_the_receiver_takes_only_a_sessions_json(conn: sqlite3.Connection,
                                                 state: ServiceState) -> None:  # fmt: skip
    made = api(state, "POST", "/v1/sessions").json()
    bearer = made["telemetry_bearer"]
    assert bearer != made["profile_token"]
    body = logs(api_request(source="main", input_tokens=10, output_tokens=2))
    assert export(state, "wrong", body).status_code == 401
    assert export(state, made["profile_token"], body).status_code == 401  # not the API token
    assert export(state, bearer, body, content_type="application/x-protobuf").status_code == 415
    assert export(state, bearer, {}, path="/v1/metrics").status_code == 200  # accepted, dropped
    assert export(state, bearer, body).status_code == 200
    rows = conn.execute("SELECT session_id, model, source, input_tokens FROM claude_calls")
    assert [tuple(r) for r in rows] == [(made["session_id"], CLASSIFIER, "main", 10)]
    assert api(state, "DELETE", f"/v1/sessions/{made['session_id']}").status_code == 200
    assert export(state, bearer, body).status_code == 401  # revoked with the session


# ---- the model check -------------------------------------------------------------------------


def claim(state: ServiceState, work: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = api(state, "POST", "/v1/review-queue", {}, work).json()["items"]
    return items[0]


def classify(state: ServiceState, agent_token: str, item: dict[str, Any],
             agent: str | None = None) -> dict[str, Any]:  # fmt: skip
    out: dict[str, Any] = api(state, "POST", f"/v1/claims/{item['id']}/classification",
                              {"claim_token": item["claim_token"], "classification": REQUEST,
                               "agent": agent or item["agent"]}, agent_token).json()  # fmt: skip
    return out


@pytest.fixture
def review(conn: sqlite3.Connection, state: ServiceState) -> tuple[str, str, str, str, str]:
    """A C address with one waiting item and a session: (item id, session, WORK token, bearer,
    agent token)."""
    clock = state.clock
    assert isinstance(clock, FakeClock)
    add(conn, clock, "c", "C")
    sid = waiting_item(conn, clock, "c")
    made = api(state, "POST", "/v1/sessions").json()
    return (sid, made["session_id"], made["profile_token"], made["telemetry_bearer"],
            made["agent_token"])  # fmt: skip


Review = tuple[str, str, str, str, str]


def test_a_submission_is_held_until_telemetry_catches_up(
    conn: sqlite3.Connection, state: ServiceState, review: Review
) -> None:
    sid, session, work, bearer, agent = review
    item = claim(state, work)
    assert item["agent"] == "ecf-classifier"
    assert classify(state, agent, item) == {"accepted": True, "pending": True, "errors": []}
    assert conn.execute("SELECT state FROM claims").fetchone()[0] == "held"
    assert api(state, "POST", "/v1/review-queue", {}, work).json()["items"] == []  # not again
    export(state, bearer, logs(api_request(1)))
    row = conn.execute("SELECT status, pinned_models FROM items").fetchone()
    assert (
        row["status"] == "awaiting_claude"
        and json.loads(row["pinned_models"])["classifier"]
        == claude_pins.effective(conn)["classifier"]
    )  # classified; on to the actor (OD-269)
    q = api(state, "POST", "/v1/review-queue", {}, work).json()
    assert q["results"] == [{"id": sid, "outcome": "awaiting_claude"}]
    assert q["items"][0]["need"] == "act"
    api(state, "DELETE", f"/v1/sessions/{session}")
    done = conn.execute("SELECT items, submitted, refused FROM claude_sessions").fetchone()
    assert tuple(done) == (1, 1, 0)


def test_work_on_another_model_is_refused_and_stops_the_review(
    conn: sqlite3.Connection, state: ServiceState, review: Review
) -> None:
    sid, session, work, bearer, agent = review
    export(state, bearer, logs(api_request(1, model="claude-opus-5-5")))
    item = claim(state, work)
    got = classify(state, agent, item)
    assert got["accepted"] is False and "claude-opus-5-5" in got["errors"][0]
    assert conn.execute("SELECT status, classification FROM items").fetchone()[1] is None
    notifier = state.notifier
    assert isinstance(notifier, FakeNotifier)
    assert [t for t, _ in notifier.sent] == ["[ecf-alert] System Error"]
    q = api(state, "POST", "/v1/review-queue", {}, work).json()
    assert q["results"] == [{"id": sid, "outcome": "model_refused"}] and q["items"] == []
    assert q["stopped"] == f"1 refused (model claude-opus-5-5, expected {CLASSIFIER})"
    api(state, "DELETE", f"/v1/sessions/{session}")
    st = api(state, "GET", "/v1/status").json()
    checks = {c.name: c for c in doctor.judge_claude(st)}
    check = checks["claude model check"]
    assert check.level is Level.WARN and "1 refused" in check.detail
    assert f"expected {CLASSIFIER}" in check.detail
    assert checks["claude pins"].level is Level.OK  # an address uses B or C (step 11)
    refused = conn.execute("SELECT data FROM audit WHERE event = 'claude.refused'").fetchone()[0]
    assert json.loads(refused)["model_check"] == "model"


def test_only_the_named_agents_server_reads_and_submits(
    conn: sqlite3.Connection, state: ServiceState, review: Review
) -> None:
    sid, _session, work, _bearer, agent = review
    item = claim(state, work)
    body = {"claim_token": item["claim_token"], "agent": item["agent"]}
    r = api(state, "POST", f"/v1/claims/{sid}/message", body, work)  # the main session's token
    assert r.status_code == 403 and "W-9" not in r.text  # OD-307
    assert classify(state, work, item)["code"] == "forbidden_profile"  # nor may it submit
    r = api(state, "POST", f"/v1/claims/{sid}/message", body | {"agent": "ecf-actor"}, agent)
    assert r.status_code == 403 and "is for ecf-classifier" in r.json()["detail"]
    assert api(state, "POST", "/v1/review-queue", {}, agent).status_code == 403  # nor claim
    r = api(state, "POST", f"/v1/claims/{sid}/message", {"claim_token": item["claim_token"]},
            agent)  # fmt: skip
    assert r.status_code == 400 and "agent" in r.json()["detail"]
    r = api(state, "POST", f"/v1/claims/{sid}/message", body, agent)
    assert r.status_code == 200 and r.json()["need"] == "classify"


def test_work_with_nothing_in_its_window_is_refused_when_the_session_ends(
    conn: sqlite3.Connection, state: ServiceState, review: Review
) -> None:
    sid, session, work, _bearer, agent = review
    assert classify(state, agent, claim(state, work))["pending"] is True
    assert api(state, "DELETE", f"/v1/sessions/{session}").status_code == 200
    assert conn.execute("SELECT state, outcome FROM claims").fetchone()[:] == (
        "released",
        "model_refused",
    )
    assert conn.execute("SELECT classification FROM items").fetchone()[0] is None
    made = api(state, "POST", "/v1/sessions").json()  # a later session can claim it again
    assert claim(state, made["profile_token"])["id"] == sid
    last = conn.execute("SELECT refused, refused_model FROM claude_sessions WHERE session_id = ?",
                        (session,)).fetchone()  # fmt: skip
    assert last["refused"] == 1 and last["refused_model"].startswith("none")


# ---- plan usage, stats, daily, retention -----------------------------------------------------


def test_plan_usage_and_figures(conn: sqlite3.Connection, state: ServiceState,
                                review: Review) -> None:  # fmt: skip
    _sid, session, work, bearer, agent = review
    assert api(state, "POST", "/v1/statusline", {"five_hour": 10, "seven_day": 90}, "cli-token"
               ).json()["code"] == "forbidden_profile"  # fmt: skip
    for five, seven in ((10, 90), (14.5, 95)):
        reading = {"five_hour": five, "seven_day": seven, "seven_day_resets_at": 1790500000}
        r = api(state, "POST", "/v1/statusline", reading, work)
        assert r.json() == {"recorded": True}
    export(state, bearer, logs(api_request(-1, source="main", input_tokens=100, output_tokens=10,
                                           duration_ms=1000),
                               api_request(1, input_tokens=2000, output_tokens=50,
                                           duration_ms=3000, cost_usd=0.01)))  # fmt: skip
    classify(state, agent, claim(state, work))
    api(state, "DELETE", f"/v1/sessions/{session}")
    row = conn.execute("SELECT five_hour_start, five_hour_end, seven_day_start, seven_day_end,"
                       " seven_day_resets FROM claude_sessions").fetchone()  # fmt: skip
    assert tuple(row) == (10.0, 14.5, 90.0, 95.0, "1790500000")
    made = api(state, "POST", "/v1/sessions").json()
    assert made["last_plan"]["seven_day"] == 95.0
    stats = api(state, "GET", "/v1/stats?hours=24").json()["claude"]
    assert stats["sessions"] == 2 and stats["items"] == 1 and stats["refused"] == 0
    assert stats["all"]["input_tokens"] == 2100 and stats["tokens_per_item"] == 2160
    by = {(g["model"], g["source"]): g for g in stats["groups"]}
    assert by[(CLASSIFIER, "agent")]["api_equivalent_usd"] == 0.01
    assert by[(CLASSIFIER, "main")]["seconds"] == {"median": 1.0, "p95": 1.0}
    assert "claude" not in api(state, "GET", "/v1/stats?hours=24&preset=A").json()
    clock = state.clock
    line = claude_usage.daily_line(conn, clock.now() - timedelta(days=1))
    assert line is not None and line.startswith("Claude: 2 review session(s), 1 email(s)")
    assert "7-day 95%" in line
    assert claude_usage.daily_line(conn, clock.now() + timedelta(days=1)) is None


def test_the_main_session_is_main_in_every_mode() -> None:
    """`sdk` in print mode, `repl_main_thread` interactive (seen in the step 13 run)."""
    assert {claude_usage.source_of(q) for q in ("main", "sdk", "repl_main_thread")} == {"main"}
    assert claude_usage.source_of(SUBAGENT) == "agent"


def test_retention_prunes_claude_usage(conn: sqlite3.Connection, clock: FakeClock) -> None:
    claude_usage.start_session(conn, clock, "old")
    claude_usage.record_calls(conn, clock, "old", [ApiCall(at(1), HAIKU, SUBAGENT, 5, 1)])
    clock.advance(400 * 86400)
    counts = retention.run(conn, clock)
    assert counts["claude_calls"] == 1 and counts["claude_sessions"] == 1


# ---- the status-line script ------------------------------------------------------------------


def test_the_status_line_sends_only_plan_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    session = {"session_id": "s", "transcript_path": "/x/projects/p.jsonl",
               "model": {"id": HAIKU}, "workspace": {"current_dir": "/w"},
               "rate_limits": {
                   "five_hour": {"used_percentage": 3, "resets_at": 17},
                   "seven_day": {"used_percentage": 41.6, "resets_at": "x"}}}  # fmt: skip
    assert sl.usage(session) == {"five_hour": 3, "five_hour_resets_at": 17, "seven_day": 41.6,
                                 "seven_day_resets_at": "x"}  # fmt: skip
    assert sl.usage({"rate_limits": None}) == {} and sl.usage([]) == {}
    sent: list[tuple[str, str, dict[str, Any]]] = []

    def send(sock: str, tok: str, body: dict[str, Any]) -> None:
        sent.append((sock, tok, body))

    monkeypatch.setattr(sl, "send", send)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(session)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setenv("ECF_PROFILE_TOKEN", "tok")
    assert sl.main(["/run/ecf.sock"]) == 0
    assert out.getvalue() == "ecf review · 5h 3% · 7d 42%\n"
    assert sent == [("/run/ecf.sock", "tok", sl.usage(session))]
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert sl.main(["/nowhere/ecf.sock"]) == 0  # never fails the status line


def test_the_status_line_survives_a_dead_service(monkeypatch: pytest.MonkeyPatch,
                                                 tmp_path: Path) -> None:  # fmt: skip
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"rate_limits": {"five_hour": {"used_percentage": 1}}})))  # fmt: skip
    monkeypatch.setenv("ECF_PROFILE_TOKEN", "tok")
    assert sl.main([str(tmp_path / "no.sock")]) == 0


def test_doctor_is_quiet_before_any_review() -> None:
    assert doctor.judge_claude({"claude": {"last_review": None}}) == []
    ok = doctor.judge_claude({"claude": {"last_review": {
        "ended_at": to_ts(FakeClock().now()), "refused": 0}}})  # fmt: skip
    assert ok[0].level is Level.OK
