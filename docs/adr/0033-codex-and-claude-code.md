# ADR 0033: Codex works beside Claude Code; Claude keeps main

- **Status:** accepted (operator decisions 2026-10-17 and 2026-10-18, OD-532)
- **Context source:** the session plan of 2026-10-17 (not committed); OpenAI's Codex docs, fetched
  2026-10-17 and 2026-10-18 (Facts below)

## Context

The operator wants to use OpenAI Codex on this repository next to Claude Code. Every rule lived in
files only Claude reads: `CLAUDE.md`, `.claude/rules/*.md`, and the gitignored `CLAUDE-STATE.md`
and `state-archive/`. Codex reads `AGENTS.md`, which didn't exist, so it would have started with
none of the rules. Codex keeps no memory between runs beyond its instruction files and resumable
session transcripts, and by default can't write outside its own workspace.

## Decision

- **One set of rules** in `AGENTS.md`, moved word for word from `CLAUDE.md`, which now imports it
  (`@AGENTS.md`) and keeps only what Claude alone does (session state, Codex branches, path-rule
  loading). The two path-scoped rules' text moves into `AGENTS.md` too; the `.claude/rules/` files
  keep their `paths:` and point there.
- **Reserved for Claude Code**, stated at the top of `AGENTS.md`: anything on `main`, tags,
  releases and version bumps, the macOS merge gate, real-service tests, Keychain, launchd and
  Slack, evals, the main checkout and the accept service, and reading `state-archive/`,
  `CLAUDE-STATE.md` or `CLAUDE.local.md`.
- **Codex works on branches** `codex/<topic>`, one per session, each in its own worktree; it may
  commit and push its branch after the operator's OK. Any number of Codex sessions or subagents.
- **Claude merges** every Codex branch, after the operator's OK, with the usual gate and CI checks.
- **Handoff in the branch:** the newest commit's message ends with a `Handoff: ready` block (done,
  not done, tests, CI, CHANGELOG line, numbers). A SessionStart hook
  (`.claude/hooks/codex-handoffs.sh`) lists unmerged `codex/*` branches with their blocks when a
  Claude session starts; Claude tells the operator and waits. No watcher.
- **Numbers:** Codex writes `OD-NEW` / `ADR-NEW`; Claude assigns real numbers at merge.
- **Trailer:** Codex commits end with `Co-Authored-By: Codex <noreply@openai.com>`; still exactly
  one trailer, never naming the model.
- **Reviews:** Phase R and diff reviews stay Fable only. Codex doesn't run them, and doesn't start
  building a plan that needs Phase R until it is done.
- **Archive not shared:** Codex doesn't read `state-archive/`. Decisions are in SPEC and
  `docs/adr/`, which it can read.

## Alternatives considered

- **A gitignored handoff file in the main checkout** (the plan's first choice): rejected. By
  default Codex writes only inside its workspace and temp directories, so a session in its own
  worktree can't reach the file; granting it the main checkout would break the reserved list.
- **A Claude session that watches for handoffs** and alerts the operator: rejected (operator,
  2026-10-17); it only works while that session is open and adds a running process. Startup
  reading covers it.
- **Codex merges too**, under the same gate rules: rejected; one owner for main, the gate and the
  accept service.
- **Nested `AGENTS.md` files** for the path rules: rejected. Codex reads them only in directories
  between the project root and where it starts, so a session started at the root never sees them.
- **Codex as a reviewer**, in four forms considered on 2026-10-18: Claude adds read-only
  `codex exec --sandbox read-only --output-schema` lenses to its own panel; Codex runs its own
  panel; `codex review` for diffs only; a Codex lens in place of a Fable one. All rejected for now.
  If the operator later wants a second model family on the panel, the first form is the way in: one
  P-list, the same spot-checks, and the operator still decides each finding.

## Consequences

- `AGENTS.md` is about 21 KB, under Codex's default 32 KiB instruction limit; it must stay under it,
  or Codex silently stops reading the rest.
- Claude now loads the two path rules all the time (through `@AGENTS.md`), a small context cost.
- Each Codex branch costs one Claude session to land it.
- Plans and code go to OpenAI when Codex works on them. Mail never does (it is never on disk).
- **Revisit** if Codex reports it can't reference the archive of stated decisions
  (`state-archive/`), or if the operator changes their mind.

## Facts

- Codex reads a global `~/.codex/AGENTS.md`, then one file per directory from the project root down
  to the directory it starts in, joined in that order, later ones overriding earlier ones; it stops
  at `project_doc_max_bytes` (32 KiB by default) and reads the files again on every run
  (learn.chatgpt.com/docs/agent-configuration/agents-md, fetched 2026-10-17).
- Sessions can be resumed (`codex resume`, `/resume`); `codex exec` takes `--sandbox read-only`,
  `--output-schema` and `--model`; `codex review` reviews uncommitted changes, a branch diff or a
  commit (learn.chatgpt.com/docs/developer-commands, fetched 2026-10-18).
- The default `:workspace` profile writes only inside the active workspace roots and system temp
  directories, keeps the workspace's `.git`, `.agents`, `.codex` and `.aws` read-only, and has the
  network off by default (learn.chatgpt.com/docs/permissions, fetched 2026-10-18).
- Unverified: whether a commit in a linked worktree (whose git metadata is in the main checkout's
  `.git`) needs the operator's approval in Codex; whether Codex's documented subagents can run a
  review panel.
