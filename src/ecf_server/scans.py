"""Runs of `ecf backfill` and `ecf reprocess`, and each run's one Slack summary (OD-507, plan
v3.0.0-STEP5 §3.4, §4.2; D8, P10).

A run is a `scan_run.<run id>` setting: kind (`scan` or `reprocess`), address, range, mode, when
it started and finished, and what its summary has posted. Its items carry the marker
(`marked.py`), so no item of a run gets a post or card of its own; instead, in the summary
channel:

- **At run start:** the head post (address, range, mode).
- **As found:** fraud and regulator items, and items whose plan escalates, as thread replies, one
  line each in the burst-card style, up to `REPLY_LINES` per reply (then a new reply); a reply
  with a fraud line mentions you (SPEC §5.4). A records-only scan's say "found in old mail, no
  action taken".
- **Until the run settles:** the head is edited in place with fetched, recorded, classified,
  waiting for `/ecf-review`, approvals pending (`ecf approve --scan <id>`), fraud and regulator
  counts, and any lines the run adds (reprocess). It settles when the run has finished and none
  of its items waits for `/ecf-review`, so a summary after `--stop` isn't stale.

The records are here; the Slack summary is `scan_summary.py` (its `tick` runs in the Slack thread's
housekeeping pass, every half minute). Settled runs are forgotten after `log_retention_days`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ecf.ids import new_random_id
from ecf_server import marked
from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx

PREFIX = "scan_run."
REPLY_LINES = 20  # per thread reply (plan §3.4, §14.3)
WAITING = ("awaiting_claude", "clarified")
APPROVALS = ("awaiting_approval", "awaiting_stepup", "expired")  # expired: re-offered (M4)


def new_run(conn: sqlite3.Connection, clock: Clock, *, kind: str, address_id: str, since: str,
            until: str | None, mode: str) -> str:  # fmt: skip
    """Record a run (inside the caller's write transaction); returns its id."""
    run_id = new_random_id()[:12]
    email = conn.execute("SELECT email FROM addresses WHERE address_id = ?",
                         (address_id,)).fetchone()  # fmt: skip
    doc: dict[str, Any] = {"run_id": run_id, "kind": kind, "address_id": address_id,
           "email": email["email"] if email else address_id, "since": since, "until": until,
           "mode": mode, "started_at": to_ts(clock.now()), "finished_at": None, "outcome": None,
           "reported": [], "replies": 0, "reply_lines": [], "lines": [], "head": "",
           "settled": False}  # fmt: skip
    put(conn, clock, doc)
    return run_id


def load(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (PREFIX + run_id,)).fetchone()
    return json.loads(row[0]) if row else None


def find(conn: sqlite3.Connection, ref: str) -> dict[str, Any] | None:
    """A run by its id or a unique prefix of it."""
    rows = [json.loads(r[0]) for r in conn.execute(
        "SELECT value FROM settings WHERE key LIKE ? ORDER BY key",
        (PREFIX + ref.replace("%", "").replace("_", "") + "%",))]  # fmt: skip
    rows = [r for r in rows if str(r["run_id"]).startswith(ref)]
    return rows[0] if len(rows) == 1 else None


def runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT value FROM settings WHERE key LIKE 'scan\\_run.%' ESCAPE '\\'"
                        " ORDER BY key").fetchall()  # fmt: skip
    return [json.loads(r[0]) for r in rows]


def finish(conn: sqlite3.Connection, clock: Clock, run_id: str, outcome: str,
           lines: list[str] | None = None) -> None:  # fmt: skip
    """The run's work is over (done, stopped, mailbox reset); its summary keeps updating until
    its items settle. `lines` are added to the head (reprocess's "plan now differs")."""

    def change(doc: dict[str, Any]) -> None:
        doc |= {"finished_at": to_ts(clock.now()), "outcome": outcome}
        if lines:
            doc["lines"] = [*doc["lines"], *lines]

    update(conn, clock, run_id, change)


def note(conn: sqlite3.Connection, clock: Clock, run_id: str, lines: list[str]) -> None:
    """Replace the run's own head lines (a running reprocess's progress)."""
    update(conn, clock, run_id, lambda doc: doc.update({"lines": lines}))


def update(conn: sqlite3.Connection, clock: Clock, run_id: str,
           change: Callable[[dict[str, Any]], object]) -> None:  # fmt: skip
    """Read, change and write a run in one write transaction: the checks, the timer and the
    Slack thread each change their own fields of it (review M3)."""
    with write_tx(conn):
        doc = load(conn, run_id)
        if doc is not None:
            change(doc)
            put(conn, clock, doc)


def put(conn: sqlite3.Connection, clock: Clock, doc: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, 'service')"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (PREFIX + doc["run_id"], json.dumps(doc), to_ts(clock.now())),
    )


OF_RUN = "(json_extract(facts, '$.scan_run') = :run OR json_extract(facts, '$.reprocess') = :run)"


def counts(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    """The head's figures for one run, from its items."""
    row = conn.execute(
        "SELECT count(*) AS fetched,"  # noqa: S608 - fixed terms
        " coalesce(sum(status = 'observed'), 0) AS recorded,"
        " coalesce(sum(classification IS NOT NULL), 0) AS classified,"
        " coalesce(sum(status IN ('awaiting_claude', 'clarified')), 0) AS waiting,"
        " coalesce(sum(status IN ('awaiting_approval', 'awaiting_stepup') OR (status = 'expired'"
        " AND coalesce(json_array_length(proposal, '$.actions'), 0) > 0)), 0) AS approvals,"
        " coalesce(sum(coalesce(json_array_length(facts, '$.triggers.fraud'), 0) > 0), 0)"
        " AS fraud,"
        " coalesce(sum(coalesce(json_array_length(facts, '$.triggers.regulator'), 0) > 0), 0)"
        f" AS regulator FROM items WHERE {OF_RUN}",
        {"run": run_id},
    ).fetchone()
    return {k: int(row[k]) for k in row.keys()}  # noqa: SIM118 - sqlite3.Row


def waiting_line(conn: sqlite3.Connection) -> str | None:
    """The reminder's and daily summary's separate line (plan §2), or None."""
    row = conn.execute(
        "SELECT count(*) FROM items WHERE status IN ('awaiting_claude', 'clarified') AND "  # noqa: S608
        + marked.sql()).fetchone()  # fmt: skip
    n = int(row[0])
    if not n:
        return None
    return (f"{n} scan/reprocess item(s) waiting for /ecf-review; they don't count toward the"
            " reminders, and new mail is reviewed first")  # fmt: skip


def inbox_lines(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> list[str]:
    """One count line per run for "Needs you" and `ecf inbox` (plan §2, P9), from the marked
    items `inbox.inbox` set aside (`rows`, with `run` and `status`)."""
    by_run: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_run.setdefault(str(r.get("run") or "-"), []).append(r)
    out: list[str] = []
    for run_id, rs in sorted(by_run.items()):
        doc = load(conn, run_id) if run_id != "-" else None
        what = "Reprocess" if doc and doc["kind"] == "reprocess" else "Scan"
        where = doc["email"] if doc else rs[0]["address_id"]
        n_appr = sum(1 for r in rs if r["status"] in APPROVALS)
        line = f"{what} of {where}: {len(rs)} waiting"
        if n_appr and doc:
            line += f", {n_appr} approval(s): ecf approve --scan {run_id}"
        elif n_appr:
            line += f", {n_appr} approval(s): ecf approve <id>"
        out.append(line)
    return out
