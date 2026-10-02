"""The local fallback for the Claude queue (V1.4 step 8; SPEC §4.3; OD-019, OD-020): the setting
and its step-up, shadow runs at the lowest priority, the fallback's own gate, the hand-off after
`claude_queue_timeout`, and what the local model then does with B and C items."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ecf.errors import ConflictError, InvalidInputError, StepupRequiredError
from ecf.ids import StableId
from ecf_server import (
    claude_queue,
    claude_review,
    digests,
    fallback,
    gate,
    items,
    modelq,
    ollama,
    pipeline,
    settings,
    stepup,
)
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.state_machine import Status, TransitionContext
from ecf_server.stepper import FakeStepper
from tests.test_classifier import GOOD, ChatOllama
from tests.test_decide import KNOWN_BULK, MARKETING, item_row
from tests.test_gate import _eval, make_address  # pyright: ignore[reportPrivateUsage]
from tests.test_models import PIN, check_kw

DIGEST = PIN.digest
REQUEST: dict[str, Any] = MARKETING | {"category": "customer_request", "requires_reply": True,
                                       "requires_action": True}  # fmt: skip
PAYMENT: dict[str, Any] = REQUEST | {"category": "invoice", "payment_related": True}


class SeqOllama(ChatOllama):
    """Answers each chat with the next reply in turn."""

    def __init__(self, *replies: str | Exception) -> None:
        super().__init__("")
        self.replies = list(replies)

    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/chat":
            self.reply = self.replies.pop(0)
        return super().handler(req)


def _ready() -> ollama.Ready:
    return ollama.Ready("0.35.0", DIGEST, ollama.Listener(("127.0.0.1:11434",), 1), {})


def _act(action: str, target: str = "") -> str:
    return json.dumps({"action": action, "target": target, "reason": "the model's reason"})


def _address(conn: sqlite3.Connection, clock: FakeClock, preset: str,
             stage: str = "shadow") -> None:  # fmt: skip
    make_address(conn, clock, stage)
    with write_tx(conn):
        conn.execute("UPDATE addresses SET preset = ?", (preset,))


def _on(conn: sqlite3.Connection, clock: FakeClock, value: str = "4") -> dict[str, Any]:
    notifier = FakeNotifier()
    with pytest.raises(StepupRequiredError) as ei:
        fallback.set_timeout(conn, clock, notifier, "ap", value, nonce=None)
    issued = stepup.issue(conn, clock, FakeStepper(), "claude_queue_timeout",
                          ei.value.extra["target"])  # fmt: skip
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    return fallback.set_timeout(conn, clock, notifier, "ap", value, nonce=issued.nonce_id) | {
        "notices": notifier.sent
    }


def _item(conn: sqlite3.Connection, clock: FakeClock, n: int, *, cls: dict[str, Any] | None = None,
          facts: dict[str, Any] | None = None) -> str:  # fmt: skip
    """An email waiting for Claude: C unclassified, or (with `cls`) B classified, actor needed."""
    sid = f"f{n:04d}".ljust(64, "0")
    items.create_item(conn, clock, stable_id=StableId(sid), address_id="ap",  # type: ignore[arg-type]
                      content_hash="h", facts=json.dumps(facts or KNOWN_BULK), subject="s",
                      sender="news@vendor-a.example", prechecked=1)  # fmt: skip
    with write_tx(conn):
        conn.execute("INSERT INTO excerpts (stable_id, classifier_text, actor_text)"
                     " VALUES (?, 'Please send the W-9 again.', 'Please send the W-9 again.')",
                     (sid,))  # fmt: skip
    if cls is None:
        items.transition(conn, clock, StableId(sid), Status.AWAITING_CLAUDE, TransitionContext(),
                         actor="service")  # fmt: skip
    else:
        with write_tx(conn):
            conn.execute("UPDATE items SET classification = ? WHERE stable_id = ?",
                         (json.dumps(cls), sid))  # fmt: skip
        items.transition(conn, clock, StableId(sid), Status.CLASSIFIED, TransitionContext(),
                         actor="classifier")  # fmt: skip
        from ecf_server import decide  # noqa: PLC0415

        assert decide.apply(conn, clock, sid) is Status.AWAITING_CLAUDE
    return sid


def _passed(conn: sqlite3.Connection, preset: str) -> None:
    with write_tx(conn):
        conn.execute("INSERT INTO gate (address_id, pair_key, reviewed, correct, fraud_misses,"
                     " unsafe, ollama_digest, passed_at) VALUES ('ap', ?, 100, 100, 0, 0, ?,"
                     " '2026-10-01T00:00:00Z')", (fallback.PAIR[preset], DIGEST))  # fmt: skip


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "labels.jsonl").write_text('{"id": "x"}\n')
    return tmp_path


def _status(conn: sqlite3.Connection, sid: str) -> str:
    return str(conn.execute("SELECT status FROM items WHERE stable_id = ?", (sid,)).fetchone()[0])


# ---- the setting --------------------------------------------------------------------------------


def test_turning_it_on_needs_step_up_and_sends_a_security_notice(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, "C")
    r = _on(conn, clock, "4")
    assert r["value"] == 4 and fallback.hours(conn, "ap") == 4
    assert any("Local fallback on" in body for _t, body in r["notices"])
    assert fallback.reminders(conn) == [] and fallback.needs_ollama(conn)
    shown = {s["key"]: s["value"] for s in settings.show(conn, "ap")}
    assert shown["claude_queue_timeout"] == 4
    with pytest.raises(ConflictError):
        fallback.set_timeout(conn, clock, FakeNotifier(), "ap", "4", nonce=None)
    # off needs no step-up; items handed over but not taken go back to Claude
    sid = _item(conn, clock, 1)
    with write_tx(conn):
        conn.execute("UPDATE items SET fallback_at = 'x'")
    off = fallback.set_timeout(conn, clock, FakeNotifier(), "ap", "off", nonce=None)
    assert off["value"] == "off" and fallback.hours(conn, "ap") is None
    assert item_row(conn, sid)["fallback_at"] is None
    assert fallback.reminders(conn) == ["ap"]


@pytest.mark.parametrize("value", ["0", "169", "soon", ""])
def test_hours_from_1_to_168(conn: sqlite3.Connection, clock: FakeClock, value: str) -> None:
    _address(conn, clock, "B")
    with pytest.raises(InvalidInputError, match="1 to 168"):
        fallback.set_timeout(conn, clock, FakeNotifier(), "ap", value, nonce=None)


def test_only_b_and_c_addresses_have_it(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock, "A")
    with pytest.raises(InvalidInputError, match="preset B and C"):
        fallback.set_timeout(conn, clock, FakeNotifier(), "ap", "4", nonce=None)
    with pytest.raises(InvalidInputError, match="step-up"):
        settings.key("claude_queue_timeout", "ap")
    assert "claude_queue_timeout" not in {s["key"] for s in settings.show(conn, "ap")}


def test_claude_since_is_when_it_started_waiting(conn: sqlite3.Connection,
                                                  clock: FakeClock) -> None:  # fmt: skip
    _address(conn, clock, "C")
    sid = _item(conn, clock, 1)
    assert item_row(conn, sid)["claude_since"] == to_ts(clock.now())


# ---- shadow runs ------------------------------------------------------------------------------


def test_a_c_shadow_run_classifies_and_acts_and_changes_nothing(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, "C")
    before = _item(conn, clock, 0)  # before the fallback was on: not run
    clock.advance(1)
    _on(conn, clock)
    clock.advance(1)
    sid = _item(conn, clock, 1)
    assert fallback.shadow_addresses(conn) == ["ap"]
    first = fallback.next_shadow(conn, "ap", set())
    assert first is not None and first["stable_id"] == sid
    fake = SeqOllama(json.dumps(GOOD | {"requires_reply": True}), _act("flag"))
    r = fallback.shadow_item(conn, clock, fake.client(), _ready(), item_row(conn, sid))
    assert r.outcome == "ok" and len(fake.bodies) == 2
    row = conn.execute("SELECT * FROM fallback_shadow").fetchone()
    assert row["outcome"] == "ok" and row["digest"] == DIGEST
    assert json.loads(row["classification"])["category"] == "invoice"
    plan = json.loads(row["plan"])
    assert plan["actor"] == {"action": "flag", "target": None, "fallback": True}
    assert "reason" not in row["plan"]  # the model's words aren't kept
    it = item_row(conn, sid)
    assert it["status"] == "awaiting_claude" and it["classification"] is None
    assert fallback.shadow_addresses(conn) == [] and before != sid


def test_a_b_shadow_run_is_the_actor_on_the_items_claude_acts_on(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, "B")
    _on(conn, clock)
    clock.advance(1)
    sid = _item(conn, clock, 1, cls=REQUEST)
    fake = SeqOllama(_act("flag"))
    assert fallback.shadow_item(conn, clock, fake.client(), _ready(),
                                item_row(conn, sid)).outcome == "ok"  # fmt: skip
    row = conn.execute("SELECT classification, plan FROM fallback_shadow").fetchone()
    assert json.loads(row["classification"]) == REQUEST  # B's own (local) classification
    assert json.loads(row["plan"])["actor"]["action"] == "flag"
    assert _status(conn, sid) == "awaiting_claude"


def test_a_failed_shadow_run_is_kept_and_not_tried_again(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, "C")
    _on(conn, clock)
    clock.advance(1)
    sid = _item(conn, clock, 1)
    fake = SeqOllama("not json")
    assert fallback.shadow_item(conn, clock, fake.client(), _ready(),
                                item_row(conn, sid)).outcome == "failed"  # fmt: skip
    assert conn.execute("SELECT outcome FROM fallback_shadow").fetchone()[0] == "failed"
    assert fallback.shadow_addresses(conn) == []
    assert item_row(conn, sid)["model_failed"] == 0  # the item itself is untouched


def test_shadow_runs_come_after_all_other_local_work(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, "C")
    _on(conn, clock)
    clock.advance(1)
    shadow_sid = _item(conn, clock, 1)
    waiting_shadow = fallback.shadow_addresses(conn)
    handed = _item(conn, clock, 2)
    with write_tx(conn):  # an item handed to the fallback: real local work
        conn.execute("UPDATE items SET fallback_at = 'x' WHERE stable_id = ?", (handed,))
    order: list[str] = []

    def work(conn: sqlite3.Connection, clock: Any, client: Any, ready: Any,
             item: sqlite3.Row) -> modelq.ItemResult:  # fmt: skip
        order.append("work:" + item["stable_id"][:5])
        with write_tx(conn):
            conn.execute("UPDATE items SET fallback_at = NULL, model_failed = 1"
                         " WHERE stable_id = ?", (item["stable_id"],))  # fmt: skip
        return modelq.ItemResult("skipped")

    def shadow(conn: sqlite3.Connection, clock: Any, client: Any, ready: Any,
               item: sqlite3.Row) -> modelq.ItemResult:  # fmt: skip
        order.append("shadow:" + item["stable_id"][:5])
        fallback._store(conn, clock, item, DIGEST, "ok", {}, {})  # pyright: ignore[reportPrivateUsage]
        return modelq.ItemResult("ok")

    fake = ChatOllama("")
    r = modelq.run_round(conn, clock, FakeNotifier(), fake.client(), work, shadow=shadow,
                         check_kw=check_kw())  # fmt: skip
    assert waiting_shadow == ["ap"] and r.status == "done"
    # the handed-off item first; it went back to Claude, and waits for its shadow run until the
    # next round (each item once per round)
    assert order == ["work:f0002", "shadow:f0001"]
    assert shadow_sid.startswith("f0001")
    # without shadow work, a round runs none
    order.clear()
    assert modelq.run_round(conn, clock, FakeNotifier(), fake.client(), work,
                            check_kw=check_kw()).done == 0  # fmt: skip


# ---- its own gate -----------------------------------------------------------------------------


def _shadowed(conn: sqlite3.Connection, clock: FakeClock, n: int, *, shadow: dict[str, Any],
              verdict: str = "correct", plan: dict[str, Any] | None = None,
              start: int = 0) -> None:  # fmt: skip
    for i in range(start, start + n):
        sid = _item(conn, clock, i)
        with write_tx(conn):
            conn.execute("UPDATE items SET classification = ?, review = ? WHERE stable_id = ?",
                         (json.dumps(REQUEST), json.dumps({"verdict": verdict,
                                                           "category_ok": True}), sid))  # fmt: skip
        fallback._store(conn, clock, item_row(conn, sid), DIGEST, "ok", shadow,  # pyright: ignore[reportPrivateUsage]
                        plan or {"payment_or_fraud": False, "actor": None})  # fmt: skip


def test_the_gate_scores_the_shadow_against_your_reviews(
    conn: sqlite3.Connection, clock: FakeClock, root: Path
) -> None:
    _address(conn, clock, "C")
    _on(conn, clock)
    clock.advance(1)
    _shadowed(conn, clock, 90, shadow=REQUEST)
    _shadowed(conn, clock, 10, shadow=MARKETING, start=90)  # wrong category
    g = fallback.compute(conn, "ap")
    by = {c.name: c for c in g.checks}
    assert g.reviewed == 100 and g.correct == 90 and by["reviewed"].ok
    assert by["accuracy"].ok and not by["synthetic"].ok and not g.met
    assert not any(c.waivable for c in g.checks)  # nothing in it can be waived
    _eval(conn, clock, root)
    assert fallback.compute(conn, "ap").met
    fallback.tick(conn, clock)
    assert fallback.passed(conn, "ap", "C")
    assert fallback.shadow_addresses(conn) == []  # shadow runs stop
    assert gate.stored(conn, "ap") is None  # the Claude gate's row is a different one
    posts = [json.loads(r[0]) for r in conn.execute(
        "SELECT payload FROM jobs WHERE queue = 'slack_out'")]  # fmt: skip
    assert any("Local fallback ready" in json.dumps(p) for p in posts)


def test_unsafe_shadow_proposals_and_fraud_misses_fail_the_gate(
    conn: sqlite3.Connection, clock: FakeClock, root: Path
) -> None:
    _address(conn, clock, "C")
    _on(conn, clock)
    clock.advance(1)
    _eval(conn, clock, root)
    _shadowed(conn, clock, 100, shadow=REQUEST)
    _shadowed(
        conn,
        clock,
        1,
        shadow=REQUEST,
        start=100,
        plan={"payment_or_fraud": True, "actor": {"action": "archive", "fallback": True}},
    )
    g = fallback.compute(conn, "ap")
    assert g.unsafe == 1 and not g.met
    # a reviewed email the shadow called routine whose truth is the fraud guard's
    sid = _item(conn, clock, 200, facts=KNOWN_BULK)
    with write_tx(conn):
        conn.execute(
            "UPDATE items SET classification = ?, review = ?, human_correction = ?"
            " WHERE stable_id = ?",
            (
                json.dumps(REQUEST),
                json.dumps({"verdict": "fixed"}),
                json.dumps({"category": "spam_or_phishing", "fraud_risk": "high"}),
                sid,
            ),
        )
    fallback._store(conn, clock, item_row(conn, sid), DIGEST, "ok", REQUEST,  # pyright: ignore[reportPrivateUsage]
                    {"payment_or_fraud": False, "actor": None})  # fmt: skip
    assert fallback.compute(conn, "ap").fraud_misses == 1


# ---- the hand-off -----------------------------------------------------------------------------


def test_items_go_to_the_local_model_only_after_the_timeout_and_the_gate(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, "C")
    _on(conn, clock, "4")
    sid = _item(conn, clock, 1)
    clock.advance(5 * 3600)
    assert fallback.hand_off(conn, clock) == 0  # its gate hasn't passed
    _passed(conn, "C")
    fresh = _item(conn, clock, 2)
    assert fallback.hand_off(conn, clock) == 1
    assert item_row(conn, sid)["fallback_at"] is not None
    assert item_row(conn, fresh)["fallback_at"] is None  # not waited long enough
    assert modelq.waiting(conn) == {"ap": 1} and claude_queue.waiting(conn) == {"ap": 1}
    q = claude_review.review_queue(conn, clock, "s1")
    assert [i["id"] for i in q["items"]] == [fresh]  # /ecf-review no longer offers it
    assert fallback.handed_off(conn, "ap") == 1


def test_an_item_a_claude_session_holds_is_not_handed_off(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, "C")
    _on(conn, clock, "1")
    _passed(conn, "C")
    clock.advance(2 * 3600)
    sid = _item(conn, clock, 1)
    claude_review.review_queue(conn, clock, "s1")  # claimed for 15 minutes
    with write_tx(conn):
        conn.execute("UPDATE items SET claude_since = ? WHERE stable_id = ?",
                     (to_ts(clock.now() - timedelta(hours=2)), sid))  # fmt: skip
    assert fallback.hand_off(conn, clock) == 0
    clock.advance(16 * 60)
    assert fallback.hand_off(conn, clock) == 1


def test_c_handed_off_the_local_model_classifies_then_acts_under_local_high_risk(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, "C")
    _on(conn, clock, "1")
    _passed(conn, "C")
    sid = _item(conn, clock, 1)
    clock.advance(2 * 3600)
    fallback.hand_off(conn, clock)
    fake = SeqOllama(json.dumps(REQUEST), _act("flag"))
    assert pipeline.work(conn, clock, fake.client(), _ready(), item_row(conn, sid)).outcome == "ok"
    it = item_row(conn, sid)
    pins = json.loads(it["pinned_models"])
    assert it["status"] == "classified" and pins["pin_key"] == DIGEST and pins["fallback"]
    assert modelq.waiting(conn) == {"ap": 1}  # for the local actor, not Claude
    assert pipeline.work(conn, clock, fake.client(), _ready(), item_row(conn, sid)).outcome == "ok"
    it = item_row(conn, sid)
    plan = json.loads(it["proposal"])["plan"]
    assert it["status"] == "observed" and plan["actor"]["fallback"] is True
    assert modelq.waiting(conn) == {} and claude_queue.waiting(conn) == {}


def test_b_handed_off_the_local_actor_decides_and_its_gate_is_kept_apart(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, "B")
    _on(conn, clock, "1")
    _passed(conn, "B")
    sid = _item(conn, clock, 1, cls=PAYMENT)
    clock.advance(2 * 3600)
    fallback.hand_off(conn, clock)
    fake = SeqOllama(_act("flag"))
    assert pipeline.work(conn, clock, fake.client(), _ready(), item_row(conn, sid)).outcome == "ok"
    it = item_row(conn, sid)
    plan = json.loads(it["proposal"])["plan"]
    assert it["status"] == "observed" and plan["actor"]["fallback"] is True
    assert plan["payment_or_fraud"] and plan["actor"]["action"] == "flag"
    # a hide the fallback proposed on payment mail counts toward its own gate, not Claude's
    unsafe = plan | {"actor": plan["actor"] | {"action": "archive"}}
    assert not gate.unsafe(unsafe) and gate.unsafe(unsafe, fallback=True)


def test_when_the_fallback_gives_up_the_item_goes_back_to_claude(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _address(conn, clock, "C")
    _on(conn, clock, "1")
    _passed(conn, "C")
    sid = _item(conn, clock, 1)
    clock.advance(2 * 3600)
    fallback.hand_off(conn, clock)
    fake = SeqOllama("not json", "not json")
    for _ in range(modelq.MAX_ATTEMPTS):
        r = modelq.run_round(conn, clock, FakeNotifier(), fake.client(), pipeline.work,
                             check_kw=check_kw())  # fmt: skip
        assert r.failed == 1
    it = item_row(conn, sid)
    assert it["status"] == "awaiting_claude" and it["fallback_at"] is None and it["model_failed"]
    assert claude_queue.waiting(conn) == {"ap": 1} and modelq.waiting(conn) == {}
    assert fallback.hand_off(conn, clock) == 0  # not again until `ecf item requeue`
    assert modelq.retry(conn, clock, sid, actor="t")
    assert fallback.hand_off(conn, clock) == 1


def test_the_digest_lists_handed_off_mail(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _address(conn, clock, "C")
    _on(conn, clock, "1")
    _passed(conn, "C")
    _item(conn, clock, 1)
    since = clock.now()
    clock.advance(2 * 3600)
    fallback.hand_off(conn, clock)
    card = digests.build(conn, "ap", since, clock.now())
    assert card is not None and "local_high_risk" in card.text


def test_doctor_reminds_when_off_and_shows_the_gate_while_in_shadow() -> None:
    from ecf.doctor import Level, judge_fallback  # noqa: PLC0415

    st = {"fallback": [{"address_id": "b", "hours": None},
                       {"address_id": "c", "hours": 4, "gate_passed": False,
                        "gate": "not met: 3/100 reviewed", "handed_off": 0},
                       {"address_id": "d", "hours": 8, "gate_passed": True,
                        "handed_off": 2}]}  # fmt: skip
    b, c, d = judge_fallback(st)
    assert b.level is Level.WARN and "claude_queue_timeout" in b.fix
    assert c.level is Level.WARN and "3/100 reviewed" in c.detail
    assert d.level is Level.OK and "2 handed" in d.detail
