"""Preset D in the service: TypeSafe Jev classifies, Claude acts (v3.0.0 step 4; OD-509, OD-510;
plan planning-docs/v3.0.0-STEP4.md §2).

Imported only by `service.py` and `api.py` (an import-linter contract): every other module is in
the classifier's import closure, which never imports `jev`, and reads what this module stores
through `jev_keys`.

- **Keys** (`refresh_keys`, at service start and each tick): D's gate key and prompt digest from
  `jev.gate_key` and `jev.prompt_digest` for the effective schema, the starter rules (as `ecf eval
  run --classifier-backend jev` computes them, so a D eval's source run matches) and the pin, in
  the `jev.keys` setting, for `claude_pins` (D's pin key), `classifier.store` and `reprocess`.
- **The drain** (`start_drain`, by the tick): a one-shot thread, as the model watch and scheduled
  export are, while D items wait, Jev isn't down and none runs; `route_new` wakes the tick
  (`jev_keys.WAKE`). It classifies one item at a time (concurrency 1, S3-D4) until none is left,
  a fault, or the service stops. The pick (P3, P9, P12), in one write transaction: unmarked (new)
  mail before marked, then by `claude_since`, on addresses not paused or removed with the owner's
  agreement to TypeSafe (`jev_consent_at`), with no claim live until its expiry plus `GRACE`; the
  drain claims it (session `jev`), so `/ecf-review` can't take it meanwhile. The request is built
  as `claude_review.get_message` builds Claude's, redaction always on; the answer is stored by
  `classifier.store(provider="jev")`, which stores only over no classification (P3).
- **The service-mode client** (`ServiceClient`, P4): no run budget, no in-call cool-down, retry
  or pacing: a fault returns at once and the drain stops; the tick decides when to try again. It
  sleeps at most once per item, for a `Retry-After` within the short tier; a longer one is
  `long_wait`. Each drain and probe builds its own client, so one a transport error broke (after a
  wake, say) is never reused.
- **Failures** (§2.3, P11): an item-specific failure (an answer that doesn't map, a request error
  after a successful call, a request too large to send) counts in `facts.jev_attempts`; at
  `ATTEMPTS` the item waits with a System Error naming it, and `ecf doctor` lists it. Request errors
  on `PIN_AFTER` consecutive distinct items read as a retired or changed pin. A locked secret store
  is a quiet pause: nothing counted, no alert, no timer.
- **Jev down, per install** (`tick`; OD-510; one writer of `jev.state`: the tick): down at once on
  an auth failure or a removed key, a retired or changed pin, a `Retry-After` past the short tier
  or a quota refusal; or after `jev_down_after_minutes` of provider faults counted in awake,
  online time while D items wait (reset on wake; paused while api.typesafe.ai doesn't resolve); or
  when a running drain made no progress for that long (liveness). While down no drain starts and
  D mail waits: there is no Claude fallback in v3.0.0 (P2(a)). The probe (the key check's minimal
  POST, no email content) runs at each cool-down end, 30 s doubling to 5 min, in its own thread;
  `PROBE_OK` consecutive successes bring Jev back. One `jev_down` alert (install-wide; count,
  oldest age and reason, its detail kept current), resolved on recovery; at most `REOPEN_CAP`
  opens a UTC day. Down and up are audited (`jev.down`, `jev.up`), with the reason only.
- **Records** (§5, P7): one `model_calls` row per Jev call and probe: numbers and fixed codes
  (backend, model, fault, ms, tries, billed input tokens), never content; TypeSafe's request ID goes
  to the service log only (`jev.call`, D7).

Every limit here is a placeholder until step 3 sets it (SPEC §15.4).
"""

from __future__ import annotations

import json
import math
import secrets
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, cast

import httpx

from ecf.schema import CompiledSchema
from ecf_server import (
    alerts,
    classifier,
    claude_queue,
    config,
    health,
    jev,
    jev_keys,
    marked,
    rules,
    settings,
    signals,
)
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.log_bridge import log
from ecf_server.notify import Notifier
from ecf_server.secretstore import SecretStore
from ecf_server.state_machine import Status

PRESET = jev_keys.PRESET
SESSION = "jev"  # the drain's claims (`claims.session_id`)
CLAIM_TTL = timedelta(seconds=120)  # longer than one call (jev.TIMEOUT_S, one short wait)
GRACE = timedelta(seconds=300)  # as claude_review.GRACE: an expired claim's agent may still run
ATTEMPTS = 3  # item-specific failures before an item waits (placeholder, step 3)
PIN_AFTER = 3  # request errors on this many consecutive distinct items: the pin (placeholder)
PROBE_OK = 2  # K: consecutive probe successes that bring Jev back (placeholder)
REOPEN_CAP = 3  # `jev_down` alert opens a UTC day (placeholder)
ALERT = "jev_down"
DOWN_SETTING = "jev_down_after_minutes"
REASONS = {
    "faults": "TypeSafe kept failing",
    "stalled": "the Jev drain stopped making progress",
    "key": "TypeSafe refused the API key, or it was removed: ecf models api-key set --provider"
           " typesafe",
    "pin": "TypeSafe doesn't serve the pinned Jev version ({pin}), or answered with another one:"
           " upgrade ecf (this doesn't recover by itself)",
    "long_wait": "TypeSafe asked ecf to wait longer than its short tier (probably a quota or"
                 " billing limit)",
    "quota": "TypeSafe refused the calls for quota or billing",
}  # fmt: skip
# a drain's stop class -> the reason Jev goes down at once (P10); `provider` starts the timer
AT_ONCE = {"auth": "key", "pin": "pin", "long_wait": "long_wait", "quota": "quota"}
Spawn = Callable[[Callable[[], None]], None]


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="ecf-jev", daemon=True).start()


# The keys: D's gate key and prompt digest, computed here and stored for every other reader.


def refresh_keys(conn: sqlite3.Connection, clock: Clock) -> dict[str, Any]:
    """`jev.keys` for the effective schema, the starter rules and the pin; written only when one
    of them (or the shipped mapping) changed."""
    schema = config.current_schema(conn)
    starter = rules.load_starter_rules(schema)
    pin = jev.load_pin()
    want: dict[str, Any] = {"pin": pin, "schema": schema.digest, "rules": starter.digest,
                            "mapping": jev_keys.mapping_digest()}  # fmt: skip
    have = jev_keys.stored(conn)
    if all(have.get(k) == v for k, v in want.items()) and have.get("gate_key"):
        return have
    doc = want | {"gate_key": jev.gate_key(schema, starter, pin=pin),
                  "prompt_digest": jev.prompt_digest(schema, pin=pin)}  # fmt: skip
    now = to_ts(clock.now())
    with write_tx(conn):
        _put(conn, jev_keys.KEYS, doc, now)
    return doc


def _put(conn: sqlite3.Connection, key: str, value: Any, now: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'service')"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at,"
        " updated_by = excluded.updated_by",
        (key, json.dumps(value, sort_keys=True), now),
    )


# The service-mode client: faults return at once; at most one short `Retry-After` per item.


class LockedError(Exception):
    """The secret store couldn't be read: a quiet pause (P4)."""


class ServiceClient(jev.Client):
    """`jev.Client` for the service (P4): no waiting budget, no breaker cool-down, no retry after
    a provider fault, no proactive pacing; a 429 waits once for a `Retry-After` within the short
    tier, a longer one stops (`long_wait`), and one without a usable wait is a provider fault."""

    def __init__(self, key: Callable[[], str | None], *,
                 transport: httpx.BaseTransport | None = None, clock: Clock | None = None,
                 stop: threading.Event | None = None,
                 wait: Callable[[float], bool] | None = None) -> None:  # fmt: skip
        super().__init__(key, transport=transport, clock=clock, stop=stop, wait=wait,
                         budget_s=math.inf)  # fmt: skip
        self._short_used = False

    def classify(self, schema: CompiledSchema, email: jev.Email, sent: tuple[str, ...], *,
                 first: bool = False, redact: bool = True, fmt: jev.StateFormat = "s1",
                 preamble: jev.Preamble = "state") -> jev.Result:  # fmt: skip
        self._short_used = False
        return super().classify(schema, email, sent, first=first, redact=redact, fmt=fmt,
                                preamble=preamble)  # fmt: skip

    def _breaker_gate(self) -> float:
        return 0.0  # the tick probes while Jev is down; no in-call cool-down

    def _pace(self) -> float:
        return 0.0

    def _note_limits(self, headers: httpx.Headers, need: int) -> None:
        return None

    def _provider_fault(self, counts: Any) -> float | None:
        self.breaker.on_fault()
        return None  # the item fails at once with `provider`; the drain stops

    def _rate_limited(self, reply: jev.Reply, est: jev.Estimate,
                      counts: Any) -> float | None:  # fmt: skip
        if reply.code is not None and reply.code in jev.QUOTA_CODES:
            raise jev.JevError("quota", f"TypeSafe refused the call for quota or billing (HTTP"
                               f" 429, {reply.code})", status=429, code=reply.code)  # fmt: skip
        self.stats.rate_limits += 1
        if est.near:
            return None  # a request near a limit: a request error, as in an eval
        ra = jev.retry_after(reply.headers, self.clock.now())
        if ra is not None and ra > jev.SHORT_WAIT_S:
            raise jev.JevError("long_wait", f"TypeSafe asked to wait {math.ceil(ra)} s",
                               status=429)  # fmt: skip
        if ra is None or self._short_used:
            raise jev.JevError("provider", "TypeSafe is rate limiting (HTTP 429)", status=429)
        self._short_used = True
        self.stats.pauses["short"] += 1
        return ra


# In memory: what the drain and the probe report to the tick, which alone writes `jev.state`.


@dataclass
class Outcome:
    """One drain: why it ended (None: nothing left), how many items it stored, and the request
    errors on consecutive distinct items since its last success."""

    stopped: str | None = None
    ok: int = 0
    request_errors: list[str] = field(default_factory=list[str])


@dataclass
class Live:
    """The drain's and probe's state in `ServiceState` (P4 liveness). Tests set `transport`,
    `wait`, `spawn` and `resolves`."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    stop: threading.Event = field(default_factory=threading.Event)
    running: bool = False
    probing: bool = False
    had_ok: bool = False  # a call succeeded since the service started or Jev came back (P11)
    last_run: str | None = None
    last_ok: str | None = None
    progress_at: str | None = None  # the drain started, or finished an item
    outcomes: list[Outcome] = field(default_factory=list[Outcome])
    probes: list[str] = field(default_factory=list[str])
    transport: httpx.BaseTransport | None = None
    wait: Callable[[float], bool] | None = None
    spawn: Spawn = _thread
    resolves: Callable[[str], bool] = health.resolves

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"running": self.running, "probing": self.probing,
                    "last_run": self.last_run, "last_ok": self.last_ok,
                    "progress_at": self.progress_at}  # fmt: skip


# The drain: pick, claim, ask Jev, store; one item at a time.


def _claimable(now: datetime) -> tuple[str, list[Any]]:
    """The items the drain may take (P3, P9): D, waiting for Jev, below the attempt limit, on an
    address that is neither paused nor removed and whose owner agreed, with no claim live until its
    expiry plus GRACE."""
    sql = (" FROM items i JOIN addresses a USING (address_id) WHERE a.preset = 'D'"  # noqa: S608 - fixed names
           " AND a.paused = 0 AND a.removed_at IS NULL"
           f" AND json_extract(a.overrides, '$.{jev_keys.CONSENT}') IS NOT NULL"
           " AND i.status = 'awaiting_claude' AND i.classification IS NULL"
           " AND coalesce(json_extract(i.facts, '$.jev_attempts'), 0) < ?"
           " AND NOT EXISTS (SELECT 1 FROM claims c WHERE c.stable_id = i.stable_id"
           " AND (c.state = 'held' OR (c.state = 'claimed' AND c.expires_at > ?)))")  # fmt: skip
    return sql, [ATTEMPTS, to_ts(now - GRACE)]


def claimable(conn: sqlite3.Connection, now: datetime) -> int:
    sql, args = _claimable(now)
    return int(conn.execute("SELECT count(*)" + sql, args).fetchone()[0])


def pick(conn: sqlite3.Connection, clock: Clock, tried: list[str]) -> sqlite3.Row | None:
    """The next item and the drain's claim on it, in one write transaction: new mail first, then
    marked, by `claude_since` (P12)."""
    now = clock.now()
    sql, args = _claimable(now)
    with write_tx(conn):
        row = conn.execute(
            "SELECT i.*, a.stage" + sql + " AND i.stable_id NOT IN (SELECT value FROM"  # noqa: S608
            f" json_each(?)) ORDER BY {marked.sql('i.')}, i.claude_since, i.stable_id LIMIT 1",
            [*args, json.dumps(tried)]).fetchone()  # fmt: skip
        if row is None:
            return None
        fence = conn.execute("SELECT fence FROM claims WHERE stable_id = ?",
                             (row["stable_id"],)).fetchone()  # fmt: skip
        token = secrets.token_hex(16)  # never presented: only the claim matters
        conn.execute(
            "INSERT INTO claims (stable_id, address_id, session_id, token_hash, fence, need, agent,"
            " batch_id, claimed_at, expires_at, state, invalid, outcome, reported)"
            " VALUES (?, ?, ?, ?, ?, 'classify', 'jev', 'jev', ?, ?, 'claimed', 0, NULL, 1)"
            " ON CONFLICT (stable_id) DO UPDATE SET session_id = excluded.session_id,"
            " token_hash = excluded.token_hash, fence = excluded.fence, need = excluded.need,"
            " agent = excluded.agent, batch_id = excluded.batch_id,"
            " claimed_at = excluded.claimed_at, expires_at = excluded.expires_at,"
            " state = 'claimed', invalid = 0, outcome = NULL, reported = 1",
            (row["stable_id"], row["address_id"], SESSION, token,
             (int(fence["fence"]) if fence else 0) + 1, to_ts(now), to_ts(now + CLAIM_TTL)),
        )  # fmt: skip
    return cast("sqlite3.Row", row)


def _end_claim(conn: sqlite3.Connection, sid: str, state: str) -> None:
    with write_tx(conn):
        conn.execute("UPDATE claims SET state = ?, outcome = ? WHERE stable_id = ? AND"
                     " session_id = ? AND state = 'claimed'",
                     (state, "jev" if state == "done" else None, sid, SESSION))  # fmt: skip


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def email_of(conn: sqlite3.Connection, item: sqlite3.Row) -> tuple[jev.Email, tuple[str, ...]]:
    """What Claude's classifier gets (`claude_review.get_message`): the sender, subject, date
    (`internaldate`, else when ecf recorded it), attachment metadata with the same inline/name
    filter, the stored (redacted) classifier excerpt; and the OD-483 signals."""
    ex = conn.execute("SELECT classifier_text FROM excerpts WHERE stable_id = ?",
                      (item["stable_id"],)).fetchone()  # fmt: skip
    facts: dict[str, Any] = json.loads(item["facts"] or "{}")
    listed: list[Any] = facts.get("attachments") or []
    atts = tuple(jev.Attachment(a.get("name"), a.get("type"), _int(a.get("size")))
                 for a in (cast("dict[str, Any]", x) for x in listed if isinstance(x, dict))
                 if not a.get("inline") or a.get("name"))  # fmt: skip
    date = json.loads(item["locator"] or "{}").get("internaldate") or item["created_at"]
    email = jev.Email(item["sender_name"] or "", item["sender"] or "", item["subject"] or "",
                      str(date or ""), str(ex[0] or "") if ex else "", atts)  # fmt: skip
    return email, signals.sent(facts)


def _call_row(  # noqa: PLR0913 - a row's fixed codes, keyword-only
    conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row | None, role: str, outcome: str, *,
    pin: str, fault: str | None = None, ms: int | None = None, tries: int | None = None,
    tokens_in: int | None = None, tokens_out: int | None = None,
) -> None:  # fmt: skip
    """One `model_calls` row (I5: numbers and fixed codes only)."""
    with write_tx(conn):
        conn.execute(
            "INSERT INTO model_calls (ts, address_id, preset, stage, role, outcome, digest,"
            " prompt_tokens, output_tokens, backend, model, fault, ms, tries)"
            " VALUES (?, ?, 'D', ?, ?, ?, ?, ?, ?, 'jev', ?, ?, ?, ?)",
            (to_ts(clock.now()), item["address_id"] if item else None,
             item["stage"] if item else None, role, outcome, pin, tokens_in, tokens_out, pin,
             fault, ms, tries),
        )  # fmt: skip


def _attempt(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, item: sqlite3.Row,
             why: str) -> None:  # fmt: skip
    """An item-specific failure (P11); at the limit the item waits, with a System Error naming
    it (its address and short id, never its content)."""
    facts: dict[str, Any] = json.loads(item["facts"] or "{}")
    n = int(facts.get("jev_attempts") or 0) + 1
    with write_tx(conn):
        conn.execute("UPDATE items SET facts = json_set(facts, '$.jev_attempts', ?) WHERE"
                     " stable_id = ?", (n, item["stable_id"]))  # fmt: skip
    log.info("jev.attempt_failed", stable_id=str(item["stable_id"])[:8], why=why, attempts=n)
    if n == ATTEMPTS:
        alerts.event(conn, clock, notifier, "system_error",
                     f"{item['address_id']}: TypeSafe Jev couldn't classify email"
                     f" {str(item['stable_id'])[:8]} in {n} tries ({why}); it waits, and"
                     " nothing else classifies it (no Claude fallback in v3.0.0). ecf doctor"
                     " lists it.")  # fmt: skip


def drain(conn: sqlite3.Connection, clock: Clock, store: Callable[[], SecretStore], live: Live,
          notifier: Notifier) -> Outcome:  # fmt: skip
    """Classify D items until none is left, a fault, or the service stops (module notes)."""
    out = Outcome()
    try:
        s = store()
    except Exception:  # no usable secret store: a quiet pause
        out.stopped = "locked"
        return out

    def key() -> str | None:
        try:
            return s.get(jev_keys.KEY_SECRET)
        except Exception as exc:
            raise LockedError from exc

    tried: list[str] = []
    with ServiceClient(key, transport=live.transport, clock=clock, stop=live.stop,
                       wait=live.wait) as client:  # fmt: skip
        while not live.stop.is_set():
            item = pick(conn, clock, tried)
            if item is None:
                break
            sid = str(item["stable_id"])
            tried.append(sid)
            stop = _one(conn, clock, client, item, live, notifier, out)
            with live.lock:
                live.progress_at = to_ts(clock.now())
            if stop is not None:
                out.stopped = stop
                break
    return out


def _one(  # noqa: PLR0911 - one return per way an item ends
    conn: sqlite3.Connection, clock: Clock, client: jev.Client, item: sqlite3.Row, live: Live,
    notifier: Notifier, out: Outcome,
) -> str | None:  # fmt: skip
    """One item; returns why the drain stops, or None to go on."""
    sid = str(item["stable_id"])
    schema = config.current_schema(conn)
    email, sent = email_of(conn, item)
    try:
        res = client.classify(schema, email, sent, redact=True)
    except LockedError:
        _end_claim(conn, sid, "released")
        return "locked"
    except jev.JevError as e:
        _end_claim(conn, sid, "released")
        if e.cls != "stopped":
            _call_row(conn, clock, item, "classifier", "error", pin=client.pin, fault=e.cls)
        return e.cls
    _call_row(conn, clock, item, "classifier", "ok" if res.classification else "error",
              pin=client.pin, fault=res.fault or (None if res.classification else "unmapped"),
              ms=res.classifier_ms, tries=res.tries, tokens_in=res.input_tokens,
              tokens_out=res.output_tokens)  # fmt: skip
    if res.fault == "provider":
        _end_claim(conn, sid, "released")
        return "provider"
    if res.classification is None:
        _end_claim(conn, sid, "released")
        if res.fault == "request_error" and res.tries == 0:  # too large to send: its own
            _attempt(conn, clock, notifier, item, "the email is too large to send")
            return None
        if res.fault == "request_error":
            out.request_errors.append(sid)
            if live.had_ok:  # item-specific: Jev answered others (P11)
                _attempt(conn, clock, notifier, item, "TypeSafe refused the request")
            return "pin" if len(out.request_errors) >= PIN_AFTER else None
        _attempt(conn, clock, notifier, item, "an answer ecf couldn't map")
        return None
    out.ok += 1
    out.request_errors.clear()
    with live.lock:
        live.had_ok, live.last_ok = True, to_ts(clock.now())
    stored = classifier.store(conn, clock, sid, res.classification, {"classifier": client.pin},
                              expected=Status.AWAITING_CLAUDE, actor="jev",
                              schema_digest=schema.digest, provider="jev")  # fmt: skip
    # the schema changed during the call: asked again with the new one by a later drain (OD-478)
    _end_claim(conn, sid, "done" if stored else "released")
    return None


def start_drain(connect: Callable[[], sqlite3.Connection], clock: Clock,
                store: Callable[[], SecretStore], live: Live,
                notifier: Notifier) -> bool:  # fmt: skip
    """The drain in its own thread; False when one is running."""
    with live.lock:
        if live.running:
            return False
        live.running = True
        live.last_run = live.progress_at = to_ts(clock.now())

    def work() -> None:
        out = Outcome(stopped="error")
        try:
            conn = connect()
            try:
                out = drain(conn, clock, store, live, notifier)
            finally:
                conn.close()
        except Exception as exc:  # never raised into the thread; the tick starts another
            log.error("jev.drain_failed", error_type=type(exc).__name__)
        finally:
            with live.lock:
                live.outcomes.append(out)
                live.running = False

    live.spawn(work)
    return True


# The probe: the key check's minimal POST (no email content), while Jev is down.


def probe(conn: sqlite3.Connection, clock: Clock, store: Callable[[], SecretStore],
          live: Live) -> str:  # fmt: skip
    """`ok`, `fault`, `auth` (refused or removed key), `pin` (the pinned version refused or
    another answered) or `locked`."""
    try:
        key = store().get(jev_keys.KEY_SECRET)
    except Exception:
        return "locked"
    if not key:
        return "auth"
    pin = jev.load_pin()
    with jev.http_client(live.transport, read_s=jev.KEY_CHECK_TIMEOUT_S) as client:
        body = {"model": pin, "questions": jev.KEY_CHECK_QUESTIONS, "state": jev.KEY_CHECK_STATE}
        reply = jev.send(client, jev.build_request(client, body, key), clock,
                         jev.KEY_CHECK_TIMEOUT_S)  # fmt: skip
    log.info("jev.call", **{"class": "timeout" if reply.timed_out else reply.cls,
                            "status": reply.status, "http_ms": reply.http_ms, "try": 0,
                            "n_questions": 1,
                            "request_id": reply.headers.get("x-typesafe-request-id")})  # fmt: skip
    if reply.cls == "ok" and reply.body is not None:
        got = "ok" if reply.body.get("model") == pin else "pin"
    elif reply.cls == "auth":
        got = "auth"
    elif reply.status in jev.FIRST_CASE_STOP:
        got = "pin"
    else:
        got = "fault"
    _call_row(conn, clock, None, "probe", "ok" if got == "ok" else
              "timeout" if reply.timed_out else "error", pin=pin,
              fault=None if got == "ok" else got, ms=reply.http_ms, tries=1)  # fmt: skip
    return got


def start_probe(connect: Callable[[], sqlite3.Connection], clock: Clock,
                store: Callable[[], SecretStore], live: Live) -> bool:  # fmt: skip
    with live.lock:
        if live.probing:
            return False
        live.probing = True

    def work() -> None:
        got = "fault"
        try:
            conn = connect()
            try:
                got = probe(conn, clock, store, live)
            finally:
                conn.close()
        except Exception as exc:
            log.error("jev.probe_failed", error_type=type(exc).__name__)
        finally:
            with live.lock:
                live.probes.append(got)
                live.probing = False

    live.spawn(work)
    return True


# The tick: the one writer of `jev.state` (up or down, why, the probe's ladder, the down timer).


def _cool_down(level: int) -> float:
    return min(jev.COOL_DOWN_MAX_S, jev.COOL_DOWN_S * 2**level)


def _down(st: dict[str, Any], reason: str, now: datetime) -> None:
    if st.get("down"):
        st["reason"] = reason
        return
    st.update(down=True, reason=reason, since=to_ts(now), level=0, probe_ok=0,
              probe_at=to_ts(now + timedelta(seconds=_cool_down(0))))  # fmt: skip


def _apply(st: dict[str, Any], out: Outcome, now: datetime) -> None:
    """A drain's outcome (P10, P11)."""
    if out.ok:
        st.update(faulting=False, fault_s=0.0)
    if out.stopped in AT_ONCE:
        _down(st, AT_ONCE[out.stopped], now)
    elif out.stopped == "provider" and not st.get("faulting"):
        st.update(faulting=True, fault_s=0.0)


def _apply_probe(st: dict[str, Any], got: str, now: datetime) -> bool:
    """A probe's result while down; True when Jev is back."""
    if not st.get("down") or got == "locked":
        return False
    if got == "ok":
        st["probe_ok"] = int(st.get("probe_ok") or 0) + 1
        if st["probe_ok"] >= PROBE_OK:
            return True
        st["probe_at"] = to_ts(now)  # the next one at the next tick
        return False
    if got in ("auth", "pin"):
        st["reason"] = "key" if got == "auth" else "pin"
    st["probe_ok"] = 0
    st["level"] = int(st.get("level") or 0) + 1
    st["probe_at"] = to_ts(now + timedelta(seconds=_cool_down(st["level"])))
    return False


def detail(conn: sqlite3.Connection, st: dict[str, Any]) -> str:
    """The `jev_down` alert's text: the reason, the D mail waiting (count and oldest) and what
    happens; counts and dates only (I5)."""
    why = REASONS.get(str(st.get("reason")), str(st.get("reason"))).format(pin=jev.load_pin())
    row = conn.execute(
        "SELECT count(*), min(i.claude_since) FROM items i JOIN addresses a USING (address_id)"  # noqa: S608
        f" WHERE a.removed_at IS NULL AND i.status = 'awaiting_claude' AND"
        f" {claude_queue.FOR_JEV}").fetchone()  # fmt: skip
    n, oldest = int(row[0]), row[1]
    waits = (f"{n} preset D email(s) wait (the oldest since {str(oldest)[:16].replace('T', ' ')}"
             " UTC)" if n else "no preset D email waits yet")  # fmt: skip
    return (f"TypeSafe Jev is down since {str(st.get('since'))[:16].replace('T', ' ')} UTC: {why}."
            f" {waits}; D mail waits until Jev is back, and nothing else classifies it (no"
            " Claude fallback in v3.0.0). ecf checks TypeSafe every few minutes; ecf doctor shows"
            " the state.")  # fmt: skip


def tick(conn: sqlite3.Connection, clock: Clock, live: Live, notifier: Notifier,
         connect: Callable[[], sqlite3.Connection], store: Callable[[], SecretStore], *,
         awake: float, woke: bool) -> dict[str, Any]:  # fmt: skip
    """Each tick (§2.2, §2.3): the keys, the drain's and probe's results, the down timer and
    liveness, the alert, then a probe (down) or a drain (up, D items claimable). Returns the
    state written."""
    refresh_keys(conn, clock)
    now = clock.now()
    st: dict[str, Any] = dict(jev_keys.state(conn))
    before = json.dumps(st, sort_keys=True)
    was_down = bool(st.get("down"))
    with live.lock:
        outs, probes = list(live.outcomes), list(live.probes)
        live.outcomes.clear()
        live.probes.clear()
    for out in outs:
        _apply(st, out, now)
    back = any([_apply_probe(st, got, now) for got in probes])
    waiting = claimable(conn, now)
    limit = 60 * int(settings.get(conn, DOWN_SETTING))
    if not st.get("down") and st.get("faulting") and waiting:
        if woke:  # the timer counts awake time only (P4)
            st["fault_s"] = 0.0
        elif live.resolves(jev_keys.HOST):  # offline: paused
            st["fault_s"] = float(st.get("fault_s") or 0.0) + max(0.0, awake)
        if float(st.get("fault_s") or 0.0) >= limit:
            _down(st, "faults", now)
    snap = live.snapshot()
    if (not st.get("down") and snap["running"] and snap["progress_at"]
            and (now - from_ts(snap["progress_at"])).total_seconds() >= limit):  # fmt: skip
        _down(st, "stalled", now)
    if back:
        st = {"down": False, "up_at": to_ts(now), "opens": st.get("opens"),
              "alerted": st.get("alerted")}  # fmt: skip
        with live.lock:
            live.had_ok = False  # a request error counts toward attempts after a new success
    _write(conn, clock, notifier, st, before, was_down)
    if st.get("down"):
        if now >= from_ts(str(st["probe_at"])):
            start_probe(connect, clock, store, live)
    elif waiting or jev_keys.WAKE.is_set():
        jev_keys.WAKE.clear()
        if waiting:
            start_drain(connect, clock, store, live, notifier)
    return st


def _write(conn: sqlite3.Connection, clock: Clock, notifier: Notifier, st: dict[str, Any],
           before: str, was_down: bool) -> None:  # fmt: skip
    """`jev.state` when it changed, the audit rows, and the alert (opened once, its detail kept
    current, resolved on recovery; at most REOPEN_CAP opens a UTC day)."""
    now = clock.now()
    down = bool(st.get("down"))
    if down and not was_down:
        day = now.date().isoformat()
        opens: dict[str, Any] = st.get("opens") or {}
        n = int(opens.get("n") or 0) if opens.get("day") == day else 0
        st["alerted"] = n < REOPEN_CAP
        st["opens"] = {"day": day, "n": n + 1 if st["alerted"] else n}
    if json.dumps(st, sort_keys=True) != before:
        with write_tx(conn):
            _put(conn, jev_keys.STATE, st, to_ts(now))
            if down != was_down:
                conn.execute("INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
                             " VALUES (?, NULL, ?, 'service', 'ok', ?)",
                             (to_ts(now), "jev.down" if down else "jev.up",
                              json.dumps({"reason": st.get("reason")} if down else {})),
                             )  # fmt: skip
        if down != was_down:
            log.info("jev.down" if down else "jev.up", reason=st.get("reason"))
    if down and st.get("alerted"):
        health.open_alert(conn, clock, notifier, ALERT, None, detail(conn, st))
    elif not down and was_down:
        health.resolve_alert(conn, clock, notifier, ALERT, None)


# What `ecf status` and `ecf doctor` show.


def status(conn: sqlite3.Connection, live: Live, store: SecretStore | None) -> dict[str, Any]:
    """The pin, the key, up or down and why, the drain, what waits, D addresses without the
    owner's agreement, items at the attempt limit (§5, S4-D18)."""
    st = jev_keys.state(conn)
    d = conn.execute("SELECT address_id, overrides FROM addresses WHERE removed_at IS NULL AND"
                     " preset = 'D' ORDER BY address_id").fetchall()  # fmt: skip
    key: bool | None = None
    if store is not None and d:  # the secret store is read only while an address uses D
        try:
            key = bool(store.get(jev_keys.KEY_SECRET))
        except Exception:  # a locked store: unknown
            key = None
    limit = conn.execute("SELECT stable_id FROM items WHERE status = 'awaiting_claude' AND"
                         " classification IS NULL AND json_extract(facts, '$.jev_attempts') >= ?"
                         " ORDER BY stable_id", (ATTEMPTS,)).fetchall()  # fmt: skip
    waits = claude_queue.waiting_for_jev(conn)
    return {
        "addresses": [str(r[0]) for r in d],
        "no_consent": [str(r[0]) for r in d if not jev_keys.consented(r[1])],
        "pin": jev_keys.pin(), "mapping": jev_keys.mapping_digest(), "key": key,
        "keys": jev_keys.stored(conn).get("gate_key"),
        "down": bool(st.get("down")), "reason": st.get("reason"), "since": st.get("since"),
        "detail": detail(conn, st) if st.get("down") else None,
        "drain": live.snapshot(), "waiting": sum(waits.values()), "waiting_by_address": waits,
        "at_limit": [str(r[0])[:8] for r in limit],
    }  # fmt: skip
