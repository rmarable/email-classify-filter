"""The loopback telemetry receiver (SPEC §11.5; V1.4 step 6; OD-117): a separate two-route app,
`/v1/logs` and `/v1/metrics`, OTLP over HTTP with JSON bodies only, on its own `uvicorn.Server`
bound to 127.0.0.1 on a port the system picks. It is never the main API, which stays on the Unix
socket. Each `ecf claude` session has its own bearer token (`OTEL_EXPORTER_OTLP_HEADERS`).

Logs carry what ecf keeps (`telemetry.parse_logs`); metrics are accepted and dropped, since the
per-request log events carry the same figures. After each logs export, the session's held
submissions that telemetry now binds are settled (`claude_review.settle`, or `claude_eval.settle`
for `/ecf-eval`'s).
"""

from __future__ import annotations

import json
import socket
import sqlite3
from typing import TYPE_CHECKING, Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ecf_server import claude_eval, claude_review, claude_usage, telemetry
from ecf_server.log_bridge import log

if TYPE_CHECKING:
    from ecf_server.api import ServiceState

MAX_BODY = 8 * 1024 * 1024


def _reply(status: int, message: str = "") -> JSONResponse:
    return JSONResponse({"message": message} if message else {}, status_code=status)


def settle_bound(state: ServiceState, session_id: str, *, final: bool = False) -> None:
    """Settle the session's holds that telemetry binds; at session end (`final`) the rest are
    refused as unbound. One failure doesn't stop the others."""
    tel = state.telemetry
    pending = (tel.take_all(session_id) if final
               else [(h, s) for h, s in tel.take_bound(session_id)])  # fmt: skip
    if not pending:
        return
    conn = state.connect()
    try:
        for hold, seen in pending:
            try:
                settle_one(state, conn, hold, seen)
            except Exception as exc:  # e.g. the item moved on while held: its outcome says so
                log.warning("telemetry.settle_failed", error_type=type(exc).__name__)
    finally:
        conn.close()


def settle_one(state: ServiceState, conn: sqlite3.Connection, hold: telemetry.Hold,
               seen: telemetry.Seen | None) -> dict[str, Any]:  # fmt: skip
    """Settle one held submission, `/ecf-review`'s or `/ecf-eval`'s."""
    if hold.kind == "eval":
        return claude_eval.settle(state.connect, state.clock, state.notifier, state.telemetry,
                                  hold, seen)  # fmt: skip
    return claude_review.settle(conn, state.clock, state.notifier, state.telemetry, hold, seen)


def create_receiver(state: ServiceState) -> Starlette:
    def _session(request: Request) -> str | None:
        header = request.headers.get("authorization", "")
        bearer = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        return state.telemetry.session_for(bearer) if bearer else None

    def _json(request: Request, body: bytes) -> Any:
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype != "application/json" or request.headers.get("content-encoding"):
            return None
        try:
            return json.loads(body)
        except ValueError:
            return None

    async def logs(request: Request) -> JSONResponse:
        session_id = _session(request)
        if session_id is None:
            return _reply(401, "unknown session")
        body = await request.body()
        if len(body) > MAX_BODY:
            return _reply(413, "too large")
        doc = _json(request, body)
        if doc is None:
            return _reply(415, "OTLP/HTTP with a JSON body only")
        calls, tools = telemetry.parse_logs(doc)
        state.telemetry.add(session_id, calls, tools)
        if calls and state.db_path is not None:
            conn = state.connect()
            try:
                claude_usage.record_calls(conn, state.clock, session_id, calls)
            finally:
                conn.close()
        settle_bound(state, session_id)
        return _reply(200)

    async def metrics(request: Request) -> JSONResponse:
        if _session(request) is None:
            return _reply(401, "unknown session")
        body = await request.body()
        if len(body) > MAX_BODY:
            return _reply(413, "too large")
        if _json(request, body) is None:
            return _reply(415, "OTLP/HTTP with a JSON body only")
        return _reply(200)

    return Starlette(routes=[Route("/v1/logs", logs, methods=["POST"]),
                             Route("/v1/metrics", metrics, methods=["POST"])])  # fmt: skip


def bind() -> socket.socket:
    """A loopback TCP socket on a port the system picks."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock


def server(state: ServiceState) -> uvicorn.Server:
    return uvicorn.Server(uvicorn.Config(create_receiver(state), log_config=None,
                                         lifespan="off", access_log=False))  # fmt: skip
