"""Slack clicks and form submissions (SPEC §10.1; V1.2 step 3b).

On Slack's listener thread (`Inbound.on_envelope`), only what must happen at once:
1. acknowledge the envelope (Slack retries anything unacknowledged within 3 s);
2. refuse anything not from this app, this workspace and your member ID (OD-084), audited with the
   clicker's member ID only, never the payload;
3. drop a repeat of an envelope already seen (`slack_dedupe`);
4. for a button that opens a form, open it now (`views.open` needs the 3-second `trigger_id`);
5. hand the click to the durable `slack_in` queue.

A worker (`SlackReceiver`) then runs the registered handler for the click's action. A button's
value is an opaque reference: handlers load the action from the grant or item it names, never
from the payload, and edit cards by their stored ts (§10.1, security review of the V1.2 plan).
Clicks handled while the Mac sleeps (a brief wake on AC power, tested 2026-09-29) must be safe
without a form; form buttons need an awake Mac.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any, Literal, cast

from ecf.errors import EcfError
from ecf.ids import AddressId
from ecf_server import jobs
from ecf_server._slack import Envelope
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.slack_render import action_name

WORKER = "slack-in"
TIMEOUT_S = 60
VALUE_MAX = 3000  # a form field; longer is cut
FIELDS_MAX = 10
DEDUPE_KEEP = timedelta(days=1)


@dataclass(frozen=True)
class SlackIdentity:
    """Who may click: this app, this workspace, and you (OD-084)."""

    app_id: str
    team_id: str
    member: str


@dataclass(frozen=True)
class Click:
    kind: Literal["button", "form"]
    action: str
    ref: str  # opaque: a grant or item reference
    channel: str | None
    user: str
    values: dict[str, str] = field(default_factory=dict[str, str])  # form fields, capped


Handler = Callable[[sqlite3.Connection, Clock, Click], None]
FormBuilder = Callable[[Click], dict[str, Any]]
HANDLERS: dict[str, Handler] = {}
FORMS: dict[str, FormBuilder] = {}  # buttons that open a form instead of queueing a click


def handles(action: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS[action] = fn
        return fn

    return register


def opens_form(action: str) -> Callable[[FormBuilder], FormBuilder]:
    """The form's `callback_id` must be the action its submission is handled by, and its
    `private_metadata` the opaque reference."""

    def register(fn: FormBuilder) -> FormBuilder:
        FORMS[action] = fn
        return fn

    return register


class Inbound:
    def __init__(
        self,
        ident: SlackIdentity,
        clock: Clock,
        connect: Callable[[], sqlite3.Connection],
        ack: Callable[[str], None],
        open_view: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self._ident, self._clock, self._connect = ident, clock, connect
        self._ack, self._open_view = ack, open_view

    def on_envelope(self, env: Envelope) -> None:
        self._ack(env.envelope_id)
        if env.type != "interactive":
            return
        p = env.payload
        conn = self._connect()
        try:
            why = refusal(p, self._ident)
            if why is not None:
                _refused(conn, self._clock, why, _user(p))
                return
            if not _first_time(conn, self._clock, env.envelope_id):
                return
            click = to_click(p)
            if click is None:
                return
            form = FORMS.get(click.action) if click.kind == "button" else None
            if form is not None:
                self._open_view(str(p.get("trigger_id", "")), form(click))
                return
            jobs.enqueue(conn, self._clock, jobs.Queue.SLACK_IN, AddressId(click.user),
                         asdict(click), timeout_s=TIMEOUT_S, max_attempts=3)  # fmt: skip
        finally:
            conn.close()


class SlackReceiver:
    """Runs queued clicks, one per `run_once`."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def run_once(self, conn: sqlite3.Connection) -> bool:
        job = jobs.claim(conn, self._clock, jobs.Queue.SLACK_IN, WORKER)
        if job is None:
            return False
        p = job.payload
        click = Click(p["kind"], p["action"], p["ref"], p.get("channel"), p["user"],
                      dict(p.get("values") or {}))  # fmt: skip
        handler = HANDLERS.get(click.action)
        try:
            if handler is None:
                _audit(conn, self._clock, "slack.click_unknown", {"action": click.action})
            else:
                handler(conn, self._clock, click)
        except EcfError as exc:  # refused by policy or state: the person can act again
            _audit(conn, self._clock, "slack.click_failed",
                   {"action": click.action, "code": exc.code.value})  # fmt: skip
        except Exception as exc:
            jobs.fail(conn, self._clock, job.job_id, WORKER, type(exc).__name__)
            log.error("slack.handler_crashed", action=click.action, error_type=type(exc).__name__)
            return True
        jobs.complete(conn, job.job_id, WORKER)
        return True


def refusal(p: dict[str, Any], ident: SlackIdentity) -> str | None:
    if p.get("api_app_id") != ident.app_id:
        return "wrong_app"
    if _obj(p.get("team")).get("id") != ident.team_id:
        return "wrong_team"
    if _user(p) != ident.member:
        return "not_you"
    return None


def to_click(p: dict[str, Any]) -> Click | None:
    kind = p.get("type")
    user = _user(p)
    if kind == "block_actions":
        actions = cast("list[Any]", p.get("actions") or [])
        if not actions:
            return None
        a = _obj(actions[0])
        channel = _obj(p.get("channel")).get("id")
        action = action_name(str(a.get("action_id", "")))
        return Click("button", action, str(a.get("value", "")), _str_or_none(channel), user)
    if kind == "view_submission":
        view = _obj(p.get("view"))
        values: dict[str, str] = {}
        state = _obj(_obj(view.get("state")).get("values"))
        for block_id, inputs in list(state.items())[:FIELDS_MAX]:
            for element in _obj(inputs).values():
                values[block_id] = str(_obj(element).get("value") or "")[:VALUE_MAX]
        callback, ref = str(view.get("callback_id", "")), str(view.get("private_metadata", ""))
        return Click("form", callback, ref, None, user, values)
    return None


def _obj(v: Any) -> dict[str, Any]:
    """A JSON object from an untyped payload, or an empty one."""
    return cast("dict[str, Any]", v) if isinstance(v, dict) else {}


def _str_or_none(v: Any) -> str | None:
    return None if v is None else str(v)


def prune_dedupe(conn: sqlite3.Connection, clock: Clock) -> int:
    with write_tx(conn):
        return conn.execute(
            "DELETE FROM slack_dedupe WHERE seen_at < ?", (to_ts(clock.now() - DEDUPE_KEEP),)
        ).rowcount


def _user(p: dict[str, Any]) -> str:
    return str(_obj(p.get("user")).get("id", ""))


def _first_time(conn: sqlite3.Connection, clock: Clock, envelope_id: str) -> bool:
    with write_tx(conn):
        return (
            conn.execute(
                "INSERT INTO slack_dedupe (payload_id, seen_at) VALUES (?, ?)"
                " ON CONFLICT (payload_id) DO NOTHING",
                (envelope_id, to_ts(clock.now())),
            ).rowcount
            == 1
        )


def _refused(conn: sqlite3.Connection, clock: Clock, why: str, member: str) -> None:
    _audit(conn, clock, "slack.click_refused", {"reason": why, "member": member[:32]},
           outcome="denied")  # fmt: skip


def _audit(
    conn: sqlite3.Connection, clock: Clock, event: str, data: dict[str, Any], outcome: str = "ok"
) -> None:
    with write_tx(conn):
        conn.execute(
            "INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?, ?, 'service', ?, ?)",
            (to_ts(clock.now()), event, outcome, json.dumps(data)),
        )
