"""SQLite access (SPEC §11.2).

Connections run in autocommit mode; every write goes through `write_tx` (BEGIN IMMEDIATE), and
no transaction is ever held across a network or model call. One connection per long-lived thread,
one per request for HTTP routes.
"""

from __future__ import annotations

import contextvars
import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager, suppress
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


def _migration_files() -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []
    for f in resources.files("ecf_server.migrations").iterdir():
        name = f.name
        if name.endswith(".sql") and name[:4].isdigit():
            out.append((int(name[:4]), name, f.read_text(encoding="utf-8")))
    return sorted(out)


# Migrations that rewrite many rows, after which a WAL checkpoint runs (0036 schema v3, 0037 its
# removed values, 0038 the local models' columns and the `addresses` rebuild; P27; 0039 builds two
# indexes over the whole audit table, OD-507)
CHECKPOINT_AFTER = frozenset({36, 37, 38, 39})
# Migrations that rebuild a table other tables reference (0038 `addresses`; P1), with that table:
# SQLite ignores `PRAGMA foreign_keys` inside a transaction, and with enforcement on, dropping the
# old table would delete or refuse its children. So enforcement goes off before the transaction,
# `PRAGMA foreign_key_check` on the rebuilt table's children (only those; review R4) must find no
# row pointing at it before it commits (a row found rolls it all back, and the error names the
# first few by table and rowid), and the connection's setting comes back afterwards, whatever
# happened (best effort; review R10).
NO_FK: dict[int, str] = {38: "addresses"}
ORPHANS_NAMED = 5  # rows a failed check names
# Columns kept in the schema though nothing reads or writes them, until a later major version
# drops them (v4): dropping a column rewrites the whole table (review R1). Export leaves them out
# and import doesn't copy them.
UNUSED_COLUMNS: dict[str, tuple[str, ...]] = {
    "items": ("fallback_at", "model_attempts", "model_failed", "model_failed_at"),  # 0038
}


class MigrationError(sqlite3.IntegrityError):
    """A migration would leave the database inconsistent; its transaction was rolled back (an
    IntegrityError, so import and restore report it as rows that don't load)."""


def migrate(conn: sqlite3.Connection, up_to: int | None = None) -> list[str]:
    """Apply pending migrations in order, each in its own write transaction; return their names.
    `up_to` stops after that version (import builds an older bundle's schema first, OD-355)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL) STRICT"
    )
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied: list[str] = []
    ran: set[int] = set()
    for version, name, sql in _migration_files():
        if version in done:
            continue
        if up_to is not None and version > up_to:
            break
        if not _apply(conn, version, name, sql):
            continue
        applied.append(name)
        ran.add(version)
    if ran & CHECKPOINT_AFTER and not conn.in_transaction:
        # a rewrite of every stored classification can leave a large WAL: fold it back now,
        # without waiting on readers (PASSIVE; schema doc §9, P13)
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
    return applied


def _apply(conn: sqlite3.Connection, version: int, name: str, sql: str) -> bool:
    """One migration in its own write transaction; False if another migrator applied it first."""
    parent = NO_FK.get(version)
    was = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    began = False
    try:
        if parent is not None:
            conn.execute("PRAGMA foreign_keys=OFF")
            if conn.execute("PRAGMA foreign_keys").fetchone()[0]:  # inside a caller's transaction
                raise MigrationError(f"migration {name} needs foreign keys off, and they can't"
                                     " be turned off inside a transaction")  # fmt: skip
        began = True
        with write_tx(conn):
            # another migrator may have applied it since we looked
            again = "SELECT 1 FROM schema_migrations WHERE version = ?"
            if conn.execute(again, (version,)).fetchone():
                return False
            for statement in _split(sql):
                conn.execute(statement)
            if parent is not None and (found := orphans(conn, parent)):
                named = ", ".join(f"{t} rowid {r} -> {p}" for t, r, p in found[:ORPHANS_NAMED])
                raise MigrationError(
                    f"migration {name} would leave rows whose parent is missing ({named}"
                    f"{', ...' if len(found) > ORPHANS_NAMED else ''}); nothing was changed, and"
                    " ecf upgrade puts the previous version back. Report it with these names"
                    " (table and row only); don't delete rows without a copy of the database"
                )
            conn.execute(
                "INSERT INTO schema_migrations VALUES (?, ?, ?)",
                (version, name, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")),
            )
        return True
    finally:
        if parent is not None:  # best effort, whatever happened above (review R10)
            with suppress(sqlite3.Error):
                if began and conn.in_transaction:  # our own, left open by a failed ROLLBACK
                    conn.execute("ROLLBACK")
            with suppress(sqlite3.Error):
                conn.execute(f"PRAGMA foreign_keys={'ON' if was else 'OFF'}")


def orphans(conn: sqlite3.Connection, parent: str) -> list[tuple[str, int | None, str]]:
    """Rows of `parent`'s child tables whose `parent` row is missing: (table, rowid, parent),
    from `PRAGMA foreign_key_check` on each child only, not the whole database (review R4)."""
    children = [r[0] for r in conn.execute(
        "SELECT DISTINCT m.name FROM sqlite_master m, pragma_foreign_key_list(m.name) f"
        " WHERE m.type = 'table' AND f.\"table\" = ? ORDER BY m.name", (parent,))]  # fmt: skip
    out: list[tuple[str, int | None, str]] = []
    for child in children:
        for r in conn.execute(f'PRAGMA foreign_key_check("{child}")'):
            if r[2] == parent:
                out.append((str(r[0]), r[1], str(r[2])))
    return out


def _split(sql: str) -> list[str]:
    """Split a migration into statements (no semicolons inside our SQL literals)."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
