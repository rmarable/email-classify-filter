"""SQLite access (SPEC §11.2).

Connections run in autocommit mode; every write goes through `write_tx` (BEGIN IMMEDIATE), and
no transaction is ever held across a network or model call. One connection per long-lived thread,
one per request for HTTP routes.
"""

from __future__ import annotations

import contextvars
import os
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Literal

from ecf.errors import ServiceUnavailableError

MIN_SQLITE = (3, 37, 0)  # STRICT tables (3.37); RETURNING needs 3.35
BUSY_TIMEOUT_MS = 5000


# The single-writer rule for items (SPEC §6.2), enforced by SQLite's authorizer at statement
# compile time: only `items.create_item` may insert into `items`, and only `items.transition` may
# write `items.status`. This catches every SQL form (UPDATE OR IGNORE, REPLACE, upserts, ...).
_ITEMS_WRITER: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ecf_items_writer", default=None
)


@contextmanager
def items_writer(kind: Literal["create", "transition"]) -> Generator[None]:
    token = _ITEMS_WRITER.set(kind)
    try:
        yield
    finally:
        _ITEMS_WRITER.reset(token)


def _authorizer(action: int, arg1: str | None, arg2: str | None, *_: str | None) -> int:
    scope = _ITEMS_WRITER.get()
    if action == sqlite3.SQLITE_UPDATE and arg1 == "items" and arg2 == "status":
        return sqlite3.SQLITE_OK if scope == "transition" else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_INSERT and arg1 == "items":
        return sqlite3.SQLITE_OK if scope == "create" else sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def sqlite_version_ok() -> bool:
    return sqlite3.sqlite_version_info >= MIN_SQLITE


def connect(path: Path) -> sqlite3.Connection:
    if not sqlite_version_ok():
        raise ServiceUnavailableError(
            f"SQLite {sqlite3.sqlite_version} is too old; ecf needs "
            f"{'.'.join(map(str, MIN_SQLITE))} or newer"
        )
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    old_umask = os.umask(0o077)
    try:
        # cached_statements=0: every statement is compiled, so the authorizer always runs
        conn = sqlite3.connect(
            path, autocommit=True, timeout=BUSY_TIMEOUT_MS / 1000, cached_statements=0
        )
    finally:
        os.umask(old_umask)
    conn.row_factory = sqlite3.Row
    conn.set_authorizer(_authorizer)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    for suffix in ("", "-wal", "-shm"):
        p = Path(f"{path}{suffix}")
        if p.exists():
            p.chmod(0o600)
    return conn


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Generator[sqlite3.Connection]:
    """One write transaction. Take the write lock up front so readers never upgrade mid-way."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        # SQLite may already have rolled back (e.g. SQLITE_FULL); a failed COMMIT leaves the
        # transaction open. Either way, never leave the connection inside a transaction.
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


class ThreadConnections:
    """A connection per long-lived thread (`threading.local`)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._local = threading.local()

    def get(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = connect(self._path)
            self._local.conn = conn
        return conn


def _migration_files() -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []
    for f in resources.files("ecf_server.migrations").iterdir():
        name = f.name
        if name.endswith(".sql") and name[:4].isdigit():
            out.append((int(name[:4]), name, f.read_text(encoding="utf-8")))
    return sorted(out)


def migrate(conn: sqlite3.Connection, up_to: int | None = None) -> list[str]:
    """Apply pending migrations in order, each in its own write transaction; return their names.
    `up_to` stops after that version (import builds an older bundle's schema first, OD-355)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL) STRICT"
    )
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied: list[str] = []
    for version, name, sql in _migration_files():
        if version in done:
            continue
        if up_to is not None and version > up_to:
            break
        with write_tx(conn):
            # another migrator may have applied it since we looked
            again = "SELECT 1 FROM schema_migrations WHERE version = ?"
            if conn.execute(again, (version,)).fetchone():
                continue
            for statement in _split(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations VALUES (?, ?, ?)",
                (version, name, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")),
            )
        applied.append(name)
    return applied


def _split(sql: str) -> list[str]:
    """Split a migration into statements (no semicolons inside our SQL literals)."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
