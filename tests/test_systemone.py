"""Decision models through `/v1/systemone` (SPEC §7.8; OD-471; ADR 0029): questions from the schema,
answer mapping, failures, and no request text in errors (R16)."""

from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import sqlite3
from collections.abc import Callable
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from ecf.cli import app
from ecf.errors import InvalidInputError, ServiceUnavailableError
from ecf.eval import labels
from ecf.eval.builder import build_all
from ecf.eval.results import CaseResult, ResultFile
from ecf.schema import FIXED_ITEMS, derive_fixed, load_schema, local_schema
from ecf_server import (
    claude_eval,
    claude_pins,
    db,
    evalrun,
    gate,
    modelq,
    models,
    ollama,
    systemone,
)
from ecf_server.clock import FakeClock
from ecf_server.ollama import Client, OllamaError
from tests.helpers import BEC, PS_ENV, ChatOllama, check_kw
from tests.test_evalrun import AC, SYNTHETIC

SCHEMA = load_schema()
CANARY = "CANARY-7f3a"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> Client:
    return Client(transport=httpx.MockTransport(handler))


def _answers(**over: Any) -> dict[str, Any]:
    """A full, valid reply: invoice, fraud_risk high, payment_related (a value schema v2 and v3
    both have; S3b removed `vendor_change_request` from v3)."""
    a: dict[str, Any] = {
        "category": {
            "type": "choice",
            "choice": "invoice",
            "probabilities": {"invoice": 0.9, "other": 0.1},
        },
        "sender_type": {
            "type": "choice",
            "choice": "vendor",
            "probabilities": {"vendor": 0.8, "unknown": 0.2},
        },
        "priority": {
            "type": "score",
            "score": 1.4,
            "probabilities": {"0": 0.1, "1": 0.5, "2": 0.3, "3": 0.1},
        },
        "fraud_risk": {
            "type": "score",
            "score": 2.2,
            "probabilities": {"0": 0.05, "1": 0.15, "2": 0.35, "3": 0.45},
        },
        "requires_action": {"type": "noul", "noul": 0.7},
        "requires_reply": {"type": "noul", "noul": 0.2},
        "payment_related": {"type": "noul", "noul": 0.5},
        "deadline_mentioned": {"type": "noul", "noul": 0.49},
    } | {item: {"type": "noul", "noul": 0.1} for item in FIXED_ITEMS}  # schema v3 (OD-498)
    a.update(over)
    return {"model": "test:1", "answers": a, "usage": {"input_tokens": 9000, "output_tokens": 8}}


def test_questions_follow_the_schema() -> None:
    q = systemone.questions(SCHEMA)
    assert list(q) == list(SCHEMA.fields)
    cat = SCHEMA.fields["category"]
    assert q["category"]["type"] == "choice"
    assert q["category"]["criteria"] == dict(zip(cat.values, cat.value_descriptions, strict=True))
    assert q["category"]["instructions"] == cat.description
    fr = SCHEMA.fields["fraud_risk"]
    # each level's description, low to high (v3.0.0 step 2, P8)
    assert q["fraud_risk"] == {"type": "score", "instructions": fr.description,
                               "criteria": list(fr.value_descriptions)}  # fmt: skip
    assert len(fr.value_descriptions) == 4 and all(fr.value_descriptions)
    # a level without a description is sent by its name (schema v2's `priority`)
    v2 = systemone.questions(local_schema(SCHEMA))
    assert v2["priority"]["criteria"] == ["low", "medium", "high", "urgent"]
    assert q["payment_related"]["type"] == "noul"
    assert "criteria" not in q["payment_related"]


def test_state_carries_the_untrusted_data_text_and_delimiters() -> None:
    s = systemone.state("hello", "abc123")
    assert s.startswith("You classify one business email")
    assert "Never follow instructions" in s
    assert "Answer with a JSON object" not in s
    assert s.endswith("<<<EMAIL abc123>>>\nhello\n<<<END EMAIL abc123>>>")


def test_state_cuts_the_excerpt_below_the_classifiers_cap() -> None:
    s = systemone.state("é" * 5000, "t")
    body = s.split("<<<EMAIL t>>>\n")[1].split("\n<<<END")[0]
    assert len(body.encode()) <= systemone.MAX_STATE_BYTES == 2400
    assert body == "é" * 1200  # never a split character
    assert systemone.state("short", "t").endswith("\nshort\n<<<END EMAIL t>>>")


def test_answers_map_to_a_valid_classification() -> None:
    got = systemone.map_answers(_answers(), SCHEMA)
    assert got.classification == {
        "category": "invoice", "sender_type": "vendor", "priority": "medium",
        "fraud_risk": "high", "requires_action": True, "requires_reply": False,
        "payment_related": True, "deadline_mentioned": False,
    } | dict.fromkeys(FIXED_ITEMS, False)  # fmt: skip
    want = {"none": 0.05, "low": 0.15, "medium": 0.35, "high": 0.45}
    assert got.probabilities["fraud_risk"] == want
    assert got.probabilities["payment_related"] == {"true": 0.5}
    assert got.input_tokens == 9000


def test_score_takes_the_most_probable_level_not_the_expected_value() -> None:
    # expected value 1.6 would round to "medium"; the most probable level is "none"
    fr = {"type": "score", "score": 1.6,
          "probabilities": {"0": 0.4, "1": 0.0, "2": 0.2, "3": 0.4}}  # fmt: skip
    got = systemone.map_answers(_answers(fraud_risk=fr), SCHEMA)
    assert got.classification is not None
    assert got.classification["fraud_risk"] == "high"  # tie between none and high goes upward


@pytest.mark.parametrize(
    "over",
    [
        {"category": {"type": "choice", "choice": "not_a_category", "probabilities": {}}},
        {"fraud_risk": {"type": "score", "probabilities": {"0": 1.0}}},
        {"payment_related": {"type": "noul"}},
        {"payment_related": {"type": "noul", "noul": True}},
        {"sender_type": "vendor"},
    ],
)
def test_an_answer_that_does_not_map_is_a_failed_attempt(over: dict[str, Any]) -> None:
    assert systemone.map_answers(_answers(**over), SCHEMA).classification is None


def test_a_missing_question_is_a_failed_attempt() -> None:
    raw = _answers()
    del raw["answers"]["deadline_mentioned"]
    assert systemone.map_answers(raw, SCHEMA).classification is None
    assert systemone.map_answers({"model": "x"}, SCHEMA).classification is None


def test_ask_sends_the_questions_and_the_state() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["host"] = req.url.host
        seen["body"] = json.loads(req.content)
        vcr = {"type": "choice", "choice": "vendor_change_request",
               "probabilities": {"vendor_change_request": 0.9, "invoice": 0.1}}  # fmt: skip
        return httpx.Response(200, json=_answers(category=vcr))  # a v2 value: it was asked v2

    got = systemone.ask(_client(handler), "ecf/test-decision:1", "the email", SCHEMA)
    assert got.classification is not None
    assert seen["path"] == "/v1/systemone"
    assert seen["host"] == "127.0.0.1"
    body = seen["body"]
    assert body["model"] == "ecf/test-decision:1"
    assert body["keep_alive"] == "5m"
    # asked schema v2, as Gemma is (P6; S2 review F5): no fixed items asked ...
    assert body["questions"] == systemone.questions(local_schema(SCHEMA))
    assert len(body["questions"]) == 8 and not set(FIXED_ITEMS) & set(body["questions"])
    # ... and the answer brought to v3 (`from_v2`, S3b): the items derived from v2's (the
    # reply's own ignored), then the removed value read as `other`
    assert got.classification["bank_detail_change"] is True
    assert got.classification["category"] == "other"
    v2_answer = got.classification | {"category": "vendor_change_request"}
    assert {k: got.classification[k] for k in FIXED_ITEMS} == derive_fixed(v2_answer)
    assert "the email" in body["state"]


def test_over_the_context_is_a_failed_attempt_with_no_request_text() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": f"prompt 0 has 2051 tokens {CANARY}"})

    got = systemone.ask(_client(handler), "m", CANARY, SCHEMA)
    assert got.classification is None


@pytest.mark.parametrize("status", [413, 422, 500])
def test_http_errors_keep_only_the_status(status: int) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": f"bad state near {CANARY}"})

    with pytest.raises(OllamaError) as err:
        systemone.ask(_client(handler), "m", CANARY, SCHEMA)
    assert CANARY not in str(err.value)
    assert err.value.detail == f"HTTP {status}"


def test_a_server_fault_keeps_its_cause_without_text() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": f"runner out of memory {CANARY}"})

    with pytest.raises(OllamaError) as err:
        systemone.ask(_client(handler), "m", "x", SCHEMA)
    assert err.value.cause == "server"
    assert CANARY not in str(err.value)


def test_a_missing_model_is_model_missing() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": 'model "m" not found'})

    with pytest.raises(OllamaError) as err:
        systemone.ask(_client(handler), "m", "x", SCHEMA)
    assert err.value.cause == "model_missing"


def test_the_service_classifier_path_never_imports_systemone() -> None:
    """Nor Jev (v3.0.0 step 2, P9); `pipeline` went with the local model's queue (ADR 0031), and
    step 4's dispatch module is its own; the import-linter contract keeps `classifier` from
    importing `jev` indirectly."""
    for mod in ("ecf_server.classifier", "ecf_server.modelq", "ecf_server.actor"):
        spec = importlib.util.find_spec(mod)
        assert spec is not None and spec.origin is not None
        with open(spec.origin, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom | ast.Import)
                 for a in n.names}  # fmt: skip
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert "systemone" not in names and "ecf_server.systemone" not in mods, mod
        assert "jev" not in names and "ecf_server.jev" not in mods, mod


# ---- the eval-only lock (Phase 2b; empty since OD-494) -----------------------------------------


# No decision model is pinned since OD-494 (tev1 and every Qwen-based model banned), so the eval
# harness is tested with a test-only pin patched into `load_pins`.
TEV = ollama.Pin(tag="test-decision:1", digest="9b" * 32, ecf_name="ecf/test-decision")
REAL_LOAD_PINS = systemone.load_pins


@pytest.fixture(autouse=True)
def _test_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(systemone, "load_pins", lambda: {"test-decision": TEV})


class FakeOllama:
    """Tags, pull (of any tag, to `upstream`), copy and delete."""

    def __init__(self, upstream: str = TEV.digest) -> None:
        self.models: dict[str, str] = {}
        self.upstream = upstream
        self.pulled: list[str] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/api/tags":
            listed = [{"name": k, "digest": v} for k, v in self.models.items()]
            return httpx.Response(200, json={"models": listed})
        if path == "/api/pull":
            tag = json.loads(req.content)["model"]
            self.pulled.append(tag)
            self.models[tag] = self.upstream
            return httpx.Response(200, content=json.dumps({"status": "success"}).encode())
        if path == "/api/copy":
            body = json.loads(req.content)
            self.models[body["destination"]] = self.models[body["source"]]
            return httpx.Response(200)
        if path == "/api/delete":
            self.models.pop(json.loads(req.content)["model"], None)
            return httpx.Response(200)
        return httpx.Response(404, json={"error": "not found"})

    def client(self) -> Client:
        return Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture(autouse=True)
def _idle() -> None:
    models.INSTALLS.set(state="idle", status="", error="", completed=0, total=0)


def test_the_lock_is_empty_since_qwen_was_banned() -> None:
    raw = json.loads(resources.files("ecf_server.data").joinpath(systemone.LOCK).read_text("utf-8"))
    assert raw["models"] == {}, "no decision model is pinned (OD-494)"
    assert REAL_LOAD_PINS() == {}
    assert "tev1" not in json.dumps(raw["models"])


def test_the_production_pin_stays_gemma() -> None:
    assert ollama.load_pin().tag == "gemma4:12b-it-q4_K_M"
    assert all(p.ecf_name != ollama.load_pin().ecf_name for p in REAL_LOAD_PINS().values())


def test_with_the_shipped_lock_every_decision_backend_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(systemone, "load_pins", REAL_LOAD_PINS)
    with pytest.raises(InvalidInputError, match=r"known: none"):
        evalrun.decision_pin("systemone:tev1-4b")


def test_a_refused_decision_backend_records_no_run(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    monkeypatch.setattr(systemone, "load_pins", REAL_LOAD_PINS)
    with pytest.raises(InvalidInputError, match=r"known: none"):
        _start(db_path, clock, root, DecisionOllama(), backend="systemone:tev1-4b")
    assert conn.execute("SELECT count(*) FROM eval_runs").fetchone()[0] == 0


def test_an_unknown_decision_model_is_refused() -> None:
    with pytest.raises(InvalidInputError, match="no decision model"):
        systemone.pin("qwen3:32b")


def test_verify_checks_ecfs_copy() -> None:
    fake = FakeOllama()
    with pytest.raises(OllamaError) as err:
        systemone.verify(fake.client(), TEV)
    assert err.value.cause == "model_missing"
    fake.models[TEV.ecf_tag] = "c" * 64
    with pytest.raises(OllamaError) as err:
        systemone.verify(fake.client(), TEV)
    assert err.value.cause == "digest_mismatch"
    fake.models[TEV.ecf_tag] = TEV.digest
    assert systemone.verify(fake.client(), TEV) == TEV.digest


# ---- `ecf eval run --classifier-backend` (Phase 2c) --------------------------------------------


CAPPED_ENV = PS_ENV + " LLAMA_ARG_CACHE_RAM=1024"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "synthetic"
    (r / "cases").mkdir(parents=True)
    for name in ("starter-bec.md", "starter-control.md", "starter-injection.md"):
        shutil.copy(SYNTHETIC / "cases" / name, r / "cases" / name)
    assert not build_all(r).findings
    labels.confirm(r, "starter-bec", date(2026, 10, 1))
    labels.confirm(r, "starter-injection", date(2026, 10, 1))
    return r


@pytest.fixture(autouse=True)
def _reset_run() -> None:
    evalrun.RUN.set(state="idle", run_id="", done=0, total=0, result=None, detail="")
    evalrun.RUN.stop.clear()
    if modelq.EXCLUSIVE.held():
        modelq.EXCLUSIVE.release()


def _inline(work: Callable[[], None]) -> None:
    work()


class DecisionOllama(ChatOllama):
    """Gemma's chat (the actor) plus a decision model on `/v1/systemone`."""

    def __init__(self, decision_digest: str = TEV.digest) -> None:
        super().__init__(json.dumps(BEC))
        self.models[TEV.ecf_tag] = decision_digest
        self.decision_calls = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/systemone":
            self.decision_calls += 1
            return httpx.Response(200, json=_answers())
        return super().handler(req)


def _start(db_path: Path, clock: FakeClock, root: Path, fake: ChatOllama,
           ps: str = CAPPED_ENV, **opts: Any) -> dict[str, Any]:  # fmt: skip
    return evalrun.start(lambda: db.connect(db_path), clock, fake.client, db_path.parent,
                         evalrun.Options(root, **opts), power=lambda: AC, battery=lambda: 90,
                         spawn=_inline, check_kw=check_kw(ps=ps))  # fmt: skip


def test_a_decision_model_run_never_reads_as_gemmas(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    gemma = ollama.load_pin().digest
    before = gate.inputs(conn, "a1", gemma)
    fake = DecisionOllama()
    _start(db_path, clock, root, fake, backend="systemone:test-decision")
    snap = evalrun.RUN.snapshot()
    assert snap["state"] == "done", snap
    assert fake.decision_calls >= 3  # every case, then the determinism re-run
    row = conn.execute("SELECT pair, digest, gate_passed, path FROM eval_runs").fetchone()
    assert (row["pair"], row["digest"], row["gate_passed"]) == (
        "systemone-test-decision/local",
        TEV.digest,
        0,
    )
    summary = json.loads(Path(row["path"]).read_text())["summary"]
    assert summary["backend"] == "systemone:test-decision"
    assert summary["classifier_digest"] == TEV.digest and summary["actor_digest"] == gemma
    assert summary["schema_digest"] == local_schema(SCHEMA).digest  # asked v2, as Gemma (F5)
    assert summary["gate_passed"] is False
    assert summary["classifier_latency_s"]["p50"] is not None
    assert summary["power"] == {"ac_at_start": True, "ac_at_end": True, "paused": False}
    case = json.loads(Path(row["path"]).read_text())["cases"][0]
    assert case["probabilities"]["fraud_risk"]["high"] == 0.45
    assert isinstance(case["classifier_ms"], int)
    # no gate reader takes it for Gemma's (R1)
    assert evalrun.latest(conn, gemma) is None
    assert "no synthetic-set result" in gate.synthetic(conn, gemma).detail
    assert gate.inputs(conn, "a1", gemma) == before
    with pytest.raises(InvalidInputError, match="there is none"):
        claude_eval._a_run(conn, evalrun.set_version(root))  # pyright: ignore[reportPrivateUsage]


def test_a_decision_model_run_needs_the_cache_cap(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    for ps in (PS_ENV, PS_ENV + " LLAMA_ARG_CACHE_RAM=8192", PS_ENV + " LLAMA_ARG_CACHE_RAM=-1"):
        with pytest.raises(ServiceUnavailableError, match="LLAMA_ARG_CACHE_RAM"):
            _start(db_path, clock, root, DecisionOllama(), ps=ps, backend="systemone:test-decision")
    assert conn.execute("SELECT count(*) FROM eval_runs").fetchone()[0] == 0


def test_a_decision_model_run_refuses_a_changed_copy(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    with pytest.raises(ServiceUnavailableError, match="pinned one"):
        _start(db_path, clock, root, DecisionOllama("c" * 64), backend="systemone:test-decision")
    assert conn.execute("SELECT count(*) FROM eval_runs").fetchone()[0] == 0


@pytest.mark.parametrize("backend", ["systemone:qwen3-32b", "llama", "systemone:"])
def test_an_unknown_backend_is_refused(db_path: Path, clock: FakeClock, root: Path,
                                       backend: str) -> None:  # fmt: skip
    with pytest.raises(InvalidInputError):
        _start(db_path, clock, root, DecisionOllama(), backend=backend)
    assert evalrun.RUN.snapshot()["state"] == "idle"


def test_the_null_arm_runs_without_the_classifier_model(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    fake = DecisionOllama()
    _start(db_path, clock, root, fake, ps=PS_ENV, backend="null")
    assert evalrun.RUN.snapshot()["state"] == "done"
    assert fake.decision_calls == 0
    assert all(b["options"]["num_predict"] == ollama.NUM_PREDICT["actor"] for b in fake.bodies)
    row = conn.execute("SELECT pair, digest, gate_passed FROM eval_runs").fetchone()
    assert (row["pair"], row["digest"], row["gate_passed"]) == ("null/local", "null", 0)


def test_the_null_classification_is_the_least_risky() -> None:
    assert evalrun.null_classification(SCHEMA) == {
        "category": "other",
        "priority": "low",
        "requires_action": False,
        "requires_reply": False,
        "payment_related": False,
        "deadline_mentioned": False,
        "sender_type": "unknown",
        "fraud_risk": "none",
    } | dict.fromkeys(FIXED_ITEMS, False)


def test_no_redact_runs_only_on_the_fraud_subset_and_never_passes(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    with pytest.raises(InvalidInputError, match="--fraud-only"):
        _start(db_path, clock, root, ChatOllama(json.dumps(BEC)), ps=PS_ENV, redact=False)
    _start(db_path, clock, root, ChatOllama(json.dumps(BEC)), ps=PS_ENV, redact=False,
           fraud_only=True)  # fmt: skip
    row = conn.execute("SELECT pair, gate_passed, metrics FROM eval_runs").fetchone()
    assert row["pair"] == "gemma4-12b/local" and row["gate_passed"] == 0
    assert json.loads(row["metrics"])["redact"] is False


def test_a_gemma_run_still_records_preset_as_pair(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    _start(db_path, clock, root, ChatOllama(json.dumps(BEC)), ps=PS_ENV)
    row = conn.execute("SELECT pair, digest, metrics FROM eval_runs").fetchone()
    assert row["pair"] == "gemma4-12b/local" and row["digest"] == claude_pins.local_key()
    assert json.loads(row["metrics"])["backend"] == "gemma"


# ---- run figures (Phase 2d) --------------------------------------------------------------------


def test_a_decision_model_run_reports_calibration_and_under_rating(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    _start(db_path, clock, root, DecisionOllama(), backend="systemone:test-decision")
    summary = json.loads(conn.execute("SELECT metrics FROM eval_runs").fetchone()[0])
    under = summary["fraud_under"]
    assert under["of"] >= 1 and 0 <= under["under"] <= under["of"]
    cal = summary["calibration"]
    assert "category" in cal
    cat = cal["category"]
    assert cat["n"] >= 1 and 0 <= cat["ece"] <= 1
    assert sum(b["n"] for b in cat["reliability"]) == cat["n"]
    if "fraud_risk" in cal:
        assert "rps" in cal["fraud_risk"]


def test_a_gemma_run_has_no_calibration_but_reports_under_rating(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    _start(db_path, clock, root, ChatOllama(json.dumps(BEC)), ps=PS_ENV)
    summary = json.loads(conn.execute("SELECT metrics FROM eval_runs").fetchone()[0])
    assert "calibration" not in summary
    assert summary["fraud_under"]["of"] >= 1


# ---- `ecf eval compare`, `ecf models status`, doctor (Phase 2e) --------------------------------


def _file(tmp: Path, name: str, backend: str, correct: list[bool], **extra: Any) -> Path:
    cases = [CaseResult(id=f"c{i}", correct=c, fields={"category": c})
             for i, c in enumerate(correct)]  # fmt: skip
    summary: dict[str, object] = {"backend": backend, "redact": True, **extra}
    r = ResultFile(run_id=name, pair=evalrun.pair_for(backend), set_version="v",
                   created_at="t", cases=cases, summary=summary)  # fmt: skip
    path = tmp / f"{name}.json"
    path.write_text(r.model_dump_json())
    return path


def test_compare_shows_the_experiment_figures(tmp_path: Path) -> None:
    g = _file(tmp_path, "g", "gemma", [True, False] * 10,
              fraud_under={"under": 2, "of": 6})  # fmt: skip
    d = _file(tmp_path, "d", "systemone:test-decision", [True] * 20,
              fraud_under={"under": 1, "of": 6},
              classifier_latency_s={"p50": 2.5, "p95": 3.1},
              power={"ac_at_start": True, "ac_at_end": True, "paused": False},
              calibration={"category": {"n": 20, "accuracy": 1.0, "ece": 0.04,
                                        "ece_ci95": [0.01, 0.08], "ece_baseline": 0.0,
                                        "reliability": []}})  # fmt: skip
    out = CliRunner().invoke(app, ["eval", "compare", str(g), str(d)])
    assert out.exit_code == 0, out.output
    text = out.output
    assert "95% Newcombe interval" in text and "score interval" not in text  # one rule (OD-486)
    assert "confirmatory (exact McNemar, Holm over the two): end-to-end p =" in text
    assert "B  classifier systemone:test-decision" in text
    assert "B  fraud risk under-rated on 1 of 6" in text
    assert "A  fraud risk under-rated on 2 of 6" in text
    assert "B  classifier call 2.5 s median, 3.1 s p95" in text
    assert "B  calibration category: ECE 0.040 (0.010-0.080" in text


def test_compare_of_two_gemma_runs_is_unchanged(tmp_path: Path) -> None:
    a = _file(tmp_path, "a", "gemma", [True, False] * 5)
    b = _file(tmp_path, "b", "gemma", [True] * 10)
    out = CliRunner().invoke(app, ["eval", "compare", str(a), str(b)])
    assert out.exit_code == 0
    assert "score interval" not in out.output and "confirmatory" not in out.output
