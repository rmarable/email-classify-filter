"""The `PreToolUse` hook on Agent spawns in an `ecf claude` session (SPEC §10.3; OD-497):
`python -m ecf.spawn_guard`, run by Claude Code with the hook's JSON on stdin.

A spawn may set only `subagent_type`, `description` and `prompt`. Any other key (`isolation`,
`model`, `effort`, `run_in_background`, an empty value, a parameter Claude Code adds later) and any
input it can't read is refused: exit 2, with a fixed reason on stderr, which Claude sees. The deny
rules `Agent(isolation:*)` and `Agent(model:*)` are the first layer; this catches what they can't
match. The hook is set with `onFailure: block`, so a hook that can't start or times out refuses the
spawn too. Standard library only, so it starts fast; no service call, and it prints nothing from
the input.
"""

from __future__ import annotations

import json
import sys
from typing import Any, cast

ALLOWED_KEYS = frozenset({"subagent_type", "description", "prompt"})
REASON = ("ecf: an Agent spawn may set only subagent_type, description and prompt; spawn again"
          " with just those three.")  # fmt: skip


def allowed(data: Any) -> bool:
    """True when the hook input is an Agent call whose `tool_input` names a subagent type and
    has only the three keys, each a string with more than whitespace."""
    if not isinstance(data, dict):
        return False
    if cast(dict[str, Any], data).get("tool_name") != "Agent":
        return False
    tool_input: Any = cast(dict[str, Any], data).get("tool_input")
    if not isinstance(tool_input, dict) or not tool_input:
        return False
    fields = cast(dict[str, Any], tool_input)
    if "subagent_type" not in fields or not set(fields) <= ALLOWED_KEYS:
        return False
    return all(isinstance(v, str) and v.strip() for v in fields.values())


def main() -> int:
    try:
        data: Any = json.loads(sys.stdin.read())
    except (ValueError, UnicodeDecodeError):
        data = None
    if allowed(data):
        return 0
    sys.stderr.write(REASON + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
