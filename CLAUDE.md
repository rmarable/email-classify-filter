# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Rules

1. Do not tell lies.
2. Do not make up facts; if something is unknown or unverified, say so.
3. Always confirm before writing files, committing, or pushing.

## Project

ecf (email-classify-filter) watches business mailboxes over IMAP, classifies each message, applies deterministic fraud and regulator rules, and asks a person to approve actions in Slack. v1 is single-user local mode on macOS and Linux (no AWS). Roadmap: M1 AWS mode, M2 teams, M3 remote access, M4 always-on.

Each deliverable, and each real-service test, starts only when the operator names it.

## Where the design lives

- `SPEC.md` is authoritative. It owns the threat model, stated limits, privacy statement and release criteria; README, SECURITY.md and this file summarize and point to it.
- The approved design plan is committed as `docs/history/design-plan-2026-09-27.md` (`docs/CURRENT-DESIGN-PLAN.md` links to the latest plan). It holds the full design of each roadmap milestone until that milestone's `docs/roadmap/<milestone>.md` is written at its start.
- `docs/history/` also holds the superseded early documents (`LOCAL-EMAIL-PROCESSING.md`, `JEV-EMAIL-PROCESSING.md`, `POTENTIAL_CODE_SOURCES.md`, `email_classifier_sketch.py`). They are history only; do not use them as a design source.

## Design-plan link rule

- Design plans are dated, immutable snapshots: `docs/history/design-plan-YYYY-MM-DD.md` (same-day plans add `-2`, `-3`). Never edit a plan after it is committed.
- `docs/CURRENT-DESIGN-PLAN.md` is a relative symlink to the latest plan. Whenever a new plan is committed, repoint the symlink in the same commit.
- `SPEC.md` is authoritative over any plan.

## Architecture (v1; details in SPEC)

- **One local service**, `ecf-server local` (launchd on macOS, systemd user unit on Linux), holds all state and does all security-relevant work: IMAP fetch, DKIM/DMARC, computed facts, fraud and regulator triggers, rules, policy, grants, state transitions, Slack (Socket Mode), step-up, execution, timers.
- State in SQLite (WAL); secrets in the OS secret store; full messages are held in memory only, never written to disk.
- **Clients** (`ecf` CLI; `ecf-mcp`, the stdio MCP server the Claude Code plugin runs) talk to the service over HTTP on a 0600 Unix socket. Clients hold no rules, policy, facts or state transitions.
- One distribution, `email-classify-filter`, with two import packages: `ecf` (client) and `ecf_server` (service). `ecf` never imports `ecf_server` (import-linter).
- Model output can raise risk but never, alone, hide mail or approve anything. MCP has no approval or admin tools.
- Presets: A all-local (Gemma 4 12B via Ollama); B local classifier + Claude actor; C all-Claude. Claude runs only on demand, via `/ecf-review` typed in an `ecf claude` session.

## Build milestones

V1.0 foundations · V1.1 mail and checks · V1.2 Slack and approvals · V1.3 local models (preset A) · V1.4 Claude on demand (B, C) · V1.5 outbound and operations · V1.6 Linux verification (gates `v1.0.0`).

## Commands

Python ≥ 3.12, managed with uv (`.python-version`). `docs/` is excluded from pytest, ruff and pyright. Full how-to: `CONTRIBUTING.md`.

```sh
uv sync                                   # create/update .venv from uv.lock
uv run ruff check . && uv run ruff format --check .
uv run pyright                            # strict
uv run lint-imports                       # ecf must never import ecf_server
uv run pytest                             # all tests for this OS
uv run pytest tests/test_smoke.py::test_cli_version   # one test
uv run pytest -m macos                    # macOS-only tests
uv run python scripts/check_licenses.py   # dependency license allow-list (--markdown: table)
uv run ecf-server dev                     # throwaway dev service; prints ECF_SOCKET=...
uv run ecf eval build                     # synthetic set: hygiene scan, then .eml + labels
uv build                                  # wheel + sdist
```

**When to run tests** (operator decision 2026-10-02; a full run takes ~3 minutes):
- While working, run only the test files for the code you changed (`uv run pytest tests/test_x.py`), plus ruff and pyright.
- Run the full suite once, right before asking to commit, and only after the change is final. If the code changes after that, re-run only the affected files, unless the change touches code many tests share (then run it all once more).
- Don't re-run a suite whose inputs haven't changed, and don't run the full suite to "double-check" a green targeted run. After a push, CI is the check (see Commits); the full macOS run with 0 skipped is for the merge gate.

**Shell on macOS:** use `gsed` for GNU sed syntax (the built-in BSD sed rejects `\|` alternation and needs `-i ''`); for multi-line or exact replacements prefer the Edit tool or a short Python script.

**macOS merge gate:** GitHub CI runs on Linux only (to stay within free minutes). Before any merge to `main`, run the full suite on this Mac (`uv run pytest -rs`, which includes the `macos` tests) and put the result in the merge commit message, e.g. `macOS tests: 212 passed (macOS 27.0, 2026-10-02)`. **The gate passes only with 0 skipped:** the `imap` tests skip when Docker isn't running, so start Colima first; the Ollama tests need Ollama installed and the pinned model (`uv run ecf models install`), see CONTRIBUTING; if anything is skipped, fix the environment and run again rather than merge. Tests that touch the real Keychain or launchd use `ecf-test-*` names and remove what they create.

## Hard constraints

- **Credentials** (IMAP app passwords, Slack tokens, API keys) never enter the repo, environment variables or secrets files. They live only in the OS secret store (macOS Keychain, Linux Secret Service or `systemd-creds`), written only by the local service.
- **AWS skills:** v1 is local-only (no AWS). Don't load AWS skills or follow the global AWS guidance until M1 starts (operator decision 2026-09-30; this project file overrides the global one).
- **Privacy** (SPEC owns the full statement): email content goes only to the mail provider, this computer, Slack (subjects, senders, classifications, the actor's reason, answers; short excerpts only on request), and Anthropic during `/ecf-review` and `/ecf-eval` (presets B and C). Never add another destination. No payload logging.
- **Models:** exclude PRC-affiliated and Meta/X-affiliated labs (operator preference), e.g. Qwen, DeepSeek, Llama, Grok.
- **Real-service tests** need the operator's go-ahead each time; their code is throwaway and stays in the session scratchpad, never the repo; Slack resources they create are torn down afterwards; results go in SPEC (an ADR only when a result changes a decision).

## Tags

- **Milestone tags** (`ms-v1.0-foundations` … `ms-v1.5-outbound-ops`, `ms-v1.6-linux`, later `ms-m1-aws`, …) are annotated tags recording internal progress. They do **not** mean the software is ready for anyone else.
- **Release tags** `vX.Y.Z` (optionally `-rcN`) are the only tags built and published from, and the only ones `ecf upgrade --to` accepts. `v1.0.0` requires every V1.x milestone plus the release criteria in SPEC.
- Create or push a tag only after the operator confirms and approves both the tag and the push.
- When a milestone's work looks complete (its scope built, CI green, its gating real-service test passed), prompt the operator that the `ms-…` tag is due, and say what's done and what isn't. Never apply a tag unprompted.

## Commits

- A commit message ends with exactly one trailer line: `Co-Authored-By: Claude <noreply@anthropic.com>`. Never name the model, and never add a `Claude-Session:` link or any other trailer.
- **After every push, wait for CI and check it passed** (`gh run watch <id> --exit-status`) before reporting the push done or starting the next step, and include the result in the report. A green local run isn't enough: CI runs Python 3.12 and 3.13 on Linux. If CI fails, stop and investigate before anything else. (In V1.1 a CI failure went unnoticed for 9 pushes.)

## Changelog

- Every commit that changes behavior, adds or changes a command, or records a decision adds one line to `CHANGELOG.md` under the heading for the next tag, e.g. `## ms-v1.1-mail-checks (not yet tagged)`. Refactors and test-only changes don't.
- Lines are plain language (what changed for someone using ecf), one per change, citing the ADR or `OD-nnn` where there is one. No commit hashes (history rewrites change them).
- Newest first. Sections (e.g. "Changed", "Security") only where they help.
- When a tag is created, its heading becomes the tag name and date (`## ms-v1.0-foundations (2026-10-02)`); the entry is reviewed with the tag request.

## Documentation style

- Terse, precise, for a semi-technical reader; no filler.
- One owner per topic: SPEC (design, threat model, limits), SECURITY.md (vulnerability reporting, supported versions, a summary pointing to SPEC), CONTRIBUTING (how-to only).
- A "verified" fact cites its site and date; anything else reads "unverified, confirm in V1.x". "(rv)" marks a fact verified by a reviewer's cited source and not re-checked by Claude.

## Session state

- `CLAUDE-STATE.md` holds local working state. It is gitignored, must never be committed, and is loaded automatically through `CLAUDE.local.md` (gitignored; the single line `@CLAUDE-STATE.md`).
- If either file is missing (fresh clone, another worktree), create a short placeholder for each (rule 3 applies).
- Update it as work progresses (rule 3 applies). At most 80 lines, in four sections: Current task, Next steps, Open questions, Recent progress (the last 32 entries; operator decision 2026-09-30, was 24).
- Decisions don't live there. A decision made during the build becomes an ADR in `docs/adr/` (committed with the operator's OK); a small decision that doesn't justify an ADR (a default value, a name) goes in the relevant SPEC section, marked with its date and "operator decision", and the commit message names the change. Either way the state file keeps at most a one-line pointer.
- **Archiving:** at each `ms-…` tag, or when the file passes 80 lines, progress entries older than the last 32 and resolved open questions (each with a one-line pointer to where its answer landed) move to `state-archive/YYYY-MM.md` (gitignored, never imported). Propose the rollover, show what moves, and do it only after the operator confirms.

## Path-scoped rules

`.claude/rules/*.md` files with `paths:` frontmatter load only when Claude works on matching files. Now: `eval-synthetic.md` (`tests/eval/synthetic/**`). A `docs/roadmap/**` rule arrives with the first roadmap document; an `infra/**` rule with M1.
