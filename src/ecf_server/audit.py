"""The audit log (SPEC §15.2): the `audit` table, copied to append-only JSON-lines files.

Every event is written to the `audit` table in the same transaction as the change it records.
`flush` copies rows not yet copied to `<data>/audit/<address_id or _install>/YYYY/MM/DD.jsonl`
(the local form of the M1 `logs/` layout), in id order, and then advances a high-water mark in
`settings`; a crash between the two re-copies at most the rows of that flush, which carry their
row `id`, so a reader can drop the repeat. Folders are 0700, files 0600. Lines carry no email
content, only what the events record (§15.2). The daily retention job (V1.2) prunes both.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx

MARK_KEY = "audit.flushed_id"
BATCH = 1000
INSTALL_DIR = "_install"


def flush(conn: sqlite3.Connection, clock: Clock, audit_dir: Path, install: str) -> int:
    """Copy new audit rows to the files; returns how many were written."""
    mark = _mark(conn)
    written = 0
    while True:
        rows = conn.execute(
            "SELECT * FROM audit WHERE id > ? ORDER BY id LIMIT ?", (mark, BATCH)
        ).fetchall()
        if not rows:
            return written
        by_file: dict[Path, list[str]] = {}
        for r in rows:
            by_file.setdefault(_file_for(audit_dir, r), []).append(json.dumps(line(r, install)))
        for path, lines in by_file.items():
            _append(path, lines)
        mark = rows[-1]["id"]
        with write_tx(conn):
            conn.execute(
                "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at",
                (MARK_KEY, json.dumps(mark), to_ts(clock.now()), "service"),
            )
        written += len(rows)


def line(r: sqlite3.Row, install: str) -> dict[str, Any]:
    """One event as SPEC §15.2 lays it out."""
    out: dict[str, Any] = {"id": r["id"], "ts": r["ts"], "event": r["event"], "install": install}
    if r["address_id"]:
        out["address_id"] = r["address_id"]
    if r["stable_id"]:
        out["stable_id"] = r["stable_id"]
    return out | {"actor": r["actor"], "outcome": r["outcome"], "data": json.loads(r["data"])}


def query(
    conn: sqlite3.Connection,
    install: str,
    *,
    address_id: str | None = None,
    event_prefix: str | None = None,
    since: str | None = None,
    after_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """The newest `limit` matching events, oldest first (what `ecf logs` shows)."""
    where, args = ["1 = 1"], list[Any]()
    if address_id:
        where.append("address_id = ?")
        args.append(address_id)
    if event_prefix:
        where.append("substr(event, 1, length(?)) = ?")
        args += [event_prefix, event_prefix]
    if since:
        where.append("ts >= ?")
        args.append(since)
    if after_id is not None:
        where.append("id > ?")
        args.append(after_id)
    rows = conn.execute(
        f"SELECT * FROM audit WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",  # noqa: S608 - fixed clauses; values are parameters
        (*args, max(1, min(limit, 1000))),
    ).fetchall()
    return [line(r, install) for r in reversed(rows)]


def _mark(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (MARK_KEY,)).fetchone()
    return int(json.loads(row["value"])) if row else 0


def _file_for(audit_dir: Path, r: sqlite3.Row) -> Path:
    day = from_ts(r["ts"])
    who = r["address_id"] or INSTALL_DIR
    return audit_dir / who / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}.jsonl"


def _append(path: Path, lines: list[str]) -> None:
    for d in reversed([path.parent, *path.parent.parents]):
        if not d.exists():
            d.mkdir(mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())
