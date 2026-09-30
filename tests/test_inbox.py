"""`ecf inbox`, `ecf item show` and `ecf item resolve` (V1.2 step 7a)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from ecf.cli_items import describe, plain
from ecf.errors import ConflictError, InvalidInputError, NotFoundError, StepupRequiredError
from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import inbox, items, stepup
from ecf_server.api import ServiceState, create_app
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.inbox import Selection
from ecf_server.state_machine import TransitionContext
from ecf_server.stepper import FakeStepper

FRAUD = {
    "triggers": {"fraud": ["bank details from an unconfirmed sender"]},
    "payment_keyword": True,
}
PLAIN: dict[str, Any] = {"triggers": {}, "sender_confirmed": True}


def _address(conn: sqlite3.Connection, clock: FakeClock, aid: str = "ap") -> None:
    with write_tx(conn):
        conn.execute(
            "INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
            " VALUES (?, ?, 'standard', 'A', ?)",
            (aid, f"{aid}@acme.example", to_ts(clock.now())),
        )


def _item(
    conn: sqlite3.Connection,
    clock: FakeClock,
    sid: str,
    facts: dict[str, Any],
    *,
    escalated: bool = False,
    aid: str = "ap",
) -> None:
    def also(c: sqlite3.Connection) -> None:
        c.execute("INSERT INTO excerpts (stable_id, classifier_text) VALUES (?, ?)",
                  (sid, "Hello \x1b[31mred\x1b[0m text"))  # fmt: skip
        if escalated:
            c.execute("INSERT INTO escalations (stable_id, address_id, state, created_at)"
                      " VALUES (?, ?, 'posted', ?)", (sid, aid, to_ts(clock.now())))  # fmt: skip

    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId(aid),
                      content_hash="h", facts=json.dumps(facts), subject="Invoice 42",
                      sender="billing@vendor-a.example", also=also)  # fmt: skip


def _status(conn: sqlite3.Connection, sid: str) -> str:
    return str(conn.execute("SELECT status FROM items WHERE stable_id = ?", (sid,)).fetchone()[0])


def test_short_ids(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock)
    _item(conn, clock, "abcdef01" + "0" * 56, PLAIN)
    _item(conn, clock, "abcdef01" + "1" * 56, PLAIN)
    _item(conn, clock, "12345678" + "0" * 56, PLAIN)
    assert inbox.find(conn, "12345678")["stable_id"].startswith("12345678")
    assert inbox.find(conn, "ABCDEF010")["stable_id"] == "abcdef01" + "0" * 56
    with pytest.raises(ConflictError, match="more than one"):
        inbox.find(conn, "abcdef01")
    with pytest.raises(NotFoundError):
        inbox.find(conn, "ffffffff")
    for bad in ("1234567", "1234567z", "12345678'; --"):
        with pytest.raises(InvalidInputError):
            inbox.find(conn, bad)


def test_inbox_lists_escalations_and_items_waiting_on_you(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock)
    _address(conn, clock, "billing")
    _item(conn, clock, "a" * 64, FRAUD, escalated=True)
    _item(conn, clock, "b" * 64, PLAIN)  # new, nothing for you to do
    _item(conn, clock, "c" * 64, PLAIN, aid="billing", escalated=True)
    with write_tx(conn):
        conn.execute("UPDATE items SET stale = 1 WHERE stable_id = ?", ("c" * 64,))
    found = inbox.inbox(conn)
    assert [i["id"] for i in found] == ["c" * 64, "a" * 64]  # stale first
    assert found[1]["payment_or_fraud"] and found[1]["why"].startswith("bank details")
    assert [i["id"] for i in inbox.inbox(conn, address_id="ap")] == ["a" * 64]
    assert [i["id"] for i in inbox.inbox(conn, stale_only=True)] == ["c" * 64]
    assert inbox.counts(conn) == {"ap": {"new": 2}, "billing": {"new": 1}}


def test_show_has_facts_history_and_the_excerpt(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock)
    _item(conn, clock, "a" * 64, FRAUD, escalated=True)
    d = inbox.show(conn, "aaaaaaaa")
    assert d["subject"] == "Invoice 42" and d["escalation"]["state"] == "posted"
    assert [h["event"] for h in d["history"]] == ["item.created"]
    assert "red" in d["excerpt"]
    printed = "\n".join(plain(x) for x in describe(d))
    assert "\x1b" not in printed and "Hello [31mred[0m text" in printed  # no escape sequences
    assert "ecf never pays anything" in printed


def test_resolving_a_plain_item_needs_a_reason_but_no_step_up(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock)
    _item(conn, clock, "b" * 64, PLAIN)
    with pytest.raises(InvalidInputError, match="reason"):
        inbox.resolve(conn, clock, Selection(refs=["bbbbbbbb"]), reason=" ", nonce=None)
    assert inbox.resolve(conn, clock, Selection(refs=["bbbbbbbb"]), reason="handled by phone",
                         nonce=None) == ["b" * 64]  # fmt: skip
    assert _status(conn, "b" * 64) == Status.RESOLVED_MANUAL
    row = conn.execute("SELECT data FROM audit WHERE event = 'item.resolved'").fetchone()
    assert json.loads(row["data"]) == {"reason": "handled by phone"}
    with pytest.raises(ConflictError, match="already closed"):
        inbox.resolve(conn, clock, Selection(refs=["bbbbbbbb"]), reason="again", nonce=None)


def test_a_fraud_item_needs_step_up_for_exactly_that_set(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock)
    _item(conn, clock, "a" * 64, FRAUD, escalated=True)
    _item(conn, clock, "b" * 64, PLAIN)
    both = Selection(refs=["aaaaaaaa", "bbbbbbbb"])
    with pytest.raises(StepupRequiredError) as ei:
        inbox.resolve(conn, clock, both, reason="spoofed; called the vendor", nonce=None)
    target = ei.value.extra["target"]
    issued = stepup.issue(conn, clock, FakeStepper(), "item_resolve", target)
    assert issued.prompt.startswith("ecf: close 2 emails (1 payment or fraud) without acting")
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    with pytest.raises(StepupRequiredError):  # a smaller set isn't what was confirmed
        inbox.resolve(conn, clock, Selection(refs=["aaaaaaaa"]), reason="x", nonce=issued.nonce_id)
    done = inbox.resolve(conn, clock, both, reason="spoofed", nonce=issued.nonce_id)
    assert done == ["a" * 64, "b" * 64]
    card = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out'").fetchone()
    assert card is None  # no route recorded: nothing to edit


def test_older_than_previews_then_resolves_that_set(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock)
    _item(conn, clock, "a" * 64, PLAIN)
    clock.advance(40 * 86400)
    _item(conn, clock, "b" * 64, PLAIN)
    old = Selection(older_than_days=30)
    assert inbox.resolve(conn, clock, old, reason="old", nonce=None, dry_run=True) == ["a" * 64]
    assert _status(conn, "a" * 64) == Status.NEW  # a dry run changes nothing
    assert inbox.resolve(conn, clock, old, reason="old", nonce=None) == ["a" * 64]
    with pytest.raises(InvalidInputError):
        inbox.resolve(conn, clock, Selection(), reason="x", nonce=None)


def test_a_card_is_edited_when_its_item_is_resolved(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock)
    with write_tx(conn):
        conn.execute("INSERT INTO routes (address_id, surface, route_ref, name)"
                     " VALUES ('ap', 'slack', 'CAP', 'ecf-default-ap')")  # fmt: skip
    _item(conn, clock, "b" * 64, PLAIN)
    with write_tx(conn):
        conn.execute("INSERT INTO escalations (stable_id, address_id, state, thread_key,"
                     " created_at) VALUES (?, 'ap', 'posted', ?, 't')",
                     ("b" * 64, f"item:{'b' * 64}"))  # fmt: skip
    inbox.resolve(conn, clock, Selection(refs=["bbbbbbbb"]), reason="done", nonce=None)
    p = json.loads(conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out'").fetchone()[0])
    assert p["key"] == f"item:{'b' * 64}" and p["card"]["title"] == "Resolved at your computer"
    assert p["card"]["buttons"] == []


def test_the_routes(conn: sqlite3.Connection, db_path: Path, clock: FakeClock) -> None:
    _address(conn, clock)
    _item(conn, clock, "a" * 64, FRAUD, escalated=True)
    _item(conn, clock, "b" * 64, PLAIN)
    items.transition(conn, clock, StableId("b" * 64), Status.RESOLVED_MANUAL, TransitionContext(),
                     actor="test")  # fmt: skip
    state = ServiceState(install="t", token="tok", started_at="2026-10-01T12:00:00.000000Z",
                         clock=clock, db_path=db_path)  # fmt: skip

    async def call(method: str, path: str, body: Any = None) -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.request(method, path, json=body,
                                   headers={"Authorization": "Bearer tok"})  # fmt: skip

    r = anyio.run(call, "GET", "/v1/inbox")
    assert [i["short_id"] for i in r.json()["items"]] == ["aaaaaaaa"]
    assert anyio.run(call, "GET", "/v1/items/aaaaaaaa").json()["status"] == "new"
    assert anyio.run(call, "GET", "/v1/counts").json()["counts"]["ap"] == {
        "new": 1, "resolved_manual": 1}  # fmt: skip
    r = anyio.run(call, "POST", "/v1/items/aaaaaaaa/resolve", {"reason": "checked"})
    assert r.status_code == 403 and r.json()["code"] == "stepup_required"
    r = anyio.run(call, "POST", "/v1/items/resolve",
                  {"older_than_days": 0, "reason": "x"})  # fmt: skip
    assert r.status_code == 400
