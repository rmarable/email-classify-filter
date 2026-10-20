"""The Mistral grader (eval generate plan, Adapters and Cost guards; build step 3): the key
(`ecf models api-key set --provider mistral`), the service's call (`POST /v1/eval/mistral`, which
reads the email from a generated set itself), and the client's checks, cost cap and pacing.
Mistral is an `httpx.MockTransport`: no network, no real key."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

import ecf.eval.mistral_api
from ecf.cli import app
from ecf.eval import grade as grading
from ecf.eval import mistral_api, models, tools
from ecf.eval.results import load_result
from ecf.paths import paths_for
from ecf_server import mistral
from ecf_server.secretstore.memory import MemorySecretStore
from tests.test_api_keys import Env, Fake
from tests.test_eval_generate import LABELS, run

KEY = "mistral-test-key-CANARY-91c2"  # invented
OK = {"refused": False, "labels": LABELS}


def _reply(answer: Any = OK, *, finish: str = "stop", tool_calls: Any = None,
           usage: dict[str, int] | None = None) -> dict[str, Any]:  # fmt: skip
    return {"id": "x", "object": "chat.completion", "model": "mistral-large-4-0",
            "usage": usage or {"prompt_tokens": 3000, "completion_tokens": 400,
                               "total_tokens": 3400},
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "tool_calls": tool_calls,
                                     "content": json.dumps(answer)}}]}  # fmt: skip


class Mistral:
    """A fake Mistral: answers from a queue of (status, body, headers), records requests."""

    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []
        self.queue: list[tuple[int, Any, dict[str, str]]] = []

    def __call__(self, r: httpx.Request) -> httpx.Response:
        self.seen.append(r)
        if r.url.path == "/v1/models":
            ok = r.headers.get("authorization") == f"Bearer {KEY}"
            return httpx.Response(200 if ok else 401, json={"data": []})
        status, doc, headers = self.queue.pop(0) if self.queue else (200, _reply(), {})
        return httpx.Response(status, json=doc, headers=headers)


@pytest.fixture
def env(conn: sqlite3.Connection, db_path: Path) -> Env:
    del conn  # migrated
    return Env(db_path)


@pytest.fixture
def fake(env: Env) -> Mistral:
    m = Mistral()
    env.state.mistral_transport = httpx.MockTransport(m)
    return m


# The key: checked by one model-list call, stored only by the service, audited without the key,
# a Security Notice on set, replace and clear.


def test_the_key_is_checked_stored_replaced_and_cleared(
    env: Env, fake: Mistral, conn: sqlite3.Connection
) -> None:
    r = env.post("/v1/models/keys/mistral", {"key": f" {KEY}\n"})
    assert r.status_code == 200 and r.json() == {"provider": "mistral", "stored": True,
                                                 "replaced": False}  # fmt: skip
    assert env.store.get(mistral.KEY_SECRET) == KEY
    assert fake.seen[0].url == "https://api.mistral.ai/v1/models"
    assert env.post("/v1/models/keys/mistral", {"key": KEY}).json()["replaced"] is True
    assert env.post("/v1/models/keys/mistral", {"clear": True}).status_code == 200
    assert env.store.get(mistral.KEY_SECRET) is None
    assert env.audit(conn) == [("models.mistral_key_set", {"replaced": False}),
                               ("models.mistral_key_set", {"replaced": True}),
                               ("models.mistral_key_cleared", {"had": True})]  # fmt: skip
    notices = env.notices()
    assert len(notices) == 3 and "Mistral API key was set" in notices[0]
    assert all(KEY not in n for n in notices)


def test_a_key_mistral_refuses_is_not_stored(env: Env, fake: Mistral) -> None:
    r = env.post("/v1/models/keys/mistral", {"key": "wrong-key"})
    assert r.status_code == 400 and "the key was refused (HTTP 401)" in r.text
    assert env.store.get(mistral.KEY_SECRET) is None and env.notices() == []
    assert len(fake.seen) == 1


# The service's call: only a generated PROD set's case, the email read and the prompt built by
# the service, a strict JSON schema and no tools.


def _prod_set(tmp_path: Path) -> Path:
    _summary, root = run(tmp_path, mode="prod")
    return root.resolve()


def _ask(env: Env, root: Path, case: str, model: str = "mistral-large-4-0") -> httpx.Response:
    return env.post("/v1/eval/mistral", {"root": str(root), "case": case, "model": model})


def test_the_service_grades_one_case_of_a_generated_set(env: Env, fake: Mistral,
                                                        tmp_path: Path) -> None:  # fmt: skip
    env.store.set(mistral.KEY_SECRET, KEY)
    root = _prod_set(tmp_path)
    row = json.loads((root / "labels.jsonl").read_text().splitlines()[0])
    r = _ask(env, root, row["id"])
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok", "answer": OK, "detail": "",
                        "usage": {"prompt_tokens": 3000, "completion_tokens": 400}}  # fmt: skip
    sent = fake.seen[-1]
    assert sent.url == "https://api.mistral.ai/v1/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(sent.content)
    assert body["model"] == "mistral-large-4-0" and "tools" not in body
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == grading.grade_schema()
    email = (root / row["file"]).read_bytes().decode()
    assert body["messages"][1]["content"] == grading.grade_prompt(email)
    assert body["messages"][0]["content"] == mistral.SYSTEM_PROMPT


def test_the_service_refuses_what_isnt_a_generated_prod_set(env: Env, fake: Mistral,
                                                            tmp_path: Path) -> None:  # fmt: skip
    env.store.set(mistral.KEY_SECRET, KEY)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert "isn't a generated set" in _ask(env, plain, "x").text
    assert "absolute path" in _ask(env, Path("rel"), "x").text
    (tmp_path / "d").mkdir()
    _summary, dev = run(tmp_path / "d", mode="dev")
    row = json.loads((dev / "labels.jsonl").read_text().splitlines()[0])
    assert "for prod sets only" in _ask(env, dev.resolve(), row["id"]).text
    root = _prod_set(tmp_path)
    assert "no such case" in _ask(env, root, "nope").text
    assert "isn't on ecf's model list" in _ask(env, root, row["id"], "mistral-small").text
    assert "Z.ai" in _ask(env, root, row["id"], "glm-5.3").text
    assert fake.seen == []  # nothing sent


def test_the_service_needs_the_key(env: Env, fake: Mistral, tmp_path: Path) -> None:
    root = _prod_set(tmp_path)
    row = json.loads((root / "labels.jsonl").read_text().splitlines()[0])
    assert "ecf models api-key set --provider mistral" in _ask(env, root, row["id"]).text
    assert fake.seen == []


@pytest.mark.parametrize(("status", "doc", "want"), [
    (429, {}, ("rate_limit", "HTTP 429")),
    (401, {}, ("auth", "key refused (HTTP 401)")),
    (503, {}, ("provider", "HTTP 503")),
    (422, {}, ("failed", "HTTP 422")),
    (200, _reply(finish="length"), ("failed", "finish length")),
    (200, _reply(tool_calls=[{"id": "t"}]), ("failed", "tool call")),
    (200, {"choices": []}, ("failed", "no choice")),
])  # fmt: skip
def test_each_kind_of_failure_is_named_never_quoted(status: int, doc: Any,
                                                    want: tuple[str, str]) -> None:  # fmt: skip
    store = MemorySecretStore()
    store.set(mistral.KEY_SECRET, KEY)
    m = Mistral()
    m.queue.append((status, doc, {"retry-after": "7"}))
    got = mistral.complete(store, "mistral-large-4-0", "p", {}, httpx.MockTransport(m))
    assert (got["status"], got["detail"]) == want
    if status == 429:
        assert got["retry_after"] == 7.0


# The client: the answer checked against the schema, the cost added up and capped, 429s paced
# for up to 5 minutes, a refused key a stop.


@pytest.fixture
def client(env: Env, monkeypatch: pytest.MonkeyPatch) -> Fake:
    f = Fake(env.state)
    monkeypatch.setattr(ecf.eval.mistral_api, "LocalClient", f)
    env.store.set(mistral.KEY_SECRET, KEY)
    return f


def _adapter(**kw: Any) -> mistral_api.MistralAdapter:
    return mistral_api.MistralAdapter("mistral-large-4-0", paths_for("ecf-test-mistral"), **kw)


@pytest.mark.usefixtures("client")
def test_grading_a_set_through_the_service(fake: Mistral, tmp_path: Path) -> None:
    root = _prod_set(tmp_path)
    adapter = _adapter()
    path, counts, _detail = grading.Grading(root, adapter).run(resume=False)
    assert counts == {"graded": 20} and path is not None
    g = load_result(path)
    assert g.pair == "grader:mistral-large-4-0" and all(c.got == LABELS for c in g.cases)
    summary: dict[str, Any] = g.summary or {}
    assert summary["grader"]["via"] == "ecf service"
    per_call = (3000 * 1.36 + 400 * 4.18) / 1e6
    assert adapter.spent == pytest.approx(20 * per_call)
    assert len(fake.seen) == 20


@pytest.mark.usefixtures("client")
def test_the_cost_cap_stops_before_a_call_could_pass_it(fake: Mistral, tmp_path: Path) -> None:
    root = _prod_set(tmp_path)
    first = _adapter().cost({"prompt_tokens": 20_000, "completion_tokens": 2_048})
    adapter = _adapter(max_cost=first * 1.1)  # room for one call priced as the first estimate
    row = json.loads((root / "labels.jsonl").read_text().splitlines()[0])
    assert adapter.ask_case(root, row["id"], grading.grade_schema()).status == "ok"
    reply = adapter.ask_case(root, row["id"], grading.grade_schema())
    assert reply.status == "limit" and "--max-cost" in reply.detail
    assert len(fake.seen) == 1
    assert tools.Breaker().note(reply)  # a stop: the run ends and can --resume


@pytest.mark.usefixtures("client")
def test_429s_are_paced_then_stop_after_five_minutes(fake: Mistral, tmp_path: Path) -> None:
    root = _prod_set(tmp_path)
    row = json.loads((root / "labels.jsonl").read_text().splitlines()[0])
    slept: list[float] = []
    adapter = _adapter(sleep=slept.append)
    fake.queue += [(429, {}, {}), (429, {}, {"retry-after": "30"}), (200, _reply(), {})]
    assert adapter.ask_case(root, row["id"], grading.grade_schema()).status == "ok"
    assert slept == [5.0, 30.0]
    fake.queue += [(429, {}, {"retry-after": "200"})] * 3
    reply = adapter.ask_case(root, row["id"], grading.grade_schema())
    assert (reply.status, reply.detail) == ("limit", "rate limited for 5 minutes")
    assert slept[2:] == [200.0]


@pytest.mark.usefixtures("client")
def test_a_misfit_answer_fails_and_a_refused_key_stops(fake: Mistral, tmp_path: Path) -> None:
    root = _prod_set(tmp_path)
    row = json.loads((root / "labels.jsonl").read_text().splitlines()[0])
    adapter = _adapter()
    fake.queue.append((200, _reply({"refused": False}), {}))
    reply = adapter.ask_case(root, row["id"], grading.grade_schema())
    assert (reply.status, reply.detail) == ("failed", "schema misfit")
    fake.queue.append((401, {}, {}))
    reply = adapter.ask_case(root, row["id"], grading.grade_schema())
    assert reply.status == "limit" and "api-key set --provider mistral" in reply.detail


@pytest.mark.usefixtures("client")
def test_the_grade_command_estimates_caps_and_checks_the_families(
    fake: Mistral, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prod_set(tmp_path)
    monkeypatch.setenv("ECF_HOME", str(tmp_path / "home"))
    families = {"fake-model-1": "openai", "mistral-large-4-0": "mistral"}

    def family(_provider: str, model: str) -> str:
        return families[model]

    monkeypatch.setattr(models, "family", family)
    args = ["eval", "grade", "--root", str(root), "--grader", "mistral:mistral-large-4-0", "--yes"]
    r = CliRunner().invoke(app, [*args, "--max-cost", "0"])
    assert "--max-cost must be more than 0" in str(r.exception) and fake.seen == []
    families["fake-model-1"] = "mistral"
    r = CliRunner().invoke(app, args)
    assert "both mistral" in str(r.exception) and fake.seen == []
    families["fake-model-1"] = "openai"
    r = CliRunner().invoke(app, [*args, "--max-cost", "2"])
    assert r.exit_code == 0, r.output
    assert "20 calls to mistral-large-4-0: at most about $" in r.output
    assert "stops before passing $2.00 (--max-cost)" in r.output and "graded 20" in r.output
