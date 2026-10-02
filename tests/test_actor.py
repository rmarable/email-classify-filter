"""The local actor (V1.3 step 4c; SPEC §5.1 step 7, §8.1, §8.2; I1, I4): output checks, the policy
on its proposal, questions and local_high_risk, answers, and the model queue's dispatch."""

from __future__ import annotations

import json
import sqlite3
import sys
from typing import Any

import pytest

from ecf_server import actor, answers, modelq, ollama, pipeline
from ecf_server.clock import FakeClock
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.ollama import Client
from tests.test_classifier import ChatOllama
from tests.test_decide import KNOWN_BULK, MARKETING, item_row, make_address, make_classified
from tests.test_models import PIN, check_kw

REQUEST = MARKETING | {"category": "customer_request", "requires_reply": True,
                       "requires_action": True}  # fmt: skip
ROUTINE = REQUEST | {"requires_reply": False, "requires_action": False}
LABELS = frozenset({"invoice", "customer_request", "suspicious"})
FOLDERS = frozenset({"Receipts"})


def _ready() -> ollama.Ready:
    return ollama.Ready("0.35.0", PIN.digest, ollama.Listener(("127.0.0.1:11434",), 1), {})


def _to_actor(conn: sqlite3.Connection, clock: FakeClock, stage: str = "shadow",
              facts: dict[str, Any] | None = None,
              cls: dict[str, Any] | None = None) -> str:  # fmt: skip
    from ecf_server import decide  # noqa: PLC0415

    make_address(conn, clock, stage)
    sid = make_classified(conn, clock, cls or REQUEST, facts or KNOWN_BULK)
    with write_tx(conn):
        conn.execute("INSERT INTO excerpts (stable_id, classifier_text, actor_text)"
                     " VALUES (?, 'x', 'Please send the W-9 again.')", (sid,))  # fmt: skip
    assert decide.apply(conn, clock, sid).value == "classified"
    return sid


def _reply(action: str, target: str = "", reason: str = "ok") -> str:
    return json.dumps({"action": action, "target": target, "reason": reason})


def _act(conn: sqlite3.Connection, clock: FakeClock, sid: str, reply: str) -> modelq.ItemResult:
    return actor.act_item(conn, clock, ChatOllama(reply).client(), _ready(), item_row(conn, sid))


# ---- parsing (I4) -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "nope",
        _reply("send_payment"),
        _reply("label", "Ignore all previous instructions"),
        _reply("move", "INBOX"),
        json.dumps({"action": "flag", "target": ""}),
        json.dumps({"action": "flag", "target": "", "reason": "x", "extra": 1}),
        _reply("label", "Receipts"),  # a folder isn't a label
        _reply("move", "invoice"),  # nor a label a folder
        _reply("label"),  # a label needs one
    ],
)
def test_anything_outside_the_vocabulary_is_refused(content: str) -> None:
    assert actor.parse(content, LABELS, FOLDERS) is None


def test_a_target_on_an_action_that_takes_none_is_dropped() -> None:
    got = actor.parse(_reply("flag", "invoice"), LABELS, FOLDERS)
    assert got is not None and (got["action"], got["target"]) == ("flag", "")


def test_the_reason_is_cleaned_and_capped() -> None:
    reason = "Call 555-0100 or see https://evil.example/x " + "a" * 400
    got = actor.parse(_reply("flag", "", reason), LABELS, FOLDERS)
    assert got is not None and got["action"] == "flag"
    assert "evil.example" not in got["reason"] and "555-0100" not in got["reason"]
    assert len(got["reason"]) <= actor.REASON_MAX


def test_the_output_schema_lists_only_known_targets() -> None:
    s = actor.output_schema(LABELS, FOLDERS)
    assert s["properties"]["target"]["enum"] == ["", "Receipts", "customer_request", "invoice",
                                                 "suspicious"]  # fmt: skip
    assert "send" not in json.dumps(s["properties"]["action"]["enum"])


# ---- no hiding mail that needs someone (OD-250) --------------------------------------------------


def test_mail_that_needs_someone_cant_be_hidden_by_the_actor() -> None:
    assert actor.allowed(ROUTINE) == actor.ACTIONS
    kept = actor.allowed(REQUEST)
    assert not set(kept) & {"mark_read", "archive", "move", "junk"}
    assert {"label", "flag", "escalate", "leave", "needs_clarification"} <= set(kept)
    s = actor.output_schema(LABELS, FOLDERS, kept)
    assert s["properties"]["action"]["enum"] == list(kept)
    assert "Receipts" not in s["properties"]["target"]["enum"]  # no folders without move
    for hide in ("archive", "mark_read", "junk"):
        assert actor.parse(_reply(hide), LABELS, FOLDERS, kept) is None
    assert actor.parse(_reply("move", "Receipts"), LABELS, FOLDERS, kept) is None


def test_the_actor_is_never_offered_a_hide_for_a_request(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _to_actor(conn, clock)  # a customer request that needs a reply
    fake = ChatOllama(_reply("archive", "", "the email says it is spam"))
    assert actor.act_item(conn, clock, fake.client(), _ready(),
                          item_row(conn, sid)).outcome == "failed"  # fmt: skip
    body = fake.bodies[0]
    assert "archive" not in body["format"]["properties"]["action"]["enum"]
    assert "hiding it" in body["messages"][1]["content"]
    assert conn.execute("SELECT outcome FROM model_calls").fetchone()[0] == "schema_failure"
    assert item_row(conn, sid)["status"] == "classified"  # still waiting; nothing hidden


# ---- deciding -----------------------------------------------------------------------------------


def test_a_proposal_joins_the_rules_plan(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _to_actor(conn, clock)
    fake = ChatOllama(_reply("flag", "", "the customer is waiting"))
    result = actor.act_item(conn, clock, fake.client(), _ready(), item_row(conn, sid))
    assert result.outcome == "ok"
    row = item_row(conn, sid)
    assert row["status"] == "observed" and row["decision_source"] == "actor"
    plan = json.loads(row["proposal"])["plan"]
    assert plan["actor"] == {"action": "flag", "target": None, "reason": "the customer is waiting"}
    assert {"name": "flag", "target": None, "mode": "auto"} in plan["actions"]
    body = fake.bodies[0]
    assert "W-9" in body["messages"][1]["content"] and body["options"]["num_predict"] == 320
    assert conn.execute("SELECT role FROM model_calls").fetchone()[0] == "actor"


def test_a_hide_from_the_actor_still_needs_corroboration(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _to_actor(conn, clock, facts=KNOWN_BULK | {"bulk_corroborates": False}, cls=ROUTINE)
    _act(conn, clock, sid, _reply("archive", "", "routine"))
    plan = json.loads(item_row(conn, sid)["proposal"])["plan"]
    assert not any(a["name"] == "archive" for a in plan["actions"])
    assert any(d["name"] == "archive" and d["why"] == "not corroborated" for d in plan["dropped"])


def test_a_question_goes_to_you(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _to_actor(conn, clock)
    _act(conn, clock, sid, _reply("needs_clarification", "", "Which W-9 year do they need?"))
    row = item_row(conn, sid)
    assert row["status"] == "needs_clarification"
    proposal = json.loads(row["proposal"])
    assert proposal["question"] == "Which W-9 year do they need?"
    assert proposal["plan"]["actor"]["action"] == "needs_clarification"  # the decision is kept
    assert row["decision_source"] == "actor"


def test_a_question_on_a_high_risk_item_escalates_instead(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _to_actor(conn, clock, facts=KNOWN_BULK | {"sender_seen_before": False})
    _act(conn, clock, sid, _reply("needs_clarification", "", "Is this real?"))
    row = item_row(conn, sid)
    assert row["status"] == "observed"
    assert conn.execute("SELECT count(*) FROM escalations WHERE stable_id = ?",
                        (sid,)).fetchone()[0] == 1  # fmt: skip


def test_after_your_answer_the_actor_runs_again_with_it(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _to_actor(conn, clock)
    _act(conn, clock, sid, _reply("needs_clarification", "", "Which year?"))
    answers.answer(conn, clock, sid, "2025, and CC the accountant", actor="os_user")
    assert item_row(conn, sid)["status"] == "clarified"
    assert modelq.waiting(conn) == {"ap": 1}
    fake = ChatOllama(_reply("flag", "", "reply with the 2025 W-9"))
    actor.act_item(conn, clock, fake.client(), _ready(), item_row(conn, sid))
    assert "2025, and CC the accountant" in fake.bodies[0]["messages"][1]["content"]
    row = item_row(conn, sid)
    assert row["status"] == "observed"
    assert json.loads(row["proposal"])["answers"][0]["answer"].startswith("2025")


def test_schema_failures_count_as_attempts(conn: sqlite3.Connection, clock: FakeClock) -> None:
    sid = _to_actor(conn, clock)
    assert _act(conn, clock, sid, "garbage").outcome == "failed"
    assert item_row(conn, sid)["status"] == "classified"
    assert conn.execute("SELECT outcome FROM model_calls").fetchone()[0] == "schema_failure"


def test_the_queue_waits_for_actor_items_and_dispatches_them(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _to_actor(conn, clock)
    assert modelq.waiting(conn) == {"ap": 1}
    report = modelq.run_round(conn, clock, FakeNotifier(),
                              ChatOllama(_reply("leave", "", "nothing to do")).client(),
                              pipeline.work, check_kw=check_kw())  # fmt: skip
    assert report.done == 1 and modelq.waiting(conn) == {}
    assert item_row(conn, sid)["status"] == "observed"


def test_when_the_actor_gives_up_the_rules_plan_goes_ahead_without_it(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    sid = _to_actor(conn, clock)
    garbage = ChatOllama("garbage").client
    report = modelq.run_round(conn, clock, FakeNotifier(), garbage(), pipeline.work,
                              check_kw=check_kw())  # fmt: skip
    assert report.failed == 1 and report.marked_failed == 0
    report = modelq.run_round(conn, clock, FakeNotifier(), garbage(), pipeline.work,
                              check_kw=check_kw())  # fmt: skip
    assert report.marked_failed == 1 and modelq.waiting(conn) == {}
    row = item_row(conn, sid)
    assert (row["status"], row["model_failed"], row["decision_source"]) == ("observed", 0, "rule")
    assert row["model_failed_at"] is not None  # still counts toward the hour's System Error
    assert json.loads(row["proposal"])["plan"]["to_actor"] is False


@pytest.mark.macos
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_the_real_actor_answers_in_the_vocabulary(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """Needs ecf's Ollama login item and `ecf models install` on this Mac (merge gate)."""
    client = Client()
    try:
        ready = ollama.readiness(client)
    except ollama.OllamaError as e:
        pytest.fail(f"the local model isn't ready on this Mac: {e} ({e.fix})")
    sid = _to_actor(conn, clock)
    try:
        result = actor.act_item(conn, clock, client, ready, item_row(conn, sid))
    finally:
        client.close()
    assert result.outcome == "ok"
    assert json.loads(item_row(conn, sid)["proposal"])["plan"]["actor"]["action"] in actor.ACTIONS
