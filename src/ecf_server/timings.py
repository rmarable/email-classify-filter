"""Stage and Slack timings for `ecf stats` (SPEC §13.4; OD-507 §5, capture points a, b, e).

- **Stages (a), derived from the audit log:** each `item.transitioned` row is one stage, named by
  its `(from, to)`, lasting from the item's previous `item.created` or `item.transitioned` row (by
  audit id) to it. Median, p95 and n per stage.
- **Claude queue wait (b):** from an item's first entry to `awaiting_claude` to its first
  `classified` after it.
- Items a scan or a reprocess marked (`facts.backfill`, `facts.reprocess`) are reported as their own
  group; imported items (`items.origin`, whose rows were written on another computer) are left out
  and counted. An item retention has deleted counts as unmarked.
- **Slack (e):** from `slack_calls`, one row per post job: queue wait (queued to done), time in
  Slack calls, tries beyond the first, rate-limit holds and pacing.

Counts and times only, never content.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from ecf_server.clock import from_ts, to_ts
from ecf_server.stats import percentile

ARROW = "→"


def _figures(values: list[float]) -> dict[str, Any]:
    return {"n": len(values), "median_s": _r(percentile(values, 50)),
            "p95_s": _r(percentile(values, 95))}  # fmt: skip


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 3)


def _marked(conn: sqlite3.Connection) -> tuple[set[str], set[str]]:
    marked = {r[0] for r in conn.execute(
        "SELECT stable_id FROM items WHERE json_extract(facts, '$.backfill') = 1"
        " OR json_extract(facts, '$.reprocess') IS NOT NULL")}  # fmt: skip
    imported = {r[0] for r in conn.execute("SELECT stable_id FROM items WHERE origin IS NOT NULL")}
    return marked, imported


def stages(conn: sqlite3.Connection, since: datetime) -> dict[str, Any]:
    """`ecf stats --stages` (module notes): rows from `since` on."""
    marked, imported = _marked(conn)
    rows = conn.execute(
        "SELECT stable_id, ts, event, json_extract(data, '$.from') AS frm,"
        " json_extract(data, '$.to') AS dest FROM audit"
        " WHERE event IN ('item.created', 'item.transitioned') AND ts >= ? AND origin IS NULL"
        " ORDER BY stable_id, id", (to_ts(since),))  # fmt: skip
    groups: dict[bool, dict[str, list[float]]] = {False: {}, True: {}}
    waits: dict[bool, list[float]] = {False: [], True: []}
    items: dict[bool, set[str]] = {False: set(), True: set()}
    skipped: set[str] = set()
    for sid, seq in _by_item(rows):
        if sid in imported:
            skipped.add(sid)
            continue
        group = sid in marked
        items[group].add(sid)
        prev: datetime | None = None
        entered: datetime | None = None
        waited = False
        for ts, event, frm, dest in seq:
            at = from_ts(ts)
            if event == "item.transitioned":
                if prev is not None:
                    name = f"{frm} {ARROW} {dest}"
                    groups[group].setdefault(name, []).append((at - prev).total_seconds())
                if dest == "awaiting_claude" and entered is None:
                    entered = at
                elif dest == "classified" and entered is not None and not waited:
                    waits[group].append((at - entered).total_seconds())
                    waited = True
            prev = at
    out: dict[str, Any] = {"since": to_ts(since), "imported_left_out": len(skipped)}
    for group, key in ((False, "new_mail"), (True, "scan_or_reprocess")):
        out[key] = {
            "items": len(items[group]),
            "stages": [{"stage": name} | _figures(v) for name, v in sorted(groups[group].items())],
            "claude_wait": _figures(waits[group]),
        }
    return out


def _by_item(
    rows: Iterable[sqlite3.Row],
) -> Iterable[tuple[str, list[tuple[str, str, str | None, str | None]]]]:
    sid: str | None = None
    seq: list[tuple[str, str, str | None, str | None]] = []
    for r in rows:
        if r["stable_id"] != sid:
            if sid is not None:
                yield sid, seq
            sid, seq = r["stable_id"], []
        seq.append((r["ts"], r["event"], r["frm"], r["dest"]))
    if sid is not None:
        yield sid, seq


def slack(conn: sqlite3.Connection, since: datetime) -> dict[str, Any]:
    """The Slack block of `ecf stats` (module notes)."""
    rows = conn.execute("SELECT * FROM slack_calls WHERE ts >= ?", (to_ts(since),)).fetchall()
    waits = [(from_ts(r["ts"]) - from_ts(r["queued_at"])).total_seconds() for r in rows]
    holds = [r["rate_hold_s"] for r in rows if r["rate_hold_s"]]
    outcomes: dict[str, int] = {}
    for r in rows:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    return {
        "posts": len(rows),
        "outcomes": outcomes,
        "wait": _figures(waits),
        "call": _figures([r["call_ms"] / 1000 for r in rows]),
        "retries": sum(max(r["attempts"] - 1, 0) for r in rows),
        "rate_limited": len(holds),
        "rate_hold_s": sum(holds),
        "pace_s": round(sum(r["pace_ms"] for r in rows) / 1000, 1),
    }
