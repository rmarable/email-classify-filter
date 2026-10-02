"""The telemetry receiver and the Claude model check (V1.4 step 6; SPEC §7.5, §11.5, §13.4;
OD-268, OD-274): what the receiver keeps, binding a tool call to its model, held submissions,
refusals, plan usage from the status line, and Claude's figures in stats, the daily summary,
retention and doctor."""

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
from ecf_server.telemetry import SUBAGENT, ApiCall, Telemetry
from tests.test_claude_review import REQUEST, add, waiting_item

HAIKU = "claude-haiku-4-5-20251001"


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


def api_request(
    seq: int, model: str = HAIKU, source: str = SUBAGENT, **more: Any
) -> dict[str, Any]:
    return record(event__name="claude_code.api_request", event__sequence=seq, model=model,
                  query_source=source, user__email="someone@example.com", **more)  # fmt: skip


def tool_event(seq: int, tool_use_id: str) -> dict[str, Any]:
    return record(event__name="tool_decision", event__sequence=seq, tool_use_id=tool_use_id,
                  tool_name="mcp_tool")  # fmt: skip


# ---- parsing and binding -------------------------------------------------------------------


def test_only_numbers_models_and_codes_are_kept() -> None:
    body = logs(api_request(3, input_tokens=1200, output_tokens=80, cache_read_tokens=900,
                            duration_ms=2100, cost_usd=0.0123, prompt="<REDACTED>"),
                tool_event(4, "toolu_A"), tool_event(9, "toolu_A"),
                record(event__name="api_request", model="evil model\nname", query_source=7),
                {"attributes": "not a list"})  # fmt: skip
    calls, tools = telemetry.parse_logs(body)
    assert calls[0] == ApiCall(3, HAIKU, SUBAGENT, 1200, 80, 900, None, 2100, 0.0123)
    assert calls[1].model == "unknown" and calls[1].source == "unknown" and calls[1].seq is None
    assert tools == {"toolu_A": 4}  # the first event of the call
    assert "someone@example.com" not in repr((calls, tools))
    assert telemetry.parse_logs({"resourceLogs": "x"}) == ([], {})


def test_a_call_binds_to_the_request_just_before_it() -> None:
    tel = Telemetry()
    tel.open("s1")
    tel.add("s1", [ApiCall(1, "claude-sonnet-5-5", "main"), ApiCall(5, HAIKU, SUBAGENT)],
            {"toolu_A": 6, "toolu_B": 3, "toolu_C": 0})  # fmt: skip
    assert tel.seen("s1", "toolu_A") == telemetry.Seen(HAIKU, SUBAGENT)
    assert tel.seen("s1", "toolu_B") == telemetry.Seen("claude-sonnet-5-5", "main")
    assert tel.seen("s1", "toolu_C") is None  # nothing before it
    assert tel.seen("s1", "toolu_unknown") is None and tel.seen("s2", "toolu_A") is None


def test_seen_waits_for_the_events_to_arrive() -> None:
    tel = Telemetry()
    tel.open("s1")
    later = threading.Timer(0.1, lambda: tel.add("s1", [ApiCall(1, HAIKU, SUBAGENT)],
                                                 {"toolu_A": 2}))  # fmt: skip
    later.start()
    started = time.monotonic()
    assert tel.seen("s1", "toolu_A", wait_s=5) == telemetry.Seen(HAIKU, SUBAGENT)
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
    body = logs(api_request(1, source="main", input_tokens=10, output_tokens=2))
    assert export(state, "wrong", body).status_code == 401
    assert export(state, made["profile_token"], body).status_code == 401  # not the API token
    assert export(state, bearer, body, content_type="application/x-protobuf").status_code == 415
    assert export(state, bearer, {}, path="/v1/metrics").status_code == 200  # accepted, dropped
    assert export(state, bearer, body).status_code == 200
    rows = conn.execute("SELECT session_id, model, source, input_tokens FROM claude_calls")
    assert [tuple(r) for r in rows] == [(made["session_id"], HAIKU, "main", 10)]
    assert api(state, "DELETE", f"/v1/sessions/{made['session_id']}").status_code == 200
    assert export(state, bearer, body).status_code == 401  # revoked with the session


# ---- the model check -------------------------------------------------------------------------


def claim(state: ServiceState, work: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = api(state, "POST", "/v1/review-queue", {}, work).json()["items"]
    return items[0]


def classify(state: ServiceState, work: str, item: dict[str, Any], call: str) -> dict[str, Any]:
    out: dict[str, Any] = api(state, "POST", f"/v1/claims/{item['id']}/classification",
                              {"claim_token": item["claim_token"], "classification": REQUEST,
                               "tool_use_id": call}, work).json()  # fmt: skip
    return out


@pytest.fixture
def review(conn: sqlite3.Connection, state: ServiceState) -> tuple[str, str, str, str]:
    """A C address with one waiting item and a session: (item id, session, WORK token, bearer)."""
    clock = state.clock
    assert isinstance(clock, FakeClock)
    add(conn, clock, "c", "C")
    sid = waiting_item(conn, clock, "c")
    made = api(state, "POST", "/v1/sessions").json()
    return sid, made["session_id"], made["profile_token"], made["telemetry_bearer"]


def test_a_submission_is_held_until_telemetry_binds_it(
    conn: sqlite3.Connection, state: ServiceState, review: tuple[str, str, str, str]
) -> None:
    sid, session, work, bearer = review
    item = claim(state, work)
    assert classify(state, work, item, "toolu_1") == {"accepted": True, "pending": True,
                                                      "errors": []}  # fmt: skip
    assert conn.execute("SELECT state FROM claims").fetchone()[0] == "held"
    assert api(state, "POST", "/v1/review-queue", {}, work).json()["items"] == []  # not again
    export(state, bearer, logs(api_request(1), tool_event(2, "toolu_1")))
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


def test_a_call_from_another_model_is_refused_and_stops_the_review(
    conn: sqlite3.Connection, state: ServiceState, review: tuple[str, str, str, str]
) -> None:
    sid, session, work, bearer = review
    export(state, bearer, logs(api_request(1, model="claude-opus-5-5"),
                               tool_event(2, "toolu_1")))  # fmt: skip
    item = claim(state, work)
    got = classify(state, work, item, "toolu_1")
    assert got["accepted"] is False and "claude-opus-5-5" in got["errors"][0]
    assert conn.execute("SELECT status, classification FROM items").fetchone()[1] is None
    notifier = state.notifier
    assert isinstance(notifier, FakeNotifier)
    assert [t for t, _ in notifier.sent] == ["[ecf-alert] System Error"]
    q = api(state, "POST", "/v1/review-queue", {}, work).json()
    assert q["results"] == [{"id": sid, "outcome": "model_refused"}] and q["items"] == []
    assert q["stopped"] == f"1 refused (model claude-opus-5-5, expected {HAIKU})"
    api(state, "DELETE", f"/v1/sessions/{session}")
    st = api(state, "GET", "/v1/status").json()
    checks = {c.name: c for c in doctor.judge_claude(st)}
    check = checks["claude model check"]
    assert check.level is Level.WARN and "1 refused" in check.detail
    assert "expected claude-haiku" in check.detail
    assert checks["claude pins"].level is Level.OK  # an address uses B or C (step 11)
    refused = conn.execute("SELECT data FROM audit WHERE event = 'claude.refused'").fetchone()[0]
    assert json.loads(refused)["model_check"] == "model"


def test_the_main_session_gets_no_message_text(
    conn: sqlite3.Connection, state: ServiceState, review: tuple[str, str, str, str]
) -> None:
    sid, _session, work, bearer = review
    export(state, bearer, logs(api_request(1, source="main"), tool_event(2, "toolu_1")))
    item = claim(state, work)
    r = api(state, "POST", f"/v1/claims/{sid}/message",
            {"claim_token": item["claim_token"], "tool_use_id": "toolu_1"}, work)  # fmt: skip
    assert r.status_code == 403 and "W-9" not in r.text  # OD-274
    got = classify(state, work, item, "toolu_1")  # nor may it submit
    assert got["accepted"] is False and "main session" in got["errors"][0]


def test_unbound_work_is_refused_when_the_session_ends(
    conn: sqlite3.Connection, state: ServiceState, review: tuple[str, str, str, str]
) -> None:
    sid, session, work, _bearer = review
    assert classify(state, work, claim(state, work), "toolu_9")["pending"] is True
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


def test_a_submission_without_a_call_id_is_refused(
    state: ServiceState, review: tuple[str, str, str, str]
) -> None:
    _sid, _session, work, _bearer = review
    item = claim(state, work)
    r = api(state, "POST", f"/v1/claims/{item['id']}/classification",
            {"claim_token": item["claim_token"], "classification": REQUEST}, work)  # fmt: skip
    assert r.status_code == 400 and "tool call ID" in r.json()["detail"]


# ---- plan usage, stats, daily, retention -----------------------------------------------------


def test_plan_usage_and_figures(conn: sqlite3.Connection, state: ServiceState,
                                review: tuple[str, str, str, str]) -> None:  # fmt: skip
    _sid, session, work, bearer = review
    assert api(state, "POST", "/v1/statusline", {"five_hour": 10, "seven_day": 90}, "cli-token"
               ).json()["code"] == "forbidden_profile"  # fmt: skip
    for five, seven in ((10, 90), (14.5, 95)):
        reading = {"five_hour": five, "seven_day": seven, "seven_day_resets_at": 1790500000}
        r = api(state, "POST", "/v1/statusline", reading, work)
        assert r.json() == {"recorded": True}
    export(state, bearer, logs(api_request(1, source="main", input_tokens=100, output_tokens=10,
                                           duration_ms=1000),
                               api_request(3, input_tokens=2000, output_tokens=50,
                                           duration_ms=3000, cost_usd=0.01),
                               tool_event(4, "toolu_1")))  # fmt: skip
    classify(state, work, claim(state, work), "toolu_1")
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
    assert by[(HAIKU, "agent")]["api_equivalent_usd"] == 0.01
    assert by[(HAIKU, "main")]["seconds"] == {"median": 1.0, "p95": 1.0}
    assert "claude" not in api(state, "GET", "/v1/stats?hours=24&preset=A").json()
    clock = state.clock
    line = claude_usage.daily_line(conn, clock.now() - timedelta(days=1))
    assert line is not None and line.startswith("Claude: 2 review session(s), 1 email(s)")
    assert "7-day 95%" in line
    assert claude_usage.daily_line(conn, clock.now() + timedelta(days=1)) is None


def test_retention_prunes_claude_usage(conn: sqlite3.Connection, clock: FakeClock) -> None:
    claude_usage.start_session(conn, clock, "old")
    claude_usage.record_calls(conn, clock, "old", [ApiCall(1, HAIKU, SUBAGENT, 5, 1)])
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
