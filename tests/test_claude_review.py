"""Claims and submissions for `/ecf-review` (V1.4 step 3; SPEC §10.4, §15.1; OD-267, OD-270):
claiming and agents, claim expiry and fencing, the untrusted message, submissions checked like the
local model's, the batch guard, and the routes' profiles."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from ecf.errors import ConflictError, ForbiddenProfileError, InvalidInputError, NotFoundError
from ecf.ids import AddressId, StableId
from ecf.schema import load_schema, with_fixed
from ecf_server import (
    claude_batch,
    claude_pins,
    claude_queue,
    claude_review,
    config,
    decide,
    items,
    policy,
    signals,
)
from ecf_server.api import ServiceState, create_app
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import NullNotifier
from ecf_server.rules import load_starter_rules
from ecf_server.state_machine import Status, TransitionContext
from ecf_server.telemetry import SUBAGENT, ApiCall, Hold, Seen, Telemetry
from tests.test_decide import KNOWN_BULK, MARKETING

# a full schema v3 answer (the fixed items derived, OD-498)
REQUEST: dict[str, Any] = with_fixed(MARKETING | {"category": "customer_request",
                                                  "requires_reply": True,
                                                  "requires_action": True})  # fmt: skip
ROUTINE: dict[str, Any] = REQUEST | {"requires_reply": False, "requires_action": False}
S1, S2 = "session-one", "session-two"
# a schema extension (SCHEMA-V2-PLAN step 4): one enum field, one boolean, one category value
EXT: dict[str, Any] = {
    "fields": {
        "contract_stage": {"type": "enum", "description": "Where a contract discussed stands.",
                           "values": {"none": "No contract discussed.",
                                      "draft": "A draft is being exchanged."}},
        "lawyer_involved": {"type": "boolean", "description": "A lawyer writes or is copied."},
    },
    "category_values": {"legal_notice": "Letter from a lawyer or court about us."},
}  # fmt: skip


def extend(conn: sqlite3.Connection, clock: FakeClock, ext: dict[str, Any] | None = None) -> None:
    """Apply a schema extension (as `ecf config apply` would store it)."""
    with write_tx(conn):
        conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at, updated_by)"
                     " VALUES ('config.schema', ?, ?, 'test')",
                     (config.ordered_json(ext or EXT), to_ts(clock.now())))  # fmt: skip


def add(conn: sqlite3.Connection, clock: FakeClock, aid: str, preset: str,
        sensitivity: str = "standard", stage: str = "shadow") -> None:  # fmt: skip
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at,"
                     " stage) VALUES (?, ?, ?, ?, ?, ?)",
                     (aid, f"{aid}@acme.example", sensitivity, preset, to_ts(clock.now()),
                      stage))  # fmt: skip


def waiting_item(conn: sqlite3.Connection, clock: FakeClock, aid: str, n: int = 0, *,
                 classification: dict[str, Any] | None = None,
                 facts: dict[str, Any] | None = None) -> str:  # fmt: skip
    """An item at `awaiting_claude`: unclassified (C) or classified and waiting for the actor."""
    sid = f"{aid.encode().hex()}{n:02d}".ljust(64, "0")  # hex, as item IDs are
    attachments = [{"name": "w9.pdf", "type": "application/pdf", "size": 1200, "inline": False},
                   {"name": None, "type": "image/png", "size": 90, "inline": True}]  # fmt: skip
    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId(aid),
                      content_hash="h", subject="W-9 please", sender="ann@cust.example",
                      sender_name="Ann", locator=json.dumps({"internaldate": "2026-10-02T09:00Z"}),
                      facts=json.dumps((facts or KNOWN_BULK) | {"attachments": attachments}),
                      )  # fmt: skip
    with write_tx(conn):
        conn.execute("INSERT INTO excerpts (stable_id, classifier_text, actor_text) VALUES"
                     " (?, 'short text', 'Please send the W-9 again. Longer text.')",
                     (sid,))  # fmt: skip
        if classification is not None:
            conn.execute("UPDATE items SET classification = ? WHERE stable_id = ?",
                         (json.dumps(classification), sid))  # fmt: skip
    if classification is not None:
        items.transition(conn, clock, StableId(sid), Status.CLASSIFIED, TransitionContext(),
                         actor="classifier")  # fmt: skip
        assert decide.apply(conn, clock, sid) is Status.AWAITING_CLAUDE
    else:
        items.transition(conn, clock, StableId(sid), Status.AWAITING_CLAUDE, TransitionContext(),
                         actor="service")  # fmt: skip
    clock.advance(1)  # a distinct updated_at: oldest first
    return sid


def row(conn: sqlite3.Connection, sid: str) -> sqlite3.Row:
    r: sqlite3.Row = conn.execute("SELECT * FROM items WHERE stable_id = ?", (sid,)).fetchone()
    return r


def queue(conn: sqlite3.Connection, clock: FakeClock, session: str = S1,
          **kw: Any) -> dict[str, Any]:  # fmt: skip
    return claude_review.review_queue(conn, clock, session, **kw)


def drain(conn: sqlite3.Connection, clock: FakeClock) -> list[dict[str, Any]]:
    """Every round until the queue is empty, each by a new session (a round hands out one agent
    type, and a session gets no other type while its claims are out); the rounds' items."""
    rounds: list[list[dict[str, Any]]] = []
    while items := with_tokens(queue(conn, clock, f"session-drain-{len(rounds)}"))["items"]:
        rounds.append(items)
    assert all(len({i["agent"] for i in r}) == 1 for r in rounds)  # one agent type a round
    return [i for r in rounds for i in r]


def tokens_of(q: dict[str, Any]) -> dict[str, str]:
    """Item -> token, read from the spawn prompts the service wrote (the only place, OD-499)."""
    out: dict[str, str] = {}
    for s in q["spawns"]:
        for line in s["prompt"].splitlines():
            ref, _, token = line.removeprefix("id=").partition(" claim_token=")
            assert ref not in out  # each claimed item exactly once
            out[ref] = token
    return out


def with_tokens(q: dict[str, Any]) -> dict[str, Any]:
    """The reply with each item's token from its spawn's prompt, as its agent gets it."""
    tok = tokens_of(q)
    return q | {"items": [i | {"claim_token": tok[i["id"]]} for i in q["items"]]}


def token_for(q: dict[str, Any], sid: str) -> str:
    return tokens_of(q)[sid]


def agent_of(conn: sqlite3.Connection, sid: str) -> str:
    """The agent the service named for the item's claim: its agent server calls as that agent."""
    row = conn.execute("SELECT agent FROM claims WHERE stable_id = ?", (sid,)).fetchone()
    return str(row[0]) if row else "ecf-classifier"


def read_msg(conn: sqlite3.Connection, clock: FakeClock, session: str, sid: str,
             token: str) -> dict[str, Any]:  # fmt: skip
    return claude_review.get_message(conn, clock, session, sid, token, agent_of(conn, sid))


def settled(conn: sqlite3.Connection, clock: FakeClock,
            got: dict[str, Any] | Hold) -> dict[str, Any]:  # fmt: skip
    """Telemetry stands in here: the window's subagent work on the role's pinned model."""
    if not isinstance(got, Hold):
        return got
    pin = claude_pins.effective(conn)[claude_review.ROLE[got.agent]]
    return claude_review.settle(conn, clock, NullNotifier(), Telemetry(), got, Seen(pin))


def submit_classification(conn: sqlite3.Connection, clock: FakeClock, session: str, sid: str,
                          token: str, c: Any) -> dict[str, Any]:  # fmt: skip
    return settled(conn, clock, claude_review.record_classification(
        conn, clock, session, sid, token, c, agent_of(conn, sid)))  # fmt: skip


def submit_proposal(conn: sqlite3.Connection, clock: FakeClock, session: str, sid: str,
                    token: str, body: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
    return settled(conn, clock, claude_review.propose_action(
        conn, clock, session, sid, token, body, agent_of(conn, sid)))  # fmt: skip


# Claiming: the queue claims waiting items for one agent type at a time, waits for claims out,
# and refuses expired, other sessions' or ended claims.


def test_the_queue_claims_waiting_items_and_names_their_agent(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    add(conn, clock, "h", "C", sensitivity="high")
    add(conn, clock, "b", "C")
    c1 = waiting_item(conn, clock, "c")
    h1 = waiting_item(conn, clock, "h")
    b1 = waiting_item(conn, clock, "b", classification=REQUEST)
    b2 = waiting_item(conn, clock, "b", 1, classification=REQUEST,
                      facts=KNOWN_BULK | {"sender_seen_before": False})  # fmt: skip
    q = with_tokens(queue(conn, clock))
    assert [i["id"] for i in q["items"]] == [c1]  # the oldest item's agent type, only that
    items = q["items"] + drain(conn, clock)
    got = {i["id"]: (i["need"], i["agent"]) for i in items}
    assert got == {c1: ("classify", "ecf-classifier"), h1: ("classify", "ecf-classifier-high"),
                   b1: ("act", "ecf-actor"), b2: ("act", "ecf-actor-high")}  # fmt: skip
    assert [i["id"] for i in items] == [c1, h1, b1, b2]  # oldest first
    assert q["more"] is False and q["results"] == []
    assert all(i["claim_token"].startswith("1.") for i in items)
    assert queue(conn, clock)["items"] == []  # claimed: not handed out again
    assert queue(conn, clock, S2)["items"] == []  # nor to another session


def test_a_session_gets_one_agent_type_at_a_time(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """The model check can't tell overlapping work of two models apart (telemetry.py): while a
    session's classifier claims are out it gets no actor work; another session can."""
    add(conn, clock, "c", "C")
    add(conn, clock, "b", "C")
    c1 = waiting_item(conn, clock, "c")
    b1 = waiting_item(conn, clock, "b", classification=REQUEST)
    assert [i["id"] for i in queue(conn, clock)["items"]] == [c1]
    assert queue(conn, clock)["items"] == []  # b1 needs ecf-actor: not while c1 is out
    assert [i["id"] for i in queue(conn, clock, S2)["items"]] == [b1]
    clock.advance(int(claude_review.CLAIM_TTL.total_seconds()) + 1)  # c1's claim expires
    assert [i["id"] for i in queue(conn, clock)["items"]] == [c1]  # S1 may take any type now


def test_in_progress_counts_the_sessions_claims_and_an_empty_reply_waits_for_them(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """A session that lost count of its spawns asks again (run 5f5772a7): the reply says how
    many of its claims are out (held ones too) and, when empty only because of them, waits up to
    WAIT_S; with nothing out it returns at once."""
    add(conn, clock, "c", "C")
    a, b = waiting_item(conn, clock, "c"), waiting_item(conn, clock, "c", 1)
    slept: list[float] = []
    q = claude_review.review_queue_waiting(conn, clock, S1, sleep=slept.append)
    assert len(q["items"]) == 2 and q["in_progress"] == 2 and slept == []
    assert queue(conn, clock, S2)["in_progress"] == 0  # another session's claims aren't counted
    got = claude_review.record_classification(conn, clock, S1, a, token_for(q, a), REQUEST,
                                              agent_of(conn, a))  # fmt: skip
    assert isinstance(got, Hold)  # held, waiting for its model check: still in progress
    q2 = claude_review.review_queue_waiting(conn, clock, S1, sleep=slept.append)
    assert q2["items"] == [] and q2["in_progress"] == 2
    assert sum(slept) == claude_review.WAIT_S and claude_review.WAIT_S < 10  # ecf-mcp's limit
    settled(conn, clock, got)
    assert claude_review.release_session(conn, S1) == 1  # b's claim
    slept.clear()
    q3 = claude_review.review_queue_waiting(conn, clock, S1, sleep=slept.append)
    assert [i["id"] for i in q3["items"]] == [b] and q3["in_progress"] == 1
    q4 = claude_review.review_queue_waiting(conn, clock, S2, sleep=slept.append)
    assert [(i["id"], i["need"]) for i in q4["items"]] == [(a, "act")]  # classified: to the actor
    q5 = claude_review.review_queue_waiting(conn, clock, "session-3", sleep=slept.append)
    assert q5["items"] == [] and q5["in_progress"] == 0 and slept == []  # nothing to wait for


def test_an_expired_claims_agent_holds_back_other_agent_types(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """An expired claim's agent may still be running (dispatch plan S3): no other agent type
    starts for that session for GRACE, and the same agent is re-handed its work at once."""
    add(conn, clock, "c", "C")
    waiting_item(conn, clock, "c")
    first = queue(conn, clock)["items"][0]
    assert queue(conn, clock)["next"] == "call again"
    clock.advance(int(claude_review.CLAIM_TTL.total_seconds()) + 1)
    again = queue(conn, clock)
    assert again["results"] == [{"id": first["id"], "outcome": "claim_expired"}]
    assert [i["agent"] for i in again["items"]] == [first["agent"]]
    claude_review._GRACE[S1] = {"ecf-actor-high": clock.now() + timedelta(seconds=10)}  # pyright: ignore[reportPrivateUsage]
    waiting_item(conn, clock, "c", 1)
    q = queue(conn, clock, session=S1)
    assert q["items"] == [] and q["next"] == "call again" and q["more"] is True


def test_the_review_wait_goes_on_while_next_says_call_again(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    waiting_item(conn, clock, "c")
    claude_review._GRACE[S1] = {"ecf-actor-high": clock.now() + timedelta(seconds=60)}  # pyright: ignore[reportPrivateUsage]
    slept: list[float] = []
    q = claude_review.review_queue_waiting(conn, clock, S1, sleep=slept.append)
    assert q["next"] == "call again" and q["in_progress"] == 0 and slept


def test_an_empty_queue_says_end(conn: sqlite3.Connection, clock: FakeClock) -> None:
    add(conn, clock, "c", "C")
    assert queue(conn, clock)["next"] == "end"


def test_a_held_resubmission_is_pending_not_a_conflict(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    sid = waiting_item(conn, clock, "c")
    item = with_tokens(queue(conn, clock))["items"][0]
    got = claude_review.record_classification(conn, clock, S1, sid, item["claim_token"], REQUEST,
                                              item["agent"])  # fmt: skip
    assert isinstance(got, Hold)
    again = claude_review.record_classification(conn, clock, S1, sid, item["claim_token"],
                                                REQUEST, item["agent"])  # fmt: skip
    assert again == {"accepted": True, "pending": True, "errors": []}
    with pytest.raises(ConflictError):  # another session's token never replays
        claude_review.record_classification(conn, clock, S2, sid, item["claim_token"], REQUEST,
                                            item["agent"])  # fmt: skip


def test_limit_more_address_and_paused(conn: sqlite3.Connection, clock: FakeClock) -> None:
    add(conn, clock, "c", "C")
    add(conn, clock, "d", "C")
    sids = [waiting_item(conn, clock, "c", n) for n in range(3)]
    d1 = waiting_item(conn, clock, "d")
    q = queue(conn, clock, limit=2, address="c@acme.example")
    assert [i["id"] for i in q["items"]] == sids[:2] and q["more"] is True
    with write_tx(conn):
        conn.execute("UPDATE addresses SET paused = 1 WHERE address_id = 'c'")
    claude_review.release_session(conn, S1)  # a session gets nothing while its claims are out
    assert [i["id"] for i in queue(conn, clock)["items"]] == [d1]
    for bad in (0, 51):
        with pytest.raises(InvalidInputError):
            queue(conn, clock, limit=bad)
    with pytest.raises(NotFoundError):
        queue(conn, clock, address="nobody@acme.example")


def test_an_expired_claim_is_reported_and_its_token_refused(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    sid = waiting_item(conn, clock, "c")
    old = token_for(queue(conn, clock), sid)
    clock.advance(claude_review.CLAIM_TTL.total_seconds() + 1)
    with pytest.raises(ConflictError):
        read_msg(conn, clock, S1, sid, old)
    again = queue(conn, clock)
    assert again["results"] == [{"id": sid, "outcome": "claim_expired"}]
    new = token_for(again, sid)
    assert new.startswith("2.")  # the fence went up
    with pytest.raises(ConflictError):  # the earlier claim is fenced off for good
        submit_classification(conn, clock, S1, sid, old, REQUEST)
    assert queue(conn, clock)["results"] == []  # reported once


def test_another_claim_or_session_is_refused(conn: sqlite3.Connection, clock: FakeClock) -> None:
    add(conn, clock, "c", "C")
    a, b = waiting_item(conn, clock, "c"), waiting_item(conn, clock, "c", 1)
    q = queue(conn, clock)
    with pytest.raises(ConflictError):
        read_msg(conn, clock, S2, a, token_for(q, a))  # another session
    with pytest.raises(ConflictError):
        read_msg(conn, clock, S1, a, token_for(q, b))  # b's token
    with pytest.raises(ConflictError):
        submit_proposal(conn, clock, S1, a, token_for(q, a),
                                     {"action": "flag", "target": "", "reason": "x"})  # fmt: skip
    with pytest.raises(NotFoundError):
        read_msg(conn, clock, S1, "f" * 64, token_for(q, a))


def test_claims_end_with_the_session_and_at_start(conn: sqlite3.Connection,
                                                   clock: FakeClock) -> None:  # fmt: skip
    add(conn, clock, "c", "C")
    sid = waiting_item(conn, clock, "c")
    tok = token_for(queue(conn, clock), sid)
    assert claude_review.release_session(conn, S1) == 1
    with pytest.raises(ConflictError):
        read_msg(conn, clock, S1, sid, tok)
    assert [i["id"] for i in queue(conn, clock, S2)["items"]] == [sid]
    claude_review.release_all(conn)
    assert [i["id"] for i in queue(conn, clock)["items"]] == [sid]


# Reading a message: it is wrapped, carries no facts, and the classifier gets the true signals.


def test_the_message_is_wrapped_and_carries_no_facts_but_signals(conn: sqlite3.Connection,
                                                      clock: FakeClock) -> None:  # fmt: skip
    add(conn, clock, "c", "C")
    add(conn, clock, "b", "C")
    c1 = waiting_item(conn, clock, "c")
    b1 = waiting_item(conn, clock, "b", classification=REQUEST)
    q, q2 = queue(conn, clock), queue(conn, clock, S2)  # one agent type a round
    m = read_msg(conn, clock, S1, c1, token_for(q, c1))
    assert m["notice"] == claude_review.NOTICE and m["need"] == "classify"
    assert m["untrusted_email"] == {
        "from": "Ann <ann@cust.example>", "subject": "W-9 please", "date": "2026-10-02T09:00Z",
        "text": "short text",
        "attachments_meta": [{"name": "w9.pdf", "type": "application/pdf", "size": 1200}],
    }  # fmt: skip
    assert "category" in json.dumps(m["schema"])
    assert m["schema_text"] == load_schema().prompt_block  # each field's and value's meaning
    flat = json.dumps(m)
    for fact in ("sender_seen_before", "auth_result", "bulk_corroborates", "triggers"):
        assert fact not in flat  # §7.2: computed facts never go to a model
    assert m["signals"] == {}  # derived signals only (OD-484), none true here
    a = read_msg(conn, clock, S2, b1, token_for(q2, b1))
    assert "signals" not in a  # the classifiers' exemption, not the actor's
    assert a["untrusted_email"]["text"].startswith("Please send the W-9")  # the actor excerpt
    assert a["classification"] == REQUEST
    assert "archive" not in a["actions"] and "needs_clarification" in a["actions"]  # OD-250
    assert "customer_request" in a["labels"] and a["earlier_answers"] == []
    events = [r[0] for r in conn.execute("SELECT actor FROM audit WHERE event ="
                                         " 'claude.message_read'")]  # fmt: skip
    assert events == ["mcp:session-", "mcp:session-"]


def test_the_classifier_gets_the_true_signals_under_their_own_key(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """OD-484 (plan item 5): the signals `signals.sent` gives the local classifier, as a
    top-level `signals` object in fixed wording; nothing else from the facts goes with them."""
    add(conn, clock, "c", "C")
    facts = KNOWN_BULK | {"sender_origin": "external", "bec_opener": True,
                          "impersonates_internal": True, "gift_card_keyword": True,
                          "sender_hash": "abc123",
                          "triggers": {"fraud": ["dana-chief"]}}  # fmt: skip
    c1 = waiting_item(conn, clock, "c", facts=facts)
    m = read_msg(conn, clock, S1, c1, token_for(queue(conn, clock), c1))
    names = ("bec_opener_outside", "impersonates_internal", "impersonation_gift_cards")
    assert signals.sent(facts) == names
    assert m["signals"] == {n: signals.WORDING[n] for n in names}
    rest = json.dumps({k: v for k, v in m.items() if k != "signals"})
    for leaked in ("abc123", "dana-chief", "sender_origin", "bec_opener", "An outside sender"):
        assert leaked not in rest


# Submitting: classifications are checked against the effective schema and go through the
# rules; proposals are checked like the local actor's; questions go to you.


@pytest.mark.usefixtures("extensions_on")
def test_the_classifier_gets_the_effective_schema_and_is_checked_against_it(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    sid = waiting_item(conn, clock, "c")
    extend(conn, clock)
    tok = token_for(queue(conn, clock), sid)
    m = read_msg(conn, clock, S1, sid, tok)
    assert m["schema_text"] == config.current_schema(conn).prompt_block
    assert "Where a contract discussed stands." in m["schema_text"]
    assert "legal_notice: Letter from a lawyer" in m["schema_text"]
    assert {"contract_stage", "lawyer_involved"} <= set(m["schema"]["properties"])
    assert "Where a contract" not in json.dumps(m["schema"])  # meanings only in schema_text
    first = submit_classification(conn, clock, S1, sid, tok, REQUEST)
    assert sorted(first["errors"]) == ["contract_stage: missing", "lawyer_involved: missing"]
    full = REQUEST | {"category": "legal_notice", "contract_stage": "draft",
                      "lawyer_involved": True}  # fmt: skip
    assert submit_classification(conn, clock, S1, sid, tok, full)["accepted"] is True
    assert json.loads(row(conn, sid)["classification"])["contract_stage"] == "draft"


def test_check_classification_takes_the_schema() -> None:
    base = load_schema()
    assert claude_review.check_classification(REQUEST, base) == (REQUEST, [])
    _, errors = claude_review.check_classification(REQUEST | {"contract_stage": "draft"}, base)
    assert errors == ["contract_stage: not a field of the schema"]


def test_a_claude_classification_goes_through_the_rules(conn: sqlite3.Connection,
                                                        clock: FakeClock) -> None:  # fmt: skip
    add(conn, clock, "c", "C")
    sid = waiting_item(conn, clock, "c")
    tok = token_for(queue(conn, clock), sid)
    got = submit_classification(conn, clock, S1, sid, tok, REQUEST)
    assert got == {"accepted": True, "errors": []}
    r = row(conn, sid)
    assert r["status"] == "awaiting_claude"  # the rule continues to the actor (OD-269)
    pinned = json.loads(r["pinned_models"])
    assert pinned["classifier"] == claude_pins.load_lock()["classifier"]
    assert pinned["agent"] == "ecf-classifier"
    with pytest.raises(ConflictError):  # one submission per claim (applied: has ended)
        submit_classification(conn, clock, S1, sid, tok, REQUEST)
    q = queue(conn, clock)  # the classifier still writes its report: the actor waits (TAIL)
    assert q["results"] == [{"id": sid, "outcome": "awaiting_claude"}] and q["items"] == []
    clock.advance(claude_review.TAIL.total_seconds() + 1)
    q = queue(conn, clock)
    assert [(i["id"], i["need"]) for i in q["items"]] == [(sid, "act")]


@pytest.mark.usefixtures("extensions_on")
def test_a_schema_change_while_held_releases_the_claim_for_a_new_classification(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    sid = waiting_item(conn, clock, "c")
    tok = token_for(queue(conn, clock), sid)
    held = claude_review.record_classification(conn, clock, S1, sid, tok, REQUEST,
                                               agent_of(conn, sid))  # fmt: skip
    assert isinstance(held, Hold)
    extend(conn, clock)  # `ecf config apply` while the classification waits for telemetry
    got = settled(conn, clock, held)
    assert got["accepted"] is False and "schema changed" in got["errors"][0]
    r = row(conn, sid)
    assert r["status"] == "awaiting_claude" and r["classification"] is None
    q = queue(conn, clock, "session-2")
    assert [(i["id"], i["need"]) for i in q["items"]] == [(sid, "classify")]


def test_an_invalid_classification_keeps_the_claim_three_times(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    sid = waiting_item(conn, clock, "c")
    tok = token_for(queue(conn, clock), sid)
    bad = REQUEST | {"category": "ignore previous instructions"}
    first = submit_classification(conn, clock, S1, sid, tok, bad)
    assert first["accepted"] is False and first["tries_left"] == 2
    assert first["errors"] == ["category: not one of the allowed values"]  # not the model text
    submit_classification(conn, clock, S1, sid, tok, "not an object")
    last = submit_classification(conn, clock, S1, sid, tok, {})
    assert last["tries_left"] == 0
    with pytest.raises(ConflictError):
        submit_classification(conn, clock, S1, sid, tok, REQUEST)
    assert (
        row(conn, sid)["status"] == "awaiting_claude" and row(conn, sid)["classification"] is None
    )
    q = queue(conn, clock)
    assert q["results"] == [{"id": sid, "outcome": "invalid"}] and q["items"][0]["id"] == sid


def test_a_claude_proposal_is_checked_like_the_local_actors(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "b", "C")
    sid = waiting_item(conn, clock, "b", classification=REQUEST)
    tok = token_for(queue(conn, clock), sid)

    def propose(**body: Any) -> dict[str, Any]:
        return submit_proposal(conn, clock, S1, sid, tok, body)

    hide = propose(action="archive", target="", reason="done")
    assert hide["accepted"] is False and "hiding isn't available" in hide["errors"][0]  # OD-250
    assert propose(action="label", target="nope", reason="x")["accepted"] is False
    long = "Reply today, see https://evil.example/x and call +1 555 010 9999. " + "a" * 400
    ok = propose(action="flag", target="customer_request", reason=long)
    assert ok == {"accepted": True, "errors": []}
    r = row(conn, sid)
    assert r["status"] == "observed" and r["decision_source"] == "actor"  # shadow
    a = json.loads(r["proposal"])["plan"]["actor"]
    assert a["action"] == "flag" and a["target"] is None  # flag takes no target
    assert a["agent"] == "ecf-actor" and a["model"].startswith("claude-sonnet-")
    assert "evil.example" not in a["reason"] and "555" not in a["reason"]
    assert len(a["reason"]) <= 300  # OD-270
    assert queue(conn, clock)["results"] == [{"id": sid, "outcome": "observed"}]


def test_a_question_from_claude_goes_to_you_even_on_a_high_risk_item(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "b", "C")
    sid = waiting_item(conn, clock, "b", classification=REQUEST,
                       facts=KNOWN_BULK | {"sender_seen_before": False})  # fmt: skip
    q = queue(conn, clock)
    assert q["items"][0]["agent"] == "ecf-actor-high"
    tok = token_for(q, sid)
    no_q = submit_proposal(conn, clock, S1, sid, tok,
                                        {"action": "needs_clarification", "target": "",
                                         "reason": "unsure"})  # fmt: skip
    assert no_q["accepted"] is False
    stray = submit_proposal(conn, clock, S1, sid, tok,
                                         {"action": "flag", "target": "", "reason": "x",
                                          "question": "why?"})  # fmt: skip
    assert stray["accepted"] is False
    ask = {"action": "needs_clarification", "target": "", "reason": "unsure",
           "question": "Is this Ann from Cust?"}  # fmt: skip
    submit_proposal(conn, clock, S1, sid, tok, ask)
    r = row(conn, sid)
    assert r["status"] == "needs_clarification"  # a question goes to you (§8.2)
    assert json.loads(r["proposal"])["question"] == "Is this Ann from Cust?"
    assert conn.execute("SELECT count(*) FROM escalations").fetchone()[0] == 0


def test_an_item_resolved_meanwhile_ends_the_claim(conn: sqlite3.Connection,
                                                   clock: FakeClock) -> None:  # fmt: skip
    add(conn, clock, "b", "C")
    sid = waiting_item(conn, clock, "b", classification=REQUEST)
    tok = token_for(queue(conn, clock), sid)
    items.transition(conn, clock, StableId(sid), Status.RESOLVED_MANUAL, TransitionContext(),
                     actor="os_user")  # fmt: skip
    with pytest.raises(ConflictError):
        submit_proposal(conn, clock, S1, sid, tok,
                                     {"action": "flag", "target": "", "reason": "x"})  # fmt: skip
    assert queue(conn, clock)["results"] == [{"id": sid, "outcome": "resolved_manual"}]


# The batch guard (§5.6): a batch with an unclassified or risky item is risky, and a hide in
# it needs approval.


def test_a_batch_with_an_unclassified_or_risky_item_is_risky(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    add(conn, clock, "h", "C", sensitivity="high")
    a, b = waiting_item(conn, clock, "c"), waiting_item(conn, clock, "c", 1)
    h = waiting_item(conn, clock, "h")
    q = queue(conn, clock)
    submit_classification(conn, clock, S1, a, token_for(q, a), ROUTINE)
    assert claude_queue.batch_risky(conn, a)  # b isn't classified yet: unknown is risky
    assert not claude_queue.batch_risky(conn, h)  # classifier-high: one per spawn, no batch
    submit_classification(conn, clock, S1, b, token_for(q, b),
                                        ROUTINE | {"category": "marketing"})  # fmt: skip
    # plain unsolicited bulk mail (marketing from S3b) doesn't make the batch risky (P14)
    assert not claude_queue.batch_risky(conn, a)
    with write_tx(conn):
        conn.execute("UPDATE items SET classification = ? WHERE stable_id = ?",
                     (json.dumps(ROUTINE), b))  # fmt: skip
    assert not claude_queue.batch_risky(conn, a)
    with write_tx(conn):  # the phishing item alone (OD-500, P14)
        conn.execute("UPDATE items SET classification = ? WHERE stable_id = ?",
                     (json.dumps(ROUTINE | {"phishing": True}), b))  # fmt: skip
    assert claude_queue.batch_risky(conn, a)


def test_a_hide_in_a_risky_batch_needs_approval() -> None:
    ctx = policy.Context(classification=ROUTINE | {"category": "marketing"}, facts=KNOWN_BULK,
                         sensitivity="standard", rules=load_starter_rules(load_schema()),
                         action_policy={}, move_folders=frozenset())  # fmt: skip
    labels = frozenset({"marketing"})
    plain = policy.proposal(ctx, policy.plan(ctx, labels), "archive", None, labels)
    risky = replace(ctx, batch_risky=True)
    guarded = policy.proposal(risky, policy.plan(risky, labels), "archive", None, labels)
    assert isinstance(plain, policy.Planned) and plain.mode == "auto"
    assert isinstance(guarded, policy.Planned) and guarded.mode == "approve"


# A mixed-up claim token (OD-499): the items involved are freed at once, up to a limit, and
# audited; other bad tokens keep today's refusal.


def claim_row(conn: sqlite3.Connection, sid: str) -> tuple[Any, ...]:
    return tuple(conn.execute("SELECT state, outcome FROM claims WHERE stable_id = ?",
                              (sid,)).fetchone())  # fmt: skip


def test_spawns_carry_the_tokens_per_address_and_items_none(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """`ecf-classifier-high` spawns group by address, up to its `classifier_high_batch`."""
    add(conn, clock, "h", "C", sensitivity="high")
    add(conn, clock, "k", "C", sensitivity="high")
    with write_tx(conn):
        conn.execute("UPDATE addresses SET overrides = ? WHERE address_id = 'h'",
                     (json.dumps({claude_batch.KEY: 2}),))  # fmt: skip
    h = [waiting_item(conn, clock, "h", n) for n in range(3)]
    k = [waiting_item(conn, clock, "k", n) for n in range(2)]
    q = queue(conn, clock)
    assert all("claim_token" not in i for i in q["items"])
    assert set(q["items"][0]) == {"id", "address_id", "need", "agent", "spawn"}
    assert {s["agent"] for s in q["spawns"]} == {"ecf-classifier-high"}
    ids = [[ln.split()[0].removeprefix("id=") for ln in s["prompt"].splitlines()]
           for s in q["spawns"]]  # fmt: skip
    assert ids == [h[:2], [h[2]], [k[0]], [k[1]]]
    assert len(q["spawns"]) == len({i["spawn"] for i in q["items"]})
    tok = tokens_of(q)  # every claimed item exactly once
    assert sorted(tok) == sorted(h + k)
    for sid, token in tok.items():
        assert read_msg(conn, clock, S1, sid, token)["id"] == sid
    assert queue(conn, clock, S2)["spawns"] == []


def test_a_mixed_up_token_frees_the_item_at_once(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    x, y = waiting_item(conn, clock, "c"), waiting_item(conn, clock, "c", 1)
    tok = tokens_of(queue(conn, clock))
    with pytest.raises(ConflictError, match="isn't current"):  # committed, then refused
        read_msg(conn, clock, S1, x, tok[y])
    assert claim_row(conn, x) == ("released", "claim_mismatch")
    assert claim_row(conn, y) == ("claimed", None)  # untouched
    with pytest.raises(ConflictError):  # X's own token is refused after the free
        read_msg(conn, clock, S1, x, tok[x])
    audit = conn.execute("SELECT event, outcome, data FROM audit WHERE stable_id = ?"
                         " AND event = 'claude.refused'", (x,)).fetchall()  # fmt: skip
    assert [(a[0], a[1]) for a in audit] == [("claude.refused", "denied")]
    assert json.loads(audit[0][2]) == {"need": "classify", "agent": "ecf-classifier",
                                       "claim_mismatch": True, "by": "ecf-classifier"}  # fmt: skip
    assert conn.execute("SELECT COUNT(*) FROM grants").fetchone()[0] == 0
    grace = claude_review._grace(S1, clock.now())  # pyright: ignore[reportPrivateUsage]
    assert grace == {"ecf-classifier": clock.now() + claude_review.TAIL}  # not GRACE
    q = queue(conn, clock)
    assert q["results"] == [{"id": x, "outcome": "claim_mismatch"}]
    assert [i["id"] for i in q["items"]] == [x]  # back at once, not after CLAIM_TTL
    assert submit_classification(conn, clock, S1, y, tok[y], REQUEST)["accepted"]


def test_a_two_way_swap_frees_both_items(conn: sqlite3.Connection, clock: FakeClock) -> None:
    """Review R1: the second item's agent presents a token the first free already ended."""
    add(conn, clock, "c", "C")
    x, y = waiting_item(conn, clock, "c"), waiting_item(conn, clock, "c", 1)
    tok = tokens_of(queue(conn, clock))
    for a, b in ((x, y), (y, x)):
        with pytest.raises(ConflictError):
            read_msg(conn, clock, S1, a, tok[b])
    assert claim_row(conn, x) == claim_row(conn, y) == ("released", "claim_mismatch")
    claude_review.release_session(conn, S1)
    assert S1 not in claude_review._FREED  # pyright: ignore[reportPrivateUsage]


def test_mix_up_frees_stop_after_mismatch_max(conn: sqlite3.Connection, clock: FakeClock) -> None:
    """Review R2: a session that keeps mangling an item gets today's expiry after 3 frees."""
    add(conn, clock, "c", "C")
    x = waiting_item(conn, clock, "c")
    for n in range(claude_review.MISMATCH_MAX + 1):
        tok = token_for(queue(conn, clock), x)
        with pytest.raises(ForbiddenProfileError):
            claude_review.get_message(conn, clock, S1, x, tok, "ecf-actor")
        clock.advance(claude_review.TAIL.total_seconds() + 1)
        assert claim_row(conn, x)[0] == ("released" if n < claude_review.MISMATCH_MAX
                                         else "claimed")  # fmt: skip


def test_a_failed_mismatch_audit_keeps_the_refusal(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review R5: the claim is freed and the agent still gets the conflict, not a 500."""
    add(conn, clock, "c", "C")
    x, y = waiting_item(conn, clock, "c"), waiting_item(conn, clock, "c", 1)
    tok = tokens_of(queue(conn, clock))

    def busy(*_a: Any, **_k: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(claude_review, "_audit", busy)
    with pytest.raises(ConflictError, match="isn't current"):
        read_msg(conn, clock, S1, x, tok[y])
    assert claim_row(conn, x) == ("released", "claim_mismatch")


def test_other_bad_tokens_keep_todays_refusal(conn: sqlite3.Connection, clock: FakeClock) -> None:
    """Only the mix-up signature frees (P1): a stale fence, garbage, another session's token and a
    held claim are refused and left to expire."""
    add(conn, clock, "c", "C")
    x, y, z = (waiting_item(conn, clock, "c", n) for n in range(3))
    old = token_for(queue(conn, clock, limit=1), x)
    clock.advance(claude_review.CLAIM_TTL.total_seconds() + 1)
    tok = tokens_of(queue(conn, clock, S2))
    for token in (old, "1.garbage", ""):
        with pytest.raises(ConflictError):
            read_msg(conn, clock, S2, x, token)
    with pytest.raises(ConflictError):  # another session presenting S2's tokens
        read_msg(conn, clock, S1, x, tok[y])
    assert claim_row(conn, x) == ("claimed", None)
    held = claude_review.record_classification(conn, clock, S2, y, tok[y], REQUEST,
                                               "ecf-classifier")  # fmt: skip
    assert isinstance(held, Hold)
    with pytest.raises(ConflictError):  # a held claim isn't freed by another item's token
        read_msg(conn, clock, S2, y, tok[z])
    assert claim_row(conn, y)[0] == "held" and claim_row(conn, z) == ("claimed", None)


def test_its_own_token_from_the_wrong_agent_type_frees_the_item(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    x = waiting_item(conn, clock, "c")
    tok = token_for(queue(conn, clock), x)
    with pytest.raises(ForbiddenProfileError, match="that claim is for"):
        claude_review.get_message(conn, clock, S1, x, tok, "ecf-actor")
    assert claim_row(conn, x) == ("released", "claim_mismatch")
    grace = claude_review._grace(S1, clock.now())  # pyright: ignore[reportPrivateUsage]
    assert grace == {"ecf-actor": clock.now() + claude_review.TAIL}
    tok = token_for(queue(conn, clock, S2), x)
    with pytest.raises(ForbiddenProfileError):  # another session's agent: refused, kept
        claude_review.get_message(conn, clock, S1, x, tok, "ecf-actor")
    assert claim_row(conn, x) == ("claimed", None)


# A dropped spawn (OD-501): a spawn none of whose claims was read is freed after UNREAD_MIN,
# fenced and audited; one with a read claim never is.


UNREAD_S = int(claude_review.UNREAD_MIN.total_seconds())


def _dropped(conn: sqlite3.Connection, clock: FakeClock) -> tuple[str, str, str, str]:
    """Round 1's spawn (x) is never made; round 2's (y) runs. x is freed `UNREAD_MIN` after the
    session's next call, and not before. x, its token, y, its token."""
    add(conn, clock, "c", "C")
    x, y = waiting_item(conn, clock, "c"), waiting_item(conn, clock, "c", 1)
    tx = token_for(queue(conn, clock, limit=1), x)
    clock.advance(5)
    ty = token_for(queue(conn, clock, limit=1), y)  # the session's next call
    read_msg(conn, clock, S1, y, ty)
    clock.advance(UNREAD_S - 1)
    q = queue(conn, clock, limit=1)
    assert q["items"] == [] and q["results"] == [] and claim_row(conn, x) == ("claimed", None)
    assert q["next"] == "call again" and q["frees_in_s"] == 1  # the free, not CLAIM_TTL (P7)
    clock.advance(1)
    return x, tx, y, ty


def test_a_dropped_spawn_is_freed_fenced_audited_and_reported(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    x, _tx, y, _ty = _dropped(conn, clock)
    before = tuple(row(conn, x))
    q = queue(conn, clock, limit=1)
    assert q["results"] == [{"id": x, "outcome": "claim_unread"}]
    assert claim_row(conn, y) == ("claimed", None)  # it runs
    assert x in {i["id"] for i in q["items"]}  # back at once
    audit = conn.execute("SELECT event, outcome, data FROM audit WHERE stable_id = ?"
                         " AND event = 'claude.refused'", (x,)).fetchall()  # fmt: skip
    assert [(a[0], a[1], json.loads(a[2])) for a in audit] == [
        ("claude.refused", "denied",
         {"need": "classify", "agent": "ecf-classifier", "claim_unread": True})]  # fmt: skip
    assert tuple(row(conn, x)) == before  # no item moves
    assert conn.execute("SELECT COUNT(*) FROM grants").fetchone()[0] == 0
    grace = claude_review._grace(S1, clock.now())  # pyright: ignore[reportPrivateUsage]
    assert grace == {"ecf-classifier": clock.now() + claude_review.TAIL}  # TAIL, not GRACE


def test_the_unread_free_is_fenced(conn: sqlite3.Connection, clock: FakeClock) -> None:
    """A claim that moved on since the spawn was judged is left alone."""
    add(conn, clock, "c", "C")
    x = waiting_item(conn, clock, "c")
    tok = token_for(queue(conn, clock), x)
    stale = conn.execute("SELECT * FROM claims WHERE stable_id = ?", (x,)).fetchone()
    assert submit_classification(conn, clock, S1, x, tok, REQUEST)["accepted"]
    claude_review._free_unread(conn, clock, S1, [stale], clock.now())  # pyright: ignore[reportPrivateUsage]
    assert conn.execute("SELECT outcome FROM claims WHERE stable_id = ?", (x,)).fetchone()[0] != (
        "claim_unread")  # fmt: skip
    assert not conn.execute("SELECT 1 FROM audit WHERE data LIKE '%claim_unread%'").fetchall()


def test_a_failed_unread_audit_frees_nothing(
    conn: sqlite3.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review R3: the frees and their audit rows are one transaction; a failure leaves the claim
    for the next call."""
    x, _tx, _y, _ty = _dropped(conn, clock)

    def busy(*_a: Any, **_k: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(claude_review, "_audit_row", busy)
    assert queue(conn, clock, limit=1)["results"] == []
    assert claim_row(conn, x) == ("claimed", None)
    monkeypatch.undo()
    assert queue(conn, clock, limit=1)["results"] == [{"id": x, "outcome": "claim_unread"}]


def test_a_spawn_with_any_claim_read_is_never_freed_unread(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    x, y = waiting_item(conn, clock, "c"), waiting_item(conn, clock, "c", 1)
    q = queue(conn, clock)
    assert len(q["spawns"]) == 1  # one spawn of two
    read_msg(conn, clock, S1, x, token_for(q, x))
    clock.advance(5)
    queue(conn, clock)
    clock.advance(4 * UNREAD_S)
    q = queue(conn, clock)
    assert q["results"] == [] and claim_row(conn, y) == ("claimed", None)  # waits, as today
    assert q["frees_in_s"] == int(claude_review.CLAIM_TTL.total_seconds()) - 5 - 4 * UNREAD_S


def test_a_reclaimed_siblings_new_claim_doesnt_make_a_running_spawn_look_unread(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """P3: the spawn is judged over every claim it handed out, not each item's latest."""
    add(conn, clock, "c", "C")
    x, y = waiting_item(conn, clock, "c"), waiting_item(conn, clock, "c", 1)
    tok = tokens_of(queue(conn, clock))
    read_msg(conn, clock, S1, y, tok[y])
    with pytest.raises(ForbiddenProfileError):  # y's claim freed (mix-up), its read stays
        claude_review.get_message(conn, clock, S1, y, tok[y], "ecf-actor")
    clock.advance(claude_review.TAIL.total_seconds() + 1)
    assert y in {i["id"] for i in queue(conn, clock)["items"]}  # claimed again, a new spawn
    clock.advance(UNREAD_S + 1)
    assert queue(conn, clock)["results"] == []
    assert claim_row(conn, x) == ("claimed", None)
    assert conn.execute("SELECT fence FROM claims WHERE stable_id = ?", (x,)).fetchone()[0] == 1


def test_another_sessions_calls_dont_count_for_review(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    x = waiting_item(conn, clock, "c")
    for n in range(3):
        waiting_item(conn, clock, "c", n + 1)
    queue(conn, clock, limit=1)
    for _ in range(3):
        clock.advance(UNREAD_S)
        queue(conn, clock, S2, limit=1)
    assert claim_row(conn, x) == ("claimed", None)  # S1 hasn't called since
    queue(conn, clock, limit=1)
    clock.advance(UNREAD_S)
    queue(conn, clock, S2, limit=1)  # S1's signal holds, but only S1's own call frees it
    assert claim_row(conn, x) == ("claimed", None)
    assert queue(conn, clock, limit=1)["results"] == [{"id": x, "outcome": "claim_unread"}]


def test_a_late_agents_refused_call_extends_tail_for_review(
    conn: sqlite3.Connection, clock: FakeClock, capsys: pytest.CaptureFixture[str]
) -> None:
    """P2: its request is refused, its type counts as out for `TAIL` from then, no mix-up."""
    x, tx, y, _ty = _dropped(conn, clock)
    assert x in {i["id"] for i in queue(conn, clock, limit=1)["items"]}  # a new token
    clock.advance(claude_review.TAIL.total_seconds() + 5)
    with pytest.raises(ConflictError, match="isn't current"):
        read_msg(conn, clock, S1, x, tx)
    grace = claude_review._grace(S1, clock.now())  # pyright: ignore[reportPrivateUsage]
    assert grace == {"ecf-classifier": clock.now() + claude_review.TAIL}
    assert "claude.claim_unread_late" in capsys.readouterr().out  # review R1, once per token
    with pytest.raises(ConflictError):
        read_msg(conn, clock, S1, x, tx)
    assert "claim_unread_late" not in capsys.readouterr().out
    with pytest.raises(ConflictError):  # nor for another item
        read_msg(conn, clock, S1, y, tx)
    assert claim_row(conn, x) == claim_row(conn, y) == ("claimed", None)


def test_a_mix_up_alone_counts_as_an_agent_running_for_review(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """The only call under the spawn is y's own token from the wrong agent type (P6)."""
    add(conn, clock, "c", "C")
    x, y = waiting_item(conn, clock, "c"), waiting_item(conn, clock, "c", 1)
    tok = tokens_of(queue(conn, clock))
    with pytest.raises(ForbiddenProfileError):
        claude_review.get_message(conn, clock, S1, y, tok[y], "ecf-actor")
    clock.advance(claude_review.TAIL.total_seconds() + 1)
    queue(conn, clock)
    clock.advance(UNREAD_S + 1)
    assert queue(conn, clock)["results"] == [] and claim_row(conn, x) == ("claimed", None)


def test_a_wrong_need_first_call_counts_as_an_agent_running_for_review(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """Review R2: the stamp is set before the need check."""
    add(conn, clock, "c", "C")
    x = waiting_item(conn, clock, "c")
    tok = token_for(queue(conn, clock, limit=1), x)
    with pytest.raises(ConflictError, match="is to classify"):
        claude_review.propose_action(conn, clock, S1, x, tok, {"action": "flag", "reason": "r"},
                                     "ecf-classifier")  # fmt: skip
    clock.advance(5)
    queue(conn, clock, limit=1)
    clock.advance(UNREAD_S + 1)
    assert queue(conn, clock, limit=1)["results"] == [] and claim_row(conn, x) == ("claimed", None)


def test_the_spawn_memory_is_pruned_once_no_claim_of_it_is_out(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """`_CALLED` and `_NEXT_CALL` keep a spawn's entries only while one of its claims is out."""
    add(conn, clock, "c", "C")
    x, y = waiting_item(conn, clock, "c"), waiting_item(conn, clock, "c", 1)
    tok = tokens_of(queue(conn, clock))
    read_msg(conn, clock, S1, x, tok[x])
    clock.advance(1)
    queue(conn, clock)  # round 1's next call
    called, rounds = claude_review._CALLED, claude_review._NEXT_CALL  # pyright: ignore[reportPrivateUsage]
    assert len(called) == 1 and len(rounds) == 1
    assert submit_classification(conn, clock, S1, x, tok[x], REQUEST)["accepted"]
    queue(conn, clock)
    assert len(called) == 1 and len(rounds) == 1  # y still out: kept
    assert submit_classification(conn, clock, S1, y, tok[y], REQUEST)["accepted"]
    queue(conn, clock)
    assert called == {} and rounds == {}


def test_an_items_own_earlier_token_never_frees_its_new_claim(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """P4: a mix-up freed x; its late agent presents x's old token after x was claimed again."""
    add(conn, clock, "c", "C")
    x = waiting_item(conn, clock, "c")
    old = token_for(queue(conn, clock), x)
    with pytest.raises(ForbiddenProfileError):
        claude_review.get_message(conn, clock, S1, x, old, "ecf-actor")
    clock.advance(claude_review.TAIL.total_seconds() + 1)
    new = token_for(queue(conn, clock), x)
    with pytest.raises(ConflictError, match="isn't current"):
        read_msg(conn, clock, S1, x, old)
    assert claim_row(conn, x) == ("claimed", None)
    assert submit_classification(conn, clock, S1, x, new, REQUEST)["accepted"]


# The progress line counts this session's rounds, frees, late agents and mix-ups; the review
# routes follow.


def test_the_progress_line_counts_this_sessions_rounds(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    x, y, _z = (waiting_item(conn, clock, "c", n) for n in range(3))
    tok = tokens_of(q := queue(conn, clock, limit=2))
    assert q["progress"] == "0 done of 2 handed out, 2 in progress, 1 waiting, round 1"
    assert submit_classification(conn, clock, S1, x, tok[x], REQUEST)["accepted"]
    q = queue(conn, clock, limit=1)
    # x, classified, now waits for its actor
    assert q["progress"] == "1 done of 3 handed out, 2 in progress, 1 waiting, round 2"
    q = queue(conn, clock, S2)  # each session's own counts: it takes x's act
    assert q["progress"] == "0 done of 1 handed out, 1 in progress, 0 waiting, round 1"
    assert claim_row(conn, y) == ("claimed", None)


def test_the_progress_line_counts_frees_and_late_agents(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    x, tx, _y, _ty = _dropped(conn, clock)
    q = queue(conn, clock, limit=1)
    assert q["progress"].endswith("round 3, 1 unread claims freed")
    with pytest.raises(ConflictError):
        read_msg(conn, clock, S1, x, tx)  # the late agent: counted once per token
    with pytest.raises(ConflictError):
        read_msg(conn, clock, S1, x, tx)
    q = queue(conn, clock, limit=1)
    assert q["progress"].endswith(", 1 unread claims freed, 1 late after an unread free")


def test_the_progress_line_counts_mix_ups(conn: sqlite3.Connection, clock: FakeClock) -> None:
    add(conn, clock, "c", "C")
    x, y = waiting_item(conn, clock, "c"), waiting_item(conn, clock, "c", 1)
    tok = tokens_of(queue(conn, clock))
    with pytest.raises(ConflictError, match="isn't current"):
        read_msg(conn, clock, S1, x, tok[y])
    assert queue(conn, clock)["progress"].endswith(", 1 mixed-up claims freed")


def call(state: ServiceState, path: str, body: Any, token: str) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.post(path, json=body, headers={"Authorization": f"Bearer {token}"})

    return anyio.run(go)


def test_the_routes_take_only_a_session(conn: sqlite3.Connection, db_path: Path) -> None:
    clock = FakeClock()
    add(conn, clock, "c", "C")
    sid = waiting_item(conn, clock, "c")
    state = ServiceState(install="t", token="cli-token", started_at=to_ts(clock.now()),
                         clock=clock, db_path=db_path)  # fmt: skip
    made = call(state, "/v1/sessions", None, "cli-token").json()
    assert made["waiting"] == 1  # what `ecf claude` prints, and the pins its plugin uses
    assert made["models"] == claude_pins.effective(conn)
    work, agent = made["profile_token"], made["agent_token"]
    refused = call(state, "/v1/review-queue", {}, "cli-token")
    assert refused.status_code == 403 and refused.json()["code"] == "forbidden_profile"
    q = call(state, "/v1/review-queue", {"limit": 5}, work).json()
    tok, name = token_for(q, sid), q["items"][0]["agent"]
    state.telemetry_wait_s = 0
    state.telemetry.add(made["session_id"], [ApiCall(clock.now().timestamp() + 1,
                                                     claude_pins.effective(conn)["classifier"],
                                                     SUBAGENT, duration_ms=3000)], [])  # fmt: skip
    main = call(state, f"/v1/claims/{sid}/message", {"claim_token": tok, "agent": name}, work)
    assert main.status_code == 403  # the main session's token: no message text (OD-307)
    m = call(state, f"/v1/claims/{sid}/message", {"claim_token": tok, "agent": name}, agent)
    assert m.status_code == 200 and m.json()["untrusted_email"]["subject"] == "W-9 please"
    stale = call(state, f"/v1/claims/{sid}/message", {"claim_token": "9.x", "agent": name}, agent)
    assert stale.status_code == 409 and stale.json()["code"] == "conflict"
    r = call(state, f"/v1/claims/{sid}/classification",
             {"claim_token": tok, "classification": REQUEST, "agent": name}, agent)  # fmt: skip
    assert r.json() == {"accepted": True, "errors": []}
    for token in (work, agent):
        for path in ("/v1/items/resolve", "/v1/settings", "/v1/approvals/pending"):
            assert call(state, path, {}, token).json()["code"] == "forbidden_profile"
    gone = state.sessions[made["session_id"]]
    assert gone.profile.value == "work"

    async def revoke() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.delete(f"/v1/sessions/{made['session_id']}",
                                  headers={"Authorization": "Bearer cli-token"})  # fmt: skip

    clock.advance(claude_review.TAIL.total_seconds() + 1)  # the classifier's tail
    q2 = call(state, "/v1/review-queue", {}, work).json()
    assert [i["need"] for i in q2["items"]] == ["act"]
    assert anyio.run(revoke).status_code == 200
    assert conn.execute("SELECT state FROM claims").fetchone()[0] == "released"
