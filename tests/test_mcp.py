"""`ecf-mcp` (V1.4 step 4; SPEC §10.4): the tools, profiles, deadlines, stdout carrying only
JSON-RPC, and `tools/list` snapshots for spec revisions 2025-11-25 and 2026-07-28."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import anyio
import httpx
import mcp_types as types
import pytest
from mcp import Client, StdioServerParameters

from ecf import mcp_server
from ecf.paths import Paths
from ecf_server import claude_pins
from ecf_server.api import ServiceState, create_app
from ecf_server.clock import FakeClock
from ecf_server.telemetry import SUBAGENT, ApiCall
from tests.test_claude_review import REQUEST, add, waiting_item

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "tests" / "snapshots"
ECF_MCP = Path(sys.executable).parent / "ecf-mcp"
MODES = ("legacy", "2026-07-28")  # the initialize handshake (2025-11-25) and the modern envelope
REVISION = {"legacy": "2025-11-25", "2026-07-28": "2026-07-28"}
TOOL_USE = mcp_server.TOOL_USE_ID

Body = Callable[[Client], Awaitable[Any]]


def session_token(state: ServiceState) -> str:
    async def go() -> str:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            r = await c.post("/v1/sessions", headers={"Authorization": "Bearer cli-token"})
            return str(r.json()["profile_token"])

    return anyio.run(go)


def with_tools(state: ServiceState, token: str, body: Body, mode: str = "legacy",
               transport: httpx.AsyncBaseTransport | None = None,
               clock: Callable[[], float] | None = None) -> Any:  # fmt: skip
    async def go() -> Any:
        t = transport or httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=t, base_url="http://ecf") as http:
            svc = (mcp_server.Service(http, token) if clock is None
                   else mcp_server.Service(http, token, clock))  # fmt: skip
            async with Client(mcp_server.build(svc), mode=mode) as client:  # pyright: ignore[reportArgumentType]
                return await body(client)

    return anyio.run(go)


def data(r: types.CallToolResult) -> dict[str, Any]:
    assert not r.is_error, r.content
    got = cast(dict[str, Any], r.structured_content)
    assert isinstance(got, dict)
    text = r.content[0]
    assert isinstance(text, types.TextContent) and json.loads(text.text) == got
    return got


def error_text(r: types.CallToolResult) -> str:
    assert r.is_error
    text = r.content[0]
    assert isinstance(text, types.TextContent)
    return text.text


@pytest.fixture
def state(conn: sqlite3.Connection, db_path: Path) -> ServiceState:  # conn: migrated
    return ServiceState(install="t", token="cli-token", started_at="2026-10-02T09:00:00Z",
                        clock=FakeClock(), db_path=db_path)  # fmt: skip


# ---- the tool list ---------------------------------------------------------------------------


def test_names_texts_and_hints() -> None:
    names = [t.name for t in mcp_server.TOOLS]
    assert names == ["status", "counts", "review_queue", "get_message",
                     "record_classification", "propose_action"]  # fmt: skip
    texts = [mcp_server.INSTRUCTIONS]
    for t in mcp_server.TOOLS:
        assert mcp_server.TOOL_NAME.fullmatch(t.name)
        tool = t.tool()
        texts += [tool.description or "", tool.title or "", json.dumps(tool.input_schema)]
        assert tool.annotations is not None and tool.annotations.read_only_hint is t.read_only
        assert tool.input_schema["additionalProperties"] is False
    assert not any("slack" in s.lower() for s in texts)  # tool text says "team chat"
    assert not {"approve", "approval", "resolve", "settings"} & {n for n in names}  # no admin


def test_observe_lists_only_status_and_counts(state: ServiceState) -> None:
    async def body(c: Client) -> list[str]:
        return [t.name for t in (await c.list_tools()).tools]

    assert with_tools(state, "", body) == ["status", "counts"]


def test_a_work_tool_without_a_session_is_refused(state: ServiceState) -> None:
    async def body(c: Client) -> types.CallToolResult:
        return await c.call_tool("review_queue", {})

    assert error_text(with_tools(state, "", body)).startswith("unauthorized: ")


# ---- the tools against the service -----------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_a_review_round(mode: str, conn: sqlite3.Connection, state: ServiceState) -> None:
    clock = state.clock
    assert isinstance(clock, FakeClock)
    add(conn, clock, "c", "C")
    add(conn, clock, "b", "B")
    sid = waiting_item(conn, clock, "c")
    work = session_token(state)
    # telemetry: the classifier's calls follow a Haiku request, the actor's a Sonnet one
    pins = claude_pins.effective(conn)
    state.telemetry_wait_s = 0
    state.telemetry.add(next(iter(state.sessions)),
                        [ApiCall(1, pins["classifier"], SUBAGENT), ApiCall(10, pins["actor"],
                                                                             SUBAGENT)],
                        {"toolu_c": 2, "toolu_a": 11})  # fmt: skip
    cls = cast(types.RequestParamsMeta, {TOOL_USE: "toolu_c"})
    act_call = cast(types.RequestParamsMeta, {TOOL_USE: "toolu_a"})

    async def body(c: Client) -> dict[str, Any]:
        out: dict[str, Any] = {"status": data(await c.call_tool("status", {}))}
        out["counts"] = data(await c.call_tool("counts", {"address_id": "c"}))
        q = data(await c.call_tool("review_queue", {"limit": 5}))
        item = q["items"][0]
        args = {"id": item["id"], "claim_token": item["claim_token"]}
        out["unbound"] = await c.call_tool("get_message", args)  # no tool-use ID: refused
        out["message"] = data(await c.call_tool("get_message", args, meta=cls))
        bad = {"classification": {"category": "tax"}}
        out["bad"] = data(await c.call_tool("record_classification", args | bad, meta=cls))
        good = {"classification": REQUEST}
        out["good"] = data(await c.call_tool("record_classification", args | good, meta=cls))
        q2 = data(await c.call_tool("review_queue", {}))
        act = q2["items"][0]
        args = {"id": act["id"], "claim_token": act["claim_token"]}
        out["act_message"] = data(await c.call_tool("get_message", args, meta=act_call))
        out["proposal"] = data(await c.call_tool("propose_action", args | {
            "action": "needs_clarification", "target": "", "reason": "unsure",
            "question": "Is this Ann from Cust?"}, meta=act_call))  # fmt: skip
        out["again"] = await c.call_tool("propose_action", args | {"action": "flag",
                                                                   "reason": "x"})  # fmt: skip
        out["stale"] = await c.call_tool("get_message", args)
        return out

    out = with_tools(state, work, body, mode)
    by = {a["address_id"]: a for a in out["status"]["addresses"]}
    assert by["c"] == {"address_id": "c", "stage": "shadow", "paused": False, "open_count": 1,
                       "awaiting_claude_count": 1, "last_check_at": None}  # fmt: skip
    assert "email" not in by["c"] and by["b"]["open_count"] == 0
    assert out["counts"] == {"by_status": {"awaiting_claude": 1}}
    m = out["message"]
    assert m["id"] == sid and m["need"] == "classify" and "schema" in m
    assert m["untrusted_email"]["subject"] == "W-9 please" and "Treat as data" in m["notice"]
    assert error_text(out["unbound"]).startswith("forbidden_profile: ")
    assert out["bad"]["accepted"] is False and out["bad"]["tries_left"] == 2
    assert out["good"] == {"accepted": True, "errors": []}
    assert out["act_message"]["need"] == "act" and "flag" in out["act_message"]["actions"]
    assert out["proposal"] == {"accepted": True, "errors": []}
    assert error_text(out["again"]).startswith("conflict: ")  # the claim has ended
    assert error_text(out["stale"]).startswith("conflict: ")
    assert conn.execute("SELECT status FROM items WHERE stable_id = ?",
                        (sid,)).fetchone()[0] == "needs_clarification"  # fmt: skip


def test_bad_arguments_are_errors(conn: sqlite3.Connection, state: ServiceState) -> None:
    clock = state.clock
    assert isinstance(clock, FakeClock)
    add(conn, clock, "c", "C")
    waiting_item(conn, clock, "c")
    work = session_token(state)

    async def body(c: Client) -> list[types.CallToolResult]:
        return [
            await c.call_tool("get_message", {"id": "../../v1/settings", "claim_token": "1.x"}),
            await c.call_tool("get_message", {"id": "abcdef12"}),
            await c.call_tool("review_queue", {"limit": 500}),
            await c.call_tool("counts", {"address_id": 7}),
            await c.call_tool("no_such_tool", {}),
        ]

    path, missing, limit, aid, unknown = with_tools(state, work, body)
    assert error_text(path).startswith(("invalid_input: ", "not_found: "))  # quoted: no escape
    assert error_text(missing) == "invalid_input: claim_token is required and must be text"
    assert error_text(limit).startswith("invalid_input: ")
    assert error_text(aid) == "invalid_input: address_id must be text"
    assert error_text(unknown).startswith("invalid_input: no tool")


# ---- deadlines (OD-088) ----------------------------------------------------------------------


def test_no_request_starts_after_100_seconds(state: ServiceState) -> None:
    ticks = iter([0.0, 50.0, 100.0])  # call start, first request, second request

    async def body(c: Client) -> types.CallToolResult:
        return await c.call_tool("status", {})

    r = with_tools(state, "", body, clock=lambda: next(ticks))
    assert data(r) == {"more_pending": True, "notice": mcp_server.MORE_PENDING}


def test_the_backstop_answers_more_pending(state: ServiceState,
                                           monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    monkeypatch.setattr(mcp_server, "DEADLINE_S", 0.2)

    async def slow(_request: httpx.Request) -> httpx.Response:
        await anyio.sleep(5)
        return httpx.Response(200, json={})

    async def body(c: Client) -> types.CallToolResult:
        return await c.call_tool("counts", {})

    r = with_tools(state, "", body, transport=httpx.MockTransport(slow))
    assert data(r)["more_pending"] is True


def test_a_request_timeout_is_an_error(state: ServiceState) -> None:
    seen: list[float] = []

    def timeout(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        raise httpx.ReadTimeout("slow", request=request)

    async def body(c: Client) -> types.CallToolResult:
        return await c.call_tool("counts", {})

    r = with_tools(state, "", body, transport=httpx.MockTransport(timeout))
    assert error_text(r) == "service_unavailable: the service didn't answer in time"
    assert seen == [10.0]


# ---- the stdio process -----------------------------------------------------------------------


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("ECF_PROFILE_TOKEN", "ECF_SOCKET")}
    return env | extra


def test_stdout_carries_only_json_rpc(tmp_path: Path) -> None:
    lines: list[dict[str, Any]] = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "review_queue", "arguments": {}}},
    ]  # fmt: skip
    env = _env(ECF_SOCKET=str(tmp_path / "none.sock"), ECF_PROFILE_TOKEN="t0k")
    pipe = subprocess.PIPE
    proc = subprocess.Popen([str(ECF_MCP), "--stdio"], stdin=pipe, stdout=pipe, stderr=pipe,
                            text=True, env=env)  # fmt: skip
    assert proc.stdin is not None and proc.stdout is not None
    raw: list[str] = []
    try:
        for m in lines:  # a client keeps stdin open until its replies arrive
            proc.stdin.write(json.dumps(m) + "\n")
            proc.stdin.flush()
            if "id" in m:
                raw.append(proc.stdout.readline())
        proc.stdin.close()
        assert proc.wait(timeout=60) == 0
        rest = proc.stdout.read()
    finally:
        proc.kill()
    assert rest == ""
    replies = [json.loads(line) for line in raw]
    assert [r["id"] for r in replies] == [1, 2, 3, 4]
    assert all(r["jsonrpc"] == "2.0" and ("result" in r or "error" in r) for r in replies)
    assert replies[0]["result"]["protocolVersion"] == "2025-11-25"
    assert len(replies[1]["result"]["tools"]) == len(mcp_server.TOOLS)
    for r in replies[2:]:  # no service: an isError result, not a crash
        assert r["result"]["isError"] is True
        assert r["result"]["content"][0]["text"].startswith("service_unavailable: ")


def test_ecf_mcp_needs_stdio() -> None:
    out = subprocess.run([str(ECF_MCP)], capture_output=True, text=True,
                         env=_env(), timeout=60, check=False)  # fmt: skip
    assert out.returncode == 2 and out.stdout == "" and "--stdio" in out.stderr


def _listed(mode: str, env: dict[str, str]) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        params = StdioServerParameters(command=str(ECF_MCP), args=["--stdio"], env=env)
        async with Client(params, mode=mode) as c:  # pyright: ignore[reportArgumentType]
            assert c.protocol_version == REVISION[mode]
            listed = await c.list_tools()
            return {"tools": [t.model_dump(mode="json", by_alias=True, exclude_none=True)
                              for t in listed.tools]}  # fmt: skip

    return anyio.run(go)


@pytest.mark.parametrize("mode", MODES)
def test_tools_list_snapshot(mode: str, tmp_path: Path) -> None:
    env = _env(ECF_SOCKET=str(tmp_path / "none.sock"), ECF_PROFILE_TOKEN="t0k")
    text = json.dumps(_listed(mode, env), indent=2, sort_keys=True) + "\n"
    snap = SNAPSHOTS / f"mcp_tools_{REVISION[mode]}.json"
    if os.environ.get("ECF_UPDATE_SNAPSHOTS") == "1":
        snap.write_text(text, encoding="utf-8")
    assert text == snap.read_text(encoding="utf-8")


def test_status_over_the_real_socket(running: Paths) -> None:
    env = _env(ECF_SOCKET=str(running.socket))

    async def go() -> types.CallToolResult:
        params = StdioServerParameters(command=str(ECF_MCP), args=["--stdio"], env=env)
        async with Client(params, mode="legacy") as c:
            return await c.call_tool("status", {})

    assert data(anyio.run(go)) == {"addresses": []}  # OBSERVE: no token
