"""`ecf upgrade --to <older version>` after the upgrade settled: the merged database (SPEC §11.10;
OD-107, OD-331, OD-382; V1.5 step 11c).

Run by the *current* `ecf-server downgrade-prepare --label <older>-to-<current>` while the service
is stopped (it takes the instance lock). It copies the upgrade's snapshot to `ecf.rollback.db`
beside it and changes that copy only:

- `sent`, `threads`, `senders` and `gate` come from the live database (their columns common to
  both versions, for addresses the snapshot has), so send history, reply limits, sender records
  and gate history aren't lost (OD-107);
- the snapshot's cursors stay as they were, so mail since the upgrade is fetched again and its
  items deduplicated by `stable_id`;
- open grants are voided; open approvals go to `expired` and approved, delayed and executing
  items to `failed_unknown` (items.map_downgraded); claims, leases, in-progress marks and queued
  actions are cleared, so nothing runs twice (OD-331);
- every address is paused and `live` becomes `assist`.

`ecf upgrade --to` then puts the copy in place of the database and reinstalls the older wheel.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ecf.errors import InvalidInputError
from ecf.paths import Paths
from ecf_server import items, upgrade_snapshot
from ecf_server.db import write_tx

KEPT = ("sent", "threads", "senders", "gate")
CLEARED = ("claim_batches", "claims", "leases", "processing", "delays")
ROLLBACK_DB = "ecf.rollback.db"


def prepare(paths: Paths, label: str) -> Path:
    from ecf_server import db  # noqa: PLC0415
    from ecf_server.service import acquire_lock  # noqa: PLC0415

    folder = upgrade_snapshot.folder(paths) / label
    snapshot = folder / "ecf.db"
    if not snapshot.is_file():
        raise InvalidInputError(f"no database copy for {label}")
    lock = acquire_lock(paths)  # refuses while the service runs
    try:
        out = folder / ROLLBACK_DB
        out.unlink(missing_ok=True)
        src = sqlite3.connect(snapshot)
        dst = sqlite3.connect(out)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        out.chmod(0o600)
        conn = db.connect(out)
        try:
            _merge(conn, paths.db)
        finally:
            conn.close()
        return out
    finally:
        lock.close()


def _merge(conn: sqlite3.Connection, live: Path) -> None:
    conn.execute("ATTACH DATABASE ? AS cur", (str(live),))
    try:
        have = _tables(conn, "main")
        with write_tx(conn):
            for t in KEPT:
                if t not in have or t not in _tables(conn, "cur"):
                    continue
                cols = [c for c in _columns(conn, "main", t) if c in set(_columns(conn, "cur", t))]
                names = ", ".join(f"[{c}]" for c in cols)
                conn.execute(f'DELETE FROM main."{t}"')  # noqa: S608 - fixed names
                where = (" WHERE address_id IN (SELECT address_id FROM main.addresses)"
                         if "address_id" in cols else "")  # fmt: skip
                conn.execute(f'INSERT OR REPLACE INTO main."{t}" ({names}) SELECT {names}'  # noqa: S608
                             f' FROM cur."{t}"{where}')  # fmt: skip
            if "grants" in have:
                conn.execute("UPDATE main.grants SET status = 'voided' WHERE status IN"
                             " ('issued', 'approved')")  # fmt: skip
            for t in CLEARED:
                if t in have:
                    conn.execute(f'DELETE FROM main."{t}"')  # noqa: S608 - fixed names
            if "jobs" in have:
                conn.execute("DELETE FROM main.jobs WHERE queue = 'actions'")
            conn.execute("UPDATE main.addresses SET paused = 1, stage = CASE WHEN stage = 'live'"
                         " THEN 'assist' ELSE stage END")  # fmt: skip
    finally:
        conn.execute("DETACH DATABASE cur")
    items.map_downgraded(conn)


def _tables(conn: sqlite3.Connection, schema: str) -> set[str]:
    return {r[0] for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master"  # noqa: S608
                                       " WHERE type = 'table'")}  # fmt: skip


def _columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA {schema}.table_info("{table}")')]
