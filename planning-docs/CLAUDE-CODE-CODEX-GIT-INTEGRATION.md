# Claude Code and Codex in one Git repository: how and why

Status: reference, written 2026-10-19 from ecf's setup (OD-532, ADR 0033, merged 8129f37). It
explains how and why OpenAI Codex works in a repository beside Claude Code: one set of rules, Codex
on its own branches, Claude Code as the only agent that lands work on the main branch. The steps
are in a skill (How to set it up).

## The model

- **One rule file both agents read.** Codex reads `AGENTS.md`; Claude Code reads `CLAUDE.md`, which
  imports `AGENTS.md` with an `@AGENTS.md` line. Shared rules live only in `AGENTS.md`; `CLAUDE.md`
  keeps what Claude alone does.
- **Codex works on branches** named `codex/<topic>`, each in its own worktree
  (`../<project>-codex-<topic>`, made from `origin/<main>`). It may commit and push its branch, with
  the operator's OK. Any number of Codex sessions, one branch and worktree each.
- **Claude Code alone touches `<main>`**: merge, push, tags, releases, the merge gate, anything
  needing live credentials or services. It merges each Codex branch after the operator's OK.
- **Handoff in the branch itself.** When a branch is ready, its newest commit message ends with a
  `Handoff: ready` block. A Claude Code SessionStart hook lists unmerged `codex/*` branches and
  their blocks when a session starts; Claude tells the operator and waits. No watcher process.
- **Numbers assigned at merge.** If the project numbers decisions (ADRs, decision rows), Codex
  writes placeholders (`ADR-NEW`, `OD-NEW-1`); Claude assigns real numbers when it merges, so
  parallel sessions can't collide.
- **One trailer per agent**, so history shows who wrote what:
  `Co-Authored-By: Claude <noreply@anthropic.com>` or `Co-Authored-By: Codex <noreply@openai.com>`.

Why this shape: one owner for the main branch, the gate and anything live; no shared mutable file
between agents; nothing to keep in sync, because the rules exist once.

## Facts it rests on

Re-check these against the current docs before each new setup; Codex changes quickly.

- Codex reads a global `~/.codex/AGENTS.md`, then one `AGENTS.md` per directory from the project
  root down to the directory it starts in, joined in that order; it stops reading at
  `project_doc_max_bytes` (32 KiB by default) and re-reads the files on every run; it has no other
  memory between runs (learn.chatgpt.com/docs/agent-configuration/agents-md, fetched 2026-10-17).
- Codex's default `:workspace` sandbox writes only inside the workspace roots and system temp,
  keeps `.git`, `.agents`, `.codex` and `.aws` read-only, and has the network off by default
  (learn.chatgpt.com/docs/permissions, fetched 2026-10-18). So Codex in its own worktree **can't
  write a handoff file in another checkout**; the handoff has to travel in Git.
- `codex resume` continues a session; `codex exec` takes `--sandbox read-only`, `--output-schema`
  and `--model`; `codex review` reviews a diff (learn.chatgpt.com/docs/developer-commands, fetched
  2026-10-18).
- Git worktrees share refs, so a hook in the main checkout sees local `codex/*` branches without a
  fetch. Git won't check out one branch in two worktrees.
- Claude Code: `@path` in `CLAUDE.md` imports a file; a `SessionStart` hook's stdout is added to the
  session's context; `$CLAUDE_PROJECT_DIR` is the project root.
- Unverified (ecf, 2026-10-18): whether Codex asks for approval to commit in a linked worktree,
  whose Git metadata lives in the main checkout's `.git`. Check on the first Codex run.

## How to set it up

The steps, templates and hook script are in the project skill
`.claude/skills/claude-codex-integration/SKILL.md` (version 1, 2026-10-19). In Claude Code, ask to
set up Codex in a repository, or run `/claude-codex-integration`. To use it in another repository,
copy the skill's folder into that repository's `.claude/skills/`, or into `~/.claude/skills/` to
make it personal. This document keeps the reasons; the skill keeps the steps.

## Alternatives rejected in ecf

- **A gitignored handoff file in the main checkout:** Codex's sandbox can't write outside its
  worktree, and giving it the main checkout breaks the reserved list.
- **A Claude session that watches for handoffs:** only works while that session is open, and adds a
  running process. Reading at startup covers it.
- **Codex merges too:** two owners of the main branch and the gate.
- **Nested `AGENTS.md` files for path rules:** never seen by a session started at the root.
- **Codex as a reviewer** (read-only `codex exec --sandbox read-only --output-schema` lenses added to
  Claude's review panel; Codex running its own panel; `codex review` on diffs; a Codex lens in place
  of a Claude one): ecf kept reviews Claude-only. If a project wants a second model family on the
  panel, the first form is the simplest: one merged findings list, the same spot-checks, the operator
  still decides.

## Pitfalls seen in ecf

- `git merge --no-ff -F -` doesn't read the message from stdin ("could not read file '-'"); write it
  to a temp file and pass `-F <file>`.
- The hook only runs in sessions started after `.claude/settings.json` exists; the current session
  won't show it.
- Deleting a remote branch that was never pushed fails harmlessly; check with
  `git ls-remote --heads origin <branch>` before calling it an error.
- Claude now loads the path rules all the time through the import: a small context cost, accepted
  so nothing needs syncing.

## Revisit when

- Codex reports it needs something only Claude can read (ecf: the archive of past state and review
  findings). Move what it needs into committed docs rather than opening the local state.
- Codex's instruction-file rules, sandbox defaults or size limit change (re-read the facts above).
- The operator wants Codex to merge, release or review.
