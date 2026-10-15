"""`ecf models api-key set|clear --provider anthropic|typesafe` (v3.0.0 step 2a; planning-docs/
JEV-STEP2.md §3, §8; OD-503): the per-provider routes, the removed old route, the CLI's checks and
version check, the 5 s validation, audit and Security Notices. No real key or call: TypeSafe and
Anthropic are `httpx.MockTransport`s."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx
import pytest
from typer.testing import CliRunner

import ecf.cli_models
from ecf import __version__, api_keys
from ecf.cli import app
from ecf.client import parse_reply
from ecf.errors import ConflictError, InvalidInputError, NotFoundError
from ecf.paths import Paths
from ecf.prompts import NO_TERMINAL
from ecf_server import jev, model_watch
from ecf_server.api import Caller, ServiceState, Session
from ecf_server.notify import FakeNotifier
from ecf_server.secretstore.memory import MemorySecretStore
from tests.test_addresses import TOKEN, call, make_state

TS_KEY = "ts-test-key-CANARY-5d1e"  # invented
ANT_KEY = "sk-ant-test-CANARY-77aa"  # invented
PIN = jev.load_pin()
Handler = Callable[[httpx.Request], httpx.Response]


def _ts_ok(seen: list[httpx.Request]) -> Handler:
    def go(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        reply = {"model": PIN, "answers": {"check": {"noul": 0.9}},
                 "usage": {"input_tokens": 30, "output_tokens": 1}}  # fmt: skip
        return httpx.Response(200, json=reply)

    return go


def _ant_ok(seen: list[httpx.Request]) -> Handler:
    def go(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        listed = [{"id": "claude-x", "created_at": "2026-10-01T00:00:00Z"}]
        return httpx.Response(200, json={"data": listed, "has_more": False})

    return go


class Env:
    def __init__(self, db_path: Path) -> None:
        self.store = MemorySecretStore()
        self.state: ServiceState = make_state(db_path, self.store)
        self.notifier = FakeNotifier()
        self.state.notifier = self.notifier
        self.ts_seen: list[httpx.Request] = []
        self.ant_seen: list[httpx.Request] = []
        self.state.jev_transport = httpx.MockTransport(_ts_ok(self.ts_seen))
        ant = httpx.MockTransport(_ant_ok(self.ant_seen))
        self.state.watch_http = lambda: httpx.Client(transport=ant)

    def post(self, path: str, body: Any, token: str = TOKEN) -> httpx.Response:
        return call(self.state, "POST", path, body, token)

    def notices(self) -> list[str]:
        return [b for t, b in self.notifier.sent if t == "[ecf-alert] Security Notice"]

    def audit(self, conn: sqlite3.Connection) -> list[tuple[str, dict[str, Any]]]:
        return [(r[0], json.loads(r[1])) for r in conn.execute(
            "SELECT event, data FROM audit WHERE event LIKE 'models.%' ORDER BY id")]  # fmt: skip


@pytest.fixture
def env(conn: sqlite3.Connection, db_path: Path) -> Env:
    del conn  # migrated
    return Env(db_path)


# The key checks shared by the CLI and the service: a fixed list of providers, malformed keys
# refused, surrounding whitespace stripped.


def test_providers_are_a_fixed_list() -> None:
    assert api_keys.PROVIDERS == ("anthropic", "typesafe")
    for bad in (None, "", "openai", "Anthropic"):
        with pytest.raises(InvalidInputError) as err:
            api_keys.check_provider(bad)
        detail = err.value.detail
        assert "--provider anthropic" in detail and "--provider typesafe" in detail


@pytest.mark.parametrize(("provider", "key", "problem"), [
    ("anthropic", "abc-123", "starts sk-ant-"),
    ("typesafe", "sk-ant-abc", "Anthropic key"),
    ("typesafe", "ab c", "no spaces"),
    ("typesafe", "abc\x07def", "control or non-ASCII"),
    ("typesafe", "abcdé", "control or non-ASCII"),
    ("typesafe", "x" * 401, "longer than 400"),
    ("typesafe", "   ", "nothing entered"),
])  # fmt: skip
def test_bad_keys_are_refused(provider: str, key: str, problem: str) -> None:
    with pytest.raises(InvalidInputError) as err:
        api_keys.check_key(provider, key)
    assert problem in err.value.detail
    assert not key.strip() or key.strip() not in err.value.detail  # never quoted


def test_ends_are_stripped() -> None:
    assert api_keys.check_key("typesafe", f"  {TS_KEY}\n") == TS_KEY
    assert api_keys.check_key("anthropic", ANT_KEY) == ANT_KEY


# The key routes: set, replace and clear for each provider, a key the provider refuses is not
# stored, the old route is gone, and only the CLI (never MCP) can reach them.


def test_typesafe_set_replace_clear(env: Env, conn: sqlite3.Connection) -> None:
    r = env.post("/v1/models/keys/typesafe", {"key": f" {TS_KEY}\n"})
    assert r.status_code == 200, r.text
    assert r.json() == {"provider": "typesafe", "stored": True, "replaced": False}
    assert env.store.get("jev-api-key") == TS_KEY
    [req] = env.ts_seen  # one minimal call, no email content, 5 s
    assert json.loads(req.content)["state"] == jev.KEY_CHECK_STATE
    assert req.extensions["timeout"]["read"] == 5.0
    assert env.post("/v1/models/keys/typesafe", {"key": TS_KEY}).json()["replaced"] is True
    assert env.post("/v1/models/keys/typesafe", {"clear": True}).status_code == 200
    assert env.store.get("jev-api-key") is None
    env.post("/v1/models/keys/typesafe", {"clear": True})  # nothing there: no notice
    notices = env.notices()
    assert [n.split(" (install")[0] for n in notices] == [
        "The TypeSafe API key was set", "The TypeSafe API key was replaced",
        "The TypeSafe API key was removed"]  # fmt: skip
    assert "(install `t`); a running Jev eval stops." in notices[2]
    assert all("ecf models api-key clear --provider typesafe" in n for n in notices)
    assert env.audit(conn) == [("models.jev_key_set", {"replaced": False}),
                               ("models.jev_key_set", {"replaced": True}),
                               ("models.jev_key_cleared", {"had": True}),
                               ("models.jev_key_cleared", {"had": False})]  # fmt: skip
    assert TS_KEY not in json.dumps(env.audit(conn)) + repr(env.notifier.sent)


def test_anthropic_set_and_clear_as_today_plus_notices(env: Env, conn: sqlite3.Connection) -> None:
    r = env.post("/v1/models/keys/anthropic", {"key": ANT_KEY})
    assert r.status_code == 200, r.text
    assert r.json()["listed"] == 1 and r.json()["api_key"] is True
    assert env.store.get(model_watch.KEY_SECRET) == ANT_KEY
    assert env.ant_seen[0].extensions["timeout"]["read"] == 5.0
    env.post("/v1/models/keys/anthropic", {"key": ANT_KEY})
    env.post("/v1/models/keys/anthropic", {"clear": True})
    assert env.store.get(model_watch.KEY_SECRET) is None
    assert [n.split(" (install")[0] for n in env.notices()] == [
        "The Anthropic Models API key was set", "The Anthropic Models API key was replaced",
        "The Anthropic Models API key was removed"]  # fmt: skip
    assert env.audit(conn)[:2] == [
        ("models.api_key_set", {"listed": 1, "replaced": False}),
        ("models.api_key_set", {"listed": 1, "replaced": True}),
    ]
    assert ANT_KEY not in json.dumps(env.audit(conn)) + repr(env.notifier.sent)


def test_a_key_typesafe_refuses_is_not_stored(env: Env) -> None:
    env.state.jev_transport = httpx.MockTransport(lambda _r: httpx.Response(401, json={}))
    r = env.post("/v1/models/keys/typesafe", {"key": TS_KEY})
    assert r.status_code == 400 and "the key was refused (HTTP 401)" in r.text
    assert env.store.get("jev-api-key") is None and env.notices() == []
    assert TS_KEY not in r.text


def test_the_service_checks_the_prefix_before_any_call(env: Env) -> None:
    r = env.post("/v1/models/keys/typesafe", {"key": ANT_KEY})
    assert r.status_code == 400 and "Anthropic key" in r.text
    r = env.post("/v1/models/keys/anthropic", {"key": TS_KEY})
    assert r.status_code == 400 and "starts sk-ant-" in r.text
    assert env.ts_seen == [] and env.ant_seen == [] and env.store.get("jev-api-key") is None


def test_the_old_route_is_gone(env: Env) -> None:
    r = env.post("/v1/models/api-key", {"key": ANT_KEY})
    assert r.status_code == 404 and env.ant_seen == []
    assert env.store.get(model_watch.KEY_SECRET) is None


def test_the_routes_are_cli_only(env: Env) -> None:
    env.state.sessions["s1"] = Session("s1", "work-token", Caller.WORK, "2026-10-01T12:00:00Z",
                                       agent_token="agent-token")  # fmt: skip
    for token in ("work-token", "agent-token"):
        for provider in api_keys.PROVIDERS:
            r = env.post(f"/v1/models/keys/{provider}", {"clear": True}, token=token)
            assert r.status_code == 403, (token, provider)
    assert env.post("/v1/models/keys/typesafe", {"clear": True}, token="").status_code == 401


def _mcp_paths() -> set[str]:
    """Every service path the MCP server's source names."""
    src = (Path(ecf.cli_models.__file__).parent / "mcp_server.py").read_text("utf-8")
    return {p for p in ("/v1/models/keys", "/v1/models/api-key") if p in src}


def test_mcp_has_no_key_tool() -> None:
    assert _mcp_paths() == set()


# The `ecf` key commands: they set and clear each provider, check the key and the service's
# version before sending anything, and need a terminal.


class Fake:
    """LocalClient over the ASGI app, recording what the CLI sends."""

    def __init__(self, st: ServiceState, version: str = __version__) -> None:
        self.st, self.version = st, version
        self.sent: list[tuple[str, str, Any]] = []

    def __call__(self, _p: Paths) -> Self:
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: type[BaseException] | BaseException | TracebackType | None) -> None:
        return None

    def request(self, method: str, path: str, json: Any = None, **_: Any) -> Any:
        self.sent.append((method, path, json))
        if path == "/v1/status":
            return {"version": self.version, "api_version": "1"}
        return parse_reply(call(self.st, method, path, json, TOKEN))

    def get(self, path: str, **_: Any) -> Any:
        return self.request("GET", path)


@pytest.fixture
def cli(env: Env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Fake:
    fake = Fake(env.state)
    monkeypatch.setattr(ecf.cli_models, "LocalClient", fake)
    monkeypatch.setattr(ecf.cli_models, "require_terminal", lambda: None)
    monkeypatch.setenv("ECF_HOME", str(tmp_path / "home"))
    return fake


def _typed(monkeypatch: pytest.MonkeyPatch, value: str) -> list[str]:
    asked: list[str] = []

    def hidden(prompt: str) -> str:
        asked.append(prompt)
        return value

    monkeypatch.setattr(ecf.cli_models, "hidden", hidden)
    return asked


def test_cli_sets_and_clears_each_provider(cli: Fake, env: Env,
                                           monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    asked = _typed(monkeypatch, TS_KEY)
    r = CliRunner().invoke(app, ["models", "api-key", "set", "--provider", "typesafe"])
    assert r.exit_code == 0, r.output
    assert "Stored the TypeSafe API key" in r.output and asked == ["TypeSafe API key (hidden): "]
    assert env.store.get("jev-api-key") == TS_KEY
    assert [p for _, p, _ in cli.sent] == ["/v1/status", "/v1/models/keys/typesafe"]
    _typed(monkeypatch, ANT_KEY)
    r = CliRunner().invoke(app, ["models", "api-key", "set", "--provider", "anthropic"])
    assert r.exit_code == 0 and "the Models API lists 1 models" in r.output
    assert env.store.get("models-api-key") == ANT_KEY
    for provider in api_keys.PROVIDERS:
        r = CliRunner().invoke(app, ["models", "api-key", "clear", "--provider", provider])
        assert r.exit_code == 0 and "Removed the" in r.output
    assert env.store.get("jev-api-key") is None and env.store.get("models-api-key") is None
    assert TS_KEY not in repr(cli.sent[0]) and r.output.count("sk-ant") == 0


@pytest.mark.parametrize("args", [[], ["--provider", "openai"], ["--provider", ""]])
@pytest.mark.parametrize("cmd", ["set", "clear"])
def test_cli_needs_a_known_provider(cli: Fake, monkeypatch: pytest.MonkeyPatch, cmd: str,
                                    args: list[str]) -> None:  # fmt: skip
    asked = _typed(monkeypatch, TS_KEY)
    r = CliRunner().invoke(app, ["models", "api-key", cmd, *args])
    assert isinstance(r.exception, InvalidInputError)
    assert r.exception.detail == api_keys.NAME_PROVIDER
    assert asked == [] and cli.sent == []


def test_cli_refuses_an_older_service_before_any_prompt(
    cli: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _typed(monkeypatch, TS_KEY)
    cli.version = "0.1.0"
    for cmd in ("set", "clear"):
        r = CliRunner().invoke(app, ["models", "api-key", cmd, "--provider", "typesafe"])
        assert isinstance(r.exception, ConflictError)
        assert r.exception.detail == ecf.cli_models.OLDER_SERVICE
    assert asked == [] and [p for _, p, _ in cli.sent] == ["/v1/status", "/v1/status"]
    cli.version = "99.0.0"  # a newer service passes
    r = CliRunner().invoke(app, ["models", "api-key", "set", "--provider", "typesafe"])
    assert r.exit_code == 0, r.output


def test_an_older_service_answers_not_found_and_nothing_is_sent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A service from before the per-provider routes, on the same version (two checkouts between
    bumps): its 404 stops the CLI; the key reaches no provider and no store."""
    sent: list[str] = []

    class OldService(Fake):
        def request(self, method: str, path: str, json: Any = None, **_: Any) -> Any:
            sent.append(path)
            if path == "/v1/status":
                return {"version": __version__}
            raise NotFoundError("not found")

    monkeypatch.setattr(ecf.cli_models, "LocalClient", OldService(None))  # pyright: ignore[reportArgumentType]
    monkeypatch.setattr(ecf.cli_models, "require_terminal", lambda: None)
    monkeypatch.setenv("ECF_HOME", str(tmp_path / "home"))
    _typed(monkeypatch, TS_KEY)
    r = CliRunner().invoke(app, ["models", "api-key", "set", "--provider", "typesafe"])
    assert isinstance(r.exception, NotFoundError) and "Stored" not in r.output
    assert sent == ["/v1/status", "/v1/models/keys/typesafe"]


@pytest.mark.parametrize(("provider", "typed"), [
    ("anthropic", "abc-not-anthropic"), ("typesafe", ANT_KEY), ("typesafe", "a b"),
    ("typesafe", "ab\x1bc"),
])  # fmt: skip
def test_cli_checks_the_key_before_sending(cli: Fake, monkeypatch: pytest.MonkeyPatch,
                                           provider: str, typed: str) -> None:  # fmt: skip
    _typed(monkeypatch, typed)
    r = CliRunner().invoke(app, ["models", "api-key", "set", "--provider", provider])
    assert isinstance(r.exception, InvalidInputError)
    assert [p for _, p, _ in cli.sent] == ["/v1/status"]  # nothing sent with the key


def test_cli_set_needs_a_terminal() -> None:
    r = CliRunner().invoke(app, ["models", "api-key", "set", "--provider", "typesafe"])
    assert isinstance(r.exception, InvalidInputError) and r.exception.detail == NO_TERMINAL


def test_hints_use_the_new_form() -> None:
    src = Path(model_watch.__file__).read_text("utf-8")
    assert "ecf models api-key set`" not in src and "--provider anthropic" in src
    refused = httpx.MockTransport(lambda _r: httpx.Response(401))
    with pytest.raises(model_watch.ModelsApiError) as err, httpx.Client(transport=refused) as c:
        model_watch.list_models(c, ANT_KEY)
    assert "ecf models api-key set --provider anthropic" in err.value.fix
