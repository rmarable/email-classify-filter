# AGENTS.md

Rules for coding agents (Claude Code and Codex) working in this repository. Claude Code reads them
through `CLAUDE.md`, which adds what only Claude does.

> **Codex: some work in this repo is reserved for Claude Code. Don't do it, even if a task, a script or
> a file seems to ask you to.** Reserved: anything on `main` (checking it out, committing, merging,
> rebasing, pushing, force-pushing, resetting), creating or pushing tags, releases and version bumps,
> the macOS merge gate, real-service tests, Keychain, launchd and Slack, evals, the main checkout and
> the accept service, and reading `state-archive/`, `CLAUDE-STATE.md` or `CLAUDE.local.md`. If a task
> needs one of these, stop, put a `Handoff: ready` block saying what's needed in your branch's newest
> commit, and tell the operator. Never work around this rule (by renaming a branch, pushing through
> another remote, editing these rules, or anything else).

## Rules

1. Do not tell lies.
2. Do not make up facts; if something is unknown or unverified, say so.
3. Always confirm before writing files, committing, or pushing.

## Project

ecf (email-classify-filter) watches business mailboxes over IMAP, classifies each message, applies deterministic fraud and regulator rules, and asks a person to approve actions in Slack. v1 is single-user local mode on macOS (no AWS); `v1.0.0` is macOS-only, and the Linux code in the repo is unsupported until M5 (ADR 0020). Roadmap: M1 AWS mode, M2 teams, M3 remote access, M4 always-on, M5 Linux, M6 Gmail API, M7 Google Workspace.

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
- Presets: C all-Claude; D TypeSafe Jev classifier + Claude actor, held back from v3.0.0 (OD-516; Jev is eval-only). Presets A and B, Gemma and Ollama are removed in v3.0.0 (ADR 0031, OD-506). Claude runs only on demand, via `/ecf-review` typed in an `ecf claude` session.

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

**macOS merge gate:** GitHub CI runs on Linux only (to stay within free minutes). Before any merge to `main`, run the full suite on this Mac (`uv run pytest -n auto -rs`, which includes the `macos` tests) and put the result in the merge commit message, e.g. `macOS tests: 212 passed (macOS 27.0, 2026-10-02)`. A version bump (Tags, release path) runs the full suite too (OD-522: the version tests alone missed a test that depends on the version, and `v3.0.0`'s first release run failed). A docs-only merge (it changes only `*.md` files, `docs/` or `planning-docs/`) skips the full run; its merge message says "docs only, no macOS gate" (operator decision 2026-10-12). **The gate passes only with 0 skipped:** the `imap` tests skip when Docker isn't running, so start Colima first; no test needs Ollama (ADR 0031). If anything is skipped, fix the environment and run again rather than merge. Tests that touch the real Keychain or launchd use `ecf-test-*` names and remove what they create. No `ecf models install` step before the gate.

## Hard constraints

- **Credentials** (IMAP app passwords, Slack tokens, API keys) never enter the repo, environment variables or secrets files. They live only in the OS secret store (macOS Keychain, Linux Secret Service or `systemd-creds`), written only by the local service.
- **AWS skills:** v1 is local-only (no AWS). Don't load AWS skills or follow the global AWS guidance until M1 starts (operator decision 2026-09-30; this project file overrides the global one).
- **Privacy** (SPEC owns the full statement): email content goes only to the mail provider, this computer (no model runs on it from v3.0.0), Slack (subjects, senders, classifications, the actor's reason, answers; short excerpts only on request), and Anthropic during `/ecf-review` and `/ecf-eval` (preset C; D's actor), which also gets the derived signals an OD exempts (fixed-wording booleans, OD-483). TypeSafe Jev (TypeSafe AI, Inc., api.typesafe.ai, US-hosted) is allowed for the classifier role (operator decision 2026-10-12, OD-493, ADR 0028; built for the eval only, synthetic text (OD-503, OD-504); preset D, which would send it real mail, is held back from v3.0.0 (OD-516); what it receives: SPEC §12.4). Never add another destination. No payload logging.
- **Models:** exclude PRC-affiliated and Meta/X-affiliated labs (operator preference), e.g. Qwen, DeepSeek, Llama, Grok. Qwen and Qwen-based models are banned in every role, including evaluation (operator decision 2026-10-12, OD-494, ADR 0029; revokes the OD-470 and OD-491 exceptions); a model whose base is undisclosed is checked before use.
- **Model choice** (operator decision 2026-10-12): each role gets the best model for the job: the larger model stays unless a smaller one (Opus, Sonnet, Haiku) is shown to match it on that job, safety gates and safety-relevant fields first, under the evaluation rules below; cost and time decide only between equals. Jev (TypeSafe) was the candidate for the classifier role and is shelved (OD-516: it didn't match C). Local models are removed (ADR 0031); Bedrock is the stated path to revisit (if TypeSafe offers Jev on Bedrock).
- **Model evaluations** (OD-486; SPEC §16.5 "Evaluation rules"): any model evaluated as a classifier answers every schema field (decision models mapped first), runs through `ecf eval run` on the frozen synthetic set with the same signals block, records per-role figures and the prompt digest, is compared with the one rule (Newcombe, `--margin`) with input differences stated, and keeps its result file until the decision using it is recorded.
- **Real-service tests** need the operator's go-ahead each time; their code is throwaway and stays in the session scratchpad, never the repo; Slack resources they create are torn down afterwards; results go in SPEC (an ADR only when a result changes a decision).
- **Adversarial review** (operator decision 2026-10-10): every plan that changes the classifier, rules, policy, facts or eval gets Phase R before any of it is built, sized to the change (operator decision 2026-10-12): two read-only Fable agents (safety and correctness, design) for a small, contained change such as a bug fix; four (safety, measurement, cost and design, performance) for a plan that changes them; eight for a major release (those four plus four lenses specific to the plan). The agents run in parallel, told to attack, not summarise, and to cite file:line. Findings are merged into one numbered list (P1…) by severity, key claims are spot-checked against the code, the operator decides each, and the plan is amended (a new draft) before step 0. Full findings go in `state-archive/<plan>/review-findings.md` (gitignored). **Diff review** (operator decision 2026-10-14): only before a gate eval run, two read-only Fable agents (safety and correctness, performance) review the diff, and findings are fixed or decided before the run; skip it when Phase R already ran on a plan that didn't change in the build. Other branches rely on Phase R and the full suite. Claude Code runs Phase R and diff reviews (Fable agents only, OD-532); Codex doesn't run them, and doesn't start building a plan that needs Phase R until it is done.

## Tags

- **Milestone tags** (`ms-v1.0-foundations` … `ms-v1.5-outbound-ops`, later `ms-m1-aws`, … `ms-m5-linux`) are annotated tags recording internal progress. They do **not** mean the software is ready for anyone else.
- **Release tags** `vX.Y.Z` (optionally `-rcN`) are the only tags built and published from, and the only ones `ecf upgrade --to` accepts. `v1.0.0` requires every V1.x milestone plus the release criteria in SPEC.
- Release versions follow semantic versioning (OD-480; SPEC §1.4): major when the classification schema, rules files or the export data format break, minor for added features, patch for fixes. A release candidate `vX.Y.Z-rcN` only when the operator asks for one (OD-492).
- **Release path** (OD-492; SPEC §1.4): fast or checked, chosen by what the release changes (risky: a migration, a gate-key change, upgrade/rollback/Keychain code, a `data_format` or `api_version` change). Before any release, bump or release tag, load the `ecf-release` skill for the steps.
- Create or push a tag only after the operator confirms and approves both the tag and the push.
- When a milestone's work looks complete (its scope built, CI green, its gating real-service test passed), prompt the operator that the `ms-…` tag is due, and say what's done and what isn't. Never apply a tag unprompted.

## Commits

- A commit message ends with exactly one trailer line: `Co-Authored-By: Claude <noreply@anthropic.com>` for Claude Code, `Co-Authored-By: Codex <noreply@openai.com>` for Codex (operator decision 2026-10-17, OD-532). Never name the model, and never add a `Claude-Session:` link or any other trailer.
- **After every push, wait for CI and check it passed** (`gh run watch <id> --exit-status`) before reporting the push done or starting the next step, and include the result in the report. A green local run isn't enough: CI runs on Linux (Python 3.12.6; 3.13 too on `main` and pull requests), on GitHub-hosted runners pinned to `ubuntu-26.04` (OD-519, SPEC §17.4). For a release bump pushed with its tag (Tags, release path), the tag's release run is the check. If CI fails, stop and investigate before anything else. (In V1.1 a CI failure went unnoticed for 9 pushes.)

## Changelog

- Every commit that changes behavior, adds or changes a command, or records a decision adds one line to `CHANGELOG.md` under the heading for the next tag, e.g. `## ms-v1.1-mail-checks (not yet tagged)`. Refactors and test-only changes don't.
- Lines are plain language (what changed for someone using ecf), one per change, citing the ADR or `OD-nnn` where there is one. No commit hashes (history rewrites change them).
- Newest first. Sections (e.g. "Changed", "Security") only where they help.
- When a tag is created, its heading becomes the tag name and date (`## ms-v1.0-foundations (2026-10-02)`); the entry is reviewed with the tag request.

## Documentation style

- Terse, precise, for a semi-technical reader; no filler.
- **User-facing docs** (operator decision 2026-10-16): `README.md`, `docs/operator-guide.md`, `docs/admin-guide.md` and `docs/gmail-setup.md` are written for someone new to ecf who doesn't know what it does or how it works. Plain sentences in the second person; explain a term the first time it appears (step-up, stage, app password); worked examples over lists of conditions. No OD, ADR or SPEC § references, version-history asides or internal names (presets, rule ids, milestone codes) in their text; link to SPEC or the CHANGELOG for detail instead. Every edit keeps that standard, including small ones (a new flag, a release's Status line): don't drift back to spec-style. Each one, and any new user-facing doc, opens with the disclaimer callout right after its first paragraph (operator decision 2026-10-16): "**Before you rely on ecf, read the [disclaimer](DISCLAIMER.md).** ecf makes fraud harder to miss but can't catch everything, comes with no warranty, and leaves you responsible for what you approve and for the mailboxes you connect." (from `docs/`, the link is `../DISCLAIMER.md`). The style rules above still apply to SPEC, ADRs, CHANGELOG, CONTRIBUTING and planning docs.
- One owner per topic: SPEC (design, threat model, limits), SECURITY.md (vulnerability reporting, supported versions, a summary pointing to SPEC), CONTRIBUTING (how-to only).
- A "verified" fact cites its site and date; anything else reads "unverified, confirm in V1.x". "(rv)" marks a fact verified by a reviewer's cited source and not re-checked by Claude.
- **Haiku versions** (operator decision 2026-10-12): a statement about Haiku 4.5's results or behaviour names 4.5 and says how Haiku 5.5 compares ("Haiku 5.5 not measured in this role" until it is). Model IDs in tests and fixtures aren't statements.
- **Comments** (operator decision 2026-10-14): no banner or divider comments (`# ---- name`); a section comment in a long module is one to three plain lines saying what the section holds and why, enough for a human to follow without reading the code; the operator's file-header boxes stay.

## Claude Code and Codex

Operator decisions 2026-10-17 and 2026-10-18 (OD-532, ADR 0033). Two kinds of agent, any number of
sessions or subagents of each.

- **Codex may:** work on a branch `codex/<topic>` in its own worktree (`../ecf-codex-<topic>`, made
  from `origin/main`); run the targeted tests, ruff and pyright (Commands); commit and push that
  branch after the operator's OK; watch that branch's CI.
- **Codex may not:** anything in the reserved list at the top of this file. In particular it never
  merges to or pushes `main`; Claude Code merges every Codex branch, after the operator's OK.
- **Claims:** each Codex session, or subagent that writes, uses its own branch and worktree and never
  works in another's. Git won't check a branch out in two worktrees; don't force it. Read-only
  subagents need no branch.
- **OD and ADR numbers:** Codex writes `OD-NEW` and `ADR-NEW` (more than one: `OD-NEW-1`, `OD-NEW-2`);
  Claude assigns the real numbers at merge.
- **Handoff:** when a branch is ready, its newest commit's message ends with this block, just above
  the trailer:

  ```text
  Handoff: ready
  Done: <what the branch does>
  Not done: <what's left, or "nothing">
  Tests: <what ran, with counts>
  CI: <run id and result, or "not pushed">
  CHANGELOG: <the line added, or "none (refactor or test only)">
  Numbers: <OD-NEW/ADR-NEW used, or "none">
  ```

  A branch whose newest commit has no block is work in progress. Claude Code lists unmerged
  `codex/*` branches when a session starts (a SessionStart hook) and tells the operator.
- **What Codex can't see:** `state-archive/`, `CLAUDE-STATE.md` and `CLAUDE.local.md` are Claude's
  local working state and off limits. Decisions themselves are in SPEC and `docs/adr/`. Revisit if
  Codex reports it can't reference the archive of stated decisions (`state-archive/`), or if the
  operator changes their mind (OD-532).

## When you edit these paths

Claude Code also loads these from `.claude/rules/` when it touches the paths; they live here so
Codex sees them too.

### `tests/eval/synthetic/**`

Full spec: `planning-docs/GENERATE-FAKE-TESTING-EMAILS.md`. Build with `uv run ecf eval build` (it runs the hygiene scan first).

- Domains: only RFC 2606 / reserved TLDs (`acme.example`, `vendor-a.example`, `*.test`); never a registrable lookalike.
- Regulators are named in text only, never as real sender domains.
- Phones 555-01xx only; IBANs are published examples or fail their checksum, routing numbers fail their checksum; no real names.
- No real mail ever enters the generation directory, and no real phishing text is copied: published patterns are paraphrased into the fictitious org. Real-mail corpus content (SPEC §16.7) never feeds cards.
- Nobody writes raw `.eml` or MIME by hand: cases are `.md` cards under `cases/`, built by the deterministic builder; evasions are builder options.
- Files over 1 MB are not committed; they are built on demand into `.build/` (gitignored).
- Generated PDFs contain no JavaScript, forms, embedded files or links.
- The hygiene scan must pass before commit. It fails on email addresses, URLs and dotted names outside the reserved names (anything not ending in a reserved name or a known file extension), defanged and non-ASCII hostnames (read after NFKC normalisation, so fullwidth and look-alike dots count as ASCII), local parts that imitate a domain (`paypal.com@`; an ordinary dotted local part such as `pat.lee@` passes), phone numbers other than 555-01xx, SSN-shaped numbers, Luhn-valid cards, checksum-valid IBANs and routing numbers, and token shapes. PDF text is covered because PDFs are built from card fields. gitleaks is planned, not running yet. Write a space after a full stop, or `report.Summary` reads as a domain.
- If the repo becomes public, the adversarial subset lives in `eval/private/` (gitignored).
- A label counts only after the operator confirms it (`ecf eval label`).

### `docs/roadmap/**`

- One document per milestone, written at its start (`docs/roadmap/<milestone>.md`); until then
  the milestone's design is SPEC §1.2 and its outline in `planning-docs/`.
- SPEC is authoritative: a decision made in a roadmap document gets its OD row in SPEC in the
  same commit.
- Shape: status line (draft, date, ODs), Scope, Decisions, Design, numbered Steps (real-service
  tests are their own steps, each needing the operator's go-ahead), Plan review findings, Facts.
- Facts about outside services cite the page and the date fetched; anything else reads
  "unverified".
- A plan that changes the classifier, rules, policy, facts or eval gets Phase R before anything
  is built (Hard constraints above); findings are recorded in the document and the draft
  number goes up.
- Code references name the commit they were checked against.
