"""`ecf models status` after v3.0.0 (ADR 0031): the Claude pins, any override and the weekly
watch, through `GET /v1/models/claude`; `ecf models install|prune|serve` and `GET /v1/models`
went with the local model."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import pytest
from typer.testing import CliRunner

import ecf.cli_models
from ecf.cli import app
from ecf.client import parse_reply
from ecf.paths import Paths
from ecf_server import claude_pins
from ecf_server.api import ServiceState
from ecf_server.secretstore.memory import MemorySecretStore
from tests.test_addresses import TOKEN, call, make_state


class Fake:
    """LocalClient over the ASGI app."""

    def __init__(self, st: ServiceState) -> None:
        self.st = st

    def __call__(self, _p: Paths) -> Self:
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: type[BaseException] | BaseException | TracebackType | None) -> None:
        return None

    def get(self, path: str, **_: Any) -> Any:
        return parse_reply(call(self.st, "GET", path, None, TOKEN))


@pytest.fixture
def state(conn: sqlite3.Connection, db_path: Path) -> ServiceState:
    del conn  # migrated
    return make_state(db_path, MemorySecretStore())


def test_status_shows_the_claude_pins_and_the_watch(
    state: ServiceState, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ecf.cli_models, "LocalClient", Fake(state))
    r = CliRunner().invoke(app, ["models", "status"])
    assert r.exit_code == 0, r.output
    lock = claude_pins.load_lock()
    assert "Claude pins (models.lock)" in r.output and lock["actor_high"] in r.output
    assert "no Models API key" in r.output
    assert "Ollama" not in r.output and "not ready" not in r.output


def test_the_local_model_routes_and_commands_are_gone(state: ServiceState) -> None:
    assert call(state, "GET", "/v1/models", None, TOKEN).status_code == 404
    for path in ("/v1/models/install", "/v1/models/prune"):
        assert call(state, "POST", path, {}, TOKEN).status_code == 404
    got = call(state, "GET", "/v1/models/claude", None, TOKEN).json()
    assert set(got) == {"claude", "watch"} and "ollama" not in got["watch"]
    for cmd in ("install", "prune", "serve"):
        r = CliRunner().invoke(app, ["models", cmd])
        assert r.exit_code != 0 and "No such command" in r.output
