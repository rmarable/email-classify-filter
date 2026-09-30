"""Clarifications (V1.2 step 7c), with fake questions: the actor that asks arrives in V1.3."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import ConflictError, InvalidInputError, StepupRequiredError
from ecf.ids import AddressId, StableId
from ecf.status import Status
from ecf_server import answers, approvals, db, items, slack_admin, stepup
from ecf_server._slack import Envelope
from ecf_server.clock import FakeClock, to_ts
from ecf_server.db import write_tx
from ecf_server.notify import FakeNotifier
from ecf_server.slack_in import Inbound, SlackReceiver
from ecf_server.state_machine import TransitionContext
from ecf_server.stepper import FakeStepper

ME = "U0ME1"
FRAUD = {"triggers": {"fraud": ["bank details from an unconfirmed sender"]}}
QUESTION = "Is https://evil.test/pay the vendor's portal? Call +1 (555) 010-0199 or a@b.example"


def _setup(conn: sqlite3.Connection, clock: FakeClock) -> None:
    now = to_ts(clock.now())
    with write_tx(conn):
        conn.execute("INSERT INTO addresses (address_id, email, sensitivity, stage, preset,"
                     " created_at) VALUES ('ap', 'ap@acme.example', 'standard', 'live', 'A', ?)",
                     (now,))  # fmt: skip
        conn.execute("INSERT INTO routes (address_id, surface, route_ref, name)"
                     " VALUES ('ap', 'slack', 'CAP', 'ecf-default-ap')")  # fmt: skip
        for k, v in (("slack_app_id", "A1"), ("slack_team_id", "T1"), ("slack_member_id", ME)):
            slack_admin.put_setting(conn, k, v, now, actor="test")


def _proposed(
    conn: sqlite3.Connection, clock: FakeClock, sid: str, facts: dict[str, Any] | None = None
) -> str:
    items.create_item(conn, clock, stable_id=StableId(sid), address_id=AddressId("ap"),
                      content_hash="h", facts=json.dumps(facts or {}), subject="Invoice 42",
                      sender="billing@vendor-a.example")  # fmt: skip
    for to in (Status.CLASSIFIED, Status.PROPOSED):
        items.transition(conn, clock, StableId(sid), to, TransitionContext(), actor="test")
    return sid


def _status(conn: sqlite3.Connection, sid: str) -> str:
    return str(conn.execute("SELECT status FROM items WHERE stable_id = ?", (sid,)).fetchone()[0])


def _state(conn: sqlite3.Connection, sid: str) -> dict[str, Any]:
    row = conn.execute("SELECT proposal FROM items WHERE stable_id = ?", (sid,)).fetchone()
    return json.loads(row[0] or "{}")


def _posts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload FROM jobs WHERE queue = 'slack_out' ORDER BY rowid")
    return [json.loads(r[0]) for r in rows]


def _submit(db_path: Path, clock: FakeClock, conn: sqlite3.Connection, sid: str, text: str) -> None:
    inbound = Inbound(slack_admin.identity, clock, lambda: db.connect(db_path),
                      lambda _e: None, lambda _t, _v: None)  # fmt: skip
    payload = {"type": "view_submission", "api_app_id": "A1", "team": {"id": "T1"},
               "user": {"id": ME},
               "view": {"callback_id": "answer", "private_metadata": sid,
                        "state": {"values": {"answer": {"text": {"value": text}}}}}}  # fmt: skip
    inbound.on_envelope(Envelope(f"e-{text}", "interactive", payload, None, None))
    SlackReceiver(clock).run_once(conn)


def _nonce(conn: sqlite3.Connection, clock: FakeClock, exc: StepupRequiredError) -> str:
    issued = stepup.issue(conn, clock, FakeStepper(), exc.extra["purpose"], exc.extra["target"])
    stepup.verify(conn, clock, FakeStepper(), issued.nonce_id)
    return issued.nonce_id


def test_model_text_loses_links_addresses_and_numbers() -> None:
    q = answers.model_text(QUESTION)
    assert "evil.test" not in q and "555" not in q and "@" not in q
    assert q == ("Is [link removed] the vendor's portal? Call [number removed] or "
                 "[address removed]")  # fmt: skip
    assert len(answers.model_text("x" * 500)) == answers.QUESTION_MAX


def test_a_question_is_posted_with_an_answer_button_and_labelled(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    assert answers.ask(conn, clock, sid, QUESTION, member=ME) == Status.NEEDS_CLARIFICATION
    [card] = _posts(conn)
    assert card["card"]["title"] == "ecf has a question (round 1 of 2)"
    assert card["card"]["note"] == answers.MODEL_LABEL
    assert dict(card["card"]["fields"])["Question"].startswith("Is [link removed]")
    assert [b["action"] for b in card["card"]["buttons"]] == ["answer", "show_excerpt"]


def test_the_answer_form_shows_the_question(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    answers.ask(conn, clock, sid, "Which cost center?")
    from ecf_server.slack_in import FORMS, Click  # noqa: PLC0415

    view = FORMS["answer"](conn, Click("button", "answer", sid, "CAP", ME))
    assert view["callback_id"] == "answer" and view["private_metadata"] == sid
    assert view["blocks"][1]["text"]["text"] == "Which cost center?"
    assert view["blocks"][2]["element"]["max_length"] == answers.ANSWER_MAX


def test_a_slack_answer_is_recorded_and_mirrored(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    answers.ask(conn, clock, sid, "Which cost center?")
    _submit(db_path, clock, conn, sid, "CC-42 (facilities)")
    assert _status(conn, sid) == Status.CLARIFIED
    assert _state(conn, sid)["answers"][0]["answer"] == "CC-42 (facilities)"
    posts = _posts(conn)
    assert posts[-2]["card"]["title"] == "Answered: ecf will use your answer"
    assert (
        posts[-1]["thread_key"] == f"item:{sid}"
        and posts[-1]["card"]["text"] == "CC-42 (facilities)"
    )
    audit = conn.execute("SELECT data FROM audit WHERE event = 'answer.recorded'").fetchone()[0]
    assert json.loads(audit) == {"length": 18}  # never the text
    with pytest.raises(ConflictError):  # the first answer won
        answers.answer(conn, clock, sid, "CC-7", actor="os_user")


def test_a_fraud_answer_from_slack_waits_for_step_up_at_the_computer(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(conn, clock)
    n = FakeNotifier()
    monkeypatch.setattr(approvals, "desktop", n)
    sid = _proposed(conn, clock, "a" * 64, FRAUD)
    answers.ask(conn, clock, sid, "Did you ask for the new account?")
    _submit(db_path, clock, conn, sid, "No, never")
    assert _status(conn, sid) == Status.AWAITING_STEPUP
    assert _state(conn, sid)["answer_pending"] == "No, never"
    assert n.sent and "ecf answer aaaaaaaa" in n.sent[0][1]
    assert approvals.pending(conn) == {"batch": [], "more": 0, "sends": []}  # not an approval
    with pytest.raises(StepupRequiredError) as ei:
        answers.answer(conn, clock, sid, None, actor="os_user")
    assert "No, never" not in json.dumps(ei.value.extra)  # bound to a hash of it
    answers.answer(conn, clock, sid, None, actor="os_user", nonce=_nonce(conn, clock, ei.value))
    assert _status(conn, sid) == Status.CLARIFIED
    assert "answer_pending" not in _state(conn, sid)


def test_a_fraud_answer_from_the_cli_steps_up_there(
    conn: sqlite3.Connection, clock: FakeClock
) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64, FRAUD)
    answers.ask(conn, clock, sid, "Did you ask for the new account?")
    with pytest.raises(StepupRequiredError) as ei:
        answers.answer(conn, clock, sid, "No", actor="os_user")
    nonce = _nonce(conn, clock, ei.value)
    with pytest.raises(StepupRequiredError):  # the step-up was for a different answer
        answers.answer(conn, clock, sid, "Yes", actor="os_user", nonce=nonce)
    answers.answer(conn, clock, sid, "No", actor="os_user", nonce=nonce)
    assert _status(conn, sid) == Status.CLARIFIED


def test_answers_must_fit(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    answers.ask(conn, clock, sid, "?")
    for bad in ("  ", "x" * (answers.ANSWER_MAX + 1)):
        with pytest.raises(InvalidInputError):
            answers.answer(conn, clock, sid, bad, actor="os_user")


def test_a_third_question_goes_to_a_person(conn: sqlite3.Connection, clock: FakeClock) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    for n in (1, 2):
        answers.ask(conn, clock, sid, f"question {n}")
        answers.answer(conn, clock, sid, f"answer {n}", actor="os_user")
        items.transition(conn, clock, StableId(sid), Status.PROPOSED, TransitionContext(),
                         actor="test")  # the actor proposes again (V1.3)  # fmt: skip
    assert answers.ask(conn, clock, sid, "question 3") == Status.NEEDS_HUMAN
    assert _posts(conn)[-1]["card"]["title"].startswith("Needs you: two rounds")


def test_a_waiting_answer_expires_and_counts_as_a_round(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64, FRAUD)
    answers.ask(conn, clock, sid, "Did you ask for the new account?")
    _submit(db_path, clock, conn, sid, "No")
    clock.advance(13 * 86400)
    assert answers.expire(conn, clock) == 0
    clock.advance(2 * 86400)
    assert answers.expire(conn, clock) == 1  # round 2 now
    assert _status(conn, sid) == Status.NEEDS_CLARIFICATION
    assert _posts(conn)[-1]["card"]["title"] == "Your answer expired: answer again"
    _submit(db_path, clock, conn, sid, "Still no")
    clock.advance(15 * 86400)
    answers.expire(conn, clock)  # expiring in the second round: a person
    assert _status(conn, sid) == Status.NEEDS_HUMAN


def test_a_queued_answer_is_not_an_approval(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    """`ecf reject` could drop an answer waiting for step-up (V1.2 review, 2026-09-30)."""
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64, FRAUD)
    answers.ask(conn, clock, sid, "Did you ask for the new account?")
    _submit(db_path, clock, conn, sid, "No, never")
    for decide in (approvals.reject, approvals.cancel):
        with pytest.raises(ConflictError):
            decide(conn, clock, sid, actor="os_user")
    with pytest.raises(ConflictError, match="ecf answer aaaaaaaa"):
        approvals.approve(conn, clock, FakeNotifier(), sid, actor="os_user")
    assert _status(conn, sid) == Status.AWAITING_STEPUP
    assert _state(conn, sid)["answer_pending"] == "No, never"


def test_a_refused_question_changes_nothing(conn: sqlite3.Connection, clock: FakeClock) -> None:
    """A refused `ask` used to overwrite the stored question (V1.2 review, 2026-09-30)."""
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    approvals.request(conn, clock, sid, [approvals.Planned("archive")])
    before = _state(conn, sid)
    with pytest.raises(ConflictError):  # awaiting_approval can't take a question
        answers.ask(conn, clock, sid, "Which cost center?")
    assert _state(conn, sid) == before
    with pytest.raises(ConflictError):  # nor a second request
        approvals.request(conn, clock, sid, [approvals.Planned("junk")])
    assert _state(conn, sid) == before


def test_an_expired_answer_does_not_count_as_an_approval_expiry(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    """One counter served both (V1.2 review, 2026-09-30)."""
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64, FRAUD)
    answers.ask(conn, clock, sid, "Did you ask for the new account?")
    _submit(db_path, clock, conn, sid, "No")
    clock.advance(15 * 86400)
    answers.expire(conn, clock)
    count = conn.execute("SELECT expiry_count FROM items WHERE stable_id = ?", (sid,)).fetchone()
    assert count[0] == 0


def test_a_refused_slack_answer_is_told_by_dm(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock
) -> None:
    """The form closed as if it worked (V1.2 review, 2026-09-30)."""
    _setup(conn, clock)
    sid = _proposed(conn, clock, "a" * 64)
    answers.ask(conn, clock, sid, "Which cost center?")
    answers.answer(conn, clock, sid, "CC-1", actor="os_user")  # answered at the computer first
    _submit(db_path, clock, conn, sid, "CC-2")
    dm = _posts(conn)[-1]
    assert dm["channel"] == ME and dm["card"]["title"] == "Your answer wasn't recorded"
