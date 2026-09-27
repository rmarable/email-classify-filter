import re
import sqlite3
from pathlib import Path

import pytest

from ecf.errors import ConflictError, NotFoundError, PolicyDeniedError
from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import items
from ecf_server.clock import FakeClock
from ecf_server.state_machine import Origin, Stage, TransitionContext

SRC = Path(__file__).resolve().parents[1] / "src"
SID = StableId("ab" * 32)
ADDR = AddressId("billing")


@pytest.fixture
def item(conn: sqlite3.Connection, clock: FakeClock) -> StableId:
    conn.execute(
        "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at) "
        "VALUES ('billing', 'billing@acme.example', 'high', 'A', 't')"
    )
    items.create_item(conn, clock, stable_id=SID, address_id=ADDR, content_hash="c" * 64)
    return SID


def test_created_at_new_with_audit(conn: sqlite3.Connection, item: StableId) -> None:
    assert items.get_status(conn, item) is Status.NEW
    assert conn.execute("SELECT event FROM audit").fetchone()[0] == "item.created"


def test_cannot_create_other_than_new(conn: sqlite3.Connection, clock: FakeClock) -> None:
    with pytest.raises(ConflictError):
        items.create_item(
            conn, clock, stable_id=SID, address_id=ADDR, content_hash="c", status="executed"
        )


def test_transition_writes_and_audits(
    conn: sqlite3.Connection, clock: FakeClock, item: StableId
) -> None:
    prev = items.transition(conn, clock, item, Status.CLASSIFIED, TransitionContext(), actor="t")
    assert prev is Status.NEW
    assert items.get_status(conn, item) is Status.CLASSIFIED
    ev = conn.execute("SELECT event, data FROM audit ORDER BY id DESC").fetchone()
    assert ev["event"] == "item.transitioned"
    assert '"to": "classified"' in ev["data"]


def test_refusals_change_nothing(
    conn: sqlite3.Connection, clock: FakeClock, item: StableId
) -> None:
    with pytest.raises(ConflictError):
        items.transition(conn, clock, item, Status.EXECUTED, TransitionContext(), actor="t")
    with pytest.raises(ConflictError):
        items.transition(
            conn,
            clock,
            item,
            Status.CLASSIFIED,
            TransitionContext(),
            actor="t",
            expected=Status.PROPOSED,
        )
    items.transition(conn, clock, item, Status.CLASSIFIED, TransitionContext(), actor="t")
    items.transition(conn, clock, item, Status.PROPOSED, TransitionContext(), actor="t")
    items.transition(conn, clock, item, Status.HELD, TransitionContext(), actor="t")
    with pytest.raises(PolicyDeniedError):
        items.transition(
            conn, clock, item, Status.PROPOSED, TransitionContext(stage=Stage.ASSIST), actor="t"
        )
    assert items.get_status(conn, item) is Status.HELD
    assert not conn.in_transaction


def test_missing_item(conn: sqlite3.Connection, clock: FakeClock) -> None:
    with pytest.raises(NotFoundError):
        items.transition(conn, clock, SID, Status.CLASSIFIED, TransitionContext(), actor="t")


def test_counters_come_from_the_database(
    conn: sqlite3.Connection, clock: FakeClock, item: StableId
) -> None:
    ctx = TransitionContext(origin=Origin.ANSWER)
    lying = TransitionContext(clarification_rounds=5)  # the caller's counts are ignored
    for to in (Status.CLASSIFIED, Status.PROPOSED, Status.NEEDS_CLARIFICATION):
        items.transition(conn, clock, item, to, ctx, actor="t")
    with pytest.raises(PolicyDeniedError):  # only 1 round so far
        items.transition(conn, clock, item, Status.NEEDS_HUMAN, lying, actor="t")
    for to in (Status.CLARIFIED, Status.PROPOSED, Status.NEEDS_CLARIFICATION):
        items.transition(conn, clock, item, to, ctx, actor="t")
    items.transition(conn, clock, item, Status.NEEDS_HUMAN, ctx, actor="t")  # 2 rounds
    assert items.get_status(conn, item) is Status.NEEDS_HUMAN


def test_transition_is_the_only_status_writer() -> None:
    pattern = re.compile(r"UPDATE\s+items\s+SET[^\"';]*\bstatus\s*=", re.I | re.S)
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if p.name != "items.py" and pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []
