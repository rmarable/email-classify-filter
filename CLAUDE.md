# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
The rules shared with Codex are in `AGENTS.md` (OD-532, ADR 0033):

@AGENTS.md

## Codex branches

- At the start of each session, the SessionStart hook (`.claude/hooks/codex-handoffs.sh`) lists the
  `codex/*` branches not yet merged into main, each with its handoff block. Tell the operator about
  each one marked `Handoff: ready`, and ask before acting on any of them.
- Picking one up, after the operator's OK: read the handoff and the diff (`main...codex/<topic>`);
  check the reserved list wasn't crossed, the CHANGELOG line and the tests; assign real numbers for
  `OD-NEW` / `ADR-NEW`; run the macOS merge gate unless it is docs only; merge with the gate line;
  push; watch CI; then remove the Codex worktree and branch (OK needed). Fixes go on the branch
  before the merge, or back to Codex as a list.
- Phase R and diff reviews stay Fable only (OD-532); Codex isn't one of the reviewers.

## Session state

- `CLAUDE-STATE.md` holds local working state. It is gitignored, must never be committed, and is loaded automatically through `CLAUDE.local.md` (gitignored; the single line `@CLAUDE-STATE.md`).
- If either file is missing (fresh clone, another worktree), create a short placeholder for each (rule 3 applies).
- Update it as work progresses (rule 3 applies). At most 100 lines, in four sections: Current task, Next steps, Open questions, Recent progress (the last 32 entries, one line each; operator decision 2026-10-14; the file limit was 80 lines).
- Another session may be editing this file too. Re-read it right before each write. Change only your own lines, except when archiving.
- Decisions don't live there. A decision made during the build becomes an ADR in `docs/adr/` (committed with the operator's OK); a small decision that doesn't justify an ADR (a default value, a name) goes in the relevant SPEC section, marked with its date and "operator decision", and the commit message names the change. Either way the state file keeps at most a one-line pointer.
- **Archiving:** at each `ms-…` tag, or when the file passes 100 lines or Recent progress passes 32 entries, entries older than the last 32 and resolved open questions (each with a one-line pointer to where its answer landed) move to `state-archive/YYYY-MM.md` (gitignored, never imported). Propose the rollover, show what moves, and do it only after the operator confirms.

## Path-scoped rules

`.claude/rules/*.md` files with `paths:` frontmatter load only when Claude works on matching files. Now: `eval-synthetic.md` (`tests/eval/synthetic/**`) and `roadmap.md` (`docs/roadmap/**`); their text is in `AGENTS.md`, "When you edit these paths", so Codex sees it too, and the rule files point there. An `infra/**` rule arrives with M1.
