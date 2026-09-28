"""SQLite access (SPEC §11.2).

Connections run in autocommit mode; every write goes through `write_tx` (BEGIN IMMEDIATE), and
no transaction is ever held across a network or model call. One connection per long-lived thread,
one per request for HTTP routes.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from ecf.errors import ServiceUnavailableError

MIN_SQLITE = (3, 37, 0)  # STRICT tables (3.37); RETURNING needs 3.35
BUSY_TIMEOUT_MS = 5000


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
        conn = sqlite3.connect(path, autocommit=True, timeout=BUSY_TIMEOUT_MS / 1000)
    finally:
        os.umask(old_umask)
    conn.row_factory = sqlite3.Row
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
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


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


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations in order, each in its own write transaction; return their names."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL) STRICT"
    )
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied: list[str] = []
    for version, name, sql in _migration_files():
        if version in done:
            continue
        with write_tx(conn):
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
