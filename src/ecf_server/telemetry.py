"""Claude Code telemetry for `ecf claude` sessions (SPEC §7.5, §11.5, §13.4; V1.4 step 6).

Each session gets a bearer token for the loopback OTLP/JSON receiver (`telemetry_app`). From the
log events the receiver keeps only numbers, model IDs, times and fixed codes, in memory per
session:

- **API requests** (`api_request`): model, `query_source`, input/output/cache tokens, duration,
  end time and, when present, `cost_usd` (an API-equivalent figure); each also becomes a
  `claude_calls` row.
- **Model swaps** (`subagent_completed` with `model_swapped`): the subagent's run and the model it
  ended on.

Everything else (user email, account and organization IDs, prompt and tool fields, which arrive
redacted anyway) is dropped on arrival (§12.4).

**Model check** (operator decision 2026-10-02, OD-307, replacing OD-268's per-call binding): the
reading and submitting tools exist only on the agent servers, which Claude Code gives to ecf's
subagents and never to the main session (tested 2026-10-02, §21.2), so every read and submission
comes from a subagent. What telemetry still has to show is the model. Telemetry can't tie a tool
call to its API request (tool events carry no agent or request ID, and a tool starts before its
request's event is logged; step 13, 2026-10-02), so a submission is judged by its window: from the
claim's read to the submission, every subagent request that overlaps it (and every swapped
subagent run) must be on the role's pinned model. `/ecf-review` runs one agent type's spawns at a
time, so parallel spawns in the window share one model; work that overlaps another model is
refused, never accepted. A submission waits until telemetry has caught up with it: a subagent
request or swap that ends at or after it.

Submissions waiting for their judgement (`Hold`) are kept here too, and settled by `claude_review`
(or `claude_eval` for `/ecf-eval`).
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

SUBAGENT = "agent:custom"  # query_source of an ecf agent (tested 2026-10-02)
BUILTIN = "agent:builtin:"  # query_source prefix of a built-in agent (seen 2026-10-06)
SLACK_S = 0.5  # timestamps from Claude Code and the service's clock, same computer
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/\[\]-]{0,99}$")
_SOURCE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,63}$")
_RESETS = re.compile(r"^[0-9A-Za-z:.+_-]{1,40}$")


@dataclass(frozen=True)
class Seen:
    """What telemetry says about a submission's window: the model(s) of its subagent requests."""

    model: str


@dataclass(frozen=True)
class ApiCall:
    end: float | None  # when the request ended (epoch seconds), from `event.timestamp`
    model: str
    source: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    duration_ms: int | None = None
    cost_usd: float | None = None

    @property
    def start(self) -> float | None:
        return None if self.end is None else self.end - (self.duration_ms or 0) / 1000


@dataclass(frozen=True)
class Swap:
    """A subagent run that ended on another model than it started on."""

    start: float
    end: float
    model: str


@dataclass(frozen=True)
class Plan:
    """Plan-window usage from the status line (Pro and Max only)."""

    five_hour: float | None
    seven_day: float | None
    five_hour_resets: str | None
    seven_day_resets: str | None


@dataclass(frozen=True)
class Hold:
    """A checked submission waiting for telemetry to show the model of its window."""

    session_id: str
    stable_id: str
    fence: int
    need: str
    agent: str
    payload: dict[str, Any]
    kind: str = "review"  # review (`/ecf-review`, claude_review) | eval (`/ecf-eval`, claude_eval)
    at: float = 0.0  # when it was submitted (epoch seconds; set by the API)
    read_at: float | None = None  # when its claim was read, if it was


@dataclass
class Tel:
    session_id: str
    bearer_hash: str
    requests: list[ApiCall] = field(default_factory=list[ApiCall])
    swaps: list[Swap] = field(default_factory=list[Swap])
    # (kind, ref, fence) -> when the claim was first read
    reads: dict[tuple[str, str, int], float] = field(
        default_factory=dict[tuple[str, str, int], float]
    )
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

    def add(self, session_id: str, calls: list[ApiCall], swaps: list[Swap]) -> None:
        with self._cond:
            t = self._sessions.get(session_id)
            if t is None:
                return
            t.requests.extend(calls)
            t.swaps.extend(swaps)
            self._cond.notify_all()

    def read(self, session_id: str, kind: str, ref: str, fence: int, at: float) -> None:
        """A claim was read (its window starts at the first read)."""
        with self._cond:
            t = self._sessions.get(session_id)
            if t is not None:
                t.reads.setdefault((kind, ref, fence), at)

    def read_at(self, session_id: str, kind: str, ref: str, fence: int) -> float | None:
        with self._cond:
            t = self._sessions.get(session_id)
            return t.reads.get((kind, ref, fence)) if t else None

    def judge(self, h: Hold, wait_s: float = 0.0) -> Seen | None:
        """The model of a submission's window, waiting up to `wait_s` for telemetry to catch up;
        None while it hasn't."""
        deadline = time.monotonic() + wait_s
        with self._cond:
            while True:
                t = self._sessions.get(h.session_id)
                found = _judge(t, h) if t else None
                left = deadline - time.monotonic()
                if found is not None or t is None or left <= 0:
                    return found
                self._cond.wait(left)

    def hold(self, h: Hold) -> bool:
        """Keep a submission until telemetry catches up; False when the session has ended."""
        with self._cond:
            t = self._sessions.get(h.session_id)
            if t is not None:
                t.holds.append(h)
            return t is not None

    def take_bound(self, session_id: str) -> list[tuple[Hold, Seen]]:
        """The session's holds that telemetry can now judge, removed from the waiting list."""
        with self._cond:
            t = self._sessions.get(session_id)
            if t is None:
                return []
            out: list[tuple[Hold, Seen]] = []
            for h in list(t.holds):
                s = _judge(t, h)
                if s is not None:
                    t.holds.remove(h)
                    out.append((h, s))
            return out

    def take_all(self, session_id: str) -> list[tuple[Hold, Seen | None]]:
        """At session end: every hold, judged on what arrived (None: nothing in its window)."""
        with self._cond:
            t = self._sessions.get(session_id)
            if t is None:
                return []
            out = [(h, _judge(t, h, final=True)) for h in t.holds]
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
        """`/ecf-review` and `/ecf-eval` stop once the model check has refused anything (SPEC
        §7.5), or once a built-in Claude Code agent has run in the session: ecf denies them by
        name, and a new one would otherwise spend the plan unseen (v1.0.0, 2026-10-06)."""
        with self._cond:
            t = self._sessions.get(session_id)
            if t is None:
                return None
            if t.refused:
                return refusal_line(t.refused, t.refused_model, t.expected_model)
            builtin = sorted({c.source.removeprefix(BUILTIN) for c in t.requests
                              if c.source.startswith(BUILTIN)})  # fmt: skip
            if builtin:
                return (
                    f"a built-in Claude Code agent ran in this session ({', '.join(builtin)});"
                    " ecf doesn't deny it yet, so this session stops here: update ecf"
                )
            return None


def refusal_line(n: int, model: str | None, expected: str | None) -> str:
    return f"{n} refused (model {model or 'unknown'}, expected {expected or 'unknown'})"


def _judge(t: Tel, h: Hold, *, final: bool = False) -> Seen | None:
    """The models of the subagent work overlapping [read_at, at], once telemetry has caught up
    (or at session end); None when it hasn't, or nothing overlaps."""
    spans = [
        (c.start, c.end, c.model)
        for c in t.requests
        if c.source == SUBAGENT and c.end is not None and c.start is not None
    ]
    spans += [(s.start, s.end, s.model) for s in t.swaps]
    if not final and not any(end >= h.at for _s, end, _m in spans):
        return None
    begin = h.at if h.read_at is None else min(h.read_at, h.at)
    models = sorted({m for start, end, m in spans
                     if start <= h.at + SLACK_S and end >= begin - SLACK_S})  # fmt: skip
    return Seen(" + ".join(models)) if models else None


def _when(v: Any) -> float | None:
    """`event.timestamp` (ISO 8601, UTC) as epoch seconds."""
    if not isinstance(v, str) or len(v) > 40:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


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


def parse_logs(body: Any) -> tuple[list[ApiCall], list[Swap]]:
    """API requests and model swaps from an OTLP/JSON logs export; the rest is dropped."""
    calls: list[ApiCall] = []
    swaps: list[Swap] = []
    for rl in _list(body, "resourceLogs"):
        for sl in _list(rl, "scopeLogs"):
            for rec in _list(sl, "logRecords"):
                if not isinstance(rec, dict):
                    continue
                r = cast(dict[str, Any], rec)
                a = _attrs(r.get("attributes"))
                name = a.get("event.name") or _value(r.get("body"))
                name = name.removeprefix("claude_code.") if isinstance(name, str) else ""
                end = _when(a.get("event.timestamp"))
                model, source = a.get("model"), a.get("query_source")
                if name == "subagent_completed" and a.get("model_swapped") in (True, "true"):
                    final = a.get("final_model")
                    if end is not None:
                        swaps.append(Swap(end - (_int(a.get("duration_ms")) or 0) / 1000, end,
                                          final if isinstance(final, str) and _MODEL.match(final)
                                          else "unknown"))  # fmt: skip
                if name != "api_request":
                    continue
                calls.append(ApiCall(
                    end,
                    model if isinstance(model, str) and _MODEL.match(model) else "unknown",
                    source if isinstance(source, str) and _SOURCE.match(source) else "unknown",
                    _int(a.get("input_tokens")),
                    _int(a.get("output_tokens")),
                    _int(a.get("cache_read_tokens")),
                    _int(a.get("cache_creation_tokens")),
                    _int(a.get("duration_ms")),
                    _float(a.get("cost_usd")),
                ))  # fmt: skip
    return calls, swaps


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
