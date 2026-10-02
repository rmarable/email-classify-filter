"""`ecf claude`: open Claude Code in ecf's own configuration for `/ecf-review` (SPEC §10.3).

V1.0 built the plumbing; the review tools behind `ecf-mcp` arrived in V1.4 (step 4), the plugin
(`/ecf-review` and its agents) in step 5. Facts this relies on were checked against code.claude.com
on 2026-09-27: CLAUDE_CONFIG_DIR relocates settings, transcripts and credentials (so a separate
login is needed); permissions.defaultMode "dontAsk" denies anything not pre-approved;
`--strict-mcp-config` ignores every other MCP source. `--plugin-dir` (loads a plugin for one
session) is from `claude --help` of 2.1.287.

The plugin is rendered from the wheel's templates at each start (operator decision 2026-10-02,
OD-283): each agent's `model:` is the pin in force (models.lock plus any override, from the
service), so the plugin always matches both the package and the pins. Unverified, confirm in
V1.4: that `model:` takes a full model ID, and the `/ecf-review` name of a plugin skill.

Telemetry (V1.4 step 6; SPEC §11.5, §13.4): logs and metrics go only to the service's loopback
receiver, OTLP over HTTP with JSON, with the session's own bearer token; logs every second, so the
model check binds a call within about 2 s. The status line runs `ecf.statusline`, which sends the
service only the plan-usage numbers.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, cast

from ecf import __version__
from ecf.client import LocalClient
from ecf.errors import EcfError, InvalidInputError, ServiceUnavailableError
from ecf.paths import Paths

MIN_CLAUDE = (2, 1, 242)
# Under dontAsk a subagent's tools must also be allowed here (tested 2026-10-02, SPEC §21.2).
SUBAGENT_TOOLS = [
    "mcp__ecf__get_message",
    "mcp__ecf__record_classification",
    "mcp__ecf__propose_action",
]
ALLOWED_TOOLS = ["mcp__ecf__review_queue", "Agent", *SUBAGENT_TOOLS]
# Built-in agent types, denied by name (OD-275): general-purpose had Bash in the 2026-10-02 test.
BUILTIN_AGENTS = ("general-purpose", "Explore", "Plan", "statusline-setup", "claude-code-guide")
DENIED_TOOLS = [
    "Bash",
    "WebFetch",
    "WebSearch",
    "Edit",
    "Write",
    "NotebookEdit",
    *(f"Agent({a})" for a in BUILTIN_AGENTS),
]
# Plugin agent -> (template, role in models.lock, description).
AGENTS = {
    "classifier": ("classifier", "classifier",
                   "Classifies ecf review items it is given by id and claim token."),
    "classifier-high": ("classifier", "classifier_high",
                        "Classifies one ecf review item from a high-sensitivity address."),
    "actor": ("actor", "actor", "Proposes one next step for each ecf review item it is given."),
    "actor-high": ("actor", "actor_high", "Proposes one next step for one high-risk ecf item."),
}  # fmt: skip
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
# What the purge keeps in the config folder: ecf's own files and the login. `.claude.json` held no
# prompt text in the 2026-10-02 test (SPEC §10.3). The plugin is rendered again at each start.
KEEP = frozenset({"settings.json", "ecf-mcp.json", ".credentials.json", ".claude.json", "plugins"})
# Forced in the process environment so a value in the user's shell can't change them.
PRIVACY_ENV = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "0",  # on only with the receiver's settings (telemetry_env)
    "OTEL_LOG_USER_PROMPTS": "0",
    "OTEL_LOG_ASSISTANT_RESPONSES": "0",
    "OTEL_LOG_TOOL_DETAILS": "0",
    "OTEL_LOG_TOOL_CONTENT": "0",
    "OTEL_LOG_RAW_API_BODIES": "0",
    "OTEL_LOG_MANAGED_SETTINGS": "0",
    "OTEL_METRICS_INCLUDE_ACCOUNT_UUID": "false",
    "CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS": "180000",
    "DISABLE_AUTOUPDATER": "1",  # OD-276: updates come from normal use, not ecf's sessions
}


LOGS_INTERVAL_MS = "1000"  # as in the 2026-10-02 test; the model check waits about 2 s


def telemetry_env(port: int, bearer: str) -> dict[str, str]:
    """Claude Code telemetry to the service's receiver only (the user's OTEL_* never pass)."""
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{port}",
        "OTEL_EXPORTER_OTLP_HEADERS": f"Authorization=Bearer {bearer}",
        "OTEL_LOGS_EXPORT_INTERVAL": LOGS_INTERVAL_MS,
    }


def statusline_command(socket: Path) -> str:
    return shlex.join([sys.executable, "-m", "ecf.statusline", str(socket)])


@dataclass(frozen=True)
class Layout:
    config_dir: Path
    work_dir: Path
    settings: Path
    mcp_config: Path
    plugin_dir: Path
    state: Path  # .claude.json


def layout(paths: Paths) -> Layout:
    cfg = paths.data_dir / "claude-config"
    return Layout(cfg, paths.data_dir / "claude-work", cfg / "settings.json",
                  cfg / "ecf-mcp.json", cfg / "ecf-plugin", cfg / ".claude.json")  # fmt: skip


def settings_doc(main_model: str, statusline: str) -> dict[str, Any]:
    return {
        "cleanupPeriodDays": 1,
        "model": main_model,
        "statusLine": {"type": "command", "command": statusline},
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


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def render_plugin(dest: Path, models: dict[str, str]) -> None:
    """Write the plugin from the wheel's templates, each agent on its pinned model."""
    src = resources.files("ecf.data").joinpath("plugin")
    if dest.exists():
        shutil.rmtree(dest)
    for sub in (".claude-plugin", "skills/ecf-review", "agents"):
        (dest / sub).mkdir(mode=0o700, parents=True)
    manifest = src.joinpath(".claude-plugin", "plugin.json").read_text(encoding="utf-8")
    _write(dest / ".claude-plugin" / "plugin.json", manifest.replace("{{VERSION}}", __version__))
    skill = src.joinpath("skills", "ecf-review", "SKILL.md").read_text(encoding="utf-8")
    _write(dest / "skills" / "ecf-review" / "SKILL.md", skill)
    for name, (template, role, description) in AGENTS.items():
        text = src.joinpath("agents", f"{template}.md").read_text(encoding="utf-8")
        for key, value in (("NAME", name), ("DESCRIPTION", description), ("MODEL", models[role])):
            text = text.replace("{{" + key + "}}", value)
        _write(dest / "agents" / f"{name}.md", text)


def mark_ready(lay: Layout) -> None:
    """Onboarding done and the working folder trusted in `.claude.json` (OD-276); a first
    interactive start otherwise shows onboarding screens (tested 2026-10-02). Keeps other keys."""
    try:
        loaded: Any = json.loads(lay.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        loaded = None
    state = cast(dict[str, Any], loaded) if isinstance(loaded, dict) else {}
    state["hasCompletedOnboarding"] = True
    found: Any = state.get("projects")
    projects = cast(dict[str, Any], found) if isinstance(found, dict) else {}
    state["projects"] = projects
    key = str(lay.work_dir.resolve())
    entry: Any = projects.get(key)
    kept = cast(dict[str, Any], entry) if isinstance(entry, dict) else {}
    projects[key] = {**kept, "hasTrustDialogAccepted": True}
    _write(lay.state, json.dumps(state, indent=2) + "\n")


def write_config(lay: Layout, ecf_mcp: Path, socket: Path, models: dict[str, str]) -> None:
    for d in (lay.config_dir, lay.work_dir):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        d.chmod(0o700)
    docs = ((lay.settings, settings_doc(models["main_session"], statusline_command(socket))),
            (lay.mcp_config, mcp_doc(ecf_mcp, socket)))  # fmt: skip
    for path, doc in docs:
        _write(path, json.dumps(doc, indent=2) + "\n")
    render_plugin(lay.plugin_dir, models)
    mark_ready(lay)


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


def session_env(lay: Layout, token: str, telemetry: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOW or k.startswith("LC_")}
    env.update(PRIVACY_ENV)
    env.update(telemetry)
    env["CLAUDE_CONFIG_DIR"] = str(lay.config_dir)
    env["ECF_PROFILE_TOKEN"] = token
    return env


def plan_line(p: Any) -> str | None:
    """The plan usage after the last review (status line; Pro and Max plans only)."""
    if not isinstance(p, dict):
        return None
    d = cast(dict[str, Any], p)
    windows = (("five_hour", "5-hour"), ("seven_day", "7-day"))
    parts = [f"{label} {d[k]:.0f}%" for k, label in windows if isinstance(d.get(k), int | float)]
    if not parts:
        return None
    return (
        f"Plan used after the last review ({str(d.get('at'))[:16].replace('T', ' ')} UTC): "
        + ", ".join(parts)
    )


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


def run(paths: Paths, extra_args: list[str], echo: Callable[[str], None] = print) -> int:
    check_args(extra_args)
    claude = find_claude()
    ecf_mcp = ecf_mcp_path()
    lay = layout(paths)
    purge_transcripts(lay)  # a crashed earlier session may have left some behind
    with LocalClient(paths) as c:
        session: dict[str, Any] = c.request("POST", "/v1/sessions")
    try:
        write_config(lay, ecf_mcp, paths.socket.absolute(), session["models"])
    except BaseException:
        with LocalClient(paths) as c:
            c.request("DELETE", f"/v1/sessions/{session['session_id']}")
        raise
    waiting = int(session.get("waiting", 0))
    echo(f"ecf claude: {waiting} item(s) waiting for review; type /ecf-review to start.")
    if line := plan_line(session.get("last_plan")):
        echo(line)
    args = [claude, "--strict-mcp-config", "--mcp-config", str(lay.mcp_config),
            "--plugin-dir", str(lay.plugin_dir), *extra_args]  # fmt: skip
    port = session.get("telemetry_port")
    if not isinstance(port, int):
        raise ServiceUnavailableError("the service's telemetry receiver isn't listening")
    env = session_env(lay, session["profile_token"],
                      telemetry_env(port, session["telemetry_bearer"]))  # fmt: skip
    try:
        return subprocess.run(args, cwd=lay.work_dir, env=env, check=False).returncode  # noqa: S603
    finally:
        purge_transcripts(lay)  # first: it must not depend on the service being reachable
        try:
            with LocalClient(paths) as c:
                c.request("DELETE", f"/v1/sessions/{session['session_id']}")
        except EcfError:
            pass  # service restarted or stopped: its in-memory session tokens are gone anyway
