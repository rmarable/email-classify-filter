"""A labelled real-mail corpus in the eval (SPEC §16.7; R2, R53, R113, R114, R117, R120, R157,
R178, R181): its cases (`corpus_run`, which `ecf eval routes --corpus` and `ecf eval rescore
--corpus` read), the rescore and the deterministic noise. The model-driven `ecf eval run
--corpus` went in v3.0.0 (ADR 0031, R-D5, P14); its route refuses a corpus."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import corpus_labels as cl
from ecf.eval import results
from ecf.schema import extend_schema, from_v2, load_schema
from ecf_server import config, evalrun
from ecf_server import corpus_session as cs
from ecf_server.clock import FakeClock
from tests.helpers import BEC
from tests.test_addresses import TOKEN, call, make_state
from tests.test_claude_review import EXT, extend
from tests.test_corpus import SECRET, fetch, req, server_with

# a corpus label: BEC's answer is v2's, and a new label can't use a value schema v3 removed (S3b)
LABEL = BEC | {"category": "invoice"}


@pytest.fixture
def session(conn: sqlite3.Connection, clock: FakeClock, db_path: Path,
            tmp_path: Path) -> Iterator[cs.Session]:  # fmt: skip
    evalrun.RUN.set(state="idle", run_id="", done=0, total=0, result=None, detail="")
    out = tmp_path / "out"
    out.mkdir()
    path, *_ = fetch(conn, clock, db_path.parent, req(out / "c.ecfcorpus", total=3), server_with(3))
    assert path is not None
    opened = cs.open_session(str(path), SECRET, lambda: 0.0)
    s = cs.get(opened["session_id"], lambda: 0.0)
    labels: dict[cl.Key, cl.Label] = {}
    keys = [(str(r["key"]["content_hash"]), str(r["key"]["identity_digest"])) for r in s.rows]
    cl.put(labels, cl.make(keys[0], opened["corpus_id"], LABEL, None, date(2026, 10, 7)))
    cl.put(labels, cl.make(keys[1], opened["corpus_id"], None, "u", date(2026, 10, 7)))
    cl.save(cl.path_for(path), labels)  # the third message stays unlabelled
    yield s
    st = cs.status(lambda: 0.0)
    if st is not None:
        cs.release(str(st["session_id"]))


def test_eval_run_refuses_a_corpus(
    conn: sqlite3.Connection, db_path: Path, session: cs.Session
) -> None:
    """R-D5 (Jev step 2 plan P5): `POST /v1/eval/runs` with a corpus session is refused, the
    session left as it was; no run starts."""
    del conn  # migrated
    r = call(make_state(db_path, None), "POST", "/v1/eval/runs",
             {"corpus_session": session.id}, TOKEN)  # fmt: skip
    assert r.status_code == 400 and "corpus runs aren't available" in r.text
    assert not session.busy and evalrun.RUN.snapshot()["state"] == "idle"


def _corpus_result(run: evalrun.CorpusRun, folder: Path) -> Path:
    """A corpus result as the removed `eval run --corpus` saved one: the same answer for every
    message, its rule and actions recorded (R53)."""
    answer = from_v2(LABEL)
    cases = [evalrun.score_parts(c, answer, "fraud_guard", {"escalate", "flag"}, None)
             for c in run.cases]  # fmt: skip
    summary = dict(evalrun.summarize(cases, 0, gate_eligible=False))
    summary |= {"set": "corpus", "labels_hash": run.labels_hash}
    r = results.ResultFile(run_id="c" * 32, pair="gemma4-12b/local", set_version=run.version,
                           created_at="2026-10-07T00:00:00Z", cases=cases, digest="d",
                           summary=summary)  # fmt: skip
    return evalrun.write_result(folder, r)


def test_only_confirmed_scored_cases_count_in_the_headline() -> None:
    a = results.ResultFile(run_id="a", pair="p", set_version="s", created_at="t", cases=[
        results.CaseResult(id="1", correct=True), results.CaseResult(id="2", correct=False,
                                                                     scored=False)])  # fmt: skip
    assert results.summary(a).startswith("p: 1/1 correct")
    none = a.model_copy(update={"cases": [results.CaseResult(id="2", correct=False,
                                                             confirmed=False)]})  # fmt: skip
    assert results.summary(none) == "p: no confirmed cases to score"


def test_rescore_uses_the_current_labels_and_keeps_the_original(
    db_path: Path, clock: FakeClock, session: cs.Session
) -> None:
    """R53, R113, R120, R178: no model runs; a new file beside the original; mixed sets refused.
    Results from before v3.0.0 (Gemma's) rescore too (R-D12)."""
    run = evalrun.corpus_run(session)
    assert len(run.cases) == 3 and sum(c.confirmed for c in run.cases) == 1
    first = _corpus_result(run, evalrun.corpus_folder(db_path.parent, run.corpus_id))
    cs.release(session.id)
    reopened = cs.open_session(str(session.path), SECRET, lambda: 0.0)
    s = cs.get(reopened["session_id"], lambda: 0.0)
    labels = cl.load(cl.path_for(s.path))
    third = s.rows[2]["key"]
    cl.put(labels, cl.make((third["content_hash"], third["identity_digest"]), run.corpus_id, LABEL,
                           None, date(2026, 10, 8)))  # fmt: skip
    cl.save(cl.path_for(s.path), labels)
    got = evalrun.rescore(s, first, clock)
    second = Path(got["path"])
    assert second != first and first.exists() and second.name.startswith(first.stem + "-rescore-")
    old, new = results.load_result(first), results.load_result(second)
    assert old.summary is not None and new.summary is not None
    assert new.summary["confirmed"] == 2 and old.summary["confirmed"] == 1
    assert new.set_version != old.set_version and new.summary["gate_passed"] is False
    assert new.summary["rescored_from"]["run_id"] == old.run_id  # type: ignore[index]
    with pytest.raises(InvalidInputError, match="different sets"):
        results.compare(old, new)
    wrong = first.with_name("other.json")
    wrong.write_text(old.model_copy(update={"set_version": "corpus:elsewhere:x"})
                     .model_dump_json())  # fmt: skip
    with pytest.raises(InvalidInputError, match="isn't from this corpus"):
        evalrun.rescore(s, wrong, clock)


@pytest.mark.usefixtures("extensions_on")
def test_corpus_labels_are_checked_against_the_effective_schema(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, session: cs.Session
) -> None:
    labels = cl.load(cl.path_for(session.path))
    third = session.rows[2]["key"]
    schema = extend_schema(load_schema(), EXT)
    cl.put(labels, cl.make((third["content_hash"], third["identity_digest"]),
                           str(session.corpus.first["corpus_id"]),
                           LABEL | {"contract_stage": "draft"}, None, date(2026, 10, 8),
                           schema))  # fmt: skip
    cl.save(cl.path_for(session.path), labels)
    with pytest.raises(InvalidInputError, match="exactly the schema"):
        evalrun.corpus_run(session)  # the shipped schema alone refuses it
    extend(conn, clock)
    run = evalrun.corpus_run(session, config.current_schema(conn))
    [case] = [c for c in run.cases if c.id == "00003"]
    assert case.confirmed and case.expected["labels"]["contract_stage"] == "draft"


def test_deterministic_noise_counts_rules_firing_on_harmless_labels() -> None:
    def case(rule: str, fraud: str, money: bool, confirmed: bool = True) -> evalrun.Case:
        return evalrun.Case("x", Path(), {"rule": rule, "labels": {"fraud_risk": fraud,
                            "payment_related": money}}, confirmed, "operator")  # fmt: skip

    cases = [case("fraud_weak", "none", False), case("unverified_payment_sender", "none", False),
             case("fraud_guard", "high", True), case("marketing", "none", False),
             case("fraud_weak", "none", False, confirmed=False)]  # fmt: skip
    assert evalrun.deterministic_noise(cases) == {"flagged": 2, "of": 4}
