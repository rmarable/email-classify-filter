"""TypeSafe Jev over HTTPS (v3.0.0 step 2c; PLANNING §4.3; OD-503, OD-504; eval only until step 4).

Jev answers typed questions about a `state` (docs.typesafe.ai/api, read 2026-10-14). This module
holds the hosted transport, the pin, the request, the failure classes, the `Retry-After` tiers,
the backoff, AIMD and circuit-breaker objects, the probe, the gate key and the key's `set_key`.
It uses `systemone`'s question builder and answer mapper; nothing moved out of `systemone`
(D4, P9). `ecf_server.classifier` never imports this module (an import-linter contract), and
nothing in the service's classifier path calls it in step 2.

- **Transport:** plain HTTPS, not TypeSafe's SDK (the SDK reads `TYPESAFE_*` from the
  environment, defaults to `jev-latest` and logs bodies at debug level). No proxy or certificate
  settings from the environment, no redirects (a 3xx is a provider fault and the key never
  reaches its `Location`), `User-Agent: ecf` (D8). A per-try monotonic deadline classes a slow
  reply as a timeout, since httpx's read timeout is per receive (P17).
- **Request (D1, OD-504, P10):** the questions from the effective schema (`systemone.questions`,
  `fraud_risk` as a choice of its levels, step 3);
  the state is ecf's untrusted-data preamble, the signals block (the classifier's wording), then
  between token delimiters what Claude gets: one `From:`, `Subject:` and `Date:` line, one
  `Attachment:` line each, and the excerpt. Header values are collapsed to one line and capped;
  with redaction on, a subject or display name that trips fraud trigger 10 becomes ecf's marker.
  A request whose estimate (tokens <= UTF-8 bytes) passes the limits is refused before any call.
- **Failures (§5.4):** by class; stops raise `JevError`, a case that fails is returned with its
  `fault`. Every number here is a placeholder set in step 3 (SPEC §15.4).
- **Never sent or logged:** the key (only in one headers dict), any body or error message. Error
  text carries the status, the class and a recognised error code only.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import secrets
import sqlite3
import threading
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from importlib import resources
from typing import Any, Literal, cast

import httpx

from ecf.errors import InternalError, InvalidInputError
from ecf.schema import SAFETY_FIELDS, CompiledSchema
from ecf_server import excerpt, jev_mapping, signals, systemone, triggers
from ecf_server.clock import Clock, SystemClock, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.message import CLASSIFIER_CHARS
from ecf_server.rules import (
    CompiledRules,
)
from ecf_server.secretstore import SecretStore

HOST = "api.typesafe.ai"
BASE_URL = f"https://{HOST}"
PATH = "/v1/systemone"
USER_AGENT = "ecf"  # D8
KEY_SECRET = "jev-api-key"  # noqa: S105 - the secret store's entry name, not a secret
LOCK = "jev.lock"
_PIN = re.compile(r"^jev-\d+\.\d+\.\d+$")

# Limits and timings: placeholders until step 3 sets them and SPEC §15.4 records them.
TIMEOUT_S = 30.0  # per try: httpx's read timeout and the monotonic deadline (P17)
CONNECT_S, WRITE_S, POOL_S = 5.0, 10.0, 5.0
KEEPALIVE_S = 60.0
KEY_CHECK_TIMEOUT_S = 5.0  # under the CLI's 10 s socket timeout (P12)
MAX_CONCURRENCY = 8
TRIES = 4  # per case, for provider faults
BACKOFF_BASE_S = 1.0
RATE_LIMIT_CAP_S = 60.0
FAULT_CAP_S = 30.0
SHORT_WAIT_S = 60.0  # a `Retry-After` up to this is paused through (+0-10% jitter)
SHORT_JITTER = 0.10
WAIT_BUDGET_S = 600.0  # backoff + Retry-After + breaker-open time + timed-out tries, per run
BREAKER_AFTER = 5  # consecutive faulted tries
COOL_DOWN_S, COOL_DOWN_MAX_S = 30.0, 300.0
RESET_AFTER = 10  # K: consecutive successes that close a half-open breaker (and step AIMD up)
STATE_LIMIT = 30_000  # estimated tokens of state + the longest question (TypeSafe: 32k)
TOTAL_LIMIT = 62_000  # estimated tokens of the whole request (TypeSafe: 64k)
NEAR = 0.9  # a 429 on a request past this share of a limit is a request error (perf 2e)
FAULT_SHARE = 0.02  # early stop past floor(0.02 n) faulted cases (P22)
# proactive pacing's token estimate when a reply has no `usage` (J5): UTF-8 bytes per token, a
# placeholder until the live check (L8) measures it
PACE_BYTES_PER_TOKEN = 4.0

# Caps on the header lines and excerpt sent to Jev (P10).
NAME_CHARS = 300  # display name, and each attachment's name
SUBJECT_CHARS = 300
MAX_ATTACHMENTS = 10
# the stored excerpt with its cut's ellipsis, in characters; never a byte cut (P10)
EXCERPT_CHARS = CLASSIFIER_CHARS + 1
UNKNOWN_DATE = "unknown"

# Values learned in the live check (SPEC §21.2); empty until then.
# error-code values ecf may keep in error text; any other code is dropped
KNOWN_CODES: frozenset[str] = frozenset()
QUOTA_CODES: frozenset[str] = frozenset()  # a 429 with one of these stops the run at once
# the status and error code L4 recorded for a retired pin; until then every first-case
# refusal says "TypeSafe refused the first request" (draft 4, B)
RETIRED_PIN: tuple[int, str | None] | None = None
# the statuses that stop a run on its first case: a retired pin gave 400 with only `detail` in
# the live check (OD-505), the docs name 404 and 422
FIRST_CASE_STOP = frozenset({400, 404, 422})

KEY_CHECK_STATE = "ecf key check"
KEY_CHECK_QUESTIONS: dict[str, Any] = {
    "check": {"type": "noul", "instructions": "The text says it is a key check."}
}
REQUEST_ERRORS = frozenset({400, 404, 413, 422})
PROVIDER_STATUSES = frozenset({408, 529})
STOP_CLASSES = frozenset({"auth", "pin", "quota", "long_wait", "budget", "stopped"})
KEY_GONE = "the TypeSafe key was removed"
Emit = Callable[..., None]


def load_pin() -> str:
    """The pinned Jev version (`data/jev.lock`), a versioned ID, never an alias."""
    raw: Any = json.loads(resources.files("ecf_server.data").joinpath(LOCK).read_text("utf-8"))
    model = cast("dict[str, Any]", raw).get("model") if isinstance(raw, dict) else None
    if not isinstance(model, str) or not _PIN.fullmatch(model):
        raise InternalError(f"{LOCK} must pin a versioned Jev ID (jev-X.Y.Z)")
    return model


# Errors: the Jev outcomes that stop a run, and reading an error code from a reply's body.


class JevError(Exception):
    """A Jev outcome that stops the run: `cls` is one of STOP_CLASSES. The text holds only the
    status, the class and a recognised error code; never a body, a message or the key."""

    def __init__(self, cls: str, text: str, *, status: int | None = None,
                 code: str | None = None) -> None:  # fmt: skip
        super().__init__(text)
        self.cls, self.text, self.status, self.code = cls, text, status, code

    def __str__(self) -> str:
        return self.text


def auth_error(status: int | None) -> JevError:
    if status is None:
        return JevError("auth", KEY_GONE)
    return JevError("auth", f"TypeSafe refused the key (HTTP {status}); replace it with"
                    " `ecf models api-key set --provider typesafe`", status=status)  # fmt: skip


def first_case_error(status: int, code: str | None) -> JevError:
    """A 400, 404 or 422 on the first case (§5.3, OD-505): the pin message only for what L4
    recorded."""
    if RETIRED_PIN is not None and status == RETIRED_PIN[0] and RETIRED_PIN[1] in (None, code):
        return JevError("pin", "the pinned Jev version isn't served", status=status, code=code)
    return JevError("pin", f"TypeSafe refused the first request (HTTP {status}); the run stopped"
                    " before scoring", status=status, code=code)  # fmt: skip


def error_code(body: Any) -> str | None:
    """A recognised error-code value from an error body (`code`, or `error.code` or
    `error.type`), only when it is in KNOWN_CODES; anything else is dropped."""
    found: list[Any] = []
    if isinstance(body, dict):
        b = cast("dict[str, Any]", body)
        found.append(b.get("code"))
        err = b.get("error")
        if isinstance(err, dict):
            e = cast("dict[str, Any]", err)
            found += [e.get("code"), e.get("type")]
    for v in found:
        if isinstance(v, str) and v in KNOWN_CODES:
            return v
    return None


# The request: the email fields Jev gets, how they become the `state` and its questions, and
# the POST exactly as sent, with its size estimate.


@dataclass(frozen=True)
class Attachment:
    name: str | None
    type: str | None
    size: int | None


@dataclass(frozen=True)
class Email:
    """What Claude's classifier gets (D1): the sender, subject, date, attachment metadata and the
    stored excerpt (already redacted when redaction is on)."""

    from_name: str
    from_addr: str
    subject: str
    date: str
    text: str
    attachments: tuple[Attachment, ...] = ()


def _flat(value: Any) -> str:
    out = "".join(" " if unicodedata.category(c) in ("Cc", "Zl", "Zp") else c for c in str(value))
    return " ".join(out.split())


def one_line(value: Any, cap: int) -> str:
    """`value` on one line (control and line-separator characters become a space, runs of space
    one), its ends stripped, cut to `cap` characters."""
    return _flat(value)[:cap]


def _header_value(value: str, cap: int, redact: bool) -> str:
    """One header value; with redaction on, checked whole for injection before its cap."""
    line = _flat(value)
    if redact and line and triggers.redact_injection(line) != line:
        return triggers.INJECTION_MARK
    return line[:cap]


def normalise_date(raw: str | None) -> str:
    """RFC 3339 UTC (`YYYY-MM-DDTHH:MM:SSZ`) from an RFC 5322 `Date:` (the eval's card) or an
    ISO 8601 time (the service's `internaldate` or `created_at`); `unknown` when neither parses.
    A time without a zone is read as UTC."""
    s = (raw or "").strip()
    dt: datetime | None = None
    if s:
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            try:
                dt = parsedate_to_datetime(s)
            except (TypeError, ValueError, IndexError):
                dt = None
    if dt is None:
        return UNKNOWN_DATE
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sender(name: str, addr: str) -> str:
    return f"{name} <{addr}>" if name and addr else (addr or name)


def header_lines(email: Email, *, redact: bool) -> list[str]:
    """`From:`, `Subject:`, `Date:` and up to MAX_ATTACHMENTS `Attachment:` lines (P10)."""
    name = _header_value(email.from_name, NAME_CHARS, redact)
    lines = [f"From: {_sender(name, one_line(email.from_addr, NAME_CHARS))}",
             f"Subject: {_header_value(email.subject, SUBJECT_CHARS, redact)}",
             f"Date: {normalise_date(email.date)}"]  # fmt: skip
    for a in email.attachments[:MAX_ATTACHMENTS]:
        size = f"{a.size} bytes" if isinstance(a.size, int) else "size unknown"
        lines.append(f"Attachment: {one_line(a.name or 'unnamed', NAME_CHARS)};"
                     f" {one_line(a.type or 'unknown type', NAME_CHARS)}; {size}")  # fmt: skip
    return lines


def _excerpt(text: str) -> str:
    return text[:EXCERPT_CHARS]


def state(email: Email, sent: tuple[str, ...], token: str, *, redact: bool) -> str:
    """The state: the preamble, the signals block, then the email between `<<<EMAIL token>>>`
    and `<<<END EMAIL token>>>` (format `s1`; step 3 measured `s2` and the preamble in each
    question's instructions, and neither did better, OD-512)."""
    headers = "\n".join(header_lines(email, redact=redact))
    return (f"{systemone.PREAMBLE}\n\n{signals.block(sent, token)}<<<EMAIL {token}>>>\n{headers}"
            f"\n\n{_excerpt(email.text)}\n<<<END EMAIL {token}>>>")  # fmt: skip


def questions(schema: CompiledSchema) -> dict[str, Any]:
    """One question per field of the effective schema, in order, keyed by field name (keys
    aren't sent to the model, /api). `fraud_risk` is asked as a choice of its levels, `none` last
    (step 3's J4: it under-rated fewer cases than the score; P17, OD-512); `as_asked` maps the
    answer back to its most probable level."""
    q = systemone.questions(schema)
    if (f := schema.fields.get("fraud_risk")) is not None:
        texts = list(f.value_descriptions) + [""] * (len(f.values) - len(f.value_descriptions))
        crit = {v: d or v for v, d in zip(f.values, texts, strict=True)}
        order = [v for v in f.values if v != "none"] + (["none"] if "none" in crit else [])
        q["fraud_risk"] = {"type": "choice", "instructions": f.description,
                           "criteria": {v: crit[v] for v in order}}  # fmt: skip
    return q


def request_body(schema: CompiledSchema, email: Email, sent: tuple[str, ...], *, pin: str,
                 token: str, redact: bool) -> dict[str, Any]:  # fmt: skip
    return {"model": pin, "questions": questions(schema),
            "state": state(email, sent, token, redact=redact)}  # fmt: skip


def encode(body: Mapping[str, Any]) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class Estimate:
    """Tokens estimated as UTF-8 bytes, an upper bound (P24)."""

    state_and_longest: int
    total: int

    @property
    def over(self) -> bool:
        return self.state_and_longest > STATE_LIMIT or self.total > TOTAL_LIMIT

    @property
    def near(self) -> bool:
        return (self.state_and_longest > NEAR * STATE_LIMIT
                or self.total > NEAR * TOTAL_LIMIT)  # fmt: skip


def estimate(body: Mapping[str, Any]) -> Estimate:
    st = body["state"]
    st_bytes = len(st.encode("utf-8")) if isinstance(st, str) else len(encode(st))
    qs = cast("dict[str, Any]", body["questions"])
    longest = max((len(encode(q)) for q in qs.values()), default=0)
    return Estimate(st_bytes + longest, len(encode(body)))


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "Accept": "application/json", "User-Agent": USER_AGENT}  # fmt: skip


def build_request(client: httpx.Client, body: Mapping[str, Any], key: str) -> httpx.Request:
    """The POST exactly as sent: ecf's four headers plus Host and Content-Length (httpx's own
    defaults removed)."""
    req = client.build_request("POST", PATH, content=encode(body), headers=_headers(key))
    for h in ("accept-encoding", "connection"):
        if h in req.headers:
            del req.headers[h]
    return req


# The transport: the HTTPS client, one try with its deadline and the class of its reply, and
# the minimal call that checks a key.


def default_transport() -> httpx.BaseTransport:
    """The real HTTPS transport (tests replace this with one that raises)."""
    limits = httpx.Limits(max_connections=MAX_CONCURRENCY,
                          max_keepalive_connections=MAX_CONCURRENCY,
                          keepalive_expiry=KEEPALIVE_S)  # fmt: skip
    return httpx.HTTPTransport(limits=limits, trust_env=False)


def http_client(transport: httpx.BaseTransport | None = None, *,
                read_s: float = TIMEOUT_S) -> httpx.Client:  # fmt: skip
    return httpx.Client(base_url=BASE_URL,
                        timeout=httpx.Timeout(connect=min(CONNECT_S, read_s), read=read_s,
                                              write=min(WRITE_S, read_s),
                                              pool=min(POOL_S, read_s)),
                        trust_env=False, follow_redirects=False,
                        transport=transport or default_transport())  # fmt: skip


@dataclass(frozen=True)
class Reply:
    """One try: its class (`ok`, `rate_limit`, `provider`, `auth`, `request`), status, body
    (only on a 200), headers and HTTP time."""

    cls: str
    status: int | None
    body: dict[str, Any] | None
    headers: httpx.Headers
    http_ms: int
    code: str | None = None
    timed_out: bool = False


def _classify_status(status: int) -> str:  # noqa: PLR0911 - one return per class
    if status == 200:
        return "ok"
    if status == 429:
        return "rate_limit"
    if status in (401, 403):
        return "auth"
    if status in REQUEST_ERRORS:
        return "request"
    if status in PROVIDER_STATUSES or status >= 500 or 300 <= status < 400:
        return "provider"
    if 400 <= status < 500:
        return "request"  # an unlisted 4xx: not retried
    return "provider"


def send(  # noqa: PLR0911 - one return per outcome
    client: httpx.Client, req: httpx.Request, clock: Clock, deadline_s: float
) -> Reply:
    """One try, bounded by a monotonic deadline: a reply still arriving after it is a timeout."""
    start = clock.monotonic()
    end = start + deadline_s

    def ms() -> int:
        return int((clock.monotonic() - start) * 1000)

    empty = httpx.Headers()
    try:
        r = client.send(req, stream=True)
    except httpx.TimeoutException:
        return Reply("provider", None, None, empty, ms(), timed_out=True)
    except httpx.TransportError:
        return Reply("provider", None, None, empty, ms())
    try:
        chunks: list[bytes] = []
        for chunk in r.iter_bytes():
            chunks.append(chunk)
            if clock.monotonic() > end:
                return Reply("provider", None, None, r.headers, ms(), timed_out=True)
        if clock.monotonic() > end:
            return Reply("provider", None, None, r.headers, ms(), timed_out=True)
    except httpx.TimeoutException:
        return Reply("provider", None, None, r.headers, ms(), timed_out=True)
    except httpx.TransportError:
        return Reply("provider", r.status_code, None, r.headers, ms())
    finally:
        r.close()
    cls = _classify_status(r.status_code)
    raw = b"".join(chunks)
    parsed: Any = None
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = None
    if cls == "ok":
        if not isinstance(parsed, dict):
            return Reply("provider", r.status_code, None, r.headers, ms())
        return Reply("ok", 200, cast("dict[str, Any]", parsed), r.headers, ms())
    return Reply(cls, r.status_code, None, r.headers, ms(), code=error_code(parsed))


def other_version_error() -> JevError:
    return JevError("pin", "TypeSafe answered with a different Jev version than the pin; the run"
                    " stopped")  # fmt: skip


def check_key(key: str, transport: httpx.BaseTransport | None = None, *,
              clock: Clock | None = None,
              timeout_s: float = KEY_CHECK_TIMEOUT_S) -> None:  # fmt: skip
    """The key's validation (P7, P12): one minimal POST with no email content, the probe's call,
    within `timeout_s`. Raises InvalidInputError when TypeSafe doesn't accept it, when a 404 or
    422 says the pinned version may not be served, or when the reply names another version
    (J12)."""
    ck = clock or SystemClock()
    pin = load_pin()
    with http_client(transport, read_s=timeout_s) as client:
        body = {"model": pin, "questions": KEY_CHECK_QUESTIONS, "state": KEY_CHECK_STATE}
        reply = send(client, build_request(client, body, key), ck, timeout_s)
    if reply.cls == "ok":
        if reply.body is not None and reply.body.get("model") != pin:
            raise InvalidInputError(f"TypeSafe answered with a different Jev version than the pin"
                                    f" ({pin}); nothing was stored")  # fmt: skip
        return
    if reply.status in FIRST_CASE_STOP:
        raise InvalidInputError(f"TypeSafe refused the check call (HTTP {reply.status}): the"
                                f" pinned Jev version ({pin}) may not be served; nothing was"
                                " stored")  # fmt: skip
    if reply.timed_out:
        why = f"no answer in {timeout_s:.0f} s"
    elif reply.status is None:
        why = "network error"
    elif reply.cls == "auth":
        why = f"the key was refused (HTTP {reply.status})"
    else:
        why = f"HTTP {reply.status}"
    raise InvalidInputError(f"TypeSafe didn't accept it: {why}; nothing was stored")


# Waits between tries: how long to pause after a 429 or a provider fault (Retry-After tiers and
# jittered backoff), the run-wide waiting budget, concurrency (AIMD) and the circuit breaker.


def retry_after(headers: httpx.Headers, now: datetime) -> float | None:
    """Seconds to wait from `retry-after-ms`, or `Retry-After` as delta-seconds or an HTTP-date;
    None when absent or unreadable."""
    ms = headers.get("retry-after-ms")
    if ms is not None:
        try:
            v = float(ms)
            if math.isfinite(v) and v >= 0:
                return v / 1000
        except ValueError:
            pass
    ra = headers.get("retry-after")
    if ra is None:
        return None
    ra = ra.strip()
    if ra.isdigit():
        return float(int(ra))
    try:
        when = parsedate_to_datetime(ra)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - now).total_seconds())


def backoff(attempt: int, cap: float, rng: random.Random, base: float = BACKOFF_BASE_S) -> float:
    """Full jitter: uniform between 0 and min(cap, base * 2^attempt)."""
    return rng.uniform(0, min(cap, base * 2 ** max(0, attempt)))


@dataclass
class WaitBudget:
    """The run's waiting budget: backoff, `Retry-After`, breaker-open time and timed-out tries."""

    total_s: float = WAIT_BUDGET_S
    spent_s: float = 0.0

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.total_s - self.spent_s)

    def spend(self, seconds: float) -> None:
        self.spent_s += max(0.0, seconds)


@dataclass
class Aimd:
    """Concurrency: halved on a 429 (floor 1), one more after RESET_AFTER successes (up to
    `ceiling`). The eval stays serial in step 2 (P6); the object is built and tested here."""

    limit: int = 1
    ceiling: int = MAX_CONCURRENCY
    step_after: int = RESET_AFTER
    ok: int = 0

    def on_rate_limit(self) -> None:
        self.limit, self.ok = max(1, self.limit // 2), 0

    def on_success(self) -> None:
        self.ok += 1
        if self.ok >= self.step_after:
            self.limit, self.ok = min(self.ceiling, self.limit + 1), 0


@dataclass
class Breaker:
    """Opens after `after` consecutive faulted tries; while open, no calls. The cool-down doubles
    from `cool_s` to `cap_s`; a probe success half-opens it (concurrency 1), a half-open fault
    re-opens it with the next cool-down, and only `reset_after` consecutive successes close it
    and reset the ladder (perf 2d)."""

    after: int = BREAKER_AFTER
    cool_s: float = COOL_DOWN_S
    cap_s: float = COOL_DOWN_MAX_S
    reset_after: int = RESET_AFTER
    state: Literal["closed", "open", "half_open"] = "closed"
    faults: int = 0
    level: int = 0
    successes: int = 0
    trips: int = 0

    def cool_down_s(self) -> float:
        return min(self.cap_s, self.cool_s * 2**self.level)

    def on_fault(self) -> bool:
        """A faulted try; True when this opens the breaker."""
        self.successes = 0
        if self.state == "half_open":
            self.level += 1
            self.state, self.trips = "open", self.trips + 1
            return True
        if self.state == "open":
            return False
        self.faults += 1
        if self.faults >= self.after:
            self.state, self.faults, self.trips = "open", 0, self.trips + 1
            return True
        return False

    def on_probe_ok(self) -> None:
        self.state, self.successes = "half_open", 0

    def on_probe_fault(self) -> None:
        """A probe that faulted re-opens the breaker: a trip, as `on_fault`'s are (J13)."""
        self.level += 1
        self.state, self.trips = "open", self.trips + 1

    def on_success(self) -> bool:
        """A good try; True when this closes a half-open breaker."""
        self.faults = 0
        if self.state != "half_open":
            return False
        self.successes += 1
        if self.successes >= self.reset_after:
            self.state, self.level, self.successes = "closed", 0, 0
            return True
        return False


def fault_limit(n: int) -> int:
    """The most faulted cases a run of `n` may have: the next one stops it (P22)."""
    return math.floor(FAULT_SHARE * n)


# The client: one eval run's Jev calls with their retries, waits and breaker, each case's
# result, and the run's figures for `summary.jev`.


@dataclass
class Result:
    """One case: the mapped answer, or a failure with its `fault` (`request_error` or
    `provider`; an answer that doesn't map has no fault and no classification, as today)."""

    classification: dict[str, Any] | None
    probabilities: dict[str, dict[str, float]] = field(default_factory=dict[str, dict[str, float]])
    confidence: dict[str, float] = field(default_factory=dict[str, float])
    input_tokens: int | None = None
    output_tokens: int | None = None
    classifier_ms: int | None = None  # the successful try's HTTP time only (P14)
    wait_ms: int = 0
    queue_ms: int = 0  # waiting for a concurrency slot: 0 at concurrency 1
    tries: int = 0
    concurrency: int = 1  # the level the case's last try was sent at, before AIMD moved (J7)
    fault: str | None = None
    status: int | None = None
    # a try got a reply classed ok or request error (not a provider fault or a 429): the eval's
    # first-case pin check stays armed until one does (J11)
    replied: bool = False


@dataclass
class Stats:
    """What `summary.jev` reports for a run (§5.5). `pauses` counts `Retry-After` tiers (`short`,
    `medium`) and proactive pacing (`pacing`, J5); `breaker_trips` every opening, a probe's
    re-open included (J13); `wait_s` each sleep's planned length, an interrupted one too (J13)."""

    latency_ms: dict[int, list[int]] = field(default_factory=dict[int, list[int]])
    retries: Counter[str] = field(default_factory=Counter[str])
    rate_limits: int = 0
    pauses: Counter[str] = field(default_factory=Counter[str])
    breaker_trips: int = 0
    wait_s: float = 0.0
    input_tokens_billed: int = 0  # every 200's `usage`, repeats and probes included (P21)
    usage_missing: int = 0  # 200s without `usage.input_tokens`: not in the bill (J6)
    faulted: Counter[str] = field(default_factory=Counter[str])

    def summary(self) -> dict[str, Any]:
        lat = {str(c): {"n": len(v), "p50": _pct(v, 50), "p95": _pct(v, 95), "max": max(v)}
               for c, v in sorted(self.latency_ms.items()) if v}  # fmt: skip
        return {"latency_ms_by_concurrency": lat, "retries": dict(self.retries),
                "rate_limits": self.rate_limits, "pauses": dict(self.pauses),
                "breaker_trips": self.breaker_trips, "wait_s": round(self.wait_s, 3),
                "input_tokens_billed": self.input_tokens_billed,
                "usage_missing": self.usage_missing, "faulted": dict(self.faulted)}  # fmt: skip


def _pct(v: list[int], p: int) -> int:
    s = sorted(v)
    return s[min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))]


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _usage(body: Mapping[str, Any]) -> tuple[int | None, int | None]:
    u = body.get("usage")
    if not isinstance(u, dict):
        return None, None
    usage = cast("dict[str, Any]", u)
    return _int(usage.get("input_tokens")), _int(usage.get("output_tokens"))


def _confidence(body: Mapping[str, Any]) -> dict[str, float]:
    answers = body.get("answers")
    out: dict[str, float] = {}
    if isinstance(answers, dict):
        for k, a in cast("dict[str, Any]", answers).items():
            if isinstance(a, dict):
                c = cast("dict[str, Any]", a).get("confidence")
                if isinstance(c, int | float) and not isinstance(c, bool):
                    out[str(k)] = float(c)
    return out


def as_asked(body: dict[str, Any], schema: CompiledSchema) -> dict[str, Any]:
    """A reply in the shape `systemone.map_answers` reads: `fraud_risk`, asked as a choice,
    turned back into a score's level probabilities (its most probable level is the answer; Jev's
    `choice` key itself isn't used)."""
    answers = body.get("answers")
    f = schema.fields.get("fraud_risk")
    if not isinstance(answers, dict) or f is None:
        return body
    got = dict(cast("dict[str, Any]", answers))
    a = got.get("fraud_risk")
    if isinstance(a, dict):
        raw = cast("dict[str, Any]", a).get("probabilities")
        probs = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
        got["fraud_risk"] = {"probabilities": {str(i): probs.get(v)
                                               for i, v in enumerate(f.values)}}  # fmt: skip
    return body | {"answers": got}


def _int_header(v: str | None) -> int | None:
    return int(v) if v is not None and v.strip().isdigit() else None


def _no_pause(_line: str) -> None:
    return None


class Client:
    """Jev calls for one eval run: one httpx client, the run's breaker, AIMD and waiting
    budget. The key is re-read before each try (J10) and at each probe (P12); every wait is
    `wait(seconds)`, by default the run's stop event, so `ecf eval stop` ends it (P18)."""

    def __init__(  # noqa: PLR0913 - the run's settings, all keyword-only
        self, key: Callable[[], str | None], *,
        transport: httpx.BaseTransport | None = None, pin: str | None = None,
        clock: Clock | None = None, stop: threading.Event | None = None,
        wait: Callable[[float], bool] | None = None, rng: random.Random | None = None,
        emit: Emit | None = None, on_pause: Callable[[str], None] | None = None,
        budget_s: float = WAIT_BUDGET_S, concurrency: int = 1, timeout_s: float = TIMEOUT_S,
    ) -> None:  # fmt: skip
        if concurrency != 1:
            raise InvalidInputError("--jev-concurrency above 1 is not in step 2")
        self._key = key
        self.pin = pin or load_pin()
        self.clock: Clock = clock or SystemClock()
        self.stop = stop or threading.Event()
        self._wait = wait or self.stop.wait
        self.rng = rng or random.Random()  # noqa: S311 - jitter, not security
        self._emit: Emit = emit or log.info
        self.on_pause: Callable[[str], None] = on_pause or _no_pause
        self.timeout_s = timeout_s
        self.http = http_client(transport, read_s=timeout_s)
        self.budget = WaitBudget(budget_s)
        # the run's concurrency is AIMD's ceiling: a serial run stays at 1 (P6, P14)
        self.aimd = Aimd(limit=concurrency, ceiling=concurrency)
        self.breaker = Breaker()
        self.stats = Stats()
        self._pace_until: float | None = None
        # a `Retry-After` on the 429 that opened the breaker or failed its probe: the next
        # cool-down lasts at least that long, by its tiers (J2, J13)
        self._hold_s: float | None = None

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # Waits: stopping, sleeping within the run's budget, `Retry-After` and proactive pacing.

    def _stopped(self) -> None:
        if self.stop.is_set():
            raise JevError("stopped", "the run was stopped")

    def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self.stats.wait_s += seconds
        if self._wait(seconds) or self.stop.is_set():
            raise JevError("stopped", "the run was stopped")

    def _budget_spent(self, what: str) -> JevError:
        return JevError("budget", f"the run's waiting budget ({self.budget.total_s / 60:.0f}"
                        f" min) is spent ({what})")  # fmt: skip

    def _spend(self, seconds: float, what: str) -> None:
        if seconds > self.budget.remaining_s:
            raise self._budget_spent(what)
        self.budget.spend(seconds)

    def _retry_after_wait(self, seconds: float) -> float:
        """The `Retry-After` tiers (P7): short paused through with jitter, medium paused and
        announced. A wait past the remaining budget stops the run: as `long_wait` (no result)
        only when it is past SHORT_WAIT_S or the whole budget, otherwise as `budget`, which
        keeps the cases scored so far (J1)."""
        if seconds > self.budget.remaining_s:
            if seconds <= SHORT_WAIT_S and seconds <= self.budget.total_s:
                raise self._budget_spent("Retry-After")
            resume = (self.clock.now() + timedelta(seconds=seconds)).astimezone()
            raise JevError("long_wait", f"TypeSafe asked to wait {math.ceil(seconds / 60)} min;"
                           " probably a quota or billing limit; re-run after"
                           f" {resume:%H:%M}")  # fmt: skip
        if seconds <= SHORT_WAIT_S:
            tier = "short"
            seconds = min(self.budget.remaining_s,
                          seconds * (1 + self.rng.uniform(0, SHORT_JITTER)))  # fmt: skip
        else:
            tier = "medium"
            resume = self.clock.now() + timedelta(seconds=seconds)
            self.on_pause(f"TypeSafe asked to wait {math.ceil(seconds)} s; resuming at"
                          f" {resume.astimezone():%H:%M:%S}")  # fmt: skip
        self.stats.pauses[tier] += 1
        self._emit("jev.pause", tier=tier, seconds=round(seconds, 3),
                   resume_at=to_ts(self.clock.now() + timedelta(seconds=seconds)))  # fmt: skip
        self._spend(seconds, "Retry-After")
        return seconds

    def _pace(self) -> float:
        """Proactive pacing from `x-ratelimit-*` (names unverified until the live check)."""
        if self._pace_until is None:
            return 0.0
        wait = self._pace_until - self.clock.monotonic()
        self._pace_until = None
        if wait <= 0:
            return 0.0
        self._spend(wait, "pacing")
        self.stats.pauses["pacing"] += 1
        self._sleep(wait)
        return wait

    def _note_limits(self, headers: httpx.Headers, need: int) -> None:
        """Pace the next call when a limit is spent. The headers' names and the reset's format
        are unverified until the live check, so a reset past SHORT_WAIT_S (perhaps an epoch or
        milliseconds) is ignored (J5); `need` is a token estimate."""
        for kind, enough in (("requests", 1), ("tokens", need)):
            left, reset = (headers.get(f"x-ratelimit-remaining-{kind}"),
                           headers.get(f"x-ratelimit-reset-{kind}"))  # fmt: skip
            if left is None or reset is None:
                continue
            try:
                n, r = float(left), float(reset)
            except ValueError:
                continue
            if math.isfinite(r) and 0 < r <= SHORT_WAIT_S and n < enough:
                until = self.clock.monotonic() + r
                self._pace_until = max(self._pace_until or until, until)

    # The breaker: wait out its cool-down, then probe with a minimal POST.

    def _breaker_gate(self) -> float:
        """While the breaker is open: wait out its cool-down, then probe. Returns the seconds
        waited. A try refused by the open breaker isn't spent (perf 3a)."""
        waited = 0.0
        while self.breaker.state == "open":
            cool = self.breaker.cool_down_s()
            hold, self._hold_s = self._hold_s, None
            if hold is not None and hold > cool:  # TypeSafe asked for longer: its tiers apply
                cool = self._retry_after_wait(hold)
            else:
                self._spend(cool, "the breaker's cool-down")
            self._sleep(cool)
            waited += cool
            self.probe()
        return waited

    def _opened(self) -> None:
        self.stats.breaker_trips += 1
        self._emit("jev.breaker_open", cool_down_s=self.breaker.cool_down_s(),
                   trips=self.breaker.trips)  # fmt: skip

    def probe(self) -> bool:
        """The minimal POST (no email content), after a cool-down: success half-opens the
        breaker; a fault re-opens it with the next cool-down (at least a 429's `Retry-After`,
        J13); a refused key or another version stops the run (J12)."""
        self._stopped()
        key = self._key()
        if not key:
            raise auth_error(None)
        body = {"model": self.pin, "questions": KEY_CHECK_QUESTIONS, "state": KEY_CHECK_STATE}
        level = self.aimd.limit
        reply = send(self.http, build_request(self.http, body, key), self.clock, self.timeout_s)
        self._log(reply, try_n=0, wait_s=0.0, n_questions=1, level=level)
        if reply.cls == "ok" and reply.body is not None:
            self._bill(reply.body)  # billed even when a stop discards it (J6)
        self._stopped()
        if reply.cls == "auth":
            raise auth_error(reply.status)
        if reply.cls == "ok" and reply.body is not None:
            if reply.body.get("model") != self.pin:
                raise other_version_error()
            self.breaker.on_probe_ok()
            self.aimd.limit = 1
            self._emit("jev.breaker_half_open")
            return True
        if reply.timed_out:
            self.budget.spend(reply.http_ms / 1000)
        if reply.cls == "rate_limit":
            self.stats.rate_limits += 1
            self._hold_s = retry_after(reply.headers, self.clock.now())
        self.breaker.on_probe_fault()
        self._opened()
        return False

    # One case: the call, its bill and log line, and what a 429, a fault or a 200 leads to.

    def _bill(self, body: Mapping[str, Any]) -> tuple[int | None, int | None]:
        """A 200's `usage` into the bill; one without input tokens is counted apart (J6)."""
        n_in, n_out = _usage(body)
        if n_in is None:
            self.stats.usage_missing += 1
        else:
            self.stats.input_tokens_billed += n_in
        return n_in, n_out

    def _log(self, reply: Reply, *, try_n: int, wait_s: float, n_questions: int,
             level: int) -> None:  # fmt: skip
        """One `jev.call` line. The token counts are `usage_in`/`usage_out`: the log scrubber
        blanks any key containing "token" (J13)."""
        n_in, n_out = _usage(reply.body) if reply.body is not None else (None, None)
        fields: dict[str, Any] = {
            "class": "timeout" if reply.timed_out else reply.cls,
            "status": reply.status, "http_ms": reply.http_ms, "wait_ms": int(wait_s * 1000),
            "concurrency": level, "usage_in": n_in, "usage_out": n_out, "try": try_n,
            "n_questions": n_questions,
            "request_id": reply.headers.get("x-typesafe-request-id"),  # service log only (D7)
            # a number on every 200 (live check); maybe the server's time, unverified (P7)
            "upstream_ms": _int_header(reply.headers.get("x-envoy-upstream-service-time")),
        }  # fmt: skip
        if reply.code is not None:
            fields["code"] = reply.code
        self._emit("jev.call", **fields)

    def classify(self, schema: CompiledSchema, email: Email, sent: tuple[str, ...], *,
                 first: bool = False, redact: bool = True) -> Result:  # fmt: skip
        """One case. Raises JevError for a stop; returns a Result otherwise (a failure carries
        its `fault`). The key is re-read before each try and the request rebuilt with it (J10)."""
        self._stopped()
        token = secrets.token_hex(8)
        body = request_body(schema, email, sent, pin=self.pin, token=token, redact=redact)
        est = estimate(body)
        if est.over:
            self.stats.faulted["request_error"] += 1
            return Result(None, fault="request_error", concurrency=self.aimd.limit)
        n_q = len(body["questions"])
        tries, waited, replied = 0, 0.0, False
        counts: Counter[str] = Counter()
        while True:
            self._stopped()
            before = self._breaker_gate() + self._pace()
            waited += before
            key = self._key()
            if not key:
                raise auth_error(None)
            req = build_request(self.http, body, key)
            level = self.aimd.limit  # before AIMD moves on this reply (J7)
            tries += 1
            reply = send(self.http, req, self.clock, self.timeout_s)
            self._log(reply, try_n=tries, wait_s=before, n_questions=n_q, level=level)
            ok = reply.cls == "ok" and reply.body is not None
            usage = self._bill(cast("dict[str, Any]", reply.body)) if ok else (None, None)
            self._stopped()  # an in-flight result after stop is discarded, billed (P18, J6)
            replied = replied or ok or reply.cls == "request"
            if ok:
                return self._answer(reply, schema, tries, waited, est, level, usage)
            if reply.cls == "auth":
                raise auth_error(reply.status)
            if reply.cls == "request" and first and reply.status in FIRST_CASE_STOP:
                raise first_case_error(reply.status or 0, reply.code)
            if reply.timed_out:
                self._spend(reply.http_ms / 1000, "timed-out tries")
            if reply.cls == "request":
                pause = None
            elif reply.cls == "rate_limit":
                pause = self._rate_limited(reply, est, counts)
            else:
                pause = self._provider_fault(counts)
            if pause is None:  # the case fails: scored as a failure (P1)
                fault = "provider" if reply.cls == "provider" else "request_error"
                self.stats.faulted[fault] += 1
                return Result(None, wait_ms=int(waited * 1000), tries=tries, concurrency=level,
                              fault=fault, status=reply.status, replied=replied)  # fmt: skip
            self._sleep(pause)
            waited += pause

    def _rate_limited(self, reply: Reply, est: Estimate, counts: Counter[str]) -> float | None:
        """A 429: the pause before the next try, or None when it is a request error (a request
        the estimate flags as near a limit). A quota code or a long `Retry-After` stops."""
        if reply.code is not None and reply.code in QUOTA_CODES:
            raise JevError("quota", f"TypeSafe refused the call for quota or billing (HTTP 429,"
                           f" {reply.code})", status=429, code=reply.code)  # fmt: skip
        self.stats.rate_limits += 1
        if est.near:
            return None
        self.aimd.on_rate_limit()
        self.stats.retries["rate_limit"] += 1
        ra = retry_after(reply.headers, self.clock.now())
        if self.breaker.on_fault():  # the cool-down, at least `ra`, replaces this pause (J2)
            self._opened()
            self._hold_s = ra
            counts["rate_limit"] += 1
            return 0.0
        if ra is not None:
            pause = self._retry_after_wait(ra)
        else:
            pause = backoff(counts["rate_limit"], RATE_LIMIT_CAP_S, self.rng)
            self._spend(pause, "backoff")
        counts["rate_limit"] += 1
        return pause

    def _provider_fault(self, counts: Counter[str]) -> float | None:
        """A provider fault: the backoff before the next try, or None once the case's tries are
        spent."""
        counts["provider"] += 1
        opened = self.breaker.on_fault()
        if opened:
            self._opened()
        if counts["provider"] >= TRIES:
            return None
        self.stats.retries["provider"] += 1
        if opened:  # the breaker's cool-down replaces this try's backoff (J2)
            return 0.0
        pause = backoff(counts["provider"] - 1, FAULT_CAP_S, self.rng)
        self._spend(pause, "backoff")
        return pause

    def _answer(self, reply: Reply, schema: CompiledSchema, tries: int, waited: float,
                est: Estimate, level: int,
                usage: tuple[int | None, int | None]) -> Result:  # fmt: skip
        """A 200 (already billed): its answer, with its latency and concurrency at `level`, the
        level it was sent at (J7)."""
        body = cast("dict[str, Any]", reply.body)
        if body.get("model") != self.pin:
            raise other_version_error()
        if self.breaker.on_success():
            self._emit("jev.breaker_closed")
        self.aimd.on_success()
        n_in, n_out = usage
        need = n_in if n_in is not None else math.ceil(est.total / PACE_BYTES_PER_TOKEN)
        self._note_limits(reply.headers, need)
        self.stats.latency_ms.setdefault(level, []).append(reply.http_ms)
        ans = systemone.map_answers(as_asked(body, schema), schema)
        return Result(ans.classification, ans.probabilities, _confidence(body), n_in, n_out,
                      classifier_ms=reply.http_ms, wait_ms=int(waited * 1000), tries=tries,
                      concurrency=level, status=200, replied=True)  # fmt: skip


# The TypeSafe key: stored after a call shows it works, or removed; audited without the key.


def _audit(conn: sqlite3.Connection, now: str, event: str, data: dict[str, Any]) -> None:
    conn.execute("INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
                 " VALUES (?, NULL, ?, 'os_user', 'ok', ?)",
                 (now, event, json.dumps(data)))  # fmt: skip


def set_key(conn: sqlite3.Connection, clock: Clock, store: SecretStore, key: str | None,
            transport: httpx.BaseTransport | None = None) -> dict[str, Any]:  # fmt: skip
    """Store the TypeSafe key after one minimal call shows it works, or remove it (`None`).
    Audited without the key (`{"replaced": bool}`); no step-up (D3)."""
    now = to_ts(clock.now())
    had = store.get(KEY_SECRET) is not None
    if key is None:
        store.delete(KEY_SECRET)
        with write_tx(conn):
            _audit(conn, now, "models.jev_key_cleared", {"had": had})
        return {"provider": "typesafe", "stored": False, "had": had}
    check_key(key, transport, clock=clock)
    store.set(KEY_SECRET, key)
    with write_tx(conn):
        _audit(conn, now, "models.jev_key_set", {"replaced": had})
    return {"provider": "typesafe", "stored": True, "replaced": had}


# Digests (§5.7): hashes of the field mapping, the state format and the questions, and the
# gate fields; together they make the gate key and prompt digest a result records.


def _sha(doc: Any) -> str:
    raw = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mapping_digest() -> str:
    """The shipped mapping's digest (`jev_mapping.digest`): the yes threshold, the score tie
    rule, and step 3's bands and bars (P13)."""
    return jev_mapping.digest()


def state_format_id() -> str:
    """The state format: the preamble, the template, the header and excerpt caps, header
    normalisation, `excerpt.VERSION` and the signals wording."""
    doc = {"fmt": "s1", "preamble_at": "state", "preamble": systemone.PREAMBLE,
           "template": state(Email("{name}", "{addr}", "{subject}", "{date}", "{text}"),
                             tuple(signals.WORDING), "{token}", redact=False),
           "caps": [NAME_CHARS, SUBJECT_CHARS, MAX_ATTACHMENTS, EXCERPT_CHARS],
           "date": "rfc3339-utc", "excerpt": excerpt.VERSION,
           "signals": signals.WORDING}  # fmt: skip
    return f"jev-s1-state-{_sha(doc)[:12]}"


def gate_fields(schema: CompiledSchema, rules: CompiledRules) -> list[str]:
    """The 11 fixed fields (the 7 safety items, the 3 workflow fields and `priority`) and any
    field a hide-capable rule in the effective rule set reads, in schema order."""
    want = set(SAFETY_FIELDS) | jev_mapping.hide_fields(rules)
    return [n for n in schema.fields if n in want]


def _in_order(q: dict[str, Any]) -> dict[str, Any]:
    """Questions with each `criteria` as a list of pairs: `_sha` sorts keys, and the order of a
    choice's options is part of what Jev is asked (it leans toward the first, /model-jaggedness)."""
    return {k: v | {"criteria": list(v["criteria"].items())}
            if isinstance(v.get("criteria"), dict) else v for k, v in q.items()}  # fmt: skip


def questions_digest(schema: CompiledSchema, rules: CompiledRules) -> str:
    q = _in_order(questions(schema))
    return _sha([[n, q[n]] for n in gate_fields(schema, rules)])


def gate_key(schema: CompiledSchema, rules: CompiledRules, *, pin: str | None = None) -> str:
    """`jev:<pin>+<questions digest>+<state format id>+<mapping digest>` (§5.7). Recorded only;
    no gate reads it until step 4."""
    return (f"jev:{pin or load_pin()}+{questions_digest(schema, rules)[:16]}"
            f"+{state_format_id()}+{mapping_digest()[:16]}")  # fmt: skip


def prompt_digest(schema: CompiledSchema, *, pin: str | None = None) -> str:
    """The preamble, the full question JSON (every field, in order), the caps, the state format,
    the signals wording, the mapping digest and the pin."""
    return _sha({"preamble": systemone.PREAMBLE, "questions": _in_order(questions(schema)),
                 "caps": [NAME_CHARS, SUBJECT_CHARS, MAX_ATTACHMENTS, EXCERPT_CHARS],
                 "state_format": state_format_id(), "signals": signals.WORDING,
                 "mapping": mapping_digest(), "pin": pin or load_pin()})  # fmt: skip


def excerpt_cap() -> dict[str, int]:
    """The character and header caps (`excerpt_cap` in a result's summary, P13)."""
    return {"excerpt_chars": EXCERPT_CHARS, "name_chars": NAME_CHARS,
            "subject_chars": SUBJECT_CHARS, "attachments": MAX_ATTACHMENTS}  # fmt: skip
