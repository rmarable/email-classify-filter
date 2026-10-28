"""`ecf claude`: open Claude Code in ecf's own configuration for `/ecf-review` (SPEC §10.3).

V1.0 built the plumbing; the review tools behind `ecf-mcp` arrived in V1.4 (step 4), the plugin
(`/ecf-review` and its agents) in step 5. Facts this relies on were checked against code.claude.com
on 2026-09-27: CLAUDE_CONFIG_DIR relocates settings, transcripts and credentials (so a separate
login is needed); permissions.defaultMode "dontAsk" denies anything not pre-approved;
`--strict-mcp-config` ignores every other MCP source. `--plugin-dir` (loads a plugin for one
session) is from `claude --help` of 2.1.287.

The plugin (the `/ecf-review` and `/ecf-eval` skills) is rendered from the wheel's templates at
each start (operator decision 2026-10-02, OD-283). The agents are passed with `--agents`
(operator decision 2026-10-02, OD-307): each on the pin in force for its role (models.lock plus
any override, from the service), each with its own `ecf-mcp --agent <name>` server, which
Claude Code starts for that agent only; a plugin agent's `mcpServers` is ignored (tested
2026-10-02 with Claude Code 2.1.287), so the agents can't live in the plugin. The main session's
`ecf` server has no reading or submitting tools.

Telemetry (V1.4 step 6; SPEC §11.5, §13.4): logs and metrics go only to the service's loopback
receiver, OTLP over HTTP with JSON, with the session's own bearer token; logs every second, so a
submission's model check usually settles within about 2 s. The status line runs
`ecf.statusline`, which sends the service only the plan-usage numbers.
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
from ecf.plugin_digest import plugin_digest

MIN_CLAUDE = (2, 1, 295)  # `onFailure: block` on the spawn guard hook (OD-497)
# the main session: dispatch only (`/ecf-review`; `/ecf-eval` from V1.4 step 7)
MAIN_TOOLS = ["mcp__ecf__review_queue", "mcp__ecf__eval_next", "mcp__ecf__eval_results"]
# Built-in agent types, denied by name (OD-275): general-purpose had Bash in the 2026-10-02 test.
# `claude` appeared in a later Claude Code (2.1.291): the first /ecf-eval run handed its loop to
# it (v1.0.0, 2026-10-06). Claude Code can deny agents only by name, so the service also stops a
# session that runs any built-in agent (telemetry.stopped).
BUILTIN_AGENTS = ("general-purpose", "claude", "fork", "Explore", "Plan", "statusline-setup",
                  "claude-code-guide")  # fmt: skip
DENIED_TOOLS = [
    "Bash",
    "WebFetch",
    "WebSearch",
    "Edit",
    "Write",
    "NotebookEdit",
    *(f"Agent({a})" for a in BUILTIN_AGENTS),
    # Where a spawn runs and on which model aren't the main session's choice (OD-497): B0 run 2
    # spawned with `isolation: "remote"` (SPEC §21.2). `*` matches any explicit value; the hook
    # (spawn_guard_hook) refuses what these can't match.
    "Agent(isolation:*)",
    "Agent(model:*)",
]
# Agent name -> (template, role in models.lock, description).
AGENTS = {
    "ecf-classifier": ("classifier", "classifier",
                       "Classifies ecf review items it is given by id and claim token."),
    "ecf-classifier-high": ("classifier", "classifier_high",
                            "Classifies one ecf review item from a high-sensitivity address."),
    "ecf-actor": ("actor", "actor",
                  "Proposes one next step for each ecf review item it is given."),
    "ecf-actor-high": ("actor", "actor_high",
                       "Proposes one next step for one high-risk ecf item."),
}  # fmt: skip
SKILLS = ("ecf-review", "ecf-eval")
# A comparison eval on other models (OD-288) gets these, rendered from the same templates.
EVAL_AGENTS = {name.replace("ecf-", "ecf-eval-", 1):
               (template, role, f"For /ecf-eval only: {description[0].lower()}{description[1:]}")
               for name, (template, role, description) in AGENTS.items()}  # fmt: skip


def agent_tools(name: str) -> list[str]:
    """An agent's tools, on its own server; under dontAsk they must also be allowed in the
    settings (tested 2026-10-02, SPEC §21.2)."""
    submit = "record_classification" if "classifier" in name else "propose_action"
    return [f"mcp__{name}__get_message", f"mcp__{name}__{submit}"]


# Only ecf's own agents may be spawned, by name (OD-461): under dontAsk anything not allowed is
# refused, so a built-in agent Claude Code adds later is refused without ecf naming it (`fork` ran
# in a B eval session, 2026-10-06); the deny list stays as a second layer.
SPAWNABLE = [f"Agent({n})" for n in (*AGENTS, *EVAL_AGENTS)]
ALLOWED_TOOLS = [*MAIN_TOOLS, *SPAWNABLE,
                 *(t for n in (*AGENTS, *EVAL_AGENTS) for t in agent_tools(n))]  # fmt: skip
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
# Claude Code's plugin store (`plugins`: synced claude.ai plugins, marketplaces) isn't kept: a
# plugin's mod can approve a call the deny rules or the spawn guard refused (OD-497).
KEEP = frozenset({"settings.json", "ecf-mcp.json", ".credentials.json", ".claude.json"})
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
# Sessions only: Haiku 5.5 bills a prompt over 100,000 tokens at 5x (pricing page, read
# 2026-10-27), and Claude Code compacts it only near its 1M window, so the main session would
# pay that on most requests of a long run. A 100,000 window (its floor; code.claude.com
# env-vars and model-config, read 2026-10-27) makes it compact near 67k: Claude Code keeps a
# 33k buffer, and CLAUDE_AUTOCOMPACT_PCT_OVERRIDE at 80 or 100 didn't move it (`/context`,
# Claude Code 2.1.312, 2026-10-28; OD-564).
SESSION_ENV = {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "100000"}


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
    # -I: nothing from the working folder or the environment reaches the import path (OD-497)
    return shlex.join([sys.executable, "-I", "-m", "ecf.statusline", str(socket)])


SPAWN_GUARD_TIMEOUT_S = 30  # a slow interpreter start under memory pressure isn't a refusal


def spawn_guard_hook() -> dict[str, Any]:
    """`PreToolUse` on Agent: `ecf.spawn_guard` refuses a spawn with any key beyond
    subagent_type, description and prompt (OD-497). `onFailure: block` (Claude Code 2.1.295+,
    code.claude.com hooks, read 2026-10-13) refuses the spawn if the hook can't run."""
    command = shlex.join([sys.executable, "-I", "-m", "ecf.spawn_guard"])
    return {"PreToolUse": [{"matcher": "Agent", "hooks": [
        {"type": "command", "command": command, "timeout": SPAWN_GUARD_TIMEOUT_S,
         "onFailure": "block"}]}]}  # fmt: skip


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


def client_doc(claude_version: tuple[int, ...] | None = None) -> dict[str, Any]:
    """What `POST /v1/sessions` checks against the service's own install (v2.2.0): this
    client's version and the digest of the plugin it renders; and the Claude Code version, which
    a draft eval run records (M8 plan AP2)."""
    doc: dict[str, Any] = {"version": __version__, "plugin_digest": plugin_digest()}
    if claude_version is not None:
        doc["claude_version"] = ".".join(map(str, claude_version))
    return {"client": doc}


def settings_doc(main_model: str, statusline: str) -> dict[str, Any]:
    return {
        "cleanupPeriodDays": 1,
        "model": main_model,
        # Background calls that do no ecf work: in the C high eval, prompt suggestions were 19%
        # of its plan usage (v1.0.0, 2026-10-06; keys: code.claude.com settings reference).
        "promptSuggestionEnabled": False,
        "awaySummaryEnabled": False,
        "statusLine": {"type": "command", "command": statusline},
        "hooks": spawn_guard_hook(),
        # No plugin but ecf's own (`--plugin-dir`): a plugin's mod can approve what the deny
        # rules and the hook refuse (OD-497; code.claude.com permissions and settings, read
        # 2026-10-13: a user-level `false` for the sync keys is honored over managed `true`).
        "syncClaudeAiPlugins": False,
        "syncClaudeAiSkills": False,
        "enabledPlugins": {},
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


def render_plugin(dest: Path) -> None:
    """Write the plugin (the skills) from the wheel's templates."""
    src = resources.files("ecf.data").joinpath("plugin")
    if dest.exists():
        shutil.rmtree(dest)
    for sub in (".claude-plugin", *(f"skills/{k}" for k in SKILLS)):
        (dest / sub).mkdir(mode=0o700, parents=True)
    manifest = src.joinpath(".claude-plugin", "plugin.json").read_text(encoding="utf-8")
    _write(dest / ".claude-plugin" / "plugin.json", manifest.replace("{{VERSION}}", __version__))
    for k in SKILLS:
        skill = src.joinpath("skills", k, "SKILL.md").read_text(encoding="utf-8")
        _write(dest / "skills" / k / "SKILL.md", skill)


def agents_doc(ecf_mcp: Path, socket: Path, models: dict[str, str],
               eval_models: dict[str, str] | None = None) -> dict[str, Any]:  # fmt: skip
    """The `--agents` definitions, each on its pinned model with its own agent server; with
    `eval_models` (agent name -> model, from the service), the `ecf-eval-*` agents of a
    comparison eval too. The token reaches the server through the session's environment."""
    src = resources.files("ecf.data").joinpath("plugin")
    agents = [(n, t, models[r], d) for n, (t, r, d) in AGENTS.items()]
    agents += [(n, t, (eval_models or {})[n], d) for n, (t, _r, d) in EVAL_AGENTS.items()
               if n in (eval_models or {})]  # fmt: skip
    doc: dict[str, Any] = {}
    for name, template, model, description in agents:
        prompt = src.joinpath("agents", f"{template}.md").read_text(encoding="utf-8")
        server = {"type": "stdio", "command": str(ecf_mcp),
                  "args": ["--stdio", "--agent", name, "--socket", str(socket)]}  # fmt: skip
        doc[name] = {"description": description, "prompt": prompt.replace("{{NAME}}", name),
                     "tools": agent_tools(name), "model": model,
                     "mcpServers": [{name: server}]}  # fmt: skip
    return doc


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


def work_dir_problem(lay: Layout) -> str | None:
    """Why the working folder can't hold a session, or None (OD-497). Sessions load only user
    settings (`--setting-sources user`); this is the second layer: a `.claude` folder there could
    hold project settings that turn hooks off, and inside a git repository Claude Code reads
    `.claude/settings.local.json` at the repository root (code.claude.com settings, read
    2026-10-13). ecf never creates either."""
    if (lay.work_dir / ".claude").exists():
        return (f"{lay.work_dir / '.claude'} exists; ecf never creates it: look at it, remove it,"
                " and run `ecf claude` again")  # fmt: skip
    for parent in lay.work_dir.absolute().parents:
        if (parent / ".git").exists():
            return (f"{lay.work_dir} is inside the git repository {parent}; move ecf's data"
                    " folder (ECF_HOME) out of it")  # fmt: skip
    return None


def check_work_dir(lay: Layout) -> None:
    if problem := work_dir_problem(lay):
        raise InvalidInputError(f"ecf claude won't start: {problem}")


def write_config(lay: Layout, ecf_mcp: Path, socket: Path, models: dict[str, str]) -> None:
    check_work_dir(lay)
    for d in (lay.config_dir, lay.work_dir):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        d.chmod(0o700)
    docs = ((lay.settings, settings_doc(models["main_session"], statusline_command(socket))),
            (lay.mcp_config, mcp_doc(ecf_mcp, socket)))  # fmt: skip
    for path, doc in docs:
        _write(path, json.dumps(doc, indent=2) + "\n")
    render_plugin(lay.plugin_dir)
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
    return find_claude_version()[0]


def find_claude_version() -> tuple[str, tuple[int, ...]]:
    """`claude` and its version, refused below MIN_CLAUDE."""
    claude = shutil.which("claude")
    if claude is None:
        raise ServiceUnavailableError("Claude Code (`claude`) is not installed")
    version = claude_version(claude)
    if version is None or version < MIN_CLAUDE:
        found = ".".join(map(str, version)) if version else "unknown"
        need = ".".join(map(str, MIN_CLAUDE))
        raise ServiceUnavailableError(f"ecf claude needs Claude Code {need}+ (found {found})")
    return claude, version


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


def base_env(lay: Layout) -> dict[str, str]:
    """The allow-listed environment in ecf's config: for sessions, and `claude auth`."""
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOW or k.startswith("LC_")}
    env.update(PRIVACY_ENV)
    env["CLAUDE_CONFIG_DIR"] = str(lay.config_dir)
    return env


def session_env(lay: Layout, token: str, agent_token: str,
                telemetry: dict[str, str]) -> dict[str, str]:  # fmt: skip
    env = base_env(lay)
    env.update(SESSION_ENV)
    env.update(telemetry)
    env["ECF_PROFILE_TOKEN"] = token
    env["ECF_AGENT_TOKEN"] = agent_token  # the agent servers inherit it (OD-307)
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
    claude, version = find_claude_version()
    ecf_mcp = ecf_mcp_path()
    lay = layout(paths)
    check_work_dir(lay)  # before a service session is opened
    purge_transcripts(lay)  # a crashed earlier session may have left some behind
    with LocalClient(paths) as c:
        session: dict[str, Any] = c.request("POST", "/v1/sessions", client_doc(version))
    socket = paths.socket.absolute()
    try:
        ev: dict[str, Any] | None = session.get("eval")
        write_config(lay, ecf_mcp, socket, session["models"])
        agents = agents_doc(ecf_mcp, socket, session["models"], ev["agents"] if ev else None)
    except BaseException:
        with LocalClient(paths) as c:
            c.request("DELETE", f"/v1/sessions/{session['session_id']}")
        raise
    waiting = int(session.get("waiting", 0))
    echo(f"ecf claude: {waiting} item(s) waiting for review; type /ecf-review to start.")
    if ev:
        echo(f"Eval {str(ev['run_id'])[:8]} is registered ({ev['left']} of {ev['total']} cases"
             " left); type /ecf-eval to run it.")  # fmt: skip
    if line := plan_line(session.get("last_plan")):
        echo(line)
    # only ecf's own settings: no project or local settings file can turn the hook off (OD-497)
    args = [claude, "--setting-sources", "user", "--strict-mcp-config", "--mcp-config",
            str(lay.mcp_config),
            "--plugin-dir", str(lay.plugin_dir), "--agents", json.dumps(agents),
            *extra_args]  # fmt: skip
    port = session.get("telemetry_port")
    if not isinstance(port, int):
        raise ServiceUnavailableError("the service's telemetry receiver isn't listening")
    env = session_env(lay, session["profile_token"], session["agent_token"],
                      telemetry_env(port, session["telemetry_bearer"]))  # fmt: skip
    code: int | None = None
    try:
        code = subprocess.run(args, cwd=lay.work_dir, env=env, check=False).returncode  # noqa: S603
        return code
    finally:
        purge_transcripts(lay)  # first: it must not depend on the service being reachable
        try:
            with LocalClient(paths) as c:  # the exit code: why a draft run's session ended
                c.request("DELETE", f"/v1/sessions/{session['session_id']}",
                          {} if code is None else {"exit_code": code})  # fmt: skip
        except EcfError:
            pass  # service restarted or stopped: its in-memory session tokens are gone anyway
