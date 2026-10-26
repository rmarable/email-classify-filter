"""The `claude` provider for DEV generated sets (eval generate plan, Adapters; build step 2),
against a fake `claude` on PATH. The fake answers as `claude -p --output-format json` does, with
the answer in `structured_output`; for seeds, emails and grades it asks the fake generator of
test_eval_generate and wraps its answer. No network, no real Claude."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ecf.claude_wrapper import layout
from ecf.cli import app
from ecf.errors import InvalidInputError
from ecf.eval import claude_tool, providers, tools
from ecf.eval import generate as gen
from ecf.eval import grade as grading
from ecf.eval import mix as mixmod
from ecf.eval import report as rep
from ecf.eval.cards import load_cards
from ecf.eval.results import load_result
from ecf.paths import Paths, paths_for
from tests.test_eval_generate import fake_tool

FAKE_CLAUDE = r"""#!{python}
import json, os, subprocess, sys
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("{version} (Claude Code)"); sys.exit(0)
if args[:1] == ["--help"]:
    print("-p, --print --output-format --json-schema --model --system-prompt --tools"
          " --strict-mcp-config --safe-mode --no-session-persistence"); sys.exit(0)
sc = json.load(open(os.environ["FAKEGEN_SCENARIO"]))
prompt = sys.stdin.read()
with open(os.environ["FAKECLAUDE_LOG"], "a") as log:
    log.write(json.dumps({{"argv": args, "config": os.environ.get("CLAUDE_CONFIG_DIR"),
                          "telemetry": os.environ.get("CLAUDE_CODE_ENABLE_TELEMETRY"),
                          "key": "ANTHROPIC_API_KEY" in os.environ}}) + "\n")
model = args[args.index("--model") + 1]
doc = {{"type": "result", "subtype": "success", "is_error": False, "num_turns": 2,
        "usage": {{"input_tokens": 1200, "output_tokens": 178}},
        "modelUsage": {{sc.get("answered_by", model): {{}}}}, "permission_denials": []}}
if sc.get("denied"):
    doc["permission_denials"] = [{{"tool_name": "Read"}}]
if sc.get("refusal"):
    doc["stop_reason"] = "refusal"
elif sc.get("no_output"):
    pass
elif "decoy.txt" in prompt:
    doc["structured_output"] = {{"word": "ok", "decoy": "none"}}
else:
    got = subprocess.run(["fakegen"], input=prompt, capture_output=True, text=True)
    inner = json.loads(got.stdout)
    if inner["status"] == "refused":
        doc["stop_reason"] = "refusal"
    else:
        doc["structured_output"] = inner["answer"]
print(json.dumps(doc))
"""


Faked = tuple[tools.CliTool, dict[str, str]]


def fake_claude(tmp_path: Path, scenario: dict[str, Any], version: str = "2.1.304",
                model: str = "claude-haiku-5-5") -> Faked:  # fmt: skip
    gen_tool, environ = fake_tool(tmp_path, scenario)  # the fakegen it delegates to
    exe = tmp_path / "bin" / "claude"
    exe.write_text(FAKE_CLAUDE.format(python=sys.executable, version=version))
    exe.chmod(0o755)
    tool = claude_tool.tool_for(paths_for("ecf-test-claude-tool"), model)
    env = dict(tool.env) | dict(gen_tool.env) | {"FAKECLAUDE_LOG": str(tmp_path / "claude.log")}
    return replace(tool, env=env), environ


def logged(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (tmp_path / "claude.log").read_text().splitlines()]


def adapter(tmp_path: Path, scenario: dict[str, Any] | None = None,
            model: str = "claude-haiku-5-5") -> tools.CliAdapter:  # fmt: skip
    tool, environ = fake_claude(tmp_path, scenario or {}, model=model)
    a = tools.CliAdapter(tool, model, environ)
    a.preflight(tmp_path)
    return a


def test_the_call_is_tool_less_on_ecfs_sign_in(tmp_path: Path) -> None:
    ok, why = tools.canary(adapter(tmp_path), tmp_path / "w")
    assert ok, why
    call = logged(tmp_path)[-1]
    argv = call["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert {"-p", "--strict-mcp-config", "--safe-mode", "--no-session-persistence"} <= set(argv)
    assert argv[argv.index("--model") + 1] == "claude-haiku-5-5"
    assert json.loads(argv[argv.index("--json-schema") + 1]) == tools.CANARY_SCHEMA
    assert argv[argv.index("--system-prompt") + 1] == claude_tool.SYSTEM_PROMPT
    want = layout(paths_for("ecf-test-claude-tool")).config_dir
    assert call["config"] == str(want) and call["telemetry"] == "0" and not call["key"]


def test_a_tool_request_another_model_or_no_answer_fails(tmp_path: Path) -> None:
    schema = tools.CANARY_SCHEMA
    r = adapter(tmp_path, {"denied": True}).ask(tools.CANARY_PROMPT, schema, tmp_path / "w")
    assert (r.status, r.detail, r.items) == ("failed", "tool event tool:Read", ("tool:Read",))
    r = adapter(tmp_path, {"answered_by": "claude-opus-5-5"}).ask(
        tools.CANARY_PROMPT, schema, tmp_path / "w"
    )
    assert (r.status, r.detail) == ("failed", "answered on another model")
    r = adapter(tmp_path, {"no_output": True}).ask(tools.CANARY_PROMPT, schema, tmp_path / "w")
    assert (r.status, r.detail) == ("failed", "no structured output")
    r = adapter(tmp_path, {"refusal": True}).ask(tools.CANARY_PROMPT, schema, tmp_path / "w")
    assert r.status == "refused"
    assert r.usage == {"input_tokens": 1200, "output_tokens": 178}


def test_an_old_claude_code_is_refused(tmp_path: Path) -> None:
    tool, environ = fake_claude(tmp_path, {}, version="2.1.200")
    with pytest.raises(InvalidInputError, match=r"older than 2\.1\.303"):
        tools.CliAdapter(tool, "claude-haiku-5-5", environ).preflight(tmp_path)


def test_an_api_key_in_the_environment_is_refused(tmp_path: Path) -> None:
    tool, environ = fake_claude(tmp_path, {})
    a = tools.CliAdapter(tool, "claude-haiku-5-5", environ | {"ANTHROPIC_API_KEY": "x"})
    with pytest.raises(InvalidInputError, match="ANTHROPIC_API_KEY is set"):
        a.preflight(tmp_path)


def test_a_dev_set_end_to_end_with_the_claude_provider(tmp_path: Path) -> None:
    """Generate 20 with one model, grade with another: the DEV plumbing, on the fake."""
    root = tmp_path / "set"
    gen.start(root, resume=False)
    s = gen.Run(root, mixmod.default(), 20, adapter(tmp_path), "dev").run(resume=False)
    assert s.state == "complete" and s.kept == 20
    assert {c.generator for c in load_cards(root / "cases")} == {"claude-haiku-5-5"}
    grader = adapter(tmp_path, model="claude-sonnet-5-5")
    path, counts, _detail = grading.Grading(root, grader).run(resume=False)
    assert path is not None and counts == {"graded": 20}
    assert load_result(path).pair == "grader:claude-sonnet-5-5"
    assert gen.verify(root)["generator"]["version"] == "2.1.304"


def test_the_dev_commands_check_the_tool_then_generate_and_grade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(model: str, _paths: Paths) -> tools.CliAdapter:
        tool, environ = fake_claude(tmp_path, {}, model=model)
        return tools.CliAdapter(tool, model, environ)

    monkeypatch.setitem(providers.BUILT, "claude", fake)
    root = tmp_path / "set"
    r = CliRunner().invoke(app, ["eval", "generate", "--dev", "--root", str(root), "--count",
                                 "20", "--yes"])  # fmt: skip
    assert r.exit_code == 0, r.output
    assert "claude claude-haiku-5-5: tool-less check passed" in r.output
    assert "complete: kept 20 of 20" in r.output
    r = CliRunner().invoke(app, ["eval", "grade", "--root", str(root), "--yes"])
    assert r.exit_code == 0, r.output
    assert "claude claude-sonnet-5-5: tool-less check passed" in r.output
    assert "graded 20" in r.output
    r = CliRunner().invoke(app, ["eval", "report", "--root", str(root)])
    assert r.exit_code == 0 and r.output.startswith(rep.DEV_BANNER), r.output


def test_a_relative_root_is_resolved_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(model: str, _paths: Paths) -> tools.CliAdapter:
        tool, environ = fake_claude(tmp_path, {}, model=model)
        return tools.CliAdapter(tool, model, environ)

    seen: list[Path] = []
    start = gen.start

    def spy(root: Path, *, resume: bool) -> None:
        seen.append(root)
        start(root, resume=resume)

    monkeypatch.setitem(providers.BUILT, "claude", fake)
    monkeypatch.setattr(gen, "start", spy)
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["eval", "generate", "--dev", "--root", "set", "--count", "20",
                                 "--yes"])  # fmt: skip
    assert r.exit_code == 0, r.output
    assert seen == [tmp_path.resolve() / "set"]


def test_a_failed_canary_stops_before_the_set_folder_is_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(model: str, _paths: Paths) -> tools.CliAdapter:
        tool, environ = fake_claude(tmp_path, {"denied": True}, model=model)
        return tools.CliAdapter(tool, model, environ)

    monkeypatch.setitem(providers.BUILT, "claude", fake)
    r = CliRunner().invoke(app, ["eval", "generate", "--dev", "--root", str(tmp_path / "s"),
                                 "--yes"])  # fmt: skip
    assert r.exit_code != 0 and "failed the tool-less check" in str(r.exception)
    assert not (tmp_path / "s").exists()
