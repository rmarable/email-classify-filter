"""Retention (SPEC §6.5; V1.2 step 11a): the daily job and `ecf retention set`.

Once a day the service deletes, in batches of 1,000:

- terminal items last changed more than `log_retention_days` ago (default 90, range 1-3650), with
  their excerpts, grants, delays, escalation rows and Slack card records. Items that fired a
  fraud, weak fraud or regulator trigger, or were quarantined, are kept (OD-217);
- finished and dead jobs, and used or expired step-up nonces, older than the same age;
- records of one-off Slack posts (digests, daily summaries, stale lists, alerts, answers, notices)
  older than the same age. Item cards go with their item; "Needs you" and a burst card whose items
  remain are kept (V1.2 review, 2026-09-30).

Never pruned: the audit log (table and files, OD-217); senders, sent, threads, gate and eval
results (OD-040); open items (they are never auto-closed, §6.5).

`ecf retention set <days>` needs step-up (§9.6). Lowering it deletes history sooner, so it also
sends a Security Notice; `security_config_delay_minutes` is 0 in local mode (OD-074), so there is no
waiting window. The new value applies at the next daily run.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any

from ecf.errors import InvalidInputError
from ecf.status import TERMINAL
from ecf_server import slack_admin, slack_out, stepup
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import Notifier

KEY = "log_retention_days"
LAST_RUN_KEY = "retention.last_run"
DEFAULT_DAYS = 90
MIN_DAYS, MAX_DAYS = 1, 3650
BATCH = 1000
EVERY = timedelta(days=1)
_TERMINAL = tuple(sorted(s.value for s in TERMINAL))
# a fraud, weak fraud or regulator trigger fired, or the message was quarantined (OD-217)
# (a missing key gives NULL, and NOT NULL would keep every item: hence coalesce)
_KEPT = (
    "coalesce(json_array_length(facts, '$.triggers.fraud'), 0) > 0"
    " OR coalesce(json_array_length(facts, '$.triggers.fraud_weak'), 0) > 0"
    " OR coalesce(json_array_length(facts, '$.triggers.regulator'), 0) > 0"
    " OR coalesce(json_extract(facts, '$.quarantined'), 0) = 1"
)


def days(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (KEY,)).fetchone()
    return int(json.loads(row[0])) if row else DEFAULT_DAYS


def _put(conn: sqlite3.Connection, key: str, value: Any, now: str, actor: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (key, json.dumps(value), now, actor),
    )


# ---------------------------------------------------------------------------- setting


@stepup.purpose("retention_set")
def _describe(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    now, to = days(conn), target.get("days")
    if isinstance(to, bool) or not isinstance(to, int) or not MIN_DAYS <= to <= MAX_DAYS:
        raise InvalidInputError(f"days: a whole number from {MIN_DAYS} to {MAX_DAYS}")
    return stepup.Bound(stepup.digest("retention_set", now, to),
                        f"ecf: keep finished items {to} days (was {now})")  # fmt: skip


def set_days(
    conn: sqlite3.Connection,
    clock: Clock,
    notifier: Notifier,
    value: int,
    *,
    nonce: str | None,
    actor: str = "os_user",
) -> dict[str, Any]:
    if isinstance(value, bool) or not MIN_DAYS <= value <= MAX_DAYS:
        raise InvalidInputError(f"days: a whole number from {MIN_DAYS} to {MAX_DAYS}")
    was = days(conn)
    if value == was:
        return {"days": value, "was": was, "changed": False}
    stepup.consume(conn, clock, "retention_set", {"days": value}, nonce)
    now = to_ts(clock.now())
    with write_tx(conn):
        _put(conn, KEY, value, now, actor)
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, NULL, 'retention.changed', ?, 'ok', ?)",
            (now, actor, json.dumps({"from": was, "to": value})),
        )
    if value < was:  # history goes sooner: a Security Notice (§13.3)
        ident = slack_admin.identity(conn)
        slack_admin.notice(
            conn, clock, notifier,
            f"Retention lowered from {was} to {value} days: finished items older than that are"
            " deleted at the next daily run (fraud and regulator items and the audit log are"
            " kept).",
            dms=[ident.member] if ident and ident.member else [],
        )  # fmt: skip
    return {"days": value, "was": was, "changed": True}


# ---------------------------------------------------------------------------- the daily job


def due(conn: sqlite3.Connection, clock: Clock) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (LAST_RUN_KEY,)).fetchone()
    return row is None or clock.now() - from_ts(json.loads(row[0])) >= EVERY


def run(conn: sqlite3.Connection, clock: Clock) -> dict[str, int]:
    """Delete what is past retention; returns counts by kind."""
    cutoff = to_ts(clock.now() - timedelta(days=days(conn)))
    marks = ",".join("?" * len(_TERMINAL))
    counts = {"items": 0, "jobs": 0, "nonces": 0, "posts": 0, "model_calls": 0}
    while True:
        with write_tx(conn):
            ids = [r[0] for r in conn.execute(
                f"SELECT stable_id FROM items WHERE status IN ({marks}) AND updated_at < ?"  # noqa: S608 - placeholders only
                f" AND NOT ({_KEPT}) LIMIT ?", (*_TERMINAL, cutoff, BATCH))]  # fmt: skip
            if not ids:
                break
            q = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM slack_messages WHERE key IN ({q})",  # noqa: S608
                         [f"item:{i}" for i in ids])  # fmt: skip
            conn.execute(f"DELETE FROM items WHERE stable_id IN ({q})", ids)  # noqa: S608
            counts["items"] += len(ids)
    counts["jobs"] = _batched(conn, "DELETE FROM jobs WHERE rowid IN (SELECT rowid FROM jobs"
                              " WHERE state IN ('done', 'dead') AND created_at < ? LIMIT ?)",
                              (cutoff,))  # fmt: skip
    counts["nonces"] = _batched(conn, "DELETE FROM nonces WHERE rowid IN (SELECT rowid FROM"
                                " nonces WHERE (consumed_at IS NOT NULL OR expires_at < ?)"
                                " AND created_at < ? LIMIT ?)", (cutoff, cutoff))  # fmt: skip
    counts["posts"] = _batched(conn, "DELETE FROM slack_messages WHERE rowid IN (SELECT rowid FROM"
                               " slack_messages m WHERE updated_at < ? AND key NOT LIKE 'item:%'"
                               " AND key NOT IN (SELECT value FROM json_each(?))"
                               " AND NOT (key LIKE 'burst:%' AND EXISTS (SELECT 1 FROM items i"
                               " WHERE m.key LIKE 'burst:%:' || i.stable_id)) LIMIT ?)",
                               (cutoff, json.dumps(sorted(slack_out.PINNED))))  # fmt: skip
    counts["model_calls"] = _batched(
        conn, "DELETE FROM model_calls WHERE rowid IN (SELECT rowid FROM model_calls"
        " WHERE ts < ? LIMIT ?)", (cutoff,))  # fmt: skip
    now = to_ts(clock.now())
    with write_tx(conn):
        _put(conn, LAST_RUN_KEY, now, now, "service")
        conn.execute(
            "INSERT INTO audit (ts, address_id, event, actor, outcome, data)"
            " VALUES (?, NULL, 'retention.run', 'service', 'ok', ?)",
            (now, json.dumps(counts | {"days": days(conn)})),
        )
    return counts


def _batched(conn: sqlite3.Connection, sql: str, args: tuple[str, ...]) -> int:
    """Run a `DELETE ... LIMIT ?` until it deletes less than a batch."""
    total = 0
    while True:
        with write_tx(conn):
            n = conn.execute(sql, (*args, BATCH)).rowcount
        total += n
        if n < BATCH:
            return total
