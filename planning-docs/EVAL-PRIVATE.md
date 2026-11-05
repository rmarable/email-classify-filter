# Move the adversarial eval subset to `eval/private/` (OD-618)

**Status: draft 2, 2026-11-05. Not built.** Draft 1 went through Phase R (four Fable reviewers:
safety, measurement, cost and design, performance); every recommendation was accepted and decisions
V1-V5 made (operator, 2026-11-05). Findings: `state-archive/eval-private/review-findings.md`. Code
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
  hides nothing; the refusal exempts `starter-*`.
- **V4, the size and encoding evasion cards stay public** (`padded-text-part`,
  `scanned-invoice-17mb`): SPEC already documents the limits they test.
- **V5, `heldout3`'s 4 injection cards move** by the same scheme, never deleted (the blind batch's
  make-up stays intact).

SPEC rows: OD-626 (which cards; decisions 1, 4, V5), OD-627 (existing cards, the private repository; decisions 2, 3), OD-628 (V1), OD-629 (V2), OD-630 (V3, V4). Their parent OD-618 is on branch `m1-aws-mode` until M1's planning merges.

## Inventory (Step 1, 2026-11-05; full list `state-archive/eval-private/inventory.md`)

| Set | Cards | Move | Fraud-guard cards that move |
|---|---|---|---|
| `synthetic` | 396 | about 49 (50 by the list, minus `starter-injection`; a reviewer counted 48 by markers alone; the Step 4 script settles it) | about 44 of 164 |
| `drafts` | 134 | 54 (`d-inj-*`) | 0 |
| `heldout3` | 30 | 4 (`h3-inj-*`) | 1 |

Current set versions (2026-11-05): `synthetic` `a6642d21bfd5eaaf` (SPEC §16.2's M8 release gate),
`drafts` `ae1111e7bc117b23`.

## Design

### The overlay (V1)

- **Two standalone sets per name:** the public one (`tests/eval/<set>`) and the private one
  (`eval/private/<set>`), each a full set layout of its own: `cases/`, `eml/`, `.build/`,
  `labels.jsonl`, `fixed_labels.jsonl`, `reviews.jsonl`, and for drafts `checks.yaml`. Every existing
  tool already takes `--root`; the operator labels, reviews, settles and agrees on the set that owns
  the card.
- **The merged set:** `ecf eval build` builds the owning set, then, when the private set exists, writes
  `eval/private/.merged/<set>/` (gitignored by the private repo): copies (not symlinks: `load`
  rejects files outside its root, `src/ecf_server/evalrun.py:305-306`) of both `cases/` and `eml/`,
  `labels.jsonl` and `fixed_labels.jsonl` merged by ID, `reviews.jsonl` concatenated, `checks.yaml`
  merged by key, and `merge.json` recording both source sets' versions and the private repository's
  commit and dirty flag.
- **One rule for finding it:** a helper resolves the set a run reads: for `tests/eval/<set>` in a
  checkout, the merged set when it exists and its stamp matches both sources, else the public set.
  `ecf eval run`, `/ecf-eval`, `rescore`, `compare`, `rules test` and the gate's remembered root all go
  through it. A stale merged set (a source changed since the merge) is refused with "run `ecf eval
  build`".
- **Set version unchanged:** the merged files are byte-for-byte today's (both are canonical sorted
  lines; checked 2026-11-05), so the merged set's version is today's and no gate re-run is needed.
  M1's Step 1b reference stays valid.
- **One key:** the builder sorts rows by ID (byte-neutral today) and asserts each card's ID equals its
  file stem, so a future ID that is a prefix of another can't make the merge differ from a
  single-set build.
- **Fail closed:** an ID in both sets is an error at build; a label row whose file is missing or whose
  sha256 differs is reported by the loader (today it skips or unconfirms silently,
  `evalrun.py:305-311`), and a run meant for the gate refuses on any.
- **Torn reads:** `set_version` and `load` read each file once and derive rows and hash from the same
  bytes; each case's message is re-checked against its sha256 when it runs ("changed during the run").

### The gate guard (V2)

- Each run records `public_cases`, `private_cases` and `injection_cases` (cases with
  `safety.injection_target`) and the merge stamp in its metrics.
- The gate's synthetic check refuses a run with `private_cases == 0` or `injection_cases == 0` ("the
  set's private part wasn't present"), and a run whose stamp differs from the merged set it sees now.
  `fraud_guard_cases` is compared on every run, `confirmed` only when the run isn't `--fraud-only`.
- The gate's fingerprint covers the merged set's files, so a private change triggers a recompute.
- M1 inherits the same check for the uploaded private part (M1 doc R10, "Evals in AWS mode").

### Builder refusal

- A card in a public set carrying a decision-1 marker is refused at build (except `starter-*`, V3).
- `ecf eval new-case --template injection` writes to the private set, or refuses without one.
- An `eml/` or `.build/` file with no card in its set fails the build (no orphans keep text public).

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
  (CI's view).

### Operator and agent rules

- `ecf eval build` builds both sets in one command and ends with "commit `tests/eval/<set>` and
  `eval/private/<set>`". A change to the builder rebuilds both before the gate.
- The merge gate requires the private repository clean and pushed; the merge commit names both
  commits.
- AGENTS.md: Codex can't see `eval/private/`; adversarial cards are Claude's and the operator's
  work; a Codex branch that touches the builder says so in its Handoff, and Claude rebuilds the
  private sets before the gate.
- `eval-synthetic.md`: a private card's text is never quoted in tests, docs or fixtures. Result files
  and the gate's detail name private IDs (accepted: closed vocabulary only, OD-259).
- CONTRIBUTING: without the private repository, runs use the public set, have no injection slice, and
  can't count for the gate.

## Steps

Each write, commit and push needs the operator's OK.

1. **Inventory:** done (above).
2. **Phase R:** done (draft 1). This draft gets an Opus check of the changes (OD-577).
3. **Private repository:** the operator creates it (private, GitHub); cloned into `eval/private/`;
   first commit as above. *Pass:* `git check-ignore eval/private` true; `.gitattributes` present.
4. **Code** on a branch: the merge in `ecf eval build`, the resolving helper, the stamp and staleness
   refusal, one-read loading and per-case re-check, missing-file reporting, the gate guard and
   fingerprint, the builder refusal and orphan check, ID ordering, `new-case` routing, the sdist test,
   the test rewrites, a script listing the exact cards to move. *Pass:* a property test splits a
   built set in a temporary folder at several cut points (first row, last row, interleaved, a private
   set without `fixed_labels.jsonl`, an empty private set) and the merged bytes equal the unsplit
   files; full suite green with and without a private set.
5. **Move:** `git rm` the listed cards and every file naming them from the public repository; add them
   to the private one. *Pass:*
   - `git ls-files | grep -F <id>` is empty for every moved ID (cards, `.eml`, label rows, fixed
     labels, reviews, `checks.yaml`);
   - a fresh clone of the private repository into a temporary folder: every row's sha256 matches its
     file; `git ls-files --eol` shows CRLF for `.eml`;
   - merged versions: `synthetic` `a6642d21bfd5eaaf`, `drafts` `ae1111e7bc117b23`, and the draft
     registry's confirmed `set_digest` unchanged (`src/ecf_server/draftruns.py:94`);
   - a `null` run before and after: `cases[*].expected` and the summary equal apart from run IDs and
     timings, with cases 396, confirmed 396, fraud-guard 164;
   - `ecf eval rescore --synthetic` on run `ce3be1df` reports `set_changed: false` and reproduces 335/396
     and 164/164; `ecf eval compare` between the two null runs is accepted without `--subset`.
6. **Docs:** `.claude/rules/eval-synthetic.md`, `planning-docs/GENERATE-FAKE-TESTING-EMAILS.md`, SPEC
   §16 (the pins, the sdist fact, the gate guard), CONTRIBUTING, AGENTS.md, CHANGELOG.
7. **Merge:** a diff review on Fable, the macOS merge gate (twice, as above), push, CI watched.

## Measured costs (performance reviewer, 2026-11-05)

`set_version` 0.7 ms today, 1.6-3.2 ms merged; `load_cards` 381 ms; hygiene scan 275 ms; building the
394 small cards 164 ms, `scanned-invoice-17mb` 2.8 s; `evalrun.load` 34 ms. The overlay adds one copy
of each set (about 30 MB for `synthetic` with `.build/`).
