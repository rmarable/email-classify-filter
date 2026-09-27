import json
import os
import stat
import sys
from pathlib import Path

import pytest

from ecf import claude_wrapper as cw
from ecf.errors import ServiceUnavailableError
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
