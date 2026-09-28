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
    items.transition(
        conn, clock, item, Status.HELD, TransitionContext(stage=Stage.ASSIST), actor="t"
    )
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
    lying = TransitionContext(clarification_rounds=0)  # the caller's counts are ignored
    for to in (
        Status.CLASSIFIED,
        Status.PROPOSED,
        Status.NEEDS_CLARIFICATION,
        Status.CLARIFIED,
        Status.PROPOSED,
        Status.NEEDS_CLARIFICATION,
    ):
        items.transition(conn, clock, item, to, ctx, actor="t")
    with pytest.raises(PolicyDeniedError):  # 2nd question open (rounds=2): no person yet
        items.transition(conn, clock, item, Status.NEEDS_HUMAN, ctx, actor="t")
    for to in (Status.CLARIFIED, Status.PROPOSED, Status.NEEDS_CLARIFICATION):  # 3rd question
        items.transition(conn, clock, item, to, ctx, actor="t")
    with pytest.raises(PolicyDeniedError):  # it can't be answered, even if the caller lies
        items.transition(conn, clock, item, Status.CLARIFIED, lying, actor="t")
    items.transition(conn, clock, item, Status.NEEDS_HUMAN, ctx, actor="t")
    assert items.get_status(conn, item) is Status.NEEDS_HUMAN


def test_no_dead_end_after_the_cap(
    conn: sqlite3.Connection, clock: FakeClock, item: StableId
) -> None:
    """Regression: a third round used to be accepted and then stuck at `clarified`."""
    ctx = TransitionContext(origin=Origin.ANSWER)
    for to in (Status.CLASSIFIED, Status.PROPOSED):
        items.transition(conn, clock, item, to, ctx, actor="t")
    for _ in range(2):
        for to in (Status.NEEDS_CLARIFICATION, Status.CLARIFIED, Status.PROPOSED):
            items.transition(conn, clock, item, to, ctx, actor="t")
    items.transition(conn, clock, item, Status.NEEDS_CLARIFICATION, ctx, actor="t")
    with pytest.raises(PolicyDeniedError):
        items.transition(conn, clock, item, Status.CLARIFIED, ctx, actor="t")
    assert items.get_status(conn, item) is Status.NEEDS_CLARIFICATION  # and needs_human is open


def test_transition_is_the_only_status_writer() -> None:
    pattern = re.compile(r"UPDATE\s+items\s+SET[^\"';]*\bstatus\s*=", re.I | re.S)
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if p.name != "items.py" and pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE items SET status = 'executed'",
        "UPDATE OR IGNORE items SET status = 'executed'",
        "UPDATE main.items SET stale = 1, status = 'executed'",
        "UPDATE items SET review = 'x', status = 'executed' WHERE 1",
    ],
)
def test_sqlite_refuses_status_writes_outside_transition(
    conn: sqlite3.Connection, item: StableId, sql: str
) -> None:
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        conn.execute(sql)
    assert items.get_status(conn, item) is Status.NEW


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO items (stable_id, address_id, content_hash, status, created_at, updated_at) "
        "VALUES ('x', 'billing', 'c', 'executed', 't', 't')",
        "REPLACE INTO items (stable_id, address_id, content_hash, status, created_at, updated_at) "
        "VALUES ('x', 'billing', 'c', 'executed', 't', 't')",
        "INSERT INTO items (stable_id, address_id, content_hash, status, created_at, updated_at) "
        "VALUES ('x', 'billing', 'c', 'new', 't', 't') "
        "ON CONFLICT (stable_id) DO UPDATE SET status = 'executed'",
    ],
)
def test_sqlite_refuses_item_inserts_outside_create(
    conn: sqlite3.Connection, item: StableId, sql: str
) -> None:
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        conn.execute(sql)
