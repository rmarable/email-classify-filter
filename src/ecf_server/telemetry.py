"""Claude Code telemetry for `ecf claude` sessions (SPEC §7.5, §11.5, §13.4; V1.4 step 6).

Each session gets a bearer token for the loopback OTLP/JSON receiver (`telemetry_app`). From the
log events the receiver keeps only numbers, model IDs and fixed codes, in memory per session:

- **API requests** (`api_request`): model, `query_source`, input/output/cache tokens, duration and,
  when present, `cost_usd` (an API-equivalent figure); each also becomes a `claude_calls` row.
- **Tool calls**: the event sequence at which each `tool_use_id` first appears.

Everything else (user email, account and organization IDs, prompt and tool fields, which arrive
redacted anyway) is dropped on arrival (§12.4).

**Binding** (operator decision 2026-10-02, OD-268; mechanism tested 2026-10-02, §21.2): an MCP call
carries `_meta.claudecode/toolUseId`, equal to the telemetry `tool_use_id`; the API request just
before that tool event (by `event.sequence`) made the call, and gives its model and query source.
This assumes that request belongs to the same agent: `/ecf-review` runs one agent type's spawns at
a time (all on one model), so parallel spawns of that agent share the model. Unverified with
parallel spawns, confirm in V1.4 step 13; a wrong binding refuses work (it can't widen access).

Submissions waiting for their binding (`Hold`) are kept here too, and settled by `claude_review`
(or `claude_eval` for `/ecf-eval`).
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, cast

SUBAGENT = "agent:custom"  # query_source of a plugin agent (tested 2026-10-02)
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/\[\]-]{0,99}$")
_SOURCE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,63}$")
_TOOL_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_RESETS = re.compile(r"^[0-9A-Za-z:.+_-]{1,40}$")


def valid_tool_use_id(v: object) -> str | None:
    return v if isinstance(v, str) and _TOOL_ID.match(v) else None


@dataclass(frozen=True)
class Seen:
    """What telemetry says about one tool call: the model and query source of its request."""

    model: str
    source: str


@dataclass(frozen=True)
class ApiCall:
    seq: int | None
    model: str
    source: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    duration_ms: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True)
class Plan:
    """Plan-window usage from the status line (Pro and Max only)."""

    five_hour: float | None
    seven_day: float | None
    five_hour_resets: str | None
    seven_day_resets: str | None


@dataclass(frozen=True)
class Hold:
    """A checked submission waiting for telemetry to bind its call to a model."""

    session_id: str
    tool_use_id: str
    stable_id: str
    fence: int
    need: str
    agent: str
    payload: dict[str, Any]
    kind: str = "review"  # review (`/ecf-review`, claude_review) | eval (`/ecf-eval`, claude_eval)


@dataclass
class Tel:
    session_id: str
    bearer_hash: str
    requests: list[ApiCall] = field(default_factory=list[ApiCall])
    tools: dict[str, int] = field(default_factory=dict[str, int])
    holds: list[Hold] = field(default_factory=list[Hold])
    first_plan: Plan | None = None
    last_plan: Plan | None = None
    items: set[str] = field(default_factory=set[str])
    submitted: int = 0
    refused: int = 0
    refused_model: str | None = None
    expected_model: str | None = None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogateescape")).hexdigest()


class Telemetry:
    """Per-session telemetry and holds, in memory (sessions don't outlive the service)."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._sessions: dict[str, Tel] = {}
        self.port: int | None = None  # the receiver's loopback port, once it listens

    def open(self, session_id: str) -> str:
        bearer = secrets.token_urlsafe(32)
        with self._cond:
            self._sessions[session_id] = Tel(session_id, _hash(bearer))
        return bearer

    def close(self, session_id: str) -> Tel | None:
        with self._cond:
            return self._sessions.pop(session_id, None)

    def session_for(self, bearer: str) -> str | None:
        h = _hash(bearer)
        with self._cond:
            for t in self._sessions.values():
                if secrets.compare_digest(h, t.bearer_hash):
                    return t.session_id
        return None

    def get(self, session_id: str) -> Tel | None:
        with self._cond:
            return self._sessions.get(session_id)

    def add(self, session_id: str, calls: list[ApiCall], tools: dict[str, int]) -> None:
        with self._cond:
            t = self._sessions.get(session_id)
            if t is None:
                return
            t.requests.extend(calls)
            for tid, seq in tools.items():
                t.tools[tid] = min(seq, t.tools.get(tid, seq))
            self._cond.notify_all()

    def seen(self, session_id: str, tool_use_id: str, wait_s: float = 0.0) -> Seen | None:
        """The model and source behind a tool call, waiting up to `wait_s` for its events."""
        deadline = time.monotonic() + wait_s
        with self._cond:
            while True:
                t = self._sessions.get(session_id)
                found = _bind(t, tool_use_id) if t else None
                left = deadline - time.monotonic()
                if found is not None or t is None or left <= 0:
                    return found
                self._cond.wait(left)

    def hold(self, h: Hold) -> bool:
        """Keep a submission until its binding comes; False when the session has ended."""
        with self._cond:
            t = self._sessions.get(h.session_id)
            if t is not None:
                t.holds.append(h)
            return t is not None

    def take_bound(self, session_id: str) -> list[tuple[Hold, Seen]]:
        """The session's holds that telemetry now binds, removed from the waiting list."""
        with self._cond:
            t = self._sessions.get(session_id)
            if t is None:
                return []
            out: list[tuple[Hold, Seen]] = []
            for h in list(t.holds):
                s = _bind(t, h.tool_use_id)
                if s is not None:
                    t.holds.remove(h)
                    out.append((h, s))
            return out

    def take_all(self, session_id: str) -> list[tuple[Hold, Seen | None]]:
        with self._cond:
            t = self._sessions.get(session_id)
            if t is None:
                return []
            out = [(h, _bind(t, h.tool_use_id)) for h in t.holds]
            t.holds.clear()
            return out

    def plan(self, session_id: str, p: Plan) -> bool:
        with self._cond:
            t = self._sessions.get(session_id)
            if t is None:
                return False
            if t.first_plan is None:
                t.first_plan = p
            t.last_plan = p
            return True

    def submitted(self, session_id: str, stable_id: str) -> None:
        with self._cond:
            t = self._sessions.get(session_id)
            if t is not None:
                t.submitted += 1
                t.items.add(stable_id)

    def refused(self, session_id: str, model: str, expected: str) -> bool:
        """Count a model refusal; True for the session's first (one System Error each)."""
        with self._cond:
            t = self._sessions.get(session_id)
            if t is None:
                return False
            t.refused += 1
            t.refused_model, t.expected_model = model, expected
            return t.refused == 1

    def stopped(self, session_id: str) -> str | None:
        """`/ecf-review` stops once the model check has refused anything (SPEC §7.5)."""
        with self._cond:
            t = self._sessions.get(session_id)
            if t is None or not t.refused:
                return None
            return refusal_line(t.refused, t.refused_model, t.expected_model)


def refusal_line(n: int, model: str | None, expected: str | None) -> str:
    return f"{n} refused (model {model or 'unknown'}, expected {expected or 'unknown'})"


def _bind(t: Tel, tool_use_id: str) -> Seen | None:
    seq = t.tools.get(tool_use_id)
    if seq is None:
        return None
    before = [r for r in t.requests if r.seq is not None and r.seq < seq]
    if not before:
        return None
    r = max(before, key=lambda c: c.seq or 0)
    return Seen(r.model, r.source)


# ---------------------------------------------------------------------------- OTLP/JSON


def _value(v: Any) -> Any:
    if not isinstance(v, dict) or not v:
        return None
    d = cast(dict[str, Any], v)
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in d:
            return d[key]
    return None


def _attrs(lst: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(lst, list):
        for a in cast(list[Any], lst):
            if isinstance(a, dict) and isinstance(cast(dict[str, Any], a).get("key"), str):
                d = cast(dict[str, Any], a)
                out[d["key"]] = _value(d.get("value"))
    return out


def _int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if v >= 0 else None
    if isinstance(v, float) and v.is_integer() and v >= 0:
        return int(v)
    if isinstance(v, str) and v.isdigit() and len(v) <= 18:  # OTLP JSON sends int64 as text
        return int(v)
    return None


def _float(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v) if v >= 0 else None
    if isinstance(v, str):
        try:
            f = float(v)
        except ValueError:
            return None
        return f if f >= 0 else None
    return None


def _list(d: Any, key: str) -> list[Any]:
    v = cast(dict[str, Any], d).get(key) if isinstance(d, dict) else None
    return cast(list[Any], v) if isinstance(v, list) else []


def parse_logs(body: Any) -> tuple[list[ApiCall], dict[str, int]]:
    """API requests and tool-call sequences from an OTLP/JSON logs export; the rest is dropped."""
    calls: list[ApiCall] = []
    tools: dict[str, int] = {}
    for rl in _list(body, "resourceLogs"):
        for sl in _list(rl, "scopeLogs"):
            for rec in _list(sl, "logRecords"):
                if not isinstance(rec, dict):
                    continue
                r = cast(dict[str, Any], rec)
                a = _attrs(r.get("attributes"))
                name = a.get("event.name") or _value(r.get("body"))
                name = name.removeprefix("claude_code.") if isinstance(name, str) else ""
                seq = _int(a.get("event.sequence"))
                tid = valid_tool_use_id(a.get("tool_use_id"))
                if tid is not None and seq is not None:
                    tools[tid] = min(seq, tools.get(tid, seq))
                if name != "api_request":
                    continue
                model, source = a.get("model"), a.get("query_source")
                calls.append(ApiCall(
                    seq,
                    model if isinstance(model, str) and _MODEL.match(model) else "unknown",
                    source if isinstance(source, str) and _SOURCE.match(source) else "unknown",
                    _int(a.get("input_tokens")),
                    _int(a.get("output_tokens")),
                    _int(a.get("cache_read_tokens")),
                    _int(a.get("cache_creation_tokens")),
                    _int(a.get("duration_ms")),
                    _float(a.get("cost_usd")),
                ))  # fmt: skip
    return calls, tools


def parse_plan(body: dict[str, Any]) -> Plan:
    """The status-line script's numbers; anything out of shape is dropped."""

    def pct(key: str) -> float | None:
        v = _float(body.get(key))
        return v if v is not None and v <= 100 else None

    def resets(key: str) -> str | None:
        v = body.get(key)
        if isinstance(v, int | float) and not isinstance(v, bool):
            v = str(int(v))
        return v if isinstance(v, str) and _RESETS.match(v) else None

    return Plan(pct("five_hour"), pct("seven_day"), resets("five_hour_resets_at"),
                resets("seven_day_resets_at"))  # fmt: skip
