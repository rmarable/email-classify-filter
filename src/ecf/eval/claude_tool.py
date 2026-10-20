"""The `claude` provider for DEV generated sets (eval generate plan, Adapters; build step 2).

`claude -p` with structured output, every tool off (`--tools ""`), no MCP servers, no session
saved, ecf's own system prompt, on ecf's Claude sign-in (the install's `CLAUDE_CONFIG_DIR`, set
up by `ecf claude --login`), under the allow-listed environment with telemetry off. Checked on
Claude Code 2.1.303 (plan Facts, 2026-10-20): the answer is in `structured_output` when
`subtype` is `success`. A call that reports a permission denial (a tool asked for) or answers on
another model than the one named fails.

The config folder is shared with `ecf claude` sessions, so it isn't purged after a run (a live
session's files would go too); `--no-session-persistence` keeps these calls' transcripts out.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from ecf.claude_wrapper import PRIVACY_ENV, layout
from ecf.errors import InvalidInputError
from ecf.eval import tools
from ecf.paths import Paths

MIN_VERSION = (2, 1, 303)
# ecf's adapter text: format only, no guidance on content (decision 1)
SYSTEM_PROMPT = "Answer with JSON that matches the given schema, and nothing else."


def _argv(model: str, schema: Path, _out: Path) -> list[str]:
    text = schema.read_text(encoding="utf-8") if schema.exists() else "{}"
    return ["-p", "--output-format", "json", "--json-schema", text, "--model", model,
            "--system-prompt", SYSTEM_PROMPT, "--tools", "", "--strict-mcp-config", "--safe-mode",
            "--no-session-persistence"]  # fmt: skip


def read(ran: tools.Ran, _out: Path, model: str) -> tools.Reply:  # noqa: PLR0911 - one per outcome
    doc = tools.parse_object(ran.stdout)
    if doc is None:
        return tools.Reply("failed", detail=f"not one JSON object (exit {ran.code})")
    raw_usage = cast(dict[str, Any], doc.get("usage") or {})
    usage = {k: int(v) for k, v in raw_usage.items()
             if k.endswith("tokens") and isinstance(v, int)}  # fmt: skip
    denials = cast(list[dict[str, Any]], doc.get("permission_denials") or [])
    items = tuple(f"tool:{d.get('tool_name', '?')}" for d in denials)
    models = set(cast(dict[str, Any], doc.get("modelUsage") or {}))
    if items:
        return tools.Reply("failed", detail="tool asked for", usage=usage, items=items)
    if models and model not in models:
        return tools.Reply("failed", detail="answered on another model", usage=usage)
    if doc.get("stop_reason") == "refusal":
        return tools.Reply("refused", detail="model refusal", usage=usage)
    if doc.get("subtype") != "success" or doc.get("is_error"):
        return tools.Reply("failed", detail=f"claude {doc.get('subtype') or 'error'}",
                           usage=usage)  # fmt: skip
    answer = doc.get("structured_output")
    if not isinstance(answer, dict):
        return tools.Reply("failed", detail="no structured output", usage=usage)
    return tools.Reply("ok", cast(dict[str, Any], answer), usage=usage)


def tool_for(paths: Paths, model: str) -> tools.CliTool:
    env = dict(PRIVACY_ENV) | {"CLAUDE_CONFIG_DIR": str(layout(paths).config_dir)}
    return tools.CliTool(provider="claude", executable="claude", argv=_argv,
                         read=lambda ran, out: read(ran, out, model), env=env,
                         min_version=MIN_VERSION, allowed_items=frozenset())  # fmt: skip


def adapter(model: str, paths: Paths) -> tools.CliAdapter:
    if not model.startswith("claude-"):
        raise InvalidInputError(f"{model!r} isn't a full Claude model id (claude-haiku-5-5, ...)")
    return tools.CliAdapter(tool_for(paths, model), model)
