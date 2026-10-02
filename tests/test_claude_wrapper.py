import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

import ecf
from ecf import claude_wrapper as cw
from ecf.errors import InvalidInputError, ServiceUnavailableError
from ecf.paths import Paths

from .conftest import uds_client

FAKE_CLAUDE = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "{version} (Claude Code)"; exit 0; fi
mkdir -p "$CLAUDE_CONFIG_DIR/projects/p"
echo '{{"x":1}}' > "$CLAUDE_CONFIG_DIR/projects/p/s.jsonl"
"{python}" -c 'import json, sys; json.dump(sys.argv[2:], open(sys.argv[1], "w"))' \\
  "{record}.args" "$@"
{{ echo "TOKEN=$ECF_PROFILE_TOKEN"; echo "AGENT_TOKEN=$ECF_AGENT_TOKEN";
   echo "CFG=$CLAUDE_CONFIG_DIR";
   echo "PROMPTS=$OTEL_LOG_USER_PROMPTS"; echo "PWD=$(pwd)";
   echo "TELEMETRY=$CLAUDE_CODE_ENABLE_TELEMETRY"; echo "OTLP=$OTEL_EXPORTER_OTLP_ENDPOINT";
   echo "PROTOCOL=$OTEL_EXPORTER_OTLP_PROTOCOL"; }} > "{record}"
"{python}" "{session}" > "{record}.status"
exit 0
"""
# What the fake session does: one telemetry export to the receiver, and the status line once.
FAKE_SESSION = """
import json, os, subprocess, urllib.request
cfg = os.environ["CLAUDE_CONFIG_DIR"]
cmd = json.load(open(os.path.join(cfg, "settings.json")))["statusLine"]["command"]
limits = {"five_hour": {"used_percentage": 12.5, "resets_at": 1790000000},
          "seven_day": {"used_percentage": 95, "resets_at": 1790500000}}
line = subprocess.run(cmd, shell=True, input=json.dumps({"session_id": "s", "rate_limits": limits}),
                      capture_output=True, text=True).stdout
attrs = [{"key": "event.name", "value": {"stringValue": "api_request"}},
         {"key": "event.sequence", "value": {"intValue": "1"}},
         {"key": "model", "value": {"stringValue": "claude-haiku-4-5-20251001"}},
         {"key": "query_source", "value": {"stringValue": "main"}},
         {"key": "input_tokens", "value": {"intValue": "1200"}},
         {"key": "user.email", "value": {"stringValue": "someone@example.com"}}]
body = {"resourceLogs": [{"scopeLogs": [{"logRecords": [{"attributes": attrs}]}]}]}
auth = os.environ["OTEL_EXPORTER_OTLP_HEADERS"].split("=", 1)[1]
req = urllib.request.Request(os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] + "/v1/logs",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json", "Authorization": auth})
print(line.strip(), urllib.request.urlopen(req, timeout=10).status)
"""


def fake_claude(bin_dir: Path, record: Path, version: str = "2.1.281") -> None:
    bin_dir.mkdir(exist_ok=True)
    session = bin_dir / "fake_session.py"
    session.write_text(FAKE_SESSION)
    f = bin_dir / "claude"
    f.write_text(FAKE_CLAUDE.format(version=version, record=record, python=sys.executable,
                                    session=session))  # fmt: skip
    f.chmod(0o755)


MODELS = {"main_session": "claude-haiku-x", "classifier": "claude-haiku-x",
          "classifier_high": "claude-sonnet-x", "actor": "claude-sonnet-x",
          "actor_high": "claude-opus-x"}  # fmt: skip


def test_settings_document() -> None:
    d = cw.settings_doc("claude-haiku-x", "status-cmd")
    assert d["cleanupPeriodDays"] == 1 and d["model"] == "claude-haiku-x"
    assert d["statusLine"] == {"type": "command", "command": "status-cmd"}
    assert d["permissions"]["defaultMode"] == "dontAsk"
    allow = d["permissions"]["allow"]
    assert allow[:4] == ["mcp__ecf__review_queue", "mcp__ecf__eval_next",
                         "mcp__ecf__eval_results", "Agent"]  # fmt: skip
    assert "mcp__ecf-actor-high__propose_action" in allow  # each agent's own server (OD-307)
    assert "mcp__ecf-eval-classifier__record_classification" in allow
    assert not any(t.startswith("mcp__ecf__") and t.endswith(("get_message", "propose_action"))
                   for t in allow)  # fmt: skip
    for tool in ("Bash", "WebFetch", "WebSearch", "Edit", "Write", "Agent(general-purpose)",
                 "Agent(Explore)", "Agent(Plan)"):  # fmt: skip
        assert tool in d["permissions"]["deny"]
    assert "hooks" not in d


def _frontmatter(text: str) -> dict[str, str]:
    head = text.split("---\n")[1]
    return dict(line.split(": ", 1) for line in head.strip().splitlines())


def test_plugin_has_only_the_skills(tmp_path: Path) -> None:
    dest = tmp_path / "plugin"
    (dest / "stale.md").parent.mkdir()
    (dest / "stale.md").write_text("old")
    cw.render_plugin(dest)
    assert not (dest / "stale.md").exists() and not (dest / "agents").exists()
    manifest = json.loads((dest / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "ecf" and manifest["version"] == ecf.__version__
    skill = (dest / "skills" / "ecf-review" / "SKILL.md").read_text()
    assert _frontmatter(skill)["name"] == "ecf-review"
    skill = (dest / "skills" / "ecf-eval" / "SKILL.md").read_text()
    assert _frontmatter(skill)["name"] == "ecf-eval"
    assert _frontmatter(skill)["disable-model-invocation"] == "true"


def test_agents_each_have_their_pin_and_their_own_server() -> None:
    """OD-307: `--agents`, each agent on its pin with its own `ecf-mcp --agent` server."""
    doc = cw.agents_doc(Path("/abs/ecf-mcp"), Path("/data/t/run/ecf.sock"), MODELS)
    want = {"ecf-classifier": "claude-haiku-x", "ecf-classifier-high": "claude-sonnet-x",
            "ecf-actor": "claude-sonnet-x", "ecf-actor-high": "claude-opus-x"}  # fmt: skip
    assert {n: a["model"] for n, a in doc.items()} == want
    for name, a in doc.items():
        submit = "record_classification" if "classifier" in name else "propose_action"
        assert a["tools"] == [f"mcp__{name}__get_message", f"mcp__{name}__{submit}"]
        assert "{{" not in a["prompt"] and f"`mcp__{name}__get_message`" in a["prompt"]
        assert "mcp__ecf__" not in a["prompt"]
        assert a["mcpServers"] == [{name: {
            "type": "stdio", "command": "/abs/ecf-mcp",
            "args": ["--stdio", "--agent", name, "--socket", "/data/t/run/ecf.sock"]}}]  # fmt: skip
        assert "env" not in a["mcpServers"][0][name]  # the token comes from the environment
        assert set(a["tools"]) <= set(cw.ALLOWED_TOOLS)


def test_eval_agents_only_for_a_comparison() -> None:
    """V1.4 step 7 (OD-288): the `ecf-eval-*` agents only when the service names them, each on
    the run's model, from the same templates."""
    sock, mcp = Path("/s.sock"), Path("/m")
    assert not [n for n in cw.agents_doc(mcp, sock, MODELS) if "eval" in n]
    doc = cw.agents_doc(mcp, sock, MODELS, {"ecf-eval-classifier": "claude-opus-y",
                                            "ecf-eval-actor": "claude-opus-y"})  # fmt: skip
    assert sorted(n for n in doc if "eval" in n) == ["ecf-eval-actor", "ecf-eval-classifier"]
    ev = doc["ecf-eval-classifier"]
    assert ev["model"] == "claude-opus-y"
    assert ev["prompt"] == doc["ecf-classifier"]["prompt"].replace(
        "ecf-classifier", "ecf-eval-classifier"
    )


def test_eval_agents_match_the_service() -> None:
    from ecf_server import claude_eval  # noqa: PLC0415

    for name, (_t, role, _d) in cw.EVAL_AGENTS.items():
        assert claude_eval.agent_name(role, pinned=False) == name
        assert claude_eval.role_of(name) == role
    for name, (_t, role, _d) in cw.AGENTS.items():
        assert claude_eval.agent_name(role, pinned=True) == name


def test_agents_match_the_service_and_ecf_mcp() -> None:
    """The service names these agents (claude_review.ROLE); `ecf claude` must define each, and
    `ecf-mcp --agent` must take each name."""
    from ecf import mcp_server  # noqa: PLC0415
    from ecf_server import claude_review  # noqa: PLC0415

    assert {n: r for n, (_t, r, _d) in cw.AGENTS.items()} == claude_review.ROLE
    assert all(mcp_server.AGENT_NAME.match(n) for n in (*cw.AGENTS, *cw.EVAL_AGENTS))


def test_mark_ready_keeps_other_keys(tmp_path: Path) -> None:
    lay = cw.layout(Paths("t", tmp_path))
    lay.config_dir.mkdir(parents=True)
    lay.work_dir.mkdir(parents=True)
    lay.state.write_text(json.dumps({"userID": "u", "projects": {"/other": {"a": 1}}}))
    cw.mark_ready(lay)
    doc = json.loads(lay.state.read_text())
    assert doc["userID"] == "u" and doc["hasCompletedOnboarding"] is True
    assert doc["projects"]["/other"] == {"a": 1}
    assert doc["projects"][str(lay.work_dir.resolve())]["hasTrustDialogAccepted"] is True
    lay.state.write_text("not json")
    cw.mark_ready(lay)
    assert json.loads(lay.state.read_text())["hasCompletedOnboarding"] is True


def test_mcp_document() -> None:
    d = cw.mcp_doc(Path("/abs/ecf-mcp"), Path("/data/t/run/ecf.sock"))["mcpServers"]["ecf"]
    assert d["command"] == "/abs/ecf-mcp" and d["args"] == ["--stdio"]
    assert d["env"] == {"ECF_PROFILE_TOKEN": "${ECF_PROFILE_TOKEN}",
                        "ECF_SOCKET": "/data/t/run/ecf.sock"}  # fmt: skip


def test_config_is_private(tmp_path: Path) -> None:
    lay = cw.layout(Paths("t", tmp_path))
    cw.write_config(lay, Path("/abs/ecf-mcp"), Path("/data/t/run/ecf.sock"), MODELS)
    for d in (lay.config_dir, lay.work_dir):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
    skill = lay.plugin_dir / "skills" / "ecf-review" / "SKILL.md"
    for f in (lay.settings, lay.mcp_config, lay.state, skill):
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
    env = cw.session_env(cw.layout(Paths("t", tmp_path)), "tok", "atok",
                         cw.telemetry_env(4318, "b"))  # fmt: skip
    assert env["OTEL_LOG_USER_PROMPTS"] == "0" and env["OTEL_LOG_RAW_API_BODIES"] == "0"
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:4318"
    assert env["OTEL_EXPORTER_OTLP_HEADERS"] == "Authorization=Bearer b"
    assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/json"
    assert env["ECF_PROFILE_TOKEN"] == "tok" and env["ECF_AGENT_TOKEN"] == "atok"
    assert env["CLAUDE_CONFIG_DIR"].endswith("claude-config")


def test_run_end_to_end(running: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = tmp_path / "record.txt"
    fake_claude(tmp_path / "bin", record)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    assert (Path(sys.executable).parent / "ecf-mcp").exists()
    said: list[str] = []
    assert cw.run(running, ["--model", "x"], echo=said.append) == 0
    assert said == ["ecf claude: 0 item(s) waiting for review; type /ecf-review to start."]
    rec = dict(line.split("=", 1) for line in record.read_text().splitlines())
    assert rec["TELEMETRY"] == "1" and rec["PROTOCOL"] == "http/json"
    assert rec["OTLP"].startswith("http://127.0.0.1:")
    # the status line reached the service and printed; the export was accepted
    assert Path(f"{record}.status").read_text().strip() == "ecf review · 5h 12% · 7d 95% 200"
    lay = cw.layout(running)
    args: list[str] = json.loads(Path(f"{record}.args").read_text())
    assert args[:3] == ["--strict-mcp-config", "--mcp-config", str(lay.mcp_config)]
    assert args[3:5] == ["--plugin-dir", str(lay.plugin_dir)] and args[5] == "--agents"
    agents = json.loads(args[6])
    assert sorted(agents) == sorted(cw.AGENTS)  # no eval waiting: no ecf-eval-* agents
    sock = agents["ecf-actor"]["mcpServers"][0]["ecf-actor"]["args"][-1]
    assert sock == str(running.socket.absolute())
    assert args[7:] == ["--model", "x"]
    assert rec["CFG"] == str(lay.config_dir) and rec["PROMPTS"] == "0"
    assert Path(rec["PWD"]).resolve() == lay.work_dir.resolve()
    token, agent_token = rec["TOKEN"], rec["AGENT_TOKEN"]
    assert token and agent_token and token != agent_token
    with uds_client(running) as c:  # both session tokens were revoked on exit
        for t in (token, agent_token):
            r = c.get("/v1/status", headers={"Authorization": f"Bearer {t}"})
            assert r.status_code == 401
    assert not (lay.config_dir / "projects").exists()  # transcripts purged
    db = sqlite3.connect(running.db)
    try:
        calls = db.execute("SELECT model, source, input_tokens FROM claude_calls").fetchall()
        plan = db.execute("SELECT five_hour_end, seven_day_end, ended_at IS NOT NULL"
                          " FROM claude_sessions").fetchall()  # fmt: skip
    finally:
        db.close()
    assert calls == [("claude-haiku-4-5-20251001", "main", 1200)]
    assert plan == [(12.5, 95.0, 1)]
    said.clear()
    assert cw.run(running, [], echo=said.append) == 0  # the next session shows the plan usage
    assert said[1].startswith("Plan used after the last review (") and said[1].endswith(
        "UTC): 5-hour 12%, 7-day 95%"
    )


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
    env = cw.session_env(cw.layout(Paths("t", tmp_path)), "tok", "atok", {})
    for gone in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    ):
        assert gone not in env
    assert env["HTTPS_PROXY"] == "http://corp-proxy.example:8080" and "PATH" in env
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "0" and env["DISABLE_AUTOUPDATER"] == "1"


def test_purge_keeps_only_login_and_config(tmp_path: Path) -> None:
    lay = cw.layout(Paths("t", tmp_path))
    cw.write_config(lay, Path("/abs/ecf-mcp"), Path("/data/t/run/ecf.sock"), MODELS)
    for name in (".credentials.json", ".claude.json", "history.jsonl"):
        (lay.config_dir / name).write_text("{}")
    for d in ("projects/p", "debug", "file-history", "plugins/ecf"):
        (lay.config_dir / d).mkdir(parents=True)
    # history.jsonl, projects, debug, file-history, and ecf-plugin (rendered again at each start)
    assert cw.purge_transcripts(lay) == 5
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
