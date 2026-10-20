"""The `codex` provider (eval generate plan, Adapters; build step 3), against a fake `codex` on
PATH that answers as `codex exec --json` does (JSON events on stdout, the answer in the `-o`
file) and checks it was given the direct-mode catalogue. No network, no real Codex."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from ecf.errors import InvalidInputError
from ecf.eval import codex_tool, tools
from ecf.paths import paths_for

FAKE_CODEX = r"""#!{python}
import json, os, sys
args = sys.argv[1:]
sc = json.load(open(os.environ["FAKECODEX_SCENARIO"]))
if args[:1] == ["--version"]:
    print("codex-cli {version}"); sys.exit(0)
if args[:2] == ["exec", "--help"]:
    print("--sandbox --ephemeral --skip-git-repo-check --ignore-user-config --ignore-rules --json"
          " --output-schema -o, --output-last-message -m, --model --disable -c, --config")
    sys.exit(0)
if args[:3] == ["debug", "models", "--bundled"]:
    slugs = sc.get("catalogue", ["gpt-5.6-terra", "gpt-6.1-sol"])
    print(json.dumps({{"models": [{{"slug": s, "tool_mode": "code_mode_only", "shell_type":
                                    "unified_exec"}} for s in slugs]}}))
    sys.exit(0)
prompt = sys.stdin.read()
settings = [args[i + 1] for i, a in enumerate(args) if a == "-c"]
catalog = next(s.split("=", 1)[1] for s in settings if s.startswith("model_catalog_json="))
modes = [m["tool_mode"] for m in json.load(open(json.loads(catalog)))["models"]]
with open(os.environ["FAKECODEX_LOG"], "a") as log:
    log.write(json.dumps({{"argv": args, "home": os.environ.get("CODEX_HOME"),
                          "modes": modes, "cwd": sorted(os.listdir("."))}}) + "\n")
def emit(e): print(json.dumps(e))
emit({{"type": "thread.started", "thread_id": "t"}})
emit({{"type": "turn.started"}})
if sc.get("limit"):
    print("You\u2019ve hit your usage limit"); sys.exit(1)
if sc.get("not_on_plan"):
    msg = "The 'x' model is not supported when using Codex with a ChatGPT account."
    emit({{"type": "error", "message": msg}})
    emit({{"type": "turn.failed", "error": {{"message": msg}}}})
    sys.exit(1)
if sc.get("tool_event") or modes != ["direct"]:
    emit({{"type": "item.completed", "item": {{"id": "i0", "type": "mcp_tool_call"}}}})
answer = {{"word": "ok", "decoy": "none"}} if "decoy.txt" in prompt else sc.get("answer", {{}})
out = args[args.index("-o") + 1]
open(out, "w").write(json.dumps(answer))
emit({{"type": "item.completed", "item": {{"id": "i1", "type": "agent_message",
                                          "text": json.dumps(answer)}}}})
emit({{"type": "turn.completed", "usage": {{"input_tokens": 6098, "output_tokens": 69}}}})
"""


def _adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
             scenario: dict[str, Any] | None = None, model: str = "gpt-5.6-terra",
             version: str = "0.163.0",
             extra_env: dict[str, str] | None = None) -> tools.CliAdapter:  # fmt: skip
    monkeypatch.setenv("ECF_HOME", str(tmp_path / "home"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (tmp_path / "w0").mkdir(exist_ok=True)  # the preflight's throwaway folder
    exe = bin_dir / "codex"
    exe.write_text(FAKE_CODEX.format(python=sys.executable, version=version))
    exe.chmod(0o755)
    (tmp_path / "scenario.json").write_text(json.dumps(scenario or {}))
    paths = paths_for("ecf-test-codex")
    tool = codex_tool.tool_for(paths, model)
    env = dict(tool.env) | {"FAKECODEX_SCENARIO": str(tmp_path / "scenario.json"),
                            "FAKECODEX_LOG": str(tmp_path / "codex.log")}  # fmt: skip
    from dataclasses import replace  # noqa: PLC0415

    environ = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)} | (extra_env or {})
    return tools.CliAdapter(replace(tool, env=env), model, environ)


def _log(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (tmp_path / "codex.log").read_text().splitlines()]


def test_preflight_writes_a_one_model_catalogue_in_direct_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _adapter(tmp_path, monkeypatch)
    a.preflight(tmp_path / "w0")
    path = codex_tool.catalog_path(paths_for("ecf-test-codex"), "gpt-5.6-terra")
    doc = json.loads(path.read_text())
    assert [m["slug"] for m in doc["models"]] == ["gpt-5.6-terra"]
    assert doc["models"][0]["tool_mode"] == "direct"
    assert doc["models"][0]["shell_type"] == "unified_exec"  # the rest kept as shipped
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    d = a.describe()
    assert d["version"] == "0.163.0" and d["catalog_tool_mode"] == "code_mode_only"
    assert len(d["catalog_sha256"]) == 64


def test_the_canary_passes_with_every_tool_off_and_the_catalogue_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _adapter(tmp_path, monkeypatch)
    a.preflight(tmp_path / "w0")
    ok, why = tools.canary(a, tmp_path / "canary")
    assert ok, why
    call = _log(tmp_path)[-1]
    assert call["modes"] == ["direct"]
    assert call["home"] == str(codex_tool.home(paths_for("ecf-test-codex")))
    argv = call["argv"]
    for feature in ("shell_tool", "unified_exec", "apps", "plugins", "multi_agent"):
        assert argv[argv.index(feature) - 1] == "--disable"
    for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--json"):
        assert flag in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert 'cli_auth_credentials_store="keyring"' in argv and 'web_search="disabled"' in argv


def test_a_call_without_the_direct_catalogue_reports_a_tool_event_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _adapter(tmp_path, monkeypatch, {"tool_event": True})
    a.preflight(tmp_path / "w0")
    reply = a.ask("hello", tools.CANARY_SCHEMA, tmp_path / "call")
    assert reply.status == "failed" and reply.detail == "tool event mcp_tool_call"
    ok, why = tools.canary(a, tmp_path / "canary")
    assert not ok and "didn't answer" in why


def test_a_model_the_plan_lacks_and_the_usage_limit_are_told_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _adapter(tmp_path, monkeypatch, {"not_on_plan": True})
    a.preflight(tmp_path / "w0")
    reply = a.ask("hello", tools.CANARY_SCHEMA, tmp_path / "call")
    assert (reply.status, reply.detail) == ("failed", "model not on this ChatGPT plan")
    (tmp_path / "scenario.json").write_text(json.dumps({"limit": True}))
    assert a.ask("hello", tools.CANARY_SCHEMA, tmp_path / "call").status == "limit"


def test_an_answer_that_misfits_the_schema_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _adapter(tmp_path, monkeypatch, {"answer": {"word": "ok"}})
    a.preflight(tmp_path / "w0")
    reply = a.ask("hello", tools.CANARY_SCHEMA, tmp_path / "call")
    assert (reply.status, reply.detail) == ("failed", "schema misfit")
    assert reply.usage == {"input_tokens": 6098, "output_tokens": 69}


def test_preflight_refuses_a_model_missing_from_the_catalogue_or_an_old_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _adapter(tmp_path, monkeypatch, {"catalogue": ["gpt-6-luna"]})
    with pytest.raises(InvalidInputError, match="isn't in this Codex's model catalogue"):
        a.preflight(tmp_path / "w0")
    a = _adapter(tmp_path, monkeypatch, version="0.150.0")
    with pytest.raises(InvalidInputError, match=r"older than 0\.163\.0"):
        a.preflight(tmp_path / "w0")


def test_an_openai_key_in_the_environment_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _adapter(tmp_path, monkeypatch, extra_env={"OPENAI_API_KEY": "sk-test-CANARY"})
    with pytest.raises(InvalidInputError, match="OPENAI_API_KEY is set"):
        a.preflight(tmp_path / "w0")
    assert not (tmp_path / "codex.log").exists()
