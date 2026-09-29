"""Check leases (SPEC §5.1 step 1, §6.4): one holder per address, 3 minutes, renewed every 60 s.

A lease carries a fencing token that grows with every new acquisition; writes that depend on
holding the lease (the cursor) check the token, so a holder that stalled past expiry (a sleeping
laptop, a hung call) can't write after someone else took over. Expiry uses wall-clock time
(§5.5). A per-address `threading.Lock` gives in-process exclusion on top (§5.1).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from ecf_server.clock import Clock, to_ts
from ecf_server.db import write_tx

LEASE_S = 180  # OD-023
RENEW_S = 60

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def local_lock(address_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(address_id, threading.Lock())


@dataclass(frozen=True)
class Lease:
    address_id: str
    holder: str
    token: int


def acquire(
    conn: sqlite3.Connection, clock: Clock, address_id: str, holder: str, *, ttl_s: int = LEASE_S
) -> Lease | None:
    """Take the lease if it is free or expired; None if someone else holds it."""
    now = clock.now()
    with write_tx(conn):
        row = conn.execute(
            "SELECT holder, fencing_token, expires_at FROM leases WHERE address_id = ?",
            (address_id,),
        ).fetchone()
        if row is not None and row["expires_at"] > to_ts(now) and row["holder"] != holder:
            return None
        token = (row["fencing_token"] if row else 0) + 1
        conn.execute(
            "INSERT INTO leases (address_id, holder, fencing_token, expires_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (address_id) DO UPDATE SET holder = excluded.holder,"
            " fencing_token = excluded.fencing_token, expires_at = excluded.expires_at",
            (address_id, holder, token, to_ts(now + timedelta(seconds=ttl_s))),
        )
    return Lease(address_id, holder, token)


def renew(conn: sqlite3.Connection, clock: Clock, lease: Lease, *, ttl_s: int = LEASE_S) -> bool:
    """Extend the lease; False if it was lost (expired and taken, or released)."""
    now = clock.now()
    with write_tx(conn):
        cur = conn.execute(
            "UPDATE leases SET expires_at = ? WHERE address_id = ? AND holder = ?"
            " AND fencing_token = ? AND expires_at > ?",
            (
                to_ts(now + timedelta(seconds=ttl_s)),
                lease.address_id,
                lease.holder,
                lease.token,
                to_ts(now),
            ),
        )
    return cur.rowcount == 1


def release(conn: sqlite3.Connection, lease: Lease) -> None:
    with write_tx(conn):
        conn.execute(
            "DELETE FROM leases WHERE address_id = ? AND holder = ? AND fencing_token = ?",
            (lease.address_id, lease.holder, lease.token),
        )


def held(conn: sqlite3.Connection, clock: Clock, lease: Lease) -> bool:
    """True while this lease (holder and fencing token) is current and unexpired."""
    row = conn.execute(
        "SELECT 1 FROM leases WHERE address_id = ? AND holder = ? AND fencing_token = ?"
        " AND expires_at > ?",
        (lease.address_id, lease.holder, lease.token, to_ts(clock.now())),
    ).fetchone()
    return row is not None


@dataclass
class Renewer:
    """Renews a lease on its own thread every RENEW_S seconds until stopped; `lost` is set if a
    renewal fails, and the holder must stop writing."""

    connect: Callable[[], sqlite3.Connection]
    clock: Clock
    lease: Lease
    every_s: float = RENEW_S
    lost: threading.Event = field(default_factory=threading.Event)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def __enter__(self) -> Renewer:
        self._thread = threading.Thread(
            target=self._run, name=f"lease-{self.lease.address_id}", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        conn = self.connect()  # its own connection: SQLite connections aren't shared (§11.2)
        try:
            while not self._stop.wait(self.every_s):
                if not renew(conn, self.clock, self.lease):
                    self.lost.set()
                    return
        finally:
            conn.close()
