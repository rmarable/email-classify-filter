"""The `codex` provider for PROD generated sets (eval generate plan, Adapters; build step 3).

`codex exec` with structured output (`--output-schema`, the answer in the `-o` file), every tool
it can switch off switched off, the read-only sandbox, nothing saved, on ecf's own Codex sign-in
(`CODEX_HOME` under the install's data folder, credentials in the Keychain), under the
allow-listed environment. Checked on Codex CLI 0.163.0 (plan Facts, 2026-10-20).

Codex's model catalogue gives each current model `tool_mode: code_mode_only`, which wins over
the `--disable` switches: it adds the MCP resource tools and a JavaScript `exec` tool even with no
MCP server configured. So the preflight writes a one-model catalogue, the model's entry from the
catalogue shipped inside this Codex binary (`codex debug models --bundled`, no network) with
`tool_mode` set to `direct`, and every call passes it with `-c model_catalog_json=...` (a supplied
catalogue replaces the server's). With `--json`, a call may report only `agent_message`,
`reasoning` and `error` items; anything else fails it.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast

from ecf.errors import InvalidInputError
from ecf.eval import tools
from ecf.paths import Paths

MIN_VERSION = (0, 163, 0)
ALLOWED_ITEMS = frozenset({"agent_message", "reasoning", "error"})
DISABLED = ("shell_tool", "unified_exec", "apps", "plugins", "remote_plugin", "multi_agent",
            "image_generation", "browser_use", "computer_use", "goals", "sleep_tool",
            "tool_suggest", "skill_search", "view_image")  # fmt: skip
SETTINGS = ('cli_auth_credentials_store="keyring"', 'web_search="disabled"',
            "tools.experimental_request_user_input.enabled=false",
            "include_environment_context=false", "include_environment_context_time=false",
            "analytics.enabled=false", 'otel.metrics_exporter="none"', "feedback.enabled=false",
            "check_for_update_on_startup=false", 'history.persistence="none"',
            "memories.generate_memories=false", "memories.use_memories=false")  # fmt: skip
# the 400 Codex returns for a model the account's ChatGPT plan doesn't include (plan Facts)
NOT_ON_PLAN = "not supported when using Codex with a ChatGPT account"


def home(paths: Paths) -> Path:
    return paths.data_dir / "codex-home"


def catalog_path(paths: Paths, model: str) -> Path:
    return home(paths) / f"ecf-catalog-{model}.json"


def _argv(paths: Paths, model: str, schema: Path, out: Path) -> list[str]:
    argv = ["exec", "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check",
            "--ignore-user-config", "--ignore-rules", "--json", "--output-schema", str(schema),
            "-o", str(out), "-m", model]  # fmt: skip
    for feature in DISABLED:
        argv += ["--disable", feature]
    for setting in (*SETTINGS, f"model_catalog_json={json.dumps(str(catalog_path(paths, model)))}"):
        argv += ["-c", setting]
    return argv


def write_catalog(paths: Paths, model: str, exe: str, env: dict[str, str], workdir: Path
                  ) -> dict[str, Any]:  # fmt: skip
    """The one-model catalogue in direct tool mode, from the binary's bundled catalogue."""
    ran = tools.run_cli([exe, "debug", "models", "--bundled"], "", env, workdir, timeout=60)
    doc = tools.parse_object(ran.stdout)
    models = cast(list[dict[str, Any]], (doc or {}).get("models") or [])
    entry = next((m for m in models if m.get("slug") == model), None)
    if entry is None:
        raise InvalidInputError(f"{model} isn't in this Codex's model catalogue (`codex debug"
                                " models --bundled`): update Codex, or pick another"
                                " model")  # fmt: skip
    if "tool_mode" not in entry:
        raise InvalidInputError(f"{model}'s catalogue entry has no tool_mode: this Codex isn't"
                                " the one ecf was checked against")  # fmt: skip
    text = json.dumps({"models": [entry | {"tool_mode": "direct"}]}, sort_keys=True)
    path = catalog_path(paths, model)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return {"catalog_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "catalog_tool_mode": entry["tool_mode"]}  # fmt: skip


def read(ran: tools.Ran, out: Path) -> tools.Reply:
    items: list[str] = []
    usage: dict[str, int] = {}
    failed = ""
    for line in ran.stdout.decode("utf-8", "replace").splitlines():
        event = tools.parse_object(line)
        if event is None:
            continue
        kind = event.get("type")
        item = event.get("item")
        if kind in {"item.started", "item.completed"} and isinstance(item, dict):
            items.append(str(cast(dict[str, Any], item).get("type", "?")))
        elif kind == "turn.completed":
            raw = cast(dict[str, Any], event.get("usage") or {})
            usage = {k: v for k, v in raw.items() if isinstance(v, int)}
        elif kind in {"error", "turn.failed"}:
            failed = ("model not on this ChatGPT plan" if NOT_ON_PLAN in line
                      else failed or f"codex {kind}")  # fmt: skip
    seen = tuple(dict.fromkeys(items))
    if failed:
        return tools.Reply("failed", detail=failed, usage=usage, items=seen)
    if ran.code != 0 or not out.exists():
        return tools.Reply("failed", detail=f"codex exit {ran.code}, no answer", usage=usage,
                           items=seen)  # fmt: skip
    answer = tools.parse_object(out.read_bytes())
    if answer is None:
        return tools.Reply("failed", detail="not one JSON object", usage=usage, items=seen)
    return tools.Reply("ok", answer, usage=usage, items=seen)


def tool_for(paths: Paths, model: str) -> tools.CliTool:
    return tools.CliTool(
        provider="codex", executable="codex",
        argv=lambda m, schema, out: _argv(paths, m, schema, out), read=read,
        env={"CODEX_HOME": str(home(paths))}, min_version=MIN_VERSION,
        help_argv=(("exec", "--help"),), allowed_items=ALLOWED_ITEMS,
        prepare=lambda exe, env, workdir: write_catalog(paths, model, exe, dict(env), workdir),
    )  # fmt: skip


def adapter(model: str, paths: Paths) -> tools.CliAdapter:
    return tools.CliAdapter(tool_for(paths, model), model)
