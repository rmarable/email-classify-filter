"""The Claude queue (V1.4 step 1): preset C stops at `awaiting_claude`. Preset B's edges and the
local model queue went in v3.0.0 (ADR 0031)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from ecf.ids import AddressId, StableId
from ecf_server import claude_queue, decide, items, precheck
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.mail.fake import FakeMailSource
from ecf_server.state_machine import Status, TransitionContext
from tests.test_decide import KNOWN_BULK, MARKETING

REQUEST: dict[str, Any] = MARKETING | {"category": "customer_request", "requires_reply": True,
                                       "requires_action": True}  # fmt: skip


def add(conn: sqlite3.Connection, clock: FakeClock, aid: str, preset: str) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at,"
                     " stage) VALUES (?, ?, 'standard', ?, ?, 'shadow')",
                     (aid, f"{aid}@acme.example", preset, to_ts(clock.now())))  # fmt: skip


def new_item(conn: sqlite3.Connection, clock: FakeClock, aid: str, n: int = 0,
             facts: dict[str, Any] | None = None) -> str:  # fmt: skip
    sid = f"{aid}-{n:02d}".ljust(64, "0")
    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId(aid),
                      content_hash="h", facts=json.dumps(facts or KNOWN_BULK), subject="s",
                      sender="news@vendor-a.example")  # fmt: skip
    return sid


def classify(conn: sqlite3.Connection, clock: FakeClock, sid: str, c: dict[str, Any]) -> None:
    with write_tx(conn):
        conn.execute("UPDATE items SET classification = ? WHERE stable_id = ?",
                     (json.dumps(c), sid))  # fmt: skip
    items.transition(conn, clock, StableId(sid), Status.CLASSIFIED, TransitionContext(),
                     actor="classifier")  # fmt: skip


def status(conn: sqlite3.Connection, sid: str) -> str:
    return str(conn.execute("SELECT status FROM items WHERE stable_id = ?", (sid,)).fetchone()[0])


def run_precheck(conn: sqlite3.Connection, clock: FakeClock, aid: str, sids: list[str]) -> None:
    precheck.run(conn, clock, FakeMailSource(), aid, sids, install="default",
                 max_scan_bytes=1 << 20)  # fmt: skip


def test_preset_c_mail_waits_for_claude_after_the_precheck(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    c_sid = new_item(conn, clock, "c")
    assert status(conn, c_sid) == "new"  # not pre-checked yet
    run_precheck(conn, clock, "c", [c_sid])
    row = conn.execute("SELECT status, prechecked FROM items WHERE stable_id = ?",
                       (c_sid,)).fetchone()  # fmt: skip
    assert tuple(row) == ("awaiting_claude", 1)  # the pre-check's decision still stands
    assert claude_queue.waiting(conn) == {"c": 1}


def test_without_the_actor_an_item_goes_on_by_its_rule(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    sid = new_item(conn, clock, "c")
    classify(conn, clock, sid, MARKETING)
    assert decide.apply(conn, clock, sid) is Status.OBSERVED
    assert claude_queue.waiting(conn) == {}


def test_preset_c_after_a_claude_classification_the_actor_waits_for_claude(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    sid = new_item(conn, clock, "c")
    run_precheck(conn, clock, "c", [sid])
    classify(conn, clock, sid, REQUEST)  # awaiting_claude -> classified (Claude, step 3)
    assert decide.apply(conn, clock, sid) is Status.AWAITING_CLAUDE  # OD-269
    assert status(conn, sid) == "awaiting_claude"


def test_answered_questions_wait_for_claude(conn: sqlite3.Connection, clock: FakeClock) -> None:
    for aid, preset in (("a", "A"), ("c", "C")):  # A: a removed preset, until migration 0038
        add(conn, clock, aid, preset)
        sid = new_item(conn, clock, aid)
        for to in (Status.CLASSIFIED, Status.PROPOSED, Status.NEEDS_CLARIFICATION,
                   Status.CLARIFIED):  # the actor asked and you answered  # fmt: skip
            items.transition(conn, clock, StableId(sid), to, TransitionContext(), actor="t")
    assert claude_queue.waiting(conn) == {"c": 1}


def test_the_sweep_queues_a_classified_item_left_short(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(conn, clock, "x", "C")
    sid = new_item(conn, clock, "x")
    with write_tx(conn):
        conn.execute("UPDATE items SET prechecked = 1 WHERE stable_id = ?", (sid,))
    items.transition(
        conn, clock, StableId(sid), Status.AWAITING_CLAUDE, TransitionContext(), actor="service"
    )
    classify(conn, clock, sid, REQUEST)

    def crashed(*_a: object) -> Status:  # the move to the queue never happened
        return Status.CLASSIFIED

    monkeypatch.setattr(decide.claude_queue, "to_actor", crashed)
    decide.apply(conn, clock, sid)
    monkeypatch.undo()
    assert status(conn, sid) == "classified"
    assert claude_queue.sweep(conn, clock) == 0  # it may still be in progress
    clock.advance(claude_queue.SETTLED.total_seconds() + 1)
    assert claude_queue.sweep(conn, clock) == 1
    assert status(conn, sid) == "awaiting_claude"
    assert decide.sweep(conn, clock) == 0


def test_the_sweep_queues_prechecked_c_mail_but_not_records_only_backfill(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    stranded, backfilled, unchecked = (new_item(conn, clock, "c", n) for n in range(3))
    with write_tx(conn):
        conn.execute("UPDATE items SET prechecked = 1 WHERE stable_id IN (?, ?)",
                     (stranded, backfilled))  # fmt: skip
        conn.execute("UPDATE items SET facts = json_set(facts, '$.precheck.stage', 'backfill')"
                     " WHERE stable_id = ?", (backfilled,))  # fmt: skip
    clock.advance(claude_queue.SETTLED.total_seconds() + 1)
    assert claude_queue.sweep(conn, clock) == 1
    assert [status(conn, s) for s in (stranded, backfilled, unchecked)] == [
        "awaiting_claude", "new", "new"]  # fmt: skip


def test_a_removed_preset_stays_out_of_the_queue(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """An A or B address before migration 0038 converts it (ADR 0031): nothing queues its mail."""
    for preset in ("A", "B"):
        add(conn, clock, preset.lower(), preset)
        sid = new_item(conn, clock, preset.lower())
        classify(conn, clock, sid, REQUEST)
        assert decide.apply(conn, clock, sid) is Status.CLASSIFIED
    clock.advance(3600)
    assert claude_queue.sweep(conn, clock) == 0
    assert claude_queue.waiting(conn) == {}


def test_the_backlog_counts_what_waits_at_awaiting_claude(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """§5.3 (v3.0.0, P9): paused and removed addresses don't count."""
    for aid in ("c", "p", "r"):
        add(conn, clock, aid, "C")
        for n in range(2):
            sid = new_item(conn, clock, aid, n)
            items.transition(conn, clock, StableId(sid), Status.AWAITING_CLAUDE,
                             TransitionContext(), actor="service")  # fmt: skip
    new_item(conn, clock, "c", 9)  # still new: not in the backlog
    assert claude_queue.backlog(conn) == 6
    with write_tx(conn):
        conn.execute("UPDATE addresses SET paused = 1 WHERE address_id = 'p'")
        conn.execute("UPDATE addresses SET removed_at = ? WHERE address_id = 'r'",
                     (to_ts(clock.now()),))  # fmt: skip
    assert claude_queue.backlog(conn) == 2
