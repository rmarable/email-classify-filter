"""The socket HTTP API (SPEC §11.4, §15.1): one Starlette app, sync endpoints.

Callers authenticate with a bearer token:
- the CLI token (0600 file, rewritten at every service start): full CLI access;
- a session profile token issued to `ecf claude` (WORK) and revoked when it exits, and with it the
  session's agent token (AGENT: the claim routes only, used by the agent servers `ecf-mcp --role`,
  which only ecf's subagents can call; OD-307);
- no token at all: OBSERVE (status and counts only, no email text; V1.4 step 3, operator decision
  2026-10-02). A wrong token is refused, and a route OBSERVE can't use answers `unauthorized`.
Routes declare which callers they accept. Decision and settings routes never accept a session
token (SPEC §10.4). `/v1/health` needs no token. Errors are RFC 9457 problem+json.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sqlite3
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field, replace
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import anyio.from_thread
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ecf import __version__, api_keys
from ecf.errors import (
    PROBLEM_CONTENT_TYPE,
    ConflictError,
    EcfError,
    ForbiddenProfileError,
    InternalError,
    InvalidInputError,
    NotFoundError,
    PolicyDeniedError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from ecf.ids import new_random_id
from ecf.plugin_digest import plugin_digest
from ecf.schema import load_schema
from ecf_server import (
    _slack,
    addresses,
    alerts,
    answers,
    approvals,
    audit,
    backfill,
    bundle_reader,
    checks,
    claude_batch,
    claude_eval,
    claude_pins,
    claude_queue,
    claude_review,
    claude_usage,
    config,
    corpus,
    corpus_merge,
    corpus_session,
    db,
    destroy,
    digests,
    evalrun,
    export_keys,
    gate,
    health,
    import_plan,
    importer,
    inbox,
    initsetup,
    internal,
    jev_dispatch,
    jev_keys,
    manual_export,
    model_watch,
    ops_doctor,
    outbound,
    outbound_remind,
    passphrase,
    pause,
    reprocess,
    restore,
    retention,
    routereport,
    rules,
    ruletest,
    scans,
    schedule,
    scheduled_export,
    send_actions,
    send_limits,
    senders,
    settings,
    slack_admin,
    slack_doctor,
    slack_remove,
    slack_routes,
    stages,
    stats,
    stepup,
    telemetry,
    telemetry_app,
    timings,
)
from ecf_server import upgrade_state as upgrade_state_mod
from ecf_server.chat import FakeChat
from ecf_server.clock import Clock, FakeClock, SystemClock, from_ts, to_ts
from ecf_server.facts import PUBLIC_DOMAINS
from ecf_server.log_bridge import log
from ecf_server.mail.smtp import SenderFactory
from ecf_server.notify import Notifier, NullNotifier
from ecf_server.secretstore import SecretStore
from ecf_server.stepper import Stepper
from ecf_server.telemetry import Telemetry

API_VERSION = 1


class Caller(StrEnum):
    CLI = "cli"
    WORK = "work"  # an `ecf claude` session
    AGENT = "agent"  # its agent servers (OD-307)
    OBSERVE = "observe"  # no token (`ecf-mcp` without ECF_PROFILE_TOKEN)


@dataclass
class Session:
    session_id: str
    token: str = field(repr=False)
    profile: Caller
    created_at: str
    agent_token: str = field(default="", repr=False)
    client_checked: bool = False  # `ecf claude` matched this install (v2.2.0)


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
    tick_failures: int = 0  # consecutive ticks whose work failed (V1.2 review)
    stopping_on_purpose: bool = False  # `ecf service stop|uninstall` said so (OD-222)
    tick_error: str | None = None
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
    sender_factory: SenderFactory | None = field(default=None, repr=False)  # SMTP (V1.5)
    notifier: Notifier = field(default_factory=NullNotifier, repr=False)
    stepper: Stepper | None = field(default=None, repr=False)  # None: step-up is refused
    slack: dict[str, Any] = field(default_factory=lambda: {"installed": False})  # live, runtime's
    slack_web: Callable[[str], Any] = field(default=_slack.Web, repr=False)  # a fake in tests
    slack_reload: Callable[[], None] = field(default=lambda: None, repr=False)  # the runtime's
    # the runtime's `held`: keeps the Slack thread idle while `ecf slack remove` runs
    slack_hold: Callable[[], AbstractContextManager[None]] = field(default=nullcontext, repr=False)
    watch_http: model_watch.HttpFactory = field(default=model_watch.http_client, repr=False)
    # TypeSafe's transport for the key check: None is the real one (`jev.default_transport`; a
    # fake in tests, v3.0.0 step 2a)
    jev_transport: httpx.BaseTransport | None = field(default=None, repr=False)
    # Mistral's transport for the key check and grading calls: None is the real one (a fake in
    # tests; eval generate plan step 3)
    mistral_transport: httpx.BaseTransport | None = field(default=None, repr=False)
    # preset D's Jev drain and probe in memory (v3.0.0 step 4; `jev_dispatch`, P4)
    jev: jev_dispatch.Live = field(default_factory=jev_dispatch.Live, repr=False)
    # ecf's GitHub releases for the weekly watch; set only by `ecf-server local` (§7.6)
    watch_releases: model_watch.Releases | None = field(default=None, repr=False)
    power: Callable[[], schedule.Power] = field(default=schedule.host_power, repr=False)
    # Claude Code telemetry per `ecf claude` session, and how long a read or submission waits for
    # its call's events (V1.4 step 6; logs are exported every second)
    telemetry: Telemetry = field(default_factory=Telemetry, repr=False)
    telemetry_wait_s: float = 2.0
    request_stop: Callable[[], None] = field(default=lambda: None, repr=False)  # the service's

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
        if not token:
            return Caller.OBSERVE, None
        # compare bytes: compare_digest raises on non-ASCII str (headers decode as latin-1)
        given = token.encode("utf-8", "surrogateescape")
        if hmac.compare_digest(given, self.token.encode()):
            return Caller.CLI, None
        with self.lock:
            for s in self.sessions.values():
                if hmac.compare_digest(given, s.token.encode()):
                    return s.profile, s
                if s.agent_token and hmac.compare_digest(given, s.agent_token.encode()):
                    return Caller.AGENT, s
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
                caller, session = state.caller_for(_bearer(request))
                if caller is Caller.OBSERVE and caller not in callers:
                    raise UnauthorizedError("missing or wrong token")
                if caller not in callers:
                    raise ForbiddenProfileError(f"not available to a {caller} caller")
                request.state.caller = caller
                request.state.session = session
                return handler(request)

            return wrapper

        return deco

    def health(_request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    @allow(Caller.CLI, Caller.WORK, Caller.OBSERVE)
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
                "tick_failures": state.tick_failures,
                "tick_error": state.tick_error,
                "ticks": state.ticks,
                "breaker": state.breaker,
                "secret_store": state.secret_store,
                "addresses": _address_states(state),
                "alerts": _alerts(state),
                "slack": dict(state.slack),
                "claude": _claude_status(state),
                "model_watch": _with_db(state, model_watch.status, None),
                "jev": _with_db(state, lambda c: _jev_status(state, c), None),
            }
        )

    @allow(Caller.CLI)
    def create_session(request: Request) -> JSONResponse:
        checked = _client_checked(_body(request).get("client"))
        s = replace(_new_session(state), client_checked=checked)
        with state.lock:
            state.sessions[s.session_id] = s
        log.info("session.created", session_id=s.session_id, profile=s.profile.value)
        bearer = state.telemetry.open(s.session_id)
        setup = _review_setup(state, s.session_id)
        return JSONResponse(
            {"session_id": s.session_id, "profile_token": s.token, "profile": s.profile.value,
             "agent_token": s.agent_token,
             "telemetry_port": state.telemetry.port, "telemetry_bearer": bearer} | setup,
            status_code=201,
        )  # fmt: skip

    @allow(Caller.CLI)
    def delete_session(request: Request) -> JSONResponse:
        session_id = str(request.path_params["session_id"])
        with state.lock:
            removed = state.sessions.pop(session_id, None)
        if removed is None:
            raise NotFoundError(f"no session {session_id[:8]}")
        _end_session(state, session_id)
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
            *_item_routes(state, allow),
            *_pause_routes(state, allow),
            *_alert_routes(state, allow),
            *_export_routes(state, allow),
            *_corpus_routes(state, allow),
            *_corpus_session_routes(state, allow),
            *_upgrade_routes(state, allow),
            *_destroy_routes(state, allow),
            *_stage_routes(state, allow),
            *_config_routes(state, allow),
            *_sender_routes(state, allow),
            *_data_routes(state, allow),
            *_backfill_routes(state, allow),
            *_reprocess_routes(state, allow),
            *_setup_routes(state, allow),
            *_model_routes(state, allow),
            *_watch_routes(state, allow),
            *_eval_routes(state, allow),
            *_review_routes(state, allow),
            Route("/v1/dev/clock", dev_clock, methods=["GET", "POST"]),
            Route("/v1/dev/chat/posts", dev_posts, methods=["GET", "DELETE"]),
        ],
        exception_handlers={EcfError: on_ecf_error, 404: on_not_found, Exception: on_unexpected},
    )


Allow = Callable[..., Callable[[Handler], Handler]]


def _bearer(request: Request) -> str:
    """The bearer token; "" when there is no Authorization header at all (OBSERVE). A header
    that isn't a bearer token is refused, never taken as OBSERVE."""
    header = request.headers.get("authorization")
    if header is None:
        return ""
    given = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    if not given:
        raise UnauthorizedError("missing or wrong token")
    return given


def _end_claims(state: ServiceState, session_id: str) -> None:
    """A revoked session's review claims end with it (V1.4 step 3)."""
    if state.db_path is None:
        return
    conn = state.connect()
    try:
        claude_review.release_session(conn, session_id)
    finally:
        conn.close()
    claude_eval.release_session(session_id)


def _new_session(state: ServiceState) -> Session:
    """`ecf claude`'s session; refused while a pin is past its retirement date (§7.5; V1.4
    step 10)."""
    _with_db(state, lambda c: model_watch.refuse_retired(c, state.clock), None)
    now = to_ts(state.clock.now())
    return Session(
        new_random_id(), secrets.token_urlsafe(32), Caller.WORK, now, secrets.token_urlsafe(32)
    )


def _client_mismatch(what: str) -> ConflictError:
    return ConflictError(f"this `ecf claude` {what}; run `ecf claude` from the install the"
                         f" service runs ({Path(sys.executable).parent})")  # fmt: skip


def _client_checked(client: Any) -> bool:
    """`ecf claude`'s client against this install (v2.2.0; SPEC §10.3): a different version or
    plugin is refused, and so is a client that doesn't say (v3.0.1, OD-523): every client since
    v2.2.0 sends it, and an older one's session would review with instructions the gate key
    doesn't name."""
    if client is None:
        raise _client_mismatch("is older than the service")
    if not isinstance(client, dict):
        raise InvalidInputError("client must be an object")
    c = cast(dict[str, Any], client)
    version, digest = c.get("version"), c.get("plugin_digest")
    if not isinstance(version, str) or not isinstance(digest, str):
        raise InvalidInputError("client needs version and plugin_digest strings")
    if version != __version__:
        raise _client_mismatch(f"is version {version[:32]}, the service {__version__}")
    if digest != plugin_digest():
        raise _client_mismatch("renders a different plugin from the service's")
    return True


def _require_checked(request: Request) -> None:
    """`/ecf-eval` only from a session whose client was checked (`_client_checked`)."""
    if not cast(Session, request.state.session).client_checked:
        raise _client_mismatch("is older than the service")


def _review_setup(state: ServiceState, session_id: str) -> dict[str, Any]:
    """What `ecf claude` needs to start a session (V1.4 steps 5-6): the Claude pins in force, for
    the plugin's agents and the main session, how many items wait for review, and the plan usage
    after the last review; from step 7, the Claude eval waiting for `/ecf-eval`, if any, with the
    `ecf-eval-*` agents to render for it."""
    if state.db_path is None:
        return {"models": claude_pins.load_lock(), "waiting": 0, "last_plan": None, "eval": None}
    conn = state.connect()
    try:
        claude_usage.start_session(conn, state.clock, session_id)
        setup = {"models": claude_pins.effective(conn),
                 "waiting": sum(claude_queue.waiting(conn).values()),
                 "last_plan": claude_usage.last_plan(conn)}  # fmt: skip
    finally:
        conn.close()
    return setup | {"eval": claude_eval.session_info(state.connect, state.clock)}


def _end_session(state: ServiceState, session_id: str) -> None:
    """Held submissions are settled (unbound ones refused) before the claims end and the
    session's usage is written (V1.4 step 6)."""
    if state.db_path is not None:
        telemetry_app.settle_bound(state, session_id, final=True)
    tel = state.telemetry.close(session_id)
    _end_claims(state, session_id)
    if tel is not None and state.db_path is not None:
        conn = state.connect()
        try:
            claude_usage.end_session(conn, state.clock, tel)
        finally:
            conn.close()


def _claude_status(state: ServiceState) -> dict[str, Any]:
    if state.db_path is None:
        return {"last_review": None, "used": False, "pins": None}
    conn = state.connect()
    try:
        used = claude_pins.in_use(conn)  # doctor's Claude checks fail only then (§13.2)
        return {"last_review": claude_usage.last_review(conn), "used": used,
                "pins": claude_pins.effective(conn) if used else None}  # fmt: skip
    finally:
        conn.close()


def _jev_status(state: ServiceState, conn: sqlite3.Connection) -> dict[str, Any]:
    """Preset D's Jev (v3.0.0 step 4): up or down, the drain, what waits (`ecf status`, doctor)."""
    return jev_dispatch.status(conn, state.jev, state.secrets)


def _with_db[T](state: ServiceState, fn: Callable[[sqlite3.Connection], T], empty: T) -> T:
    if state.db_path is None:
        return empty
    conn = state.connect()
    try:
        return fn(conn)
    finally:
        conn.close()


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
    secrets: SecretStore,
    factory: addresses.MailFactory,
) -> Iterator[str]:
    """The `POST /v1/checks` stream: one check per address (`--until-empty` and the local-model
    round went in v3.0.0, ADR 0031). A connection per check: Starlette may run each step of this
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
        try:
            r = one(address_id)
        except Exception as exc:  # the response has started: report it, don't cut off
            log.error("api.check_failed", error_type=type(exc).__name__)
            failed = {"address_id": address_id, "status": "internal_error"}
            yield json.dumps(failed | {"error": f"internal error ({type(exc).__name__})"})
            yield "\n"
            continue
        yield json.dumps(r.to_json()) + "\n"
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


def _item_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §15.1 (V1.2 step 7a). Item routes carry email metadata: CLI only, except counts."""

    def _with_conn(fn: Callable[[sqlite3.Connection], Any]) -> JSONResponse:
        conn = state.connect()
        try:
            return JSONResponse(fn(conn))
        finally:
            conn.close()

    @allow(Caller.CLI, Caller.WORK, Caller.OBSERVE)
    def counts(request: Request) -> JSONResponse:
        aid = request.query_params.get("address_id")
        return _with_conn(lambda c: {"counts": inbox.counts(c, aid)})

    @allow(Caller.CLI)
    def list_inbox(request: Request) -> JSONResponse:
        q = request.query_params
        aid, stale = q.get("address_id"), q.get("stale") in ("1", "true")
        return _with_conn(lambda c: {
            "items": inbox.inbox(c, address_id=aid, stale_only=stale),
            "runs": scans.inbox_lines(c, inbox.inbox(c, address_id=aid, stale_only=stale,
                                                      old=True))})  # OD-507 §2  # fmt: skip

    @allow(Caller.CLI)
    def show(request: Request) -> JSONResponse:
        ref = str(request.path_params["ref"])
        return _with_conn(lambda c: inbox.show(c, ref))

    @allow(Caller.CLI)
    def resolve_one(request: Request) -> JSONResponse:
        body, ref = _body(request), str(request.path_params["ref"])
        reason, nonce = _str(body, "reason"), _opt_str(body, "nonce_id")
        one = inbox.Selection(refs=[ref])
        return _with_conn(lambda c: {"resolved": inbox.resolve(
            c, state.clock, one, reason=reason, nonce=nonce)})  # fmt: skip

    @allow(Caller.CLI)
    def resolve_many(request: Request) -> JSONResponse:
        body = _body(request)
        ids, days = body.get("ids"), body.get("older_than_days")
        if days is not None and (not isinstance(days, int) or isinstance(days, bool)):
            raise InvalidInputError("older_than_days must be a whole number")
        refs = _str_list(ids) if ids is not None else None
        reason, nonce, aid = (_str(body, "reason"), _opt_str(body, "nonce_id"),
                              _opt_str(body, "address_id"))  # fmt: skip
        dry = body.get("dry_run") is True
        key = "would_resolve" if dry else "resolved"
        sel = inbox.Selection(refs=refs, older_than_days=days, address_id=aid)
        return _with_conn(lambda c: {key: inbox.resolve(
            c, state.clock, sel, reason=reason, nonce=nonce, dry_run=dry)})  # fmt: skip

    return [
        *_decision_routes(state, allow),
        Route("/v1/counts", counts, methods=["GET"]),
        Route("/v1/inbox", list_inbox, methods=["GET"]),
        Route("/v1/items/resolve", resolve_many, methods=["POST"]),
        Route("/v1/items/{ref}", show, methods=["GET"]),
        Route("/v1/items/{ref}/resolve", resolve_one, methods=["POST"]),
    ]


def _stage_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §9.1, §9.4, §14 (V1.2 step 10a): stages, sensitivity and settings; CLI only."""

    def _with_conn(fn: Callable[[sqlite3.Connection], Any]) -> JSONResponse:
        conn = state.connect()
        try:
            return JSONResponse(fn(conn))
        finally:
            conn.close()

    @allow(Caller.CLI)
    def stage_status(_request: Request) -> JSONResponse:
        return _with_conn(lambda c: {"addresses": stages.status(c, state.clock.now())})

    @allow(Caller.CLI)
    def set_stage(request: Request) -> JSONResponse:
        body, ref = _body(request), str(request.path_params["ref"])
        to, reason = _str(body, "value"), _opt_str(body, "reason") or ""
        nonce = _opt_str(body, "nonce_id")
        override, held = body.get("override") is True, _opt_str(body, "held") or "run"
        return _with_conn(lambda c: stages.set_stage(c, state.clock, ref, to, reason=reason,
                                                     nonce=nonce, override=override,
                                                     held=held))  # fmt: skip

    @allow(Caller.CLI)
    def address_gate(request: Request) -> JSONResponse:
        ref = str(request.path_params["ref"])

        def get(c: sqlite3.Connection) -> dict[str, Any]:
            aid = addresses.get_address(c, ref)["address_id"]
            return {"gate": gate.compute(c, aid).as_json(),
                    "held": stages.held_by_age(c, aid, state.clock.now())}  # fmt: skip

        return _with_conn(get)

    @allow(Caller.CLI)
    def outbound_report(request: Request) -> JSONResponse:
        """`ecf outbound report` (§9.8; V1.5 step 6)."""
        ref = str(request.path_params["ref"])
        return _with_conn(lambda c: outbound_remind.report(c, state.clock, ref))

    @allow(Caller.CLI)
    def set_outbound(request: Request) -> JSONResponse:
        """`ecf outbound enable|disable|resume|snooze|dismiss` (§9.8; step-up to enable, OD-323 on
        disable)."""
        body, ref = _body(request), str(request.path_params["ref"])
        value, nonce = _str(body, "value"), _opt_str(body, "nonce_id")
        if value not in ("on", "off", "resume", "snooze", "dismiss"):
            raise InvalidInputError("value must be on, off, resume, snooze or dismiss")
        if value == "snooze":
            days = body.get("days", 7)
            if not isinstance(days, int) or isinstance(days, bool):
                raise InvalidInputError("days must be a whole number")
            return _with_conn(lambda c: outbound_remind.snooze(c, state.clock, ref, days,
                                                               actor="os_user"))  # fmt: skip
        if value == "dismiss":
            return _with_conn(lambda c: outbound_remind.dismiss(c, state.clock, ref,
                                                                actor="os_user"))  # fmt: skip
        if value == "resume":  # after the send limit (OD-059)
            return _with_conn(lambda c: send_limits.resume(c, state.clock, ref, actor="os_user",
                                                           nonce=nonce))  # fmt: skip
        if value == "off":
            return _with_conn(lambda c: outbound.disable(c, state.clock, ref, actor="os_user"))
        return _with_conn(lambda c: outbound.enable(c, state.clock,
                                                    lambda t: _notice(state, c, t), ref,
                                                    actor="os_user", nonce=nonce))  # fmt: skip

    @allow(Caller.CLI)
    def set_sensitivity(request: Request) -> JSONResponse:
        body, ref = _body(request), str(request.path_params["ref"])
        to, reason = _str(body, "value"), _opt_str(body, "reason") or ""
        nonce = _opt_str(body, "nonce_id")
        return _with_conn(lambda c: stages.set_sensitivity(
            c, state.clock, state.notifier, ref, to, reason=reason, nonce=nonce))  # fmt: skip

    @allow(Caller.CLI)
    def show_settings(request: Request) -> JSONResponse:
        aid = request.query_params.get("address_id")
        return _with_conn(lambda c: {"settings": settings.show(
            c, addresses.get_address(c, aid)["address_id"] if aid else None)})  # fmt: skip

    @allow(Caller.CLI)
    def set_setting(request: Request) -> JSONResponse:
        body = _body(request)
        key, value, aid = _str(body, "key"), _str(body, "value"), _opt_str(body, "address_id")
        if key == settings.HIGH_BATCH_KEY:  # step-up to raise it (V1.4 step 9)
            if not aid:
                raise InvalidInputError(f"{key} is per address: add --address")
            nonce = _opt_str(body, "nonce_id")
            return _with_conn(lambda c: claude_batch.set_size(c, state.clock, state.notifier, aid,
                                                              value, nonce=nonce))  # fmt: skip
        return _with_conn(lambda c: settings.set_value(c, state.clock, key, value, address=aid,
                                                       actor="os_user"))  # fmt: skip

    return [
        Route("/v1/stages", stage_status, methods=["GET"]),
        Route("/v1/addresses/{ref}/stage", set_stage, methods=["POST"]),
        Route("/v1/addresses/{ref}/gate", address_gate, methods=["GET"]),
        Route("/v1/addresses/{ref}/sensitivity", set_sensitivity, methods=["POST"]),
        Route("/v1/addresses/{ref}/outbound", set_outbound, methods=["POST"]),
        Route("/v1/addresses/{ref}/outbound", outbound_report, methods=["GET"]),
        Route("/v1/settings", show_settings, methods=["GET"]),
        Route("/v1/settings", set_setting, methods=["POST"]),
    ]


def _config_routes(state: ServiceState, allow: Allow) -> list[Route]:  # noqa: PLR0915 - one handler a route
    """SPEC §8.6, §9.7 (V1.2 step 10b): `ecf config apply` (step-up) and `ecf rules test`; from
    v2.0.0 `GET /v1/schema` (OD-478)."""

    @allow(Caller.CLI)
    def apply_config(request: Request) -> JSONResponse:
        body = _body(request)
        text, nonce = _str(body, "document"), _opt_str(body, "nonce_id")
        dry = body.get("dry_run") is True
        conn = state.connect()
        try:
            r = config.apply(conn, state.clock, state.notifier, text, dry_run=dry, nonce=nonce)
        finally:
            conn.close()
        return JSONResponse(r.to_json())

    @allow(Caller.CLI)
    def test_rules(request: Request) -> JSONResponse:
        body = _body(request)
        text, root = _str(body, "rules"), Path(_str(body, "cases_dir"))
        if not root.is_absolute():
            raise InvalidInputError("cases_dir must be an absolute path")
        conn = state.connect()
        try:
            current = config.current_rules(conn)
        finally:
            conn.close()
        return JSONResponse(ruletest.run(state.clock, current, text, root))

    @allow(Caller.CLI)
    def eval_expect(request: Request) -> JSONResponse:
        """`ecf eval expect`: each case's rule and safety from the starter rules (as `ecf eval run`
        uses them) on its labels and facts (eval generate plan decision 8), and for a judged
        case each combination of sender_type and priority in `variants` (draft 21); writes
        nothing."""
        body = _body(request)
        root = Path(_str(body, "root"))
        if not root.is_absolute():
            raise InvalidInputError("root must be an absolute path")
        schema = load_schema()  # the shipped schema, as eval runs use
        rows = ruletest.outcomes(state.clock, rules.load_starter_rules(schema), root,
                                 _variants(body.get("variants")))  # fmt: skip

        def one(r: dict[str, Any]) -> dict[str, Any]:
            safety = evalrun.expected_safety(r["labels"], r["rule"], r["actions"])
            return {"rule": r["rule"], "error": r.get("error"), "safety": safety}

        return JSONResponse({
            "rules_digest": rules.composed_digest(schema, None),
            "cases": [{"id": r["id"]} | one(r)
                      | ({"variants": [{"labels": v["variant"]} | one(v) for v in r["variants"]]}
                         if "variants" in r else {})
                      for r in rows]})  # fmt: skip

    @allow(Caller.CLI)
    def eval_mistral(request: Request) -> JSONResponse:
        """`ecf eval grade --grader mistral:<model>`: one case of a generated PROD set graded by
        the Mistral API (eval generate plan step 3). The service checks the set, reads the email
        and builds the fixed grading prompt itself, so only a generated set's emails go."""
        from ecf.eval import generate as gen  # noqa: PLC0415 - eval only
        from ecf.eval import genset  # noqa: PLC0415
        from ecf.eval import grade as grading  # noqa: PLC0415
        from ecf.eval import models as eval_models  # noqa: PLC0415
        from ecf_server import mistral  # noqa: PLC0415 - eval only, not a service import

        body = _body(request)
        root, case_id, model = Path(_str(body, "root")), _str(body, "case"), _str(body, "model")
        if not root.is_absolute():
            raise InvalidInputError("root must be an absolute path")
        if not genset.is_generated(root):
            raise InvalidInputError(f"{root} isn't a generated set: Mistral grades only those")
        doc = _verified(root, gen.verify)
        entry = eval_models.check("mistral", model, "grader", str(doc.get("mode")))
        view = grading.case_view(root, case_id)  # the labeller's view (OD-544)
        if view is None:
            return JSONResponse({"status": "too large", "detail": "too large", "usage": {}})
        prompt = grading.grade_prompt(view)
        return JSONResponse(mistral.complete(state.store(), model, prompt,
                                             grading.grade_schema(),
                                             state.mistral_transport,
                                             reasoning_effort=entry.reasoning_effort))  # fmt: skip

    @allow(Caller.CLI, Caller.WORK, Caller.OBSERVE)
    def show_schema(_request: Request) -> JSONResponse:
        """The effective schema: version, the install's extension, digest (OD-478)."""
        conn = state.connect()
        try:
            schema = config.current_schema(conn)
        finally:
            conn.close()
        return JSONResponse({"version": schema.version, "extension": schema.extension,
                             "digest": schema.digest})  # fmt: skip

    return [
        Route("/v1/config/apply", apply_config, methods=["POST"]),
        Route("/v1/rules/test", test_rules, methods=["POST"]),
        Route("/v1/eval/expect", eval_expect, methods=["POST"]),
        Route("/v1/eval/mistral", eval_mistral, methods=["POST"]),
        Route("/v1/schema", show_schema, methods=["GET"]),
    ]


def _setup_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §13.1, §13.2 (V1.2 steps 11c, 12a): `ecf init` state and doctor's Slack checks; V1.5
    step 13a: doctor's sending, alert-email and backup checks."""

    @allow(Caller.CLI)
    def init_status(_request: Request) -> JSONResponse:
        conn = state.connect()
        try:
            return JSONResponse(initsetup.status(conn))
        finally:
            conn.close()

    @allow(Caller.CLI)
    def init_role(request: Request) -> JSONResponse:
        value = _str(_body(request), "install_role")
        conn = state.connect()
        try:
            return JSONResponse(initsetup.set_role(conn, state.clock, value))
        finally:
            conn.close()

    @allow(Caller.CLI)
    def init_skip(request: Request) -> JSONResponse:
        step = _str(_body(request), "step")
        conn = state.connect()
        try:
            return JSONResponse(initsetup.skip(conn, state.clock, step))
        finally:
            conn.close()

    @allow(Caller.CLI)
    def doctor_slack(_request: Request) -> JSONResponse:
        conn = state.connect()
        try:
            rows = slack_doctor.checks(conn, state.secrets, state.slack_web, dict(state.slack),
                                       state.stepper)  # fmt: skip
            return JSONResponse({"checks": rows})
        finally:
            conn.close()

    @allow(Caller.CLI)
    def doctor_ops(_request: Request) -> JSONResponse:
        if state.db_path is None:
            raise ServiceUnavailableError("the service has no database yet")
        conn = state.connect()
        try:
            rows = ops_doctor.checks(conn, state.clock, state.db_path.parent,
                                     state.notifier.name)  # fmt: skip
            return JSONResponse({"checks": rows})
        finally:
            conn.close()

    @allow(Caller.CLI)
    def digest_now(request: Request) -> JSONResponse:
        ref = _str(_body(request), "address_id")
        conn = state.connect()
        try:
            return JSONResponse(digests.post_now(conn, state.clock, ref))
        finally:
            conn.close()

    @allow(Caller.CLI)
    def stopping(_request: Request) -> JSONResponse:
        """The CLI is about to stop the service on purpose: disarm the dead-man's switch at the
        stop (a shutdown or logout leaves it armed, OD-222)."""
        state.stopping_on_purpose = True
        return JSONResponse({"ok": True})

    return [
        Route("/v1/service/stopping", stopping, methods=["POST"]),
        Route("/v1/digests", digest_now, methods=["POST"]),
        Route("/v1/init", init_status, methods=["GET"]),
        Route("/v1/init/role", init_role, methods=["POST"]),
        Route("/v1/init/skip", init_skip, methods=["POST"]),
        Route("/v1/doctor/slack", doctor_slack, methods=["GET"]),
        Route("/v1/doctor/ops", doctor_ops, methods=["GET"]),
    ]


def _eval_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §15.1, §16.2 (V1.3 step 8c): `ecf eval run|status|stop`, CLI only; from V1.4 step 7
    also `ecf eval run --claude`, which registers a run for `/ecf-eval` (its MCP side is in
    `_review_routes`)."""

    @allow(Caller.CLI)
    def start_eval(request: Request) -> JSONResponse:
        body = _body(request)
        if body.get("corpus_session") is not None:  # R-D5, P5: no model runs over real mail
            raise InvalidInputError("corpus runs aren't available since v3.0.0 (ADR 0031): no"
                                    " model is run over real mail in an eval. `ecf eval routes"
                                    " --corpus` and `ecf eval rescore --corpus` still read a"
                                    " corpus")  # fmt: skip
        root = Path(_str(body, "root")).expanduser()
        if not root.is_absolute() or not (root / "labels.jsonl").is_file():
            raise InvalidInputError("root: the synthetic set's folder (an absolute path)")
        if state.db_path is None:
            raise ServiceUnavailableError("the service has no database yet")
        opts = evalrun.Options(root, classifier=body.get("classifier") is not False,
                               fraud_only=body.get("fraud_only") is True,
                               backend=_opt_str(body, "backend") or evalrun.NULL,
                               redact=body.get("redact") is not False,
                               for_gate=body.get("for_gate") is not False,
                               copy_to=_copy_to(body),
                               signals=body.get("signals") is not False,
                               jev_concurrency=_jev_concurrency(body))  # fmt: skip
        asker = None
        if opts.backend == evalrun.JEV:  # the key is read here, then re-read per try (P12, J10)
            from ecf_server import evaljev, jev  # noqa: PLC0415 - eval only, not a service import

            store = state.store()
            if not store.get(jev.KEY_SECRET):
                raise InvalidInputError("no TypeSafe key: set it with `ecf models api-key set"
                                        " --provider typesafe`")  # fmt: skip
            asker = evaljev.factory(evaljev.Setup(lambda: store.get(jev.KEY_SECRET),
                                                  state.jev_transport))  # fmt: skip
        run = evalrun.start(state.connect, state.clock, state.db_path.parent, opts,
                            power=state.power, asker=asker)  # fmt: skip
        evalrun.note(state.connect, state.clock,
                     f"Eval {run['run_id'][:8]} started ({run['total']} cases,"
                     f" {opts.backend} backend).")  # fmt: skip
        return JSONResponse(run)

    @allow(Caller.CLI)
    def eval_status(_request: Request) -> JSONResponse:
        run = evalrun.RUN.snapshot()
        conn = state.connect()
        try:
            rows = conn.execute(
                "SELECT run_id, digest, created_at, metrics, gate_passed"
                " FROM eval_runs ORDER BY created_at DESC LIMIT 5"
            ).fetchall()
        finally:
            conn.close()
        recent = [dict(r) | {"metrics": json.loads(r["metrics"])} for r in rows]
        claude = claude_eval.status(state.connect, state.clock)
        return JSONResponse({"current": run, "recent": recent, "claude": claude})

    @allow(Caller.CLI)
    def stop_eval(_request: Request) -> JSONResponse:
        local = evalrun.stop() if evalrun.RUN.snapshot()["state"] == "running" else None
        claude = claude_eval.stop(state.connect, state.clock)
        if local is None and claude is None:
            raise NotFoundError("no eval is running")
        return JSONResponse({"local": local, "claude": claude})

    @allow(Caller.CLI)
    def start_claude_eval(request: Request) -> JSONResponse:
        """`ecf eval run --claude` (OD-288): registers the run; `/ecf-eval` drives it."""
        body = _body(request)
        root = Path(_str(body, "root")).expanduser()
        if not root.is_absolute() or not (root / "labels.jsonl").is_file():
            raise InvalidInputError("root: the synthetic set's folder (an absolute path)")
        batch = body.get("batch", 1)
        if isinstance(batch, bool) or not isinstance(batch, int):
            raise InvalidInputError("batch must be a whole number")
        if state.db_path is None:
            raise ServiceUnavailableError("the service has no database yet")
        opts = claude_eval.Options(
            root, preset=_str(body, "preset"), sensitivity=_str(body, "sensitivity"),
            fraud_only=body.get("fraud_only") is True,
            classifier_model=_opt_str(body, "classifier_model"),
            actor_model=_opt_str(body, "actor_model"),
            actor_high_model=_opt_str(body, "actor_high_model"),
            source_run=_opt_str(body, "source_run"), batch=batch,
            copy_to=_copy_to(body), for_gate=body.get("for_gate") is not False)  # fmt: skip
        return JSONResponse(claude_eval.start(state.connect, state.clock, state.db_path.parent,
                                              opts))  # fmt: skip

    return [
        Route("/v1/eval/claude", start_claude_eval, methods=["POST"]),
        Route("/v1/eval/runs", start_eval, methods=["POST"]),
        Route("/v1/eval/runs", eval_status, methods=["GET"]),
        Route("/v1/eval/runs/stop", stop_eval, methods=["POST"]),
    ]


def _statusline(state: ServiceState, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Records plan numbers when there are any; the reply carries an open eval run's progress,
    which the script prints (v2.2.0)."""
    p = telemetry.parse_plan(body)
    recorded = p != telemetry.Plan(None, None, None, None) and state.telemetry.plan(session_id, p)
    out: dict[str, Any] = {"recorded": recorded}
    if (ev := claude_eval.progress()) is not None:
        out["eval"] = ev
    return out


def _review_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §10.4, §15.1 (V1.4 step 3): `/ecf-review`'s claims. The main session (WORK) claims;
    only its agent servers (AGENT, naming their agent) read and submit (OD-307). POST throughout:
    `review-queue` claims items, and claim tokens stay out of URLs."""

    def _session(request: Request) -> str:
        s: Session = request.state.session
        return s.session_id

    def _in_conn(fn: Callable[[sqlite3.Connection], dict[str, Any]]) -> dict[str, Any]:
        conn = state.connect()
        try:
            return fn(conn)
        finally:
            conn.close()

    def _with_conn(fn: Callable[[sqlite3.Connection], dict[str, Any]]) -> JSONResponse:
        return JSONResponse(_in_conn(fn))

    def _now() -> float:
        return state.clock.now().timestamp()

    def _submitted(sid: str, conn: sqlite3.Connection,
                   got: dict[str, Any] | telemetry.Hold) -> dict[str, Any]:  # fmt: skip
        """A valid submission waits briefly for telemetry to catch up with it; if it hasn't, it
        stays held and is settled when it does, or judged on what came at session end
        (OD-307)."""
        if not isinstance(got, telemetry.Hold):
            return got
        got = replace(got, at=_now(), read_at=state.telemetry.read_at(
            sid, got.kind, got.stable_id, got.fence))  # fmt: skip
        seen = state.telemetry.judge(got, state.telemetry_wait_s)
        if seen is None and state.telemetry.hold(got):
            return {"accepted": True, "pending": True, "errors": []}
        # bound, or the session ended while it waited (then it is refused as unbound)
        return telemetry_app.settle_one(state, conn, got, seen)

    @allow(Caller.WORK)
    def review_queue(request: Request) -> JSONResponse:
        body, sid = _body(request), _session(request)
        limit = body.get("limit", claude_review.LIMIT_DEFAULT)
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise InvalidInputError("limit must be a whole number")
        address = _opt_str(body, "address_id")
        stopped = state.telemetry.stopped(sid)
        return _with_conn(lambda c: claude_review.review_queue_waiting(
            c, state.clock, sid, address=address, limit=limit, stopped=stopped))  # fmt: skip

    @allow(Caller.AGENT)
    def message(request: Request) -> JSONResponse:
        body, sid, ref = _body(request), _session(request), str(request.path_params["ref"])
        token, agent = _str(body, "claim_token"), _str(body, "agent")
        kind = "eval" if claude_eval.owns(ref) else "review"
        out = (claude_eval.get_message(state.clock, sid, ref, token, agent) if kind == "eval"
               else _in_conn(lambda c: claude_review.get_message(
                   c, state.clock, sid, ref, token, agent)))  # fmt: skip
        state.telemetry.read(sid, kind, ref, int(token.partition(".")[0]), _now())
        return JSONResponse(out)

    @allow(Caller.AGENT)
    def classification(request: Request) -> JSONResponse:
        body, sid, ref = _body(request), _session(request), str(request.path_params["ref"])
        token, agent = _str(body, "claim_token"), _str(body, "agent")
        got = body.get("classification")
        if claude_eval.owns(ref):
            return _with_conn(lambda c: _submitted(sid, c, claude_eval.record_classification(
                state.clock, sid, ref, token, got, agent)))  # fmt: skip
        return _with_conn(lambda c: _submitted(sid, c, claude_review.record_classification(
            c, state.clock, sid, ref, token, got, agent)))  # fmt: skip

    @allow(Caller.AGENT)
    def proposal(request: Request) -> JSONResponse:
        body, sid, ref = _body(request), _session(request), str(request.path_params["ref"])
        token, agent = _str(body, "claim_token"), _str(body, "agent")
        if claude_eval.owns(ref):
            return _with_conn(lambda c: _submitted(sid, c, claude_eval.propose_action(
                state.clock, sid, ref, token, body, agent)))  # fmt: skip
        return _with_conn(lambda c: _submitted(sid, c, claude_review.propose_action(
            c, state.clock, sid, ref, token, body, agent)))  # fmt: skip

    @allow(Caller.WORK)
    def eval_next(request: Request) -> JSONResponse:
        """`/ecf-eval` (V1.4 step 7, OD-287): claims the next cases, as `review_queue` does."""
        body, sid = _body(request), _session(request)
        _require_checked(request)
        limit = body.get("limit", claude_review.LIMIT_DEFAULT)
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise InvalidInputError("limit must be a whole number")
        stopped = state.telemetry.stopped(sid)
        return JSONResponse(claude_eval.eval_next_waiting(
            state.connect, state.clock, sid, limit=limit, stopped=stopped))  # fmt: skip

    @allow(Caller.WORK)
    def eval_results(_request: Request) -> JSONResponse:
        return JSONResponse(claude_eval.results())

    @allow(Caller.WORK)
    def statusline(request: Request) -> JSONResponse:
        """The status-line script's plan usage (SPEC §13.4): numbers only."""
        return JSONResponse(_statusline(state, _session(request), _body(request)))

    return [
        Route("/v1/review-queue", review_queue, methods=["POST"]),
        Route("/v1/claims/{ref}/message", message, methods=["POST"]),
        Route("/v1/claims/{ref}/classification", classification, methods=["POST"]),
        Route("/v1/claims/{ref}/proposal", proposal, methods=["POST"]),
        Route("/v1/eval/next", eval_next, methods=["POST"]),
        Route("/v1/eval/results", eval_results, methods=["POST"]),
        Route("/v1/statusline", statusline, methods=["POST"]),
    ]


def _jev_concurrency(body: dict[str, Any]) -> int:
    v = body.get("jev_concurrency", 1)
    if isinstance(v, bool) or not isinstance(v, int):
        raise InvalidInputError("jev_concurrency must be a whole number")
    return v


def _watch_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §7.6 (V1.4 step 10): the weekly model watch's key and a run now."""

    @allow(Caller.CLI)
    def anthropic_key(request: Request) -> JSONResponse:
        """SPEC §7.6: `ecf models api-key set|clear --provider anthropic` (v3.0.0 step 2a,
        OD-503); the service alone writes the secret store. Checked by one Models API call
        within 5 s, then a Security Notice."""
        key = _api_key(request, "anthropic")
        store = state.store()
        had = store.get(model_watch.KEY_SECRET) is not None
        conn = state.connect()
        try:
            r = model_watch.set_key(conn, state.clock, store, key, state.watch_http,
                                    timeout_s=KEY_CHECK_S)  # fmt: skip
            _key_notice(state, conn, "anthropic", key is not None, had)
        finally:
            conn.close()
        log.info("models.api_key_cleared" if key is None else "models.api_key_set")
        return JSONResponse(r)

    @allow(Caller.CLI)
    def typesafe_key(request: Request) -> JSONResponse:
        """SPEC §7.6, §7.9: `ecf models api-key set|clear --provider typesafe` (v3.0.0 step 2a,
        OD-503). Checked by one minimal call with no email content within 5 s, then a Security
        Notice."""
        from ecf_server import jev  # noqa: PLC0415 - eval only, not a service import

        key = _api_key(request, "typesafe")
        conn = state.connect()
        try:
            r = jev.set_key(conn, state.clock, state.store(), key, state.jev_transport)
            had = bool(r.get("had") if key is None else r.get("replaced"))
            _key_notice(state, conn, "typesafe", key is not None, had)
        finally:
            conn.close()
        log.info("models.jev_key_cleared" if key is None else "models.jev_key_set")
        return JSONResponse(r)

    @allow(Caller.CLI)
    def mistral_key(request: Request) -> JSONResponse:
        """`ecf models api-key set|clear --provider mistral` (eval generate plan step 3; operator
        decision 2026-10-20): the key `ecf eval grade` uses. Checked by one model-list call
        within 5 s, then a Security Notice."""
        from ecf_server import mistral  # noqa: PLC0415 - eval only, not a service import

        key = _api_key(request, "mistral")
        conn = state.connect()
        try:
            r = mistral.set_key(conn, state.clock, state.store(), key, state.mistral_transport)
            had = bool(r.get("had") if key is None else r.get("replaced"))
            _key_notice(state, conn, "mistral", key is not None, had)
        finally:
            conn.close()
        log.info("models.mistral_key_cleared" if key is None else "models.mistral_key_set")
        return JSONResponse(r)

    @allow(Caller.CLI)
    def watch_now(_request: Request) -> JSONResponse:
        conn = state.connect()
        try:
            model_watch.run_now(conn)
            return JSONResponse(model_watch.status(conn))
        finally:
            conn.close()

    return [
        Route("/v1/models/keys/anthropic", anthropic_key, methods=["POST"]),
        Route("/v1/models/keys/typesafe", typesafe_key, methods=["POST"]),
        Route("/v1/models/keys/mistral", mistral_key, methods=["POST"]),
        Route("/v1/models/watch", watch_now, methods=["POST"]),
    ]


def _model_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §7.5, §13.2: `ecf models status` (the Claude pins) and the Claude model override;
    the local model's routes went in v3.0.0 (ADR 0031)."""

    @allow(Caller.CLI)
    def claude_models(_request: Request) -> JSONResponse:
        """`ecf models status` (v3.0.0, ADR 0031): the Claude pins, any override and the weekly
        watch; `GET /v1/models`, the local model's status, went with it."""
        conn = state.connect()
        try:
            return JSONResponse({"claude": claude_pins.show(conn),
                                 "watch": model_watch.status(conn)})  # fmt: skip
        finally:
            conn.close()

    @allow(Caller.CLI)
    def claude_override(request: Request) -> JSONResponse:
        """SPEC §7.5 (V1.4 step 2): `ecf settings set claude_model_override` (step-up)."""
        body = _body(request)
        value, nonce = _str(body, "value"), _opt_str(body, "nonce_id")
        conn = state.connect()
        try:
            return JSONResponse(claude_pins.set_override(conn, state.clock, state.notifier, value,
                                                         nonce=nonce))  # fmt: skip
        finally:
            conn.close()

    @allow(Caller.CLI)
    def show_stats(request: Request) -> JSONResponse:
        """SPEC §13.4 (V1.3 step 9): `ecf stats`; Claude's part (V1.4 step 6) isn't split by
        address, so it comes only without `address` and for preset C (A and B went in v3.0.0;
        their `model_calls` rows stay until pruned with the logs)."""
        q = request.query_params
        now = state.clock.now()
        if "since" in q:  # the range parser's UTC form (OD-507); `hours` from older clients
            try:
                since = from_ts(q["since"])
            except ValueError as exc:
                raise InvalidInputError("since: a UTC time, 2026-09-01T00:00:00.000000Z") from exc
        else:
            try:
                hours = float(q.get("hours", "168"))
            except ValueError as exc:
                raise InvalidInputError("hours: a number") from exc
            if not 0 < hours <= 24 * 366:
                raise InvalidInputError("hours: more than 0, at most a year")
            since = now - timedelta(hours=hours)
        if not now - timedelta(days=366) <= since < now:
            raise InvalidInputError("since: in the past, at most a year back")
        conn = state.connect()
        try:
            if q.get("stages") == "1":  # `ecf stats --stages` (OD-507 §5 a, b)
                return JSONResponse(timings.stages(conn, since))
            ref = q.get("address")
            aid = addresses.get_address(conn, ref)["address_id"] if ref else None
            preset = q.get("preset")
            out = stats.report(conn, since, address=aid, preset=preset)
            if aid is None and preset in (None, "C", "D"):  # D's actor is Claude (step 4)
                out["claude"] = claude_usage.report(conn, since)
            if aid is None:  # posts aren't per address (OD-507 §5 e)
                out["slack"] = timings.slack(conn, since)
            return JSONResponse(out)
        finally:
            conn.close()

    return [
        Route("/v1/models/claude", claude_models, methods=["GET"]),
        Route("/v1/models/claude-override", claude_override, methods=["POST"]),
        Route("/v1/stats", show_stats, methods=["GET"]),
    ]


def _data_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §6.5 (V1.2 step 11): retention; CLI only."""

    @allow(Caller.CLI)
    def show_retention(_request: Request) -> JSONResponse:
        conn = state.connect()
        try:
            last = conn.execute("SELECT value FROM settings WHERE key = ?",
                                (retention.LAST_RUN_KEY,)).fetchone()  # fmt: skip
            return JSONResponse({"days": retention.days(conn),
                                 "last_run": json.loads(last[0]) if last else None})  # fmt: skip
        finally:
            conn.close()

    @allow(Caller.CLI)
    def set_retention(request: Request) -> JSONResponse:
        body = _body(request)
        value, nonce = body.get("days"), _opt_str(body, "nonce_id")
        if not isinstance(value, int):
            raise InvalidInputError("days must be a whole number")
        conn = state.connect()
        try:
            r = retention.set_days(conn, state.clock, state.notifier, value, nonce=nonce)
        finally:
            conn.close()
        return JSONResponse(r)

    return [
        Route("/v1/retention", show_retention, methods=["GET"]),
        Route("/v1/retention", set_retention, methods=["POST"]),
    ]


def _backfill_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §10.2 (V1.2 step 11; OD-507 §3): `ecf backfill`; CLI only."""

    @allow(Caller.CLI)
    def start_backfill(request: Request) -> JSONResponse:
        """A bare `since` date is UTC midnight (v2.2.x clients); the new client sends UTC times
        from the range parser, with `until` and what was typed (OD-507 §3.1, P13)."""
        body = _body(request)
        ref, since, act = _str(body, "address_id"), _str(body, "since"), body.get("act") is True
        until, nonce = _opt_str(body, "until"), _opt_str(body, "nonce_id")
        raw = body.get("typed")
        typed = {k: str(v)[:80] for k, v in cast(dict[str, Any], raw).items()
                 if k in ("since", "until")} if isinstance(raw, dict) else None  # fmt: skip
        conn = state.connect()
        try:
            r = backfill.start(conn, state.clock, ref, since, act=act, until=until, nonce=nonce,
                               typed=typed)  # fmt: skip
        finally:
            conn.close()
        return JSONResponse(r)

    @allow(Caller.CLI)
    def count_backfill(request: Request) -> JSONResponse:
        """`ecf backfill --act`'s count: asked here, answered by the next check (P11)."""
        body = _body(request)
        conn = state.connect()
        try:
            return JSONResponse(backfill.request_count(
                conn, state.clock, _str(body, "address_id"), _str(body, "since"),
                _opt_str(body, "until")))  # fmt: skip
        finally:
            conn.close()

    @allow(Caller.CLI)
    def backfill_count(request: Request) -> JSONResponse:
        conn = state.connect()
        try:
            return JSONResponse(backfill.count(conn, request.query_params.get("address_id", "")))
        finally:
            conn.close()

    @allow(Caller.CLI)
    def backfill_status(_request: Request) -> JSONResponse:
        conn = state.connect()
        try:
            return JSONResponse({"running": backfill.status(conn)})
        finally:
            conn.close()

    @allow(Caller.CLI)
    def stop_backfill(request: Request) -> JSONResponse:
        ref = _str(_body(request), "address_id")
        conn = state.connect()
        try:
            return JSONResponse(backfill.stop(conn, state.clock, ref))
        finally:
            conn.close()

    return [
        Route("/v1/backfill/stop", stop_backfill, methods=["POST"]),
        Route("/v1/backfill/count", count_backfill, methods=["POST"]),
        Route("/v1/backfill/count", backfill_count, methods=["GET"]),
        Route("/v1/backfill", start_backfill, methods=["POST"]),
        Route("/v1/backfill", backfill_status, methods=["GET"]),
    ]


def _reprocess_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §10.2 (OD-507 §4): `ecf reprocess`; CLI only."""

    @allow(Caller.CLI)
    def start_reprocess(request: Request) -> JSONResponse:
        """`ecf reprocess` (OD-507 §4.2): `dry_run` counts only; otherwise the job starts."""
        body = _body(request)
        ref, since = _str(body, "address_id"), _str(body, "since")
        until, nonce = _opt_str(body, "until"), _opt_str(body, "nonce_id")
        rules_only, force = body.get("rules_only") is True, body.get("force") is True
        raw = body.get("typed")
        typed = {k: str(v)[:80] for k, v in cast(dict[str, Any], raw).items()
                 if k in ("since", "until")} if isinstance(raw, dict) else None  # fmt: skip
        conn = state.connect()
        try:
            if body.get("dry_run") is True:
                return JSONResponse(reprocess.dry_run(
                    conn, state.clock, ref, since, until, rules_only=rules_only,
                    force=force))  # fmt: skip
            return JSONResponse(reprocess.start(conn, state.clock, ref, since, until,
                                                rules_only=rules_only, force=force, nonce=nonce,
                                                typed=typed))  # fmt: skip
        finally:
            conn.close()

    @allow(Caller.CLI)
    def reprocess_status(_request: Request) -> JSONResponse:
        conn = state.connect()
        try:
            return JSONResponse({"runs": reprocess.status(conn)})
        finally:
            conn.close()

    @allow(Caller.CLI)
    def stop_reprocess(request: Request) -> JSONResponse:
        ref = _str(_body(request), "address_id")
        conn = state.connect()
        try:
            return JSONResponse(reprocess.stop(conn, state.clock, ref))
        finally:
            conn.close()

    return [
        Route("/v1/reprocess/stop", stop_reprocess, methods=["POST"]),
        Route("/v1/reprocess", start_reprocess, methods=["POST"]),
        Route("/v1/reprocess", reprocess_status, methods=["GET"]),
    ]


def _sender_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §7.2, §8.5 (V1.2 step 10c): sender records; setting them needs step-up."""

    def _with_conn(fn: Callable[[sqlite3.Connection], dict[str, Any]]) -> JSONResponse:
        conn = state.connect()
        try:
            return JSONResponse(fn(conn))
        finally:
            conn.close()

    @allow(Caller.CLI)
    def show_sender(request: Request) -> JSONResponse:
        q = request.query_params
        sender, aid = q.get("sender") or "", q.get("address_id")
        return _with_conn(lambda c: senders.show(c, sender, aid))

    @allow(Caller.CLI)
    def confirm(request: Request) -> JSONResponse:
        body = _body(request)
        sender, category = _str(body, "sender"), _str(body, "category")
        aid, nonce = _opt_str(body, "address_id"), _opt_str(body, "nonce_id")
        return _with_conn(lambda c: senders.confirm(c, state.clock, sender, category,
                                                    address=aid, nonce=nonce))  # fmt: skip

    @allow(Caller.CLI)
    def reply_to(request: Request) -> JSONResponse:
        body = _body(request)
        sender, domain = _str(body, "sender"), _opt_str(body, "domain")
        aid, nonce = _opt_str(body, "address_id"), _opt_str(body, "nonce_id")
        return _with_conn(lambda c: senders.set_reply_to(c, state.clock, sender, domain,
                                                         address=aid, nonce=nonce))  # fmt: skip

    @allow(Caller.CLI)
    def verified(request: Request) -> JSONResponse:
        body = _body(request)
        sender, on = _str(body, "sender"), body.get("on") is not False
        aid, nonce = _opt_str(body, "address_id"), _opt_str(body, "nonce_id")
        return _with_conn(lambda c: senders.set_verified(c, state.clock, sender, on,
                                                         address=aid, nonce=nonce))  # fmt: skip

    return [
        Route("/v1/senders", show_sender, methods=["GET"]),
        Route("/v1/senders/confirm", confirm, methods=["POST"]),
        Route("/v1/senders/reply-to", reply_to, methods=["POST"]),
        Route("/v1/senders/verified", verified, methods=["POST"]),
    ]


def _alert_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §13.3, §15.1 (V1.2 step 9): `ecf alerts show|set|test`; `set` needs step-up. V1.5
    step 7a: `ecf alerts email set|off` (step-up)."""

    def _with_conn(fn: Callable[[sqlite3.Connection], dict[str, Any]]) -> JSONResponse:
        conn = state.connect()
        try:
            return JSONResponse(fn(conn))
        finally:
            conn.close()

    @allow(Caller.CLI)
    def show(_request: Request) -> JSONResponse:
        return _with_conn(alerts.show)

    @allow(Caller.CLI)
    def set_routes(request: Request) -> JSONResponse:
        body = _body(request)
        cls, nonce = _opt_str(body, "class"), _opt_str(body, "nonce_id")
        to = _str_list(body.get("to", []))
        return _with_conn(lambda c: alerts.set_routes(c, state.clock, state.notifier, cls, to,
                                                      nonce=nonce))  # fmt: skip

    @allow(Caller.CLI)
    def test(_request: Request) -> JSONResponse:
        return _with_conn(lambda c: alerts.test(c, state.clock, state.notifier))

    @allow(Caller.CLI)
    def email_set(request: Request) -> JSONResponse:
        body = _body(request)
        frm, to = _opt_str(body, "from") or "", _opt_str(body, "to") or ""
        nonce = _opt_str(body, "nonce_id")
        return _with_conn(lambda c: alerts.set_email(c, state.clock, state.notifier, frm, to,
                                                     nonce=nonce))  # fmt: skip

    @allow(Caller.CLI)
    def email_off(request: Request) -> JSONResponse:
        nonce = _opt_str(_body(request), "nonce_id")
        return _with_conn(lambda c: alerts.email_off(c, state.clock, state.notifier, nonce=nonce))

    return [
        Route("/v1/alerts", show, methods=["GET"]),
        Route("/v1/alerts", set_routes, methods=["POST"]),
        Route("/v1/alerts/test", test, methods=["POST"]),
        Route("/v1/alerts/email", email_set, methods=["POST"]),
        Route("/v1/alerts/email/off", email_off, methods=["POST"]),
    ]


def _export_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §11.9, §15.1 (V1.5 step 8a): the backup key and `export_dir`, each with step-up;
    (step 8b) the schedule's state and `ecf export now`; (step 9a) manual `ecf export --to`;
    (step 9b) `ecf import --dry-run`; (step 9c) `ecf import [--replace]`; (step 10a) `ecf
    restore`."""

    def _with_conn(fn: Callable[[sqlite3.Connection], dict[str, Any]]) -> JSONResponse:
        conn = state.connect()
        try:
            return JSONResponse(fn(conn))
        finally:
            conn.close()

    def data_dir() -> Path:
        if state.db_path is None:
            raise ServiceUnavailableError("the service has no database yet")
        return state.db_path.parent

    @allow(Caller.CLI)
    def show(_request: Request) -> JSONResponse:
        return _with_conn(lambda c: export_keys.show(c, data_dir()) | scheduled_export.status(c))

    @allow(Caller.CLI)
    def new_key(_request: Request) -> JSONResponse:
        state.store()  # no secret store, no key: fail before showing one
        return _with_conn(lambda c: export_keys.new_key(c, state.clock))

    @allow(Caller.CLI)
    def rotate(request: Request) -> JSONResponse:
        body = _body(request)
        pid, typed = _str(body, "pending_id"), _str(body, "fingerprint")
        nonce = _opt_str(body, "nonce_id")
        return _with_conn(lambda c: export_keys.rotate(c, state.clock, state.notifier,
                                                       state.store(), data_dir(), pid, typed,
                                                       nonce=nonce))  # fmt: skip

    @allow(Caller.CLI)
    def now(_request: Request) -> JSONResponse:
        return _with_conn(lambda c: scheduled_export.now(c, state.clock, state.notifier,
                                                         state.secrets, data_dir(),
                                                         state.install))  # fmt: skip

    @allow(Caller.CLI)
    def suggest(_request: Request) -> JSONResponse:
        return JSONResponse({"passphrase": passphrase.generate()})

    @allow(Caller.CLI)
    def manual(request: Request) -> JSONResponse:
        body = _body(request)
        path, secret = _str(body, "path"), _str(body, "passphrase")
        nonce = _opt_str(body, "nonce_id")
        return _with_conn(lambda c: manual_export.export(c, state.clock, state.notifier,
                                                         state.secrets, data_dir(),
                                                         state.install, path, secret,
                                                         nonce=nonce))  # fmt: skip

    @allow(Caller.CLI)
    def inspect(request: Request) -> JSONResponse:
        path = _str(_body(request), "path")
        return _with_conn(lambda c: bundle_reader.describe(bundle_reader.inspect(c, path)))

    @allow(Caller.CLI)
    def import_(request: Request) -> JSONResponse:
        body = _body(request)
        path, secret = _str(body, "path"), _str(body, "secret")
        dry_run, replace = body.get("dry_run") is True, body.get("replace") is True
        typed, nonce = _opt_str(body, "install"), _opt_str(body, "nonce_id")

        def go(c: sqlite3.Connection) -> dict[str, Any]:
            parsed = bundle_reader.read(c, path, secret)
            if dry_run:
                return import_plan.preview(c, parsed)
            return importer.apply(c, state.clock, state.notifier, data_dir(), state.install,
                                  parsed, path, replace=replace, typed_install=typed,
                                  nonce=nonce)  # fmt: skip

        return _with_conn(go)

    @allow(Caller.CLI)
    def restore_(request: Request) -> JSONResponse:
        body = _body(request)
        path, secret = _str(body, "path"), _str(body, "secret")
        key_text = _opt_str(body, "backup_key")
        nonce = _opt_str(body, "nonce_id")

        def go(c: sqlite3.Connection) -> dict[str, Any]:
            parsed = bundle_reader.read(c, path, secret, key_text)
            if body.get("dry_run") is True:
                return restore.check(c, parsed)
            typed = secret if parsed.opened.header["kind"] == "scheduled" else key_text
            if typed is None:
                raise InvalidInputError("restoring a manual bundle needs the backup key too")
            return restore.restore(c, state.clock, state.notifier, state.store(), data_dir(),
                                   parsed, path, typed, stopped=body.get("stopped") is True,
                                   older_ok=body.get("older_ok") is True, nonce=nonce)  # fmt: skip

        return _with_conn(go)

    @allow(Caller.CLI)
    def set_dir(request: Request) -> JSONResponse:
        body = _body(request)
        path, typed = _str(body, "path"), _str(body, "fingerprint")
        nonce = _opt_str(body, "nonce_id")
        return _with_conn(lambda c: export_keys.set_dir(c, state.clock, state.notifier,
                                                        data_dir(), path, typed,
                                                        nonce=nonce))  # fmt: skip

    return [
        Route("/v1/export", show, methods=["GET"]),
        Route("/v1/export/keys/new", new_key, methods=["POST"]),
        Route("/v1/export/keys", rotate, methods=["POST"]),
        Route("/v1/export/dir", set_dir, methods=["POST"]),
        Route("/v1/export/now", now, methods=["POST"]),
        Route("/v1/export/passphrase", suggest, methods=["GET"]),
        Route("/v1/export", manual, methods=["POST"]),
        Route("/v1/import/inspect", inspect, methods=["POST"]),
        Route("/v1/import", import_, methods=["POST"]),
        Route("/v1/restore", restore_, methods=["POST"]),
    ]


def _corpus_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §16.7 (OD-466 to OD-468): `ecf corpus`; CLI only, never on `ecf-server dev`, whose
    step-up is a fake (R56, R106)."""

    def data_dir() -> Path:
        if state.db_path is None:
            raise ServiceUnavailableError("the service has no database yet")
        return state.db_path.parent

    def not_dev() -> None:
        if state.dev is not None:
            raise PolicyDeniedError("corpus commands don't run on ecf-server dev: its step-up is"
                                    " a fake (SPEC §16.7)")  # fmt: skip

    def secret(name: str) -> str | None:
        return state.store().get(name)

    @allow(Caller.CLI)
    def preflight(request: Request) -> JSONResponse:
        not_dev()
        body = _body(request)
        conn = state.connect()
        try:
            req, password = corpus.from_body(conn, body, secret)
            corpus.check_request(req, data_dir())
            reader = corpus.CorpusReader(req.host, req.email, password, port=req.port)
            try:
                return JSONResponse(corpus.preflight(conn, state.clock, req, reader))
            finally:
                reader.close()
        finally:
            conn.close()

    @allow(Caller.CLI)
    def fetch_(request: Request) -> JSONResponse:
        not_dev()
        body = _body(request)
        conn = state.connect()
        try:
            req, password = corpus.from_body(conn, body, secret)
        finally:
            conn.close()
        return JSONResponse(corpus.begin(state.connect, state.clock, state.notifier, data_dir(),
                                         req, password, nonce=_opt_str(body, "nonce_id"),
                                         own_passphrase=_opt_str(body, "passphrase")))  # fmt: skip

    @allow(Caller.CLI)
    def status(_request: Request) -> JSONResponse:
        session = corpus_session.status(state.clock.monotonic)
        return JSONResponse(corpus.RUN.snapshot() | {"session": session})

    @allow(Caller.CLI)
    def stop(_request: Request) -> JSONResponse:
        return JSONResponse(corpus.stop())

    @allow(Caller.CLI)
    def info(request: Request) -> JSONResponse:
        return JSONResponse(corpus.info(_str(_body(request), "path")))

    @allow(Caller.CLI)
    def merge(request: Request) -> JSONResponse:
        not_dev()
        body = _body(request)
        return _with_conn(lambda c: corpus_merge.from_body(c, state.clock, state.notifier,
                                                           data_dir(), body))  # fmt: skip

    def _with_conn(fn: Callable[[sqlite3.Connection], dict[str, Any]]) -> JSONResponse:
        conn = state.connect()
        try:
            return JSONResponse(fn(conn))
        finally:
            conn.close()

    return [
        Route("/v1/corpus/preflight", preflight, methods=["POST"]),
        Route("/v1/corpus/fetch", fetch_, methods=["POST"]),
        Route("/v1/corpus", status, methods=["GET"]),
        Route("/v1/corpus/stop", stop, methods=["POST"]),
        Route("/v1/corpus/info", info, methods=["POST"]),
        Route("/v1/corpus/merge", merge, methods=["POST"]),
    ]


def _corpus_session_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §16.7: a decrypted corpus held for labelling and eval runs (R46, R104, R154); CLI
    only. The passphrase arrives in the open request and isn't kept."""

    @allow(Caller.CLI)
    def open_(request: Request) -> JSONResponse:
        body = _body(request)
        return JSONResponse(corpus_session.open_session(_str(body, "path"),
                                                        _str(body, "passphrase"),
                                                        state.clock.monotonic))  # fmt: skip

    def _session(request: Request) -> corpus_session.Session:
        return corpus_session.get(str(request.path_params["session_id"]), state.clock.monotonic)

    @allow(Caller.CLI)
    def keys(request: Request) -> JSONResponse:
        return JSONResponse({"keys": corpus_session.keys(_session(request))})

    @allow(Caller.CLI)
    def item(request: Request) -> JSONResponse:
        try:
            index = int(request.path_params["index"])
        except ValueError as exc:
            raise InvalidInputError("index: a message number") from exc
        return JSONResponse(corpus_session.item(_session(request), index))

    @allow(Caller.CLI)
    def close(request: Request) -> JSONResponse:
        stop = request.query_params.get("stop") == "1"
        return JSONResponse(corpus_session.close(str(request.path_params["session_id"]),
                                                 stop=stop))  # fmt: skip

    @allow(Caller.CLI)
    def rescore(request: Request) -> JSONResponse:
        """`ecf eval rescore`: a corpus result against the session's current labels (R53), a
        pre-v3 synthetic result against the set's current labels (`synthetic_root`, E1), or a
        synthetic result planned again through today's facts and policy (`through_policy`)."""
        body = _body(request)
        if body.get("synthetic_root") is not None:
            path = Path(_str(body, "result")).expanduser()
            if path.suffix != ".json" or not path.is_file():
                raise InvalidInputError(f"no result file at {path}")
            root = Path(_str(body, "synthetic_root")).expanduser()
            if body.get("through_policy") is True:  # pre-classifier plan step 7
                return JSONResponse(evalrun.rescore_through_policy(root, path, state.clock))
            return JSONResponse(evalrun.rescore_synthetic(root, path, state.clock))
        s = corpus_session.get(_str(body, "corpus_session"), state.clock.monotonic)
        if s.busy:
            raise InvalidInputError("an eval run is using this corpus; wait for it to end")
        path = Path(_str(body, "result")).expanduser()
        if path.suffix != ".json" or not path.is_file():
            raise InvalidInputError(f"no result file at {path}")
        conn = state.connect()
        try:
            schema = config.current_schema(conn)
        finally:
            conn.close()
        return JSONResponse(evalrun.rescore(s, path, state.clock, schema))

    @allow(Caller.CLI)
    def routes(request: Request) -> JSONResponse:
        """`ecf eval routes`: the OD-490 routes on synthetic sets, attribution of what changed in
        recorded results on the first set, and the routes on an open corpus (pre-classifier plan
        steps 7b, 7c); no model runs."""
        body = _body(request)
        sets: list[Any] = body.get("sets") or []
        given: list[Any] = body.get("results") or []
        roots = [Path(str(x)).expanduser() for x in sets]
        results = [Path(str(x)).expanduser() for x in given]
        for path in results:
            if path.suffix != ".json" or not path.is_file():
                raise InvalidInputError(f"no result file at {path}")
        corpus = None
        if body.get("corpus_session") is not None:
            corpus = corpus_session.get(_str(body, "corpus_session"), state.clock.monotonic)
            if corpus.busy:
                raise InvalidInputError("an eval run is using this corpus; wait for it to end")
        return JSONResponse(routereport.report(roots, results, state.clock, corpus))

    return [
        Route("/v1/eval/rescore", rescore, methods=["POST"]),
        Route("/v1/eval/routes", routes, methods=["POST"]),
        Route("/v1/corpus/session", open_, methods=["POST"]),
        Route("/v1/corpus/session/{session_id}/keys", keys, methods=["GET"]),
        Route("/v1/corpus/session/{session_id}/items/{index}", item, methods=["GET"]),
        Route("/v1/corpus/session/{session_id}", close, methods=["DELETE"]),
    ]


def _destroy_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §11.11 (V1.5 step 12a): `ecf destroy`'s preview and the service's part; after the
    service's part the service stops, as a stop you asked for (OD-222)."""

    def root() -> Path:
        if state.db_path is None:
            raise ServiceUnavailableError("the service has no database yet")
        return state.db_path.parent.parent

    def sessions() -> int:
        with state.lock:
            return len(state.sessions)

    def sender_for(conn: sqlite3.Connection, address_id: str) -> Any:
        if send_actions.sender_for is None:
            raise ServiceUnavailableError("no mail sender here")
        return send_actions.sender_for(conn, address_id)

    @allow(Caller.CLI)
    def show(_request: Request) -> JSONResponse:
        conn = state.connect()
        try:
            return JSONResponse(destroy.preview(conn, state.clock, state.install, root(),
                                                sessions()))  # fmt: skip
        finally:
            conn.close()

    @allow(Caller.CLI)
    def run(request: Request) -> JSONResponse:
        body = _body(request)
        typed, token = _str(body, "install"), _opt_str(body, "config_token")
        nonce = _opt_str(body, "nonce_id")
        ctx = destroy.Context(state.install, root(), state.clock, state.notifier, state.store(),
                              state.slack_web, sender_for)  # fmt: skip
        conn = state.connect()
        try:
            rec = destroy.run(conn, ctx, typed=typed, config_token=token, nonce=nonce,
                              sessions=sessions())  # fmt: skip
        finally:
            conn.close()
        state.stopping_on_purpose = True
        state.request_stop()
        return JSONResponse(rec)

    return [Route("/v1/destroy", show, methods=["GET"]),
            Route("/v1/destroy", run, methods=["POST"])]  # fmt: skip


def _upgrade_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §11.10 (V1.5 step 11a): what `ecf upgrade` checks against the new wheel."""

    @allow(Caller.CLI)
    def upgrade_state(_request: Request) -> JSONResponse:
        conn = state.connect()
        try:
            with state.lock:
                sessions = len(state.sessions)
            return JSONResponse(upgrade_state_mod.state(conn, state.clock, api_version=API_VERSION,
                                                        sessions=sessions))  # fmt: skip
        finally:
            conn.close()

    @allow(Caller.CLI)
    def finish(request: Request) -> JSONResponse:
        body = _body(request)
        conn = state.connect()
        try:
            return JSONResponse(upgrade_state_mod.finish(conn, state.clock, body))
        finally:
            conn.close()

    return [Route("/v1/upgrade/state", upgrade_state, methods=["GET"]),
            Route("/v1/upgrade/finish", finish, methods=["POST"])]  # fmt: skip


def _pause_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §15.1 (V1.2 step 8a): pause and resume one address, or all of them. Instant, no
    step-up (§9.1); the pre-check and fraud flagging never pause."""

    def change(ref: str, paused: bool, *, owner_agreed: bool = False) -> JSONResponse:
        conn = state.connect()
        try:
            changed = pause.set_paused(conn, state.clock, ref, paused, actor="os_user",
                                       owner_agreed=owner_agreed)  # fmt: skip
            waiting = pause.awaiting_consent(conn) if ref == pause.ALL else None
        finally:
            conn.close()
        return JSONResponse({"changed": changed,
                             "message": pause.describe(changed, paused, waiting)})  # fmt: skip

    @allow(Caller.CLI)
    def pause_one(request: Request) -> JSONResponse:
        return change(str(request.path_params["ref"]), True)

    @allow(Caller.CLI)
    def resume_one(request: Request) -> JSONResponse:
        """A converted address (v3.0.0, P3) needs `owner_agreed: true`, which `ecf resume`
        sends only after asking in a terminal."""
        agreed = _body(request).get("owner_agreed") is True
        return change(str(request.path_params["ref"]), False, owner_agreed=agreed)

    @allow(Caller.CLI)
    def pause_all(_request: Request) -> JSONResponse:
        return change(pause.ALL, True)

    @allow(Caller.CLI)
    def resume_all(_request: Request) -> JSONResponse:
        return change(pause.ALL, False)

    return [
        Route("/v1/addresses/{ref}/pause", pause_one, methods=["POST"]),
        Route("/v1/addresses/{ref}/resume", resume_one, methods=["POST"]),
        Route("/v1/pause-all", pause_all, methods=["POST"]),
        Route("/v1/resume-all", resume_all, methods=["POST"]),
    ]


def _decision_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §9.5, §15.1 (V1.2 step 7b): approve, reject, cancel, requeue, `approve --pending`.
    CLI only: decisions never accept an MCP profile token."""

    def _with_conn(fn: Callable[[sqlite3.Connection], Any]) -> JSONResponse:
        conn = state.connect()
        try:
            return JSONResponse(fn(conn))
        finally:
            conn.close()

    @allow(Caller.CLI)
    def approve(request: Request) -> JSONResponse:
        body, ref = _body(request), str(request.path_params["ref"])
        grant, nonce = _opt_str(body, "grant_id"), _opt_str(body, "nonce_id")
        return _with_conn(lambda c: approvals.approve(
            c, state.clock, state.notifier, ref, actor="os_user", grant_id=grant,
            nonce=nonce))  # fmt: skip

    @allow(Caller.CLI)
    def reject(request: Request) -> JSONResponse:
        ref = str(request.path_params["ref"])
        return _with_conn(lambda c: approvals.reject(c, state.clock, ref, actor="os_user"))

    @allow(Caller.CLI)
    def cancel(request: Request) -> JSONResponse:
        ref = str(request.path_params["ref"])
        return _with_conn(lambda c: approvals.cancel(c, state.clock, ref, actor="os_user"))

    @allow(Caller.CLI)
    def answer(request: Request) -> JSONResponse:
        body, ref = _body(request), str(request.path_params["ref"])
        text, nonce = _opt_str(body, "text"), _opt_str(body, "nonce_id")
        return _with_conn(lambda c: answers.answer(c, state.clock, ref, text, actor="os_user",
                                                   nonce=nonce))  # fmt: skip

    @allow(Caller.CLI)
    def requeue(request: Request) -> JSONResponse:
        ref, nonce = str(request.path_params["ref"]), _opt_str(_body(request), "nonce_id")
        return _with_conn(lambda c: approvals.requeue(c, state.clock, ref, actor="os_user",
                                                      nonce=nonce))  # fmt: skip

    @allow(Caller.CLI)
    def pending(request: Request) -> JSONResponse:
        body = _body(request)
        ids = body.get("confirm_ids")
        if ids is None:
            return _with_conn(approvals.pending)
        confirm, nonce = _str_list(ids), _opt_str(body, "nonce_id")
        return _with_conn(lambda c: {"results": approvals.approve_pending(
            c, state.clock, state.notifier, confirm, nonce=nonce)})  # fmt: skip

    @allow(Caller.CLI)
    def undo(request: Request) -> JSONResponse:
        """`ecf item undo <id>` (OD-507 §4.3, P3): the digest's Undo, at the next check."""
        ref = str(request.path_params["ref"])

        def queue(c: sqlite3.Connection) -> dict[str, Any]:
            item = inbox.find(c, ref)
            what = digests.queue_undo(c, state.clock, item, "os_user")
            return {"id": item["stable_id"], "undo": what}

        return _with_conn(queue)

    @allow(Caller.CLI)
    def scan(request: Request) -> JSONResponse:
        """`ecf approve --scan <run id>` (OD-507 §3.5, P9): the list, then the confirmed set."""
        body, run_id = _body(request), str(request.path_params["run"])
        ids = body.get("confirm_ids")
        if ids is None:
            return _with_conn(lambda c: approvals.scan_pending(c, run_id))
        confirm, nonce = _str_list(ids), _opt_str(body, "nonce_id")
        return _with_conn(lambda c: {"results": approvals.approve_scan(
            c, state.clock, state.notifier, run_id, confirm, nonce=nonce)})  # fmt: skip

    return [
        Route("/v1/approvals/pending", pending, methods=["POST"]),
        Route("/v1/approvals/scan/{run}", scan, methods=["POST"]),
        Route("/v1/items/{ref}/undo", undo, methods=["POST"]),
        Route("/v1/items/{ref}/approve", approve, methods=["POST"]),
        Route("/v1/items/{ref}/reject", reject, methods=["POST"]),
        Route("/v1/items/{ref}/cancel", cancel, methods=["POST"]),
        Route("/v1/items/{ref}/requeue", requeue, methods=["POST"]),
        Route("/v1/items/{ref}/answer", answer, methods=["POST"]),
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
        def build(c: sqlite3.Connection) -> dict[str, Any]:
            extra = {"runtime": dict(state.slack), "channels": slack_routes.list_channels(c)}
            return slack_admin.status(c) | extra

        return _with_conn(build)

    @allow(Caller.CLI)
    def create(request: Request) -> JSONResponse:
        token = _token(_body(request), "config_token")
        return _with_conn(lambda c: slack_admin.create_app(c, state.clock, state.slack_web,
                                                           token, state.install))  # fmt: skip

    @allow(Caller.CLI)
    def install(request: Request) -> JSONResponse:
        body = _body(request)
        bot, app, member = (_token(body, "bot_token"), _token(body, "app_token"),
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
        bot, app, nonce = _token(body, "bot_token"), _token(body, "app_token"), _nonce(body)
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
        token = _token(_body(request), "config_token")
        return _with_conn(lambda c: slack_admin.reauthorize(c, state.clock, state.slack_web,
                                                            token, state.install))  # fmt: skip

    @allow(Caller.CLI)
    def refresh(_request: Request) -> JSONResponse:
        return _with_conn(lambda c: {"queued": slack_admin.refresh(c, state.clock)})

    @allow(Caller.CLI)
    def remove(request: Request) -> JSONResponse:
        body = _body(request)
        typed, token = _str(body, "install"), _opt_str(body, "config_token")
        token = token.strip() if token else None
        with state.slack_hold():
            r = _with_conn(lambda c: slack_remove.run(
                c, state.clock, state.store(), state.slack_web, state.notifier,
                install=state.install, typed=typed, config_token=token,
                nonce=_nonce(body)))  # fmt: skip
        log.info("slack.removed")
        return r

    return [
        Route("/v1/slack", show, methods=["GET"]),
        Route("/v1/slack/app", create, methods=["POST"]),
        Route("/v1/slack/install", install, methods=["POST"]),
        Route("/v1/slack/tokens", tokens, methods=["POST"]),
        Route("/v1/slack/member", member, methods=["POST"]),
        Route("/v1/slack/reauthorize", reauthorize, methods=["POST"]),
        Route("/v1/slack/refresh", refresh, methods=["POST"]),
        Route("/v1/slack/remove", remove, methods=["POST"]),
    ]


def _check_routes(state: ServiceState, allow: Allow) -> list[Route]:
    @allow(Caller.CLI)
    def run_checks(request: Request) -> StreamingResponse:
        """SPEC §15.1 `POST /v1/checks`: one JSON line per check, then a summary line."""
        body = _body(request)
        wanted = body.get("address_id")
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

        lines = _check_lines(state, ids, secrets, factory)
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
                until=q.get("until"),
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
                    # V1.6: the internal set's other half, and what `address add` needs to know
                    # to skip the org-domains question and the IMAP server for Gmail (OD-441)
                    "org_addresses": len(internal.org_addresses(conn)),
                    "public_domains": sorted(PUBLIC_DOMAINS),
                    "imap_defaults": addresses.imap_defaults(),
                    "presets": list(addresses.offered_presets()),  # C only while D is held back
                    # preset D needs it (v3.0.0 step 4); by its secret's name
                    "typesafe_key": state.secrets is not None
                    and jev_keys.has_key(state.secrets.get),
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
            imap_host=_str(body, "imap_host") if body.get("imap_host") is not None else "",
            sensitivity=_str(body, "sensitivity"),
            preset=_str(body, "preset"),
            app_password=_str(body, "app_password"),
            address_id=_str(body, "address_id") if body.get("address_id") is not None else None,
            org_domains=_str_list(org) if org is not None else None,
            smtp_host=_str(body, "smtp_host") if body.get("smtp_host") is not None else None,
            smtp_port=_port(body.get("smtp_port")),
            # preset D (v3.0.0 step 4): asked in a terminal by `ecf address add` (P22)
            owner_agreed=body.get("owner_agreed") is True,
            stepup_nonce=_opt_str(body, "stepup_nonce"),
        )
        conn = state.connect()
        try:
            a = addresses.add_address(
                conn,
                state.clock,
                state.store(),
                _factory(),
                req,
                actor="os_user",
                sender_factory=state.sender_factory,
            )
        finally:
            conn.close()
        log.info("address.added", address_id=a["address_id"])
        a["slack_channel"] = _planned_channel(state, a["address_id"])
        return JSONResponse(a, status_code=201)

    @allow(Caller.CLI)
    def set_address(request: Request) -> JSONResponse:
        ref = str(request.path_params["ref"])
        body = _body(request)
        unknown = set(body) - {"app_password", "smtp_host", "smtp_port", "stepup_nonce",
                               *send_limits.DEFAULTS}  # fmt: skip
        if unknown:
            raise InvalidInputError(f"can't set {', '.join(sorted(unknown))} here yet")
        if "smtp_host" in body:
            return JSONResponse(_set_smtp(state, ref, body))
        if set(body) & set(send_limits.DEFAULTS):
            return JSONResponse(_set_limits(state, ref, body))
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
                sender_factory=state.sender_factory,
            )
        finally:
            conn.close()
        log.info("secret.written", address_id=a["address_id"])
        return JSONResponse(a)

    @allow(Caller.CLI)
    def remove_address(request: Request) -> JSONResponse:
        nonce = _body(request).get("stepup_nonce")
        ref = str(request.path_params["ref"])
        return JSONResponse(_remove_address(state, ref, nonce if isinstance(nonce, str) else None))

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


def _remove_address(state: ServiceState, ref: str, nonce: str | None) -> dict[str, Any]:
    """Resolve open items (step-up for payment or fraud, OD-218), stop watching, then archive
    the address's recorded Slack channel only."""
    conn = state.connect()
    try:
        a = addresses.remove_address(conn, state.clock, state.store(), ref, actor="os_user",
                                     nonce=nonce)  # fmt: skip
        a["slack_channel_archived"] = slack_routes.archive(conn, state.clock, a["address_id"])
    finally:
        conn.close()
    log.info("address.removed", address_id=a["address_id"])
    return a


def _planned_channel(state: ServiceState, address_id: str) -> str | None:
    """The channel name an added address gets within a minute (the name may gain a suffix if
    taken), or None when the Slack thread won't create it: Slack counts as installed only with its
    IDs and both tokens (as the runtime starts it), and channels wait for your confirmed member ID
    (slack_routes.ensure)."""
    conn = state.connect()
    try:
        ident = slack_admin.identity(conn)
    finally:
        conn.close()
    store = state.secrets
    ready = (
        ident is not None
        and bool(ident.member)
        and store is not None
        and bool(store.get(slack_admin.BOT_SECRET))
        and bool(store.get(slack_admin.APP_SECRET))
    )
    return slack_routes.channel_name(state.install, address_id) if ready else None


MAX_BODY = 64 * 1024


_VERIFIED: dict[tuple[str, int, int], dict[str, Any]] = {}


def _verified(root: Path, verify: Callable[[Path], dict[str, Any]]) -> dict[str, Any]:
    """A generated set's manifest after `verify`, checked once per set while its manifest and
    labels index are unchanged (one grading run makes hundreds of calls; J19)."""

    def mtime(p: Path) -> int:
        return p.stat().st_mtime_ns if p.exists() else 0

    key = (str(root), mtime(root / "manifest.json"), mtime(root / "labels.jsonl"))
    if key not in _VERIFIED:
        if len(_VERIFIED) > 16:
            _VERIFIED.clear()
        _VERIFIED[key] = verify(root)
    return _VERIFIED[key]


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


def _set_smtp(state: ServiceState, ref: str, body: dict[str, Any]) -> dict[str, Any]:
    """`ecf address set --smtp-host` (step-up, Security Notice; OD-324)."""
    if "app_password" in body:
        raise InvalidInputError("set the app password and the SMTP server separately")
    nonce = body.get("stepup_nonce")
    conn = state.connect()
    try:
        return addresses.set_smtp(
            conn,
            state.clock,
            lambda text: _notice(state, conn, text),
            state.store(),
            state.sender_factory,
            ref,
            _str(body, "smtp_host"),
            _port(body.get("smtp_port")) or 465,
            actor="os_user",
            nonce=nonce if isinstance(nonce, str) else None,
        )
    finally:
        conn.close()


def _set_limits(state: ServiceState, ref: str, body: dict[str, Any]) -> dict[str, Any]:
    """`ecf address set --max-sends-per-hour|--max-sends-per-day` (step-up; §9.6)."""
    new = {k: v for k, v in body.items() if k in send_limits.DEFAULTS}
    if any(not isinstance(v, int) or isinstance(v, bool) for v in new.values()):
        raise InvalidInputError("send limits must be numbers")
    if set(body) - set(new) - {"stepup_nonce"}:
        raise InvalidInputError("set send limits on their own")
    nonce = body.get("stepup_nonce")
    conn = state.connect()
    try:
        return send_limits.set_limits(conn, state.clock, lambda t: _notice(state, conn, t), ref,
                                      new, actor="os_user",
                                      nonce=nonce if isinstance(nonce, str) else None)  # fmt: skip
    finally:
        conn.close()


def _port(v: Any) -> int | None:
    if v is None:
        return None
    if not isinstance(v, int) or isinstance(v, bool):
        raise InvalidInputError("smtp_port must be a number")
    return v


KEY_CHECK_S = 5.0  # an API key's check, under the CLI's 10 s socket timeout (P12)
_KEY_NAMES = {"anthropic": "Anthropic Models API key", "typesafe": "TypeSafe API key",
              "mistral": "Mistral API key"}  # fmt: skip


def _api_key(request: Request, provider: str) -> str | None:
    """The pasted key, checked as the CLI checks it (prefix, characters), or None to clear."""
    body = _body(request)
    if body.get("clear") is True:
        return None
    return api_keys.check_key(provider, _token(body, "key"))


def _key_notice(state: ServiceState, conn: sqlite3.Connection, provider: str, stored: bool,
                had: bool) -> None:  # fmt: skip
    """A Security Notice on every set, replace and clear of either key (D3, P12); a clear of a
    key that wasn't there changes nothing and posts none."""
    if not stored and not had:
        return
    verb = ("replaced" if had else "set") if stored else "removed"
    if provider == "mistral":
        what = ("`ecf eval grade` with Mistral uses it" if stored
                else "`ecf eval grade` with Mistral stops")  # fmt: skip
    elif provider == "typesafe":
        what = ("a running Jev eval stops and preset D mail waits" if not stored
                else "Jev evals and preset D addresses use it")  # fmt: skip
    else:
        what = (
            "the weekly model watch uses it"
            if stored
            else "the weekly model watch stops reading Anthropic's model list"
        )
    _notice(state, conn, f"The {_KEY_NAMES[provider]} was {verb} (install `{state.install}`);"
            f" {what}. If you didn't do this, clear it with `ecf models api-key clear --provider"
            f" {provider}`.")  # fmt: skip


def _notice(state: ServiceState, conn: sqlite3.Connection, text: str) -> None:
    """A Security Notice to the desktop, your DM and the summary channel (§13.3)."""
    ident = slack_admin.identity(conn)
    slack_admin.notice(conn, state.clock, state.notifier, text,
                       dms=[ident.member] if ident and ident.member else [])  # fmt: skip


def _str(body: dict[str, Any], key: str) -> str:
    v = body.get(key)
    if not isinstance(v, str):
        raise InvalidInputError(f"{key} must be a string")
    return v


def _token(body: dict[str, Any], key: str) -> str:
    """A pasted token: spaces and line breaks around it removed (a copy often brings one; V1.2
    shadow run, 2026-09-30)."""
    return _str(body, key).strip()


def _opt_str(body: dict[str, Any], key: str) -> str | None:
    v = body.get(key)
    if v is not None and not isinstance(v, str):
        raise InvalidInputError(f"{key} must be text")
    return v


def _copy_to(body: dict[str, Any]) -> Path | None:
    """`ecf eval run --copy-to`: an absolute folder for a copy of the result (plan 0.3)."""
    v = _opt_str(body, "copy_to")
    if v is None:
        return None
    if not Path(v).is_absolute():
        raise InvalidInputError("copy_to: a folder for a copy of the result (an absolute path)")
    return Path(v)


def _variants(v: Any) -> dict[str, list[dict[str, Any]]]:
    """`ecf eval expect`'s values to combine (draft 21): per case id, a list of values for
    sender_type and priority (no other field), each one the schema allows; at most
    `MAX_VARIANTS` combinations, which are returned in field order."""
    from itertools import product  # noqa: PLC0415

    from ecf.eval import labels as label_file  # noqa: PLC0415 - eval only

    if v is None:
        return {}
    if not isinstance(v, dict):
        raise InvalidInputError("variants must be an object of case id to field values")
    spec = load_schema().fields
    out: dict[str, list[dict[str, Any]]] = {}
    for cid, given in cast(dict[str, Any], v).items():
        if not isinstance(given, dict) or not given:
            raise InvalidInputError(f"variants for {cid!r}: an object of field to values")
        options = cast(dict[str, Any], given)
        for f, vals in options.items():
            if f not in label_file.UNASKED:
                raise InvalidInputError(f"variants may set only {', '.join(label_file.UNASKED)}")
            allowed = set(spec[f].values or ())  # both fields take named values
            if not isinstance(vals, list) or not vals or any(
                    not isinstance(x, str) or x not in allowed
                    for x in cast(list[Any], vals)):  # fmt: skip
                raise InvalidInputError(f"variants for {cid!r}: {f} takes a list of its values")
            options[f] = list(dict.fromkeys(cast(list[str], vals)))
        fields = list(options)
        made = [dict(zip(fields, t, strict=True))
                for t in product(*(cast(list[Any], options[f]) for f in fields))]  # fmt: skip
        if len(made) > label_file.MAX_VARIANTS:
            raise InvalidInputError(f"variants for {cid!r}: at most {label_file.MAX_VARIANTS}"
                                    " combinations")  # fmt: skip
        out[str(cid)] = made
    return out


def _str_list(v: Any) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in cast(list[Any], v)):
        raise InvalidInputError("expected a list of strings")
    return cast(list[str], v)
