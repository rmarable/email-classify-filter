"""Recording a classification (V1.3 step 3; SPEC §5.1 step 5, §7; I5). The local classifier, its
prompt and input limits went in v3.0.0 (ADR 0031)."""

from __future__ import annotations

import json
import sqlite3

import pytest

from ecf.ids import AddressId, StableId
from ecf.schema import load_schema
from ecf_server import classifier, items
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from tests.helpers import GOOD, add_address

SCHEMA = load_schema()


def _item(conn: sqlite3.Connection, clock: FakeClock, sid: str, text: str, aid: str = "ap") -> str:
    sid = sid.ljust(64, "0")

    def excerpt(c: sqlite3.Connection) -> None:
        c.execute("INSERT INTO excerpts (stable_id, classifier_text, actor_text) VALUES (?, ?, ?)",
                  (sid, text, text[:4000]))  # fmt: skip

    facts = json.dumps({"triggers": {"fraud": ["fraud_1"]}})
    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId(aid),
                      content_hash="h", facts=facts, subject="s",
                      sender="billing@vendor-a.example", also=excerpt)  # fmt: skip
    return sid


def _row(conn: sqlite3.Connection, sid: str) -> sqlite3.Row:
    row: sqlite3.Row = conn.execute("SELECT * FROM items WHERE stable_id = ?", (sid,)).fetchone()
    return row


# ---- recording ----------------------------------------------------------------------------------


@pytest.mark.usefixtures("extensions_on")
def test_a_schema_change_during_the_call_asks_again_instead_of_recording(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """`ecf config apply` changes the schema while the model answers: the answer (to the old
    schema) isn't recorded under the new key; the item stays where it was and is asked again
    (OD-478)."""
    add_address(conn, clock, "ap")
    sid = _item(conn, clock, "i1", "Invoice 4471 for $4,200 is due Friday.")
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by)"
                     " VALUES ('config.schema', ?, ?, 'test')",
                     (json.dumps({"category_values": {"legal_notice": "Legal."}}),
                      to_ts(clock.now())))  # fmt: skip
    assert classifier.store(conn, clock, sid, GOOD, {}, schema_digest=SCHEMA.digest) is False
    row = _row(conn, sid)
    assert row["status"] == "new" and row["classification"] is None
