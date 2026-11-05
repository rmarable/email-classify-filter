# Move the adversarial eval subset to `eval/private/` (OD-618)

**Status: draft 5, 2026-11-05. Not built.** Draft 1 went through Phase R (four Fable reviewers:
safety, measurement, cost and design, performance); every recommendation was accepted and decisions
V1-V5 made (operator, 2026-11-05). Draft 2 (80a5aaf) went through an Opus delta check (four
reviewers); every fix was accepted (operator, 2026-11-05) and is folded in here; OD-643 restates the
gate guard (amends OD-629). Draft 3 (f63822c) went through a second Opus check; every fix was accepted
and decisions W1-W2 made (OD-647, OD-648, operator 2026-11-05). Draft 4 (4ff2eea) went through a
third Opus check (one high: the `starter-injection` pin); every fix was accepted (operator
2026-11-05) and is folded in here, with OD-654. Findings: `state-archive/eval-private/review-findings.md`. Code
references are as of `1d6558c` (main). Comes before M1 Step 1 (`docs/roadmap/m1-aws-mode.md`).

## Why

- The repository is public (`gh repo view`: `PUBLIC`).
- `.claude/rules/eval-synthetic.md`: "If the repo becomes public, the adversarial subset lives in
  `eval/private/` (gitignored)." The folder doesn't exist; the subset is in the public tree with its
  labels, its expected answers and, for the draft set, the replies that obeying each injection would
  produce (`tests/eval/drafts/checks.yaml`).
- **The subset is republished with every release today:** the sdist's `only-include` has `tests`
  (`pyproject.toml:78-85`) and `release.yml` uploads the sdist to every GitHub Release (checked
  2026-11-05). M1 would add the release zip (OD-611).

What this protects, stated plainly: every current card is in git history, in any clone or fork and in
every past sdist, so moving it doesn't unpublish it. It protects adversarial cards written from now
on and keeps future releases clean. Rewriting history is not proposed.

## Operator decisions

2026-11-05 (draft 1):

1. **Which cards (E-1, narrow):** prompt-injection cards. In `synthetic` and `heldout3`: front matter
   with `injection_target`, `injection_phrase` or `hidden_text`, or a `threat:` naming an injection;
   in `drafts`: `expected.draft.group: injection`. The builder's refusal keys on exactly this list.
2. **Existing cards move too (E-2);** their history and sdist copies stay published.
3. **Home (E-3):** a separate private GitHub repository cloned into `eval/private/` (already
   gitignored, `.gitignore:38`); Codex never sees it.
4. **Scope (E-4):** `synthetic`, `drafts`, and (V5) `heldout3`.

2026-11-05 (after Phase R):

- **V1, overlay:** each folder is a standalone set, and `ecf eval build` writes a merged full set into
  a gitignored folder that every reader uses (Design).
- **V2, a self-describing gate guard,** no committed pin file.
- **V3, `starter-injection` stays public** as a documented exception: it is the card behind the
  `injection` template shipped in the wheel (`src/ecf/eval/case_templates.py:39-57`), so moving it
  hides nothing; the refusal exempts the exact ID `starter-injection` (not a `starter-*` prefix).
- **V4, the size and encoding evasion cards stay public** (`padded-text-part`,
  `scanned-invoice-17mb`): SPEC already documents the limits they test.
- **V5, `heldout3`'s 4 injection cards move** by the same scheme, never deleted (the blind batch's
  make-up stays intact).

SPEC rows: OD-626 (which cards; decisions 1, 4, V5), OD-627 (existing cards, the private repository; decisions 2, 3), OD-628 (V1), OD-629 (V2), OD-630 (V3, V4), OD-643 (the gate guard restated), OD-647 (W1, atomic writes and copies), OD-648 (W2, the shared resolver), OD-654 (draft 5: the `starter-injection` pin by its bytes; before-and-after measured on main's code). Their parent OD-618 is on branch `m1-aws-mode` until M1's planning merges.

## Inventory (Step 1, 2026-11-05; full list `state-archive/eval-private/inventory.md`)

| Set | Cards | Move | Fraud-guard cards that move |
|---|---|---|---|
| `synthetic` | 396 | about 49 (50 by the list, minus `starter-injection`; reviewers counted 48-50 by markers; the Step 3 script settles it) | about 44 of 164 |
| `drafts` | 134 | 54 (`d-inj-*`) | 0 |
| `heldout3` | 30 | 4 (`h3-inj-*`) | 1 |

Current set versions (2026-11-05): `synthetic` `a6642d21bfd5eaaf` (SPEC §16.2's M8 release gate),
`drafts` `ae1111e7bc117b23`.

## Design

### The overlay (V1)

- **Two standalone sets per name:** the public one (`tests/eval/<set>`) and the private one
  (`eval/private/<set>`), each a full set layout of its own: `cases/`, `eml/`, `.build/`,
  `labels.jsonl`, `fixed_labels.jsonl`, `reviews.jsonl`, and for drafts `checks.yaml`. Every tool that
  writes (label, confirm, review, settle, agree, expect, `fixed_labels`, `generate`, `new-case`, build)
  writes to the set that owns the card: a single-ID command finds the owning set itself, and never
  returns the merged folder. Whole-set modes (`ecf eval label` with no ID and `--status`, `review
  --add`'s globs, `settle`, `agree`, `expect`) cover both sets, each change written to the card's
  owning set. One guard, `writable_root()`, runs inside every writer (`build_all`, `labels.write`,
  `fixed_labels`, `review.*`, `append_log`, `new-case` at `cli.py:931`, `generate`): it refuses a root
  whose resolved path or any ancestor is the merged folder or holds `merge.json`. A test searches
  `src/ecf/eval` and `src/ecf/cli*.py` for file writes, with a named allow-list of writes that never
  touch a set (`settle.export`, `mix.write_default`, `grade.py:165`, `generate.py:123` and `:802`,
  `tools.py`, `codex_tool.py`, `corpus_labels.py`, `cli_evalgen._write_private`), so a new writer can't
  skip the guard. `append_log` appends in place, which is safe: the merged `reviews.jsonl` is rebuilt,
  never linked.
- **Atomic writes (W1, OD-647):** every set writer writes through one helper (temporary file, then
  `os.replace`). Today `builder.py:160`, `review.py:175` and `:325` and `generate.py:592` write in
  place (checked 2026-11-05); they change to the helper, and the builder skips a file only when the
  file on disk exists, has the new bytes' size and hashes to the new bytes' sha256 (never by the old
  row alone, so a damaged or missing file is rewritten and `ecf eval build` still repairs a set), which
  also saves about 31 MB of writes per build.
- **Finding the private set:** `<repo top>/eval/private/<set>` for `<repo top>/tests/eval/<set>`, nothing
  else (repo top = the logical path's `parents[2]`, no `git` call); a worktree or clone without it (Codex, `.claude/worktrees`, `../ecf-m1`) has no private set and
  says so ("public set only").
- **The merged set (W1, OD-647):** `refresh_merged(set)` writes `eval/private/.merged/<set>/` (gitignored
  by the private repository), once at the end of each CLI command that wrote a set (in a `finally`, so
  Ctrl-C still refreshes; `ecf eval review` refreshes after each changed card) and in `ecf eval build`
  (which builds both sets, then refreshes), under an exclusive lock on
  `eval/private/.merged/<set>.lock`, with `merge.json` written last. One function, `merge_sources()`
  in `ecf.eval`, computes the merged label, fixed-label and `checks.yaml` content for both
  `refresh_merged` and the gate (a test: its output equals the merged files' bytes). `cases/` and
  `eml/` are plain copies (small), made read-only; `.build/` files are links to the sources (large):
  an APFS clone (`clonefile`) where available, else a hard link, else a copy. A hard-linked `.build/`
  file is writable by design: an in-place write through the merged folder would change the private
  source, which the sha256 check then refuses (fails closed); a writer's temporary-file-and-rename
  breaks the link instead. `labels.jsonl` and `fixed_labels.jsonl` merged by ID; `checks.yaml` merged by
  key; `reviews.jsonl` concatenated. The copy is incremental (only changed files rewritten, orphans
  removed last).
- **`merge.json` is a cache, not a trust root:** it records both sources' set versions, a manifest
  (path, size, modification time, inode, sha256) of every source file and every merged file, the
  merged version, each source's ID list, and the private commit and dirty flag. The resolver and the
  gate recompute the merge of the two sources' label, fixed-label and `checks.yaml` files in memory
  (a few hundred lines) and require the merged files and the merged version to equal it, so a
  consistent hand edit of the merged files and `merge.json` is still refused.
- **The resolver (W2, OD-648), three cases:** no private set: the public set; private set present and
  the merged set fresh: the merged set; private set present and the merged set missing or stale:
  refused with "run `ecf eval build`". It lives in `ecf.eval`, shared by the CLI and the service
  (import-linter forbids only `ecf` → `ecf_server`; `ecf_server` already imports `ecf.eval`); the CLI
  sends the logical path, the absolute `<repo top>/tests/eval/<set>` (the service runs under launchd
  and the API refuses relative roots, `api.py:1013-1015`), and the service resolves it (the API's
  `labels.jsonl` check moves after resolving). Freshness is judged by size, modification time and
  inode, with a sha256 check only on a mismatch (hashing the whole set takes about 27 ms warm; stat
  about 1 ms). Every `--root` reader goes through it: `ecf eval run`, `/ecf-eval`, `rescore`,
  `compare`, `rules test`, `confirm-set`, `null-drafts`, `judge-drafts`, `routes` (`cli.py:1640`),
  `status` and `show` (and `status`'s `--root` hints), the gate and the local-only tests. **Stored drafts
  roots:** `draftruns.Registration.root`, the `root` in `Registration.options` (`claude_eval.py:689`,
  read on resume at `:696`), the null run's side file (`draftjudging.py:372`) and the calibration side
  file (`draftcalibration.py:144`); their readers (`draftjudging.root_of`, `draftjudging.py:64`, used by
  judge-drafts, calibration and settle-drafts, and `claude_eval.py:2243`'s status hint) go through the
  resolver, so runs from before the move keep working. The gate's fingerprint uses a path-only mode
  (no hashing, no `git`); the gate re-checks freshness inside `inputs()`, and `GATE_VERSION` is bumped
  so cached results from before the change are recomputed.
- **The stored root:** `EVAL_ROOT` stores the logical absolute `<repo top>/tests/eval/<set>` path,
  which is what `remember_root` already stores (`str(root.resolve())`, `evalrun.py:765`), so no stored
  value needs migrating; `remember_root`,
  `_check_root` (`src/ecf_server/claude_eval.py:819-829`), the gate's version check and its fingerprint
  resolve it through the resolver, so the first gate run after the move is accepted and the public
  `labels.jsonl` is never touched.
- **Set version unchanged:** the merged label files are byte-for-byte today's (canonical sorted lines,
  every ID equal to its file stem; checked 2026-11-05), so the merged set's version is today's.
- **One key:** the builder sorts rows by ID and asserts each card's ID equals its file stem.
- **Fail closed:** an ID in both sets fails the build; a row whose file is missing or whose sha256
  differs is reported by the loader, and a run meant for the gate refuses on any.
- **Torn reads:** `set_version` and `load` read each file once; each case's message is re-checked against
  its sha256 when it runs.

### The gate guard (V2, restated by OD-643)

- **Primary guard: the version.** For a gate run the resolver must return a fresh merged set, and the
  run's `set_version` must equal that set's verified version. A public-only set's version differs, so
  the version alone proves the run read the whole set. A for-gate run refuses up front when the
  private set is absent.
- **Second guard: counts by membership.** Each run records `public_cases`, `private_cases` and
  `private_injection_cases` (counted from `merge.json`'s ID lists, so `starter-injection` (V3) doesn't
  count) and the merged version; the gate refuses any of them being 0.
- **References:** `fraud_guard_cases` and (unless `--fraud-only`) `confirmed` are compared with
  `load(merged, fraud_only=...)` on the current verified merged set, never with the run's own numbers.
- **Runs from before the guard** (such as `ce3be1df`): accepted only when their `set_version` equals
  the current verified merged version.
- **Dirty or stale clone:** a for-gate run checks, live at its start and again whenever the gate's
  compute reruns, that neither `eval/private` nor `tests/eval/<set>` has uncommitted changes and that
  the private HEAD is contained in its remote-tracking branch (`git branch -r --contains`, no
  network); the run records the private commit and the merged version in its own metrics, and the
  gate's detail shows them. `git` runs in the service (the gate computes there, `gate.py:181-206`) as
  `<git> -C <absolute repo top> --no-optional-locks -c core.fsmonitor=false -c
  core.hooksPath=/dev/null` with `GIT_OPTIONAL_LOCKS=0`, the binary's absolute path found once and
  stored, a timeout of a few seconds, an argv test; any `git` error, timeout or missing binary counts
  as not clean, with its message. So that a commit or push is noticed without a subprocess per tick,
  the fingerprint stats `.git/HEAD`, the branch ref (or `packed-refs`), `refs/remotes` and `.git/index`
  in both repositories. The set-level part of the check (merge, load, `git`) runs once per request and
  is cached by (`merge.json` stat, private HEAD, label-file stats), not once per address. A
  clone behind its origin is an accepted limit (SPEC §16); the code merge gate checks the commit is
  pushed.
- The gate's fingerprint stats `merge.json` and both sources' label files (hashing only inside the
  gate's compute).
- M1 inherits the version guard for the uploaded private part.

### Builder refusal

- A card in a public set carrying a decision-1 marker is refused at build, except the exact ID
  `starter-injection` whose bytes hash to the pinned sha256 of its current file
  (`afebb16654e9867261b6dc457941ec5a8e2c2bb0093d63d5a8f35f7201d19a28`, 2026-11-05; OD-654). The card
  differs from the shipped `injection` template (`case_templates.py:39-57`): it has `author: claude`,
  `rule: fraud_guard` and `must_escalate: true`, added in 8a4f0f2 (OD-252) while the template wasn't, so
  a pin to the template would refuse today's card. One constant (ID and sha256) shared by the builder,
  the sdist test and a CI marker scan of `tests/eval/**/cases` on every push; a test checks the card
  equals the template's output plus exactly those keys.
- `ecf eval new-case --template injection` writes to the private set, or refuses without one.
- An `eml/` or `.build/` file with no card in its set fails the build.

### Private repository

- First commit: `.gitattributes` with `* -text` (`.eml` files are CRLF and their sha256 depends on it),
  `.gitignore` with `.build/`, `*.tmp`, `.merged/`, `heldout3/eml/` (heldout3's `.eml` files stay
  untracked, as in the public repository), and a README saying it holds the private parts of
  ecf's eval sets.
- The hygiene scan covers both sets; it is the pre-commit check for both repositories.

### Releases and CI

- `tests/test_sdist_contents.py` asserts no member under `eval/` and no marked card under
  `tests/eval/`; SPEC §16 states the sdist fact.
- CI has no private set: it runs on the public sets. Tests that copy or name moving cards are rewritten
  on cards built inside the test (never a copy of a private card):
  `tests/test_claude_eval.py:58-70`, `:1333-1343` (`inj-hidden-html-junk`); `tests/test_evalrun.py:42-50`,
  `:76-85`, `:865`; `tests/test_eval_gate_guard.py:25-34`; `tests/test_eval_jev.py:59-75`, `:409`;
  `tests/test_config.py:258-287` (`fraud-hidden-html`); `tests/test_archived_results.py:53-56` (fixtures
  hold `attachment-name-injection`, `inj-quoted-thread`); `tests/test_draft_set.py:28-37`, `:100-104`,
  `:118-158`, `:194-200` (make-up and `checks.yaml` checks run on the merged set, marked local-only).
  `starter-injection` copies stay (V3).
- The macOS merge gate runs twice: with the private set present (0 skipped) and with it moved aside
  (CI's view), where only tests marked `private` may skip (the allowed skip list; `docs/agents/testing-and-merge.md`
  says so). `tests/test_corpus_expected.py` and `tests/test_schema_v3.py` also run on the merged set
  when it is present (local-only).

### Operator and agent rules

- `ecf eval build` builds both sets in one command and ends with "commit `tests/eval/<set>` and
  `eval/private/<set>`". A change to the builder rebuilds both before the gate.
- The merge gate requires the private repository clean and pushed; the merge commit names both
  commits.
- AGENTS.md: reading or writing `eval/private/` in any checkout, and writing a card with an injection
  marker, join the list reserved for Claude Code at the top; a Codex branch that touches the builder
  says so in its Handoff, Claude rebuilds the private sets, and the operator re-confirms any private
  row the rebuild unconfirmed before the gate.
- `eval-synthetic.md`: a private card's text is never quoted in tests, docs or fixtures. Result files
  and the gate's detail name private IDs (accepted: closed vocabulary only, OD-259).
- CONTRIBUTING: without the private repository, runs use the public set, have no injection slice, and
  can't count for the gate.

## Steps

Each write, commit and push needs the operator's OK.

1. **Inventory:** done (above).
2. **Phase R:** done on draft 1 (Fable), drafts 2, 3 and 4 (Opus delta). Draft 5 gets an Opus check of
   its changes (OD-577).
3. **Code** on a branch (Steps 3, 5 and 6 share it; the private repository's commit is pushed before
   Step 7): the atomic-write helper in every writer, `writable_root()`, `refresh_merged` with its lock,
   the resolver in `ecf.eval`, `merge.json` and the in-memory recomputation, the stored-root and draft
   registry changes, one-read loading and per-case re-check, missing-file reporting, the gate guard,
   path-only fingerprint with the `git` state stats, the hardened live dirty check, the builder refusal
   with the byte pin and orphan
   check, ID ordering, `new-case` routing, the sdist test and CI marker scan, the test rewrites, a script
   listing the exact cards to move, `merge_sources()`, the drafts roots, `routes` and the whole-set
   modes, the per-request gate cache. Tests build public and private sets in temporary folders, so CI
   never needs the private repository. *Pass:* a property test splits a built set at several cut points
   (first row, last row, interleaved, neither source with `fixed_labels.jsonl`, only the public one with
   it (synthetic's real shape), an empty public or private set) and the merged bytes equal the unsplit
   files, with no merged `fixed_labels.jsonl` when no source has one; a corrupted `.eml` is repaired by
   a rebuild; a source writer replacing a hard-linked `.build/` file while the merged copy is held open
   leaves the open copy's bytes unchanged; `git` failing or timing out makes a for-gate run refuse;
   a commit in the private repository changes the gate's fingerprint; a status call over several
   addresses runs the set-level check once (its time recorded); the move script's real list applied to
   copies of all three real sets in a temporary folder gives merged label bytes equal to today's,
   `checks.yaml` parsing equal, and versions equal; a consistent hand edit of the merged files and
   `merge.json` is refused; a made-up run from before the guard is accepted only when its version
   equals the verified merged version (refused when it differs, when the private set is absent, and
   when the merged set is stale); a rebuild during a held-open merged file leaves its bytes unchanged;
   a public-only for-gate run is refused; full suite green.
4. **Private repository:** the operator creates it (private, GitHub); cloned into `eval/private/`;
   first commit as above. *Pass:* `git check-ignore eval/private` true; `.gitattributes` present.
5. **Move:** first, on main's installed code (before the branch is installed, and before Step 4
   creates any `eval/private/<set>`), record **P**, per set and read-only: for `synthetic`,
   `set_version` and a `null` run's case, confirmed and fraud-guard counts; for `drafts`, `set_version`,
   `evalrun.load`'s case count, every `card_digest` and the draft registry's `set_digest`; for
   `heldout3` (cards and labels only, no `.eml` or `.build/`), `set_version` and its 30 rows. Not
   `null-drafts`: its run ID is `sha256("null|" + version)`, so a before and after run would share a
   file. **R** is `evalrun.latest` for the current digest if it passes the gate on main, else there is
   no R; its gate check, `rescore --synthetic` and `compare` results are taken then too (as of
   2026-11-05: `synthetic` `a6642d21bfd5eaaf`, `drafts` `ae1111e7bc117b23`, `heldout3`
   `07625d6cafa4b278`, 396/396/164, R = `ce3be1df`; cards added before the move change all of these,
   which is expected). Then `git rm` the listed cards and every file naming them from the public
   repository and add them to the private one. *Pass:*
   - no moved ID is a path under `cases/`, `eml/` or `.build/`, nor a row or key in the public
     `labels.jsonl`, `fixed_labels.jsonl`, `reviews.jsonl` or `checks.yaml` (a parsed check); `git grep
     -lF -f <ids>` finds them only in these names-only files: SPEC.md, `docs/adr/0026*`,
     `docs/roadmap/m8-model-written-drafts.md`, `planning-docs/EVAL-PRIVATE.md`,
     `planning-docs/SYSTEMONE-MODEL-TESTING-PLAN.md`, `planning-docs/v3.0.0-PLANNING.md`,
     `planning-docs/v3.0.1-PLANNING.md`, `tests/data/s3b_noise_pair.json`; and no test opens a path under
     a moved ID;
   - a fresh clone of the private repository: every row's sha256 matches its file (built rows after
     `ecf eval build`); `git ls-files --eol` shows CRLF for `.eml`;
   - merged versions equal P; the real-set copy check (Step 3) is one-off and local only; the draft
     registry's confirmed `set_digest` unchanged; the merged
     `checks.yaml`, parsed, equal to the public one before the move; a drafts load with unchanged
     `card_digest`s;
   - no row's file missing; a `synthetic` `null` run after the move (without `--subset`) equals the
     before-run on the keys both runs have, apart from run IDs, timings and the membership keys;
     `public_cases + private_cases` equals the before-run's cases and `private_cases` equals the move
     script's count; the null run's stored root (the draft null-floor side file) resolves to the
     merged set;
   - if R exists: its gate check, `rescore --synthetic` (excluding timings and the recomputed
     measurements) and `compare` (R against the before-run and R against the after-run) give the same
     results before and after; if not, the gate's synthetic detail is the same before and after (and the gate's evidence
     needs a fresh C run after the move, with the operator's go-ahead); a public-only for-gate run is
     refused.
6. **Docs:** `.claude/rules/eval-synthetic.md`, `planning-docs/GENERATE-FAKE-TESTING-EMAILS.md`, SPEC
   §16 (the pins, the sdist fact, the gate guard), CONTRIBUTING, AGENTS.md, CHANGELOG.
7. **Merge:** a diff review on Fable, the macOS merge gate (twice, as above), push, CI watched.

## Measured costs (performance reviewer, 2026-11-05)

`set_version` 0.7 ms today, 1.6-3.2 ms merged; `load_cards` 381 ms; hygiene scan 275 ms; building the
394 small cards 164 ms, `scanned-invoice-17mb` 2.8 s; `evalrun.load` 34 ms; sha256 of every file in the
set about 27 ms warm, stat about 1 ms (round 3). Round 4: merging the label files 1.5 ms,
`checks.yaml` parse 23 ms, `git status` and `git branch -r --contains` about 10 ms each. The merged set
costs about 4.4 MB of copies across the three sets (`synthetic` cases 1.5 MB and `eml` 1.7 MB,
`drafts` 0.57 and 0.54 MB, `heldout3` 0.12 MB) plus linked `.build/`; Step 3 records the measured
refresh time.
