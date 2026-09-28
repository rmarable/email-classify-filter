"""The socket HTTP API (SPEC §11.4, §15.1): one Starlette app, sync endpoints.

Callers authenticate with a bearer token:
- the CLI token (0600 file, rewritten at every service start): full CLI access;
- a session profile token issued to `ecf claude` (WORK) and revoked when it exits.
Routes declare which callers they accept. Decision and settings routes never accept a session
token (SPEC §10.4). `/v1/health` needs no token. Errors are RFC 9457 problem+json.
"""

from __future__ import annotations

import hmac
import os
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ecf import __version__
from ecf.errors import (
    PROBLEM_CONTENT_TYPE,
    EcfError,
    ForbiddenProfileError,
    InternalError,
    InvalidInputError,
    NotFoundError,
    UnauthorizedError,
)
from ecf.ids import new_random_id
from ecf_server.chat import FakeChat
from ecf_server.clock import FakeClock, SystemClock, to_ts
from ecf_server.log_bridge import log

API_VERSION = 1


class Caller(StrEnum):
    CLI = "cli"
    WORK = "work"  # an `ecf claude` session


@dataclass
class Session:
    session_id: str
    token: str
    profile: Caller
    created_at: str


@dataclass
class DevHooks:
    """What `ecf-server dev` exposes over `/v1/dev/*` (dev mode only)."""

    clock: FakeClock
    chat: FakeChat
    tick: Callable[[], None]


@dataclass
class ServiceState:
    install: str
    token: str
    started_at: str
    last_tick_at: str | None = None
    ticks: int = 0
    breaker: dict[str, Any] = field(default_factory=dict[str, Any])
    secret_store: dict[str, Any] = field(default_factory=dict[str, Any])
    pid: int = field(default_factory=os.getpid)
    mode: str = "local"
    dev: DevHooks | None = None
    sessions: dict[str, Session] = field(default_factory=dict[str, Session])
    lock: threading.Lock = field(default_factory=threading.Lock)

    def caller_for(self, token: str) -> tuple[Caller, Session | None]:
        if token and hmac.compare_digest(token, self.token):
            return Caller.CLI, None
        with self.lock:
            for s in self.sessions.values():
                if hmac.compare_digest(token, s.token):
                    return s.profile, s
        raise UnauthorizedError("missing or wrong token")


def _problem(err: EcfError, request: Request) -> JSONResponse:
    return JSONResponse(
        err.to_problem(instance=request.url.path),
        status_code=err.spec.http_status,
        media_type=PROBLEM_CONTENT_TYPE,
    )


Handler = Callable[[Request], JSONResponse]


def create_app(state: ServiceState) -> Starlette:
    def allow(*callers: Caller) -> Callable[[Handler], Handler]:
        def deco(handler: Handler) -> Handler:
            def wrapper(request: Request) -> JSONResponse:
                header = request.headers.get("authorization", "")
                given = (
                    header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
                )
                caller, _session = state.caller_for(given)
                if caller not in callers:
                    raise ForbiddenProfileError(f"not available to a {caller} caller")
                request.state.caller = caller
                return handler(request)

            return wrapper

        return deco

    def health(_request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    @allow(Caller.CLI, Caller.WORK)
    def status(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "version": __version__,
                "api_version": API_VERSION,
                "mode": state.mode,
                "install": state.install,
                "pid": state.pid,
                "started_at": state.started_at,
                "last_tick_at": state.last_tick_at,
                "ticks": state.ticks,
                "breaker": state.breaker,
                "secret_store": state.secret_store,
            }
        )

    @allow(Caller.CLI)
    def create_session(_request: Request) -> JSONResponse:
        now = to_ts(SystemClock().now())
        s = Session(new_random_id(), secrets.token_urlsafe(32), Caller.WORK, now)
        with state.lock:
            state.sessions[s.session_id] = s
        log.info("session.created", session_id=s.session_id, profile=s.profile.value)
        return JSONResponse(
            {"session_id": s.session_id, "profile_token": s.token, "profile": s.profile.value},
            status_code=201,
        )

    @allow(Caller.CLI)
    def delete_session(request: Request) -> JSONResponse:
        session_id = str(request.path_params["session_id"])
        with state.lock:
            removed = state.sessions.pop(session_id, None)
        if removed is None:
            raise NotFoundError(f"no session {session_id[:8]}")
        log.info("session.revoked", session_id=session_id)
        return JSONResponse({"revoked": session_id})

    def _dev() -> DevHooks:
        if state.dev is None:
            raise NotFoundError("dev routes exist only in `ecf-server dev`")
        return state.dev

    @allow(Caller.CLI)
    def dev_clock(request: Request) -> JSONResponse:
        dev = _dev()
        if request.method == "POST":
            seconds = float(request.query_params.get("advance", "0"))
            if not 0 <= seconds <= 400 * 86400:
                raise InvalidInputError("advance must be between 0 and 400 days of seconds")
            dev.clock.advance(seconds)
            dev.tick()
        return JSONResponse({"now": to_ts(dev.clock.now())})

    @allow(Caller.CLI)
    def dev_posts(request: Request) -> JSONResponse:
        dev = _dev()
        if request.method == "DELETE":
            dev.chat.clear()
        return JSONResponse({"posts": list(dev.chat.posts)})

    async def on_ecf_error(request: Request, exc: Exception) -> JSONResponse:
        return _problem(exc if isinstance(exc, EcfError) else InternalError(), request)

    async def on_not_found(request: Request, _exc: Exception) -> JSONResponse:
        return _problem(NotFoundError(f"no route {request.method} {request.url.path}"), request)

    async def on_unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.error("api.unhandled", error_type=type(exc).__name__, path=request.url.path)
        return _problem(InternalError(), request)

    return Starlette(
        routes=[
            Route("/v1/health", health, methods=["GET"]),
            Route("/v1/status", status, methods=["GET"]),
            Route("/v1/sessions", create_session, methods=["POST"]),
            Route("/v1/sessions/{session_id}", delete_session, methods=["DELETE"]),
            Route("/v1/dev/clock", dev_clock, methods=["GET", "POST"]),
            Route("/v1/dev/chat/posts", dev_posts, methods=["GET", "DELETE"]),
        ],
        exception_handlers={EcfError: on_ecf_error, 404: on_not_found, Exception: on_unexpected},
    )
