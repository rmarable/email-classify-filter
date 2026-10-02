"""Claude usage of `ecf claude` sessions (SPEC §13.4; V1.4 step 6): the `claude_calls` and
`claude_sessions` tables, `ecf stats`' Claude part, the daily-summary line and the plan usage
`ecf claude` shows at start.

From telemetry (exact per request and per model): calls, input, output and cache tokens and
duration, by model and source (`main` for the main session, `agent` for ecf's plugin agents, else
the raw `query_source`); `cost_usd` when Claude Code sends it, shown only as an API-equivalent
figure. Per email it is approximate: a session's tokens over the items it submitted work for. Plan
usage (status line, Pro and Max only) is recorded at a session's first and last reading. Both
tables follow `log_retention_days`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx
from ecf_server.stats import percentile
from ecf_server.telemetry import ApiCall, Tel


def source_of(query_source: str) -> str:
    if query_source in ("main", "sdk"):
        return "main"
    if query_source == "agent:custom":
        return "agent"
    return query_source


def start_session(conn: sqlite3.Connection, clock: Clock, session_id: str) -> None:
    with write_tx(conn):
        conn.execute("INSERT OR IGNORE INTO claude_sessions (session_id, started_at)"
                     " VALUES (?, ?)", (session_id, to_ts(clock.now())))  # fmt: skip


def record_calls(conn: sqlite3.Connection, clock: Clock, session_id: str,
                 calls: list[ApiCall]) -> None:  # fmt: skip
    if not calls:
        return
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.executemany(
            "INSERT INTO claude_calls (ts, session_id, model, source, input_tokens, output_tokens,"
            " cache_read_tokens, cache_creation_tokens, duration_ms, cost_usd)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(now, session_id, c.model, source_of(c.source), c.input_tokens, c.output_tokens,
              c.cache_read_tokens, c.cache_creation_tokens, c.duration_ms, c.cost_usd)
             for c in calls],
        )  # fmt: skip


def end_session(conn: sqlite3.Connection, clock: Clock, t: Tel) -> None:
    first, last = t.first_plan, t.last_plan
    with write_tx(conn):
        conn.execute(
            "UPDATE claude_sessions SET ended_at = ?, five_hour_start = ?, five_hour_end = ?,"
            " seven_day_start = ?, seven_day_end = ?, five_hour_resets = ?, seven_day_resets = ?,"
            " items = ?, submitted = ?, refused = ?, refused_model = ?, expected_model = ?"
            " WHERE session_id = ?",
            (to_ts(clock.now()),
             first.five_hour if first else None, last.five_hour if last else None,
             first.seven_day if first else None, last.seven_day if last else None,
             last.five_hour_resets if last else None, last.seven_day_resets if last else None,
             len(t.items), t.submitted, t.refused, t.refused_model, t.expected_model,
             t.session_id),
        )  # fmt: skip


def last_plan(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The plan usage at the end of the latest session that had a reading."""
    r = conn.execute(
        "SELECT ended_at, five_hour_end, seven_day_end, five_hour_resets, seven_day_resets"
        " FROM claude_sessions WHERE ended_at IS NOT NULL AND (five_hour_end IS NOT NULL"
        " OR seven_day_end IS NOT NULL) ORDER BY ended_at DESC LIMIT 1"
    ).fetchone()
    if r is None:
        return None
    return {"at": r["ended_at"], "five_hour": r["five_hour_end"], "seven_day": r["seven_day_end"],
            "five_hour_resets": r["five_hour_resets"],
            "seven_day_resets": r["seven_day_resets"]}  # fmt: skip


def last_review(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The latest ended session, for `ecf doctor`'s model-check line."""
    r = conn.execute("SELECT ended_at, items, submitted, refused, refused_model, expected_model"
                     " FROM claude_sessions WHERE ended_at IS NOT NULL"
                     " ORDER BY ended_at DESC LIMIT 1").fetchone()  # fmt: skip
    return dict(r) if r else None


def plan_text(p: dict[str, Any]) -> str:
    parts = [f"{label} {p[key]:.0f}%" for key, label in (("five_hour", "5-hour"),
                                                         ("seven_day", "7-day"))
             if p.get(key) is not None]  # fmt: skip
    return ", ".join(parts)


def report(conn: sqlite3.Connection, since: datetime) -> dict[str, Any]:
    """`ecf stats`' Claude part: by model and source since `since`, and the sessions."""
    cut = to_ts(since)
    rows = conn.execute("SELECT * FROM claude_calls WHERE ts >= ? ORDER BY model, source",
                        (cut,)).fetchall()  # fmt: skip
    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for r in rows:
        groups.setdefault((r["model"], r["source"]), []).append(r)
    sessions = conn.execute(
        "SELECT count(*) AS n, coalesce(sum(items), 0) AS items, coalesce(sum(refused), 0) AS"
        " refused FROM claude_sessions WHERE started_at >= ?", (cut,)).fetchone()  # fmt: skip
    total = _figures(rows)
    items = int(sessions["items"])
    tokens = total["input_tokens"] + total["output_tokens"]
    return {
        "groups": [{"model": m, "source": s} | _figures(rs) for (m, s), rs in groups.items()],
        "all": total,
        "sessions": int(sessions["n"]),
        "items": items,
        "refused": int(sessions["refused"]),
        "tokens_per_item": round(tokens / items) if items else None,
        "plan": last_plan(conn),
    }


def run_report(conn: sqlite3.Connection, sessions: list[str], since: datetime) -> dict[str, Any]:
    """A Claude eval run's figures (V1.4 step 7): its sessions' requests since it started, by
    model and source, and the plan usage at the sessions' first and last readings (ended
    sessions only: a live one has none recorded yet)."""
    if not sessions:
        return {"groups": [], "all": _figures([]), "plan": None}
    marks = ", ".join("?" * len(sessions))
    calls = f"SELECT * FROM claude_calls WHERE ts >= ? AND session_id IN ({marks})"  # noqa: S608 - placeholders only
    rows = conn.execute(calls + " ORDER BY model, source", (to_ts(since), *sessions)).fetchall()
    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for r in rows:
        groups.setdefault((r["model"], r["source"]), []).append(r)
    plans = (
        "SELECT five_hour_start, seven_day_start, five_hour_end, seven_day_end FROM"  # noqa: S608 - placeholders only
        f" claude_sessions WHERE session_id IN ({marks}) AND ended_at IS NOT NULL"
    )
    readings = conn.execute(plans + " ORDER BY started_at", tuple(sessions)).fetchall()
    plan = None
    if readings:
        first, last = readings[0], readings[-1]
        plan = {"five_hour_start": first["five_hour_start"],
                "seven_day_start": first["seven_day_start"],
                "five_hour_end": last["five_hour_end"],
                "seven_day_end": last["seven_day_end"]}  # fmt: skip
    return {"groups": [{"model": m, "source": s} | _figures(rs) for (m, s), rs in groups.items()],
            "all": _figures(rows), "plan": plan}  # fmt: skip


def _figures(rows: list[sqlite3.Row]) -> dict[str, Any]:
    secs = [r["duration_ms"] / 1000 for r in rows if r["duration_ms"] is not None]
    costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]

    def total(col: str) -> int:
        return sum(r[col] or 0 for r in rows)

    def rounded(v: float | None) -> float | None:
        return None if v is None else round(v, 2)

    return {
        "calls": len(rows),
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "cache_read_tokens": total("cache_read_tokens"),
        "cache_creation_tokens": total("cache_creation_tokens"),
        "seconds": {"median": rounded(percentile(secs, 50)), "p95": rounded(percentile(secs, 95))},
        "api_equivalent_usd": round(sum(costs), 4) if costs else None,
    }


def daily_line(conn: sqlite3.Connection, since: datetime) -> str | None:
    """One line for the daily summary, or None when no `ecf claude` session used Claude."""
    r = report(conn, since)
    a = r["all"]
    if not a["calls"]:
        return None
    out = (f"Claude: {r['sessions']} review session(s), {r['items']} email(s),"
           f" {a['input_tokens']} tokens in and {a['output_tokens']} out")  # fmt: skip
    if r["tokens_per_item"] is not None:
        out += f", about {r['tokens_per_item']} per email"
    if r["refused"]:
        out += f"; {r['refused']} refused by the model check"
    if r["plan"] and plan_text(r["plan"]):
        out += f"; plan used after the last review: {plan_text(r['plan'])}"
    return out + " (ecf stats)"
