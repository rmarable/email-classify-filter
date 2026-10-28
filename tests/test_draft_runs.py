"""The draft eval's run handling (M8 plan §4 "Incomplete runs", "Run-handling invariants"; Step 5):
the 0600 record and journal, slots and attempts, the set's confirmation, the frozen checker,
restarts, expiry from the heartbeat, the read-then-lost rule (3a), reclaims, the shuffled order,
the Claude Code version and the determinism sample. Each invariant has a test here that fails
without it."""

from __future__ import annotations

import json
import os
import random
import stat
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from ecf.errors import ConflictError, InvalidInputError
from ecf.eval import drafts
from ecf.eval.drafts import DraftsFile
from ecf.eval.results import load_result
from ecf_server import claude_eval, draftcheck, draftruns, evalrun
from ecf_server.clock import FakeClock
from tests.test_claude_eval import (
    REQUEST,
    S1,
    S2,
    _inline,  # pyright: ignore[reportPrivateUsage]
    connector,
    nxt,
    settled,
    start,
)
from tests.test_draft_eval import _act, make_droot  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.usefixtures("conn")


@pytest.fixture
def droot(tmp_path: Path) -> Path:
    return make_droot(tmp_path)


def _reg(db_path: Path) -> draftruns.Registry:
    return draftruns.load(db_path.parent)


def _events(db_path: Path, run_id: str) -> list[dict[str, Any]]:
    return draftruns.read(draftruns.journal_path(db_path.parent, run_id))


def _run() -> claude_eval.Run:
    run = claude_eval.current()
    assert run is not None
    return run


def _case_of(item: dict[str, Any]) -> str:
    return next(w.case.id for w in _run().works if w.ref == item["id"])


def _answer(db_path: Path, clock: FakeClock, item: dict[str, Any], session: str = S1) -> None:
    ref, token, agent = item["id"], item["claim_token"], item["agent"]
    claude_eval.get_message(clock, session, ref, token, agent)
    if item["need"] == "classify":
        got = claude_eval.record_classification(clock, session, ref, token, REQUEST, agent)
    else:
        got = claude_eval.propose_action(clock, session, ref, token, _act(), agent)
    settled(db_path, clock, got)


def _drive(db_path: Path, clock: FakeClock, *, session: str = S1, until: int | None = None,
           limit: int = 10) -> None:  # fmt: skip
    """Answer every item until the run ends, or until `until` cases are scored."""
    for _ in range(40):
        run = claude_eval.current()
        if run is None or not run.open:
            return
        if until is not None and sum(w.result is not None for w in run.works) >= until:
            return
        q = nxt(db_path, clock, session, limit=limit)
        if q["done"]:
            return
        if not q["items"]:
            clock.advance(max(1, q.get("frees_in_s", 1)))
            continue
        for item in q["items"]:
            _answer(db_path, clock, item, session)
    raise AssertionError("the run didn't end")


def _restart(db_path: Path, clock: FakeClock) -> list[str]:
    """The service stops under the run and starts again."""
    claude_eval.reset()
    return claude_eval.recover(connector(db_path), clock, db_path.parent, spawn=_inline)


def _stop(db_path: Path, clock: FakeClock) -> None:
    claude_eval.stop(connector(db_path), clock, "a test stop")


# The record and the journal: 0600 files, a truncated last line ignored.


def test_the_record_and_the_journal_are_private_files(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    r = start(db_path, clock, droot)
    folder = db_path.parent / "evals"
    for f in (folder / draftruns.REGISTRY, folder / f"{r['run_id']}{draftruns.JOURNAL_SUFFIX}"):
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600, f
    rec = _reg(db_path).get(r["run_id"])
    assert rec is not None and rec.slot == "pilot-natural" and rec.attempt == 1 and rec.baseline


def test_the_journal_reader_drops_a_cut_last_line_only(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    draftruns.append(path, {"e": "a"})
    draftruns.append(path, {"e": "b"})
    with path.open("ab") as f:
        f.write(b'{"e": "c", "cut')
    assert [e["e"] for e in draftruns.read(path)] == ["a", "b"]
    path.write_bytes(b'{"e": "a"}\n{"e": "scored", "case": "x"}')  # whole, but no newline
    assert [e["e"] for e in draftruns.read(path)] == ["a"]  # as the next append would cut it
    path.write_bytes(b'{"e": "a"}\nnot json\n{"e": "b"}\n')
    with pytest.raises(ValueError, match="line 2"):
        draftruns.read(path)


# Slots and the set's confirmation (AM3, AN5, AO3): the pilot first, the baseline arm only, one of
# each kind across every version of the set; then run-1, run-2 and natural on the frozen digest.


def test_before_the_confirmation_only_the_baseline_pilots_run(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    with pytest.raises(InvalidInputError, match="only the pilot runs"):
        start(db_path, clock, droot, actor_model="claude-haiku-9-9")
    start(db_path, clock, droot, forced_draft=True)
    _drive(db_path, clock)
    with pytest.raises(InvalidInputError, match="every pilot slot for forced"):
        start(db_path, clock, droot, forced_draft=True)
    start(db_path, clock, droot)
    _drive(db_path, clock)
    with pytest.raises(InvalidInputError, match="every pilot slot for natural"):
        start(db_path, clock, droot)


def _pilots(db_path: Path, clock: FakeClock, droot: Path) -> None:
    for forced in (True, False):
        start(db_path, clock, droot, forced_draft=forced)
        _drive(db_path, clock)


def test_the_confirmation_opens_the_slots_in_order_on_its_digest(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    with pytest.raises(InvalidInputError, match="the pilot comes first"):
        claude_eval.confirm_set(clock, db_path.parent, droot, evalrun.set_version(droot))
    _pilots(db_path, clock, droot)
    with pytest.raises(InvalidInputError, match="name that digest"):
        claude_eval.confirm_set(clock, db_path.parent, droot, "0" * 64)
    got = claude_eval.confirm_set(clock, db_path.parent, droot, evalrun.set_version(droot))
    assert got["changed"] == []
    with pytest.raises(ConflictError, match="was confirmed"):
        claude_eval.confirm_set(clock, db_path.parent, droot, evalrun.set_version(droot))
    slots: list[str] = []
    for forced in (True, True, False):
        r = start(db_path, clock, droot, forced_draft=forced)
        slots.append(r["slot"])
        _drive(db_path, clock)
    assert slots == ["run-1", "run-2", "natural"]
    # another arm has its own slots on the same digest
    r = start(db_path, clock, droot, actor_model="claude-haiku-9-9")
    assert r["slot"] == "natural"


def test_a_card_changed_since_the_pilot_must_be_a_pilot_miss(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    _pilots(db_path, clock, droot)
    card = droot / "cases" / "d-benign.md"
    card.write_text(card.read_text().replace("We tried two", "We tried three"))
    from ecf.eval import labels  # noqa: PLC0415
    from ecf.eval.builder import build_all  # noqa: PLC0415

    assert not build_all(droot).findings
    labels.confirm(droot, "d-benign", clock.now().date())
    with pytest.raises(InvalidInputError, match="not a pilot classifier-stage miss: d-benign"):
        claude_eval.confirm_set(clock, db_path.parent, droot, evalrun.set_version(droot))
    assert _reg(db_path).confirmation is None


def test_the_checker_is_frozen_at_the_pilot(
    db_path: Path, clock: FakeClock, droot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start(db_path, clock, droot, forced_draft=True)
    assert _reg(db_path).frozen_checker == draftcheck.digest()
    _drive(db_path, clock)
    monkeypatch.setattr(draftcheck, "digest", lambda: "f" * 64)
    with pytest.raises(ConflictError, match="draft checker changed since the pilot"):
        start(db_path, clock, droot)


# Invariant 1: each case of a slot is scored once. A stopped attempt waits for the operator; the
# new attempt runs only the unscored cases, and the slot's result has every case once.


def test_a_new_attempt_runs_only_the_unscored_cases(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    first = start(db_path, clock, droot)
    for item in nxt(db_path, clock, S1)["items"][:1]:  # one case classified, then stopped
        _answer(db_path, clock, item)
    _stop(db_path, clock)
    rec = _reg(db_path).get(first["run_id"])
    assert rec is not None and rec.end == "stopped" and rec.stop_reason == "a test stop"
    with pytest.raises(InvalidInputError, match="--new-attempt REASON"):
        start(db_path, clock, droot)
    r = start(db_path, clock, droot, new_attempt="the session crashed")
    assert r["slot"] == "pilot-natural" and r["attempt"] == 2 and r["total"] == 3
    _drive(db_path, clock)
    result = load_result(db_path.parent / "evals" / f"{r['run_id']}.json")
    assert sorted(c.id for c in result.cases) == ["d-benign", "d-inject", "d-none"]
    assert result.summary is not None and result.summary["complete"] is True
    handling = cast(dict[str, Any], result.summary["draft_run"])
    assert handling["runs"] == [first["run_id"], r["run_id"]]


def test_a_scored_case_is_never_run_again_in_its_slot(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    start(db_path, clock, droot)
    _drive(db_path, clock, until=2, limit=1)
    assert _run().open
    left = {w.case.id for w in _run().works if w.result is None}
    assert len(left) == 1
    _stop(db_path, clock)
    r = start(db_path, clock, droot, new_attempt="finish it")
    assert {w.case.id for w in _run().works} == left and r["total"] == 1
    _drive(db_path, clock)
    record = DraftsFile.model_validate_json(
        (db_path.parent / "evals" / f"{r['run_id']}{drafts.DRAFTS_SUFFIX}").read_text()
    )
    assert sorted(c.id for c in record.cases) == ["d-benign", "d-inject", "d-none"]
    assert record.complete


def test_a_second_incomplete_attempt_ends_the_slot(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    start(db_path, clock, droot)
    _stop(db_path, clock)
    start(db_path, clock, droot, new_attempt="again")
    _stop(db_path, clock)
    with pytest.raises(InvalidInputError, match="every pilot slot for natural"):
        start(db_path, clock, droot, new_attempt="a third")


def test_new_attempt_is_only_for_a_slot_that_waits(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    with pytest.raises(InvalidInputError, match="has none"):
        start(db_path, clock, droot, new_attempt="why not")


def test_a_draft_run_covers_the_whole_set(db_path: Path, clock: FakeClock, droot: Path) -> None:
    with pytest.raises(InvalidInputError, match="covers the whole set"):
        start(db_path, clock, droot, cases=("d-benign",))


def test_a_draft_run_is_stopped_only_with_a_reason(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    start(db_path, clock, droot)
    with pytest.raises(InvalidInputError, match="with a reason"):
        claude_eval.stop(connector(db_path), clock)
    assert _run().open


# Invariant 2: each claim is journalled before it is handed out, and its first read when it
# happens, so a restart loses neither.


def test_claims_and_first_reads_are_journalled(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    r = start(db_path, clock, droot)
    q = nxt(db_path, clock, S1)
    claims = [e for e in _events(db_path, r["run_id"]) if e["e"] == "claim"]
    assert {e["case"] for e in claims} == {_case_of(i) for i in q["items"]}
    assert all(e["stage"] == "classify" and e["attempt"] == 1 for e in claims)
    item = q["items"][0]
    claude_eval.get_message(clock, S1, item["id"], item["claim_token"], item["agent"])
    reads = [e for e in _events(db_path, r["run_id"]) if e["e"] == "read"]
    assert [e["case"] for e in reads] == [_case_of(item)]


def _to_act(db_path: Path, clock: FakeClock) -> dict[str, Any]:
    """Classify every case, then claim the act stage: the first act item, read."""
    for _ in range(10):
        q = nxt(db_path, clock, S1, limit=10)
        acts = [i for i in q["items"] if i["need"] == "act"]
        for item in q["items"]:
            if item["need"] == "classify":
                _answer(db_path, clock, item)
        if acts:
            item = acts[0]
            claude_eval.get_message(clock, S1, item["id"], item["claim_token"], item["agent"])
            return item
        if not q["items"]:
            clock.advance(max(1, q.get("frees_in_s", 1)))
    raise AssertionError("no act claim")


def test_a_restart_after_a_read_scores_the_case_missing_in_the_resumed_run(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    first = start(db_path, clock, droot)
    item = _to_act(db_path, clock)
    case = _case_of(item)
    lines = _restart(db_path, clock)
    assert "lost, resumed as" in lines[0]
    old = _reg(db_path).get(first["run_id"])
    assert old is not None and old.end == "lost" and old.resumed_by == _run().run_id
    lost = db_path.parent / "evals" / f"{first['run_id']}{drafts.DRAFTS_SUFFIX}"
    assert lost.is_file()  # both runs are reported
    resumed = _run()
    w = next(w for w in resumed.works if w.case.id == case)
    assert w.result is not None and w.lost == "restart"  # scored missing, never handed out
    rec = _reg(db_path).get(resumed.run_id)
    assert rec is not None and rec.restarts == 1 and rec.attempt == 1
    assert rec.resumed_from == first["run_id"]
    _drive(db_path, clock)
    summary = resumed.summary or {}
    assert {"case": case, "cause": "restart", "detail": None} in summary["draft_run"][
        "missing_after_read"]  # fmt: skip
    record = DraftsFile.model_validate_json((db_path.parent / "evals" / (
        f"{resumed.run_id}{drafts.DRAFTS_SUFFIX}")).read_text())  # fmt: skip
    by = {c.id: c for c in record.cases}
    assert by[case].cause in ("missing_after_read", None) and by[case].lost == "restart"


def test_a_restart_with_no_claims_out_is_not_counted(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    start(db_path, clock, droot)
    _restart(db_path, clock)
    rec = _reg(db_path).get(_run().run_id)
    assert rec is not None and rec.restarts == 0
    _restart(db_path, clock)
    rec = _reg(db_path).get(_run().run_id)
    assert rec is not None and rec.restarts == 0 and _run().open


def test_the_second_restart_with_claims_out_ends_the_attempt(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    start(db_path, clock, droot)
    nxt(db_path, clock, S1)  # claims out
    _restart(db_path, clock)
    nxt(db_path, clock, S1)
    lines = _restart(db_path, clock)
    assert "second restart with claims out" in lines[0]
    assert claude_eval.current() is None or not _run().open
    with pytest.raises(InvalidInputError, match="--new-attempt"):
        start(db_path, clock, droot)


def test_an_attempt_ends_48_hours_after_it_began(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    start(db_path, clock, droot)
    for _ in range(2):  # restarts while it waits, each heartbeat fresh
        clock.advance(timedelta(hours=20).total_seconds())
        claude_eval.tick(connector(db_path), clock)
        _restart(db_path, clock)
    assert _run().open
    clock.advance(timedelta(hours=10).total_seconds())  # down from hour 40 to hour 50
    lines = _restart(db_path, clock)
    assert "passed 48 hours" in lines[0]


# Invariant 3: `reclaimed` as AR3 defines it; 3a: an act claim that read the message is never
# handed out again, the case scored missing, whatever ended the claim.


def test_a_session_closed_after_a_read_scores_the_case_missing(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    start(db_path, clock, droot)
    item = _to_act(db_path, clock)
    case = _case_of(item)
    claude_eval.release_session(S1, "closed, exit 130")
    w = next(w for w in _run().works if w.case.id == case)
    assert w.result is not None and w.lost == "session_ended"
    assert w.lost_detail == "closed, exit 130"
    q = nxt(db_path, clock, S2)
    assert case not in {_case_of(i) for i in q["items"]}


def test_an_expired_act_claim_after_a_read_is_never_handed_out_again(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    start(db_path, clock, droot)
    item = _to_act(db_path, clock)
    case = _case_of(item)
    clock.advance(claude_eval.claude_review.CLAIM_TTL.total_seconds() + 1)
    nxt(db_path, clock, S1)
    w = next(w for w in _run().works if w.case.id == case)
    assert w.lost is None  # a draft run's act claim lasts longer (3b)
    clock.advance((claude_eval.DRAFT_ACT_TTL - claude_eval.claude_review.CLAIM_TTL).total_seconds())
    nxt(db_path, clock, S1)
    assert w.lost == "claim_expired" and w.result is not None


def test_a_stop_scores_a_read_act_claim_missing(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    start(db_path, clock, droot)
    item = _to_act(db_path, clock)
    case = _case_of(item)
    _stop(db_path, clock)
    w = next(w for w in _run().works if w.case.id == case)
    assert w.lost == "run_ended" and w.result is not None
    start(db_path, clock, droot, new_attempt="after the stop")
    assert case not in {w.case.id for w in _run().works}  # a stop can't re-roll it


def test_an_unread_act_claim_and_a_classify_claim_run_again_counted_as_reclaims(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    start(db_path, clock, droot)
    q = nxt(db_path, clock, S1)  # classify claims, never read
    claude_eval.release_session(S1)
    _drive(db_path, clock, session=S2)
    summary = _run().summary or {}
    assert summary["draft_run"]["reclaims"] == {"session_ended": len(q["items"])}
    assert summary["draft_run"]["missing_after_read"] == []


def test_an_invalid_ending_is_not_a_reclaim(db_path: Path, clock: FakeClock, droot: Path) -> None:
    start(db_path, clock, droot)
    q = nxt(db_path, clock, S1, limit=1)
    item = q["items"][0]
    for _ in range(3):
        claude_eval.record_classification(clock, S1, item["id"], item["claim_token"],
                                          {"bad": 1}, item["agent"])  # fmt: skip
    _drive(db_path, clock)
    assert (_run().summary or {})["draft_run"]["reclaims"] == {}


# Invariant 4: the order is shuffled per slot, seeded by the set digest, the arm and the slot, so
# every attempt in a slot uses it.


def test_the_cases_run_in_the_slots_seeded_order(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    r = start(db_path, clock, droot)
    rec = _reg(db_path).get(r["run_id"])
    assert rec is not None
    ids = [c.id for c in evalrun.load(droot, fraud_only=False)[0]]
    random.Random(draftruns.shuffle_seed(rec.set_digest, rec.arm, rec.slot)).shuffle(ids)  # noqa: S311
    assert [w.case.id for w in _run().works] == ids


# Invariant 5: each attempt records the Claude Code version it ran under.


def test_the_claude_code_version_is_recorded(db_path: Path, clock: FakeClock, droot: Path) -> None:
    r = start(db_path, clock, droot)
    claude_eval.eval_next(connector(db_path), clock, S1, claude_version="2.1.312")
    rec = _reg(db_path).get(r["run_id"])
    assert rec is not None and rec.claude_versions == ["2.1.312"]
    sessions = [e for e in _events(db_path, r["run_id"]) if e["e"] == "session"]
    assert sessions[0]["claude_version"] == "2.1.312"


# Invariant 6: expiry from the saved heartbeat (AQ6): the minute timer ends a live run at its
# expiry; at a start, a run whose heartbeat reached its expiry is expired, else lost.


def test_the_timer_ends_a_run_at_its_expiry_across_a_sleep(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    r = start(db_path, clock, droot)
    clock.advance(timedelta(hours=25).total_seconds())  # asleep: no tick for a day
    claude_eval.tick(connector(db_path), clock)
    rec = _reg(db_path).get(r["run_id"])
    assert rec is not None and rec.end == "expired"
    assert (db_path.parent / "evals" / f"{r['run_id']}{drafts.DRAFTS_SUFFIX}").is_file()


def test_a_start_reads_expiry_from_the_heartbeat(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    r = start(db_path, clock, droot)
    clock.advance(timedelta(hours=24).total_seconds() - 60)
    claude_eval.tick(connector(db_path), clock)  # a heartbeat a minute before expiry
    lines = _restart(db_path, clock)
    rec = _reg(db_path).get(r["run_id"])
    assert rec is not None and rec.end == "expired" and "expired" in lines[0]
    assert claude_eval.current() is None


def test_a_run_the_service_was_down_for_is_lost_not_expired(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    r = start(db_path, clock, droot)
    clock.advance(timedelta(hours=10).total_seconds())  # down for 10 hours, heartbeat old
    _restart(db_path, clock)
    rec = _reg(db_path).get(r["run_id"])
    assert rec is not None and rec.end == "lost"
    new = _reg(db_path).get(_run().run_id)  # its own 24 hours (AQ6)
    assert new is not None and new.expires_at == clock.now() + claude_eval.EXPIRY
    clock.advance(timedelta(hours=30).total_seconds())  # down again, past its own expiry
    _restart(db_path, clock)
    newer = _reg(db_path).get(_run().run_id)  # never past 48 hours from the first registration
    assert newer is not None and newer.expires_at == rec.registered_at + draftruns.ATTEMPT_LIMIT


def test_a_restart_during_the_repeats_leaves_a_complete_slot(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    r = start(db_path, clock, droot)
    for _ in range(20):
        if _run().repeats_started:
            break
        q = nxt(db_path, clock, S1)
        if not q["items"]:
            clock.advance(max(1, q.get("frees_in_s", 1)))
        for item in q["items"]:
            _answer(db_path, clock, item)
    assert _run().repeats_started and _run().open
    lines = _restart(db_path, clock)
    assert lines[0].endswith("lost, complete")
    rec = _reg(db_path).get(r["run_id"])
    assert rec is not None and rec.complete is True
    assert draftruns.slot_state([rec])[0] == "filled"
    assert _restart(db_path, clock) == []  # a complete lost run is never resumed (delta 1)
    assert len(_reg(db_path).runs) == 1


# Invariant 7: no run is dropped unsaved: registering over an open run past its expiry ends and
# saves it first (AM3).


def test_register_saves_an_expired_run_first(db_path: Path, clock: FakeClock, droot: Path) -> None:
    old = start(db_path, clock, droot)
    clock.advance(timedelta(hours=25).total_seconds())  # no tick, no poll
    with pytest.raises(InvalidInputError, match="--new-attempt"):
        start(db_path, clock, droot)  # the expired attempt is incomplete: the operator decides
    rec = _reg(db_path).get(old["run_id"])
    assert rec is not None and rec.end == "expired"
    assert (db_path.parent / "evals" / f"{old['run_id']}{drafts.DRAFTS_SUFFIX}").is_file()
    other = start(db_path, clock, droot, forced_draft=True)
    assert _run().run_id == other["run_id"]


def test_the_registration_marks_the_expired_run_it_superseded(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    old = start(db_path, clock, droot)
    clock.advance(timedelta(hours=25).total_seconds())
    new = start(db_path, clock, droot, forced_draft=True)  # another slot: it registers
    rec = _reg(db_path).get(new["run_id"])
    assert rec is not None and rec.supersedes == old["run_id"]


# Determinism repeats (AR8): `evalrun.determinism_sample`'s cases this run classified.


def test_the_repeats_are_the_determinism_sample(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    start(db_path, clock, droot)
    _drive(db_path, clock)
    sample = set(evalrun.determinism_sample(evalrun.load(droot, fraud_only=False)[0]))
    repeated = {w.case.id for w in _run().works if any(c.stage == "repeat" for c in w.claims)}
    assert repeated == sample
    assert (_run().summary or {})["draft_run"]["repeats"] == len(sample)


# Outcomes stay hidden while the run goes (AK3): the status shows counts only.


def test_the_status_shows_no_draft_outcome_while_the_run_goes(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    start(db_path, clock, droot)
    item = nxt(db_path, clock, S1)["items"][0]
    _answer(db_path, clock, item)
    shown = json.dumps(claude_eval.status(connector(db_path), clock))
    shown += json.dumps(claude_eval.draft_status(db_path.parent))
    assert "drafted" not in shown and "cause" not in shown and "Your Name" not in shown


# The routes: the session's Claude Code version, a closed session's exit, a stop's reason, the
# record in `ecf eval status`, and the confirmation.


def test_the_routes_carry_the_version_the_exit_and_the_reason(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    from ecf.claude_wrapper import client_doc  # noqa: PLC0415
    from ecf_server.api import ServiceState  # noqa: PLC0415
    from tests.test_telemetry import api  # noqa: PLC0415

    state = ServiceState(install="t", token="cli-token", started_at="2026-10-02T09:00:00Z",
                         clock=clock, db_path=db_path)  # fmt: skip
    made = api(state, "POST", "/v1/eval/claude", {"root": str(droot), "preset": "C",
                                                  "sensitivity": "standard"}).json()  # fmt: skip
    assert made["slot"] == "pilot-natural"
    for _ in range(200):  # prepared in a service thread
        if claude_eval.current() is not None and _run().state != "preparing":
            break
        time.sleep(0.02)
    sess = api(state, "POST", "/v1/sessions", client_doc((2, 1, 312))).json()
    assert state.sessions[sess["session_id"]].claude_version == "2.1.312"
    q = api(state, "POST", "/v1/eval/next", {"limit": 1}, sess["profile_token"]).json()
    assert q["items"]
    rec = _reg(db_path).get(made["run_id"])
    assert rec is not None and rec.claude_versions == ["2.1.312"]
    assert api(state, "DELETE", f"/v1/sessions/{sess['session_id']}",
               {"exit_code": 130}).status_code == 200  # fmt: skip
    assert _run().session_ends[sess["session_id"]] == "closed, exit 130"
    status = api(state, "GET", "/v1/eval/runs").json()
    assert status["draft_runs"]["slots"][0]["state"] == "open"
    assert api(state, "POST", "/v1/eval/runs/stop", {}).status_code == 400
    stopped = api(state, "POST", "/v1/eval/runs/stop", {"reason": "pilot paused"})
    assert stopped.status_code == 200
    rec = _reg(db_path).get(made["run_id"])
    assert rec is not None and rec.stop_reason == "pilot paused"
    refused = api(state, "POST", "/v1/eval/drafts/confirm",
                  {"root": str(droot), "digest": evalrun.set_version(droot)})  # fmt: skip
    assert refused.status_code == 400 and "the pilot comes first" in refused.text


@pytest.mark.parametrize(
    ("sent", "kept"),
    [("2.1.312", "2.1.312"), ("2.1", None), ("2.1.312; rm", None), (2, None), (None, None)],
)
def test_only_a_plain_claude_code_version_is_kept(sent: object, kept: str | None) -> None:
    from ecf_server.api import _claude_version  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]

    assert _claude_version({"claude_version": sent}) == kept


def test_the_status_lists_the_slots_without_outcomes() -> None:
    from ecf.cli import draft_lines  # noqa: PLC0415

    lines = draft_lines({"frozen_checker": "c" * 64, "confirmation": None, "slots": [
        {"arm": "a" * 16, "slot": "pilot-forced", "set_digest": None, "state": "waiting",
         "runs": [{"run_id": "r" * 32, "attempt": 1, "end": "stopped", "complete": False,
                   "detail": "stopped after 1 of 3 cases"}]}]})  # fmt: skip
    assert lines[1] == "Draft eval slots (set not yet confirmed: pilot only):"
    assert lines[2] == "  arm aaaaaaaa pilot-forced: waiting (rrrrrrrr attempt 1 stopped)"
    assert draft_lines(None) == []


def test_before_the_confirmation_the_status_names_the_digest_to_confirm(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    """`ecf eval confirm-set --help` sends the operator to `ecf eval status` for the digest."""
    from ecf.cli import draft_lines  # noqa: PLC0415

    _pilots(db_path, clock, droot)
    st = claude_eval.draft_status(db_path.parent)
    assert st is not None
    digest = evalrun.set_version(droot)
    assert st["pilot_set"] == {"digest": digest, "root": str(droot.resolve())}
    assert draft_lines(st)[1] == (f"Draft eval slots (set not yet confirmed: the pilots ran on"
                                  f" {digest}; confirm it with `ecf eval confirm-set --root"
                                  f" {droot.resolve()} --digest {digest}`):")  # fmt: skip
    claude_eval.confirm_set(clock, db_path.parent, droot, digest)
    after = claude_eval.draft_status(db_path.parent)
    assert after is not None and after["pilot_set"] is None


def test_a_draft_run_is_named_as_one_not_as_a_comparison() -> None:
    from ecf import cli  # noqa: PLC0415

    caveat = cli._run_caveat  # pyright: ignore[reportPrivateUsage]
    pin_kind = cli._pin_kind  # pyright: ignore[reportPrivateUsage]
    cl = {"preset": "C", "pinned": False, "slot": "run-1"}
    m = {"complete": True, "gate_passed": False, "claude": cl,
         "draft_run": {"slot": "run-1", "attempt": 1}}  # fmt: skip
    assert caveat(m) == ", draft run in slot run-1 (never for the gate)"
    assert pin_kind(cl) == "draft run in slot run-1"


def test_the_repeats_are_the_sample_not_the_first_ten(
    db_path: Path, clock: FakeClock, tmp_path: Path
) -> None:
    from ecf.eval import labels  # noqa: PLC0415
    from ecf.eval.builder import build_all  # noqa: PLC0415
    from tests.test_draft_eval import BENIGN_DRAFT, _card  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]

    big = tmp_path / "big"
    (big / "cases").mkdir(parents=True)
    ids = [f"d-{n:02d}" for n in range(14)]
    for cid in ids:
        (big / "cases" / f"{cid}.md").write_text(_card(cid, BENIGN_DRAFT))
    assert not build_all(big).findings
    for cid in ids:
        labels.confirm(big, cid, clock.now().date())
    start(db_path, clock, big)
    first_ten = {w.case.id for w in _run().works[: evalrun.DETERMINISM_CASES]}
    _drive(db_path, clock)
    sample = set(evalrun.determinism_sample(evalrun.load(big, fraud_only=False)[0]))
    repeated = {w.case.id for w in _run().works if any(c.stage == "repeat" for c in w.claims)}
    assert repeated == sample and sample != first_ten


def test_a_resumed_run_repeats_only_the_cases_it_classified(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    start(db_path, clock, droot)
    for _ in range(10):  # every case classified, no act claim read
        q = nxt(db_path, clock, S1)
        if all(w.stage != "classify" for w in _run().works):
            break
        for item in q["items"]:
            if item["need"] == "classify":
                _answer(db_path, clock, item)
        if not q["items"]:
            clock.advance(max(1, q.get("frees_in_s", 1)))
    _restart(db_path, clock)
    assert all(w.restored is not None for w in _run().works)
    _drive(db_path, clock)
    assert not any(c.stage == "repeat" for w in _run().works for c in w.claims)
    assert (_run().summary or {})["draft_run"]["repeats"] == 0


def test_a_draft_runs_act_rounds_are_smaller(
    db_path: Path, clock: FakeClock, tmp_path: Path
) -> None:
    from ecf.eval import labels  # noqa: PLC0415
    from ecf.eval.builder import build_all  # noqa: PLC0415
    from tests.test_draft_eval import BENIGN_DRAFT, _card  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]

    big = tmp_path / "big"
    (big / "cases").mkdir(parents=True)
    for n in range(8):
        (big / "cases" / f"d-{n}.md").write_text(_card(f"d-{n}", BENIGN_DRAFT))
    assert not build_all(big).findings
    for n in range(8):
        labels.confirm(big, f"d-{n}", clock.now().date())
    start(db_path, clock, big)
    sizes: dict[str, list[int]] = {"classify": [], "act": []}
    for _ in range(10):
        q = nxt(db_path, clock, S1, limit=10)
        if q["items"]:
            sizes[q["items"][0]["need"]].append(len(q["items"]))
        for item in q["items"]:
            _answer(db_path, clock, item)
        if not q["items"]:
            clock.advance(max(1, q.get("frees_in_s", 1)))
        if all(w.stage not in ("classify", "act") for w in _run().works):
            break
    assert sizes["classify"][0] == 8 and max(sizes["act"]) == claude_eval.DRAFT_ACT_ROUND


def test_the_act_round_is_a_setting_of_the_run(
    db_path: Path, clock: FakeClock, tmp_path: Path, droot: Path
) -> None:
    from ecf.eval import labels  # noqa: PLC0415
    from ecf.eval.builder import build_all  # noqa: PLC0415
    from ecf_server.api import _window_tuning  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]
    from tests.test_draft_eval import BENIGN_DRAFT, _card  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(InvalidInputError, match="--act-round is for the draft set, from 1"):
        start(db_path, clock, droot, act_round=0)
    assert _window_tuning({"act_round": 3}) == {"act_round": 3}
    with pytest.raises(InvalidInputError, match="whole number"):
        _window_tuning({"act_round": True})
    big = tmp_path / "big"
    (big / "cases").mkdir(parents=True)
    for n in range(8):
        (big / "cases" / f"d-{n}.md").write_text(_card(f"d-{n}", BENIGN_DRAFT))
    assert not build_all(big).findings
    for n in range(8):
        labels.confirm(big, f"d-{n}", clock.now().date())
    r = start(db_path, clock, big, act_round=3)
    assert r["act_round"] == 3
    sizes: list[int] = []
    for _ in range(12):
        q = nxt(db_path, clock, S1, limit=10)
        if q["items"] and q["items"][0]["need"] == "act":
            sizes.append(len(q["items"]))
        for item in q["items"]:
            _answer(db_path, clock, item)
        if not q["items"]:
            clock.advance(max(1, q.get("frees_in_s", 1)))
        if all(w.stage not in ("classify", "act") for w in _run().works):
            break
    assert max(sizes) == 3
    rec = _reg(db_path).get(r["run_id"])
    assert rec is not None and rec.options["act_round"] == 3  # resumed with it after a restart
    _drive(db_path, clock)
    assert (_run().summary or {})["options"]["act_round"] == 3


# Diff review fixes (5B-1 to 5B-15, 2026-10-28): a cut journal line, the end/scored order, one
# pilot per kind, pilots across set versions, an expired run's read claims, source runs, a corrupt
# record, a stop's reason, the heartbeat's pace, one write per round, and disk errors.


def test_a_cut_journal_line_is_repaired_and_the_run_resumes(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    """5B-1: a crash mid-line leaves a fragment; the next append starts on a fresh line, so the
    restart reads the journal, resumes the run, and the slot stays usable."""
    first = start(db_path, clock, droot)
    _to_act(db_path, clock)
    j = draftruns.journal_path(db_path.parent, first["run_id"])
    with j.open("ab") as f:
        f.write(b'{"e": "claim", "cut')
    lines = _restart(db_path, clock)
    assert "lost, resumed as" in lines[0], lines
    assert draftruns.read(j)  # still readable after the lost run's `finished` was appended
    assert _run().open


def test_append_writes_the_whole_line_on_a_short_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = os.write

    def short(fd: int, data: bytes | memoryview) -> int:
        return real(fd, data[:3])

    monkeypatch.setattr(os, "write", short)
    path = tmp_path / "j.jsonl"
    draftruns.append(path, {"e": "x", "n": 1})
    draftruns.append(path, {"e": "y", "n": 2})
    assert [e["e"] for e in draftruns.read(path)] == ["x", "y"]


def test_one_run_failing_to_recover_doesnt_stop_the_others_or_jam_its_slot(
    db_path: Path, clock: FakeClock, droot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5B-1: a run whose recovery raises is marked so its slot can take a new attempt."""
    first = start(db_path, clock, droot)
    nxt(db_path, clock, S1)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(claude_eval, "_restored", boom)
    claude_eval.reset()
    lines = claude_eval.recover(connector(db_path), clock, db_path.parent, spawn=_inline)
    assert len(lines) == 1 and "RuntimeError" in lines[0]
    rec = _reg(db_path).get(first["run_id"])
    assert rec is not None and not rec.open and rec.attempt_over
    monkeypatch.undo()
    r = start(db_path, clock, droot, new_attempt="after a failed recovery")
    assert r["slot"] == "pilot-natural"


def test_a_lost_run_left_unresumed_is_resumed_at_the_next_start(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    """5B-1: a crash after a lost run was finished, before it was resumed, doesn't jam the slot."""
    first = start(db_path, clock, droot)
    nxt(db_path, clock, S1)
    claude_eval._update_record(db_path.parent, first["run_id"], end="lost",  # pyright: ignore[reportPrivateUsage]
                               ended_at=clock.now(), complete=False)  # fmt: skip
    lines = _restart(db_path, clock)
    assert any("resumed as" in x for x in lines), lines
    assert _run().open and _run().run_id != first["run_id"]


def test_a_crash_between_the_end_and_the_score_never_re_rolls_the_case(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    """5B-2: the score is journalled before the claim's end; a journal with the end but not the
    score (an older order, or a cut) scores the read act case missing, never hands it out."""
    first = start(db_path, clock, droot)
    item = _to_act(db_path, clock)
    case = _case_of(item)
    got = claude_eval.propose_action(
        clock, S1, item["id"], item["claim_token"], _act(), item["agent"]
    )
    settled(db_path, clock, got)
    events = _events(db_path, first["run_id"])
    kinds = [e["e"] for e in events if e.get("case") == case and e["e"] in ("end", "scored")]
    assert kinds[-2:] == ["scored", "end"]  # the score first
    j = draftruns.journal_path(db_path.parent, first["run_id"])
    keep = [ln for ln in j.read_bytes().split(b"\n")
            if not (b'"e": "scored"' in ln and case.encode() in ln)]  # fmt: skip
    j.write_bytes(b"\n".join(keep))
    _restart(db_path, clock)
    w = next(w for w in _run().works if w.case.id == case)
    assert w.result is not None and w.lost == "restart"


def test_only_one_pilot_of_each_kind_whatever_the_options(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    """5B-3: high sensitivity isn't the baseline, and a pilot slot counts across arms."""
    start(db_path, clock, droot, forced_draft=True)
    _drive(db_path, clock)
    with pytest.raises(InvalidInputError, match="only the pilot runs"):
        start(db_path, clock, droot, forced_draft=True, sensitivity="high")


def test_the_pilots_run_on_one_arm(
    db_path: Path, clock: FakeClock, droot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5B-3: the actor's instructions changed after the first pilot: the next pilot is refused,
    naming the way back (delta 4)."""
    start(db_path, clock, droot, forced_draft=True)
    _drive(db_path, clock)
    monkeypatch.setattr(claude_eval, "actor_prompt_digest", lambda: "f" * 64)
    with pytest.raises(InvalidInputError, match=r"pilots run on one arm.*Restore"):
        start(db_path, clock, droot)


def test_a_pilot_score_carries_over_only_for_an_unchanged_card(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    """5B-4: a card rewritten between pilot attempts runs again; its old score isn't kept."""
    from ecf.eval import labels  # noqa: PLC0415
    from ecf.eval.builder import build_all  # noqa: PLC0415

    start(db_path, clock, droot)
    _drive(db_path, clock, until=1, limit=1)
    done = [w.case.id for w in _run().works if w.result is not None]
    _stop(db_path, clock)
    card = droot / "cases" / f"{done[0]}.md"
    card.write_text(
        card.read_text().replace("\n---\n", "\n---\nP.S. One more line.\n", 1)
        if "\n---\n" in card.read_text()
        else card.read_text() + "\nOne more line.\n"
    )
    assert not build_all(droot).findings
    labels.confirm(droot, done[0], __import__("datetime").date(2026, 10, 2))
    start(db_path, clock, droot, new_attempt="card rewritten")
    assert done[0] in {w.case.id for w in _run().works}  # it runs again
    assert done[0] not in _run().prior_scored


def test_confirm_set_checks_cards_against_every_pilot(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    """5B-4: a card changed between the two pilots is checked against the miss rule too."""
    from ecf.eval import labels  # noqa: PLC0415
    from ecf.eval.builder import build_all  # noqa: PLC0415

    start(db_path, clock, droot, forced_draft=True)
    _drive(db_path, clock)
    card = droot / "cases" / "d-benign.md"
    card.write_text(card.read_text() + "\nOne more line.\n")
    assert not build_all(droot).findings
    labels.confirm(droot, "d-benign", __import__("datetime").date(2026, 10, 2))
    start(db_path, clock, droot)
    _drive(db_path, clock)
    with pytest.raises(InvalidInputError, match="not a pilot classifier-stage miss"):
        claude_eval.confirm_set(clock, db_path.parent, droot, evalrun.set_version(droot))


def test_an_expired_runs_read_case_is_scored_missing_as_run_ended(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    """5B-5: found expired at a restart, its read act case is scored missing with the run's real
    end in the next attempt, and its unscored cases show their stage."""
    first = start(db_path, clock, droot)
    item = _to_act(db_path, clock)
    case = _case_of(item)
    rec = _reg(db_path).get(first["run_id"])
    assert rec is not None
    claude_eval._update_record(db_path.parent, first["run_id"], heartbeat_at=rec.expires_at)  # pyright: ignore[reportPrivateUsage]
    clock.advance((rec.expires_at - clock.now()).total_seconds() + 60)
    _restart(db_path, clock)
    record = DraftsFile.model_validate_json((db_path.parent / "evals" / (
        f"{first['run_id']}{drafts.DRAFTS_SUFFIX}")).read_text())  # fmt: skip
    assert all(c.stage != "preparing" for c in record.cases if c.id == case)
    start(db_path, clock, droot, new_attempt="after expiry")
    w = next(w for w in _run().works if w.case.id == case)
    assert w.result is not None and w.lost == "run_ended"


def test_a_draft_run_takes_no_source_run(db_path: Path, clock: FakeClock, droot: Path) -> None:
    with pytest.raises(InvalidInputError, match="no source run"):
        start(db_path, clock, droot, source_run="0a48213c")


def test_a_corrupt_record_doesnt_break_status(db_path: Path) -> None:
    """5B-8: status for every eval still answers; the draft record shows as unreadable."""
    from ecf.cli import draft_lines  # noqa: PLC0415

    d = draftruns.folder(db_path.parent)
    d.mkdir(parents=True, exist_ok=True)
    (d / draftruns.REGISTRY).write_text("{not json")
    st = claude_eval.draft_status(db_path.parent)
    assert st is not None and "unreadable" in st["error"]
    assert "unreadable" in "\n".join(draft_lines(st))


def test_a_stop_without_a_reason_stops_nothing(
    db_path: Path, clock: FakeClock, droot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5B-9: the route checks a draft run's reason before stopping anything."""
    from ecf_server.api import ServiceState  # noqa: PLC0415
    from tests.test_telemetry import api  # noqa: PLC0415

    state = ServiceState(install="t", token="cli-token", started_at="2026-10-02T09:00:00Z",
                         clock=clock, db_path=db_path)  # fmt: skip
    start(db_path, clock, droot)
    stopped: list[int] = []
    monkeypatch.setattr(evalrun.RUN, "snapshot", lambda: {"state": "running"})
    monkeypatch.setattr(evalrun, "stop", lambda: stopped.append(1))
    assert api(state, "POST", "/v1/eval/runs/stop", {}).status_code == 400
    assert stopped == [] and _run().open  # the local eval wasn't stopped first


def test_a_baseline_draft_run_is_named_as_a_draft_run_and_the_root_is_quoted() -> None:
    from ecf import cli  # noqa: PLC0415

    pin_kind = cli._pin_kind  # pyright: ignore[reportPrivateUsage]
    assert pin_kind({"preset": "C", "pinned": True, "slot": "run-1"}) == "draft run in slot run-1"
    lines = cli.draft_lines({"frozen_checker": None, "confirmation": None, "slots": [],
                             "pilot_set": {"digest": "d" * 16, "root": "/a b/set"}})  # fmt: skip
    assert "--root '/a b/set'" in lines[1]


def test_the_heartbeat_is_saved_at_most_every_15_seconds(
    db_path: Path, clock: FakeClock, droot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5B-11: claims and submissions within 15 s of the last saved heartbeat don't rewrite it."""
    start(db_path, clock, droot)
    saves: list[int] = []
    real = draftruns.save

    def counted(d: Path, r: draftruns.Registry) -> None:
        saves.append(1)
        real(d, r)

    monkeypatch.setattr(draftruns, "save", counted)
    q = nxt(db_path, clock, S1, limit=10)
    for item in q["items"]:
        _answer(db_path, clock, item)
    assert len(saves) == 0  # within 15 s of the registration's heartbeat
    before = _reg(db_path).get(_run().run_id)
    clock.advance(16)
    nxt(db_path, clock, S1, limit=10)
    after = _reg(db_path).get(_run().run_id)
    assert len(saves) == 1 and before is not None and after is not None
    assert after.heartbeat_at > before.heartbeat_at


def test_a_claim_round_is_one_journal_write(
    db_path: Path, clock: FakeClock, droot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5B-12: a round of claims is appended in one write and one fsync."""
    start(db_path, clock, droot)
    calls: list[int] = []
    real = draftruns.append_many

    def counted(p: Path, es: list[dict[str, Any]]) -> None:
        calls.append(sum(e.get("e") == "claim" for e in es))
        real(p, es)

    monkeypatch.setattr(draftruns, "append_many", counted)
    q = nxt(db_path, clock, S1, limit=10)
    assert len(q["items"]) > 1 and [n for n in calls if n] == [len(q["items"])]


def test_the_eval_timer_runs_before_the_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    """5B-13: a slow schedule step can't delay a draft run's heartbeat."""
    import inspect  # noqa: PLC0415

    from ecf_server import service  # noqa: PLC0415

    src = inspect.getsource(service.Service.tick)
    assert src.index("claude_eval.tick") < src.index("self.scheduler.tick")


def test_a_journal_write_failure_ends_the_run_closed(
    db_path: Path, clock: FakeClock, droot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5B-14: a disk error on the journal fails the request and stops the run, nothing handed out;
    the record stays open, so a restart resumes it from what was written."""
    from ecf.errors import ServiceUnavailableError  # noqa: PLC0415

    first = start(db_path, clock, droot)

    def full(*_a: Any, **_k: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(draftruns, "append_many", full)
    monkeypatch.setattr(draftruns, "append", full)
    with pytest.raises(ServiceUnavailableError, match="record"):
        nxt(db_path, clock, S1, limit=10)
    assert not _run().open
    rec = _reg(db_path).get(first["run_id"])
    assert rec is not None and rec.open
    monkeypatch.undo()  # a new registration waits for the restart that resumes it (delta 2)
    with pytest.raises(ConflictError, match="restart the service"):
        start(db_path, clock, droot, forced_draft=True)


def test_the_record_stays_small(tmp_path: Path) -> None:
    """5B-15: only pilot registrations carry card digests; 50 runs and 2 pilots stay small."""
    now = __import__("datetime").datetime(2026, 10, 28, tzinfo=__import__("datetime").UTC)
    base: dict[str, Any] = {"kind": "forced", "attempt": 1, "arm": "a" * 16, "baseline": True,
                            "models": {"actor": "m"}, "actor_prompt_digest": "p" * 64,
                            "set_digest": "s" * 16, "root": "/r", "options": {}, "pinned": True,
                            "key": "k", "schema_digest": "c" * 64, "checker_digest": "h" * 64,
                            "registered_at": now, "expires_at": now, "attempt_started_at": now,
                            "heartbeat_at": now}  # fmt: skip
    digests = {f"card-{i:03d}": "d" * 64 for i in range(134)}
    reg = draftruns.Registry(runs=[
        draftruns.Registration(run_id=f"{i:032x}", slot="pilot-forced" if i < 2 else "run-1",
                               card_digests=digests if i < 2 else {}, **base)
        for i in range(52)])  # fmt: skip
    draftruns.save(tmp_path, reg)
    assert (draftruns.folder(tmp_path) / draftruns.REGISTRY).stat().st_size < 150_000


def test_a_run_after_the_pilot_carries_no_card_digests(
    db_path: Path, clock: FakeClock, droot: Path
) -> None:
    _pilots(db_path, clock, droot)
    claude_eval.confirm_set(clock, db_path.parent, droot, evalrun.set_version(droot))
    r = start(db_path, clock, droot, forced_draft=True)
    rec = _reg(db_path).get(r["run_id"])
    assert rec is not None and rec.slot == "run-1" and rec.card_digests == {}
