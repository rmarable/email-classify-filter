"""`ecf models install` as the CLI drives it (V1.3 step 12b): a service restart mid-install, and
an install that ends with the model still not ready."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ecf import cli_models
from ecf.errors import ServiceUnavailableError


class FakeService:
    def __init__(self, installs: list[dict[str, Any]], ready: dict[str, Any]) -> None:
        self.installs, self.ready = iter(installs), ready

    def request(self, _method: str, _path: str, _body: dict[str, Any]) -> dict[str, Any]:
        return {}

    def get(self, _path: str) -> dict[str, Any]:
        nxt = next(self.installs, None)
        return self.ready | {"install": nxt or {"state": "done", "status": "installed",
                                                "total": 0, "completed": 0}}  # fmt: skip


def _install(state: str) -> dict[str, Any]:
    return {"state": state, "status": "pulling", "total": 0, "completed": 0}


@pytest.fixture(autouse=True)
def _no_ollama_start(monkeypatch: pytest.MonkeyPatch) -> None:
    def started(_c: object, _r: object) -> None:
        return None

    monkeypatch.setattr(cli_models, "_ensure_ollama", started)
    monkeypatch.setattr(cli_models, "POLL_S", 0)


def test_a_service_restart_mid_install_ends_the_wait() -> None:
    svc = FakeService([_install("pulling"), _install("idle")], {"ready": False})
    with pytest.raises(ServiceUnavailableError, match="restarted during the install"):
        cli_models.run_install(svc, Path("/x"))  # type: ignore[arg-type]


def test_installed_but_not_ready_says_why(capsys: pytest.CaptureFixture[str]) -> None:
    fault = {"text": "Ollama listens beyond this computer. Model work is stopped until it's fixed"}
    svc = FakeService([_install("pulling")], {"ready": False, "fault": fault})
    assert cli_models.run_install(svc, Path("/x")) is False  # type: ignore[arg-type]
    assert "installed, but not ready: Ollama listens beyond" in capsys.readouterr().err
    assert cli_models.run_install(FakeService([], {"ready": True}), Path("/x")) is True  # type: ignore[arg-type]
