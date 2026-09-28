import json
import os
import stat
import sys
from pathlib import Path

import pytest

from ecf import claude_wrapper as cw
from ecf.errors import InvalidInputError, ServiceUnavailableError
from ecf.paths import Paths

from .conftest import uds_client

FAKE_CLAUDE = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "{version} (Claude Code)"; exit 0; fi
mkdir -p "$CLAUDE_CONFIG_DIR/projects/p"
echo '{{"x":1}}' > "$CLAUDE_CONFIG_DIR/projects/p/s.jsonl"
{{ echo "ARGS=$*"; echo "TOKEN=$ECF_PROFILE_TOKEN"; echo "CFG=$CLAUDE_CONFIG_DIR";
   echo "PROMPTS=$OTEL_LOG_USER_PROMPTS"; echo "PWD=$(pwd)"; }} > "{record}"
exit 0
"""


def fake_claude(bin_dir: Path, record: Path, version: str = "2.1.281") -> None:
    bin_dir.mkdir(exist_ok=True)
    f = bin_dir / "claude"
    f.write_text(FAKE_CLAUDE.format(version=version, record=record))
    f.chmod(0o755)


def test_settings_document() -> None:
    d = cw.settings_doc()
    assert d["cleanupPeriodDays"] == 1
    assert d["permissions"]["defaultMode"] == "dontAsk"
    assert d["permissions"]["allow"] == ["mcp__ecf__review_queue", "Agent"]
    for tool in ("Bash", "WebFetch", "WebSearch", "Edit", "Write"):
        assert tool in d["permissions"]["deny"]
    assert "hooks" not in d


def test_mcp_document() -> None:
    d = cw.mcp_doc(Path("/abs/ecf-mcp"))["mcpServers"]["ecf"]
    assert d["command"] == "/abs/ecf-mcp" and d["args"] == ["--stdio"]
    assert d["env"] == {"ECF_PROFILE_TOKEN": "${ECF_PROFILE_TOKEN}"}


def test_config_is_private(tmp_path: Path) -> None:
    lay = cw.layout(Paths("t", tmp_path))
    cw.write_config(lay, Path("/abs/ecf-mcp"))
    for d in (lay.config_dir, lay.work_dir):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
    for f in (lay.settings, lay.mcp_config):
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert json.loads(lay.settings.read_text())["permissions"]["defaultMode"] == "dontAsk"


def test_version_parsing() -> None:
    assert cw.parse_claude_version("2.1.281 (Claude Code)\n") == (2, 1, 281)
    assert cw.parse_claude_version("garbage") is None


def test_version_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_claude(tmp_path / "bin", tmp_path / "rec", version="2.1.100")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(ServiceUnavailableError, match=r"2\.1\.242"):
        cw.find_claude()


def test_privacy_gates_forced_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_LOG_USER_PROMPTS", "1")
    monkeypatch.setenv("OTEL_LOG_RAW_API_BODIES", "1")
    env = cw.session_env(cw.layout(Paths("t", tmp_path)), "tok")
    assert env["OTEL_LOG_USER_PROMPTS"] == "0" and env["OTEL_LOG_RAW_API_BODIES"] == "0"
    assert env["ECF_PROFILE_TOKEN"] == "tok"
    assert env["CLAUDE_CONFIG_DIR"].endswith("claude-config")


def test_run_end_to_end(running: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.txt"
    fake_claude(tmp_path / "bin", record)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    assert (Path(sys.executable).parent / "ecf-mcp").exists()
    assert cw.run(running, ["--model", "x"]) == 0
    rec = dict(line.split("=", 1) for line in record.read_text().splitlines())
    lay = cw.layout(running)
    assert rec["ARGS"].startswith("--strict-mcp-config --mcp-config ")
    assert rec["ARGS"].endswith("--model x")
    assert rec["CFG"] == str(lay.config_dir) and rec["PROMPTS"] == "0"
    assert Path(rec["PWD"]).resolve() == lay.work_dir.resolve()
    token = rec["TOKEN"]
    assert token
    with uds_client(running) as c:  # the session token was revoked on exit
        r = c.get("/v1/status", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
    assert not (lay.config_dir / "projects").exists()  # transcripts purged


@pytest.mark.parametrize("args", [[], ["--model", "x"], ["--model=x", "--verbose"]])
def test_allowed_args(args: list[str]) -> None:
    assert cw.check_args(args) == args


@pytest.mark.parametrize(
    "args",
    [
        ["--dangerously-skip-permissions"],
        ["--settings", "x.json"],
        ["--mcp-config", "x"],
        ["--add-dir", "/"],
        ["--permission-mode", "bypassPermissions"],
        ["--model"],
        ["-p", "hi"],
    ],
)
def test_refused_args(args: list[str]) -> None:
    with pytest.raises(InvalidInputError):
        cw.check_args(args)


def test_environment_is_allow_listed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-dummy")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://proxy.example")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.example")
    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy.example:8080")
    env = cw.session_env(cw.layout(Paths("t", tmp_path)), "tok")
    for gone in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    ):
        assert gone not in env
    assert env["HTTPS_PROXY"] == "http://corp-proxy.example:8080" and "PATH" in env
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "0"


def test_purge_keeps_only_login_and_config(tmp_path: Path) -> None:
    lay = cw.layout(Paths("t", tmp_path))
    cw.write_config(lay, Path("/abs/ecf-mcp"))
    for name in (".credentials.json", ".claude.json", "history.jsonl"):
        (lay.config_dir / name).write_text("{}")
    for d in ("projects/p", "debug", "file-history", "plugins/ecf"):
        (lay.config_dir / d).mkdir(parents=True)
    assert cw.purge_transcripts(lay) == 4  # history.jsonl, projects, debug, file-history
    assert sorted(p.name for p in lay.config_dir.iterdir()) == sorted(cw.KEEP)


def test_purge_happens_even_if_revoke_fails(
    running: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_claude(tmp_path / "bin", tmp_path / "record.txt")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    real = cw.LocalClient

    class RevokeFails(real):  # type: ignore[misc, valid-type]
        def request(self, method: str, path: str, json: object = None, *, auth: bool = True):  # type: ignore[no-untyped-def]
            if method == "DELETE":
                raise ServiceUnavailableError("service restarted")
            return super().request(method, path, json, auth=auth)

    monkeypatch.setattr(cw, "LocalClient", RevokeFails)
    assert cw.run(running, []) == 0
    assert not (cw.layout(running).config_dir / "projects").exists()
