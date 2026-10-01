"""Local-model token and speed figures (SPEC §13.4; OD-116; V1.3 step 9): `ecf stats`, the daily
summary's line and each eval run's figures, all from Ollama's own counts (never content, I5).

- **Tokens:** input (including cached), cached and output, summed; tokens per email is every
  role's tokens divided by the emails classified (successful classifier calls).
- **Speeds** (tokens/s): prompt speed is uncached input tokens over the prompt duration (which
  covers uncached tokens only); generation speed is output tokens over the generation duration.
  Each is shown as the median and the slowest 5% (the 5th percentile), since a high percentile of
  a speed would show the fastest calls (as built 2026-10-01).
- **Times** (seconds): per call and model load, median and p95; a load over `COLD_LOAD_S` counts
  as a cold start (the model wasn't in memory).

Grouped by model (manifest digest) and role. Calls follow `log_retention_days` (model_calls is
pruned with the logs).
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ecf_server.clock import to_ts

COLD_LOAD_S = 1.0


@dataclass(frozen=True)
class Call:
    ok: bool
    prompt_tokens: int | None
    cached_tokens: int | None
    output_tokens: int | None
    prompt_ns: int | None
    eval_ns: int | None
    load_ns: int | None
    total_ns: int | None


def percentile(values: Sequence[float], p: float) -> float | None:
    """Nearest-rank percentile (p in 0-100) of a non-empty list, else None."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def _r(v: float | None, digits: int = 1) -> float | None:
    return None if v is None else round(v, digits)


def summarize(calls: Iterable[Call], *, emails: int | None = None) -> dict[str, Any]:
    """Figures for a group of calls; `emails` is the number of emails they served (default: the
    successful calls)."""
    cs = list(calls)
    ok = [c for c in cs if c.ok]
    tokens_in = sum(c.prompt_tokens or 0 for c in ok)
    cached = sum(c.cached_tokens or 0 for c in ok)
    tokens_out = sum(c.output_tokens or 0 for c in ok)
    prompt_tps = [_uncached(c) / (c.prompt_ns / 1e9) for c in ok if c.prompt_ns and _uncached(c)]
    gen_tps = [c.output_tokens / (c.eval_ns / 1e9) for c in ok if c.output_tokens and c.eval_ns]
    seconds = [c.total_ns / 1e9 for c in ok if c.total_ns]
    loads = [c.load_ns / 1e9 for c in ok if c.load_ns is not None]
    served = len(ok) if emails is None else emails
    return {
        "calls": len(cs),
        "failed": len(cs) - len(ok),
        "emails": served,
        "input_tokens": tokens_in,
        "cached_tokens": cached,
        "output_tokens": tokens_out,
        "tokens_per_email": _r((tokens_in + tokens_out) / served, 0) if served else None,
        "prompt_tps": _speed(prompt_tps),
        "generation_tps": _speed(gen_tps),
        "seconds": _time(seconds),
        "load_seconds": _time(loads) | {"cold_starts": sum(v > COLD_LOAD_S for v in loads)},
    }


def _uncached(c: Call) -> int:
    return max(0, (c.prompt_tokens or 0) - (c.cached_tokens or 0))


def _speed(values: Sequence[float]) -> dict[str, float | None]:
    return {"median": _r(percentile(values, 50)), "slowest_5": _r(percentile(values, 5))}


def _time(values: Sequence[float]) -> dict[str, float | None]:
    return {"median": _r(percentile(values, 50), 2), "p95": _r(percentile(values, 95), 2)}


def report(conn: sqlite3.Connection, since: datetime, *, address: str | None = None,
           preset: str | None = None) -> dict[str, Any]:  # fmt: skip
    """`ecf stats`: figures by model and role since `since`, and all roles together."""
    rows = conn.execute(
        "SELECT * FROM model_calls WHERE ts >= ? AND (? IS NULL OR address_id = ?)"
        " AND (? IS NULL OR preset = ?) ORDER BY digest, role",
        (to_ts(since), address, address, preset, preset),
    ).fetchall()
    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for r in rows:
        groups.setdefault((r["digest"], r["role"]), []).append(r)
    emails = sum(1 for r in rows if r["role"] == "classifier" and r["outcome"] == "ok")
    return {
        "since": to_ts(since),
        "groups": [{"model": d, "role": role} | summarize(map(_call, rs))
                   for (d, role), rs in groups.items()],
        "all": summarize(map(_call, rows), emails=emails),
    }  # fmt: skip


def _call(r: sqlite3.Row) -> Call:
    return Call(r["outcome"] == "ok", r["prompt_tokens"], r["cached_tokens"], r["output_tokens"],
                r["prompt_ns"], r["eval_ns"], r["load_ns"], r["total_ns"])  # fmt: skip


def daily_line(conn: sqlite3.Connection, since: datetime) -> str | None:
    """One line for the daily summary, or None when the model didn't run."""
    a = report(conn, since)["all"]
    if not a["calls"]:
        return None
    gen, sec = a["generation_tps"], a["seconds"]
    out = f"Local model: {a['calls']} calls for {a['emails']} email(s)"
    if a["failed"]:
        out += f" ({a['failed']} failed)"
    if a["tokens_per_email"] is not None:
        out += f", about {a['tokens_per_email']:.0f} tokens per email"
    if gen["median"] is not None:
        out += f", {gen['median']:.0f} tokens/s (slowest 5%: {gen['slowest_5']:.0f})"
    if sec["median"] is not None:
        out += f", {sec['median']:.1f} s per call (p95 {sec['p95']:.1f})"
    return out + " (ecf stats)"
