# Trim the main session's built-in tools (draft 1)

Status: built and adopted 2026-10-28 (OD-571; SPEC §10.3 "Built-in tools loaded"). Step 0: `/context` 17.2k to 7.1k (dev), 29.2k to 6.1k (logged in). Pilot `8f93562a`: 334/394, unsafe 0, 5 compactions (14-17 before), 653 s (branch runs before 713-864 s, controls 609-711 s), all claims recorded, spawn guard still refuses. Diff review R1-R11 + N1 on Opus (Fable limit, OD-562).

## Context

The OD-564 pilot's `/context` (Claude Code 2.1.312, 2026-10-28) showed an `ecf claude` session
starting at 29.2k: system tools 22.7k, system prompt 3.2k, skills 2.8k. With the 100k window
(`SESSION_ENV`, `src/ecf/claude_wrapper.py:153`) Claude Code compacts near 67k, so only ~38k of
eval traffic fits between compactions. Compaction pauses cost ~21% wall time in the step 3 runs
(state-archive/main-session-cost/step3-results.md). Cutting the tool floor gives more room per
compaction cycle: fewer compactions, less wall time, and a smaller prompt on every main request.

What the main session needs, from the skills (`src/ecf/data/plugin/skills/*/SKILL.md`): the three
ecf MCP tools (`MAIN_TOOLS`, claude_wrapper.py:47) and the **Agent** tool to spawn ecf agents.
Nothing else. Today the session is launched with Claude Code's full default built-in set
(claude_wrapper.py:468-471); Bash, Edit, Write, WebFetch, WebSearch, NotebookEdit are only in
the deny list (`DENIED_TOOLS`, :54), and Read, Glob, Grep, Task*, Skill, ToolSearch,
AskUserQuestion, plan-mode, Cron, Monitor, LSP etc. are loaded and merely refused by `dontAsk`.

## Recommendation

Launch the session with `--tools "Agent"` (Claude Code's `--tools`: "Specify the list of
available tools from the built-in set", `claude --help`, 2.1.312; already used as `--tools ""`
in `src/ecf/eval/claude_tool.py:32`). The deny and allow lists stay as they are: second layer.
It is also a security tightening: tools that aren't loaded can't be called at all.

## Steps

0. **Measure first (dev service, no mail, no model calls; read-only on the repo).** In a worktree,
   `uv run ecf-server dev`, then start `claude` with the same args the wrapper builds plus
   `--tools "Agent"` (scratchpad script, throwaway) and read `/context`. Record: system tools
   before/after, whether the ecf MCP tools still load (and whether they're deferred behind
   ToolSearch, which would then be missing), whether the `/ecf-eval` and `/ecf-review` slash
   commands still expand without the Skill tool, and whether the custom agents (`--agents`)
   still get their MCP tools. Unverified until then: whether `--tools` removes the descriptions
   from context (step0b3-docs.md:28 says unverified). If `--tools` doesn't cut the count, try
   adding the remaining built-ins to `--disallowedTools` instead (docs say that removes them).
   Also look at the 2.8k skills line: if it's Claude Code's bundled skills, check for a setting
   that hides them (unverified; report, don't build, if found).
1. **Build** (worktree off main): `MAIN_BUILTIN_TOOLS = ["Agent"]` beside `MAIN_TOOLS`, passed as
   `--tools` in `run()`; comment cites the measurement. Tests in `tests/test_claude_wrapper*.py`
   (args contain `--tools Agent`; `--tools` stays out of `PASSTHROUGH_*`). SPEC §10.3 paragraph
   + new OD row (next free OD-570; re-check), CHANGELOG line. Targeted tests, ruff, pyright.
2. **Review**: 2 read-only Fable agents (safety and correctness, design), Opus if Fable is out
   of credits (OD-562). This touches the eval session setup only, so the small size.
3. **Pilot (real-service, go-ahead needed)**: one `--not-for-gate` standard 394 run from the
   worktree install, compared with B1/B2/B3 (step3-results.md): compaction count, wall time,
   main+compact cost, unsafe 0, claims all recorded. `/context` at session start recorded first.
4. Full suite (macOS gate, 0 skipped), commit, merge, push, CI: each with the operator's OK.

## Expected effect (to confirm in step 0)

If the tools drop from 22.7k to a few k, the room before compaction grows from ~38k to ~55k,
roughly a third fewer compactions. That is an estimate, not a measurement.

## Verification

- Step 0 `/context` figures before and after, written to state-archive/main-session-cost/.
- `uv run pytest tests/test_claude_wrapper*.py`, ruff, pyright, lint-imports; full gate before merge.
- Pilot run: compactions and wall time vs the three branch runs; unsafe 0; every claim recorded.

## Files

- `src/ecf/claude_wrapper.py` (args in `run()`, new constant)
- `tests/test_claude_wrapper*.py`
- `SPEC.md` §10.3 + OD row, `CHANGELOG.md`
