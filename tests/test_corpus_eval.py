"""`ecf eval run --corpus`: a labelled real-mail corpus through preset A, never the gate (SPEC
§16.7; R2, R54, R83, R114, R117, R138, R154, R157, R168)."""

from __future__ import annotations

import json
import sqlite3
import stat
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import corpus_labels as cl
from ecf.eval import results
from ecf.schema import extend_schema, load_schema
from ecf_server import corpus_session as cs
from ecf_server import db, evalrun
from ecf_server.api import _corpus_run  # pyright: ignore[reportPrivateUsage]
from ecf_server.clock import FakeClock
from tests.test_classifier import ChatOllama
from tests.test_claude_review import EXT, extend
from tests.test_corpus import SECRET, fetch, req, server_with
from tests.test_evalrun import AC, BEC, _inline  # pyright: ignore[reportPrivateUsage]
from tests.test_models import check_kw


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
    cl.put(labels, cl.make(keys[0], opened["corpus_id"], BEC, None, date(2026, 10, 7)))
    cl.put(labels, cl.make(keys[1], opened["corpus_id"], None, "u", date(2026, 10, 7)))
    cl.save(cl.path_for(path), labels)  # the third message stays unlabelled
    yield s
    st = cs.status(lambda: 0.0)
    if st is not None:
        cs.release(str(st["session_id"]))


def test_a_corpus_run_writes_only_its_file_and_never_passes_the_gate(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, session: cs.Session
) -> None:
    run = _corpus_run(session.id, fraud_only=False)
    assert run is not None and session.busy
    evalrun.start(lambda: db.connect(db_path), clock, ChatOllama(json.dumps(BEC)).client,
                  db_path.parent, evalrun.Options(Path("/"), corpus=run), power=lambda: AC,
                  battery=lambda: 90, spawn=_inline, check_kw=check_kw())  # fmt: skip
    snap = evalrun.RUN.snapshot()
    assert snap["state"] == "done", snap
    assert snap["set"] == f"corpus {run.corpus_id[:8]}"
    summary = snap["result"]
    assert summary["gate_passed"] is False and summary["set"] == "corpus"
    assert summary["cases"] == 3 and summary["confirmed"] == 1 and summary["rule_scored"] == 1
    assert summary["labels_hash"] == run.labels_hash and len(run.labels_hash) == 64
    assert summary["deterministic_noise"] == {"flagged": 0, "of": 1}
    assert {"category", "sender_type", "requires_reply", "deadline_mentioned"} <= set(
        summary["per_field"]
    )
    # run separation (R2): no eval_runs row, the synthetic set's root untouched
    assert conn.execute("SELECT count(*) FROM eval_runs").fetchone()[0] == 0
    root = conn.execute("SELECT value FROM settings WHERE key = ?", (evalrun.EVAL_ROOT,))
    assert root.fetchone() is None
    folder = evalrun.corpus_folder(db_path.parent, run.corpus_id)
    (path,) = list(folder.glob("*.json"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    result = results.load_result(path)
    assert result.set_version == f"corpus:{run.corpus_id}:{run.labels_hash[:12]}"
    by_id = {c.id: c for c in result.cases}
    assert by_id["00001"].confirmed and by_id["00001"].scored and by_id["00001"].actions
    assert not by_id["00003"].scored and not by_id["00003"].confirmed
    assert "Invoice" not in path.read_text()  # no message text
    audit = conn.execute("SELECT data FROM audit WHERE event = 'eval.completed'").fetchone()
    assert json.loads(audit["data"])["set_version"] == result.set_version
    assert cs.status(lambda: 0.0) is None  # the run released the corpus (R154)
    synthetic = result.model_copy(update={"set_version": "abc"})
    with pytest.raises(InvalidInputError, match="different sets"):
        results.compare(result, synthetic)


def test_a_corpus_run_refuses_fraud_only_and_unredacted_text(
    db_path: Path, clock: FakeClock, session: cs.Session
) -> None:
    with pytest.raises(InvalidInputError, match="synthetic set"):
        _corpus_run(session.id, fraud_only=True)
    assert not session.busy
    run = _corpus_run(session.id, fraud_only=False)
    assert run is not None
    with pytest.raises(InvalidInputError, match="stored, redacted"):
        evalrun.start(lambda: db.connect(db_path), clock, ChatOllama("{}").client, db_path.parent,
                      evalrun.Options(Path("/"), redact=False, corpus=run), spawn=_inline,
                      check_kw=check_kw())  # fmt: skip


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
    """R53, R113, R120, R178: no model runs; a new file beside the original; mixed sets refused."""
    run = _corpus_run(session.id, fraud_only=False)
    assert run is not None
    evalrun.start(lambda: db.connect(db_path), clock, ChatOllama(json.dumps(BEC)).client,
                  db_path.parent, evalrun.Options(Path("/"), corpus=run), power=lambda: AC,
                  battery=lambda: 90, spawn=_inline, check_kw=check_kw())  # fmt: skip
    (first,) = list(evalrun.corpus_folder(db_path.parent, run.corpus_id).glob("*.json"))
    reopened = cs.open_session(str(session.path), SECRET, lambda: 0.0)
    s = cs.get(reopened["session_id"], lambda: 0.0)
    labels = cl.load(cl.path_for(s.path))
    third = s.rows[2]["key"]
    cl.put(labels, cl.make((third["content_hash"], third["identity_digest"]), run.corpus_id, BEC,
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
                           BEC | {"contract_stage": "draft"}, None, date(2026, 10, 8),
                           schema))  # fmt: skip
    cl.save(cl.path_for(session.path), labels)
    with pytest.raises(InvalidInputError, match="exactly the schema"):
        _corpus_run(session.id, fraud_only=False)  # the shipped schema alone refuses it
    extend(conn, clock)
    run = _corpus_run(session.id, fraud_only=False, connect=lambda: db.connect(db_path))
    assert run is not None
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
