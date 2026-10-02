"""Claims and submissions for `/ecf-review` (V1.4 step 3; SPEC §10.4, §15.1; OD-267, OD-270):
claiming and agents, claim expiry and fencing, the untrusted message, submissions checked like the
local model's, the batch guard, and the routes' profiles."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from ecf.errors import ConflictError, InvalidInputError, NotFoundError
from ecf.ids import AddressId, StableId
from ecf.schema import load_schema_v1
from ecf_server import claude_pins, claude_queue, claude_review, decide, items, policy
from ecf_server.api import ServiceState, create_app
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import NullNotifier
from ecf_server.rules import load_starter_rules
from ecf_server.state_machine import Status, TransitionContext
from ecf_server.telemetry import SUBAGENT, ApiCall, Hold, Seen, Telemetry
from tests.test_decide import KNOWN_BULK, MARKETING

REQUEST: dict[str, Any] = MARKETING | {"category": "customer_request", "requires_reply": True,
                                       "requires_action": True}  # fmt: skip
ROUTINE: dict[str, Any] = REQUEST | {"requires_reply": False, "requires_action": False}
S1, S2 = "session-one", "session-two"


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


def token_for(q: dict[str, Any], sid: str) -> str:
    return next(i["claim_token"] for i in q["items"] if i["id"] == sid)


# Telemetry stands in for these helpers: a plugin agent on its pinned model (V1.4 step 6).
AGENT_CALL = Seen("claude-haiku-4-5-20251001", SUBAGENT)


def read_msg(conn: sqlite3.Connection, clock: FakeClock, session: str, sid: str,
             token: str) -> dict[str, Any]:  # fmt: skip
    return claude_review.get_message(conn, clock, session, sid, token, lambda: AGENT_CALL)


def settled(conn: sqlite3.Connection, clock: FakeClock,
            got: dict[str, Any] | Hold) -> dict[str, Any]:  # fmt: skip
    if not isinstance(got, Hold):
        return got
    pin = claude_pins.effective(conn)[claude_review.ROLE[got.agent]]
    return claude_review.settle(conn, clock, NullNotifier(), Telemetry(), got,
                                Seen(pin, SUBAGENT))  # fmt: skip


def submit_classification(conn: sqlite3.Connection, clock: FakeClock, session: str, sid: str,
                          token: str, c: Any) -> dict[str, Any]:  # fmt: skip
    return settled(conn, clock, claude_review.record_classification(
        conn, clock, session, sid, token, c, "toolu_test"))  # fmt: skip


def submit_proposal(conn: sqlite3.Connection, clock: FakeClock, session: str, sid: str,
                    token: str, body: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
    return settled(conn, clock, claude_review.propose_action(
        conn, clock, session, sid, token, body, "toolu_test"))  # fmt: skip


# ---- claiming --------------------------------------------------------------------------------


def test_the_queue_claims_waiting_items_and_names_their_agent(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "c", "C")
    add(conn, clock, "h", "C", sensitivity="high")
    add(conn, clock, "b", "B")
    add(conn, clock, "a", "A")
    c1 = waiting_item(conn, clock, "c")
    h1 = waiting_item(conn, clock, "h")
    b1 = waiting_item(conn, clock, "b", classification=REQUEST)
    b2 = waiting_item(conn, clock, "b", 1, classification=REQUEST,
                      facts=KNOWN_BULK | {"sender_seen_before": False})  # fmt: skip
    q = queue(conn, clock)
    got = {i["id"]: (i["need"], i["agent"]) for i in q["items"]}
    assert got == {c1: ("classify", "ecf:classifier"), h1: ("classify", "ecf:classifier-high"),
                   b1: ("act", "ecf:actor"), b2: ("act", "ecf:actor-high")}  # fmt: skip
    assert [i["id"] for i in q["items"]] == [c1, h1, b1, b2]  # oldest first
    assert q["more"] is False and q["results"] == []
    assert all(i["claim_token"].startswith("1.") for i in q["items"])
    assert queue(conn, clock)["items"] == []  # claimed: not handed out again
    assert queue(conn, clock, S2)["items"] == []  # nor to another session


def test_limit_more_address_and_paused(conn: sqlite3.Connection, clock: FakeClock) -> None:
    add(conn, clock, "c", "C")
    add(conn, clock, "d", "C")
    sids = [waiting_item(conn, clock, "c", n) for n in range(3)]
    d1 = waiting_item(conn, clock, "d")
    q = queue(conn, clock, limit=2, address="c@acme.example")
    assert [i["id"] for i in q["items"]] == sids[:2] and q["more"] is True
    with write_tx(conn):
        conn.execute("UPDATE addresses SET paused = 1 WHERE address_id = 'c'")
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


# ---- reading ---------------------------------------------------------------------------------


def test_the_message_is_wrapped_and_carries_no_facts(conn: sqlite3.Connection,
                                                      clock: FakeClock) -> None:  # fmt: skip
    add(conn, clock, "c", "C")
    add(conn, clock, "b", "B")
    c1 = waiting_item(conn, clock, "c")
    b1 = waiting_item(conn, clock, "b", classification=REQUEST)
    q = queue(conn, clock)
    m = read_msg(conn, clock, S1, c1, token_for(q, c1))
    assert m["notice"] == claude_review.NOTICE and m["need"] == "classify"
    assert m["untrusted_email"] == {
        "from": "Ann <ann@cust.example>", "subject": "W-9 please", "date": "2026-10-02T09:00Z",
        "text": "short text",
        "attachments_meta": [{"name": "w9.pdf", "type": "application/pdf", "size": 1200}],
    }  # fmt: skip
    assert "category" in json.dumps(m["schema"])
    flat = json.dumps(m)
    for fact in ("sender_seen_before", "auth_result", "bulk_corroborates", "triggers"):
        assert fact not in flat  # §7.2: computed facts never go to a model
    a = read_msg(conn, clock, S1, b1, token_for(q, b1))
    assert a["untrusted_email"]["text"].startswith("Please send the W-9")  # the actor excerpt
    assert a["classification"] == REQUEST
    assert "archive" not in a["actions"] and "needs_clarification" in a["actions"]  # OD-250
    assert "customer_request" in a["labels"] and a["earlier_answers"] == []
    events = [r[0] for r in conn.execute("SELECT actor FROM audit WHERE event ="
                                         " 'claude.message_read'")]  # fmt: skip
    assert events == ["mcp:session-", "mcp:session-"]


# ---- submitting ------------------------------------------------------------------------------


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
    assert pinned["classifier"].startswith("claude-haiku-") and pinned["agent"] == "ecf:classifier"
    with pytest.raises(ConflictError):  # one submission per claim
        submit_classification(conn, clock, S1, sid, tok, REQUEST)
    q = queue(conn, clock)
    assert q["results"] == [{"id": sid, "outcome": "awaiting_claude"}]
    assert [(i["id"], i["need"]) for i in q["items"]] == [(sid, "act")]


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
    add(conn, clock, "b", "B")
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
    assert a["agent"] == "ecf:actor" and a["model"].startswith("claude-sonnet-")
    assert "evil.example" not in a["reason"] and "555" not in a["reason"]
    assert len(a["reason"]) <= 300  # OD-270
    assert queue(conn, clock)["results"] == [{"id": sid, "outcome": "observed"}]


def test_a_question_from_claude_goes_to_you_even_on_a_high_risk_item(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add(conn, clock, "b", "B")
    sid = waiting_item(conn, clock, "b", classification=REQUEST,
                       facts=KNOWN_BULK | {"sender_seen_before": False})  # fmt: skip
    q = queue(conn, clock)
    assert q["items"][0]["agent"] == "ecf:actor-high"
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
    assert r["status"] == "needs_clarification"  # local_high_risk is the local pair's (§8.2)
    assert json.loads(r["proposal"])["question"] == "Is this Ann from Cust?"
    assert conn.execute("SELECT count(*) FROM escalations").fetchone()[0] == 0


def test_an_item_resolved_meanwhile_ends_the_claim(conn: sqlite3.Connection,
                                                   clock: FakeClock) -> None:  # fmt: skip
    add(conn, clock, "b", "B")
    sid = waiting_item(conn, clock, "b", classification=REQUEST)
    tok = token_for(queue(conn, clock), sid)
    items.transition(conn, clock, StableId(sid), Status.RESOLVED_MANUAL, TransitionContext(),
                     actor="os_user")  # fmt: skip
    with pytest.raises(ConflictError):
        submit_proposal(conn, clock, S1, sid, tok,
                                     {"action": "flag", "target": "", "reason": "x"})  # fmt: skip
    assert queue(conn, clock)["results"] == [{"id": sid, "outcome": "resolved_manual"}]


# ---- the batch guard (§5.6) ---------------------------------------------------------------------


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
                                        ROUTINE | {"category": "spam_or_phishing"})  # fmt: skip
    assert claude_queue.batch_risky(conn, a)
    with write_tx(conn):
        conn.execute("UPDATE items SET classification = ? WHERE stable_id = ?",
                     (json.dumps(ROUTINE), b))  # fmt: skip
    assert not claude_queue.batch_risky(conn, a)


def test_a_hide_in_a_risky_batch_needs_approval() -> None:
    ctx = policy.Context(classification=ROUTINE | {"category": "marketing"}, facts=KNOWN_BULK,
                         sensitivity="standard", rules=load_starter_rules(load_schema_v1()),
                         action_policy={}, move_folders=frozenset())  # fmt: skip
    labels = frozenset({"marketing"})
    plain = policy.proposal(ctx, policy.plan(ctx, labels), "archive", None, labels)
    risky = replace(ctx, batch_risky=True)
    guarded = policy.proposal(risky, policy.plan(risky, labels), "archive", None, labels)
    assert isinstance(plain, policy.Planned) and plain.mode == "auto"
    assert isinstance(guarded, policy.Planned) and guarded.mode == "approve"


# ---- routes ----------------------------------------------------------------------------------


def call(state: ServiceState, path: str, body: Any, token: str) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.post(path, json=body, headers={"Authorization": f"Bearer {token}"})

    return anyio.run(go)


def test_the_routes_take_only_a_work_session(conn: sqlite3.Connection, db_path: Path) -> None:
    clock = FakeClock()
    add(conn, clock, "c", "C")
    sid = waiting_item(conn, clock, "c")
    state = ServiceState(install="t", token="cli-token", started_at=to_ts(clock.now()),
                         clock=clock, db_path=db_path)  # fmt: skip
    made = call(state, "/v1/sessions", None, "cli-token").json()
    assert made["waiting"] == 1  # what `ecf claude` prints, and the pins its plugin uses
    assert made["models"] == claude_pins.effective(conn)
    work = made["profile_token"]
    refused = call(state, "/v1/review-queue", {}, "cli-token")
    assert refused.status_code == 403 and refused.json()["code"] == "forbidden_profile"
    q = call(state, "/v1/review-queue", {"limit": 5}, work).json()
    tok = q["items"][0]["claim_token"]
    state.telemetry_wait_s = 0
    state.telemetry.add(made["session_id"], [ApiCall(1, AGENT_CALL.model, SUBAGENT)],
                        {"toolu_1": 2})  # fmt: skip
    unbound = call(state, f"/v1/claims/{sid}/message", {"claim_token": tok}, work)
    assert unbound.status_code == 403  # no tool-use ID: telemetry can't vouch for the caller
    m = call(state, f"/v1/claims/{sid}/message", {"claim_token": tok, "tool_use_id": "toolu_1"},
             work)  # fmt: skip
    assert m.status_code == 200 and m.json()["untrusted_email"]["subject"] == "W-9 please"
    stale = call(state, f"/v1/claims/{sid}/message", {"claim_token": "9.x"}, work)
    assert stale.status_code == 409 and stale.json()["code"] == "conflict"
    r = call(state, f"/v1/claims/{sid}/classification",
             {"claim_token": tok, "classification": REQUEST, "tool_use_id": "toolu_1"},
             work)  # fmt: skip
    assert r.json() == {"accepted": True, "errors": []}
    for path in ("/v1/items/resolve", "/v1/settings", "/v1/approvals/pending"):
        assert call(state, path, {}, work).json()["code"] == "forbidden_profile"  # no decisions
    gone = state.sessions[made["session_id"]]
    assert gone.profile.value == "work"

    async def revoke() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as c:
            return await c.delete(f"/v1/sessions/{made['session_id']}",
                                  headers={"Authorization": "Bearer cli-token"})  # fmt: skip

    q2 = call(state, "/v1/review-queue", {}, work).json()
    assert [i["need"] for i in q2["items"]] == ["act"]
    assert anyio.run(revoke).status_code == 200
    assert conn.execute("SELECT state FROM claims").fetchone()[0] == "released"
