"""`/ecf-eval` (V1.4 step 7; SPEC §10.4, §16.2-§16.3; OD-287 to OD-292): runs registered from
the command line, cases claimed under random references through the same tools and checks as
`/ecf-review`, scoring as `ecf eval run`, the model check, preset B on Gemma's classifications,
comparison runs, stop and expiry, and the routes' profiles."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, cast

import anyio
import httpx
import pytest

from ecf.errors import ConflictError, ForbiddenProfileError, InvalidInputError, NotFoundError
from ecf.eval import labels
from ecf.eval.builder import build_all
from ecf.eval.results import CaseResult, ResultFile, load_result
from ecf_server import claude_eval, claude_pins, db, evalrun, gate, ollama
from ecf_server.api import ServiceState, create_app
from ecf_server.clock import FakeClock, to_ts
from ecf_server.notify import NullNotifier
from ecf_server.telemetry import SUBAGENT, ApiCall, Hold, Seen, Telemetry
from tests.test_decide import MARKETING

SYNTHETIC = Path(__file__).parent / "eval" / "synthetic"
BEC: dict[str, Any] = {"category": "vendor_change_request", "priority": "high",
                       "requires_action": True, "requires_reply": False, "payment_related": True,
                       "deadline_mentioned": False, "sender_type": "vendor",
                       "fraud_risk": "high"}  # fmt: skip
REQUEST: dict[str, Any] = MARKETING | {"category": "customer_request", "requires_reply": True,
                                       "requires_action": True}  # fmt: skip
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


def model_for(agent: str) -> str:
    run = claude_eval.current()
    assert run is not None
    return run.models[claude_eval.role_of(agent)]


def as_agent(agent: str) -> Callable[[], Seen]:
    return lambda: Seen(model_for(agent), SUBAGENT)


def settled(db_path: Path, clock: FakeClock, got: dict[str, Any] | Hold,
            seen: Seen | None = None, tel: Telemetry | None = None) -> dict[str, Any]:  # fmt: skip
    if not isinstance(got, Hold):
        return got
    seen = seen or Seen(model_for(got.agent), SUBAGENT)
    return claude_eval.settle(connector(db_path), clock, NullNotifier(), tel or Telemetry(), got,
                              seen)  # fmt: skip


def answer(db_path: Path, clock: FakeClock, item: dict[str, Any], session: str,
           classify: dict[str, Any], action: str = "flag") -> dict[str, Any]:  # fmt: skip
    """What a well-behaved agent does with one claimed case."""
    ref, token = item["id"], item["claim_token"]
    claude_eval.get_message(clock, session, ref, token, as_agent(item["agent"]))
    if item["need"] == "classify":
        got = claude_eval.record_classification(clock, session, ref, token, classify, "toolu_x")
    else:
        got = claude_eval.propose_action(clock, session, ref, token,
                                         {"action": action, "reason": "needs a look"},
                                         "toolu_x")  # fmt: skip
    return settled(db_path, clock, got)


def drain(db_path: Path, clock: FakeClock, by_case: Callable[[str], dict[str, Any]],
          session: str = S1) -> list[dict[str, Any]]:  # fmt: skip
    """Rounds of eval_next until the run ends; every item it handed out."""
    run = claude_eval.current()
    assert run is not None
    case_of = {w.ref: w.case.id for w in run.works}
    seen: list[dict[str, Any]] = []
    for _ in range(10):
        q = claude_eval.eval_next(connector(db_path), clock, session)
        if q["done"]:
            return seen
        for item in q["items"]:
            seen.append(item)
            assert answer(db_path, clock, item, session, by_case(case_of[item["id"]]))["accepted"]
    stages = [(w.stage, w.claim) for w in run.works]
    raise AssertionError(f"the run didn't end: {stages}")


def c_address(conn: sqlite3.Connection, clock: FakeClock) -> None:
    conn.execute("INSERT INTO addresses (address_id, email, sensitivity, preset, created_at)"
                 " VALUES ('c', 'c@acme.example', 'standard', 'C', ?)",
                 (to_ts(clock.now()),))  # fmt: skip


# ---- a run end to end ------------------------------------------------------------------------


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
    assert {i["agent"] for i in handed if i["need"] == "classify"} == {"ecf:classifier"}
    assert {i["agent"] for i in handed if i["need"] == "act"} <= {"ecf:actor", "ecf:actor-high"}
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
    assert set(m["claude"]["models"]) == {"classifier", "classifier_high", "actor", "actor_high"}
    text = Path(row["path"]).read_text()
    assert "IBAN" not in text and "SYSTEM NOTE" not in text  # metrics only
    # the go-live gate of a C address reads it (its key is the pins')
    check = gate.synthetic(conn, key, "C")
    assert check.ok is (not m["unsafe"]) and run.run_id[:8] in check.detail


def test_cases_go_out_under_random_references_with_no_gold_labels(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    q = claude_eval.eval_next(connector(db_path), clock, S1)
    out = [q]
    for item in q["items"]:
        assert len(item["id"]) == 32 and int(item["id"], 16) >= 0
        assert item["spawn"] == "classifier" and item["need"] == "classify"
        out.append(claude_eval.get_message(clock, S1, item["id"], item["claim_token"],
                                           as_agent(item["agent"])))  # fmt: skip
    text = json.dumps(out)
    for case_id in CASES:
        assert case_id not in text
    for gold in ("expected", "safety", "injection_target", "must_escalate", "fraud_guard"):
        assert gold not in text
    msg = out[1]
    assert set(msg) == {"id", "need", "untrusted_email", "notice", "schema"}
    assert set(msg["untrusted_email"]) == {"from", "subject", "date", "text", "attachments_meta"}


def test_only_a_plugin_agent_reads_a_case(db_path: Path, clock: FakeClock, root: Path) -> None:
    start(db_path, clock, root)
    item = claude_eval.eval_next(connector(db_path), clock, S1)["items"][0]
    for seen in (lambda: None, lambda: Seen("claude-haiku-4-5-20251001", "main")):
        with pytest.raises(ForbiddenProfileError):
            claude_eval.get_message(clock, S1, item["id"], item["claim_token"], seen)


def test_the_model_check_refuses_another_model_and_stops_the_session(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    item = claude_eval.eval_next(connector(db_path), clock, S1, limit=1)["items"][0]
    got = claude_eval.record_classification(clock, S1, item["id"], item["claim_token"], BEC,
                                            "toolu_x")  # fmt: skip
    tel = Telemetry()
    tel.open(S1)
    out = settled(db_path, clock, got, Seen("claude-opus-5-5", SUBAGENT), tel)
    assert out["accepted"] is False and "claude-opus-5-5" in out["errors"][0]
    stopped = tel.stopped(S1)
    assert stopped is not None
    q = claude_eval.eval_next(connector(db_path), clock, S1, stopped=stopped)
    assert q["stopped"] == stopped and not q["items"]
    assert q["results"] == [{"id": item["id"], "outcome": "model_refused"}]
    # the case waits for another session
    again = claude_eval.eval_next(connector(db_path), clock, S2)
    assert item["id"] in {i["id"] for i in again["items"]}


def test_three_invalid_tries_score_the_case_as_a_failure(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    item = claude_eval.eval_next(connector(db_path), clock, S1, limit=1)["items"][0]
    tries = [claude_eval.record_classification(clock, S1, item["id"], item["claim_token"],
                                               {"category": "tax"}, "toolu_x")
             for _ in range(3)]  # fmt: skip
    assert [cast(dict[str, Any], t)["tries_left"] for t in tries] == [2, 1, 0]
    assert all("tax" not in json.dumps(t) for t in tries)  # errors never quote the value
    run = claude_eval.current()
    assert run is not None
    w = run.by_ref[item["id"]]
    assert w.stage == "done" and w.result is not None and w.result.correct is False
    q = claude_eval.eval_next(connector(db_path), clock, S1)
    assert {"id": item["id"], "outcome": "invalid"} in q["results"]


def test_claims_expire_and_are_fenced(db_path: Path, clock: FakeClock, root: Path) -> None:
    start(db_path, clock, root)
    old = claude_eval.eval_next(connector(db_path), clock, S1, limit=1)["items"][0]
    clock.advance(16 * 60)
    q = claude_eval.eval_next(connector(db_path), clock, S2, limit=3)
    assert q["results"] == []  # S1's outcome goes to S1
    new = next(i for i in q["items"] if i["id"] == old["id"])
    assert new["claim_token"].split(".")[0] == "2"
    with pytest.raises(ConflictError):
        claude_eval.record_classification(clock, S1, old["id"], old["claim_token"], BEC, "t")
    assert claude_eval.eval_next(connector(db_path), clock, S1)["results"] == [
        {"id": old["id"], "outcome": "claim_expired"}]  # fmt: skip


def test_a_session_ending_frees_its_claims(db_path: Path, clock: FakeClock, root: Path) -> None:
    start(db_path, clock, root)
    claude_eval.eval_next(connector(db_path), clock, S1)
    claude_eval.release_session(S1)
    assert len(claude_eval.eval_next(connector(db_path), clock, S2)["items"]) == 3


# ---- models, presets and options -------------------------------------------------------------


def test_a_comparison_run_uses_the_eval_agents_and_never_counts_for_the_gate(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    r = start(db_path, clock, root, classifier_model="claude-opus-5-5")
    assert r["pinned"] is False and r["models"]["classifier"] == "claude-opus-5-5"
    info = claude_eval.session_info(connector(db_path), clock)
    assert info is not None and info["agents"]["eval-classifier"] == "claude-opus-5-5"
    items = claude_eval.eval_next(connector(db_path), clock, S1)["items"]
    assert {i["agent"] for i in items} == {"ecf:eval-classifier"}
    claude_eval.release_session(S1)
    drain(db_path, clock, lambda _c: BEC)
    row = conn.execute("SELECT digest FROM eval_runs").fetchone()
    assert row["digest"].startswith("eval-pins-")
    assert gate.synthetic(conn, claude_pins.key(claude_pins.pins(conn, "C")), "C").ok is False


def test_a_model_id_must_be_claudes(db_path: Path, clock: FakeClock, root: Path) -> None:
    with pytest.raises(InvalidInputError):
        start(db_path, clock, root, classifier_model="gpt-5")
    with pytest.raises(InvalidInputError):
        start(db_path, clock, root, preset="B", classifier_model="claude-opus-5-5")
    with pytest.raises(InvalidInputError):
        start(db_path, clock, root, batch=6)


def test_high_runs_one_case_per_spawn_unless_batched(
    db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root, sensitivity="high", batch=2)
    items = claude_eval.eval_next(connector(db_path), clock, S1)["items"]
    assert {i["agent"] for i in items} == {"ecf:classifier-high"}
    assert [i["spawn"] for i in items] == ["classifier-high-1", "classifier-high-1",
                                           "classifier-high-2"]  # fmt: skip


def _a_run(conn: sqlite3.Connection, clock: FakeClock, db_path: Path, root: Path,
           got: dict[str, Any]) -> str:  # fmt: skip
    """A saved `ecf eval run` result on the pinned digest, with Gemma's classifications."""
    cases, version = evalrun.load(root, fraud_only=False)
    results = [CaseResult(id=c.id, correct=False, got=got | {"rule": None}) for c in cases]
    summary = evalrun.summarize(results, 0)
    rf = ResultFile(run_id="a" * 32, pair="gemma4-12b/local", set_version=version,
                    created_at=to_ts(clock.now()), cases=results,
                    digest=ollama.load_pin().digest, summary=summary)  # fmt: skip
    evalrun._save(conn, clock, db_path.parent, rf)  # pyright: ignore[reportPrivateUsage]
    return rf.run_id


def test_preset_b_acts_on_gemmas_classifications_from_the_last_complete_run(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    with pytest.raises(InvalidInputError, match="ecf eval run"):
        start(db_path, clock, root, preset="B")
    source = _a_run(conn, clock, db_path, root, REQUEST)
    r = start(db_path, clock, root, preset="B")
    assert r["models"].keys() == {"actor", "actor_high"}
    handed = drain(db_path, clock, lambda _c: REQUEST)
    assert handed and {i["need"] for i in handed} == {"act"}  # no Claude classification
    m = json.loads(conn.execute("SELECT metrics FROM eval_runs WHERE pair ="
                                " 'gemma4-12b/claude'").fetchone()[0])  # fmt: skip
    assert m["claude"]["source_run"] == source and m["determinism_diffs"] == 0


# ---- one at a time, stop and expiry ----------------------------------------------------------


def test_one_run_at_a_time_and_stop_saves_what_was_scored(
    conn: sqlite3.Connection, db_path: Path, clock: FakeClock, root: Path
) -> None:
    start(db_path, clock, root)
    with pytest.raises(ConflictError):
        start(db_path, clock, root)
    item = claude_eval.eval_next(connector(db_path), clock, S1, limit=1)["items"][0]
    answer(db_path, clock, item, S1, BEC)
    s = claude_eval.stop(connector(db_path), clock)
    assert s is not None and s["state"] == "stopped" and s["done"] == 1
    m = json.loads(conn.execute("SELECT metrics FROM eval_runs").fetchone()[0])
    assert m["complete"] is False and m["gate_passed"] is False
    assert claude_eval.eval_next(connector(db_path), clock, S1)["done"] is True
    assert claude_eval.session_info(connector(db_path), clock) is None
    start(db_path, clock, root)  # the next one may register


def test_a_run_expires_after_24_hours(db_path: Path, clock: FakeClock, root: Path) -> None:
    start(db_path, clock, root)
    clock.advance(24 * 3600)
    st = claude_eval.status(connector(db_path), clock)
    assert st is not None and st["state"] == "expired"
    assert claude_eval.results()["state"] == "expired"
    assert claude_eval.eval_next(connector(db_path), clock, S1)["done"] is True
    start(db_path, clock, root)


def test_eval_results_are_metrics_only(db_path: Path, clock: FakeClock, root: Path) -> None:
    with pytest.raises(NotFoundError):
        claude_eval.results()
    start(db_path, clock, root)
    drain(db_path, clock, lambda _c: BEC)
    r = claude_eval.results()
    assert r["state"] == "done" and isinstance(r["metrics"]["unsafe"], int)
    assert not any(c in json.dumps(r) for c in CASES)


# ---- the routes ------------------------------------------------------------------------------


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
            sess = (await h.post("/v1/sessions", headers=cli)).json()
            out["session"] = sess
            work = {"Authorization": f"Bearer {sess['profile_token']}"}
            out["cli_next"] = (await h.post("/v1/eval/next", json={}, headers=cli)).status_code
            q = (await h.post("/v1/eval/next", json={"limit": 1}, headers=work)).json()
            item = q["items"][0]
            model = claude_pins.effective(conn)["classifier"]
            state.telemetry.add(sess["session_id"], [ApiCall(1, model, SUBAGENT)],
                                {"toolu_r": 2, "toolu_s": 3})  # fmt: skip
            ref, tok = item["id"], item["claim_token"]
            out["message"] = (await h.post(f"/v1/claims/{ref}/message", headers=work, json={
                "claim_token": tok, "tool_use_id": "toolu_r"})).json()  # fmt: skip
            out["submit"] = (await h.post(f"/v1/claims/{ref}/classification", headers=work,
                                          json={"claim_token": tok, "classification": BEC,
                                                "tool_use_id": "toolu_s"})).json()  # fmt: skip
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
    """Held like `/ecf-review`'s (OD-268), and settled by the same paths, by kind."""
    from ecf_server import api, telemetry_app  # noqa: PLC0415

    state = ServiceState(install="t", token="cli-token", started_at="2026-10-02T09:00:00Z",
                         clock=clock, db_path=db_path)  # fmt: skip
    state.telemetry_wait_s = 0
    start(db_path, clock, root)
    bearer = state.telemetry.open(S1)
    assert bearer
    items = claude_eval.eval_next(connector(db_path), clock, S1, limit=2)["items"]
    holds = [claude_eval.record_classification(clock, S1, i["id"], i["claim_token"], BEC,
                                               f"toolu_{n}")
             for n, i in enumerate(items)]  # fmt: skip
    for h in holds:
        assert isinstance(h, Hold) and h.kind == "eval" and state.telemetry.hold(h)
    model = claude_pins.effective(conn)["classifier"]
    state.telemetry.add(S1, [ApiCall(1, model, SUBAGENT)], {"toolu_0": 2})  # only the first
    telemetry_app.settle_bound(state, S1)
    run = claude_eval.current()
    assert run is not None
    first, second = (run.by_ref[i["id"]] for i in items)
    assert first.classification == BEC and first.claim == "free"
    assert second.claim == "held"
    api._end_session(state, S1)  # pyright: ignore[reportPrivateUsage]
    assert second.claim == "free" and second.classification is None  # refused as unbound


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
