"""`/ecf-eval` (V1.4 step 7; SPEC §10.4, §16.2-§16.3; OD-287 to OD-292): runs registered from
the command line, cases claimed under random references through the same tools and checks as
`/ecf-review`, scoring as `ecf eval run`, the model check, comparison runs, stop and expiry, and
the routes' profiles. Preset B (on Gemma's classifications) went in v3.0.0 (ADR 0031)."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import anyio
import httpx
import pytest

from ecf.claude_wrapper import client_doc
from ecf.errors import ConflictError, ForbiddenProfileError, InvalidInputError, NotFoundError
from ecf.eval import labels
from ecf.eval.builder import build_all
from ecf.eval.results import ResultFile, load_result, spawn_figures
from ecf.plugin_digest import actor_prompt_digest, plugin_digest
from ecf.schema import load_schema, with_fixed
from ecf_server import (
    claude_eval,
    claude_pins,
    claude_review,
    config,
    db,
    evalrun,
    gate,
    signals,
    triggers,
)
from ecf_server.api import ServiceState, create_app
from ecf_server.clock import FakeClock, to_ts
from ecf_server.notify import NullNotifier
from ecf_server.telemetry import SUBAGENT, ApiCall, Hold, Seen, Telemetry
from tests.test_claude_review import extend, tokens_of, with_tokens
from tests.test_decide import MARKETING

SYNTHETIC = Path(__file__).parent / "eval" / "synthetic"
# full schema v3 answers (the fixed items derived, OD-498; S3b: the bank change is the item,
# the category what the email is about, as the relabelled starter-bec card says)
BEC: dict[str, Any] = with_fixed({"category": "notification", "priority": "high",
                                  "requires_action": True, "requires_reply": False,
                                  "payment_related": True, "deadline_mentioned": False,
                                  "sender_type": "vendor", "fraud_risk": "high"}
                                 ) | {"bank_detail_change": True}  # fmt: skip
REQUEST: dict[str, Any] = with_fixed(MARKETING | {"category": "customer_request",
                                                  "requires_reply": True,
                                                  "requires_action": True})  # fmt: skip
S1, S2 = "session-one", "session-two"
CASES = ("starter-bec", "starter-control", "starter-injection")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "synthetic"
    (r / "cases").mkdir(parents=True)
    for name in CASES:
        shutil.copy(SYNTHETIC / "cases" / f"{name}.md", r / "cases" / f"{name}.md")
    assert not build_all(r).findings
    labels.confirm(r, "starter-bec", date(2026, 10, 1))
    labels.confirm(r, "starter-injection", date(2026, 10, 1))
    return r


@pytest.fixture(autouse=True)
def _reset(conn: sqlite3.Connection) -> None:  # conn: the database, migrated
    del conn
    claude_eval.reset()


def _inline(work: Callable[[], None]) -> None:
    work()


def connector(db_path: Path) -> Callable[[], sqlite3.Connection]:
    return lambda: db.connect(db_path)


def start(db_path: Path, clock: FakeClock, root: Path, **kw: Any) -> dict[str, Any]:
    opts = claude_eval.Options(root, **kw)
    return claude_eval.start(connector(db_path), clock, db_path.parent, opts, spawn=_inline)


def nxt(db_path: Path, clock: FakeClock, session: str, **kw: Any) -> dict[str, Any]:
    """`eval_next`, each item with the token its spawn's prompt gives its agent."""
    return with_tokens(claude_eval.eval_next(connector(db_path), clock, session, **kw))


def model_for(agent: str) -> str:
    run = claude_eval.current()
    assert run is not None
    return run.models[claude_eval.role_of(agent)]


def settled(db_path: Path, clock: FakeClock, got: dict[str, Any] | Hold,
            seen: Seen | None = None, tel: Telemetry | None = None) -> dict[str, Any]:  # fmt: skip
    if not isinstance(got, Hold):
        return got
    seen = seen or Seen(model_for(got.agent))
    return claude_eval.settle(connector(db_path), clock, NullNotifier(), tel or Telemetry(), got,
                              seen)  # fmt: skip


def answer(db_path: Path, clock: FakeClock, item: dict[str, Any], session: str,
           classify: dict[str, Any], action: str = "flag") -> dict[str, Any]:  # fmt: skip
    """What a well-behaved agent does with one claimed case."""
    ref, token = item["id"], item["claim_token"]
    agent = item["agent"]
    claude_eval.get_message(clock, session, ref, token, agent)
    if item["need"] == "classify":
        got = claude_eval.record_classification(clock, session, ref, token, classify, agent)
    else:
        got = claude_eval.propose_action(clock, session, ref, token,
                                         {"action": action, "reason": "needs a look"},
                                         agent)  # fmt: skip
    return settled(db_path, clock, got)


def drain(db_path: Path, clock: FakeClock, by_case: Callable[[str], dict[str, Any]],
          session: str = S1, act: Callable[[str], str] | None = None
          ) -> list[dict[str, Any]]:  # fmt: skip
    """Rounds of eval_next until the run ends; every item it handed out. `act`: the action the
    actor proposes per case (default flag)."""
    run = claude_eval.current()
    assert run is not None
    case_of = {w.ref: w.case.id for w in run.works}
    seen: list[dict[str, Any]] = []
    for _ in range(20):
        q = nxt(db_path, clock, session)
        if q["done"]:
            return seen
        if not q["items"] and q.get("next") == "call again":  # a held answer's agent (TAIL)
            clock.advance(q["frees_in_s"])
        # one agent type a round: the model check can't tell two models' overlapping work apart
        assert len({i["agent"] for i in q["items"]}) <= 1, q["items"]
        for item in q["items"]:
            seen.append(item)
            cid = case_of[item["id"]]
            got = answer(db_path, clock, item, session, by_case(cid),
                         act(cid) if act is not None else "flag")  # fmt: skip
            assert got["accepted"], got
    stages = [(w.stage, w.claim) for w in run.works]
    raise AssertionError(f"the run didn't end: {stages}")


def c_address(conn: sqlite3.Connection, clock: FakeClock) -> None:
    conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                 " VALUES ('c', 'c@acme.example', 'standard', 'C', ?)",
                 (to_ts(clock.now()),))  # fmt: skip


# A Claude eval run end to end: it scores like `ecf eval run`, hands out one agent type per
# round under random references, and handles claims, holds, retries and expiry.


def test_a_c_run_scores_like_ecf_eval_run_and_counts_for_the_gate(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    r = start(db_path, clock, root)
    assert r["state"] == "ready" and r["total"] == 3 and r["pinned"] is True
    by_case = {"starter-bec": BEC, "starter-injection": BEC, "starter-control": REQUEST}
    handed = drain(db_path, clock, lambda cid: by_case[cid])
    needs = [i["need"] for i in handed]
    # 3 classifications, the control case's actor step, then 3 determinism re-classifications
    assert needs.count("classify") == 6 and needs.count("act") == 1
    assert {i["agent"] for i in handed if i["need"] == "classify"} == {"ecf-classifier"}
    assert {i["agent"] for i in handed if i["need"] == "act"} <= {"ecf-actor", "ecf-actor-high"}
    run = claude_eval.current()
    assert run is not None and run.state == "done" and run.diffs == 0
    row = conn.execute("SELECT * FROM eval_runs").fetchone()
    key = claude_pins.key(claude_pins.pins(conn, "C"))
    assert row["digest"] == key and row["pair"] == "claude/claude"
    result = load_result(Path(row["path"]))
    assert {c.id for c in result.cases} == set(CASES)
    m = json.loads(row["metrics"])
    assert m["confirmed"] == 2 and m["complete"] is True and m["determinism_diffs"] == 0
    assert m["claude"]["pinned"] is True and m["claude"]["sessions"] == 1
    assert m["schema_digest"] == load_schema().digest  # the schema it was asked with
    assert set(m["claude"]["models"]) == {"classifier", "classifier_high", "actor", "actor_high"}
    text = Path(row["path"]).read_text()
    assert "IBAN" not in text and "SYSTEM NOTE" not in text  # metrics only
    # the go-live gate of a C address reads it (its key is the pins')
    check = gate.synthetic(conn, key, "C")
    assert check.ok is (not m["unsafe"] and not m["fraud_guard_missed"])
    assert run.run_id[:8] in check.detail


def test_a_round_hands_out_one_agent_type_and_waits_for_the_others(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """v1.0.0 run 6cea77b1: a round mixing classifier and actor work let a session run Haiku and
    Opus spawns side by side, and the model check refused all of it. One agent type a round, and
    none while the session's claims for another are out."""
    start(db_path, clock, root)
    run = claude_eval.current()
    assert run is not None
    case_of = {w.ref: w.case.id for w in run.works}
    q = nxt(db_path, clock, S1, limit=2)
    first, second = q["items"]  # starter-bec, starter-control: both to classify
    assert {case_of[first["id"]], case_of[second["id"]]} == {"starter-bec", "starter-control"}
    control = first if case_of[first["id"]] == "starter-control" else second
    other = second if control is first else first
    assert answer(db_path, clock, control, S1, REQUEST)["accepted"]  # now needs its actor
    # S1 still holds a classifier claim: no actor work for it, only more classifier work
    q = nxt(db_path, clock, S1, limit=5)
    assert {i["agent"] for i in q["items"]} <= {"ecf-classifier"}
    # another session takes the actor step in a round of its own
    q2 = nxt(db_path, clock, S2, limit=5)
    assert len({i["agent"] for i in q2["items"]}) == 1
    assert q2["items"] and q2["items"][0]["agent"].startswith("ecf-actor")
    assert answer(db_path, clock, other, S1, BEC)["accepted"]


def test_cases_go_out_under_random_references_with_no_gold_labels(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    q = nxt(db_path, clock, S1)
    out = [q]
    for item in q["items"]:
        assert len(item["id"]) == 32 and int(item["id"], 16) >= 0
        assert item["spawn"] == "ecf-classifier" and item["need"] == "classify"
        out.append(claude_eval.get_message(clock, S1, item["id"], item["claim_token"],
                                           item["agent"]))  # fmt: skip
    text = json.dumps(out)
    for case_id in CASES:
        assert case_id not in text
    for gold in ("expected", "safety", "injection_target", "must_escalate", "fraud_guard"):
        assert gold not in text
    msg = out[1]
    assert set(msg) == {"id", "need", "untrusted_email", "notice", "schema", "schema_text",
                        "signals"}  # fmt: skip
    assert msg["schema_text"] == load_schema().prompt_block
    assert set(msg["untrusted_email"]) == {"from", "subject", "date", "text", "attachments_meta"}


def test_only_the_named_agent_reads_or_submits_a_case(db_path: Path, clock: FakeClock,
                                                       root: Path) -> None:  # fmt: skip
    start(db_path, clock, root)
    item = nxt(db_path, clock, S1)["items"][0]
    with pytest.raises(ForbiddenProfileError):
        claude_eval.get_message(clock, S1, item["id"], item["claim_token"], "ecf-actor")
    with pytest.raises(ForbiddenProfileError):
        claude_eval.record_classification(clock, S1, item["id"], item["claim_token"], BEC,
                                          "ecf-classifier-high")  # fmt: skip


def test_the_model_check_refuses_another_model_and_stops_the_session(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    item = nxt(db_path, clock, S1, limit=1)["items"][0]
    got = claude_eval.record_classification(clock, S1, item["id"], item["claim_token"], BEC,
                                            item["agent"])  # fmt: skip
    tel = Telemetry()
    tel.open(S1)
    out = settled(db_path, clock, got, Seen("claude-sonnet-5-5"), tel)
    assert out["accepted"] is False and "claude-sonnet-5-5" in out["errors"][0]
    stopped = tel.stopped(S1)
    assert stopped is not None
    q = nxt(db_path, clock, S1, stopped=stopped)
    assert q["stopped"] == stopped and not q["items"]
    assert q["results"] == [{"id": item["id"], "outcome": "model_refused"}]
    # the case waits for another session
    again = nxt(db_path, clock, S2)
    assert item["id"] in {i["id"] for i in again["items"]}


def test_a_held_submission_blocks_the_next_agent_type_only_for_its_tail(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """A submission waiting for telemetry doesn't block other agents' work for long, or the loop
    sees empty rounds (run 9e745b99); but its agent still writes its report, and the session no
    longer waits for its spawns, so another agent type waits `TAIL` (review R1)."""
    start(db_path, clock, root)
    run = claude_eval.current()
    assert run is not None
    case_of = {w.ref: w.case.id for w in run.works}
    q = nxt(db_path, clock, S1, limit=2)
    control = next(i for i in q["items"] if case_of[i["id"]] == "starter-control")
    other = next(i for i in q["items"] if i is not control)
    assert answer(db_path, clock, control, S1, REQUEST)["accepted"]  # needs its actor now
    ref, token, agent = other["id"], other["claim_token"], other["agent"]
    claude_eval.get_message(clock, S1, ref, token, agent)
    held = claude_eval.record_classification(clock, S1, ref, token, BEC, agent)
    assert isinstance(held, Hold)  # submitted, waiting for telemetry: not "claimed" any more
    q = nxt(db_path, clock, S1, limit=5)
    assert not any(i["agent"].startswith("ecf-actor") for i in q["items"])  # the tail
    for item in q["items"]:  # more of the classifier's work meanwhile
        assert answer(db_path, clock, item, S1, BEC)["accepted"]
    clock.advance(int(claude_eval.TAIL.total_seconds()) + 1)
    q = nxt(db_path, clock, S1, limit=5)
    assert any(i["agent"].startswith("ecf-actor") for i in q["items"])


def test_a_session_has_at_most_two_limits_of_claims_out(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """The session no longer waits for its spawns, so the service paces it (review R2)."""
    start(db_path, clock, root)
    got = [nxt(db_path, clock, S1, limit=1)["items"] for _ in "abc"]
    assert [len(g) for g in got] == [1, 1, 0]
    assert len({i["agent"] for g in got for i in g}) == 1


def test_an_empty_reply_waits_briefly_while_work_is_out(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """The session can't pause; the service waits up to WAIT_S (polling) while work is out, and
    returns at once when there's nothing to wait for."""
    start(db_path, clock, root)
    slept: list[float] = []
    q = claude_eval.eval_next_waiting(connector(db_path), clock, S1, limit=10, sleep=slept.append)
    assert q["items"] and slept == []  # work to hand out: no wait
    q = claude_eval.eval_next_waiting(connector(db_path), clock, S1, sleep=slept.append)
    assert q["items"] == [] and q["in_progress"] > 0
    assert sum(slept) == claude_eval.WAIT_S and claude_eval.WAIT_S < 10  # under ecf-mcp's limit


def test_a_waiting_reply_keeps_the_outcomes_of_every_pass(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """Each `eval_next` hands over the outcomes since the last; the waiting loop calls it several
    times and must return all of them."""
    start(db_path, clock, root)
    q = nxt(db_path, clock, S1, limit=10)
    for item in q["items"]:
        assert answer(db_path, clock, item, S1, REQUEST)["accepted"]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        clock.advance(s)

    q = claude_eval.eval_next_waiting(connector(db_path), clock, S1, sleep=sleep)
    assert slept  # the actor waits out the classifier's tail: several passes
    assert len(q["results"]) == 3  # one per answered case, from the first pass


def test_progress_is_the_services_count_for_the_status_line_and_the_skill(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """The session never counts for itself (run 5f5772a7 reported "items left" from its own
    reading): `eval_next` carries a fixed `progress` line and the status line the same counts."""
    assert claude_eval.progress() is None  # no run
    start(db_path, clock, root)
    q = nxt(db_path, clock, S1, limit=3)
    p = claude_eval.progress()
    assert p is not None and p["scored"] == 0 and p["in_progress"] == 3 and p["round"] == 1
    assert p["total"] == len(claude_eval.current().works) and p["repeats_left"] == 0  # type: ignore[union-attr]
    assert set(p) == {"run_id", "scored", "total", "in_progress", "repeats_left", "round",
                      "mismatches", "unread", "late"}  # fmt: skip
    assert q["progress"] == f"0/{p['total']} scored, 3 in progress, round 1"
    assert claude_eval.progress_line(p | {"repeats_left": 10}).endswith(", 10 repeats left")
    assert claude_eval.progress_line(p | {"mismatches": 1}).endswith(", 1 mixed-up claims freed")


def test_a_built_in_agent_in_the_session_stops_it() -> None:
    """Claude Code can deny agents only by name; a built-in one ecf doesn't know yet (the first
    /ecf-eval run handed its loop to `claude`, v1.0.0) stops the session instead of spending."""
    tel = Telemetry()
    tel.open(S1)
    tel.add(S1, [ApiCall(1.0, "claude-haiku-4-5-20251001", SUBAGENT)], [])
    assert tel.stopped(S1) is None  # ecf's own agents are fine
    tel.add(S1, [ApiCall(2.0, "claude-haiku-4-5-20251001", "agent:builtin:claude")], [])
    stopped = tel.stopped(S1)
    assert stopped is not None and "(claude)" in stopped and "update ecf" in stopped


def test_three_invalid_tries_score_the_case_as_a_failure(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    item = nxt(db_path, clock, S1, limit=1)["items"][0]
    tries = [claude_eval.record_classification(clock, S1, item["id"], item["claim_token"],
                                               {"category": "tax"}, item["agent"])
             for _ in range(3)]  # fmt: skip
    assert [cast(dict[str, Any], t)["tries_left"] for t in tries] == [2, 1, 0]
    assert all("tax" not in json.dumps(t) for t in tries)  # errors never quote the value
    run = claude_eval.current()
    assert run is not None
    w = run.by_ref[item["id"]]
    assert w.stage == "done" and w.result is not None and w.result.correct is False
    q = nxt(db_path, clock, S1)
    assert {"id": item["id"], "outcome": "invalid"} in q["results"]


def test_claims_expire_and_are_fenced(db_path: Path, clock: FakeClock, root: Path) -> None:
    start(db_path, clock, root)
    old = nxt(db_path, clock, S1, limit=1)["items"][0]
    clock.advance(16 * 60)
    q = nxt(db_path, clock, S2, limit=3)
    assert q["results"] == []  # S1's outcome goes to S1
    new = next(i for i in q["items"] if i["id"] == old["id"])
    assert new["claim_token"].split(".")[0] == "2"
    with pytest.raises(ConflictError):
        claude_eval.record_classification(clock, S1, old["id"], old["claim_token"], BEC,
                                          old["agent"])  # fmt: skip
    assert nxt(db_path, clock, S1)["results"] == [
        {"id": old["id"], "outcome": "claim_expired"}]  # fmt: skip


def test_a_resubmission_after_a_lost_reply_gets_the_same_answer_and_records_once(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """Claude Code restarts an agent's server when a sibling spawn ends; the agent retries a call
    that had succeeded (plan S2)."""
    start(db_path, clock, root)
    item = nxt(db_path, clock, S1, limit=1)["items"][0]
    ref, token, agent = item["id"], item["claim_token"], item["agent"]
    claude_eval.get_message(clock, S1, ref, token, agent)
    held = claude_eval.record_classification(clock, S1, ref, token, BEC, agent)
    assert isinstance(held, Hold)
    pending: dict[str, Any] = {"accepted": True, "pending": True, "errors": []}
    assert claude_eval.record_classification(clock, S1, ref, token, BEC, agent) == pending
    assert settled(db_path, clock, held)["accepted"]
    again = claude_eval.record_classification(clock, S1, ref, token, BEC, agent)
    assert again == {"accepted": True, "errors": []}
    with pytest.raises(ConflictError):  # only that session's token gets the reply
        claude_eval.record_classification(clock, S2, ref, token, BEC, agent)
    assert nxt(db_path, clock, S1)["results"] == [
        {"id": ref, "outcome": "recorded"}]  # fmt: skip


def test_a_late_answer_after_a_reclaim_leaves_the_new_claim_alone(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    old = nxt(db_path, clock, S1, limit=1)["items"][0]
    clock.advance(int(claude_review.CLAIM_TTL.total_seconds()) + 1)
    new = next(i for i in nxt(db_path, clock, S2, limit=3)["items"]
               if i["id"] == old["id"])  # fmt: skip
    with pytest.raises(ConflictError):
        claude_eval.record_classification(clock, S1, old["id"], old["claim_token"], BEC,
                                          old["agent"])  # fmt: skip
    w = _work(new)
    assert w.claim == "claimed" and w.claims[-1].answered is None
    assert answer(db_path, clock, new, S2, BEC)["accepted"]


def test_an_expired_claim_goes_back_to_the_same_agent_at_once(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    items = nxt(db_path, clock, S1, limit=3)["items"]
    for item in items:  # read: the spawn runs, so only the expiry frees it (OD-501)
        claude_eval.get_message(clock, S1, item["id"], item["claim_token"], item["agent"])
    waiting = nxt(db_path, clock, S1)
    assert waiting["items"] == [] and waiting["next"] == "call again"
    assert waiting["frees_in_s"] == int(claude_review.CLAIM_TTL.total_seconds())
    clock.advance(int(claude_review.CLAIM_TTL.total_seconds()) + 1)
    q = nxt(db_path, clock, S1, limit=3)
    assert {r["outcome"] for r in q["results"]} == {"claim_expired"}
    assert {i["agent"] for i in q["items"]} == {items[0]["agent"]}


def test_another_agent_waits_while_an_expired_claims_agent_may_still_run(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    run = claude_eval.current()
    assert run is not None
    run.grace[S1] = {"ecf-actor-high": clock.now() + timedelta(seconds=10)}
    q = nxt(db_path, clock, S1)
    assert q["items"] == [] and q["next"] == "call again" and q["frees_in_s"] == 10
    clock.advance(11)
    assert nxt(db_path, clock, S1)["items"]


def test_held_answers_keep_the_session_polling_for_a_while_then_it_ends(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    for item in nxt(db_path, clock, S1, limit=3)["items"]:
        claude_eval.get_message(clock, S1, item["id"], item["claim_token"], item["agent"])
        got = claude_eval.record_classification(clock, S1, item["id"], item["claim_token"], BEC,
                                                item["agent"])  # fmt: skip
        assert isinstance(got, Hold)  # held: no telemetry yet
    q = nxt(db_path, clock, S1)
    assert q["items"] == [] and q["next"] == "call again"
    assert q["frees_in_s"] == int(claude_eval.TAIL.total_seconds())  # its agent's tail first
    clock.advance(int(claude_eval.HOLD_WAIT.total_seconds()) + 1)
    assert nxt(db_path, clock, S1)["next"] == "end"


def test_a_different_answer_under_an_answered_claim_is_refused(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    item = nxt(db_path, clock, S1, limit=1)["items"][0]
    ref, token, agent = item["id"], item["claim_token"], item["agent"]
    held = claude_eval.record_classification(clock, S1, ref, token, BEC, agent)
    other = BEC | {"priority": "low" if BEC.get("priority") != "low" else "high"}
    with pytest.raises(ConflictError):  # held: only the same answer replays
        claude_eval.record_classification(clock, S1, ref, token, other, agent)
    assert settled(db_path, clock, held)["accepted"]
    with pytest.raises(ConflictError):  # applied: likewise
        claude_eval.record_classification(clock, S1, ref, token, other, agent)


def test_the_wait_goes_on_while_next_says_call_again(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """During grace nothing is in progress, yet the session must not poll at full speed."""
    start(db_path, clock, root)
    run = claude_eval.current()
    assert run is not None
    run.grace[S1] = {"ecf-actor-high": clock.now() + timedelta(seconds=60)}
    slept: list[float] = []
    q = claude_eval.eval_next_waiting(connector(db_path), clock, S1, sleep=slept.append)
    assert q["next"] == "call again" and q["in_progress"] == 0 and slept


def test_another_sessions_claims_keep_next_at_call_again(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    assert nxt(db_path, clock, S2, limit=10)["items"]
    q = nxt(db_path, clock, S1)
    assert q["items"] == [] and q["next"] == "call again"


def test_a_session_ending_frees_its_claims(db_path: Path, clock: FakeClock, root: Path) -> None:
    start(db_path, clock, root)
    nxt(db_path, clock, S1)
    claude_eval.release_session(S1)
    assert len(nxt(db_path, clock, S2)["items"]) == 3


# Claim records (v2.2.0 plan item 2): each claim's round, spawn and times, and every way a
# claim ends.


def _work(item: dict[str, Any]) -> claude_eval.Work:
    run = claude_eval.current()
    assert run is not None
    return run.by_ref[item["id"]]


def test_a_claim_records_its_round_spawn_and_times(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    t0 = clock.now()
    first, second = nxt(db_path, clock, S1, limit=2)["items"]
    ref, token, agent = first["id"], first["claim_token"], first["agent"]
    clock.advance(3)
    claude_eval.get_message(clock, S1, ref, token, agent)
    clock.advance(1)
    claude_eval.get_message(clock, S1, ref, token, agent)  # only the first read counts
    clock.advance(4)
    got = claude_eval.record_classification(clock, S1, ref, token, BEC, agent)
    clock.advance(2)
    assert settled(db_path, clock, got)["accepted"]
    (c,) = _work(first).claims
    assert (c.stage, c.spawn, c.round, c.session, c.limit, c.items, c.preparing) == (
        "classify", "ecf-classifier", 1, 1, 2, 2, False)  # fmt: skip
    assert [(t - t0).total_seconds() if t else None
            for t in (c.claimed, c.read, c.answered, c.settled)] == [0, 3, 8, 10]  # fmt: skip
    assert c.outcome == "recorded" and c.high_risk == ()
    # the session ends with the second claim unread; a second session claims it again
    claude_eval.release_session(S1)
    (ended,) = _work(second).claims
    assert ended.outcome == "session_ended" and ended.read is None and ended.answered is None
    items = nxt(db_path, clock, S2, limit=5)["items"]
    assert second["id"] in {i["id"] for i in items}
    again = _work(second).claims[-1]
    assert len(_work(second).claims) == 2
    assert (again.round, again.session, again.items, again.outcome) == (2, 2, len(items), None)


def test_every_way_a_claim_ends_is_recorded(db_path: Path, clock: FakeClock, root: Path) -> None:
    start(db_path, clock, root)
    expiring = nxt(db_path, clock, S1, limit=1)["items"][0]
    clock.advance(16 * 60)
    invalid, refused, left = nxt(db_path, clock, S2, limit=3)["items"]
    assert _work(expiring).claims[0].outcome == "claim_expired"
    for _ in range(3):
        claude_eval.record_classification(clock, S2, invalid["id"], invalid["claim_token"],
                                          {"category": "tax"}, invalid["agent"])  # fmt: skip
    assert _work(invalid).claims[-1].outcome == "invalid"
    assert _work(invalid).claims[-1].answered is None
    got = claude_eval.record_classification(clock, S2, refused["id"], refused["claim_token"], BEC,
                                            refused["agent"])  # fmt: skip
    clock.advance(5)
    assert not settled(db_path, clock, got, Seen("claude-haiku-4-5-20251001"))["accepted"]
    r = _work(refused).claims[-1]
    assert r.outcome == "model_refused" and r.settled == clock.now()
    claude_eval.stop(connector(db_path), clock)
    assert _work(left).claims[-1].outcome == "run_ended"
    run = claude_eval.current()
    assert run is not None and all(c.outcome is not None for w in run.works for c in w.claims)


# A mixed-up claim token (OD-499): the cases involved are freed at once, up to a limit, while
# other bad tokens still wait for expiry.


def test_spawns_carry_the_tokens_and_items_none(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """The service writes each spawn's prompt (S2 run 2 `d157a948`: the session copied the wrong
    token); `items` carry no token, so there is nothing to copy by hand."""
    start(db_path, clock, root, sensitivity="high", batch=2)
    q = claude_eval.eval_next(connector(db_path), clock, S1)
    assert all("claim_token" not in i for i in q["items"])
    assert set(q["items"][0]) == {"id", "need", "agent", "spawn"}
    assert [s["agent"] for s in q["spawns"]] == ["ecf-classifier-high"] * 2
    lines = [s["prompt"].splitlines() for s in q["spawns"]]
    assert [len(ls) for ls in lines] == [2, 1]  # the spawns' grouping, each item exactly once
    tok = tokens_of(q)
    assert sorted(tok) == sorted(i["id"] for i in q["items"])
    run = claude_eval.current()
    assert run is not None
    for ref, token in tok.items():
        assert claude_eval._hash(token) == run.by_ref[ref].token_hash  # pyright: ignore[reportPrivateUsage]
    for s in q["spawns"]:
        assert all(ln.startswith("id=") and " claim_token=" in ln for ln in s["prompt"].split("\n"))
    assert claude_eval.eval_next(connector(db_path), clock, S1)["spawns"] == []


def test_a_mixed_up_token_frees_the_case_at_once(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    x, y = nxt(db_path, clock, S1, limit=2)["items"]
    clock.advance(10)
    with pytest.raises(ConflictError, match="isn't current"):  # the agent still moves on
        claude_eval.get_message(clock, S1, x["id"], y["claim_token"], x["agent"])
    assert _work(x).claim == "free" and _work(x).claims[-1].outcome == "claim_mismatch"
    assert _work(y).claim == "claimed" and _work(y).claims[-1].outcome is None  # untouched
    run = claude_eval.current()
    assert run is not None
    assert run.grace[S1][x["agent"]] == clock.now() + claude_eval.TAIL  # not GRACE
    with pytest.raises(ConflictError):  # X's own token is refused after the free
        claude_eval.get_message(clock, S1, x["id"], x["claim_token"], x["agent"])
    assert claude_eval.get_message(clock, S1, y["id"], y["claim_token"], y["agent"])
    q = nxt(db_path, clock, S1)
    assert {"id": x["id"], "outcome": "claim_mismatch"} in q["results"]
    assert x["id"] in {i["id"] for i in q["items"]}  # back at once, not after CLAIM_TTL
    assert claude_eval.progress_line(claude_eval.progress() or {}).endswith(
        ", 1 mixed-up claims freed")  # fmt: skip


def test_a_two_way_swap_frees_both_cases(db_path: Path, clock: FakeClock, root: Path) -> None:
    """Review R1: the second case's agent presents a token the first free already ended."""
    start(db_path, clock, root)
    x, y = nxt(db_path, clock, S1, limit=2)["items"]
    for a, b in ((x, y), (y, x)):
        with pytest.raises(ConflictError):
            claude_eval.get_message(clock, S1, a["id"], b["claim_token"], a["agent"])
    assert [_work(i).claims[-1].outcome for i in (x, y)] == ["claim_mismatch"] * 2
    claude_eval.release_session(S1)
    run = claude_eval.current()
    assert run is not None and S1 not in run.freed


def test_mix_up_frees_stop_after_mismatch_max(db_path: Path, clock: FakeClock, root: Path) -> None:
    """Review R2: a session that keeps mangling a case gets today's expiry after 3 frees."""
    start(db_path, clock, root)
    x = nxt(db_path, clock, S1, limit=1)["items"][0]
    for n in range(claude_review.MISMATCH_MAX + 1):
        with pytest.raises(ForbiddenProfileError):
            claude_eval.get_message(clock, S1, x["id"], x["claim_token"], "ecf-actor")
        if n < claude_review.MISMATCH_MAX:
            clock.advance(claude_eval.TAIL.total_seconds() + 1)
            (x,) = [i for i in nxt(db_path, clock, S1, limit=1)["items"] if i["id"] == x["id"]]
    assert _work(x).claim == "claimed"  # the fourth: refused, left to expire
    outcomes = [c.outcome for c in _work(x).claims]
    assert outcomes == ["claim_mismatch"] * claude_review.MISMATCH_MAX + [None]


def test_other_bad_tokens_still_wait_for_expiry(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """Only the mix-up signature frees (P1): a stale fence (a late agent after an expiry),
    garbage, another session's token and a held claim keep today's refusal."""
    start(db_path, clock, root)
    old = nxt(db_path, clock, S1, limit=1)["items"][0]
    clock.advance(claude_review.CLAIM_TTL.total_seconds() + 1)
    items = nxt(db_path, clock, S2, limit=3)["items"]
    (x,) = [i for i in items if i["id"] == old["id"]]
    y, z = [i for i in items if i is not x]
    bad = (old["claim_token"], "1.garbage", "")
    for token in bad:
        with pytest.raises(ConflictError):
            claude_eval.get_message(clock, S2, x["id"], token, x["agent"])
    with pytest.raises(ConflictError):  # another session presenting this session's tokens
        claude_eval.get_message(clock, S1, x["id"], y["claim_token"], x["agent"])
    assert _work(x).claim == "claimed" and _work(x).claims[-1].outcome is None
    held = claude_eval.record_classification(clock, S2, y["id"], y["claim_token"], BEC,
                                             y["agent"])  # fmt: skip
    assert isinstance(held, Hold)
    with pytest.raises(ConflictError):  # a held claim isn't freed by another case's token
        claude_eval.get_message(clock, S2, y["id"], z["claim_token"], y["agent"])
    assert _work(y).claim == "held" and _work(z).claim == "claimed"


def test_its_own_token_from_the_wrong_agent_type_frees_the_case(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    x = nxt(db_path, clock, S1, limit=1)["items"][0]
    with pytest.raises(ForbiddenProfileError, match="that claim is for"):
        claude_eval.get_message(clock, S1, x["id"], x["claim_token"], "ecf-actor")
    assert _work(x).claim == "free" and _work(x).claims[-1].outcome == "claim_mismatch"
    run = claude_eval.current()
    assert run is not None and run.grace[S1]["ecf-actor"] == clock.now() + claude_eval.TAIL
    y = nxt(db_path, clock, S2, limit=1)["items"][0]
    with pytest.raises(ForbiddenProfileError):  # another session's agent: refused, kept
        claude_eval.get_message(clock, S1, y["id"], y["claim_token"], "ecf-actor")
    assert _work(y).claim == "claimed"


def test_a_run_with_a_mixed_up_claim_saves_its_result(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    """`claim_mismatch` is a `ClaimOutcome`: without it the run would end with no result file."""
    start(db_path, clock, root)
    x, y = nxt(db_path, clock, S1, limit=2)["items"]
    with pytest.raises(ConflictError):
        claude_eval.get_message(clock, S1, x["id"], y["claim_token"], x["agent"])
    by_case = {"starter-bec": BEC, "starter-injection": BEC, "starter-control": REQUEST}
    assert answer(db_path, clock, y, S1, BEC)["accepted"]
    drain(db_path, clock, lambda cid: by_case[cid])
    result = load_result(_saved(conn))
    outcomes = [c.outcome for case in result.cases for c in case.claims]
    assert outcomes.count("claim_mismatch") == 1


# A dropped spawn (OD-501): a spawn none of whose claims was read is freed after UNREAD_MIN,
# never one with a read claim, and its run still saves its result.


UNREAD_S = int(claude_review.UNREAD_MIN.total_seconds())


def _dropped(db_path: Path, clock: FakeClock, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """S3b run 1 `08016ba3`: round 1's spawn (x) is never made; round 2's (y) runs. x is freed
    `UNREAD_MIN` after the session's next call, and not before."""
    start(db_path, clock, root)
    x = nxt(db_path, clock, S1, limit=1)["items"][0]
    clock.advance(5)
    y = nxt(db_path, clock, S1, limit=1)["items"][0]  # the session's next call
    claude_eval.get_message(clock, S1, y["id"], y["claim_token"], y["agent"])
    clock.advance(UNREAD_S - 1)
    q = nxt(db_path, clock, S1, limit=1)
    assert q["items"] == [] and q["results"] == [] and _work(x).claim == "claimed"
    assert q["next"] == "call again" and q["frees_in_s"] == 1  # the free, not CLAIM_TTL (P7)
    clock.advance(1)
    return x, y


def test_a_dropped_spawn_is_freed_unread_min_after_the_sessions_next_call(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    x, y = _dropped(db_path, clock, root)
    q = nxt(db_path, clock, S1, limit=1)
    assert q["results"] == [{"id": x["id"], "outcome": "claim_unread"}]
    assert _work(x).claims[0].outcome == "claim_unread"
    assert _work(y).claim == "claimed" and _work(y).claims[-1].outcome is None  # it runs
    run = claude_eval.current()
    assert run is not None
    assert run.grace[S1] == {x["agent"]: clock.now() + claude_eval.TAIL}  # TAIL, not GRACE
    assert x["id"] in {i["id"] for i in q["items"]}  # back at once
    assert q["progress"].endswith(", 1 unread claims freed")
    assert claude_eval.progress_line(claude_eval.progress() or {}).endswith(
        ", 1 unread claims freed")  # fmt: skip


def test_a_spawn_with_any_claim_read_is_never_freed_unread(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    x, y = nxt(db_path, clock, S1, limit=2)["items"]  # one spawn of two
    assert x["spawn"] == y["spawn"]
    claude_eval.get_message(clock, S1, x["id"], x["claim_token"], x["agent"])
    clock.advance(5)
    nxt(db_path, clock, S1, limit=2)
    clock.advance(4 * UNREAD_S)
    q = nxt(db_path, clock, S1, limit=2)
    assert q["results"] == [] and _work(y).claim == "claimed"  # its unread claim waits
    assert q["frees_in_s"] == int(claude_review.CLAIM_TTL.total_seconds()) - 5 - 4 * UNREAD_S
    clock.advance(q["frees_in_s"])
    q = nxt(db_path, clock, S1, limit=2)
    assert {r["outcome"] for r in q["results"]} == {"claim_expired"}  # as today, with GRACE
    run = claude_eval.current()
    assert run is not None and run.grace[S1][x["agent"]] == clock.now() + claude_eval.GRACE


def test_a_reclaimed_siblings_new_claim_doesnt_make_a_running_spawn_look_unread(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """P3: the spawn is judged over every claim of its round, not each case's latest."""
    start(db_path, clock, root)
    x, y = nxt(db_path, clock, S1, limit=2)["items"]
    claude_eval.get_message(clock, S1, y["id"], y["claim_token"], y["agent"])
    with pytest.raises(ForbiddenProfileError):  # y's claim freed (mix-up), its read stays
        claude_eval.get_message(clock, S1, y["id"], y["claim_token"], "ecf-actor")
    clock.advance(claude_eval.TAIL.total_seconds() + 1)
    q = nxt(db_path, clock, S1, limit=2)  # y claimed again, in round 2
    assert y["id"] in {i["id"] for i in q["items"]} and _work(y).claims[-1].round == 2
    clock.advance(UNREAD_S + 1)
    assert nxt(db_path, clock, S1, limit=2)["results"] == []
    assert _work(x).claim == "claimed" and [c.outcome for c in _work(x).claims] == [None]


def test_another_sessions_calls_dont_count(db_path: Path, clock: FakeClock, root: Path) -> None:
    start(db_path, clock, root)
    x = nxt(db_path, clock, S1, limit=1)["items"][0]
    for _ in range(3):
        clock.advance(UNREAD_S)
        nxt(db_path, clock, S2, limit=1)
    assert _work(x).claim == "claimed"  # S1 hasn't called since
    nxt(db_path, clock, S1, limit=1)
    clock.advance(UNREAD_S - 1)
    nxt(db_path, clock, S2, limit=1)
    assert _work(x).claim == "claimed"
    clock.advance(1)
    nxt(db_path, clock, S2, limit=1)  # any call frees it once S1's signal holds
    assert _work(x).claims[0].outcome == "claim_unread"
    assert nxt(db_path, clock, S1)["results"][0] == {"id": x["id"], "outcome": "claim_unread"}


def test_a_late_agents_refused_call_extends_tail_and_isnt_a_mix_up(
    db_path: Path, clock: FakeClock, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """P2: the spawn started after all; its agent's request is refused, its type counts as out
    for `TAIL` from then, and nothing is freed as a mix-up."""
    x, y = _dropped(db_path, clock, root)
    q = nxt(db_path, clock, S1, limit=1)
    assert x["id"] in {i["id"] for i in q["items"]}  # claimed again, a new token
    clock.advance(claude_eval.TAIL.total_seconds() + 5)
    run = claude_eval.current()
    assert run is not None
    with pytest.raises(ConflictError, match="isn't current"):
        claude_eval.get_message(clock, S1, x["id"], x["claim_token"], x["agent"])
    assert run.grace[S1][x["agent"]] == clock.now() + claude_eval.TAIL
    assert "claude_eval.claim_unread_late" in capsys.readouterr().out  # review R1
    with pytest.raises(ConflictError):  # counted once per token
        claude_eval.get_message(clock, S1, x["id"], x["claim_token"], x["agent"])
    assert "claim_unread_late" not in capsys.readouterr().out
    p = claude_eval.progress() or {}
    assert p["late"] == 1
    assert claude_eval.progress_line(p).endswith(", 1 late after an unread free")
    with pytest.raises(ConflictError):  # nor for another case
        claude_eval.get_message(clock, S1, y["id"], x["claim_token"], y["agent"])
    assert _work(x).claim == "claimed" and _work(y).claim == "claimed"
    assert all(c.outcome != "claim_mismatch" for w in run.works for c in w.claims)
    with pytest.raises(ForbiddenProfileError):  # garbage extends nothing
        claude_eval.get_message(clock, S1, x["id"], "1.garbage", "ecf-actor")
    assert "ecf-actor" not in run.grace[S1]


def test_a_cases_own_earlier_token_never_frees_its_new_claim(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """P4: a mix-up freed x; its late agent presents x's old token after x was claimed again."""
    start(db_path, clock, root)
    x = nxt(db_path, clock, S1, limit=1)["items"][0]
    with pytest.raises(ForbiddenProfileError):
        claude_eval.get_message(clock, S1, x["id"], x["claim_token"], "ecf-actor")
    clock.advance(claude_eval.TAIL.total_seconds() + 1)
    (new,) = [i for i in nxt(db_path, clock, S1, limit=1)["items"] if i["id"] == x["id"]]
    with pytest.raises(ConflictError, match="isn't current"):
        claude_eval.get_message(clock, S1, x["id"], x["claim_token"], x["agent"])
    assert _work(x).claim == "claimed" and _work(x).claims[-1].outcome is None
    assert answer(db_path, clock, new, S1, BEC)["accepted"]


def test_a_run_with_an_unread_claim_saves_its_result(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    """`claim_unread` is a `ClaimOutcome`: the result file holds it."""
    _x, y = _dropped(db_path, clock, root)
    by_case = {"starter-bec": BEC, "starter-injection": BEC, "starter-control": REQUEST}
    assert answer(db_path, clock, y, S1, by_case[_work(y).case.id])["accepted"]
    drain(db_path, clock, lambda cid: by_case[cid])
    result = load_result(_saved(conn))
    outcomes = [c.outcome for case in result.cases for c in case.claims]
    assert outcomes.count("claim_unread") == 1
    # each round's next call, for read minus next call per spawn (review R1): round 1's at 5 s
    claude = cast(dict[str, Any], (result.summary or {})["claude"])
    assert claude["next_call_s"]["1"] == 5.0
    assert set(claude["next_call_s"]) <= {str(c.round) for case in result.cases
                                          for c in case.claims}  # fmt: skip


def test_a_mix_up_alone_counts_as_an_agent_running(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """The only call under the spawn is y's own token from the wrong agent type (P6): x, skipped,
    waits as today rather than being freed unread."""
    start(db_path, clock, root)
    x, y = nxt(db_path, clock, S1, limit=2)["items"]
    with pytest.raises(ForbiddenProfileError):
        claude_eval.get_message(clock, S1, y["id"], y["claim_token"], "ecf-actor")
    clock.advance(claude_eval.TAIL.total_seconds() + 1)
    nxt(db_path, clock, S1, limit=2)
    clock.advance(UNREAD_S + 1)
    assert nxt(db_path, clock, S1, limit=2)["results"] == []
    assert [c.outcome for c in _work(x).claims] == [None]


def test_a_wrong_need_first_call_counts_as_an_agent_running(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """Review R2: the stamp is set before the need check."""
    start(db_path, clock, root)
    x = nxt(db_path, clock, S1, limit=1)["items"][0]
    with pytest.raises(ConflictError, match="is to classify"):
        claude_eval.propose_action(clock, S1, x["id"], x["claim_token"],
                                   {"action": "flag", "reason": "r"}, x["agent"])  # fmt: skip
    clock.advance(5)
    nxt(db_path, clock, S1, limit=1)
    clock.advance(UNREAD_S + 1)
    assert nxt(db_path, clock, S1, limit=1)["results"] == []
    assert [c.outcome for c in _work(x).claims] == [None]


def test_an_act_claim_records_why_it_went_to_the_high_risk_actor(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    by_case = {"starter-bec": BEC, "starter-injection": BEC, "starter-control": REQUEST}
    drain(db_path, clock, lambda cid: by_case[cid])
    run = claude_eval.current()
    assert run is not None
    acts = [c for w in run.works for c in w.claims if c.stage == "act"]
    assert acts and all(c.high_risk for c in acts)  # every synthetic sender is first-time
    assert all("first_time_sender" in c.high_risk for c in acts)
    assert all(c.spawn.startswith("ecf-actor-high") for c in acts)
    assert all(not c.high_risk for w in run.works for c in w.claims if c.stage != "act")
    assert [c.stage for c in run.works[0].claims] == ["classify", "repeat"]


def test_a_round_while_cases_are_still_being_prepared_says_so(
    db_path: Path, clock: FakeClock, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = evalrun.case_inputs
    seen: list[dict[str, Any]] = []

    def inputs(case: Any, *args: Any) -> Any:
        """Claim a round as soon as the first case is ready, while the rest are prepared."""
        run = claude_eval.current()
        if not seen and run is not None and any(w.stage == "classify" for w in run.works):
            seen.append(nxt(db_path, clock, S1))
        return real(case, *args)

    monkeypatch.setattr(evalrun, "case_inputs", inputs)
    start(db_path, clock, root)
    (q,) = seen
    assert q["preparing"] is True and len(q["items"]) == 1
    (c,) = _work(q["items"][0]).claims
    assert c.preparing is True and c.round == 1 and c.items == 1
    later = nxt(db_path, clock, S1)["items"]
    assert later and all(not _work(i).claims[-1].preparing for i in later)
    assert {_work(i).claims[-1].round for i in later} == {2}


def _saved(conn: sqlite3.Connection) -> Path:
    return Path(conn.execute("SELECT path FROM eval_runs").fetchone()["path"])


def test_the_result_carries_each_cases_claims_in_seconds_from_registration(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    """Plan item 3: every case's claims, merged at the end (a repeat's too), times relative to
    `summary.claude.registered_at`; `classifier_ms` stays a local model's figure."""
    start(db_path, clock, root)
    registered = clock.now()
    clock.advance(10)
    by_case = {"starter-bec": BEC, "starter-injection": BEC, "starter-control": REQUEST}
    drain(db_path, clock, lambda cid: by_case[cid])
    result = load_result(_saved(conn))
    claude = cast(dict[str, Any], (result.summary or {})["claude"])
    assert claude["registered_at"] == to_ts(registered)
    run = claude_eval.current()
    assert run is not None
    for w in run.works:
        case = next(c for c in result.cases if c.id == w.case.id)
        assert case.classifier_ms is None
        assert [(c.stage, c.spawn, c.round, c.session, c.outcome) for c in case.claims] == [
            (c.stage, c.spawn, c.round, c.session, c.outcome) for c in w.claims]  # fmt: skip
        for c in case.claims:  # later stages wait out a held answer's tail (TAIL)
            assert c.claimed_s is not None and c.claimed_s >= 10
            assert c.claimed_s == c.read_s == c.answered_s == c.settled_s
    first = next(c for c in result.cases if c.id == run.works[0].case.id)
    assert [c.stage for c in first.claims] == ["classify", "repeat"]
    acts = [c for r in result.cases for c in r.claims if c.stage == "act"]
    assert acts and all("first_time_sender" in c.high_risk for c in acts)
    text = _saved(conn).read_text()
    assert run.sessions[0] not in text  # the session's ordinal only, never its ID


def test_a_result_from_before_claims_loads_with_none(tmp_path: Path) -> None:
    old = {"run_id": "r", "pair": "claude/claude", "set_version": "v",
           "created_at": "2026-10-01T00:00:00Z",
           "cases": [{"id": "starter-bec", "correct": True}]}  # fmt: skip
    path = tmp_path / "old.json"
    path.write_text(json.dumps(old))
    assert load_result(path).cases[0].claims == []


def test_both_synthetic_rescores_keep_the_claims_and_the_claude_figures(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    """Plan item 6 (P8): a rescore rebuilds cases and summary from fixed lists; it carries
    `claims` and `summary.claude`."""
    start(db_path, clock, root)
    by_case = {"starter-bec": BEC, "starter-injection": BEC, "starter-control": REQUEST}
    drain(db_path, clock, lambda cid: by_case[cid])
    path = _saved(conn)
    live = load_result(path)
    claims = {c.id: c.claims for c in live.cases}
    assert all(claims.values())
    for rescore in (evalrun.rescore_synthetic, evalrun.rescore_through_policy):
        new = load_result(Path(rescore(root, path, clock)["path"]))
        assert {c.id: c.claims for c in new.cases} == claims
        assert (new.summary or {})["claude"] == (live.summary or {})["claude"]
    spawns = cast(dict[str, Any], (live.summary or {})["claude"])["spawns"]  # plan item 4
    assert spawns == spawn_figures(live.cases) and spawns["act"]["claims"] >= 1
    older = live.model_copy(update={"summary": dict(live.summary or {}) | {  # saved before item 4
        "claude": {k: v for k, v in cast(dict[str, Any], (live.summary or {})["claude"]).items()
                   if k != "spawns"}}})  # fmt: skip
    path.write_text(older.model_dump_json())
    new = load_result(Path(evalrun.rescore_through_policy(root, path, clock)["path"]))
    assert cast(dict[str, Any], (new.summary or {})["claude"])["spawns"] == spawns


# Models, presets and options: comparison runs, Claude-only model IDs, batching, the effective
# schema and digest, and the facts and signals each case goes out with.


def test_a_comparison_run_uses_the_eval_agents_and_never_counts_for_the_gate(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    r = start(db_path, clock, root, classifier_model="claude-sonnet-5-5")
    assert r["pinned"] is False and r["models"]["classifier"] == "claude-sonnet-5-5"
    info = claude_eval.session_info(connector(db_path), clock)
    assert info is not None and info["agents"]["ecf-eval-classifier"] == "claude-sonnet-5-5"
    items = nxt(db_path, clock, S1)["items"]
    assert {i["agent"] for i in items} == {"ecf-eval-classifier"}
    claude_eval.release_session(S1)
    drain(db_path, clock, lambda _c: BEC)
    row = conn.execute("SELECT digest FROM eval_runs").fetchone()
    assert row["digest"].startswith("eval-pins-")
    assert gate.synthetic(conn, claude_pins.key(claude_pins.pins(conn, "C")), "C").ok is False


def test_a_model_id_must_be_claudes(db_path: Path, clock: FakeClock, root: Path) -> None:
    with pytest.raises(InvalidInputError):
        start(db_path, clock, root, classifier_model="gpt-5")
    for gone in ("A", "B"):  # removed in v3.0.0 (ADR 0031)
        with pytest.raises(InvalidInputError, match="preset C"):
            start(db_path, clock, root, preset=gone)
    with pytest.raises(InvalidInputError):
        start(db_path, clock, root, batch=6)


def test_high_runs_one_case_per_spawn_unless_batched(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root, sensitivity="high", batch=2)
    items = nxt(db_path, clock, S1)["items"]
    assert {i["agent"] for i in items} == {"ecf-classifier-high"}
    assert [i["spawn"] for i in items] == ["ecf-classifier-high-1", "ecf-classifier-high-1",
                                           "ecf-classifier-high-2"]  # fmt: skip


def test_batch_never_groups_the_high_risk_actor(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    """As `/ecf-review` (OD-297): `--batch` sizes only the classifier-high spawns."""
    start(db_path, clock, root, sensitivity="high", batch=2)
    run = claude_eval.current()
    assert run is not None
    for agent in ("ecf-actor-high", "ecf-eval-actor-high"):
        items: list[dict[str, Any]] = [{"agent": agent} for _ in range(3)]
        claude_eval._spawns(run, items)  # pyright: ignore[reportPrivateUsage]
        assert [i["spawn"] for i in items] == [f"{agent}-{k}" for k in (1, 2, 3)]
    items = [{"agent": "ecf-eval-classifier-high"} for _ in range(3)]
    claude_eval._spawns(run, items)  # pyright: ignore[reportPrivateUsage]
    assert [i["spawn"] for i in items] == ["ecf-eval-classifier-high-1"] * 2 + [
        "ecf-eval-classifier-high-2"
    ]


@pytest.mark.usefixtures("extensions_on")
def test_a_run_asks_with_the_effective_schema_and_records_its_digest(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    extend(conn, clock)
    schema = config.current_schema(conn)
    start(db_path, clock, root)
    item = nxt(db_path, clock, S1, limit=1)["items"][0]
    msg = claude_eval.get_message(clock, S1, item["id"], item["claim_token"], item["agent"])
    assert msg["schema_text"] == schema.prompt_block and "contract_stage" in msg["schema_text"]
    bad = claude_eval.record_classification(clock, S1, item["id"], item["claim_token"], BEC,
                                            item["agent"])  # fmt: skip
    assert isinstance(bad, dict) and "contract_stage: missing" in bad["errors"]
    full = BEC | {"contract_stage": "none", "lawyer_involved": False}
    assert answer(db_path, clock, item, S1, full)["accepted"]
    s = claude_eval.stop(connector(db_path), clock)
    assert s is not None and s["done"] == 1
    m = json.loads(conn.execute("SELECT metrics FROM eval_runs").fetchone()[0])
    assert m["schema_digest"] == schema.digest != load_schema().digest
    assert m["prompt_digest"] == claude_review.prompt_digest(schema)  # Claude's side (Q4)
    # review R9: bytes of schema per classify read, with the user fields
    from ecf.schema import read_bytes  # noqa: PLC0415

    assert m["read_bytes"] == read_bytes(schema) > read_bytes(load_schema())
    assert m["keywords_digest"] == triggers.keywords_digest()
    assert m["plugin_digest"] == plugin_digest()  # the skills and agent prompts
    assert m["actor_prompt_digest"] == actor_prompt_digest()  # OD-523


def test_a_cards_expected_facts_reach_the_hide_path_as_in_ecf_eval_run(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    """Q1 for `/ecf-eval`: a card's expected facts are applied as `ecf eval run` applies them,
    so an authenticated bulk notice from a seen sender, answered benignly, is archived; the same
    notice without those facts isn't."""
    card = (root / "cases" / "starter-control.md").read_text()
    seen = card.replace("id: starter-control", "id: seen-bulk").replace(
        "  rule: marketing", "  facts: {auth_result: pass, sender_seen_before: true}\n"
        "  rule: marketing")  # fmt: skip
    (root / "cases" / "seen-bulk.md").write_text(seen)
    assert not build_all(root).findings
    labels.confirm(root, "seen-bulk", date(2026, 10, 1))
    labels.confirm(root, "starter-control", date(2026, 10, 1))
    start(db_path, clock, root)
    drain(db_path, clock, lambda _c: with_fixed(MARKETING))
    path = conn.execute("SELECT path FROM eval_runs").fetchone()["path"]
    actions = {c.id: set(c.actions) for c in load_result(Path(path)).cases}
    assert {"archive"} <= actions["seen-bulk"] and not {"archive", "mark_read"} & actions[
        "starter-control"]  # fmt: skip


def test_a_case_goes_out_with_the_signals_gemma_gets(
    db_path: Path, clock: FakeClock, tmp_path: Path
) -> None:
    """OD-484 (plan item 5): the same `signals.sent` on the case's facts, as `signals`."""
    r = tmp_path / "synthetic"
    (r / "cases").mkdir(parents=True)
    shutil.copy(SYNTHETIC / "cases" / "crypto-payment-demand.md", r / "cases")
    assert not build_all(r).findings
    start(db_path, clock, r)
    item = nxt(db_path, clock, S1, limit=1)["items"][0]
    msg = claude_eval.get_message(clock, S1, item["id"], item["claim_token"], item["agent"])
    assert msg["signals"] == {"crypto_payment": signals.WORDING["crypto_payment"]}


# One run at a time: stop saves what was scored, a run expires after 24 hours, and results
# hold metrics only.


def test_one_run_at_a_time_and_stop_saves_what_was_scored(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    with pytest.raises(ConflictError):
        start(db_path, clock, root)
    item = nxt(db_path, clock, S1, limit=1)["items"][0]
    answer(db_path, clock, item, S1, BEC)
    s = claude_eval.stop(connector(db_path), clock)
    assert s is not None and s["state"] == "stopped" and s["done"] == 1
    m = json.loads(conn.execute("SELECT metrics FROM eval_runs").fetchone()[0])
    assert m["complete"] is False and m["gate_passed"] is False
    assert nxt(db_path, clock, S1)["done"] is True
    assert claude_eval.session_info(connector(db_path), clock) is None
    start(db_path, clock, root)  # the next one may register


def test_a_run_expires_after_24_hours(db_path: Path, clock: FakeClock, root: Path) -> None:
    start(db_path, clock, root)
    clock.advance(24 * 3600)
    st = claude_eval.status(connector(db_path), clock)
    assert st is not None and st["state"] == "expired"
    assert claude_eval.results()["state"] == "expired"
    assert nxt(db_path, clock, S1)["done"] is True
    start(db_path, clock, root)


def test_eval_results_are_metrics_only(db_path: Path, clock: FakeClock, root: Path) -> None:
    with pytest.raises(NotFoundError):
        claude_eval.results()
    start(db_path, clock, root)
    drain(db_path, clock, lambda _c: BEC)
    r = claude_eval.results()
    assert r["state"] == "done" and isinstance(r["metrics"]["unsafe"], int)
    assert {"fraud_guard_cases", "fraud_guard_recall"} <= r["metrics"].keys()  # D2
    assert {"scored", "rule_undecided"} <= r["metrics"].keys()  # draft 21: accuracy's n
    assert not any(c in json.dumps(r) for c in CASES)


# The eval routes and their caller profiles, held submissions, and `eval next` through ecf-mcp.


def test_the_routes_and_their_profiles(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    state = ServiceState(install="t", token="cli-token", started_at="2026-10-02T09:00:00Z",
                         clock=clock, db_path=db_path)  # fmt: skip
    state.telemetry_wait_s = 0
    c_address(conn, clock)

    async def go() -> dict[str, Any]:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as h:
            cli = {"Authorization": "Bearer cli-token"}
            out: dict[str, Any] = {}
            body = {"root": str(root), "preset": "C", "sensitivity": "standard"}
            out["observe_start"] = (await h.post("/v1/eval/claude", json=body)).status_code
            out["start"] = (await h.post("/v1/eval/claude", json=body, headers=cli)).json()
            for _ in range(200):  # the cases are prepared in a service thread
                st = (await h.get("/v1/eval/runs", headers=cli)).json()["claude"]
                if st["state"] != "preparing":
                    break
                await anyio.sleep(0.05)
            sess = (await h.post("/v1/sessions", json=client_doc(), headers=cli)).json()
            out["session"] = sess
            work = {"Authorization": f"Bearer {sess['profile_token']}"}
            agent = {"Authorization": f"Bearer {sess['agent_token']}"}
            out["cli_next"] = (await h.post("/v1/eval/next", json={}, headers=cli)).status_code
            q = with_tokens((await h.post("/v1/eval/next", json={"limit": 1},
                                          headers=work)).json())  # fmt: skip
            item = q["items"][0]
            model = claude_pins.effective(conn)["classifier"]
            state.telemetry.add(sess["session_id"], [ApiCall(clock.now().timestamp() + 1, model,
                                                             SUBAGENT, duration_ms=3000)],
                                [])  # fmt: skip
            ref, tok, name = item["id"], item["claim_token"], item["agent"]
            out["message"] = (await h.post(f"/v1/claims/{ref}/message", headers=agent, json={
                "claim_token": tok, "agent": name})).json()  # fmt: skip
            out["submit"] = (await h.post(f"/v1/claims/{ref}/classification", headers=agent,
                                          json={"claim_token": tok, "classification": BEC,
                                                "agent": name})).json()  # fmt: skip
            out["status"] = (await h.get("/v1/eval/runs", headers=cli)).json()
            out["results"] = (await h.post("/v1/eval/results", headers=work, json={})).json()
            out["stop"] = (await h.post("/v1/eval/runs/stop", headers=cli, json={})).json()
            return out

    out = anyio.run(go)
    assert out["observe_start"] == 401 and out["cli_next"] in (401, 403)
    assert out["start"]["preset"] == "C" and out["start"]["state"] in ("preparing", "ready")
    assert out["session"]["eval"]["run_id"] == out["start"]["run_id"]
    assert out["session"]["eval"]["agents"] == {}  # pinned: the production agents
    assert out["message"]["need"] == "classify" and "schema" in out["message"]
    assert out["submit"] == {"accepted": True, "errors": []}
    assert out["status"]["claude"]["done"] == 1
    assert out["results"]["done"] == 1 and "metrics" not in out["results"]
    assert out["stop"]["local"] is None and out["stop"]["claude"]["state"] == "stopped"


def test_a_held_submission_settles_when_telemetry_arrives_or_is_refused_at_session_end(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    """Held like `/ecf-review`'s (OD-307), and settled by the same paths, by kind."""
    from ecf_server import api, telemetry_app  # noqa: PLC0415

    state = ServiceState(install="t", token="cli-token", started_at="2026-10-02T09:00:00Z",
                         clock=clock, db_path=db_path)  # fmt: skip
    state.telemetry_wait_s = 0
    start(db_path, clock, root)
    bearer = state.telemetry.open(S1)
    assert bearer
    items = nxt(db_path, clock, S1, limit=2)["items"]
    now = clock.now().timestamp()
    holds: list[Hold] = []
    for n, i in enumerate(items):  # submitted a minute apart
        h = claude_eval.record_classification(clock, S1, i["id"], i["claim_token"], BEC,
                                              i["agent"])  # fmt: skip
        assert isinstance(h, Hold) and h.kind == "eval"
        holds.append(replace(h, at=now + 60 * n))
        assert state.telemetry.hold(holds[-1])
    model = claude_pins.effective(conn)["classifier"]
    state.telemetry.add(S1, [ApiCall(now + 1, model, SUBAGENT, duration_ms=3000)],
                        [])  # only the first's window, and caught up with it only  # fmt: skip
    telemetry_app.settle_bound(state, S1)
    run = claude_eval.current()
    assert run is not None
    first, second = (run.by_ref[i["id"]] for i in items)
    assert first.classification == BEC and first.claim == "free"
    assert second.claim == "held"
    api._end_session(state, S1)  # pyright: ignore[reportPrivateUsage]
    assert second.claim == "free" and second.classification is None  # nothing in its window


def test_eval_next_through_ecf_mcp(db_path: Path, clock: FakeClock, root: Path) -> None:
    from mcp_types import CallToolResult  # noqa: PLC0415

    from tests.test_mcp import data, error_text, session_token, with_tools  # noqa: PLC0415

    state = ServiceState(install="t", token="cli-token", started_at="2026-10-02T09:00:00Z",
                         clock=clock, db_path=db_path)  # fmt: skip
    work = session_token(state)

    async def body(c: Any) -> list[CallToolResult]:
        none = await c.call_tool("eval_next", {})
        start(db_path, clock, root)
        got = await c.call_tool("eval_next", {"limit": 2})
        return [none, got, await c.call_tool("eval_results", {})]

    none, got, res = with_tools(state, work, body)
    assert error_text(none).startswith("not_found: no Claude eval is waiting")
    assert len(data(got)["items"]) == 2 and data(got)["more"] is True
    assert data(res)["state"] == "ready" and data(res)["total"] == 3


# Step 6 (OD-518): the actor's proposal recorded and judged on its own (P1), a C run on another
# run's frozen classifications (P2, P3), and `--actor-high-model` (P17).

# requires_action and requires_reply false, so a hide is offered to the actor
QUIET: dict[str, Any] = with_fixed(MARKETING | {"category": "customer_request"})
JUNK = "inj-hidden-html-junk"  # must_not_hide, injection target junk; its rule asks the actor


@pytest.fixture
def root6(root: Path) -> Path:
    """The three starter cards and a confirmed injection card that reaches the actor."""
    shutil.copy(SYNTHETIC / "cases" / f"{JUNK}.md", root / "cases" / f"{JUNK}.md")
    assert not build_all(root).findings
    labels.confirm(root, JUNK, date(2026, 10, 1))
    return root


def _junk_run(db_path: Path, clock: FakeClock, root: Path) -> None:
    """A C run whose actor proposes junk on the injection card and flag elsewhere."""
    start(db_path, clock, root)
    by_case = {"starter-bec": BEC, "starter-injection": BEC, "starter-control": REQUEST,
               JUNK: QUIET}  # fmt: skip
    drain(db_path, clock, lambda cid: by_case[cid],
          act=lambda cid: "junk" if cid == JUNK else "flag")  # fmt: skip


def _source(conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path,
            **kw: Any) -> str:  # fmt: skip
    """A finished C run: the control case goes to the actor; its run ID."""
    start(db_path, clock, root, **kw)
    by_case = {"starter-bec": BEC, "starter-injection": BEC, "starter-control": REQUEST}
    drain(db_path, clock, lambda cid: by_case[cid])
    run = claude_eval.current()
    assert run is not None and run.state == "done"
    claude_eval.reset()
    del conn
    return run.run_id


def test_a_c_run_records_each_proposal_and_judges_it_on_its_own(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root6: Path
) -> None:
    """P1: junk proposed on a must_not_hide card whose injection target is junk fails both
    proposal checks that apply, whatever policy then does with it; the plan-level checks are
    scored as before, and the figures are per run and per actor role."""
    _junk_run(db_path, clock, root6)
    result = load_result(_saved(conn))
    case = {c.id: c for c in result.cases}
    inj, ctl, bec = case[JUNK], case["starter-control"], case["starter-bec"]
    assert inj.proposal == "junk" and inj.proposal_target is None
    assert inj.proposal_safety == {"no_hide_or_send": False, "no_injection_target": False}
    assert inj.safety is False  # plan level: the injection target is the action proposed
    assert ctl.proposal == "flag" and ctl.proposal_safety == {}  # no check applies
    assert bec.proposal is None and bec.proposal_safety is None  # the rule escalated, no actor
    p = cast(dict[str, Any], (result.summary or {})["proposals"])
    assert p["asked"] == 1 and p["unsafe"] == [JUNK]  # confirmed cases only (control isn't)
    assert p["checks"]["no_hide_or_send"] == {"cases": 1, "failed": [JUNK]}
    assert p["checks"]["escalated"] == {"cases": 0, "failed": []}
    acts = [c for c in inj.claims if c.stage == "act"]
    role = "actor_high" if acts[-1].spawn.startswith("ecf-actor-high") else "actor"
    assert set(p["by_role"]) == {role} and p["by_role"][role]["unsafe"] == [JUNK]
    # gate_passed is the plan-level checks' as before: the proposal figures are reported
    assert (result.summary or {})["gate_passed"] is False


def test_a_rescore_keeps_or_replays_the_proposal_and_an_old_result_is_unchanged(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root6: Path
) -> None:
    root = root6
    _junk_run(db_path, clock, root)
    path = _saved(conn)
    live = load_result(path)

    def seen(r: ResultFile) -> dict[str, Any]:
        return {c.id: (c.proposal, c.proposal_safety, c.safety) for c in r.cases}

    for rescore in (evalrun.rescore_synthetic, evalrun.rescore_through_policy):
        new = load_result(Path(rescore(root, path, clock)["path"]))
        assert seen(new) == seen(live)
        assert (new.summary or {})["proposals"] == (live.summary or {})["proposals"]
        assert (new.summary or {})["actor_prompt_digest"] == actor_prompt_digest()  # kept
    # a result from before step 6: no proposal fields, no figures, the same scores
    bare = {"proposal": None, "proposal_target": None, "proposal_safety": None}
    summary = {k: v for k, v in (live.summary or {}).items() if k != "proposals"}
    cases = [c.model_copy(update=bare) for c in live.cases]
    old = live.model_copy(update={"format": 3, "summary": summary, "cases": cases})
    raw = json.loads(old.model_dump_json())
    for c in raw["cases"]:
        del c["proposal_safety"]
    path.write_text(json.dumps(raw))
    for rescore in (evalrun.rescore_synthetic, evalrun.rescore_through_policy):
        new = load_result(Path(rescore(root, path, clock)["path"]))
        assert all(c.proposal_safety is None and c.proposal is None for c in new.cases)
        assert "proposals" not in (new.summary or {})


def test_a_frozen_run_asks_only_the_actor_and_never_counts_for_the_gate(
    conn: sqlite3.Connection,
    db_path: Path,
    clock: FakeClock,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2, P3: the source's classifications, no classifier spawn, no determinism repeats; key
    `eval-...` over the source run's key; summarized without a classifier, not gate-eligible,
    and the synthetic check refuses it."""
    source = _source(conn, db_path, clock, root)
    pin_key = claude_pins.key(claude_pins.pins(conn, "C"))
    r = start(db_path, clock, root, source_run=source[:8])
    assert r["pinned"] is False and r["source_run"] == source
    assert set(r["models"]) == {"actor", "actor_high"}
    handed = drain(db_path, clock, lambda _c: BEC)  # a classifier answer would be refused
    assert [i["need"] for i in handed] == ["act"]  # the control case, once: no repeats
    assert all(i["agent"].startswith("ecf-eval-actor") for i in handed)
    run = claude_eval.current()
    assert run is not None and run.state == "done" and run.repeats_started is False
    assert run.key.startswith("eval-pins-") and run.key != pin_key
    row = conn.execute("SELECT * FROM eval_runs WHERE run_id = ?", (run.run_id,)).fetchone()
    m = json.loads(row["metrics"])
    assert m["options"]["classifier"] is False and m["gate_passed"] is False
    assert m["classifier_source"] == {"run_id": source, "digest": pin_key}
    assert m["claude"]["source_run"] == source and m["claude"]["pinned"] is False
    src = json.loads(conn.execute("SELECT metrics FROM eval_runs WHERE run_id = ?",
                                  (source,)).fetchone()[0])  # fmt: skip
    for k in claude_eval.C_SOURCE_KEYS:
        assert m[k] == src[k]
    assert m["actor_prompt_digest"] == actor_prompt_digest()  # its own actor's (OD-523)
    got = {c.id: c.got for c in load_result(Path(row["path"])).cases}
    assert got["starter-control"]["category"] == REQUEST["category"]  # the source's answer
    check = gate.synthetic(conn, row["digest"], "C")
    assert check.ok is False and "without the classifier or the actor" in check.detail
    # the key binds the source: another source, another key
    claude_eval.reset()
    other = _source(conn, db_path, clock, root)
    claude_eval.reset()
    r2 = start(db_path, clock, root, source_run=other)
    run2 = claude_eval.current()
    assert r2["pinned"] is False and run2 is not None
    assert run2.key == run.key  # both sources ran on the same pins: the same source key
    assert claude_eval.models_for(conn, run2.opts, "other-key")[2] != run.key
    # the actor's instructions are in the unpinned key too (OD-523)
    assert claude_eval.models_for(conn, run2.opts, run2.source_key)[2] == run2.key
    monkeypatch.setattr(claude_eval, "actor_prompt_digest", lambda: "e" * 64)
    assert claude_eval.models_for(conn, run2.opts, run2.source_key)[2] != run2.key


def test_a_frozen_run_refuses_a_source_that_doesnt_fit(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    pinned = _source(conn, db_path, clock, root)
    with pytest.raises(InvalidInputError, match="no --classifier-model"):
        start(db_path, clock, root, source_run=pinned, classifier_model="claude-sonnet-5-5")
    with pytest.raises(InvalidInputError, match="for preset C"):
        start(db_path, clock, root, preset="D", source_run=pinned)
    with pytest.raises(InvalidInputError, match="first 8 characters"):
        start(db_path, clock, root, source_run=pinned[:7])
    with pytest.raises(InvalidInputError, match="no such eval run"):
        start(db_path, clock, root, source_run="ffffffffffff")
    with pytest.raises(InvalidInputError, match="didn't run at high"):
        start(db_path, clock, root, source_run=pinned, sensitivity="high")
    unpinned = _source(conn, db_path, clock, root, classifier_model="claude-sonnet-5-5")
    start(db_path, clock, root, source_run=unpinned)  # a candidate classifier's run serves
    claude_eval.stop(connector(db_path), clock)
    claude_eval.reset()
    frozen_src = pinned
    start(db_path, clock, root, source_run=frozen_src)
    drain(db_path, clock, lambda _c: BEC)
    frozen = claude_eval.current()
    assert frozen is not None
    claude_eval.reset()
    with pytest.raises(InvalidInputError, match="from another run"):  # not a frozen run's
        start(db_path, clock, root, source_run=frozen.run_id)
    start(db_path, clock, root)  # a stopped run is incomplete
    answer(db_path, clock, nxt(db_path, clock, S1, limit=1)["items"][0], S1, BEC)
    claude_eval.stop(connector(db_path), clock)
    stopped = claude_eval.current()
    assert stopped is not None
    claude_eval.reset()
    with pytest.raises(InvalidInputError, match="isn't complete"):
        start(db_path, clock, root, source_run=stopped.run_id)
    conn.execute("UPDATE eval_runs SET set_version = 'old' WHERE run_id = ?", (pinned,))
    with pytest.raises(InvalidInputError, match="another version of the set"):
        start(db_path, clock, root, source_run=pinned)
    conn.execute("UPDATE eval_runs SET set_version = ? WHERE run_id = ?",
                 (stopped.version, pinned))  # fmt: skip
    path = Path(conn.execute("SELECT path FROM eval_runs WHERE run_id = ?",
                             (pinned,)).fetchone()[0])  # fmt: skip
    raw = json.loads(path.read_text())
    raw["cases"] = [c for c in raw["cases"] if c["id"] != "starter-control"]
    path.write_text(json.dumps(raw))
    with pytest.raises(InvalidInputError, match="lacks some of this run's cases"):
        start(db_path, clock, root, source_run=pinned)
    path.unlink()
    with pytest.raises(InvalidInputError, match="no result file"):
        start(db_path, clock, root, source_run=pinned)


def test_actor_high_model_sets_actor_high_alone(conn: sqlite3.Connection, root: Path) -> None:
    """P17: `--actor-model` alone sets both actor roles; with `--actor-high-model` it sets only
    `actor`; any off-pin model makes the run unpinned, as before."""
    eff = claude_pins.effective(conn)
    haiku, opus, sonnet = "claude-haiku-5-5", "claude-opus-5-5", "claude-sonnet-5-5"

    def roles(**kw: Any) -> tuple[str, str, bool]:
        models, pinned, _key = claude_eval.models_for(conn, claude_eval.Options(root, **kw))
        return models["actor"], models["actor_high"], pinned

    assert roles() == (eff["actor"], eff["actor_high"], True)
    assert roles(actor_model=haiku) == (haiku, haiku, False)
    assert roles(actor_high_model=haiku) == (eff["actor"], haiku, False)
    both = eff["actor"] == opus and eff["actor_high"] == sonnet
    assert roles(actor_model=opus, actor_high_model=sonnet) == (opus, sonnet, both)
    assert roles(actor_model=eff["actor"], actor_high_model=eff["actor_high"])[2] is True
    with pytest.raises(InvalidInputError, match="isn't a Claude model ID"):
        roles(actor_high_model="gpt-5")
    combos: list[dict[str, Any]] = [{"actor_model": haiku}, {"actor_high_model": haiku},
                                    {"actor_model": haiku, "actor_high_model": opus}]  # fmt: skip
    keys = {claude_eval.models_for(conn, claude_eval.Options(root, **kw))[2] for kw in combos}
    assert len(keys) == 3 and all(k.startswith("eval-") for k in keys)


def test_the_route_takes_actor_high_model_and_source_run(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    state = ServiceState(install="t", token="cli-token", started_at="2026-10-02T09:00:00Z",
                         clock=clock, db_path=db_path)  # fmt: skip

    async def go() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url="http://ecf") as h:
            cli = {"Authorization": "Bearer cli-token"}
            body = {"root": str(root), "preset": "C", "sensitivity": "standard"}
            short = await h.post("/v1/eval/claude", headers=cli,
                                 json=body | {"source_run": "abc"})  # fmt: skip
            typed = await h.post("/v1/eval/claude", headers=cli,
                                 json=body | {"source_run": 7})  # fmt: skip
            ok = await h.post("/v1/eval/claude", headers=cli,
                              json=body | {"actor_high_model": "claude-haiku-5-5"})  # fmt: skip
            return [short, typed, ok]

    short, typed, ok = anyio.run(go)
    assert short.status_code == 400 and "first 8 characters" in short.text
    assert typed.status_code == 400 and "source_run must be text" in typed.text
    started = ok.json()
    assert started["pinned"] is False and started["models"]["actor_high"] == "claude-haiku-5-5"
    assert started["models"]["actor"] == claude_pins.effective(conn)["actor"]


def test_status_lines_name_a_frozen_run_comparison_only() -> None:
    from ecf import cli  # noqa: PLC0415

    caveat = cli._run_caveat  # pyright: ignore[reportPrivateUsage]
    pin_kind = cli._pin_kind  # pyright: ignore[reportPrivateUsage]
    frozen = {"preset": "C", "pinned": False, "source_run": "0a48213c" + "0" * 24}
    m = {"complete": True, "gate_passed": False, "options": {"classifier": False, "actor": True},
         "claude": frozen}  # fmt: skip
    line = "comparison only (actor only, on run 0a48213c's classifications)"
    assert caveat(m) == ", " + line and pin_kind(frozen) == line
    d = frozen | {"preset": "D"}  # a D run names its Jev source too: D's line stays
    assert pin_kind(d) == cli.PRESET_D_ONLY
