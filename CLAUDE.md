# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Rules

1. Do not tell lies.
2. Do not make up facts; if something is unknown or unverified, say so.
3. Always confirm before writing files, committing, or pushing.

## Project

ecf (email-classify-filter) watches business mailboxes over IMAP, classifies each message, applies deterministic fraud and regulator rules, and asks a person to approve actions in Slack. v1 is single-user local mode on macOS (no AWS); `v1.0.0` is macOS-only, and the Linux code in the repo is unsupported until M5 (ADR 0020). Roadmap: M1 AWS mode, M2 teams, M3 remote access, M4 always-on, M5 Linux, M6 Gmail API.

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
- Presets: C all-Claude; D TypeSafe Jev classifier + Claude actor (from v3.0.0 step 4). Presets A and B, Gemma and Ollama are removed in v3.0.0 (ADR 0031, OD-506). Claude runs only on demand, via `/ecf-review` typed in an `ecf claude` session.

## Build milestones

V1.0 foundations · V1.1 mail and checks · V1.2 Slack and approvals · V1.3 local models (preset A; removed in v3.0.0) · V1.4 Claude on demand (B, C) · V1.5 outbound and operations · V1.6 Gmail (personal Google accounts over IMAP, OD-425) (these gate `v1.0.0`). The earlier V1.6, Linux verification, became roadmap milestone M5 (OD-421); its plan is on branch `m5-linux` (`docs/roadmap/m5-linux.md`), not merged.

After `v1.0.0`, work is tracked by release rather than V1.x milestones: `v2.0.0` is schema v2, schema extensions, the Haiku 5.5 main session and `num_ctx` 6144.

## Commands

Python ≥ 3.12.6, managed with uv (`.python-version`). `docs/` is excluded from pytest, ruff and pyright. Full how-to: `CONTRIBUTING.md`.

```sh
uv sync                                   # create/update .venv from uv.lock
uv run ruff check . && uv run ruff format --check .
uv run pyright                            # strict
uv run lint-imports                       # ecf must never import ecf_server
uv run pytest -n auto                     # all tests for this OS, in parallel (pytest-xdist)
uv run pytest tests/test_smoke.py::test_cli_version   # one test
uv run pytest -m macos                    # macOS-only tests
uv run python scripts/check_licenses.py   # dependency license allow-list (--markdown: table)
uv run ecf-server dev                     # throwaway dev service; prints ECF_SOCKET=...
uv run ecf eval build                     # synthetic set: hygiene scan, then .eml + labels
uv build                                  # wheel + sdist
```

**When to run tests** (operator decision 2026-10-02; a full run takes ~1.5 minutes with `-n auto`, ~4.5 one test at a time; measured 2026-10-05):
- While working, run only the test files for the code you changed (`uv run pytest tests/test_x.py`), plus ruff and pyright.
- Run the full suite once, right before asking to commit, and only after the change is final. If the code changes after that, re-run only the affected files, unless the change touches code many tests share (then run it all once more).
- Don't re-run a suite whose inputs haven't changed, and don't run the full suite to "double-check" a green targeted run. After a push, CI is the check (see Commits); the full macOS run with 0 skipped is for the merge gate.

**Shell on macOS:** use `gsed` for GNU sed syntax (the built-in BSD sed rejects `\|` alternation and needs `-i ''`); for multi-line or exact replacements prefer the Edit tool or a short Python script.

**macOS merge gate:** GitHub CI runs on Linux only (to stay within free minutes). Before any merge to `main`, run the full suite on this Mac (`uv run pytest -n auto -rs`, which includes the `macos` tests) and put the result in the merge commit message, e.g. `macOS tests: 212 passed (macOS 27.0, 2026-10-02)`. A version bump (Tags, release path) needs only the version tests. A docs-only merge (it changes only `*.md` files, `docs/` or `planning-docs/`) skips the full run; its merge message says "docs only, no macOS gate" (operator decision 2026-10-12). **The gate passes only with 0 skipped:** the `imap` tests skip when Docker isn't running, so start Colima first; no test needs Ollama (ADR 0031): until the removal's code lands, the three real Ollama/Gemma tests (`test_ollama_unit.py::test_a_real_login_item_runs_ollama_with_ecfs_settings`, `test_actor.py::test_the_real_actor_answers_in_the_vocabulary`, `test_classifier.py::test_the_real_model_classifies_the_starter_cards`) are deselected with `--deselect` (operator decision 2026-10-14) and the merge message says so; after it they no longer exist. If anything is skipped, fix the environment and run again rather than merge. Tests that touch the real Keychain or launchd use `ecf-test-*` names and remove what they create. No `ecf models install` step before the gate.

## Hard constraints

- **Credentials** (IMAP app passwords, Slack tokens, API keys) never enter the repo, environment variables or secrets files. They live only in the OS secret store (macOS Keychain, Linux Secret Service or `systemd-creds`), written only by the local service.
- **AWS skills:** v1 is local-only (no AWS). Don't load AWS skills or follow the global AWS guidance until M1 starts (operator decision 2026-09-30; this project file overrides the global one).
- **Privacy** (SPEC owns the full statement): email content goes only to the mail provider, this computer (no model runs on it from v3.0.0), Slack (subjects, senders, classifications, the actor's reason, answers; short excerpts only on request), and Anthropic during `/ecf-review` and `/ecf-eval` (preset C; D's actor), which also gets the derived signals an OD exempts (fixed-wording booleans, OD-483). TypeSafe Jev (TypeSafe AI, Inc., api.typesafe.ai, US-hosted) is allowed for the classifier role (operator decision 2026-10-12, OD-493, ADR 0028; decided, not built; what it receives is set when it is built). Never add another destination. No payload logging.
- **Models:** exclude PRC-affiliated and Meta/X-affiliated labs (operator preference), e.g. Qwen, DeepSeek, Llama, Grok. Qwen and Qwen-based models are banned in every role, including evaluation (operator decision 2026-10-12, OD-494, ADR 0029; revokes the OD-470 and OD-491 exceptions); a model whose base is undisclosed is checked before use.
- **Model choice** (operator decision 2026-10-12): each role gets the best model for the job: the larger model stays unless a smaller one (Opus, Sonnet, Haiku) is shown to match it on that job, safety gates and safety-relevant fields first, under the evaluation rules below; cost and time decide only between equals. Jev (TypeSafe) is the candidate for the classifier role, compared with C. Local models are removed (ADR 0031); Bedrock is the stated path to revisit (if TypeSafe offers Jev on Bedrock).
- **Model evaluations** (OD-486; SPEC §16.5 "Evaluation rules"): any model evaluated as a classifier answers every schema field (decision models mapped first), runs through `ecf eval run` on the frozen synthetic set with the same signals block, records per-role figures and the prompt digest, is compared with the one rule (Newcombe, `--margin`) with input differences stated, and keeps its result file until the decision using it is recorded.
- **Real-service tests** need the operator's go-ahead each time; their code is throwaway and stays in the session scratchpad, never the repo; Slack resources they create are torn down afterwards; results go in SPEC (an ADR only when a result changes a decision).
- **Adversarial review** (operator decision 2026-10-10): every plan that changes the classifier, rules, policy, facts or eval gets Phase R before any of it is built, sized to the change (operator decision 2026-10-12): two read-only Fable agents (safety and correctness, design) for a small, contained change such as a bug fix; four (safety, measurement, cost and design, performance) for a plan that changes them; eight for a major release (those four plus four lenses specific to the plan). The agents run in parallel, told to attack, not summarise, and to cite file:line. Findings are merged into one numbered list (P1…) by severity, key claims are spot-checked against the code, the operator decides each, and the plan is amended (a new draft) before step 0. Full findings go in `state-archive/<plan>/review-findings.md` (gitignored). Before any gate eval run, two read-only Fable agents (safety and correctness, performance) review the diff; findings are fixed or decided before the run.

## Tags

- **Milestone tags** (`ms-v1.0-foundations` … `ms-v1.5-outbound-ops`, later `ms-m1-aws`, … `ms-m5-linux`) are annotated tags recording internal progress. They do **not** mean the software is ready for anyone else.
- **Release tags** `vX.Y.Z` (optionally `-rcN`) are the only tags built and published from, and the only ones `ecf upgrade --to` accepts. `v1.0.0` requires every V1.x milestone plus the release criteria in SPEC.
- Release versions follow semantic versioning (OD-480; SPEC §1.4): major when the classification schema, rules files or the export data format break, minor for added features, patch for fixes. A release candidate `vX.Y.Z-rcN` only when the operator asks for one (OD-492).
- **Release path** (OD-492; SPEC §1.4): fast or checked, chosen by what the release changes (risky: a migration, a gate-key change, upgrade/rollback/Keychain code, a `data_format` or `api_version` change). Before any release, bump or release tag, load the `ecf-release` skill for the steps.
- Create or push a tag only after the operator confirms and approves both the tag and the push.
- When a milestone's work looks complete (its scope built, CI green, its gating real-service test passed), prompt the operator that the `ms-…` tag is due, and say what's done and what isn't. Never apply a tag unprompted.

## Commits

- A commit message ends with exactly one trailer line: `Co-Authored-By: Claude <noreply@anthropic.com>`. Never name the model, and never add a `Claude-Session:` link or any other trailer.
- **After every push, wait for CI and check it passed** (`gh run watch <id> --exit-status`) before reporting the push done or starting the next step, and include the result in the report. A green local run isn't enough: CI runs on Linux (Python 3.12.6; 3.13 too on `main` and pull requests), on a self-hosted runner until `v1.0.0` (SPEC §17.4). For a release bump pushed with its tag (Tags, release path), the tag's release run is the check. If CI fails, stop and investigate before anything else. (In V1.1 a CI failure went unnoticed for 9 pushes.)

## Changelog

- Every commit that changes behavior, adds or changes a command, or records a decision adds one line to `CHANGELOG.md` under the heading for the next tag, e.g. `## ms-v1.1-mail-checks (not yet tagged)`. Refactors and test-only changes don't.
- Lines are plain language (what changed for someone using ecf), one per change, citing the ADR or `OD-nnn` where there is one. No commit hashes (history rewrites change them).
- Newest first. Sections (e.g. "Changed", "Security") only where they help.
- When a tag is created, its heading becomes the tag name and date (`## ms-v1.0-foundations (2026-10-02)`); the entry is reviewed with the tag request.

## Documentation style

- Terse, precise, for a semi-technical reader; no filler.
- One owner per topic: SPEC (design, threat model, limits), SECURITY.md (vulnerability reporting, supported versions, a summary pointing to SPEC), CONTRIBUTING (how-to only).
- A "verified" fact cites its site and date; anything else reads "unverified, confirm in V1.x". "(rv)" marks a fact verified by a reviewer's cited source and not re-checked by Claude.
- **Haiku versions** (operator decision 2026-10-12): a statement about Haiku 4.5's results or behaviour names 4.5 and says how Haiku 5.5 compares ("Haiku 5.5 not measured in this role" until it is). Model IDs in tests and fixtures aren't statements.

## Session state

- `CLAUDE-STATE.md` holds local working state. It is gitignored, must never be committed, and is loaded automatically through `CLAUDE.local.md` (gitignored; the single line `@CLAUDE-STATE.md`).
- If either file is missing (fresh clone, another worktree), create a short placeholder for each (rule 3 applies).
- Update it as work progresses (rule 3 applies). At most 100 lines, in four sections: Current task, Next steps, Open questions, Recent progress (the last 32 entries, one line each; operator decision 2026-10-14; the file limit was 80 lines).
- Decisions don't live there. A decision made during the build becomes an ADR in `docs/adr/` (committed with the operator's OK); a small decision that doesn't justify an ADR (a default value, a name) goes in the relevant SPEC section, marked with its date and "operator decision", and the commit message names the change. Either way the state file keeps at most a one-line pointer.
- **Archiving:** at each `ms-…` tag, or when the file passes 100 lines or Recent progress passes 32 entries, entries older than the last 32 and resolved open questions (each with a one-line pointer to where its answer landed) move to `state-archive/YYYY-MM.md` (gitignored, never imported). Propose the rollover, show what moves, and do it only after the operator confirms.

## Path-scoped rules

`.claude/rules/*.md` files with `paths:` frontmatter load only when Claude works on matching files. Now: `eval-synthetic.md` (`tests/eval/synthetic/**`). A `docs/roadmap/**` rule arrives with the first roadmap document; an `infra/**` rule with M1.
