# Agent spawn guard: refuse `isolation` and `model` on spawns

- **Status:** done (2026-10-13; OD-497): merged `f7ecca6`; live check passed (SPEC §21.2). Part of v3.0.0 (step 1d).
- **Base:** `main` at `47ae2ce`.
- **Evidence:** B0 run 2 `845303ba` (SPEC §21.2, "Baseline on the 300-card set", Dispatch): the
  main session (Haiku 5.5) launched round 1's `ecf-classifier` spawn with `isolation: "remote"`,
  which the skill doesn't ask for. Claude Code 2.1.296 ran it as a local background agent (local
  transcript; 10 `get_message` and 12 `record_classification` calls on the local MCP server; all
  376 claims recorded). Of 152 spawns in the eval transcripts on disk (`cleanupPeriodDays` 1),
  only this one set a parameter beyond `subagent_type`, `description`, `prompt`.
- **Why:** where a spawn runs, and on which model, must not depend on the main session choosing
  well. `isolation: "remote"` could move a spawn off this computer; the spawn prompt holds only
  item ids and claim tokens and mail text comes from `get_message` over the local socket, so a
  remote spawn would most likely stall rather than leak (reasoning, untested). `model` overrides
  the agent's pinned model (code.claude.com/docs/en/sub-agents, read 2026-10-13: per-invocation
  model first), bypassing the role pins in `/ecf-review` and the eval's measured models. The
  service reads only `api_request` telemetry (`ecf_server/telemetry.py:7`), so it can't see spawn
  parameters; the model check would catch a wrong model only after the call.

## Facts (code.claude.com, read 2026-10-13 by a docs agent; not re-checked by Claude: "(rv)")

- Permission rules can match a direct field of a tool's input: `Agent(isolation:worktree)`,
  `Agent(model:opus)`; `*` matches any explicit value; an omitted field is never matched; values
  are compared before normalisation (docs/en/permissions, "Match by input parameter") (rv).
- Deny beats allow, and holds under `dontAsk` (docs/en/permissions) (rv).
- `PreToolUse` matches `Agent` by name (docs/en/hooks, "PreToolUse"). A hook that exits 2 blocks
  the call before permission rules, and its stderr is shown to Claude. A hook that can't start,
  times out or exits with another code lets the call through, unless the hook sets
  `"onFailure": "block"` (Claude Code ≥ 2.1.295; default timeout 600 s) (docs/en/hooks) (rv).
- `disableAllHooks: false` in a project `.claude/settings.json` overrides `true` in user settings;
  that a project `true` overrides a user `false` the same way is the docs agent's inference (rv).
- A plugin mod (`tool.check`) can approve a call a deny rule refused, except managed deny rules
  (docs/en/permissions, "Extend permissions with hooks") (rv).
- Not documented: whether `isolation: "remote"` exists as a value (only `worktree` is); whether a
  background Agent launch fires `PreToolUse`; whether `Agent(model:*)` matches an empty string;
  the Agent input schema (the hooks page's table lists `prompt`, `description`, `subagent_type`,
  `model`, not `isolation`).

## Changes

1. **Deny rules** (`ecf/claude_wrapper.py` `DENIED_TOOLS`): add `Agent(isolation:*)` and
   `Agent(model:*)`. Primary layer: enforced by Claude Code's permission check, no ecf process.
2. **Allow-list hook** (new `ecf/spawn_guard.py`; `settings_doc` adds `hooks.PreToolUse` with
   matcher `Agent`, command `sys.executable -m ecf.spawn_guard` as `statusline_command` does,
   `"onFailure": "block"`, timeout 10 s): reads the hook JSON on stdin; refuses (exit 2, a fixed
   reason on stderr) any `tool_input` with a key outside `subagent_type`, `description`,
   `prompt`, and any input it can't parse; exit 0 otherwise. Catches what the rules can't: an
   empty-string value, `effort`, `name`, and parameters Claude Code adds later. Standard library
   only; no service call.
3. **Skill text** (`/ecf-eval`, `/ecf-review`): a spawn sets only `subagent_type`, `description`
   and `prompt`.
4. **Work folder check** (`claude_wrapper`): refuse to start if `claude-work/.claude` exists (a
   project settings file there could turn hooks off). The deny rules don't depend on hooks.
5. **No plugin mods:** a test asserts the rendered plugin has only `.claude-plugin` and `skills`.

6. **Floor** (operator 2026-10-13): `MIN_CLAUDE` 2.1.295, for `onFailure`.
7. **Diff review F1-F10** (operator accepted all, 2026-10-13; `state-archive/v3.0.0/spawn-guard-review.md`):
   no claude.ai plugins (`syncClaudeAiPlugins`/`syncClaudeAiSkills` false, `enabledPlugins` {},
   `plugins` purged); hook and status line run with `python -I`; `--setting-sources user`;
   refuse a work folder inside a git repository, `doctor` row; timeout 30 s; the hook checks
   `tool_name` and refuses blank values; the work-folder check runs before the service session.

Not used: `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` (blocks per-call `model`, but the docs don't say it
keeps each agent's own `model` from `--agents`).

## Tests

- Offline: `test_claude_wrapper.py` (the two deny rules; the hook entry with `onFailure: block`;
  the work-folder refusal; the plugin's contents); new `test_spawn_guard.py` (the three keys pass;
  each extra key, an empty string, malformed JSON and missing `tool_input` are refused; exit
  codes).
- **Live check** (real service, operator's go-ahead, uses the plan; throwaway prompts in the
  scratchpad): on the `accept` install, first a logging run (the hook records the stdin JSON) to
  confirm the matcher, that background spawns fire the hook, the `isolation` field's name, and
  whether Claude Code itself adds `run_in_background` (refused until then; operator 2026-10-13);
  count refusals of keys Claude Code adds itself (`run_in_background`, `name`); confirm
  `--setting-sources user` still loads ecf's settings, status line and plugin; restart the `accept`
  service first (the skill text changes the plugin digest, so the old service refuses the new
  client; later eval runs differ from B0 by this one input); then ask the main session to spawn `ecf-classifier` with `isolation: "remote"`, then with
  `model: "opus"`: both refused, a plain spawn still works. Result in SPEC §21.2.

## Docs

SPEC §10.3 session lock-down (the rules, the hook, the work-folder check), §21.2 the live check;
OD-497 row; CHANGELOG one line.

## Review and release

Changes the session lock-down, not the classifier, rules, policy, facts or eval scoring, so no
Phase R; two read-only Fable agents (safety and correctness) review the diff before the gate.
Release: bundled into v3.0.0 (operator 2026-10-13), as step 1d of `planning-docs/v3.0.0-PLANNING.md`.
