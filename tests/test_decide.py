"""Applying the policy to a classified item (V1.3 step 4b): stages, grants, approvals, escalations,
the actor hand-off and the sweep."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from ecf.ids import AddressId, StableId
from ecf_server import decide, execute, items
from ecf_server.actions import Planned
from ecf_server.clock import Clock, FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.state_machine import Status, TransitionContext

MARKETING: dict[str, Any] = {"category": "marketing", "priority": "low", "requires_action": False,
             "requires_reply": False, "payment_related": False, "deadline_mentioned": False,
             "sender_type": "vendor", "fraud_risk": "none"}  # fmt: skip
KNOWN_BULK: dict[str, Any] = {"triggers": {}, "auth_result": "pass", "sender_seen_before": True,
              "bulk_signal": True, "bulk_corroborates": True}  # fmt: skip


def make_address(
    conn: sqlite3.Connection, clock: FakeClock, stage: str, sensitivity: str = "standard"
) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at,"
                     " stage) VALUES ('ap', 'ap@acme.example', ?, 'A', ?, ?)",
                     (sensitivity, to_ts(clock.now()), stage))  # fmt: skip


def make_classified(conn: sqlite3.Connection, clock: FakeClock, classification: dict[str, Any],
                facts: dict[str, Any], sid: str = "a") -> str:  # fmt: skip
    sid = sid.ljust(64, "0")
    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                      content_hash="h", facts=json.dumps(facts), subject="s",
                      sender="news@vendor-a.example")  # fmt: skip
    with write_tx(conn):
        conn.execute("UPDATE items SET classification = ? WHERE stable_id = ?",
                     (json.dumps(classification), sid))  # fmt: skip
    items.transition(conn, clock, StableId(sid), Status.CLASSIFIED, TransitionContext(),
                     actor="classifier")  # fmt: skip
    return sid


def item_row(conn: sqlite3.Connection, sid: str) -> sqlite3.Row:
    row: sqlite3.Row = conn.execute("SELECT * FROM items WHERE stable_id = ?", (sid,)).fetchone()
    return row


def _jobs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [json.loads(r[0]) for r in conn.execute(
        "SELECT payload FROM jobs WHERE queue = 'actions' ORDER BY rowid")]  # fmt: skip


def test_shadow_records_the_plan_and_changes_nothing(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    make_address(conn, clock, "shadow")
    sid = make_classified(conn, clock, MARKETING, KNOWN_BULK)
    assert decide.apply(conn, clock, sid) is Status.OBSERVED
    p = json.loads(item_row(conn, sid)["proposal"])
    assert p["actions"] == [{"name": "label", "target": "marketing"},
                            {"name": "archive", "target": None}]  # fmt: skip
    assert p["plan"]["rule"] == "marketing" and item_row(conn, sid)["decision_source"] == "rule"
    assert _jobs(conn) == []
    assert (
        conn.execute("SELECT count(*) FROM audit WHERE event = 'policy.decided'").fetchone()[0] == 1
    )


def test_live_runs_automatic_actions_under_a_grant(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    make_address(conn, clock, "live")
    sid = make_classified(conn, clock, MARKETING, KNOWN_BULK)
    assert decide.apply(conn, clock, sid) is Status.EXECUTING
    [job] = _jobs(conn)
    grant = conn.execute("SELECT principal, status FROM grants WHERE grant_id = ?",
                         (job["grant_id"],)).fetchone()  # fmt: skip
    assert tuple(grant) == ("service", "approved")
    ran: list[list[Planned]] = []

    def executor(
        _c: sqlite3.Connection, _clk: Clock, _i: sqlite3.Row, acts: list[Planned]
    ) -> list[str]:
        ran.append(acts)
        return [a.name for a in acts]

    assert execute.run_once(conn, clock, executor)
    assert ran == [[Planned("label", "marketing"), Planned("archive")]]
    assert item_row(conn, sid)["status"] == "executed"


def test_live_asks_a_person_for_a_first_time_sender(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    make_address(conn, clock, "live")
    sid = make_classified(conn, clock, MARKETING, KNOWN_BULK | {"sender_seen_before": False,
                                                           "bulk_corroborates": False})  # fmt: skip
    # not corroborated: label + leave, nothing to approve, runs automatically
    assert decide.apply(conn, clock, sid) is Status.EXECUTING
    sid2 = make_classified(conn, clock, MARKETING, KNOWN_BULK, sid="b")
    with write_tx(conn):
        conn.execute("INSERT INTO settings (key, value, updated_at, updated_by) VALUES"
                     " ('config.action_policy', ?, 't', 't')",
                     (json.dumps({"standard": {"archive": "approve"}}),))  # fmt: skip
    assert decide.apply(conn, clock, sid2) is Status.AWAITING_APPROVAL
    assert conn.execute("SELECT status FROM grants WHERE stable_id = ?",
                        (sid2,)).fetchone()[0] == "issued"  # fmt: skip


def test_assist_runs_labels_and_holds_the_rest(conn: sqlite3.Connection, clock: FakeClock) -> None:
    make_address(conn, clock, "assist")
    invoice = MARKETING | {"category": "invoice", "payment_related": True}
    label_only = make_classified(conn, clock, invoice, KNOWN_BULK)
    assert decide.apply(conn, clock, label_only) is Status.EXECUTING
    hide = make_classified(conn, clock, MARKETING, KNOWN_BULK, sid="b")
    assert decide.apply(conn, clock, hide) is Status.HELD


def test_a_model_driven_fraud_guard_escalates_in_every_stage(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    make_address(conn, clock, "shadow")
    risky = MARKETING | {"category": "invoice", "fraud_risk": "high", "payment_related": True}
    sid = make_classified(conn, clock, risky, KNOWN_BULK)
    decide.apply(conn, clock, sid)
    assert conn.execute("SELECT state FROM escalations WHERE stable_id = ?",
                        (sid,)).fetchone()[0] == "pending"  # fmt: skip
    p = json.loads(item_row(conn, sid)["proposal"])["plan"]
    assert p["rule"] == "fraud_guard" and p["payment_or_fraud"] and p["high_risk"]


def test_the_actor_decides_next_when_the_rule_says_so(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    make_address(conn, clock, "live")
    sid = make_classified(conn, clock, MARKETING | {"category": "customer_request",
                                                "requires_reply": True}, KNOWN_BULK)  # fmt: skip
    assert decide.apply(conn, clock, sid) is Status.CLASSIFIED
    assert json.loads(item_row(conn, sid)["proposal"])["plan"]["to_actor"] is True


@pytest.mark.parametrize("stage", ["shadow", "live"])
def test_the_sweep_decides_classified_items_left_without_a_plan(
    conn: sqlite3.Connection, clock: FakeClock, stage: str
) -> None:
    make_address(conn, clock, stage)
    sid = make_classified(conn, clock, MARKETING, KNOWN_BULK)
    assert decide.sweep(conn, clock) == 1
    assert item_row(conn, sid)["status"] in ("observed", "executing")
    assert decide.sweep(conn, clock) == 0


def test_the_sweep_moves_on_an_item_whose_plan_was_recorded_before_an_error(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_address(conn, clock, "shadow")
    sid = make_classified(conn, clock, MARKETING, KNOWN_BULK)
    real = decide.items.transition

    def busy(*_a: Any, **_k: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(decide.items, "transition", busy)
    with pytest.raises(sqlite3.OperationalError):
        decide.apply(conn, clock, sid)
    monkeypatch.setattr(decide.items, "transition", real)
    assert item_row(conn, sid)["status"] == "classified" and item_row(conn, sid)["proposal"]
    assert decide.sweep(conn, clock) == 0  # it may still be in progress
    clock.advance(decide.STRANDED_AFTER.total_seconds() + 1)
    assert decide.sweep(conn, clock) == 1
    assert item_row(conn, sid)["status"] == "observed"


def test_the_sweep_leaves_items_waiting_for_the_actor(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    make_address(conn, clock, "shadow")
    request = MARKETING | {"category": "customer_request", "requires_reply": True,
                           "requires_action": True}  # fmt: skip
    sid = make_classified(conn, clock, request, KNOWN_BULK)
    assert decide.apply(conn, clock, sid).value == "classified"  # the actor decides next
    clock.advance(3600)
    assert decide.sweep(conn, clock) == 0
