"""The Ollama adapter (V1.3 step 1a): pin, readiness checks, HTTP client, metrics (SPEC §7.5,
§12.2, §12.4, §13.4; I5, I6; OD-235, OD-240, OD-242, OD-245)."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from ecf_server import ollama, retention
from ecf_server.clock import FakeClock
from ecf_server.ollama import Client, Listener, OllamaError, Pin

PIN = Pin(tag="gemma4:12b", digest="a" * 64, ecf_name="ecf/gemma4-12b")
LSOF_OK = "p4242\nf4\nn127.0.0.1:11434\n"
PS_ENV = "/opt/homebrew/bin/ollama serve HOME=/Users/me PATH=/usr/bin OLLAMA_NUM_PARALLEL=1"


def _runner(outputs: dict[str, str | Exception]) -> Callable[[list[str]], str]:
    def run(cmd: list[str]) -> str:
        key = "lsof" if "lsof" in cmd[0] else "ss" if cmd[0].endswith("ss") else "ps"
        out = outputs[key]
        if isinstance(out, Exception):
            raise out
        return out

    return run


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> Client:
    return Client(transport=httpx.MockTransport(handler))


def _server(
    digest: str | None = "a" * 64, **extra: Any
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.35.0"})
        if req.url.path == "/api/tags":
            models = [{"name": "gemma4:12b", "digest": "a" * 64}]
            if digest:
                models.append({"name": PIN.ecf_tag, "digest": digest})
            return httpx.Response(200, json={"models": models})
        return extra["chat"](req)

    return handler


# ---- pin --------------------------------------------------------------------------------------


def test_the_shipped_pin_is_the_measured_model() -> None:
    pin = ollama.load_pin()
    assert pin.tag == "gemma4:12b"
    assert pin.digest == "6114515d63c17436a7c0417d82820ac65ad643e2806c5a3c89cb62846436ed0b"
    assert pin.ecf_tag.startswith("ecf/gemma4-12b:")


# ---- the listener (OD-240, OD-242) ----------------------------------------------------------


def test_listener_on_loopback_only_passes() -> None:
    got = ollama.find_listener(_runner({"lsof": LSOF_OK}), "darwin")
    assert got == Listener(("127.0.0.1:11434",), 4242)
    ollama.check_listener(got)  # pyright: ignore[reportArgumentType] - asserted above


@pytest.mark.parametrize("addr", ["*:11434", "0.0.0.0:11434", "192.168.1.5:11434", "[::1]:11434"])
def test_any_other_listener_is_a_fault(addr: str) -> None:
    got = ollama.find_listener(_runner({"lsof": f"p1\nn127.0.0.1:11434\nn{addr}\n"}), "darwin")
    assert got is not None
    with pytest.raises(OllamaError) as ei:
        ollama.check_listener(got)
    assert ei.value.cause == "not_loopback"


def test_nobody_listening_is_not_running() -> None:
    nothing = subprocess.CalledProcessError(1, ["lsof"], output="", stderr="")
    assert ollama.find_listener(_runner({"lsof": nothing}), "darwin") is None


CANNOT_RUN = [FileNotFoundError("lsof"), subprocess.CalledProcessError(2, ["lsof"]),
              subprocess.TimeoutExpired(["lsof"], 10)]  # fmt: skip


@pytest.mark.parametrize("err", CANNOT_RUN)
def test_a_check_that_cannot_run_is_a_loud_fault(err: Exception) -> None:
    with pytest.raises(OllamaError) as ei:
        ollama.find_listener(_runner({"lsof": err}), "darwin")
    assert ei.value.cause == "unconfirmed"


def test_linux_ss_output_is_parsed() -> None:
    out = 'LISTEN 0 4096 127.0.0.1:11434 0.0.0.0:* users:(("ollama",pid=77,fd=3))\n'
    assert ollama.find_listener(_runner({"ss": out}), "linux") == Listener(("127.0.0.1:11434",), 77)


# ---- the server's environment (OD-245) --------------------------------------------------------


def test_the_server_environment_is_read() -> None:
    env = ollama.server_env(4242, _runner({"ps": PS_ENV}), "darwin")
    assert env == {"OLLAMA_NUM_PARALLEL": "1"}
    ollama.check_env(env)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_request_logging_is_a_fault(value: str) -> None:
    with pytest.raises(OllamaError) as ei:
        ollama.check_env({"OLLAMA_DEBUG_LOG_REQUESTS": value})
    assert ei.value.cause == "logs_requests"


@pytest.mark.parametrize("value", ["", "0", "false"])
def test_request_logging_off_passes(value: str) -> None:
    ollama.check_env({"OLLAMA_DEBUG_LOG_REQUESTS": value})


def test_an_invisible_environment_is_unconfirmed() -> None:
    with pytest.raises(OllamaError) as ei:  # another user's process: ps shows the command only
        ollama.server_env(4242, _runner({"ps": "/opt/homebrew/bin/ollama serve"}), "darwin")
    assert ei.value.cause == "unconfirmed"


# ---- readiness (I6) ---------------------------------------------------------------------------


def _ready(client: Client, lsof: str | Exception = LSOF_OK, ps: str = PS_ENV) -> ollama.Ready:
    return ollama.readiness(client, PIN, run=_runner({"lsof": lsof, "ps": ps}), platform="darwin")


def test_ready_when_every_check_holds() -> None:
    got = _ready(_client(_server()))
    assert (got.version, got.digest) == ("0.35.0", "a" * 64)


@pytest.mark.parametrize(
    ("kwargs", "cause"),
    [
        ({"lsof": subprocess.CalledProcessError(1, ["lsof"], output="")}, "not_running"),
        ({"lsof": "p1\nn*:11434\n"}, "not_loopback"),
        ({"lsof": "n127.0.0.1:11434\n"}, "unconfirmed"),  # no pid
        ({"ps": PS_ENV + " OLLAMA_DEBUG_LOG_REQUESTS=1"}, "logs_requests"),
    ],
)
def test_readiness_names_the_first_fault(kwargs: dict[str, Any], cause: str) -> None:
    with pytest.raises(OllamaError) as ei:
        _ready(_client(_server()), **kwargs)
    assert ei.value.cause == cause


def test_a_missing_or_different_model_is_refused() -> None:
    with pytest.raises(OllamaError) as ei:
        _ready(_client(_server(digest=None)))
    assert ei.value.cause == "model_missing"
    with pytest.raises(OllamaError) as ei:
        _ready(_client(_server(digest="b" * 64)))
    assert ei.value.cause == "digest_mismatch"
    assert "b" * 12 in str(ei.value) and "a" * 12 in str(ei.value)


def test_a_server_that_does_not_answer_is_not_running() -> None:
    def refuse(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(OllamaError) as ei:
        _ready(_client(refuse))
    assert ei.value.cause == "not_running"


# ---- chat ---------------------------------------------------------------------------------------


def test_chat_sends_the_fixed_options_and_returns_metrics() -> None:
    seen: list[dict[str, Any]] = []

    def chat(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={
            "message": {"role": "assistant", "content": '{"category": "invoice"}'},
            "done_reason": "stop", "prompt_eval_count": 588, "prompt_eval_cached_count": 519,
            "eval_count": 57, "prompt_eval_duration": 457_379_000, "eval_duration": 1_899_440_000,
            "load_duration": 1_779_167, "total_duration": 2_361_520_750})  # fmt: skip

    reply = _client(_server(chat=chat)).chat(PIN.ecf_tag, "SYSTEM", "USER", role="classifier",
                                             fmt={"type": "object"})  # fmt: skip
    body = seen[0]
    assert body["options"] == {"num_ctx": 4096, "temperature": 0, "num_predict": 160}
    assert (body["think"], body["stream"], body["keep_alive"]) == (False, False, "5m")
    assert body["format"] == {"type": "object"}
    assert reply.content == '{"category": "invoice"}'
    assert (reply.metrics.prompt_tokens, reply.metrics.cached_tokens) == (588, 519)
    assert reply.metrics.generation_tps == pytest.approx(30.0, rel=0.01)


def test_one_retry_on_timeout_then_a_timeout_fault() -> None:
    calls: list[int] = []

    def slow(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(OllamaError) as ei:
        _client(_server(chat=slow)).chat(PIN.ecf_tag, "S", "U", role="classifier")
    assert ei.value.cause == "timeout" and len(calls) == 2


def test_errors_never_carry_more_than_a_short_error_field() -> None:
    secret = "Dear customer, wire $40,000 to account 12345678 " * 20

    def echo(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": f"bad request: {secret}"})

    with pytest.raises(OllamaError) as ei:
        _client(_server(chat=echo)).chat(PIN.ecf_tag, "S", secret, role="classifier")
    assert len(str(ei.value)) < 120 and secret not in str(ei.value)


def test_the_client_ignores_proxy_and_host_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://10.0.0.9:11434")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8080")
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(200, json={"version": "0.35.0"})

    _client(handler).version()
    assert seen == ["http://127.0.0.1:11434/api/version"]


# ---- metrics (I5) -------------------------------------------------------------------------------


def test_model_calls_hold_numbers_and_codes_only(conn: sqlite3.Connection) -> None:
    cols = conn.execute("SELECT name, type FROM pragma_table_info('model_calls')").fetchall()
    text_cols = {c[0] for c in cols if c[1] == "TEXT"}
    # every TEXT column is an identifier, a timestamp, a fixed code or the digest: none can carry
    # message or model text
    assert text_cols == {"ts", "address_id", "preset", "stage", "role", "outcome", "digest"}
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO model_calls (ts, role, outcome, digest)"
                     " VALUES ('t', 'classifier', 'the email said...', 'd')")  # fmt: skip


def test_model_calls_are_recorded_and_pruned(conn: sqlite3.Connection, clock: FakeClock) -> None:
    m = ollama.Metrics(588, 519, 57, 457, 1899, 1, 2361)
    ollama.record_call(conn, clock, role="classifier", outcome="ok", digest="a" * 64, metrics=m,
                       address_id="ap", preset="A", stage="shadow")  # fmt: skip
    row = conn.execute(
        "SELECT prompt_tokens, cached_tokens, output_tokens FROM model_calls"
    ).fetchone()
    assert tuple(row) == (588, 519, 57)
    clock.advance(400 * 86400)
    assert retention.run(conn, clock)["model_calls"] == 1
