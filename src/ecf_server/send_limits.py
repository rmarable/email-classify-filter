"""The send circuit breaker per address (SPEC §8.4, §9.6; OD-059; V1.5 step 5).

Before an approved send runs (execute.run_once, before its grant is used), the address's sends of
the last hour and of the last day are counted from `sent`: template replies and forwards that went
or may have gone (`pending`, `sent`, `unknown`); alert email doesn't count (§13.3). When another
send would pass `max_sends_per_hour` (25) or `max_sends_per_day` (250), the breaker trips: the
address's sends stop (each job is held, its grant unused, so nothing is lost or retried away), and
`[ecf-alert] Operator Input Needed: send limit reached (<address>)` is raised on the next tick.

It stays tripped until `ecf outbound resume <address>` (step-up), even after the hour passes: a
burst of sends is worth a look. Resuming clears the breaker only; it never turns outbound back on
for an address where it was turned off. Changing a limit needs step-up and sends a Security
Notice (§9.6).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from ecf.errors import ConflictError, InvalidInputError
from ecf_server import addresses, health, stepup
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier

DEFAULTS = {"max_sends_per_hour": 25, "max_sends_per_day": 250}
BOUNDS = {"max_sends_per_hour": (1, 500), "max_sends_per_day": (1, 5000)}
WINDOWS = {"max_sends_per_hour": timedelta(hours=1), "max_sends_per_day": timedelta(days=1)}
ALERT = "send_limit"
RESUME = "outbound_resume"
SET = "send_limits_set"
HOLD_S = 300  # a held send looks again every 5 minutes


def limits(conn: sqlite3.Connection, address_id: str) -> dict[str, int]:
    row = conn.execute("SELECT overrides FROM addresses WHERE address_id = ?",
                       (address_id,)).fetchone()  # fmt: skip
    overrides: dict[str, Any] = json.loads(row["overrides"]) if row else {}
    return {k: int(overrides.get(k, v)) for k, v in DEFAULTS.items()}


def counts(conn: sqlite3.Connection, clock: Clock, address_id: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, window in WINDOWS.items():
        out[key] = int(conn.execute(
            "SELECT count(*) FROM sent WHERE address_id = ? AND kind IN ('reply', 'forward')"
            " AND status IN ('pending', 'sent', 'unknown') AND sent_at >= ?",
            (address_id, to_ts(clock.now() - window))).fetchone()[0])  # fmt: skip
    return out


def tripped(conn: sqlite3.Connection, address_id: str) -> bool:
    row = conn.execute("SELECT sends_tripped_at FROM addresses WHERE address_id = ?",
                       (address_id,)).fetchone()  # fmt: skip
    return row is not None and row["sends_tripped_at"] is not None


def allow(conn: sqlite3.Connection, clock: Clock, address_id: str,
          grant_id: str | None = None) -> bool:  # fmt: skip
    """May one more send go now? Trips the breaker when it may not. A retry of a send already
    recorded for this grant doesn't count against itself."""
    if tripped(conn, address_id):
        return False
    if grant_id and conn.execute("SELECT 1 FROM sent WHERE grant_id = ?", (grant_id,)).fetchone():
        return True
    lim, now = limits(conn, address_id), counts(conn, clock, address_id)
    over = [k for k in DEFAULTS if now[k] >= lim[k]]
    if not over:
        return True
    with write_tx(conn):
        conn.execute("UPDATE addresses SET sends_tripped_at = ? WHERE address_id = ?"
                     " AND sends_tripped_at IS NULL", (to_ts(clock.now()), address_id))  # fmt: skip
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, 'outbound.breaker_tripped', 'service', 'ok', ?)",
            (to_ts(clock.now()), address_id, json.dumps({"over": over, "counts": now})),
        )
    return False


def sweep(conn: sqlite3.Connection, clock: Clock, notifier: Notifier) -> None:
    """Each tick: Operator Input Needed while an address's breaker is tripped."""
    rows = conn.execute("SELECT address_id, sends_tripped_at FROM addresses"
                        " WHERE removed_at IS NULL").fetchall()  # fmt: skip
    for r in rows:
        aid = r["address_id"]
        if r["sends_tripped_at"]:
            health.open_alert(conn, clock, notifier, ALERT, aid,
                              f"{aid}: sends stopped at the send limit; they wait until you run"
                              f" `ecf outbound resume {aid}` (`ecf outbound report {aid}`"
                              " shows what went).")  # fmt: skip
        else:
            health.resolve_alert(conn, clock, notifier, ALERT, aid)


def resume(conn: sqlite3.Connection, clock: Clock, ref: str, *, actor: str,
           nonce: str | None) -> dict[str, Any]:  # fmt: skip
    a = addresses.get_address(conn, ref)
    aid = a["address_id"]
    if not tripped(conn, aid):
        raise ConflictError(f"{a['email']}'s sends aren't stopped by the send limit")
    stepup.consume(conn, clock, RESUME, {"address_id": aid}, nonce)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET sends_tripped_at = NULL WHERE address_id = ?", (aid,))
        conn.execute("INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
                     " VALUES (?, ?, 'outbound.resumed', ?, 'ok', '{}')",
                     (to_ts(clock.now()), aid, actor))  # fmt: skip
    return addresses.get_address(conn, aid) | {"counts": counts(conn, clock, aid),
                                               "limits": limits(conn, aid)}  # fmt: skip


def set_limits(
    conn: sqlite3.Connection,
    clock: Clock,
    announce: Callable[[str], None],
    ref: str,
    new: dict[str, int],
    *,
    actor: str,
    nonce: str | None,
) -> dict[str, Any]:
    a = addresses.get_address(conn, ref)
    aid = a["address_id"]
    for k, v in new.items():
        if k not in BOUNDS:
            raise InvalidInputError(f"no send limit {k!r}")
        lo, hi = BOUNDS[k]
        if not lo <= v <= hi:
            raise InvalidInputError(f"{k} must be {lo}-{hi}")
    stepup.consume(conn, clock, SET, {"address_id": aid, "limits": new}, nonce)
    row = conn.execute("SELECT overrides FROM addresses WHERE address_id = ?", (aid,)).fetchone()
    overrides: dict[str, Any] = json.loads(row["overrides"]) | new
    old = limits(conn, aid)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET overrides = ? WHERE address_id = ?",
                     (json.dumps(overrides, sort_keys=True), aid))  # fmt: skip
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, ?, 'settings.changed', ?, 'ok', ?)",
            (to_ts(clock.now()), aid, actor, json.dumps({"from": old, "to": new})),
        )
    changes = ", ".join(f"{k} {old[k]} -> {v}" for k, v in new.items())
    announce(f"Send limits for {a['email']} changed: {changes}.")
    return {"address_id": aid, "limits": limits(conn, aid)}


@stepup.purpose(RESUME)
def _describe_resume(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    a = addresses.get_address(conn, str(target.get("address_id", "")))
    row = conn.execute("SELECT sends_tripped_at FROM addresses WHERE address_id = ?",
                       (a["address_id"],)).fetchone()  # fmt: skip
    prompt = f"ecf: let {a['email']} send again after reaching its send limit"
    return stepup.Bound(stepup.digest(RESUME, a["address_id"], row["sends_tripped_at"]), prompt)


@stepup.purpose(SET)
def _describe_set(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    a = addresses.get_address(conn, str(target.get("address_id", "")))
    new: dict[str, Any] = target.get("limits") or {}
    text = ", ".join(f"{k.removeprefix('max_sends_')} {v}" for k, v in sorted(new.items()))
    return stepup.Bound(stepup.digest(SET, a["address_id"], new, limits(conn, a["address_id"])),
                        f"ecf: set {a['email']}'s send limits: {text}")  # fmt: skip
