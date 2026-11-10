# Testing and the macOS merge gate

Rules for coding agents (Claude Code and Codex), moved out of `AGENTS.md` so they load only when
needed. `AGENTS.md` says when to read this file. The text is unchanged from `AGENTS.md`.

## When to run tests

Operator decision 2026-10-02. A full run takes ~1.5 minutes with `-n auto`, ~4.5 one test at a
time (measured 2026-10-05).

- While working, run only the test files for the code you changed (`uv run pytest tests/test_x.py`),
  plus ruff and pyright.
- Run the full suite once, right before asking to commit, and only after the change is final. If
  the code changes after that, re-run only the affected files, unless the change touches code many
  tests share (then run it all once more).
- Don't re-run a suite whose inputs haven't changed, and don't run the full suite to "double-check"
  a green targeted run. After a push, CI is the check (`AGENTS.md`, Commits); the full macOS run
  with 0 skipped is for the merge gate.

## macOS merge gate

Reserved for Claude Code. GitHub CI runs on Linux only (to stay within free minutes).

- Before any merge to `main`, run the full suite on this Mac (`uv run pytest -n auto -rs`, which
  includes the `macos` tests) and put the result in the merge commit message, e.g.
  `macOS tests: 212 passed (macOS 27.0, 2026-10-02)`.
- A version bump (`AGENTS.md`, Tags, release path) runs the full suite too (OD-522: the version
  tests alone missed a test that depends on the version, and `v3.0.0`'s first release run failed).
- A docs-only merge (it changes only `*.md` files, `docs/` or `planning-docs/`) skips the full run;
  its merge message says "docs only, no macOS gate" (operator decision 2026-10-12).
- **The gate passes only with 0 skipped:** the `imap` tests skip when Docker isn't running, so
  start Colima first; no test needs Ollama (ADR 0031). If anything is skipped, fix the environment
  and run again rather than merge.
- Tests that touch the real Keychain or launchd use `ecf-test-*` names and remove what they create.
- No `ecf models install` step before the gate.
- **Card scan** (EVAL-PRIVATE.md X5): every merge to `main`, docs-only merges included (a
  card-only merge is `.md` only), runs `uv run python scripts/check_card_injection.py --tree HEAD
  --no-skip` on the local merge commit; it must refuse nothing.
