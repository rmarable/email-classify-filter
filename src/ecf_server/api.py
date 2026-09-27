"""The socket HTTP API (SPEC §11.4, §15.1): one Starlette app, sync endpoints.

Every route except `/v1/health` needs the CLI bearer token from the 0600 token file. Errors are
RFC 9457 problem+json. M1 wraps the same app with Mangum.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ecf import __version__
from ecf.errors import (
    PROBLEM_CONTENT_TYPE,
    EcfError,
    InternalError,
    NotFoundError,
    UnauthorizedError,
)
from ecf_server.log_bridge import log

API_VERSION = 1


@dataclass
class ServiceState:
    install: str
    token: str
    started_at: str
    last_tick_at: str | None = None
    ticks: int = 0
    breaker: dict[str, Any] = field(default_factory=dict[str, Any])
    pid: int = field(default_factory=os.getpid)


def _problem(err: EcfError, request: Request) -> JSONResponse:
    return JSONResponse(
        err.to_problem(instance=request.url.path),
        status_code=err.spec.http_status,
        media_type=PROBLEM_CONTENT_TYPE,
    )


def create_app(state: ServiceState) -> Starlette:
    def authed(handler: Callable[[Request], JSONResponse]) -> Callable[[Request], JSONResponse]:
        def wrapper(request: Request) -> JSONResponse:
            header = request.headers.get("authorization", "")
            given = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
            if not given or not hmac.compare_digest(given, state.token):
                raise UnauthorizedError("missing or wrong CLI token")
            return handler(request)

        return wrapper

    def health(_request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    def status(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "version": __version__,
                "api_version": API_VERSION,
                "install": state.install,
                "pid": state.pid,
                "started_at": state.started_at,
                "last_tick_at": state.last_tick_at,
                "ticks": state.ticks,
                "breaker": state.breaker,
            }
        )

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
            Route("/v1/status", authed(status), methods=["GET"]),
        ],
        exception_handlers={EcfError: on_ecf_error, 404: on_not_found, Exception: on_unexpected},
    )
