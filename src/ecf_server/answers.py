"""Clarifications (SPEC §9.9, §6.2; V1.2 step 7c).

Built in V1.2 and exercised with fake questions: the actor that asks them arrives in V1.3.

- `ask`: the actor's question moves the item to `needs_clarification` and posts a card with an
  Answer button. The question is model output: links, email addresses and phone numbers are
  removed, it is capped, and it is labelled as such (§8.5).
- `answer`: the first answer wins. Most answers are recorded at once (`clarified`). An answer on a
  payment or fraud item needs step-up (OD-076): from the CLI it happens there and then; from Slack
  the answer waits at `awaiting_stepup` until you confirm it with `ecf answer <id>` at the
  computer. Answers are mirrored to the card's thread.
- At most 2 rounds: a third question, or an answer expiring in the second round, sends the item
  to `needs_human`. An answer waiting for step-up expires with the approval TTL (14 days).

Answers go to Slack under the privacy statement (§12.1); the audit log records their length only.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ecf.errors import ConflictError, InvalidInputError
from ecf.ids import StableId
from ecf.status import Status
from ecf_server import approvals, cards, inbox, items, slack_in, slack_out, slack_routes, stepup
from ecf_server.chat import Button, Card
from ecf_server.clock import Clock, from_ts, to_ts
from ecf_server.db import write_tx
from ecf_server.precheck import payment_or_fraud
from ecf_server.slack_in import Click
from ecf_server.slack_render import clean
from ecf_server.state_machine import MAX_CLARIFICATION_ROUNDS, Origin, TransitionContext

ANSWER = "answer"  # the button, the form's callback_id and its handler
QUESTION_MAX = 300
ANSWER_MAX = 2000
ANSWER_TTL = approvals.TTL_OTHER
MODEL_LABEL = "Question from ecf's model (it can be wrong)"
_URL = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]{1,20}://|www\.)\S+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+?\d[\d ().-]{6,}\d")


def model_text(text: str, limit: int = QUESTION_MAX) -> str:
    """Model output for Slack: no links, email addresses or phone numbers; capped (§8.5)."""
    t = _URL.sub("[link removed]", text)
    t = _EMAIL.sub("[address removed]", t)
    t = _PHONE.sub("[number removed]", t)
    return clean(" ".join(t.split()), limit)


def _state(item: sqlite3.Row) -> dict[str, Any]:
    return json.loads(item["proposal"] or "{}")


def _save(conn: sqlite3.Connection, clock: Clock, sid: str, state: dict[str, Any]) -> None:
    with write_tx(conn):
        conn.execute("UPDATE items SET proposal = ?, updated_at = ? WHERE stable_id = ?",
                     (json.dumps(state), to_ts(clock.now()), sid))  # fmt: skip


# ---- asking ---------------------------------------------------------------------------------


def ask(
    conn: sqlite3.Connection, clock: Clock, sid: str, question: str, *, member: str = ""
) -> str:
    """The actor needs to know something. Returns the item's new status. (V1.3's actor; tests.)"""
    item = inbox.find(conn, sid)
    state = _state(item)
    state["question"] = model_text(question)
    _save(conn, clock, sid, state)
    items.transition(conn, clock, StableId(sid), Status.NEEDS_CLARIFICATION, TransitionContext(),
                     actor="service", expected=Status(item["status"]))  # fmt: skip
    item = inbox.find(conn, sid)
    if item["clarification_rounds"] > MAX_CLARIFICATION_ROUNDS:  # a third question
        return _to_person(conn, clock, item)
    _question_card(conn, clock, item, member=member)
    return Status.NEEDS_CLARIFICATION.value


def _to_person(conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row) -> str:
    items.transition(conn, clock, StableId(item["stable_id"]), Status.NEEDS_HUMAN,
                     TransitionContext(clarification_rounds=item["clarification_rounds"]),
                     actor="service", expected=Status.NEEDS_CLARIFICATION)  # fmt: skip
    _card(conn, clock, inbox.find(conn, item["stable_id"]),
          "Needs you: two rounds of questions didn't settle it", [])  # fmt: skip
    return Status.NEEDS_HUMAN.value


# ---- answering --------------------------------------------------------------------------------


@stepup.purpose("answer")
def _describe_answer(conn: sqlite3.Connection, target: dict[str, Any]) -> stepup.Bound:
    item = inbox.find(conn, str(target.get("stable_id", "")))
    text = str(target.get("answer_hash", ""))
    s = inbox.summary(item)
    prompt = (f'ecf: send your answer about the email from {s["sender"][:60]}, "'
              f'{s["subject"][:60]}" on {item["address_id"]} (payment or fraud)')  # fmt: skip
    return stepup.Bound(stepup.digest("answer", item["stable_id"], item["status"], text), prompt)


def answer(
    conn: sqlite3.Connection,
    clock: Clock,
    ref: str,
    text: str | None,
    *,
    actor: str,
    nonce: str | None = None,
) -> dict[str, Any]:
    """Record an answer. `text` None confirms the answer that is waiting for step-up."""
    item = inbox.find(conn, ref)
    sid = StableId(item["stable_id"])
    status = Status(item["status"])
    state = _state(item)
    if status is Status.AWAITING_STEPUP and "answer_pending" in state:
        text = state["answer_pending"] if text is None else text
    elif status is not Status.NEEDS_CLARIFICATION:
        raise ConflictError(f"this email is {status}: no question is waiting",
                            current=str(status))  # fmt: skip
    if text is None or not text.strip():
        raise InvalidInputError("an answer can't be empty")
    text = text.strip()
    if len(text) > ANSWER_MAX:
        raise InvalidInputError(f"an answer is at most {ANSWER_MAX} characters")
    risky = payment_or_fraud(json.loads(item["facts"] or "{}"))
    ctx = TransitionContext(origin=Origin.ANSWER, payment_or_fraud=risky,
                            clarification_rounds=item["clarification_rounds"])  # fmt: skip
    if risky and actor.startswith("slack:"):
        return _queue(conn, clock, item, text, actor, ctx)
    if risky:
        stepup.consume(conn, clock, "answer",
                       {"stable_id": sid, "answer_hash": stepup.digest(text)}, nonce)  # fmt: skip
        if status is Status.NEEDS_CLARIFICATION:
            items.transition(conn, clock, sid, Status.AWAITING_STEPUP, ctx, actor=actor,
                             expected=status)  # fmt: skip
        items.transition(conn, clock, sid, Status.CLARIFIED,
                         TransitionContext(origin=Origin.ANSWER, stepup_verified=True),
                         actor=actor, expected=Status.AWAITING_STEPUP)  # fmt: skip
    else:
        items.transition(conn, clock, sid, Status.CLARIFIED, ctx, actor=actor, expected=status)
    return _recorded(conn, clock, sid, text, actor)


def _queue(
    conn: sqlite3.Connection,
    clock: Clock,
    item: sqlite3.Row,
    text: str,
    actor: str,
    ctx: TransitionContext,
) -> dict[str, Any]:
    sid = StableId(item["stable_id"])
    items.transition(conn, clock, sid, Status.AWAITING_STEPUP, ctx, actor=actor,
                     expected=Status.NEEDS_CLARIFICATION)  # fmt: skip
    state = _state(inbox.find(conn, sid)) | {"answer_pending": text, "answer_by": actor,
                                             "answer_at": to_ts(clock.now())}  # fmt: skip
    _save(conn, clock, sid, state)
    _audit(conn, clock, item, "answer.queued", actor, {"length": len(text)})
    short = sid[: cards.SHORT_ID]
    approvals.desktop.notify("ecf: answer waiting at your computer",
                             f"Confirm your answer: ecf answer {short}")  # fmt: skip
    _card(conn, clock, inbox.find(conn, sid),
          f"Answer queued for your computer: ecf answer {short}", [])  # fmt: skip
    return {"status": Status.AWAITING_STEPUP.value}


def _recorded(
    conn: sqlite3.Connection, clock: Clock, sid: str, text: str, actor: str
) -> dict[str, Any]:
    item = inbox.find(conn, sid)
    state = _state(item)
    rounds: list[dict[str, Any]] = state.get("answers", [])
    rounds.append({"question": state.get("question", ""), "answer": text, "by": actor,
                   "at": to_ts(clock.now())})  # fmt: skip
    state = {k: v for k, v in state.items() if not k.startswith("answer_")} | {"answers": rounds}
    _save(conn, clock, sid, state)
    _audit(conn, clock, item, "answer.recorded", actor, {"length": len(text)})
    _card(conn, clock, item, "Answered: ecf will use your answer", [])
    route = slack_routes.route_for(conn, item["address_id"])
    if route is not None and approvals.has_card(conn, f"item:{sid}"):  # mirrored to the thread
        slack_out.enqueue_post(conn, clock, key=f"answer:{sid}:{len(rounds)}", route=route,
                               card=Card("Your answer", text=text),
                               thread_key=f"item:{sid}")  # fmt: skip
    return {"status": Status.CLARIFIED.value}


# ---- expiry ---------------------------------------------------------------------------------


def expire(conn: sqlite3.Connection, clock: Clock) -> int:
    """A Slack answer that waited `ANSWER_TTL` for step-up expires: the question is open again,
    as a round of its own; in the second round the item goes to a person (§6.2)."""
    cutoff = clock.now() - ANSWER_TTL
    n = 0
    for item in conn.execute("SELECT * FROM items WHERE status = 'awaiting_stepup'").fetchall():
        state = _state(item)
        at = state.get("answer_at")
        if "answer_pending" not in state or at is None or from_ts(str(at)) > cutoff:
            continue
        sid = StableId(item["stable_id"])
        ctx = TransitionContext(origin=Origin.ANSWER)
        items.transition(conn, clock, sid, Status.EXPIRED, ctx, actor="service",
                         expected=Status.AWAITING_STEPUP)  # fmt: skip
        _save(conn, clock, sid, {k: v for k, v in state.items() if not k.startswith("answer_")})
        items.transition(conn, clock, sid, Status.NEEDS_CLARIFICATION, ctx, actor="service",
                         expected=Status.EXPIRED)  # fmt: skip
        _audit(conn, clock, item, "answer.expired", "service", {})
        again = inbox.find(conn, sid)
        if again["clarification_rounds"] > MAX_CLARIFICATION_ROUNDS:
            _to_person(conn, clock, again)
        else:
            _question_card(conn, clock, again, title="Your answer expired: answer again")
        n += 1
    return n


# ---- Slack ----------------------------------------------------------------------------------


@slack_in.opens_form(ANSWER)
def _answer_form(conn: sqlite3.Connection, click: Click) -> dict[str, Any]:
    item = inbox.find(conn, click.ref)
    question = str(_state(item).get("question", ""))
    return {
        "type": "modal",
        "callback_id": ANSWER,
        "private_metadata": item["stable_id"],
        "title": {"type": "plain_text", "text": "Answer ecf"},
        "submit": {"type": "plain_text", "text": "Send"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {"type": "context", "elements": [{"type": "plain_text", "text": MODEL_LABEL}]},
            {"type": "section", "text": {"type": "plain_text", "text": question or " "}},
            {
                "type": "input",
                "block_id": ANSWER,
                "label": {"type": "plain_text", "text": "Your answer"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "text",
                    "multiline": True,
                    "max_length": ANSWER_MAX,
                },
            },
        ],
    }


@slack_in.handles(ANSWER)
def _answer_submitted(conn: sqlite3.Connection, clock: Clock, click: Click) -> None:
    text = click.values.get(ANSWER, "")
    answer(conn, clock, click.ref, text, actor=f"slack:{click.user}")


def _question_card(
    conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, *, member: str = "", title: str = ""
) -> None:
    question = str(_state(item).get("question", ""))
    route = slack_routes.route_for(conn, item["address_id"])
    if route is None:
        return
    base = cards.item_card(item)
    rnd = item["clarification_rounds"]
    card = Card(title or f"ecf has a question (round {rnd} of {MAX_CLARIFICATION_ROUNDS})",
                fields=(("Question", question), *base.fields),
                buttons=(Button(ANSWER, "Answer", item["stable_id"], "primary"),
                         Button(cards.SHOW_EXCERPT, "Show excerpt", item["stable_id"])),
                note=MODEL_LABEL, mention=member)  # fmt: skip
    slack_out.enqueue_post(conn, clock, key=f"item:{item['stable_id']}", route=route, card=card,
                           identity=slack_routes.identity(item["address_id"]))  # fmt: skip


def _card(
    conn: sqlite3.Connection, clock: Clock, item: sqlite3.Row, title: str, buttons: list[Button]
) -> None:
    key = f"item:{item['stable_id']}"
    route = slack_routes.route_for(conn, item["address_id"])
    if route is None or not approvals.has_card(conn, key):
        return
    base = cards.item_card(item)
    question = str(_state(item).get("question", ""))
    fields = (("Question", question), *base.fields) if question else base.fields
    slack_out.enqueue_post(conn, clock, key=key, route=route,
                           card=Card(title, fields=fields, buttons=tuple(buttons)),
                           identity=slack_routes.identity(item["address_id"]))  # fmt: skip


def _audit(
    conn: sqlite3.Connection,
    clock: Clock,
    item: sqlite3.Row,
    event: str,
    actor: str,
    data: dict[str, Any],
) -> None:
    with write_tx(conn):
        conn.execute(
            "INSERT INTO audit (ts, address_id, stable_id, event, actor, outcome, data)"
            " VALUES (?, ?, ?, ?, ?, 'ok', ?)",
            (to_ts(clock.now()), item["address_id"], item["stable_id"], event, actor,
             json.dumps(data)),
        )  # fmt: skip
