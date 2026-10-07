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
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field, replace
from datetime import timedelta
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
    PolicyDeniedError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from ecf.ids import new_random_id
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
    fallback,
    gate,
    health,
    import_plan,
    importer,
    inbox,
    initsetup,
    internal,
    manual_export,
    model_watch,
    modelq,
    models,
    ollama,
    ops_doctor,
    outbound,
    outbound_remind,
    passphrase,
    pause,
    restore,
    retention,
    ruletest,
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
)
from ecf_server import upgrade_state as upgrade_state_mod
from ecf_server.chat import FakeChat
from ecf_server.clock import Clock, FakeClock, SystemClock, to_ts
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
    # ecf's GitHub releases for the weekly watch; set only by `ecf-server local` (§7.6)
    watch_releases: model_watch.Releases | None = field(default=None, repr=False)
    model_client: Callable[[], ollama.Client] = field(default=ollama.Client, repr=False)  # a fake
    model_check: dict[str, Any] = field(default_factory=dict[str, Any], repr=False)  # tests: run=
    model_work: modelq.Work | None = field(default=None, repr=False)  # the classifier (V1.3 step 3)
    shadow_work: modelq.Work | None = field(default=None, repr=False)  # the fallback's (V1.4)
    # generation speeds for the heat judgement, shared by the worker's rounds and `ecf check`'s
    throttle: modelq.Throttle = field(default_factory=modelq.Throttle, repr=False)
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
                "model": _model_status(state),
                "claude": _claude_status(state),
                "fallback": _with_db(state, fallback.status, []),
                "model_watch": _with_db(state, model_watch.status, None),
            }
        )

    @allow(Caller.CLI)
    def create_session(_request: Request) -> JSONResponse:
        s = _new_session(state)
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
MAX_ROUNDS = 100  # `--until-empty` stops after this many checks per address
UNTIL_EMPTY_MAX_S = 3 * 3600  # `--until-empty` stops waiting after this long
BUSY_POLL_S = 5.0  # while the scheduled check holds an address
WORKER_POLL_S = 10.0  # while the service's own model rounds hold the waiting emails


def _wait(seconds: float) -> None:
    """Sleep inside a streaming response (Starlette runs the generator on a worker thread)."""
    time.sleep(seconds)


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

    deadline = state.clock.monotonic() + UNTIL_EMPTY_MAX_S
    for address_id in ids:
        rounds, told = 0, False
        while rounds < (MAX_ROUNDS if until_empty else 1):
            try:
                r = one(address_id)
            except Exception as exc:  # the response has started: report it, don't cut off
                log.error("api.check_failed", error_type=type(exc).__name__)
                failed = {"address_id": address_id, "status": "internal_error"}
                yield json.dumps(failed | {"error": f"internal error ({type(exc).__name__})"})
                yield "\n"
                break
            if r.status == "busy" and until_empty and state.clock.monotonic() < deadline:
                if not told:  # the scheduled check holds it: wait for it, then check (12a)
                    yield json.dumps({"address_id": address_id, "status": "waiting"}) + "\n"
                    told = True
                _wait(BUSY_POLL_S)
                continue
            rounds += 1
            yield json.dumps(r.to_json()) + "\n"
            if not r.more:
                break
    for line in _model_lines(state, until_empty, deadline):
        yield json.dumps({"model": line}) + "\n"
    yield json.dumps({"done": True, "addresses": len(ids)}) + "\n"


def _model_lines(state: ServiceState, until_empty: bool,
                 deadline: float | None = None) -> Iterator[dict[str, Any]]:  # fmt: skip
    """`ecf check` runs the local model on what waits (V1.3 step 7): one round, or with
    `--until-empty` rounds until nothing waits. The model worker may run rounds too; each item is
    taken under its address's lock and lease, so the two never work on the same one. With
    `--until-empty` (step 12a fix) a heat pause is waited out, and when the worker holds the
    waiting emails this waits for it, reporting the count as it falls, until nothing waits, the
    model isn't ready, an eval holds it or `UNTIL_EMPTY_MAX_S` has passed."""
    work = state.model_work
    if work is None:
        yield {"status": "off", "detail": "this service doesn't run the local model"}
        return
    end = deadline if deadline is not None else state.clock.monotonic() + UNTIL_EMPTY_MAX_S
    shown: int | None = None
    for _ in range(MAX_ROUNDS * 100 if until_empty else 1):
        line = _model_round(state, work)
        settled = line["status"] in ("not_ready", "eval", "stopped") or line["waiting"] == 0
        if not until_empty or settled:
            yield line
            return
        if state.clock.monotonic() >= end:
            yield line | {"status": "gave_up", "minutes": round(UNTIL_EMPTY_MAX_S / 60)}
            return
        if line["status"] == "hot":
            yield line
            _wait(modelq.HEAT_PAUSE.total_seconds())
        elif line["done"] == 0 and line["failed"] == 0:  # the worker holds them: wait for it
            if line["waiting"] != shown:
                yield {"status": "worker", "done": 0, "failed": 0, "waiting": line["waiting"]}
                shown = line["waiting"]
            _wait(WORKER_POLL_S)
        else:
            yield line


def _model_round(state: ServiceState, work: modelq.Work) -> dict[str, Any]:
    conn, client = state.connect(), state.model_client()
    power = state.power()
    state.throttle.power(not (power.laptop and not power.on_ac))
    try:
        r = modelq.run_round(conn, state.clock, state.notifier, client, work,
                             resident=modelq.resident(conn), check_kw=state.model_check,
                             throttle=state.throttle, shadow=state.shadow_work)  # fmt: skip
        line: dict[str, Any] = {"status": r.status, "done": r.done, "failed": r.failed,
                                "waiting": r.waiting}  # fmt: skip
        if r.status == "not_ready":
            row = conn.execute(
                "SELECT detail FROM alerts WHERE kind IN ('local_model',"
                " 'local_model_unsafe') AND resolved_at IS NULL"
            ).fetchone()
            line["detail"] = row[0] if row else "the local model isn't ready"
        return line
    finally:
        client.close()
        conn.close()


def _model_status(state: ServiceState) -> dict[str, Any] | None:
    if state.db_path is None:
        return None
    power = state.power()
    conn = state.connect()
    try:
        return modelq.status(conn, laptop=power.laptop, on_ac=power.on_ac)
    finally:
        conn.close()


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
        return _with_conn(lambda c: {"items": inbox.inbox(c, address_id=aid, stale_only=stale)})

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
        if key == settings.FALLBACK_KEY:  # the local fallback: step-up to turn it on (V1.4)
            if not aid:
                raise InvalidInputError(f"{key} is per address: add --address")
            nonce = _opt_str(body, "nonce_id")
            return _with_conn(lambda c: fallback.set_timeout(c, state.clock, state.notifier, aid,
                                                             value, nonce=nonce))  # fmt: skip
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


def _config_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §8.6, §9.7 (V1.2 step 10b): `ecf config apply` (step-up) and `ecf rules test`."""

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

    return [
        Route("/v1/config/apply", apply_config, methods=["POST"]),
        Route("/v1/rules/test", test_rules, methods=["POST"]),
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


def _corpus_run(sid: str | None, *, fraud_only: bool) -> evalrun.CorpusRun | None:
    """The open corpus session an eval run takes over (R154); None for the synthetic set."""
    if not sid:
        return None
    if fraud_only:
        raise InvalidInputError("--fraud-only is for the synthetic set")
    session = corpus_session.set_busy(sid, True)
    try:
        return evalrun.corpus_run(session)
    except Exception:
        corpus_session.set_busy(sid, False)
        raise


def _eval_routes(state: ServiceState, allow: Allow) -> list[Route]:  # noqa: PLR0915 - route table
    """SPEC §15.1, §16.2 (V1.3 step 8c): `ecf eval run|status|stop`, CLI only; from V1.4 step 7
    also `ecf eval run --claude`, which registers a run for `/ecf-eval` (its MCP side is in
    `_review_routes`)."""

    @allow(Caller.CLI)
    def start_eval(request: Request) -> JSONResponse:
        body = _body(request)
        sid = _opt_str(body, "corpus_session")  # a real-mail corpus instead (§16.7)
        root = Path(_str(body, "root")).expanduser() if not sid else Path("/")
        if not sid and (not root.is_absolute() or not (root / "labels.jsonl").is_file()):
            raise InvalidInputError("root: the synthetic set's folder (an absolute path)")
        floor = body.get("battery_floor", evalrun.DEFAULT_FLOOR)
        if isinstance(floor, bool) or not isinstance(floor, int) or not 0 <= floor <= 100:
            raise InvalidInputError("battery_floor: a percent from 0 to 100")
        if state.db_path is None:
            raise ServiceUnavailableError("the service has no database yet")
        corpus_run = _corpus_run(sid, fraud_only=body.get("fraud_only") is True)
        opts = evalrun.Options(root, classifier=body.get("classifier") is not False,
                               actor=body.get("actor") is not False,
                               fraud_only=body.get("fraud_only") is True,
                               battery_floor=floor,
                               backend=_opt_str(body, "backend") or evalrun.GEMMA,
                               redact=body.get("redact") is not False,
                               corpus=corpus_run)  # fmt: skip
        try:
            run = evalrun.start(state.connect, state.clock, state.model_client,
                                state.db_path.parent, opts, power=state.power,
                                check_kw=state.model_check, notifier=state.notifier)  # fmt: skip
        except Exception:
            if sid:
                corpus_session.set_busy(sid, False)
            raise
        evalrun.note(state.connect, state.clock,
                     f"Eval {run['run_id'][:8]} started ({run['total']} cases): model checks"
                     " for new mail wait until it ends; fraud checks go on.")  # fmt: skip
        power = state.power()
        on_battery = power.laptop and not power.on_ac
        pct = schedule.battery_percent() if on_battery else None
        return JSONResponse(run | {"on_battery": on_battery, "battery": pct})

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
        local = evalrun.stop() if evalrun.RUN.snapshot()["state"] in ("running", "paused") else None
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
            actor_model=_opt_str(body, "actor_model"), batch=batch)  # fmt: skip
        return JSONResponse(claude_eval.start(state.connect, state.clock, state.db_path.parent,
                                              opts))  # fmt: skip

    return [
        Route("/v1/eval/claude", start_claude_eval, methods=["POST"]),
        Route("/v1/eval/runs", start_eval, methods=["POST"]),
        Route("/v1/eval/runs", eval_status, methods=["GET"]),
        Route("/v1/eval/runs/stop", stop_eval, methods=["POST"]),
    ]


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
        return _with_conn(lambda c: claude_review.review_queue(
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
        recorded = state.telemetry.plan(_session(request),
                                        telemetry.parse_plan(_body(request)))  # fmt: skip
        return JSONResponse({"recorded": recorded})

    return [
        Route("/v1/review-queue", review_queue, methods=["POST"]),
        Route("/v1/claims/{ref}/message", message, methods=["POST"]),
        Route("/v1/claims/{ref}/classification", classification, methods=["POST"]),
        Route("/v1/claims/{ref}/proposal", proposal, methods=["POST"]),
        Route("/v1/eval/next", eval_next, methods=["POST"]),
        Route("/v1/eval/results", eval_results, methods=["POST"]),
        Route("/v1/statusline", statusline, methods=["POST"]),
    ]


def _watch_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §7.6 (V1.4 step 10): the weekly model watch's key and a run now."""

    @allow(Caller.CLI)
    def api_key(request: Request) -> JSONResponse:
        """SPEC §7.6 (V1.4 step 10): `ecf models api-key set|clear`; the service alone writes the
        secret store."""
        body = _body(request)
        key = None if body.get("clear") is True else _token(body, "key")
        conn = state.connect()
        try:
            r = model_watch.set_key(conn, state.clock, state.store(), key, state.watch_http)
        finally:
            conn.close()
        log.info("models.api_key_cleared" if key is None else "models.api_key_set")
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
        Route("/v1/models/api-key", api_key, methods=["POST"]),
        Route("/v1/models/watch", watch_now, methods=["POST"]),
    ]


def _model_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §7.5, §13.2 (V1.3 step 1b): the local model's status and `ecf models install`."""

    @allow(Caller.CLI)
    def show_models(_request: Request) -> JSONResponse:
        conn, client = state.connect(), state.model_client()
        try:
            return JSONResponse(
                models.status(conn, client, **state.model_check)
                | {"watch": model_watch.status(conn)}
            )  # V1.4 step 10
        finally:
            client.close()
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
    def install_models(request: Request) -> JSONResponse:
        """`ecf models install`; with `decision`, an eval-only decision model (SPEC §7.8)."""
        decision = _opt_str(_body(request), "decision")
        return JSONResponse(models.start_install(state.connect, state.clock, state.model_client,
                                                 decision=decision))  # fmt: skip

    @allow(Caller.CLI)
    def show_stats(request: Request) -> JSONResponse:
        """SPEC §13.4 (V1.3 step 9): `ecf stats`; Claude's part (V1.4 step 6) isn't split by
        address, so it comes only without `address` and for presets B and C."""
        q = request.query_params
        try:
            hours = float(q.get("hours", "168"))
        except ValueError as exc:
            raise InvalidInputError("hours: a number") from exc
        if not 0 < hours <= 24 * 366:
            raise InvalidInputError("hours: more than 0, at most a year")
        conn = state.connect()
        try:
            ref = q.get("address")
            aid = addresses.get_address(conn, ref)["address_id"] if ref else None
            since = state.clock.now() - timedelta(hours=hours)
            preset = q.get("preset")
            out = stats.report(conn, since, address=aid, preset=preset)
            if aid is None and preset in (None, "B", "C"):
                out["claude"] = claude_usage.report(conn, since)
            return JSONResponse(out)
        finally:
            conn.close()

    return [
        Route("/v1/models", show_models, methods=["GET"]),
        Route("/v1/models/install", install_models, methods=["POST"]),
        Route("/v1/models/claude-override", claude_override, methods=["POST"]),
        Route("/v1/stats", show_stats, methods=["GET"]),
    ]


def _data_routes(state: ServiceState, allow: Allow) -> list[Route]:
    """SPEC §6.5, §10.2 (V1.2 step 11): retention and backfill; CLI only."""

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

    @allow(Caller.CLI)
    def start_backfill(request: Request) -> JSONResponse:
        body = _body(request)
        ref, since, act = _str(body, "address_id"), _str(body, "since"), body.get("act") is True
        conn = state.connect()
        try:
            r = backfill.start(conn, state.clock, ref, since, act=act)
        finally:
            conn.close()
        return JSONResponse(r)

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
        Route("/v1/retention", show_retention, methods=["GET"]),
        Route("/v1/retention", set_retention, methods=["POST"]),
        Route("/v1/backfill", start_backfill, methods=["POST"]),
        Route("/v1/backfill", backfill_status, methods=["GET"]),
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
        """`ecf eval rescore`: a corpus result against the session's current labels (R53)."""
        body = _body(request)
        s = corpus_session.get(_str(body, "corpus_session"), state.clock.monotonic)
        if s.busy:
            raise InvalidInputError("an eval run is using this corpus; wait for it to end")
        path = Path(_str(body, "result")).expanduser()
        if path.suffix != ".json" or not path.is_file():
            raise InvalidInputError(f"no result file at {path}")
        return JSONResponse(evalrun.rescore(s, path, state.clock))

    return [
        Route("/v1/eval/rescore", rescore, methods=["POST"]),
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

    def change(ref: str, paused: bool) -> JSONResponse:
        conn = state.connect()
        try:
            changed = pause.set_paused(conn, state.clock, ref, paused, actor="os_user")
        finally:
            conn.close()
        return JSONResponse({"changed": changed, "message": pause.describe(changed, paused)})

    @allow(Caller.CLI)
    def pause_one(request: Request) -> JSONResponse:
        return change(str(request.path_params["ref"]), True)

    @allow(Caller.CLI)
    def resume_one(request: Request) -> JSONResponse:
        return change(str(request.path_params["ref"]), False)

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

    return [
        Route("/v1/approvals/pending", pending, methods=["POST"]),
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
                    # V1.6: the internal set's other half, and what `address add` needs to know
                    # to skip the org-domains question and the IMAP server for Gmail (OD-441)
                    "org_addresses": len(internal.org_addresses(conn)),
                    "public_domains": sorted(PUBLIC_DOMAINS),
                    "imap_defaults": addresses.imap_defaults(),
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


def _str_list(v: Any) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in cast(list[Any], v)):
        raise InvalidInputError("expected a list of strings")
    return cast(list[str], v)
