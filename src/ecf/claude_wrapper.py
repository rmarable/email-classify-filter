"""`ecf claude`: open Claude Code in ecf's own configuration for `/ecf-review` (SPEC §10.3).

V1.0 built the plumbing; the review tools behind `ecf-mcp` arrived in V1.4 (step 4). Facts this
relies on were checked against code.claude.com on 2026-09-27: CLAUDE_CONFIG_DIR relocates settings,
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
from ecf.errors import EcfError, InvalidInputError, ServiceUnavailableError
from ecf.paths import Paths

MIN_CLAUDE = (2, 1, 242)
ALLOWED_TOOLS = ["mcp__ecf__review_queue", "Agent"]
DENIED_TOOLS = ["Bash", "WebFetch", "WebSearch", "Edit", "Write", "NotebookEdit"]
# Only these variables are passed from the user's environment (plus LC_*); everything else,
# e.g. ANTHROPIC_* (API keys, base URLs), provider switches and OTEL exporters, is dropped.
ENV_ALLOW = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "TERM_PROGRAM",
        "COLORTERM",
        "LANG",
        "TMPDIR",
        "TZ",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NODE_EXTRA_CA_CERTS",
    }
)
# Arguments that may be passed through to `claude`; anything that could loosen the session
# (permissions, settings, MCP config, extra directories) is refused.
PASSTHROUGH_WITH_VALUE = frozenset({"--model"})
PASSTHROUGH_FLAGS = frozenset({"--verbose"})
# What the purge keeps in the config folder: ecf's own files, the login, and (V1.4) the plugin.
# Whether `.claude.json` can hold prompt text is unverified; checked in V1.4.
KEEP = frozenset({"settings.json", "ecf-mcp.json", ".credentials.json", ".claude.json", "plugins"})
# Forced in the process environment so a value in the user's shell can't change them.
PRIVACY_ENV = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "0",  # on in V1.4, exported only to the local receiver
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


def mcp_doc(ecf_mcp: Path, socket: Path) -> dict[str, Any]:
    """`ECF_SOCKET` names the install's socket: `ECF_HOME` and `--install` don't reach the MCP
    server, since the session's environment is allow-listed."""
    return {
        "mcpServers": {
            "ecf": {
                "command": str(ecf_mcp),
                "args": ["--stdio"],
                "env": {"ECF_PROFILE_TOKEN": "${ECF_PROFILE_TOKEN}", "ECF_SOCKET": str(socket)},
            }
        }
    }


def write_config(lay: Layout, ecf_mcp: Path, socket: Path) -> None:
    for d in (lay.config_dir, lay.work_dir):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        d.chmod(0o700)
    for path, doc in ((lay.settings, settings_doc()), (lay.mcp_config, mcp_doc(ecf_mcp, socket))):
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


def check_args(args: list[str]) -> list[str]:
    i = 0
    while i < len(args):
        a = args[i]
        name = a.split("=", 1)[0]
        if name in PASSTHROUGH_FLAGS or (name in PASSTHROUGH_WITH_VALUE and "=" in a):
            i += 1
        elif name in PASSTHROUGH_WITH_VALUE and i + 1 < len(args):
            i += 2
        else:
            allowed = ", ".join(sorted(PASSTHROUGH_FLAGS | PASSTHROUGH_WITH_VALUE))
            raise InvalidInputError(
                f"`ecf claude` doesn't pass {a!r} to claude (allowed: {allowed})"
            )
    return args


def session_env(lay: Layout, token: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOW or k.startswith("LC_")}
    env.update(PRIVACY_ENV)
    env["CLAUDE_CONFIG_DIR"] = str(lay.config_dir)
    env["ECF_PROFILE_TOKEN"] = token
    return env


def purge_transcripts(lay: Layout) -> int:
    """Delete everything in the config folder except KEEP: transcripts, prompt history, debug
    logs and file history can all hold email text (V1.4+). Returns how many entries went."""
    if not lay.config_dir.exists():
        return 0
    removed = 0
    for entry in lay.config_dir.iterdir():
        if entry.name in KEEP:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
        removed += 1
    return removed


def run(paths: Paths, extra_args: list[str]) -> int:
    check_args(extra_args)
    claude = find_claude()
    lay = layout(paths)
    purge_transcripts(lay)  # a crashed earlier session may have left some behind
    write_config(lay, ecf_mcp_path(), paths.socket.absolute())
    with LocalClient(paths) as c:
        session: dict[str, Any] = c.request("POST", "/v1/sessions")
    args = [claude, "--strict-mcp-config", "--mcp-config", str(lay.mcp_config), *extra_args]
    env = session_env(lay, session["profile_token"])
    try:
        return subprocess.run(args, cwd=lay.work_dir, env=env, check=False).returncode  # noqa: S603
    finally:
        purge_transcripts(lay)  # first: it must not depend on the service being reachable
        try:
            with LocalClient(paths) as c:
                c.request("DELETE", f"/v1/sessions/{session['session_id']}")
        except EcfError:
            pass  # service restarted or stopped: its in-memory session tokens are gone anyway
