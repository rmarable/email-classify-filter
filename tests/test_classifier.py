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


def test_store_records_the_reprocessing_key_and_an_item_classified_row(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """OD-507 §4.1, §5 (D12, P15, P16, P20): `pinned_models` holds provider, the full prompt and
    rules digests; `item.classified` holds field values only (no text), the model and 16-hex
    prefixes."""
    from ecf_server import claude_review, config  # noqa: PLC0415 - only here

    add_address(conn, clock, "ap")
    sid = _item(conn, clock, "i2", "Invoice 4471 for $4,200 is due Friday.")
    noisy = GOOD | {"reason": "Subject says Invoice 4471 from billing@vendor-a.example"}
    assert classifier.store(conn, clock, sid, noisy, {"classifier": "claude-sonnet-5-5"})
    pinned = json.loads(_row(conn, sid)["pinned_models"])
    assert pinned["provider"] == "claude" and pinned["classifier"] == "claude-sonnet-5-5"
    assert pinned["prompt"] == claude_review.prompt_digest(SCHEMA) and len(pinned["prompt"]) == 64
    assert pinned["rules"] == config.current_rules(conn).digest and len(pinned["rules"]) == 64
    row = conn.execute("SELECT data FROM audit WHERE event = 'item.classified'").fetchone()
    data = json.loads(row["data"])
    assert set(data["fields"]) <= set(SCHEMA.fields) and data["model"] == "claude-sonnet-5-5"
    assert {k: len(v) for k, v in data["digests"].items()} == {"schema": 16, "prompt": 16,
                                                               "rules": 16}  # fmt: skip
    assert "4471" not in row["data"] and "vendor-a" not in row["data"]  # no payload (Q19)
    assert len(row["data"]) < 600  # ~350 B (plan §5)


def test_item_classified_drops_values_that_are_not_the_fields_own() -> None:
    cls = {name: "free text a model wrote" for name in SCHEMA.fields}
    assert classifier.classified_row(cls, SCHEMA, {})["fields"] == {}
