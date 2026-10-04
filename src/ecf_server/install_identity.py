"""This install's permanent ID and restore generation (SPEC §8.4, §11.9; OD-318).

`install.id` is random, written once by migration 0025 and never changed; a restore of the same
install keeps it and raises `install.generation`. Every message ecf sends carries both, as
`X-ECF-Install: <id>.<generation>`, so mail from another running copy of this install (a restore
beside a computer that was never stopped) can be told apart from this copy's own.
"""

from __future__ import annotations

import json
import re
import sqlite3

from ecf.errors import InternalError

ID_KEY = "install.id"
GENERATION_KEY = "install.generation"
_HEADER = re.compile(r"^([0-9a-f]{32})\.(\d{1,9})$")


def install_id(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (ID_KEY,)).fetchone()
    if row is None:
        raise InternalError("this install has no ID (migration 0025 didn't run)")
    return str(json.loads(row["value"]))


def generation(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (GENERATION_KEY,)).fetchone()
    return int(json.loads(row["value"])) if row is not None else 0


def header_value(conn: sqlite3.Connection) -> str:
    return f"{install_id(conn)}.{generation(conn)}"


def parse_header(value: str) -> tuple[str, int] | None:
    """(install id, generation) from an `X-ECF-Install` value, or None if it isn't one of ours."""
    m = _HEADER.fullmatch(value.strip())
    return (m.group(1), int(m.group(2))) if m else None
