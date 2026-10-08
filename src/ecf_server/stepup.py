"""Step-up nonces (SPEC §9.6; V1.2 step 2; OD-073: the service performs the check, a client's
result is never trusted).

A step-up is issued for a *purpose* and a *target* (a grant, a setting and its new value, a
config document, an answer, ...). The service loads the target itself and computes both the bound
hash and the dialog text from it; a client supplies neither (security review of the V1.2 plan,
2026-09-29). The flow:

1. `issue(purpose, target)`: a single-use nonce with a short code; the CLI prints the dialog text
   and the code, and the dialog repeats both, so the operator can tell ecf's dialog from another.
2. `verify(nonce, password?)`: the service recomputes the bound hash (refusing if the target has
   changed since), runs the Stepper (one authentication at a time, 60 s), and records the result.
   At most `MAX_TRIES` verifications per `TRY_WINDOW` across the service.
3. `consume(purpose, target, nonce)` inside the protected action: recomputes the hash from the
   target the action is about to act on, and consumes the nonce once. A verified nonce lapses
   `VERIFIED_FOR` after verification if unused.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ecf.errors import (
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    StepupFailedError,
    StepupRequiredError,
)
from ecf.ids import new_nonce_id
from ecf.text import one_line
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.stepper import Stepper

DEFAULT_TTL = timedelta(minutes=10)  # a nonce the CLI asks for; queued answers pass their own
VERIFIED_FOR = timedelta(minutes=2)
MAX_TRIES = 5  # [proposed] in §9.6
TRY_WINDOW = timedelta(minutes=10)
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
CODE_LEN = 4


@dataclass(frozen=True)
class Bound:
    """What a step-up is bound to: a hash of the exact change, and the dialog text naming the
    action, recipient and address."""

    hash: str
    prompt: str


Describer = Callable[[sqlite3.Connection, dict[str, Any]], Bound]
PURPOSES: dict[str, Describer] = {}


def purpose(name: str) -> Callable[[Describer], Describer]:
    def register(fn: Describer) -> Describer:
        PURPOSES[name] = fn
        return fn

    return register


def digest(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


@purpose("test")
def _test(conn: sqlite3.Connection, target: dict[str, Any]) -> Bound:
    """`ecf stepup test`: checks that step-up works here; verifying it changes nothing."""
    del conn
    return Bound(digest("test", target.get("label", "")), "ecf: test step-up (changes nothing)")


@dataclass(frozen=True)
class Issued:
    nonce_id: str
    code: str
    prompt: str
    expires_at: str
    needs_password: bool


def person() -> str:
    """v1: the OS user running the service is the sole approver (§9)."""
    return getpass.getuser()


def issue(
    conn: sqlite3.Connection,
    clock: Clock,
    stepper: Stepper | None,
    name: str,
    target: dict[str, Any],
    *,
    ttl: timedelta = DEFAULT_TTL,
) -> Issued:
    bound = _describe(conn, name, target)
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
    nonce = new_nonce_id()
    now = clock.now()
    with write_tx(conn):
        conn.execute(
            "INSERT INTO nonces (nonce_id, purpose, bound_hash, person, created_at, expires_at,"
            " target, code) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                nonce,
                name,
                bound.hash,
                person(),
                to_ts(now),
                to_ts(now + ttl),
                json.dumps(target),  # in its order: a schema extension's order is its prompt's
                code,
            ),
        )
        _audit(conn, now, "stepup.requested", nonce, name)
    return Issued(
        nonce,
        code,
        _dialog(bound, code),
        to_ts(now + ttl),
        bool(stepper and stepper.needs_password),
    )


def verify(
    conn: sqlite3.Connection,
    clock: Clock,
    stepper: Stepper | None,
    nonce_id: str,
    *,
    password: str | None = None,
) -> str:
    """Run the OS check for a nonce; returns the Stepper's outcome. No transaction is held while
    the dialog is up (§11.2)."""
    row = _load(conn, nonce_id)
    now = clock.now()
    if row["consumed_at"] or row["verified_at"]:
        raise StepupFailedError("this step-up was already used")
    if from_ts(row["expires_at"]) <= now:
        raise StepupFailedError("this step-up has expired; ask again")
    bound = _describe(conn, row["purpose"], json.loads(row["target"]))
    if bound.hash != row["bound_hash"]:
        _refuse(conn, clock, nonce_id, row["purpose"], "target_changed")
        raise StepupFailedError("what this step-up was for has changed since; ask again")
    _count_try(conn, clock)
    if stepper is None:
        _refuse(conn, clock, nonce_id, row["purpose"], "unavailable")
        return "unavailable"
    outcome = stepper.verify(_dialog(bound, row["code"]), password=password)
    with write_tx(conn):
        conn.execute("UPDATE nonces SET attempts = attempts + 1 WHERE nonce_id = ?", (nonce_id,))
        if outcome == "verified":
            conn.execute(
                "UPDATE nonces SET verified_at = ? WHERE nonce_id = ? AND verified_at IS NULL",
                (to_ts(clock.now()), nonce_id),
            )
        event = "stepup.verified" if outcome == "verified" else "stepup.refused"
        _audit(conn, clock.now(), event, nonce_id, row["purpose"], outcome)
    return outcome


def consume(
    conn: sqlite3.Connection,
    clock: Clock,
    name: str,
    target: dict[str, Any],
    nonce_id: str | None,
) -> None:
    """Inside a protected action, before it acts: raises StepupRequiredError (with the purpose and
    target the client should step up for) unless `nonce_id` is verified for exactly this change."""
    bound = _describe(conn, name, target)
    if nonce_id is None:
        raise StepupRequiredError("this needs step-up", purpose=name, target=target)
    row = conn.execute("SELECT * FROM nonces WHERE nonce_id = ?", (nonce_id,)).fetchone()
    now = clock.now()
    ok = (
        row is not None
        and row["purpose"] == name
        and row["bound_hash"] == bound.hash
        and row["person"] == person()
        and row["verified_at"] is not None
        and row["consumed_at"] is None
        and now - from_ts(row["verified_at"]) <= VERIFIED_FOR
        and from_ts(row["expires_at"]) > now
    )
    if not ok:
        raise StepupRequiredError("step-up missing, expired or for something else",
                                  purpose=name, target=target)  # fmt: skip
    with write_tx(conn):
        done = conn.execute(
            "UPDATE nonces SET consumed_at = ? WHERE nonce_id = ? AND consumed_at IS NULL",
            (to_ts(now), nonce_id),
        ).rowcount
        if done != 1:
            raise StepupRequiredError("step-up already used", purpose=name, target=target)
        _audit(conn, now, "stepup.consumed", nonce_id, name)


def _describe(conn: sqlite3.Connection, name: str, target: dict[str, Any]) -> Bound:
    fn = PURPOSES.get(name)
    if fn is None:
        raise InvalidInputError(f"unknown step-up purpose {name!r}")
    return fn(conn, target)


def _dialog(bound: Bound, code: str) -> str:
    """The dialog text, cleaned: prompts carry email subjects and senders (`ecf.text`)."""
    return f"{one_line(bound.prompt)} (code {code})"


def _load(conn: sqlite3.Connection, nonce_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM nonces WHERE nonce_id = ?", (nonce_id,)).fetchone()
    if row is None:
        raise NotFoundError("no such step-up")
    return row


def _count_try(conn: sqlite3.Connection, clock: Clock) -> None:
    """At most MAX_TRIES per TRY_WINDOW, service-wide, so a same-user process can't lock the OS
    account through PAM's failure counter (§9.6)."""
    now = clock.now()
    with write_tx(conn):
        row = conn.execute("SELECT * FROM rate WHERE key = 'stepup.verify'").fetchone()
        if row is None or now - from_ts(row["window_start"]) >= TRY_WINDOW:
            conn.execute(
                "INSERT INTO rate (key, window_start, count) VALUES ('stepup.verify', ?, 1)"
                " ON CONFLICT (key) DO UPDATE SET window_start = excluded.window_start, count = 1",
                (to_ts(now),),
            )
            return
        if row["count"] >= MAX_TRIES:
            raise RateLimitedError("too many step-ups; wait a few minutes")
        conn.execute("UPDATE rate SET count = count + 1 WHERE key = 'stepup.verify'")


def _refuse(conn: sqlite3.Connection, clock: Clock, nonce_id: str, name: str, why: str) -> None:
    with write_tx(conn):
        _audit(conn, clock.now(), "stepup.refused", nonce_id, name, why)


def _audit(
    conn: sqlite3.Connection, now: Any, event: str, nonce_id: str, name: str, outcome: str = "ok"
) -> None:
    conn.execute(
        "INSERT INTO audit (ts, event, actor, outcome, data) VALUES (?, ?, 'os_user', ?, ?)",
        (
            to_ts(now),
            event,
            "ok" if outcome in ("ok", "verified") else "denied",
            json.dumps({"nonce_id": nonce_id, "purpose": name, "result": outcome}),
        ),
    )
