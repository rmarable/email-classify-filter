# Move the adversarial eval subset to `eval/private/` (OD-618)

**Status: draft 3, 2026-11-05. Not built.** Draft 1 went through Phase R (four Fable reviewers:
safety, measurement, cost and design, performance); every recommendation was accepted and decisions
V1-V5 made (operator, 2026-11-05). Draft 2 (80a5aaf) went through an Opus delta check (four
reviewers); every fix was accepted (operator, 2026-11-05) and is folded in here; OD-643 restates the
gate guard (amends OD-629). Findings: `state-archive/eval-private/review-findings.md`. Code
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

SPEC rows: OD-626 (which cards; decisions 1, 4, V5), OD-627 (existing cards, the private repository; decisions 2, 3), OD-628 (V1), OD-629 (V2), OD-630 (V3, V4). Their parent OD-618 is on branch `m1-aws-mode` until M1's planning merges.

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
  writes (label, confirm, review, settle, agree, expect, `fixed_labels`, `new-case`, build) writes to
  the set that owns the card: a single-ID command finds the owning set itself; every writer refuses a
  root holding `merge.json` ("write to `tests/eval/<set>` or `eval/private/<set>`").
- **Finding the private set:** `<repo top>/eval/private/<set>` for `<repo top>/tests/eval/<set>`, nothing
  else; a worktree or clone without it (Codex, `.claude/worktrees`, `../ecf-m1`) has no private set and
  says so ("public set only").
- **The merged set:** `refresh_merged(set)` writes `eval/private/.merged/<set>/` (gitignored by the
  private repository). It runs at the end of every writer and of `ecf eval build`, which builds only
  the owning set. Contents: `cases/`, `eml/` and `.build/` (hard links where possible: a hard link
  passes `load`'s check that files resolve inside the root, and every writer replaces files through a
  temporary file and rename, which breaks the link rather than changing the source); `labels.jsonl`
  and `fixed_labels.jsonl` merged by ID; `checks.yaml` merged by key; `reviews.jsonl` concatenated
  (nothing measured reads it; each card's lines keep their order). The copy is incremental (a file is
  rewritten only when its sha256 differs; orphans removed last; the label files and `merge.json`
  written through a temporary file and rename), so a running eval never sees a file change under it.
  Merged files are read-only.
- **`merge.json`:** both sources' set versions, a manifest (path, size, sha256) of every source file
  the merge read and of every merged label, fixed-label and `checks.yaml` file, the merged set's own
  version, each source's ID list, and, for information only, the private repository's commit and
  dirty flag.
- **The resolver, three cases:** no private set: the public set; private set present and the merged set
  fresh (every manifest entry matches): the merged set; private set present and the merged set missing
  or stale: refused with "run `ecf eval build`". `ecf eval run`, `/ecf-eval`, `rescore`, `compare`,
  `rules test`, the gate and the local-only tests go through it.
- **The stored root:** `EVAL_ROOT` stores the logical `tests/eval/<set>` path; `remember_root`,
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
- **Dirty or stale clone:** a for-gate run refuses when the private repository is dirty; a clone behind
  its origin is an accepted limit, stated in SPEC §16, and the gate's detail shows the private commit.
- The gate's fingerprint stats `merge.json` and both sources' label files (hashing only inside the
  gate's compute).
- M1 inherits the version guard for the uploaded private part.

### Builder refusal

- A card in a public set carrying a decision-1 marker is refused at build, except the exact ID
  `starter-injection` (one constant shared by the builder, the sdist test and a CI marker scan of
  `tests/eval/**/cases` on every push).
- `ecf eval new-case --template injection` writes to the private set, or refuses without one.
- An `eml/` or `.build/` file with no card in its set fails the build.

### Private repository

- First commit: `.gitattributes` with `* -text` (`.eml` files are CRLF and their sha256 depends on it),
  `.gitignore` with `.build/`, `*.tmp`, `.merged/`, and a README saying it holds the private parts of
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
2. **Phase R:** done on draft 1 (Fable) and draft 2 (Opus delta). Draft 3 gets an Opus check of its
   changes (OD-577).
3. **Code** on a branch: `refresh_merged` and the writers' refusal, the resolver, `merge.json`, the
   stored-root resolution, one-read loading and per-case re-check, missing-file reporting, the gate
   guard and fingerprint, the builder refusal and orphan check, ID ordering, `new-case` routing, the
   sdist test and CI marker scan, the test rewrites, a script listing the exact cards to move. Tests
   build public and private sets in temporary folders, so CI never needs the private repository.
   *Pass:* a property test splits a built set at several cut points (first row, last row,
   interleaved, a private set without `fixed_labels.jsonl`, an empty private set) and the merged bytes
   equal the unsplit files; a hand-edited merged file is refused; a public-only for-gate run is
   refused; full suite green.
4. **Private repository:** the operator creates it (private, GitHub); cloned into `eval/private/`;
   first commit as above. Whether heldout3's private `.eml` files are committed follows the public
   rule (they aren't today: not built, not tracked), and `ecf eval build` builds heldout3 when asked.
   *Pass:* `git check-ignore eval/private` true; `.gitattributes` present.
5. **Move:** `git rm` the listed cards and every file naming them from the public repository; add them
   to the private one. *Pass:*
   - no moved ID is a path under `cases/`, `eml/` or `.build/`, nor a row or key in the public
     `labels.jsonl`, `fixed_labels.jsonl`, `reviews.jsonl` or `checks.yaml` (a parsed check); `git grep
     -lF -f <ids>` finds them only in an allow-list of names-only mentions (SPEC, ADRs, the M8 roadmap,
     rewritten fixtures);
   - a fresh clone of the private repository: every row's sha256 matches its file (heldout3 after a
     build); `git ls-files --eol` shows CRLF for `.eml`;
   - merged versions: `synthetic` `a6642d21bfd5eaaf`, `drafts` `ae1111e7bc117b23`, `heldout3`
     `07625d6cafa4b278`; the draft registry's confirmed `set_digest` unchanged; the merged
     `checks.yaml`, parsed, equal to 80a5aaf's; a drafts load of 134 cases with unchanged `card_digest`s;
   - no row's file missing; a `null` run before and after: `cases[*].expected` and the summary equal
     apart from run IDs and timings, with cases 396, confirmed 396, fraud-guard 164;
   - `ecf eval rescore --synthetic` on `ce3be1df` reports `set_changed: false` and reproduces 335/396 and
     164/164; `compare` between the two null runs is accepted without `--subset`; the gate's synthetic
     check is still ok on `ce3be1df`; a public-only for-gate run is refused.
6. **Docs:** `.claude/rules/eval-synthetic.md`, `planning-docs/GENERATE-FAKE-TESTING-EMAILS.md`, SPEC
   §16 (the pins, the sdist fact, the gate guard), CONTRIBUTING, AGENTS.md, CHANGELOG.
7. **Merge:** a diff review on Fable, the macOS merge gate (twice, as above), push, CI watched.

## Measured costs (performance reviewer, 2026-11-05)

`set_version` 0.7 ms today, 1.6-3.2 ms merged; `load_cards` 381 ms; hygiene scan 275 ms; building the
394 small cards 164 ms, `scanned-invoice-17mb` 2.8 s; `evalrun.load` 34 ms. With hard links the
merged set costs little disk; Step 3 records the measured refresh time.
