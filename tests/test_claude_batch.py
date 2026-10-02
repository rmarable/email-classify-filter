"""`classifier_high_batch` (V1.4 step 9; SPEC §7.5, §14.2; OD-052): the setting and its step-up,
and how `/ecf-review` groups `ecf-classifier-high` items into spawns."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from ecf.errors import ConflictError, InvalidInputError, StepupRequiredError
from ecf_server import claude_batch, claude_queue, settings, stepup
from ecf_server.clock import FakeClock
from ecf_server.notify import FakeNotifier
from ecf_server.stepper import FakeStepper
from tests.test_claude_review import REQUEST, add, queue, waiting_item
from tests.test_decide import KNOWN_BULK


def _raise(conn: sqlite3.Connection, clock: FakeClock, aid: str, value: str) -> dict[str, Any]:
    notifier = FakeNotifier()
    with pytest.raises(StepupRequiredError) as ei:
        claude_batch.set_size(conn, clock, notifier, aid, value, nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), claude_batch.KEY, ei.value.extra["target"])
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    return claude_batch.set_size(conn, clock, notifier, aid, value, nonce=issued.nonce_id) | {
        "notices": notifier.sent
    }


def _batches(conn: sqlite3.Connection) -> list[list[str]]:
    rows = conn.execute("SELECT batch_id, stable_id FROM claim_batches ORDER BY batch_id,"
                        " stable_id").fetchall()  # fmt: skip
    by: dict[str, list[str]] = {}
    for r in rows:
        by.setdefault(r["batch_id"], []).append(r["stable_id"])
    return list(by.values())


def test_raising_needs_step_up_and_a_notice_lowering_needs_neither(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "h", "C", sensitivity="high")
    assert claude_batch.size(conn, "h") == 1
    r = _raise(conn, clock, "h", "3")
    assert r["value"] == 3 and claude_batch.size(conn, "h") == 3
    assert any("classifier_high_batch raised from 1 to 3" in body for _t, body in r["notices"])
    notifier = FakeNotifier()
    low = claude_batch.set_size(conn, clock, notifier, "h", "2", nonce=None)
    assert low["value"] == 2 and claude_batch.size(conn, "h") == 2 and notifier.sent == []
    with pytest.raises(ConflictError):
        claude_batch.set_size(conn, clock, notifier, "h", "2", nonce=None)
    events = [r[0] for r in conn.execute("SELECT data FROM audit WHERE event = 'settings.changed'")]
    assert len(events) == 2
    show = {s["key"]: s["value"] for s in settings.show(conn, "h")}
    assert show[claude_batch.KEY] == 2


@pytest.mark.parametrize("value", ["0", "6", "two", ""])
def test_from_1_to_5(conn: sqlite3.Connection, clock: FakeClock, value: str) -> None:
    add(conn, clock, "h", "C", sensitivity="high")
    with pytest.raises(InvalidInputError):
        claude_batch.set_size(conn, clock, FakeNotifier(), "h", value, nonce=None)


def test_only_c_addresses_have_it_and_settings_set_points_to_step_up(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "b", "B")
    with pytest.raises(InvalidInputError, match="preset C"):
        claude_batch.set_size(conn, clock, FakeNotifier(), "b", "2", nonce=None)
    assert claude_batch.KEY not in {s["key"] for s in settings.show(conn, "b")}
    with pytest.raises(InvalidInputError, match="step-up"):
        settings.set_value(conn, clock, claude_batch.KEY, "2", address="b", actor="os_user")


def test_classifier_high_items_share_spawns_up_to_the_setting_per_address(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "h", "C", sensitivity="high")
    add(conn, clock, "g", "C", sensitivity="high")
    add(conn, clock, "b", "B")
    _raise(conn, clock, "h", "3")
    hs = [waiting_item(conn, clock, "h", n) for n in range(4)]
    g1 = waiting_item(conn, clock, "g")
    new_sender = KNOWN_BULK | {"sender_seen_before": False}  # high risk: ecf-actor-high
    b1, b2 = (waiting_item(conn, clock, "b", n, classification=REQUEST, facts=new_sender)
              for n in range(2))  # fmt: skip
    q = queue(conn, clock)
    spawn = {i["id"]: i["spawn"] for i in q["items"]}
    assert len({spawn[s] for s in hs[:3]}) == 1  # three of h's in one spawn
    assert spawn[hs[3]] != spawn[hs[0]]  # the fourth in the next
    assert spawn[g1] not in {spawn[s] for s in hs}  # g's (setting 1) never with h's
    assert spawn[b1] != spawn[b2]  # ecf-actor-high: still one item per spawn
    assert all(i["agent"] == "ecf-actor-high" for i in q["items"] if i["id"] in (b1, b2))
    assert _batches(conn) == [sorted(hs[:3])]  # one spawn, one batch for the hide guard
    assert claude_queue.batch_risky(conn, hs[0])  # its others aren't classified yet
    assert not claude_queue.batch_risky(conn, hs[3])  # alone in its spawn


def test_with_the_default_each_classifier_high_item_has_its_own_spawn(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "h", "C", sensitivity="high")
    add(conn, clock, "c", "C")
    hs = [waiting_item(conn, clock, "h", n) for n in range(2)]
    cs = [waiting_item(conn, clock, "c", n) for n in range(2)]
    q = queue(conn, clock)
    spawn = {i["id"]: i["spawn"] for i in q["items"]}
    assert spawn[hs[0]] != spawn[hs[1]]
    assert spawn[cs[0]] == spawn[cs[1]] == "ecf-classifier"  # the batched agent: one spawn a round
    assert _batches(conn) == [sorted(cs)]
