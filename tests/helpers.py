"""Test helpers shared by several test files, kept here so a test file can be deleted without
taking another's helpers with it (v3.0.0 removal plan, `planning-docs/REMOVE-LOCAL-MODELS.md`)."""

from __future__ import annotations

import sqlite3

from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx

GOOD = {"category": "invoice", "priority": "medium", "requires_action": True,
        "requires_reply": False, "payment_related": True, "deadline_mentioned": False,
        "sender_type": "vendor", "fraud_risk": "none"}  # fmt: skip
BEC = {"category": "vendor_change_request", "priority": "high", "requires_action": True,
       "requires_reply": False, "payment_related": True, "deadline_mentioned": False,
       "sender_type": "vendor", "fraud_risk": "high"}  # fmt: skip


def add_address(
    conn: sqlite3.Connection, clock: FakeClock, aid: str, *, paused: bool = False
) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at,"
                     " paused) VALUES (?, ?, 'standard', 'C', ?, ?)",
                     (aid, f"{aid}@acme.example", to_ts(clock.now()), int(paused)))  # fmt: skip
