"""`ecf claude`: open Claude Code in ecf's own configuration for `/ecf-review` (SPEC §10.3).

V1.0 builds the plumbing; the review tools behind `ecf-mcp` arrive in V1.4. Facts this relies
on were checked against code.claude.com on 2026-09-27: CLAUDE_CONFIG_DIR relocates settings,
transcripts and credentials (so a separate login is needed); permissions.defaultMode "dontAsk"
denies anything not pre-approved; `--strict-mcp-config` ignores every other MCP source.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecf.client import LocalClient
from ecf.errors import ServiceUnavailableError
from ecf.paths import Paths

MIN_CLAUDE = (2, 1, 242)
ALLOWED_TOOLS = ["mcp__ecf__review_queue", "Agent"]
DENIED_TOOLS = ["Bash", "WebFetch", "WebSearch", "Edit", "Write", "NotebookEdit"]
# Forced off in the process environment so a value in the user's shell can't turn them on.
PRIVACY_ENV = {
    "OTEL_LOG_USER_PROMPTS": "0",
    "OTEL_LOG_ASSISTANT_RESPONSES": "0",
    "OTEL_LOG_TOOL_DETAILS": "0",
    "OTEL_LOG_TOOL_CONTENT": "0",
    "OTEL_LOG_RAW_API_BODIES": "0",
    "OTEL_LOG_MANAGED_SETTINGS": "0",
    "OTEL_METRICS_INCLUDE_ACCOUNT_UUID": "false",
    "CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS": "180000",
}


@dataclass(frozen=True)
class Layout:
    config_dir: Path
    work_dir: Path
    settings: Path
    mcp_config: Path


def layout(paths: Paths) -> Layout:
    cfg = paths.data_dir / "claude-config"
    return Layout(cfg, paths.data_dir / "claude-work", cfg / "settings.json", cfg / "ecf-mcp.json")


def settings_doc() -> dict[str, Any]:
    return {
        "cleanupPeriodDays": 1,
        "permissions": {
            "defaultMode": "dontAsk",
            "allow": ALLOWED_TOOLS,
            "deny": DENIED_TOOLS,
        },
    }


def mcp_doc(ecf_mcp: Path) -> dict[str, Any]:
    return {
        "mcpServers": {
            "ecf": {
                "command": str(ecf_mcp),
                "args": ["--stdio"],
                "env": {"ECF_PROFILE_TOKEN": "${ECF_PROFILE_TOKEN}"},
            }
        }
    }


def write_config(lay: Layout, ecf_mcp: Path) -> None:
    for d in (lay.config_dir, lay.work_dir):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        d.chmod(0o700)
    for path, doc in ((lay.settings, settings_doc()), (lay.mcp_config, mcp_doc(ecf_mcp))):
        path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)


def parse_claude_version(text: str) -> tuple[int, ...] | None:
    m = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else None


def claude_version(claude: str) -> tuple[int, ...] | None:
    cmd = [claude, "--version"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)  # noqa: S603
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return parse_claude_version(out.stdout) if out.returncode == 0 else None


def find_claude() -> str:
    claude = shutil.which("claude")
    if claude is None:
        raise ServiceUnavailableError("Claude Code (`claude`) is not installed")
    version = claude_version(claude)
    if version is None or version < MIN_CLAUDE:
        found = ".".join(map(str, version)) if version else "unknown"
        need = ".".join(map(str, MIN_CLAUDE))
        raise ServiceUnavailableError(f"ecf claude needs Claude Code {need}+ (found {found})")
    return claude


def ecf_mcp_path() -> Path:
    p = Path(sys.executable).parent / "ecf-mcp"
    if not p.exists():
        raise ServiceUnavailableError(f"ecf-mcp not found next to {sys.executable}")
    return p.absolute()


def session_env(lay: Layout, token: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_LOG_")}
    env.update(PRIVACY_ENV)
    env["CLAUDE_CONFIG_DIR"] = str(lay.config_dir)
    env["ECF_PROFILE_TOKEN"] = token
    return env


def purge_transcripts(lay: Layout) -> int:
    """Delete this configuration's session transcripts (they contain email text in V1.4+)."""
    projects = lay.config_dir / "projects"
    if not projects.exists():
        return 0
    removed = sum(1 for _ in projects.rglob("*.jsonl"))
    shutil.rmtree(projects)
    return removed


def run(paths: Paths, extra_args: list[str]) -> int:
    claude = find_claude()
    lay = layout(paths)
    write_config(lay, ecf_mcp_path())
    with LocalClient(paths) as c:
        session: dict[str, Any] = c.request("POST", "/v1/sessions")
    args = [claude, "--strict-mcp-config", "--mcp-config", str(lay.mcp_config), *extra_args]
    env = session_env(lay, session["profile_token"])
    try:
        return subprocess.run(args, cwd=lay.work_dir, env=env, check=False).returncode  # noqa: S603
    finally:
        with LocalClient(paths) as c:
            c.request("DELETE", f"/v1/sessions/{session['session_id']}")
        purge_transcripts(lay)
