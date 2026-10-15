"""Jev's unsure band and confidence bars in the service (v3.0.0 step 4 Phase B; OD-511): the drain
stores `facts.jev` beside the classification, every risk reader reads an unsure field as yes, the
hold drops every hide on such a case, the card names it, review accuracy counts a Fix to yes as
correct, a mapping change moves D's key; and nothing is ever less cautious with the mapping than
without it."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ecf.schema import FIXED_ITEMS
from ecf_server import (
    actor,
    alert_items,
    cards,
    claude_pins,
    claude_queue,
    decide,
    jev_dispatch,
    jev_facts,
    jev_keys,
    jev_mapping,
    policy,
    precheck,
    review,
    stages,
)
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.policy import Context
from ecf_server.rules import HIDE_ACTIONS
from tests.test_claude_review import row
from tests.test_decide import KNOWN_BULK, MARKETING, make_address, make_classified
from tests.test_jev_dispatch import FIXTURE, add_d, d_item, drain, ok, rig
from tests.test_policy import CONTEXTS, SCHEMA

FIXED = dict.fromkeys(FIXED_ITEMS, False)
QUIET = MARKETING | FIXED  # a corroborated marketing email: the starter rules archive it


def _banded(monkeypatch: pytest.MonkeyPatch, **doc: Any) -> jev_mapping.JevMapping:
    m = jev_mapping.parse({"bands": {}, "bars": {}, "none_bars": {}} | doc)
    monkeypatch.setattr(jev_mapping, "load", lambda: m)
    return m


def _jev(unsure: tuple[str, ...] = (), below: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"jev": jev_facts.record(jev_facts.Derived(frozenset(unsure), frozenset(below)),
                                    None, None)}  # fmt: skip


def _plan(conn: sqlite3.Connection, sid: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(row(conn, sid)["proposal"])
    return doc


def _names(doc: dict[str, Any]) -> set[str]:
    return {a["name"] for a in doc["plan"]["actions"]}


# The drain stores `facts.jev` beside the classification; the stored yes stays p >= 0.5.


def test_the_drain_stores_jevs_caution_beside_the_answer(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _banded(monkeypatch, bands={"phishing": 0.3}, bars={"category": 0.95})
    add_d(conn, clock)
    sid = d_item(conn, clock)
    answers = json.loads(json.dumps(FIXTURE["body"]["answers"]))
    answers["phishing"] = {"noul": 0.4}  # a no today; unsure under a 0.3 band
    live, _script, _waits = rig(ok(answers=answers))
    assert drain(conn, clock, live).ok == 1
    r = row(conn, sid)
    cls = json.loads(r["classification"])
    facts = json.loads(r["facts"])
    assert cls["phishing"] is False and "jev" not in cls  # the classification as before
    j = facts["jev"]
    assert j["unsure"] == ["phishing"] and j["below_bar"] == ["category"]  # 0.9 < 0.95
    assert j["probabilities"]["phishing"] == {"true": 0.4}
    assert j["confidence"]["category"] == 0.82
    assert all(isinstance(v, float) for p in j["probabilities"].values() for v in p.values())
    assert "unsure: phishing; below bar: category" in str(cards.classified_line(r))


def test_with_the_shipped_mapping_the_drain_stores_no_caution(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add_d(conn, clock)
    sid = d_item(conn, clock)
    live, _script, _waits = rig(ok())
    drain(conn, clock, live)
    j = json.loads(row(conn, sid)["facts"])["jev"]
    assert (j["unsure"], j["below_bar"]) == ([], [])
    assert j["probabilities"]["payment_related"] == {"true": 0.8}
    assert ";" not in str(cards.classified_line(row(conn, sid)))


def test_a_claude_store_drops_an_earlier_jev_caution(conn: sqlite3.Connection,
                                                     clock: FakeClock) -> None:  # fmt: skip
    """An address moved from D to C: Claude's classification is never read with Jev's caution."""
    from ecf_server import classifier  # noqa: PLC0415
    from tests.test_claude_review import add, waiting_item  # noqa: PLC0415

    add(conn, clock, "c", "C")
    sid = waiting_item(conn, clock, "c", facts=KNOWN_BULK | _jev(("phishing",)))
    from ecf_server.state_machine import Status  # noqa: PLC0415

    assert classifier.store(conn, clock, sid, QUIET, {"classifier": "claude-sonnet-5-5"},
                            expected=Status.AWAITING_CLAUDE)  # fmt: skip
    assert "jev" not in json.loads(row(conn, sid)["facts"])


# The hold in the service: every hide dropped, the email left and flagged.


@pytest.mark.parametrize(("unsure", "below"), [(("phishing",), ()),
                                                (("requires_action",), ()),
                                                (("fraud_risk",), ()),
                                                ((), ("sender_type",)),
                                                ((), ("category",))])  # fmt: skip
def test_a_held_case_hides_nothing_and_is_flagged(
    conn: sqlite3.Connection, clock: FakeClock, unsure: tuple[str, ...], below: tuple[str, ...]
) -> None:  # fmt: skip
    make_address(conn, clock, "live")
    plain = make_classified(conn, clock, QUIET, KNOWN_BULK, sid="a")
    decide.apply(conn, clock, plain)
    assert "archive" in _names(_plan(conn, plain))  # today's plan hides it
    sid = make_classified(conn, clock, QUIET, KNOWN_BULK | _jev(unsure, below), sid="b")
    decide.apply(conn, clock, sid)
    names = _names(_plan(conn, sid))
    assert not names & HIDE_ACTIONS and {"flag", "leave"} <= names


def test_a_below_bar_field_no_hide_reads_holds_nothing(conn: sqlite3.Connection,
                                                       clock: FakeClock) -> None:  # fmt: skip
    """`category` under its bar holds only the hides of rules that read it (step 3a D4)."""
    from ecf_server import rules  # noqa: PLC0415

    starter = rules.load_starter_rules(SCHEMA)
    ctx = Context(QUIET, KNOWN_BULK | _jev(below=("category",)), "standard", starter, {},
                  frozenset())  # fmt: skip
    p = policy.plan(ctx, policy.labels(SCHEMA, starter))
    unread = dataclasses.replace(p, rule_id="otherwise", to_actor=False)
    assert jev_mapping.reasons(unread, jev_facts.derived(ctx.facts), starter) == []
    assert jev_mapping.reasons(p, jev_facts.derived(ctx.facts), starter) == [
        "below bar: category"]  # fmt: skip


def test_a_persons_fix_settles_an_unsure_field(conn: sqlite3.Connection,
                                               clock: FakeClock) -> None:  # fmt: skip
    make_address(conn, clock, "live")
    sid = make_classified(conn, clock, QUIET, KNOWN_BULK | _jev(("phishing",)))
    with write_tx(conn):
        conn.execute("UPDATE items SET human_correction = ? WHERE stable_id = ?",
                     (json.dumps({"phishing": False}), sid))  # fmt: skip
    ctx = decide.context(conn, row(conn, sid))
    assert not jev_facts.derived(ctx.facts)


# Every reader: unsure reads as yes, `fraud_risk` as low; without `facts.jev`, as today.


def test_the_readers_see_an_unsure_field_as_yes(conn: sqlite3.Connection,
                                                clock: FakeClock) -> None:  # fmt: skip
    make_address(conn, clock, "shadow")
    plain = make_classified(conn, clock, QUIET, KNOWN_BULK, sid="a")
    pay = make_classified(conn, clock, QUIET, KNOWN_BULK | _jev(("payment_related",)), sid="b")
    phish = make_classified(conn, clock, QUIET, KNOWN_BULK | _jev(("phishing",)), sid="c")
    fraud = make_classified(conn, clock, QUIET, KNOWN_BULK | _jev(("fraud_risk",)), sid="d")
    bank = make_classified(conn, clock, QUIET, KNOWN_BULK | _jev(("bank_detail_change",)),
                           sid="e")  # fmt: skip
    rows = {
        k: row(conn, s)
        for k, s in {
            "plain": plain,
            "pay": pay,
            "phish": phish,
            "fraud": fraud,
            "bank": bank,
        }.items()
    }
    # step-up and the Approve-all exclusion (answers, approvals, inbox): payment or fraud
    assert [precheck.item_payment_or_fraud(rows[k]) for k in ("plain", "pay", "fraud")] == [
        False, True, False]  # fmt: skip  # fraud_risk unsure is `low`: not medium or high
    # own review
    assert not review.needs_own_review(conn, rows["plain"])
    assert all(review.needs_own_review(conn, rows[k]) for k in ("pay", "phish", "fraud", "bank"))
    # alert items
    kind = alert_items._kind  # pyright: ignore[reportPrivateUsage]
    assert kind(rows["plain"]) == (None, "") and kind(rows["bank"])[0] == alert_items.FRAUD
    # the policy: high risk, payment or fraud, the approval mode of a hide
    for k in ("pay", "phish", "fraud", "bank"):
        ctx = decide.context(conn, rows[k])
        assert policy.high_risk(ctx) or policy.hide_blockers(ctx), k
    assert policy.payment_or_fraud(decide.context(conn, rows["pay"]))
    assert policy.fraud_signal(decide.context(conn, rows["fraud"]))
    assert "phishing" in policy.hide_blockers(decide.context(conn, rows["phish"]))
    assert policy._mode(decide.context(conn, rows["phish"]), "junk",  # pyright: ignore[reportPrivateUsage]
                        False) == "approve"  # fmt: skip


def test_a_batch_with_an_unsure_phishing_item_is_risky(conn: sqlite3.Connection,
                                                       clock: FakeClock) -> None:  # fmt: skip
    make_address(conn, clock, "shadow")
    a = make_classified(conn, clock, QUIET, KNOWN_BULK, sid="a")
    b = make_classified(conn, clock, QUIET, KNOWN_BULK | _jev(("phishing",)), sid="b")
    with write_tx(conn):
        conn.executemany("INSERT INTO claim_batches (batch_id, stable_id) VALUES ('x', ?)",
                         [(a,), (b,)])  # fmt: skip
    assert claude_queue.batch_risky(conn, a)
    with write_tx(conn):
        conn.execute("UPDATE items SET facts = ? WHERE stable_id = ?", (json.dumps(KNOWN_BULK), b))
    assert not claude_queue.batch_risky(conn, a)


def test_an_unsure_requires_action_offers_the_actor_no_hide() -> None:
    from ecf_server import rules  # noqa: PLC0415

    starter = rules.load_starter_rules(SCHEMA)
    ctx = Context(QUIET, KNOWN_BULK | _jev(("requires_action",)), "standard", starter, {},
                  frozenset())  # fmt: skip
    assert set(actor.allowed(QUIET)) & HIDE_ACTIONS
    assert not set(actor.offered(ctx)) & HIDE_ACTIONS


def test_a_malformed_or_missing_facts_jev_reads_as_today() -> None:
    for facts in ({}, {"jev": "x"}, {"jev": {"unsure": "phishing"}}, {"jev": {"unsure": [1]}}):
        assert jev_facts.risk_view(QUIET, facts) == QUIET
    assert (
        jev_facts.cautious({"fraud_risk": "high"}, jev_facts.Derived(frozenset({"fraud_risk"})))[
            "fraud_risk"
        ]
        == "high"
    )


# Review accuracy (§9.3): a Fix that sets an unsure field to yes is a correct "unsure".


def test_a_fix_to_yes_on_an_unsure_field_counts_as_correct(conn: sqlite3.Connection,
                                                           clock: FakeClock) -> None:  # fmt: skip
    make_address(conn, clock, "shadow")
    unsure = KNOWN_BULK | _jev(("phishing", "fraud_risk"))
    cases = [("a", unsure, {"phishing": True}, True),
             ("b", unsure, {"fraud_risk": "low"}, True),
             ("c", unsure, {"fraud_risk": "high"}, False),  # more than ecf treated it as
             ("d", unsure, {"category": "invoice"}, False),
             ("e", KNOWN_BULK, {"phishing": True}, False)]  # fmt: skip
    for sid, facts, fix, gated_ok in cases:
        s = make_classified(conn, clock, QUIET, facts, sid=sid)
        assert review.record(conn, clock, s, actor="you", correction=fix) == "fixed"
        rv = json.loads(row(conn, s)["review"])
        assert rv["gated_ok"] is gated_ok, sid
    assert review.reviewed(conn, "ap")["gated_ok"] == 2


# A mapping change moves D's key: a live D address drops to assist (step 4 P5).


def test_a_mapping_change_demotes_a_live_d_address(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, clock: FakeClock
) -> None:
    from ecf_server import gate  # noqa: PLC0415

    add_d(conn, clock)
    jev_dispatch.refresh_keys(conn, clock)
    key = claude_pins.address_key(conn, "d")
    with write_tx(conn):
        conn.execute("UPDATE addresses SET stage = 'live' WHERE address_id = 'd'")
        conn.execute("INSERT INTO gate (address_id, pair_key, reviewed, correct, fraud_misses,"
                     " unsafe, pinned_ids, passed_at) VALUES ('d', ?, 100, 100, 0, 0, ?, 't')",
                     (gate.pair_key("D"), json.dumps(dict(claude_pins.for_address(conn, "d")[1]),
                                                    sort_keys=True)))  # fmt: skip
    stages.tick(conn, clock)
    assert (
        conn.execute("SELECT stage FROM addresses WHERE address_id = 'd'").fetchone()[0] == "live"
    )
    _banded(monkeypatch, bands={"phishing": 0.3})
    assert jev_keys.mapping_digest() == jev_mapping.digest()
    jev_dispatch.refresh_keys(conn, clock)
    assert claude_pins.address_key(conn, "d") != key
    stages.tick(conn, clock)
    assert conn.execute("SELECT stage FROM addresses WHERE address_id = 'd'").fetchone()[0] == (
        "assist")  # fmt: skip


# P26 over the whole space: no case is less cautious with the mapping than without it.

BANDED = sorted(jev_mapping.BANDABLE | jev_mapping.NONE_BARRABLE)
BARRED = sorted(jev_mapping.BARRABLE)


def _acts(p: policy.Plan) -> dict[tuple[str, str | None], str]:
    return {(a.name, a.target): a.mode for a in p.actions}


@settings(max_examples=3000, deadline=None)
@given(CONTEXTS, st.sets(st.sampled_from(BANDED)), st.sets(st.sampled_from(BARRED)))
def test_no_case_is_less_cautious_with_the_mapping(ctx: Context, unsure: set[str],
                                                   below: set[str]) -> None:  # fmt: skip
    # an unsure field is a stored no (fraud_risk `none`), as `jev_mapping.derive` finds it
    cls = ctx.classification | {f: ("none" if f == "fraud_risk" else False) for f in unsure}
    base = dataclasses.replace(ctx, classification=cls)
    jev = dataclasses.replace(base, facts=base.facts | _jev(tuple(unsure), tuple(below)))
    known = policy.labels(SCHEMA, ctx.rules)
    before = policy.plan(base, known)
    after = jev_mapping.hold(jev, policy.plan(jev, known), jev_facts.derived(jev.facts), known,
                             ctx.rules)  # fmt: skip
    a, b = _acts(after), _acts(before)
    assert {k for k in a if k[0] in HIDE_ACTIONS} <= {k for k in b if k[0] in HIDE_ACTIONS}
    for name in ("escalate", "flag"):
        assert not any(k[0] == name for k in b) or any(k[0] == name for k in a)
    # labels: all of today's but `alert_echo`, the one label given up (step 3a)
    assert {k for k in b if k[0] == "label" and k[1] != "alert_echo"} <= set(a)
    for k, mode in b.items():
        if k in a and mode == "approve":
            assert a[k] == "approve", k
    assert after.high_risk >= before.high_risk
    assert after.payment_or_fraud >= before.payment_or_fraud
    if unsure:
        assert not after.hides and any(k[0] == "flag" for k in a)
    # the readers outside the plan
    row_b = {"facts": json.dumps(base.facts), "classification": json.dumps(cls), "proposal": None}
    row_a = row_b | {"facts": json.dumps(jev.facts)}
    assert precheck.item_payment_or_fraud(row_a) >= precheck.item_payment_or_fraud(row_b)
    kind: Any = alert_items._kind  # pyright: ignore[reportPrivateUsage]  # rows as dicts
    assert kind(row_b)[0] is None or kind(row_a)[0] is not None
    assert set(actor.offered(jev)) <= set(actor.offered(base))
    assert policy.fraud_signal(jev) >= policy.fraud_signal(base)
    assert set(policy.hide_blockers(base)) <= set(policy.hide_blockers(jev))


@settings(max_examples=500, deadline=None)
@given(CONTEXTS)
def test_without_facts_jev_the_plan_is_todays(ctx: Context) -> None:
    known = policy.labels(SCHEMA, ctx.rules)
    plain = policy.plan(ctx, known)
    empty = dataclasses.replace(ctx, facts=ctx.facts | _jev())
    again = jev_mapping.hold(empty, policy.plan(empty, known), jev_facts.derived(empty.facts),
                             known, ctx.rules)  # fmt: skip
    assert _acts(again) == _acts(plain) and again.high_risk == plain.high_risk
