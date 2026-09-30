"""The socket HTTP API (SPEC §11.4, §15.1): one Starlette app, sync endpoints.

Callers authenticate with a bearer token:
- the CLI token (0600 file, rewritten at every service start): full CLI access;
- a session profile token issued to `ecf claude` (WORK) and revoked when it exits.
Routes declare which callers they accept. Decision and settings routes never accept a session
token (SPEC §10.4). `/v1/health` needs no token. Errors are RFC 9457 problem+json.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sqlite3
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import anyio.from_thread
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ecf import __version__
from ecf.errors import (
    PROBLEM_CONTENT_TYPE,
    EcfError,
    ForbiddenProfileError,
    InternalError,
    InvalidInputError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from ecf.ids import new_random_id
from ecf_server import _slack, addresses, audit, checks, db, health, slack_admin, stepup
from ecf_server.chat import FakeChat
from ecf_server.clock import Clock, FakeClock, SystemClock, to_ts
from ecf_server.log_bridge import log
from ecf_server.notify import Notifier, NullNotifier
from ecf_server.secretstore import SecretStore
from ecf_server.stepper import Stepper

API_VERSION = 1


class Caller(StrEnum):
    CLI = "cli"
    WORK = "work"  # an `ecf claude` session


@dataclass
class Session:
    session_id: str
    token: str = field(repr=False)
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
    token: str = field(repr=False)
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
    clock: Clock = field(default_factory=SystemClock, repr=False)
    db_path: Path | None = None
    secrets: SecretStore | None = field(default=None, repr=False)
    mail_factory: addresses.MailFactory | None = field(default=None, repr=False)
    notifier: Notifier = field(default_factory=NullNotifier, repr=False)
    stepper: Stepper | None = field(default=None, repr=False)  # None: step-up is refused
    slack: dict[str, Any] = field(default_factory=lambda: {"installed": False})  # live, runtime's
    slack_web: Callable[[str], Any] = field(default=_slack.Web, repr=False)  # a fake in tests
    slack_reload: Callable[[], None] = field(default=lambda: None, repr=False)  # the runtime's

    def connect(self) -> sqlite3.Connection:
        if self.db_path is None:
            raise ServiceUnavailableError("the service has no database yet")
        return db.connect(self.db_path)

    def store(self) -> SecretStore:
        if self.secrets is None:
            detail = self.secret_store.get("detail")
            raise ServiceUnavailableError(
                f"no usable secret store{f': {detail}' if detail else ''}"
            )
        return self.secrets

    def caller_for(self, token: str) -> tuple[Caller, Session | None]:
        # compare bytes: compare_digest raises on non-ASCII str (headers decode as latin-1)
        given = token.encode("utf-8", "surrogateescape")
        if token and hmac.compare_digest(given, self.token.encode()):
            return Caller.CLI, None
        with self.lock:
            for s in self.sessions.values():
                if hmac.compare_digest(given, s.token.encode()):
                    return s.profile, s
        raise UnauthorizedError("missing or wrong token")


def _problem(err: EcfError, request: Request) -> JSONResponse:
    return JSONResponse(
        err.to_problem(instance=request.url.path),
        status_code=err.spec.http_status,
        media_type=PROBLEM_CONTENT_TYPE,
    )


Handler = Callable[[Request], Response]


def create_app(state: ServiceState) -> Starlette:
    def allow(*callers: Caller) -> Callable[[Handler], Handler]:
        def deco(handler: Handler) -> Handler:
            def wrapper(request: Request) -> Response:
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
                "addresses": _address_states(state),
                "alerts": _alerts(state),
                "slack": dict(state.slack),
            }
        )

    @allow(Caller.CLI)
    def create_session(_request: Request) -> JSONResponse:
        now = to_ts(state.clock.now())
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
            try:
                seconds = float(request.query_params.get("advance", "0"))
            except ValueError as exc:
                raise InvalidInputError("advance must be a number of seconds") from exc
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
            *_address_routes(state, allow),
            *_check_routes(state, allow),
            *_stepup_routes(state, allow),
            *_slack_routes(state, allow),
            Route("/v1/dev/clock", dev_clock, methods=["GET", "POST"]),
            Route("/v1/dev/chat/posts", dev_posts, methods=["GET", "DELETE"]),
        ],
        exception_handlers={EcfError: on_ecf_error, 404: on_not_found, Exception: on_unexpected},
    )


Allow = Callable[..., Callable[[Handler], Handler]]
MAX_ROUNDS = 100  # `--until-empty` stops after this many checks per address


def _address_states(state: ServiceState) -> list[dict[str, Any]]:
    if state.db_path is None:
        return []
    conn = state.connect()
    try:
        return checks.states(conn)
    finally:
        conn.close()


def _alerts(state: ServiceState) -> list[dict[str, Any]]:
    if state.db_path is None:
        return []
    conn = state.connect()
    try:
        return health.open_alerts(conn)
    finally:
        conn.close()


def _check_lines(
    state: ServiceState,
    ids: list[str],
    until_empty: bool,
    secrets: SecretStore,
    factory: addresses.MailFactory,
) -> Iterator[str]:
    """The `POST /v1/checks` stream. A connection per check: Starlette may run each step of this
    generator on a different thread, and a SQLite connection belongs to one (V1.1 review)."""

    def one(address_id: str) -> checks.CheckReport:
        conn = state.connect()
        try:
            r = checks.run_check(
                conn,
                state.clock,
                address_id=address_id,
                install=state.install,
                secrets=secrets,
                factory=factory,
                connect=state.connect,
            )
            health.after_check(conn, state.clock, state.notifier, r)
            return r
        finally:
            conn.close()

    for address_id in ids:
        for _ in range(MAX_ROUNDS if until_empty else 1):
            try:
                r = one(address_id)
            except Exception as exc:  # the response has started: report it, don't cut off
                log.error("api.check_failed", error_type=type(exc).__name__)
                failed = {"address_id": address_id, "status": "internal_error"}
                yield json.dumps(failed | {"error": f"internal error ({type(exc).__name__})"})
                yield "\n"
                break
            yield json.dumps(r.to_json()) + "\n"
            if not r.more:
                break
    yield json.dumps({"done": True, "addresses": len(ids)}) + "\n"


def _stepup_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §9.6, §15.1: CLI only, never an MCP profile token (V1.2 step 2)."""

    @allow(Caller.CLI)
    def issue(request: Request) -> JSONResponse:
        body = _body(request)
        name, target = body.get("purpose"), body.get("target", {})
        if not isinstance(name, str) or not isinstance(target, dict):
            raise InvalidInputError("purpose (text) and target (object) are required")
        conn = state.connect()
        try:
            i = stepup.issue(conn, state.clock, state.stepper, name, cast(dict[str, Any], target))
        finally:
            conn.close()
        return JSONResponse(
            {
                "nonce_id": i.nonce_id,
                "code": i.code,
                "prompt_text": i.prompt,
                "expires_at": i.expires_at,
                "needs_password": i.needs_password,
            }
        )

    @allow(Caller.CLI)
    def verify(request: Request) -> JSONResponse:
        nonce = request.path_params["nonce"]
        password = _body(request).get("password")
        if password is not None and not isinstance(password, str):
            raise InvalidInputError("password must be text")
        conn = state.connect()
        try:  # the OS dialog is up for up to a minute; no transaction is held meanwhile
            outcome = stepup.verify(conn, state.clock, state.stepper, nonce, password=password)
        finally:
            conn.close()
        return JSONResponse({"verified": outcome == "verified", "outcome": outcome})

    return [
        Route("/v1/stepup/nonces", issue, methods=["POST"]),
        Route("/v1/stepup/{nonce}/verify", verify, methods=["POST"]),
    ]


def _slack_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §10.1, §15.1 (V1.2 step 4): CLI only. Tokens arrive over the 0600 socket and go
    straight to the secret store; they are never logged or returned."""

    def _with_conn(fn: Callable[[sqlite3.Connection], dict[str, Any]]) -> JSONResponse:
        conn = state.connect()
        try:
            return JSONResponse(fn(conn))
        finally:
            conn.close()

    def _nonce(body: dict[str, Any]) -> str | None:
        n = body.get("stepup_nonce")
        return n if isinstance(n, str) else None

    @allow(Caller.CLI)
    def show(_request: Request) -> JSONResponse:
        return _with_conn(lambda c: slack_admin.status(c) | {"runtime": dict(state.slack)})

    @allow(Caller.CLI)
    def create(request: Request) -> JSONResponse:
        token = _str(_body(request), "config_token")
        return _with_conn(lambda c: slack_admin.create_app(c, state.clock, state.slack_web,
                                                           token, state.install))  # fmt: skip

    @allow(Caller.CLI)
    def install(request: Request) -> JSONResponse:
        body = _body(request)
        bot, app, member = (_str(body, "bot_token"), _str(body, "app_token"),
                            _str(body, "member"))  # fmt: skip
        r = _with_conn(lambda c: slack_admin.install(c, state.clock, state.store(),
                                                     state.slack_web, bot_token=bot,
                                                     app_token=app, member=member))  # fmt: skip
        state.slack_reload()
        log.info("slack.installed")
        return r

    @allow(Caller.CLI)
    def tokens(request: Request) -> JSONResponse:
        body = _body(request)
        bot, app, nonce = _str(body, "bot_token"), _str(body, "app_token"), _nonce(body)
        r = _with_conn(lambda c: slack_admin.set_tokens(c, state.clock, state.store(),
                                                        state.slack_web, state.notifier,
                                                        bot_token=bot, app_token=app,
                                                        nonce=nonce))  # fmt: skip
        state.slack_reload()
        log.info("slack.tokens_replaced")
        return r

    @allow(Caller.CLI)
    def member(request: Request) -> JSONResponse:
        body = _body(request)
        new, nonce = _str(body, "member"), _nonce(body)
        return _with_conn(lambda c: slack_admin.set_member(c, state.clock, state.store(),
                                                           state.slack_web, state.notifier,
                                                           new, nonce))  # fmt: skip

    @allow(Caller.CLI)
    def reauthorize(request: Request) -> JSONResponse:
        token = _str(_body(request), "config_token")
        return _with_conn(lambda c: slack_admin.reauthorize(c, state.clock, state.slack_web,
                                                            token, state.install))  # fmt: skip

    @allow(Caller.CLI)
    def refresh(_request: Request) -> JSONResponse:
        return _with_conn(lambda c: {"queued": slack_admin.refresh(c, state.clock)})

    return [
        Route("/v1/slack", show, methods=["GET"]),
        Route("/v1/slack/app", create, methods=["POST"]),
        Route("/v1/slack/install", install, methods=["POST"]),
        Route("/v1/slack/tokens", tokens, methods=["POST"]),
        Route("/v1/slack/member", member, methods=["POST"]),
        Route("/v1/slack/reauthorize", reauthorize, methods=["POST"]),
        Route("/v1/slack/refresh", refresh, methods=["POST"]),
    ]


def _check_routes(state: ServiceState, allow: Allow) -> list[Route]:
    @allow(Caller.CLI)
    def run_checks(request: Request) -> StreamingResponse:
        """SPEC §15.1 `POST /v1/checks`: one JSON line per check, then a summary line."""
        body = _body(request)
        wanted = body.get("address_id")
        until_empty = bool(body.get("until_empty", False))
        secrets, factory = state.store(), state.mail_factory
        if factory is None:
            raise ServiceUnavailableError("mail access isn't configured in this service")
        conn = state.connect()
        try:
            ids = [a["address_id"] for a in checks.states(conn)]
        finally:
            conn.close()
        if wanted is not None:
            if not isinstance(wanted, str) or wanted not in ids:
                raise NotFoundError(f"no address {wanted!r}")
            ids = [wanted]

        lines = _check_lines(state, ids, until_empty, secrets, factory)
        return StreamingResponse(lines, media_type="application/x-ndjson")

    @allow(Caller.CLI)
    def logs(request: Request) -> JSONResponse:
        """SPEC §15.1 `GET /v1/logs`: audit events, newest `limit`, oldest first."""
        q = request.query_params
        try:
            limit = int(q.get("limit", "50"))
            after = int(q["after_id"]) if "after_id" in q else None
        except ValueError as exc:
            raise InvalidInputError("limit and after_id must be numbers") from exc
        conn = state.connect()
        try:
            events = audit.query(
                conn,
                state.install,
                address_id=q.get("address_id"),
                event_prefix=q.get("event"),
                since=q.get("since"),
                after_id=after,
                limit=limit,
            )
        finally:
            conn.close()
        return JSONResponse({"events": events})

    return [
        Route("/v1/checks", run_checks, methods=["POST"]),
        Route("/v1/logs", logs, methods=["GET"]),
    ]


def _address_routes(state: ServiceState, allow: Allow) -> list[Route]:
    # -- addresses (SPEC §15.1) ------------------------------------------------------------------
    def _factory() -> addresses.MailFactory:
        if state.mail_factory is None:
            raise ServiceUnavailableError("mail access isn't configured in this service")
        return state.mail_factory

    @allow(Caller.CLI)
    def list_addresses(_request: Request) -> JSONResponse:
        conn = state.connect()
        try:
            return JSONResponse(
                {
                    "addresses": addresses.list_addresses(conn),
                    "org_domains": addresses.get_org_domains(conn),
                }
            )
        finally:
            conn.close()

    @allow(Caller.CLI)
    def add_address(request: Request) -> JSONResponse:
        body = _body(request)
        org = body.get("org_domains")
        req = addresses.AddRequest(
            email=_str(body, "email"),
            imap_host=_str(body, "imap_host"),
            sensitivity=_str(body, "sensitivity"),
            preset=_str(body, "preset"),
            app_password=_str(body, "app_password"),
            address_id=_str(body, "address_id") if body.get("address_id") is not None else None,
            org_domains=_str_list(org) if org is not None else None,
        )
        conn = state.connect()
        try:
            a = addresses.add_address(
                conn, state.clock, state.store(), _factory(), req, actor="os_user"
            )
        finally:
            conn.close()
        log.info("address.added", address_id=a["address_id"])
        return JSONResponse(a, status_code=201)

    @allow(Caller.CLI)
    def set_address(request: Request) -> JSONResponse:
        ref = str(request.path_params["ref"])
        body = _body(request)
        unknown = set(body) - {"app_password"}
        if unknown:
            raise InvalidInputError(f"can't set {', '.join(sorted(unknown))} here yet")
        conn = state.connect()
        try:
            a = addresses.set_app_password(
                conn,
                state.clock,
                state.store(),
                _factory(),
                ref,
                _str(body, "app_password"),
                actor="os_user",
            )
        finally:
            conn.close()
        log.info("secret.written", address_id=a["address_id"])
        return JSONResponse(a)

    @allow(Caller.CLI)
    def remove_address(request: Request) -> JSONResponse:
        conn = state.connect()
        try:
            a = addresses.remove_address(
                conn, state.clock, state.store(), str(request.path_params["ref"]), actor="os_user"
            )
        finally:
            conn.close()
        log.info("address.removed", address_id=a["address_id"])
        return JSONResponse(a)

    @allow(Caller.CLI)
    def retry_address(request: Request) -> JSONResponse:
        """`ecf address retry`: check this address at the next tick, even while logins are
        backing off (SPEC §13.3)."""
        conn = state.connect()
        try:
            a = addresses.retry(conn, state.clock, str(request.path_params["ref"]), actor="os_user")
        finally:
            conn.close()
        return JSONResponse(a)

    return [
        Route("/v1/addresses", list_addresses, methods=["GET"]),
        Route("/v1/addresses/{ref}/retry", retry_address, methods=["POST"]),
        Route("/v1/addresses", add_address, methods=["POST"]),
        Route("/v1/addresses/{ref}", set_address, methods=["POST"]),
        Route("/v1/addresses/{ref}", remove_address, methods=["DELETE"]),
    ]


MAX_BODY = 64 * 1024


def _body(request: Request) -> dict[str, Any]:
    """The JSON object body of a sync handler (run in a worker thread by Starlette)."""
    raw = anyio.from_thread.run(request.body)
    if len(raw) > MAX_BODY:
        raise InvalidInputError("request body too large")
    try:
        data: Any = json.loads(raw or b"{}")
    except ValueError as exc:
        raise InvalidInputError("request body isn't JSON") from exc
    if not isinstance(data, dict):
        raise InvalidInputError("request body must be a JSON object")
    return cast(dict[str, Any], data)


def _str(body: dict[str, Any], key: str) -> str:
    v = body.get(key)
    if not isinstance(v, str):
        raise InvalidInputError(f"{key} must be a string")
    return v


def _str_list(v: Any) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in cast(list[Any], v)):
        raise InvalidInputError("expected a list of strings")
    return cast(list[str], v)
