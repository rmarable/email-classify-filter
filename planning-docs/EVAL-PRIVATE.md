# Move the adversarial eval subset to `eval/private/` (OD-618)

**Status: draft 30, 2026-11-06. Steps 3-5 built (Step 5, the move, on 2026-11-10); Step 6 in build.** Draft 1 went through Phase R (four Fable reviewers:
safety, measurement, cost and design, performance); every recommendation was accepted and decisions
V1-V5 made (operator, 2026-11-05). Draft 2 (80a5aaf) went through an Opus delta check (four
reviewers); every fix was accepted (operator, 2026-11-05) and is folded in here; OD-643 restates the
gate guard (amends OD-629). Draft 3 (f63822c) went through a second Opus check; every fix was accepted
and decisions W1-W2 made (OD-647, OD-648, operator 2026-11-05). Draft 4 (4ff2eea) went through a
third Opus check (one high: the `starter-injection` pin); every fix was accepted (operator
2026-11-05) and is folded in, with OD-654. Draft 5 (43c1b2a) went through a fourth Opus check (no
highs); every fix was accepted (operator, 2026-11-05) and is folded in. Draft 6 (e555313) went through
a fifth Opus check (no highs); every fix was accepted (operator, 2026-11-05) and is folded in. Draft 7
(702fbb9) went through a sixth Opus check (no highs); every fix was accepted (operator, 2026-11-05) and
is folded in. Draft 8 (a6f83f8) went through a seventh Opus check, run by session fix-fraud-card-wording
after the M1 session handed this plan over (one high: a card shaped like OD-632's planned `long-093`
carries none of the markers); every fix was accepted and decisions X1-X3 made (operator 2026-11-05),
folded in with OD-633. Draft 9 (167a734) went through an eighth Opus check (two highs, both in X2 and
X3, written without checking the role rule, the shared tool environment or where an upgrade reads the
version); the operator accepted both recommendations (X4, X5, operator 2026-11-05), X2 and X3 are
withdrawn, and every other fix is folded in with OD-634. Draft 10 (a3cb9aa) went through a ninth Opus
check (two highs, both in draft 10's own additions: X5's per-string rule missed text the builder
assembles, and Step 5's worktree had no `.build/`); the operator accepted every fix (2026-11-05), and
X5 and Step 5 are rewritten from what each must guarantee (OD-635). Draft 11 (e9dd8c9) went through a
tenth Opus check (two highs: Step 5 copied the live write-ahead log over its backup, and X5's `ecf`
predicate guarded only the build, cost the `skeleton.py` move and disagreed with trigger 10 past its
10 MB part cap); the operator accepted two simplifications (X5 and X6 below, 2026-11-05), and every
other fix is folded in with OD-636. Draft 12 (c74ed3c) went through an eleventh Opus check (two
highs: CI never runs on a push that changes only `.md` files, every card included, so the stated CI
backstop didn't exist; and draft 12's recovery after the socket bind let a new run close a lost
attempt); every fix is folded in with OD-637, which also amends OD-643. Draft 13 (42060ef) went through a
twelfth Opus check (three highs: draft 13's recovery retries still closed attempts and lost restart
counts, and the card-scan workflow could be skipped); the operator accepted two simplifications (X7,
X8 below, 2026-11-05), and every other fix is folded in with OD-638. Draft 14 (301f856) went through a
thirteenth Opus
check (two highs, both in draft 14's own rules: skipping blobs already in `origin/main`'s history
scanned nothing on a push to main, and X7's all-or-nothing recovery jammed for good on a lost run whose
folder is gone); every fix is folded in with OD-639. Draft 15 (d4c335c) went through a fourteenth Opus
check with
no highs; every medium and low is folded in with OD-676 (the eval/private numbers continue at 676, agreed
with the M1 session, which holds 640-675). Draft 16 (3f44fac) went through a fifteenth Opus check (one
high, found by all four reviewers, in draft 16's own rule: scanning every release tag's tip with
nothing skipped would fail the daily run for good); the operator accepted a simpler recovery (X9
below, 2026-11-05), and every fix is folded in with OD-677. Draft 17 (a7ee94d) went through a sixteenth
Opus check with no
highs, the second in a row; every medium and low is folded in here with OD-678, and the card scan
(X5) and the start-up refresh (X9) are restated whole. Draft 18 (0e83cc4) went through a seventeenth
Opus check, again with no highs; every medium and low is folded in here with OD-679, including a wider
freeze on main during the build (operator, 2026-11-05). Draft 19 (232446b) went through an
eighteenth Opus check, the fourth with no highs; the operator chose to drop the per-push swap to
main's script (X10 below, 2026-11-05), and every other medium and low is folded in with OD-684.
Draft 20 (6014c90) went through a nineteenth Opus check, the fifth with no highs; every medium and
low is folded in with OD-685 (operator, 2026-11-05). Draft 21 (fd0109f) went through a twentieth
Opus check (one high, in draft 21's own approval rule: a missing approval line could never be fixed,
so the daily run would stay red for good); every fix is folded in with OD-686, and the merge gate is
back on a committed local merge (operator, 2026-11-05). Draft 22 (5b953ee) went through a twenty-first
Opus check with no highs; every medium and low is folded in with OD-687 (operator, 2026-11-05),
including a gate branch in its own worktree and a list of old blobs that no longer build. Draft 23
(f8c763c) went through a twenty-second Opus check (one high, in draft 23's own gate worktree: it had no
private set, and removing it could delete unpushed private commits); the operator chose a simpler
shape (X11 below, 2026-11-05), folded in with OD-688. Draft 24 (351d6f6) went through a
twenty-third Opus check (one high, found by two reviewers: the shared gate worktree had no lock, so
two sessions could push an ungated merge); every fix is folded in with OD-689 (operator,
2026-11-05). Draft 25 (9200581) went through a twenty-fourth Opus check with no highs, but its
mediums again came from the previous draft's own additions; the operator stopped the patch loop
(2026-11-06), and draft 26 cuts the card scan's guards back to their core (X12) and replaces the
start-up retry loop with plain lock waits (X13), with OD-695. Draft 26 (6d84350) went through a
twenty-fifth Opus check, scoped to the core, with no highs; it confirmed the underlying problem is
fixed, and its text fixes are folded in with OD-696 (operator, 2026-11-06). A delta check of draft 27
(8cb51a5) found it ready to build once one Step 3 test bound was corrected, done here with OD-697.
Step 3's fourth code review (round 4, Opus) found `git status` blind to an edit hidden by an index
flag; draft 29 proves the set's content against HEAD instead, with OD-704 (operator, 2026-11-06).
Round 5 found a committed symbolic link passed that check while the readers followed it to content
no commit holds, round 6 a link at or above the set folder, and round 7 that fix's own letter-case
match on case-sensitive volumes and a link at the merged folder; draft 30 refuses anything in a set
that isn't a plain file and works out a set's location in one place, through no link and by its
stored names, with OD-705 (operator, 2026-11-06).
Findings: `state-archive/eval-private/review-findings.md`. Code
references are as of `1d6558c` (main; `src/` unchanged at `bc98a9b`). Comes before M1 Step 1 (`docs/roadmap/m1-aws-mode.md`).

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
   in `drafts`: `expected.draft.group: injection`. The builder's refusal keys on exactly this list, widened by X1 (draft 9).
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

2026-11-05 (round 8, OD-633; its X2 and X3 withdrawn by X4, round 9):

- **X1, one marker predicate, widened:** `ecf.eval.markers.is_injection_card(card)`, with its rules in
  one constant, `INJECTION_MARKERS`, in a module of its own that every caller imports directly (no
  re-export), shared by the builder refusal, the
  sdist test, the move script, the private-card check, and the hook and workflow scans (X5). A card is
  marked when it has
  `injection_target`, `injection_phrase` or `hidden_text` at any level of its front matter
  (`injection_target` also sits under `expected.safety`, e.g. `injection-delimiter-escape`), a
  `threat:` containing `inject` (case-insensitive; `evasion` is not a marker, V4),
  or `expected.draft.group: injection`: front matter only, so the predicate is cheap and lives in
  `ecf`. Content is checked by trigger 10 itself on the built email (X5). Why: a card whose only sign is
  a trigger-10 phrase in its body (OD-632's
  planned `long-093`) passed every guard. A private card's `threat:` names its injection
  (`planning-docs/TRIGGERS-PAST-WINDOW.md` amended), and `long-093` joins CI's moved-ID refusal list
  now, before it exists.

2026-11-05 (round 9, OD-634):

- **X4, Step 5 runs on a scratch copy and installs nothing.** X2 (a copy of `prod`'s data folder) and
  X3 (a branch wheel at `3.2.1.devN`) are withdrawn: the copy keeps `install_role` `prod` (fixed at
  creation, `initsetup.py:39-51`), so `ecf upgrade --wheel` refuses it (`cli_upgrade.py:189-191`);
  every install runs from the one uv tool environment (`service_unit.py:34-39`), so a branch wheel
  would have replaced `prod`'s code too; and the upgrade reads the version from the wheel's
  `ecf_server/data/release.json` (`upgrade_check.py:102-103`, `:112`), not `src/ecf/__init__.py`.
  Instead P and the after-checks run on a copy of the install's data folder under a scratch
  `ECF_HOME`, named `evalmove`: P with the installed release's own binaries, the after-checks with
  `uv run` from the branch; the copy is deleted afterwards (Step 5). No wheel, no version change, no
  rollback. It stays a copy, not an export and restore: a restore clears `eval_runs.path`
  (`importer.py:191`), drops the `eval_root` setting (`import_plan.py:4-7`) and carries no
  `<data>/evals/` file (`export_bundle.py:60-68`), so R, the stored root and the side files would be
  gone. (Its service on a copy is replaced by X6, round 11: Step 5 reads the install's database
  through a backup and runs the `null` runs on fresh dev services.)
2026-11-05 (round 11, OD-636; X5 replaces draft 10's and draft 11's X5, X6 replaces X4's copy):

- **X5, content is checked by trigger 10 itself, in one script.** *Invariant:* no public card whose
  built email trigger 10 flags, whose card text carries a trigger-10 phrase, or (when new) that X1
  marks, leaves this machine through a guarded push, a merge or a release, apart from the pinned
  `starter-injection` and the known public blobs below, which are public already.
  - `scripts/check_card_injection.py` imports `ecf.eval.builder` and `ecf_server` (import-linter
    limits only `ecf`, `pyproject.toml:142-146`). For each card it checks, it builds the message
    (`build_bytes`) and runs `ecf_server.triggers.card_injection(raw)`, which returns
    `scan(texts_of(message.parse(raw)))["injection"]` (`message.py:242`, `triggers.py:465-469`);
    it also runs `card_injection` on each `.eml` it is given as bytes, and a public wrapper,
    `ecf_server.triggers.text_injection(text)`, on every string in the card file (front matter at
    any level and body; all three fold forms `scan` reads, entity-decoded included, `skeleton.folds`),
    so text the email never carries (`notes`, `expected`) is checked too (strict pyright covers
    `scripts/`, `pyproject.toml:108`, so the script imports no private name). `card_injection` and
    `text_injection` are added in Step 3. A public card or `.eml` any of these flags is refused,
    except the pinned `starter-injection` (V3, its card bytes and its built `.eml`'s sha256 pinned),
    which is exempt from X1 too.
    - PDF attachments are built as empty placeholders under the same file name (`card.model_copy`
    replacing each `generate: invoice_pdf` attachment, `cards.py:73-83`, so `build_message` is
    unchanged, `builder.py:88-96`): `message.parse`
    extracts no PDF text and `texts_of` reads only attachment names, so the render (5.5 s and 637 MB
    for `scanned-invoice-17mb`) buys nothing; a test checks `texts_of` is the same with the real PDF.
  - It fails closed: an import error, a card that doesn't parse or a build error refuses. It runs as
    `uv run --no-sync` (no install or sync; `--frozen` would still sync).
  - Where it runs: the pre-push hook, the card-scan workflow, the sdist test (every card and every
    `.eml` in the sdist, nothing skipped; `release.yml:53` runs it, so every release is checked in
    full) and the macOS merge gate (`--tree HEAD --no-skip` on the local merge commit, below).
  - **What each run checks.** For each blob it checks, the script runs trigger 10 (above) and, on a
    card, X1's front-matter predicate (so a marked card whose text trigger 10 misses, the most
    sensitive kind, is refused too). X1 is applied to main's tip and to range blobs that are not in main's
    history at the branch base (main's tip the branch sits on, pinned as a constant beside the list
    in Step 3 and moved with it if the branch is rebased; the freeze keeps main from adding a marked
    card after the freeze base, and Step 7 checks main from the freeze base, which never moves), so an
    old marked card
    that main edited before the move, or that a long-lived branch carries in when it merges main, isn't
    refused; it is not applied to other tips, since branches cut before the move hold more than 90
    marked public cards, all already in main's history. `--tree` applies X1 as main's tip does.
    Checked 2026-11-05 (round 18): after the move, main's tip holds no card X1 marks or trigger 10 flags
    but `starter-injection`; `heldout` and `heldout2` hold none.
  - **The card-scan workflow** (X8). The main CI workflow skips a push that changes only `*.md`
    files (`ci.yml:11`, `:14`), and every card is an `.md` file, so the scan runs in its own workflow,
    `.github/workflows/card-scan.yml`, set up as SPEC §17.4 sets CI up (the runner from
    `vars.CI_RUNNER`, actions pinned by SHA, `UV_VERSION` pinned as `ci.yml:24` does,
    `persist-credentials: false`, `uv sync --locked --no-dev`; no `[eval]` extra, since PDFs are
    placeholders), with `permissions: contents: read`, `fetch-depth: 0`, no path filter, no
    `concurrency` block (a group keeps only one pending run, so a range could be dropped) and never
    `pull_request_target`. Triggers:
    - `push` to any ref, tags included: the commits in `before..after`; for a new ref, or a `before`
      missing after a force-push, `after --not origin/main` (on main that range is empty, so there a
      missing `before` fails the run, closed); a deleted ref passes;
    - `pull_request`: `base..head`;
    - `schedule`, daily: main's copy of the workflow and code, which no commit message (`[skip ci]`)
      and no branch can skip; main's tip tree with nothing skipped; and every remote branch's and
      tag's tip tree and every commit not on main, reading only blobs not already in main's history
      (those are public already, and main's tip is scanned in full), so old cards on the 28 `v*` tags
      can't fail it after a schema change or a widened trigger 10 (about 13-14 s here at load 4.5,
      round 26). A new card on another ref that main's code can't parse (a branch that adds a card
      field; `cards.py:33` forbids extra fields) still gets X1 and `text_injection` on its raw front
      matter and body and fails on a hit; only its build is skipped, with a warning.
  - **One entry point**, `scripts/check_card_injection.py --ref <full refname> --revs <rev-list
    arguments>` (any rev-list spec, so `before..after`, `<local> --not <remote refs>` and `after --not
    origin/main` all fit). Main is any ref whose name is `refs/heads/main` or
    `refs/remotes/<remote>/main`: the hook passes the remote ref it is pushing to, from its stdin, so
    `git push origin foo:main` counts as main; the workflow passes `github.ref`; the daily run passes
    `refs/remotes/origin/*` and `refs/tags/*` from `for-each-ref`, and main is scanned as main before
    the other refs, so deduping blobs across refs can't hand it the list. For every commit, through one
    batched `git diff-tree --stdin -r -m --root --no-renames --diff-filter=d` process (one subprocess
    per commit would take about 45 s on this Mac for main's 1,143 commits, mostly process start-up,
    growing with main; batched 0.23-0.36 s, rounds 20-21): merge
    commits included, deleted files skipped, each blob with its own path; then the tip tree (`ls-tree
    -r`: the path checks, such as an `eml/` or
    `.build/` file with no card, and, except in the hook (X5), a build of the blobs the range didn't
    cover). Only plain files may sit under `tests/eval/`: a symbolic link or submodule entry there,
    or at `tests` or `tests/eval` itself, is refused by the range, the tip and the merge gate's tree
    scan, and the private repository's hook refuses one anywhere in its pushed range (OD-705). The
    set files' text check runs once per distinct string, and the hook skips it for a set file blob
    main's history already has (OD-705; round 5 measured 8.4-8.8 s on every push without this):
    every such blob to `2c421ac` was checked once, 55 blobs, 0 hits (Step 3 round 6), and main's own
    CI run checks every later one.
  - **The known public blobs.** One committed list, `tests/eval/known-public-injection-blobs.txt`,
    holds the blob IDs, nothing else, of every card and `.eml` blob trigger 10 flags in main's history:
    45 of 1,514 (929 card and 585 `.eml` blobs; 32 cards and 13 `.eml`; every one builds with today's
    code; counted on 2026-11-05 with this same `diff-tree` walk, since `rev-list --objects --
    tests/eval` drops one blob through history simplification), across 13 card IDs:
    `attachment-name-injection`, `bug-report-injection-archive`, `inj-fake-owner-approval`,
    `inj-lower-fraud-bankchange`, `inj-quoted-thread`, `inj-scam-claims-invoice`, `inj-spanish`,
    `inj-txt-attachment`, `injection-actor-archive`, `injection-classify-notification`,
    `injection-delimiter-escape`, `starter-injection` and `v3-inj-not-phishing`. Those bytes are public
    already, and moving cards doesn't unpublish them; branches and tags cut before the move hold only
    listed blobs (none outside main's history, 2026-11-05).
    - Committed in Step 3 (`--list-known` over main's history at the branch base; G3 keeps
      `tests/eval/` unchanged), so neither the hook nor the workflow refuses the branch's own public
      injection cards before the move.
    - **Which copy runs, and what guards the scan itself (X10, X12).** A push or pull-request run
      uses the pushed tree's workflow, script, list and code (GitHub runs a push's workflow from the
      pushed commit, as it documents; unverified here), so a branch can weaken its own runs, and a
      branch cut before Step 3 has no workflow and runs none; the daily run uses main's workflow and
      code. `tests/test_card_scan.py` pins the list's sha256 and count (45). Nothing more guards the
      scan's own code: a
      change to the script, the workflow, the hook, `src/ecf/eval/markers.py` (`INJECTION_MARKERS` and
      `is_injection_card`, imported directly by every caller) or what trigger 10 flags is reviewed like
      any other safety change (its diff review, and Phase R where the review rules apply), and only
      Claude Code pushes main (stated limit). Drafts 20-25's approval paragraph and check, gate
      worktree, lock, parent checks, direct-commit rules and daily recompute are withdrawn (X12): they
      guarded the guard against mistakes only, and each round's fixes to them produced the next
      round's findings.
      Tests: the hook and the workflow refuse a new flagged card and a new X1-marked card on a branch,
      on main and on a new ref; a listed blob passes on a branch and is refused on main's tip; a pushed
      ref whose object isn't a commit is refused; `git push origin HEAD:main` and `git push origin
      <sha>:main` are checked as pushes to main; a delete-only range leaving an orphan `.eml` is
      refused; a push to main with `before` missing fails; a new unparseable card on another ref with
      an X1 marker fails the daily run and one without is a warning; an old marked card on a `v*` tag,
      and an old tag blob a widened trigger 10 newly flags, leave the daily run green; the daily run
      scans a branch cut before Step 3 without error; the pin test matches the list.
    - Trigger 10 is re-run on the old blobs only when the list is recomputed, at Step 5 (the move
      commit) and Step 7 (the merge). Each recompute must equal the
      committed list both ways: every
      flagged blob listed, every listed blob flagged. If either differs, stop, show which blobs differ
      and why (a commit on main adding or changing a flagged card, or any change that alters what
      trigger 10 flags: `keywords.yaml`, `triggers.py`, `message.py`, `htmltext.py`, `skeleton.py`,
      `ecf/eval/builder.py`, `cards.py`, `padbank.py`, card parsing), and ask the operator before
      re-pinning.
    - **The freeze** (operator, 2026-11-05; widened to rows in draft 20): from the freeze base (main's
      tip when Step 3 starts, recorded in `state-archive/eval-private/`, where Step 7 reads it; a
      rebase never moves it) until the merge,
      no commit on main changes anything keyed by the 107 moving IDs (the move list; `long-093` is
      written in the private set after Step 7, 108 then) or the 13 listed card IDs: the card, its
      `.eml`, and its rows or keys in `labels.jsonl`, `fixed_labels.jsonl`, `reviews.jsonl` and
      `checks.yaml` (`.build/` is untracked); nor adds a card that X1 marks or trigger 10 flags; main has
      no hook or workflow until the merge to catch it. Main adds and edits these cards (2c971c0, d46bf25,
      db83787, cca7f7d, bfa00a0 since 2026-10-15) and their rows alone (845af58 added 30 `fixed_labels`
      and 30 `reviews` rows; 8bda36c; 612fd6b), and an
      edit after the move would meet a modify/delete conflict at the merge and leave the private copy
      stale; Step 7 checks it. Changes
      to the files that decide what trigger 10 flags (above) are advised against, not frozen: Step 7's
      both-ways recompute catches their effect and stops. The operator and the M1 session are told at
      Step 3.
  - **Skipping.** Only main's tip tree is scanned with nothing skipped, so a listed blob restored
    onto main is refused by the push run and the daily run. Every other ref, tags included, is
    scanned with the list: all 28 `v*` tags hold listed cards and can't change, and a new release is
    checked in full by the sdist test. The merge gate runs `scripts/check_card_injection.py --tree HEAD
    --no-skip` on the local merge commit (whole tree, nothing skipped), so a listed blob can't come
    back into main's tip through a merge. Draft 14's rule (skip anything in `origin/main`'s history)
    stays withdrawn.
  - A test reads the workflow file and checks its triggers. Stated limits: a push that skips its own
    run, or a branch that weakens its own scan (X10), is found by the next daily run if the card is in
    a tip or in a commit not on main, up to a day after it is public; a card added and removed on main
    by a push that skipped both the hook and CI is not found; GitHub may delay a scheduled
    run and disables schedules after 60 days without repository activity (unverified, as GitHub
    documents it); the scan covers cards and `.eml` files under `tests/eval/`, and set files' values
    with the text check only (OD-704), not fixtures or docs (no card text is ever quoted there, `eval-synthetic.md`);
    the hook builds only the pushed range's blobs (0.25-0.7 s for a push with no cards, rounds
    24-25; with the set-file check 0.14 s in the hook and 0.6-0.8 s in CI, Step 3 round 5); the daily run about 13-14 s here at load 4.5 (round 26), unmeasured on a runner.
  - **Card-only merges get the scan too.** A merge that changes only `.md` files counts as docs only
    and skips the macOS gate (`docs/agents/testing-and-merge.md:29-30`), and `ci.yml:11` skips
    `**/*.md`; so any merge that touches `tests/eval/` runs `--tree HEAD --no-skip` on the local merge
    commit first. The line
    saying so in `testing-and-merge.md` lands in the move commit, with the three guards (Step 3).
- **X6, Step 5 runs on fresh dev services, not a copy of the install.** With no R (the expected
  case, Step 5), nothing in P needs the install's live state: the `null` runs run on fresh `ecf-server
  dev` homes (memory secrets, no addresses, no `gh` poll), with the installed release's binary for P
  and `uv run` from the branch for the after-run. The install's database is read only through a
  SQLite backup into a scratch folder, by scripts, never by a service. This withdraws X4's service on
  a copy, its host rewrites and its network test. A fresh R before the move would need the
  install's live state; it is then re-planned and reviewed first.

2026-11-05 (round 13, OD-638):

- **X7, lost-run recovery checks each record's set before touching it.** Withdrawn in draft 17 by X9.
- **X8, the card scan also runs daily from main.** A scheduled run uses main's copy of the workflow,
  which no commit message and no branch can skip (X5, "The card-scan workflow"). Amended in draft
  15 (OD-639): already-public blobs are a fixed committed list, not "anything in main's history".

2026-11-05 (round 16, OD-677):

- **X9, a stale set is refreshed at service start, and recovery is today's.** Before recovering lost
  draft runs, the service refreshes the merged view of their sets once; then recovery runs exactly as
  today. Draft 13's tick retry and drafts 14-16's deferral (X7) are withdrawn (Design, "The merged
  set").

2026-11-05 (round 19, OD-684):

- **X10, push runs use the pushed tree.** Draft 19's per-push swap to main's script is withdrawn: the
  script still imported the branch's own scan code, and branches cut before Step 3 would fail every
  push. A branch's weakened scan is found by the daily run (main's code) (X5, "Which copy runs"); the
  scan-file diff and the rest of what drafts 21-25 added here are withdrawn by X12 (draft 26). Amended
  in draft 21 (OD-685): approval is checked per
  first-parent commit, the scan files are named exactly, and the merge gate's both-ways recompute
  covers trigger 10. Amended in draft 22 (OD-686): the hook checks approval before a push, a later
  commit can approve an earlier one, the daily run recomputes too, and the gate runs on a committed
  local merge. Amended in draft 23 (OD-687): no refusal by trailer, the gate on a gate branch in its
  own worktree with its tree recorded, and a list of old blobs that no longer build; draft 24 replaces
  those three by X11.

2026-11-05 (round 23, OD-688):

- **X11, a simpler shape for the scan's own guards.** The four scans (hook, push run, daily run,
  gate) and the known-blob recompute stay. Approval is the gate's shown scan-file diff plus one fixed
  paragraph, checked by the hook and the daily run and recoverable by a later commit. Merges and
  direct commits to main go through one persistent, set-up worktree, `../ecf-gate`, never removed by
  the flow. Withdrawn: the Codex-line rule (merges are `--no-ff`), the gated-tree record (the hook
  skips its tip build when the range changes nothing under `tests/eval/`), and the unbuildable-blob list
  (deferred to the first stricter schema, a stated limit). Amended in draft 25 (OD-689). Withdrawn in
  draft 26 by
  X12, except the hook building only the pushed range.

2026-11-06 (after round 25, OD-695; operator: "stop the whack-a-mole and address the problem"):

- **X12, the card scan's guards cut back to their core.** What stays: X1's marker and the builder's
  refusal; the sdist test; one script run by the pre-push hook (the pushed range, plus the tip's path
  checks), the card-scan workflow (push, pull request, and a daily run of main's tip unskipped and
  every other ref with the list) and the macOS merge gate (`--tree HEAD --no-skip`); the pinned
  known-blob list, recomputed at Steps 5 and 7. What goes: every guard on the guard (the approval
  paragraph and its check, the scan-file diff, the gate worktree and its lock, parent checks,
  direct-commit and tag rules, the daily recompute and history walk). The scan's own code is
  reviewed like any safety change. The underlying problem (rounds 20-25): each layer guarded the one
  below it against mistakes only, and its own fixes produced the next round's findings. The hazard
  behind the gate worktree (a held merge in the shared main checkout published by another session's
  push, aa6c5f2) is a repository-wide merge practice, not this plan's; it is raised with the
  operator separately.
- **X13, the start-up refresh waits on the lock.** Draft 19-25's retry loop (single non-blocking
  tries, re-resolves, jitter, a shared budget, floors and deadline cuts) is withdrawn: its single
  tries were what let two installs refuse each other. Recovery waits for each lock up to a per-set
  limit, and refreshes under the exclusive lock only if the set is still stale (Design, "The merged
  set").

SPEC rows: OD-626 (which cards; decisions 1, 4, V5), OD-627 (existing cards, the private repository; decisions 2, 3), OD-628 (V1), OD-629 (V2), OD-630 (V3, V4), OD-643 (the gate guard restated), OD-647 (W1, atomic writes and copies), OD-648 (W2, the shared resolver), OD-654 (draft 5: the `starter-injection` pin by its bytes; before-and-after measured on main's code), OD-633 (draft 9: X1-X3; amends OD-626 and OD-643), OD-634 (draft 10: X4, X5; amends OD-633), OD-635 (draft 11: X5 and Step 5 rewritten; amends OD-634), OD-636 (draft 12: X5 one script, X6 dev services; amends OD-635), OD-637 (draft 13: the card-scan workflow, recovery retries; amends OD-636 and OD-643), OD-638 (draft 14: X7, X8; amends OD-637), OD-639 (draft 15: X7 per record, the known-blob list; amends OD-638), OD-676 (draft 16: the list in Step 3 and pinned, main's tip unskipped, recovery limits first; amends OD-639), OD-677 (draft 17: X9, tags with the list, `--ref`, the merge-gate `--no-skip` scan; amends OD-676), OD-678 (draft 18: X5 scan and X9 refresh restated; amends OD-677 and OD-647), OD-679 (draft 19: main runs, X1 on post-move blobs, the wider freeze, X9 retry and close; amends OD-678), OD-684 (draft 20: X10, the scan-file diff, the daily run over all of main, the freeze on rows, X9's retry on a stale set; amends OD-679), OD-685 (draft 21: per-commit approval, exact scan files, the gate's recompute, the fixed freeze base, X9 bounded by time; amends OD-684), OD-686 (draft 22: approval checked before the push and recoverable, the gate on a committed merge, one scan pass, the daily recompute, X9's polled refresh; amends OD-685), OD-687 (draft 23: the gate branch and recorded tree, the unbuildable-blob list, Codex approval by a later commit, X9's floor for later sets; amends OD-686), OD-688 (draft 24: X11, the persistent gate worktree, approval by the hook and the daily run, the Codex rule, gated tree and unbuildable list withdrawn; amends OD-687), OD-689 (draft 25: the gate lock, parents checked before the push, the hook's range-only build, branch cards main can't parse as warnings, X9's capped waits; amends OD-688), OD-695 (draft 26: X12, the card scan's guards cut back to their core; X13, start-up lock waits; amends OD-689 and OD-677), OD-696 (draft 27: X13's refresh owns its lock wait, 5 s of waiting per set, other refs scanned only for blobs not in main's history, the private clone at Step 7; amends OD-695), OD-697 (draft 28: the Step 3 lock test's refresh bound; amends OD-696), OD-704 (draft 29: clean proven by content against HEAD, the fingerprint over every set file, recent files re-hashed; amends OD-648 and OD-643), OD-705 (draft 30: nothing in a set followed, links and special files
refused everywhere, a set's location never found through a link, ctime in every stat, the private
HEAD read once, merged `checks.yaml` checked against its halves, racy stats recorded after a read's
lock is released, the set-file text check once per string; amends OD-704). Their parent OD-618 is on branch `m1-aws-mode` until M1's planning merges.

## Inventory (Step 1, 2026-11-05; full list `state-archive/eval-private/inventory.md`)

| Set | Cards | Move | Fraud-guard cards that move |
|---|---|---|---|
| `synthetic` | 398 | 49 (50 marked by X1's predicate, checked 2026-11-05, minus `starter-injection`) | 44 of 166 (`long-091` and `long-092`, unmarked, stay public) |
| `drafts` | 134 | 54 (`d-inj-*`) | 0 |
| `heldout3` | 30 | 4 (`h3-inj-*`) | 1 |

Current set versions (2026-11-05): `synthetic` `9370cd54d96f28bc` (398 cards since OD-632's two cards;
SPEC §16.2's M8 release gate ran on `a6642d21bfd5eaaf`, 396 cards), `drafts` `ae1111e7bc117b23`.

## Design

### The overlay (V1)

- **Two standalone sets per name:** the public one (`tests/eval/<set>`) and the private one
  (`eval/private/<set>`), each a full set layout of its own: `cases/`, `eml/`, `.build/`,
  `labels.jsonl`, `fixed_labels.jsonl`, `reviews.jsonl`, and for drafts `checks.yaml`. Every tool that
  writes (label, confirm, review, `fixed_labels`, `generate`, `new-case`, build) writes to the set that
  owns the card: a single-ID command finds the owning set itself, and never returns the merged folder.
  Whole-set modes (`ecf eval label` with no ID and `--status`, `ecf eval label --review` with `--add`
  and its `--cases` globs, `_matching`, `cli.py:993`) cover both sets, each change written to the card's
  owning set. `ecf eval label --import-fixed` (`cli.py:1030-1045`; one import takes one branch: a fixed
  item through `fixed_labels.record`, or a card field through `review.record_page`) splits its IDs into
  a public part and a private part, validates every ID (owned by exactly one set) and every answer for
  both parts with validate-only functions split out of `record` and `record_page` (and
  `_refuse_judged` per set), then writes and logs each part in its owning set. A write failure after
  the first part leaves the import half-applied; a re-import is idempotent, as `record` already is. `settle`, `agree` and `expect` need a generated set's `manifest.json` (`gen.verify`,
  `settle.py:138`, `agree.py:128`, `expect.py:222`), which none of these sets has, so they keep refusing
  them and get no both-sets mode. One guard runs inside every writer before it writes, and its refusal names what caused it.
  It refuses a path whose resolved path (each link followed before a `..` after it, as the system
  goes) or any ancestor is a folder named `.merged` in any letter case
  (APFS ignores case; on Linux, unsupported until M5 (ADR 0020), a real `.Merged` folder is refused
  too) or holds a merged view's own `merge.json` (one with `merged_version`; a link or a file that
  doesn't read as JSON counts too). Inside a set nothing is written through a link (OD-705): every writer into a set names
  the set, `writable_set(root, path)` (a set is never guessed from what a folder holds). The set's
  folders (`cases/`, `eml/`, `.build/`, `work/`, `grades/`) and files (the set files, a generated set's
  `manifest.json`) must be plain folders and files, and so must every name from the set's folder down
  to the path written, which must lie below it with no `..`; only the set's own folder may be a link
  (an alias of the whole set), and a `..` in it after a link is refused, as the set location refuses
  it. The leftover sweep follows no link, not even that alias; a folder it can't open (a link, not a
  folder, unreadable) gets a line, and only a missing one is silent. The writers that name their set: `build_all`, `labels.write`,
  `fixed_labels`, `append_log`, the card writers `review.set_labels` and `write_expected` (a card is
  `<set>/cases/<id>.md`), `new-case` at `cli.py:942`, `generate` (its start, cards, work logs,
  manifest and the call folder `work/call` before each model call, each call's schema written new
  in a folder of its own there), `grade` and the leftover-file
  sweep; a test names each, requires its `writable_set` call to name the set's own folder, and
  finds every caller of `writable_set` anywhere in the source on that list or a named list of
  pre-flight checks, and
  tests link `work/`, `work/call/` and `grades/` and check that generate and grade refuse before any
  call or write. A helper that writes to a path its
  caller passes calls `writable_root(path)`, the view check alone: a model call (in a set, its
  caller first checks the call folder with `writable_set`) and the canary, `settle.export`, `mix.write_default`, `corpus_labels.save`, `codex_tool.write_catalog`,
  an `--export` page, checked before the service is asked, and the service's `ecf eval run --copy-to`
  result, a refused copy logged like any failed one, and a rescore, written beside the result file the
  user names (checked before any case is scored; a result in the install's own results folder is
  trusted as the run's record is, so a data folder the operator puts inside a view, or links into
  one, takes its rescores there too: a stated limit). A result file is written as a set file is, a new
  file renamed over its name, so a link, hard link or pipe already there is replaced, never written
  through; a write cut off between the two leaves its hidden 0600 temporary file in that folder,
  which every listing ignores: in the install's results folder the next run's save and the
  draft-run registry's save remove it once it is over an hour old; in a `--copy-to` folder nothing
  does (a stated limit: the user's own result). Every eval file is written through `setio`: a new
  file renamed over its name (the draft-run registry, judges' answers and corpus labels flushed to
  the disk itself, past the drive's cache (`F_FULLFSYNC` on macOS; a disk error raised), then
  their folder; result files and the draft-run journal are not (stated limits), a linked folder followed for the flush, and a
  folder that can't be opened or flushed, whatever the reason, left unflushed rather than the
  written file reported failed; the settle and export pages 0600, in a 0700 folder when
  ecf makes it, a folder already there left as it is), a new file that never replaces one (the
  default mix, a merged corpus's labels, flushed likewise: hard-linked to its name, or checked then renamed on a disk
  without hard links, a stated limit; a crash between the link and dropping the temporary name
  leaves that second name, swept after an hour in a set or results folder and kept until removed
  by hand elsewhere, a stated limit), or added to in place through one opener that refuses a link,
  hard link or pipe (the append-only logs and the draft-run journal; a journal refused that way
  says what to remove before the next attempt, and a recovery that fails records its cause and
  that the attempt is over, never a resume; a refused journal stops the
  run as a failed write does). Each model call's schema, and the answer its command line writes
  itself, go in a fresh 0700 folder of the call's own in the call folder, removed once read, so
  judges running side by side never touch each other's calls; a call holds a lock on its folder
  while it runs, so a later call or a build removes only a crashed call's folder (held by none,
  and over an hour old), never one whose call slept with the computer, and the build's check
  names one it leaves (on a disk that can't lock, a call runs unheld, and the sweep, unable to
  lock either, leaves its folder with a line saying so; a sweep from another computer that mounts
  the same share with locks could remove it past an hour, a computer asleep mid-call or clocks far
  apart, a stated limit). A test reviews
  the eval modules' ordinary code for file writes: it lists every call made directly in an eval
  module that can write a file, by what it names through the module's imports (an opener whose
  mode or flags aren't written out as read-only or can't be read: `open`, `io.open`, `os.open`,
  archive, compressed, `dbm` and `shelve` files; Path, os, shutil and temporary-file writes; a
  program run; a database connection; a log file handler; a file download; a system library's
  function (the merged view's `clonefile`, listed)), the same taken without calling it (assigned,
  passed, or reached by `getattr`), and the usual ways of reaching a module by another name (a
  computed name, a star import, `sys.modules`, `__builtins__`, `globals()`, a module of ours or
  the standard library's used as a value, the C modules behind `os` and `io`), and fails on any
  call not on its list. It is a check on ordinary code, not a proof: code written to hide a write
  from it can (a stated limit; review round 29, eighteenth delta check), and it sees direct calls
  only, so a write made inside a helper of a module outside the eval modules isn't listed, nor a
  method reached by a computed name on an object (stated limits). One such writer
  is outside `setio`: a corpus merge's sealed corpus is written by the export bundle's own writer
  (a new name, never through a link: created exclusively, then hard-linked to its name); a crash
  mid-write leaves its `.partial` file, which nothing removes (a stated limit; the next merge
  writes another name). The install's own result file goes in its data folder and gets no view check, so a
  stray `merge.json` above it never blocks a run's record. A test searches
  `src/ecf/eval`, `src/ecf/cli.py`, `cli_evalgen.py` and `cli_card_review.py` for file writes and
  requires each to follow a `writable_root` or `writable_set` call in its own function. Its named allow-list holds only
  the write helpers themselves and the merged view's own writers
  (`refresh_merged` and its helpers, the lock file's open); it never lists a whole module. It counts
  `open`/`fdopen`/`ZipFile` with a write mode or one not written out (`Path.open` on any receiver), `os.open` with any flag that
  isn't read-only, Path, os and descriptor writes (`mkdir` and `chmod` included), shutil copies and
  archives, and the `ctypes` `clonefile` call, so a new writer can't skip the guard. `append_log` and a generated set's work files append in place through that opener, which is safe: the merged `reviews.jsonl` is rebuilt, never linked. Removing an extra from
  the merged view walks it by folder handle, one open per level, so a tree deeper than the open-file
  limit (256 by default on macOS) is refused by name: a stated limit (such a tree is planted by hand).
- **Atomic writes (W1, OD-647):** every set writer writes through one helper (temporary file, then
  `os.replace`). Today `builder.py:160`, `review.py:175` and `:325` and `generate.py:592` write in
  place (checked 2026-11-05); they change to the helper, and the builder skips a file only when the
  file on disk exists, has the new bytes' size and hashes to the new bytes' sha256 (never by the old
  row alone, so a damaged or missing file is rewritten and `ecf eval build` still repairs a set), which
  also saves about 31 MB of writes per build.
- **Finding the private set:** `<repo top>/eval/private/<set>` for `<repo top>/tests/eval/<set>`, nothing
  else (repo top = the logical path's `parents[2]`, no `git` call; the logical path is the path as
  given, absolute, with a `..` folded only when the name it cancels isn't a link and refused after
  one (round 28); a set's location is never found through a link, OD-705: one function works out every path a reader, writer, lock or clean check
  opens; a symbolic link at the set folder, at `tests/eval` or `tests`, at `eval`, `eval/private`
  or `eval/private/<set>` (a private half given directly too), or at `.merged`, `.merged/<set>` or
  its lock file makes it no checkout set, refused by a for-gate run, every reader and every writer
  before anything is read or written; a refresh also walks the existing merged view (its folders and
  files, and `merge.json`, which is read without following a link or blocking on a pipe) for a
  link before it writes anything (rounds 8-9); the private half counts only when stored exactly as
  `eval/private/<set>`, by the readers, the gate's clean check and its fingerprint alike, and any
  path laid out as a private half in any letter case is refused by the readers; a link above the checkout top, such as macOS `/tmp` or an
  alias of the whole checkout, is the same checkout; a checkout set's stored names are exactly
  `tests/eval/<set>`, so on a case-sensitive volume `tests/Eval/<set>` is another folder and no
  checkout set. Accepted limit: the location is checked once per call and the folders on the way
  are opened by name after it, so a process running as the operator that swaps a folder for a link
  in between is not caught; such a process can edit the set's files directly anyway); a worktree or
  clone without it (Codex, `.claude/worktrees`, `../ecf-m1`) has no private set and
  says so ("public set only").
- **The merged set (W1, OD-647):** `refresh_merged(set)` writes `eval/private/.merged/<set>/` (gitignored
  by the private repository), at service start when a lost run's set is stale (X9), and once at the end
  of each CLI command that wrote a set (in a `finally`, so
  Ctrl-C still refreshes; `ecf eval label --review` refreshes after each changed card) and in `ecf
  eval build` (which builds both sets, then refreshes), under an exclusive lock on
  `eval/private/.merged/<set>.lock` held only while `refresh_merged` runs (never during the build
  itself, so a long build doesn't hold it), with `merge.json` written last; `refresh_merged` is the one
  writer of the merged folder and the only one that creates the lock file. Locks are `fcntl.flock` (per
  open
  file, so threads with their own descriptors exclude each other). A run takes a shared lock without
  blocking before the resolver's freshness check and `load`, opening the lock file read-only: `ecf
  eval run` in `evalrun.start()`, before the resolver and `load` (`evalrun.py:708`, on the request
  thread), inside one `try` that runs from taking the lock to a successful spawn (`:739`), whose
  `except BaseException` releases it, undoes the run state only if this call set it (a local flag set
  inside `with RUN.lock` where `RUN.state` is assigned, `:720-723`, so the "already running" refusal
  never touches another run's state; the undo restores the previous values, keeping the last run's
  result), and re-raises:
  every refusal
  or failure in between (the resolver, `load`'s own errors, no cases and Jev unconfirmed at
  `:709-716`, already running at `:718-719`, a failed thread start after `RUN.state` became
  `running` at `:720`) leaves no lock and no phantom run, so the next `ecf eval run` starts. Once
  spawned, `work()` releases it in a `finally` around its body (`:726-737`). A Claude run
  (`/ecf-eval`) does the same in `start()`, its `try` running from the lock through the spawn
  (`claude_eval.py:658`): a failure after `_SLOT.run = run` (`:642`), in `remember_root` (`:644`) or
  the spawn, ends the run as failed with `with run.lock: if run.open: _finish(...)` (a stop may have
  finished it already, `:1812-1816`) and clears the slot only `if _SLOT.run is run`, checked and cleared
  under `_SLOT.lock` (a newer run
  may hold it, and `start()` registers under that lock, `:609-642`), taken after `run.lock` is
  released: `start()` takes `_SLOT.lock` then `old.lock` (`:610-615`), and `_SLOT.lock` is not
  reentrant (`:334`). After the spawn the lock
  is kept on its `Run` and released in a `finally` around `_finish` (which does result I/O,
  `claude_eval.py:1008-1030`) and in the prepare-failed branch (`:654`). The lock is held as a file
  object; a release closes it and sets the field to `None` under `run.lock` (an `RLock`,
  `claude_eval.py:298`), so a second release (a stop while preparing, `:319-320` and `:1807-1816`,
  then `:654`) does nothing.
  No private set means no lock; with a private set and no lock file, a run refuses
  with "run `ecf eval build`". If it can't, it refuses with "a refresh is
  running; try again". A refresh takes the exclusive lock without blocking and, while a run holds the
  shared one, refuses with "a run is reading this set (see `ecf eval status`); run `ecf eval build`
  after it ends, or `ecf eval stop` it" (nothing refreshes by itself, except at service start before the
  socket is bound when a lost run's set
  is stale, X9;
  meanwhile the resolver refuses
  readers of the stale merged set, which fails closed). A CLI writer whose `finally` refresh is refused
  after its own write succeeded prints the refusal as a note after its result and keeps its own exit
  code; if the writer failed, its own error is reported and the refusal is only a note, never masking
  it. `ecf eval status` without `--root` (the run list) never resolves a set, so the hint works while
  the merged set is stale. An abandoned Claude run keeps the lock until it
  expires (up to 24 h, `claude_eval.py:119`; expiry is checked lazily, `claude_eval.py:1179-1183`,
  when status or a session asks, and the refusal's `ecf eval status` hint triggers it) or is stopped. A resumed run takes the lock again and
  checks the merged version still equals its `set_version`; a service restart drops the locks
  (accepted). Lost draft runs are recovered inside `_open_state`
  (`service.py:600`), before the socket is bound (`service.py:707`), so no request can take the slot
  first. X9
  (drafts 17-19, OD-677 to OD-679) fixes a stale set at its source, inside `claude_eval.recover`
  (whose caller already catches every exception, `service.py:599-603`, so the service binds whatever
  happens), one set at a time. For each distinct set that a record recovery would visit names (open,
  expired, or lost and not yet resumed, `claude_eval.py:2018-2020`) and whose private set exists:
  1. Wait for the set's shared lock and resolve it; a missing lock file (only `refresh_merged`
     creates it) counts as stale. Fresh: go to step 3.
  2. Stale: release it and call `refresh_merged(set, wait=<the set's remaining wait>)`, which opens
     or creates the lock file, waits for the exclusive lock itself and refreshes only if the set is
     still stale (another install may have refreshed meanwhile); recovery holds no lock during the
     call, since a second exclusive take in the same process fails (errno 35, round 26). Whatever it
     returns (refreshed, already fresh, or its wait ran out), wait for the shared lock again and
     resolve.
  3. Fresh, with the shared lock held: its records recover exactly as today: `_restored` and
     `_finish` save each open record's result, the limits apply in `_resume_lost` (`:2073-2078`),
     today's handlers (`:2022-2033`, `:2052-2056`, `:2079-2088`) close what can't be read, and the
     resumed run takes its own shared lock in `start()` before recovery releases its own. A set
     refreshed in step 2 has a new merged version, so a resume of a run started on the old one is
     refused (`:736-738`) and its attempt ends with its results saved, as today; a raise in this step
     closes only that record, caught by today's handler (`:2024-2033`).
  4. Otherwise (a wait reaches the limit, the refresh fails, or steps 1-2 raise; the set's own
     `except Exception` covers steps 1-2 and the release only), its records not yet visited by step 3
     skip `_restored`: each is closed as today's handler closes one (`_update_record` with
     `end=r.end or "lost"`, `ended_at`, `complete=False` and `attempt_over=True`, wrapped as at
     `:2026-2032`) with the end detail "the eval set <root> couldn't be read at service start
     (<reason or exception type>); this attempt is over" (the cause alone: an ended record keeps no
     advice, review round 30, delta checks 6-7).
     This is the one change to today's handlers; an open run's scored cases are lost in this case
     (stated limit).

  A wait is a 20 ms non-blocking poll, never a blocking `flock` in a thread (a timed-out thread
  can't be cancelled and took the lock later in 23-38 of 144 runs, round 26). Each set has 5 s of
  waiting in all, counted across steps 1-2's waits only (not its own resolves and refresh), and it is
  not shared out between sets, since a shorter share closed sets with no hold of their own (round
  26). The start bound is 5 s of waiting per stale set plus each set's resolves and refresh: today's
  three sets' worst start was 8.8-9.3 s with refreshes of 1 s or less and 16.4 s at 3 s (round 26,
  real `flock`; the fastest about 1.3 s),
  inside the 30 s and 60 s start limits (`cli_init.py:37`, `src/ecf/upgrade_run.py:54`). Six stale
  sets would reach about 30 s, where `ecf init` stops waiting and exits 3 (`cli_init.py:216-222`)
  though the service still binds (stated limit). Every holder's critical section is a resolve or a
  refresh, under about
  1 s (a full `synthetic` refresh 0.5-1.0 s, round 17), so two installs on one stale set both
  recover: one refreshes, the other waits and finds the set fresh (round 26: exactly one refresh
  per set in every run; a CLI build that refreshed first left nothing to do). The exception is another
  install's
  running eval, which holds the shared lock for its whole run, so a stale set can't be refreshed
  until it ends and that set's records close after 5 s (stated limit, as before). Step 3's tests
  measure the close rate and the start time with two and three installs, a slow refresh and a long
  shared hold.
  Fresh sets are not refreshed. The refresh records no private commit and no dirty flag (draft 20:
  nothing read them; a run records the private commit from the gate's live check, `clean_and_pushed`,
  which alone decides cleanliness), and runs no `git` subprocess, so nothing can hang before the
  bind. `merge.json` carries a format number; a reader that doesn't know it treats the
  set as stale. A refresh is crash-safe: each write makes a temporary file of its own (a fresh
  name, created only where nothing is; Step 3 review round 23), so it never removes or renames
  another's, and a failed write removes its own; a crashed refresh's leftover is swept by the next
  refresh (anything else in the view is), a merged copy is made read-only only after its rename, and folders are made writable
  before writing, so a refresh killed part way doesn't block the next one. All locks are released in a
  `finally`. Timing: a full refresh of `synthetic` 0.5-1.0 s, `drafts` about 0.17 s (warm, round 17),
  well within the start-up limits (`src/ecf/upgrade_run.py:54`, `cli_init.py:37`).
  Nothing else blocks on this lock: every other taker tries it once without blocking; only the
  start-up recovery waits, up to its per-set limit (X13). One function,
  `merge_sources()`
  in `ecf.eval`, computes the merged content by kind (`merge_sources(kinds=…)`): `refresh_merged` asks
  for all four files, the gate and the resolver only for the two label files (a test: its output equals
  the merged files' bytes). In `labels.jsonl`, `fixed_labels.jsonl` and `checks.yaml`, which hold
  current state, it refuses any row or key whose ID isn't a card in that same source; in the
  append-only `reviews.jsonl` it refuses only rows naming a card of the other source (so a card removed
  later doesn't make its own history refuse); CI also refuses any public row or key matching the moved
  IDs (`d-inj-*`, `h3-inj-*`, the move list and `long-093`; IDs added later are guarded by X1's
  predicate in the builder, the hook and the card-scan workflow, X5); and duplicate keys in
  `checks.yaml` (it loads through
  `ecf.yamlio`, whose loader refuses duplicate keys),
  so a private card's expected replies can't sit in a public file unnoticed; CI also runs this
  ownership check on the public sets alone, through the same function as the merge (the card scan
  parses each set file as the merge does and scans every value with trigger 10's text check;
  OD-704). Nothing in a set is followed (OD-705): the build, the freshness check and every reader
  open set files without following a link and refuse a symbolic link or special file (the private
  set folder, `cases/`, `eml/`, `.build/`, a card, an email, a set file), and the merged
  `checks.yaml` must parse equal to the two halves' keys together, so an indented private key
  can't nest under the last public one. `cases/` and `eml/` are plain copies (small; a full copy
  measured 105 ms), made read-only; `.build/` files are links to the sources (large): an APFS clone
  where available (`clonefile` from `libSystem` through `ctypes`, since Python has none; cloned to a
  temporary name, then renamed, since `clonefile` refuses an existing destination; a non-zero return
  falls back), else a hard link, else a copy. The manifest records each merged file's own inode (a
  clone gets a new one); it is a cache that only spares a refresh some copying: freshness never
  reads it, and a refresh keeps a copy only after hashing it (Step 3 rounds 9-11). A hard-linked `.build/` file (the fallback) is writable by design: an in-place write through the merged folder changes the private
  source too, so the view stays a faithful copy of its sources, and the changed email is refused at
  load by the sha256 in its label row (fails closed; Step 3 round 11); a writer's temporary-file-and-rename
  breaks the link instead. `labels.jsonl` and `fixed_labels.jsonl` merged by ID; `checks.yaml` merged by
  key; `reviews.jsonl` concatenated. The copy is incremental (only changed files rewritten, orphans
  removed last).
- **`merge.json` is a cache, not a trust root:** it records both sources' set versions, a manifest
  (path, size, modification time, inode, ctime, sha256) of every source file and every merged file, the
  merged version and each source's ID list. The resolver and the
  gate recompute the merge of the two sources' label and fixed-label files in memory (the merged
  `checks.yaml` and `reviews.jsonl` have no `src` reader, so only `refresh_merged` and the tests build
  them)
  (a few hundred lines) and require the merged files and the merged version to equal it, so a
  consistent hand edit of the merged files and `merge.json` is still refused. Nothing recorded in
  `merge.json` decides freshness (operator decisions 2026-11-06, Step 3 rounds 9-11): every merged
  copy (card, email, `.build/` file) is compared by content, hashed now, with its source, hashed
  now, the copies must be exactly the sources' files, and `merge.json` is read only for its format
  and merged version; so an edit of a merged copy, with any `merge.json` entries rewritten to
  match, is refused and replaced. The view holds its copied folders' plain files, the four set
  files and `merge.json`, nothing else: any other entry (a dot-file, a subfolder, a planted
  `manifest.json`) makes it stale and the refresh removes it, whatever its mode or depth, before
  it places any copy (or refuses naming it). Anything of a set that can't be read or written
  (either half, the view, its files, the lock) follows one rule (round 16): every entry point
  refuses it by name and the freshness check reads it as stale, never a raw error; the gate never
  raises (its check fails and the service tick carries on, its cache key included); the refresh
  repairs the view's own folders, files and lock; a model test (`tests/test_eval_unreadable_model.py`,
  rounds 16-17) takes both layouts (a split set; a public-only set), makes each folder and file of
  the layout (from `tests` and `eval` down to a single card and the lock) unreadable in turn at
  modes 000, 0o444 and 0o300, runs the service tick and the gate first (never raising), then every
  reader, writer and the builder (each works or refuses by name); whether
  judges' labels count (`models_ok`) is read from the set's own public half (Step 3 round 12). A
  set is generated (never gate evidence, P1; shown blind, P7) when either half holds a generated
  card, asked from either half's path, and a private half's build refuses a generated card
  outright (rounds 13-14). Names are compared as the volume compares them (letter case and Unicode
  form): a name in both halves is refused, and a copy renamed in either only takes its new name; a
  name another source still has is never removed. An email that can't be read is a problem and its case is left out (round 13). Cost for `synthetic`
  with its two `.build/` files of 29.5 MB (round 12, under load): a freshness check about 85-110 ms
  (hashing both sides about 66 ms), a run start or set read about 150 ms (each email hashed once
  per read), `ruletest.load_cases` about 120 ms, a gate miss about 200 ms, the gate's cached path
  about 11 ms with no hashing; a refresh about 100 ms when fresh, about 170 ms after a one-card
  edit, about 310 ms rebuilding from nothing, each file hashed at most once per refresh. A refresh still proves each existing copy by size and sha256 before
  skipping it. A file other than a card or set file present in both halves, letter case ignored,
  is refused by name; a `merge.json` that isn't a well-formed manifest reads as stale; the lock file is
  opened without following a link or blocking on a pipe. Judge-drafts and calibration read the
  cards' views from the same version their read checked, or refuse. Accepted limit (the same actor
  as the swap above, a process running as the operator): a change to a card's text alone, not its
  labels, between a judge-drafts read and its views isn't seen (card text was never bound to the
  set version).
- **The resolver (W2, OD-648), three cases:** no private set: the public set; private set present and
  the merged set fresh: the merged set; private set present and the merged set missing or stale:
  refused with "run `ecf eval build`". It refuses any path at or under `<repo top>/eval/private/`,
  comparing each ancestor's `(st_dev, st_ino)` with that folder's (APFS is case-insensitive, so a
  string comparison can be bypassed by letter case), and also any path with a merged view's own
  `merge.json` in an ancestor (the writer guard's rule: one with `merged_version`, or a link or a file
  that doesn't read as JSON); only `<repo top>/tests/eval/<name>`, or a path with no `tests/eval` parent, is read as a set.
  Inside the gate a refusal becomes a failed `synthetic` check carrying the message, never an
  exception, so the rest of the service tick (`advance_delays` included) still runs. It lives in `ecf.eval`, shared by the CLI and the service
  (import-linter forbids only `ecf` → `ecf_server`; `ecf_server` already imports `ecf.eval`); the CLI
  sends the logical path, the absolute `<repo top>/tests/eval/<set>` (the service runs under launchd
  and the API refuses relative roots, `api.py:1013-1015`), and the service resolves it (the API's
  `labels.jsonl` checks at `api.py:1013-1015` and `:1074-1076` move after resolving). Freshness is judged by size, modification time and
  inode, with a sha256 check only on a mismatch (hashing the whole set takes about 27 ms warm; stat
  about 1 ms). Every `--root` reader goes through it: `ecf eval run`, `/ecf-eval`, `rescore`,
  `compare`, `rules test`, `confirm-set`, `null-drafts`, `judge-drafts`, `routes` (`cli.py:1640`,
  which sends a `sets` list, each entry resolved),
  `status` and `show` (and `status`'s `--root` hints), the gate and the local-only tests. **Stored drafts
  roots:** `draftruns.Registration.root`, the `root` in `Registration.options` (`claude_eval.py:689`,
  read on resume at `:696`), the null run's side file (`draftjudging.py:372`) and the calibration side
  file (`draftcalibration.py:144`); their readers (`draftjudging.root_of`, `draftjudging.py:64`, used by
  judge-drafts, calibration and settle-drafts; `draftjudging.py:101`, which reads `calib["root"]`
  directly; and `claude_eval.py:2243`'s status hint) go through the resolver, so runs from before the
  move keep working. The gate's fingerprint uses a path-only mode (no hashing, no `git`); the gate
  re-checks freshness inside `inputs()` by stat only (any hashing stays in the gate's compute,
  `gate.py:124`), and `GATE_VERSION` is bumped so cached results from before the change are
  recomputed.
- **The stored root:** `EVAL_ROOT` stores the logical absolute `<repo top>/tests/eval/<set>` path.
  Today `remember_root` stores `str(root.resolve())` of the root it is given (`evalrun.py:765`), and the
  null and calibration side files do the same, so once the API resolves the path they would store the
  merged folder instead. So `root` in both `Options` classes (`evalrun.py:178`, `claude_eval.py:139`)
  stays the logical path, and the set is resolved at each load site (`evalrun.py:701` and `:708`,
  `claude_eval.py:559`, `:561`, `:596`, `:644` and `:2096`); no new field is added, so stored
  `Registration.options` (`asdict`, `claude_eval.py:688`) and `Options(**d)` on resume (`:695-698`) keep
  working. `remember_root`, the null side file and `Registration` store the logical path
  (`draftcalibration.py:144` already stores `str(root)` from `root_of`); the resolver refuses a merged
  path by path (above); a test passes a merged path as `--root` and to the gate, and a for-gate
  `/ecf-eval` run is checked to store the logical root. Stored values from before the change are already logical, so none
  needs migrating; `remember_root`,
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
- **Second guard: counts by membership.** Each run records `public_cases` and `private_cases`,
  counted over the cases the run loaded (398 without `--fraud-only`; 223 with it, the cards with
  `rule: fraud_guard` or `fraud_weak`, `must_escalate` or `injection_target`, `evalrun.py:316-321`)
  and split by owning set. `ecf.eval.membership(sources)` takes them from each source's label-file IDs,
  which the resolver and the gate already recompute from the sources (`merge.json` is only a
  cache), so no card is read at a run's start or in the gate. Every
  private card is marked by construction: the build refuses a private card that X1's front-matter
  predicate doesn't mark, so a separate count of marked private cards would always equal
  `private_cases` and draft 9's `private_injection_cases` is dropped. The run also records the
  merged version; the gate refuses either count being 0. The counts back up the version, which alone
  proves the run read the whole set.
- **References:** `fraud_guard_cases` and (unless `--fraud-only`) `confirmed` are compared with the
  same counts worked out the way `evalrun.summarize` works them out (`evalrun.py:602-629`: confirmed is
  the confirmed, scored cases; fraud-guard cases the decided cases expecting `fraud_guard`) over
  `load(merged, fraud_only=...)` on the current verified merged set, never with the run's own numbers.
- **Runs from before the guard** (such as `ce3be1df`): accepted only when their `set_version` equals
  the current verified merged version; such a run has no `public_cases` or `private_cases` keys, and
  the 0-count check skips it (it applies only to runs that recorded them). The references guard then
  applies as to any run. Its refusal names the run and each count that differs, in this form: "run
  ce3be1df's counts don't match the set: confirmed 396 (set 398), fraud_guard_cases 164 (set 166)".
- **Order:** the existing version comparison (`gate.py:267`) runs first, then the membership and
  pre-guard checks, so a run on an older version gets the same "older version" message as today.
- **Dirty or stale clone:** a for-gate run (a `/ecf-eval` run with `for_gate` true,
  `claude_eval.py:154`; no `ecf eval run` backend is gate-eligible, `GATE_BACKENDS` is empty,
  `evalrun.py:144`) checks, live at its
  start and again whenever the gate's compute reruns, that neither `eval/private` nor `tests/eval/<set>` has uncommitted changes and that
  the private HEAD is on a branch whose upstream (`@{u}`) is a remote-tracking ref (its full name
  starts with `refs/remotes/`, read with one `git rev-parse --symbolic-full-name @{u}`, about 12 ms
  through the `/usr/bin/git` shim)
  and contains it (`git merge-base --is-ancestor <HEAD> @{u}`, no network; the private HEAD is read
  once from its ref files, so the commit compared, checked and recorded is one commit, OD-705; remote URLs count as trusted
  repository config); one function, `ecf.eval.clean_and_pushed(top, set)`, does this check for the
  service and for M1's upload; a detached HEAD, no upstream, or an upstream
  that is a local branch is not clean; `extensions.refStorage` is read from the config file and a
  reftable repository is refused, since its commits move no ref file; the run records the private commit and the merged version in its own metrics, and the
  gate's detail shows them. `git` runs in the service (the gate computes there, `gate.py:124`) as
  `/usr/bin/git -C <repository top> --no-optional-locks -c core.fsmonitor=false -c
  core.hooksPath=/dev/null -c core.attributesFile=/dev/null` with `GIT_OPTIONAL_LOCKS=0`,
  `GIT_CONFIG_NOSYSTEM=1` and `GIT_CONFIG_GLOBAL=/dev/null`. Clean means the set's content is
  HEAD's (operator decision 2026-11-06, OD-704; draft 28 asked `git status`, which an
  assume-unchanged or skip-worktree flag in the index blinds, Step 3 review round 4): one
  `ls-tree -r -z <HEAD> -- <set path>` per repository (HEAD read from its ref files; `-C <top>` and
  `tests/eval/<set>` for the public repository, `-C <top>/eval/private` and `<set>` for the private
  one), and a walk of the set folder (no `git`, no ignore rules, nothing followed: a symbolic link,
  a submodule or anything else that isn't a plain file, in HEAD's tree or on disk, is dirty, OD-705)
  that hashes each file as git does (sha1 or sha256 by the repository's format); a file missing,
  added (untracked and ignored alike) or with another blob id is dirty, and a file that can't be read
  counts as changed. Left out of the walk: `.build/` (built, ignored by design), `*.tmp` and
  `.DS_Store`. So neither the index, `status` options (`status.showUntrackedFiles=no` made the old
  command fail) nor an uncommitted ignore rule can make an edit look committed. About 38 ms for
  `synthetic`'s 797 files (Step 3, round 4); it runs only when the fingerprint changes. A timeout of
  a few seconds and an argv test (the `/usr/bin/git` shim adds about 6 ms a call over the Command
  Line Tools binary, 10.9 against 4.9 ms in round 9, and survives Xcode switches); the repository's own config is trusted as
  the operator's. Any `git` error, timeout or missing binary counts as not clean, with its message, and
  is not cached across passes: the gate leaves its seen fingerprint unset so the next tick retries,
  but within one pass (one tick, one request) the failure is memoized and the check's remaining `git`
  calls are skipped, so a hanging `git` costs one timeout per pass, not one per address; after a
  timeout (not a quick error) the check backs off for 5 ticks, process-wide (the tick, `stages.status`
  and `address_gate` share it; `stage set live` and step-up still run fresh), still reporting not
  clean. So that a commit,
  a push or a new file is noticed without a subprocess per tick, the fingerprint stats, in both
  repositories: the HEAD file, the config file (and a worktree's `config.worktree`), the loose branch
  ref and `packed-refs` (both), every file under
  `refs/remotes/` (a push renames `refs/remotes/<remote>/<branch>` and leaves `refs/remotes` itself
  unchanged, measured 2026-11-05) and the index; and a stat walk of every file the clean check
  hashes, in both source sets (the same walk, OD-704: every file's name, size, `mtime_ns`, inode and
  `ctime_ns` (OD-705),
  so an edit to `checks.yaml`, `reviews.jsonl` or any other set file counts too; about 6 ms for
  `synthetic`'s 797 files, Step 3 round 4; `.build/` is left out, its sha256 is checked on load),
  which catches an in-place edit that changes no folder or index time; a missing file is a value, not
  an error; the walk runs once per pass. A stat can't vouch for a file written within the last 2 s (a
  same-size rewrite in one timestamp tick keeps it): a cached gate state keeps those files' sha256
  and counts only while they hold (OD-704); "within the last 2 s" reads the later of mtime and ctime,
  and a cached gate state drops a file once a hit finds it unchanged past the window: a later write
  changes its mtime and ctime, so the key (OD-705). The merge manifest's stats only spare a refresh
  some hashing of the sources; freshness never reads them (round 11). In a worktree, where `.git` is a file, the paths come from its `gitdir:` line
  and `commondir`, read without `git`. Accepted limit: a plain `git status` (an editor, a shell prompt)
  rewrites the public index, costing one recompute per tick for addresses that haven't passed. The
  set-level part of the check (merge, load, `git`) is cached by that whole fingerprint plus `merge.json`
  and the label-file stats, and shared by the tick (`stages.tick`, `stages.py:270-287`), `stages.status`
  and `address_gate` (`api.py:743`), so it runs once, not once per address; "private HEAD" means reading
  the HEAD and ref files, never `git rev-parse`. Two misses at once may both run `git` (accepted;
  the tick never waits on a lock). `stage set live` and the step-up path bypass the cache and run the
  live check fresh (about 125 ms with `evalrun.load`, round 9; re-measured at Step 3). A
  clone behind its origin is an accepted limit (SPEC §16); the code merge gate checks the commit is
  pushed.
- The gate's fingerprint stats `merge.json` and both sources' label files (hashing only inside the
  gate's compute).
- M1 inherits the version guard for the uploaded private part.

### Builder refusal

- A card in a public set that X1's predicate marks is refused at build (and in a private set, a card
  it doesn't mark),
  except the exact ID
  `starter-injection` whose bytes hash to the pinned sha256 of its current file
  (`afebb16654e9867261b6dc457941ec5a8e2c2bb0093d63d5a8f35f7201d19a28`, 2026-11-05; OD-654). A
  generated card (`author: generated`; P1: never gate evidence, refused by a private half) is exempt
  from the build refusal only, since `ecf eval generate` writes `hidden_text`; X5 and the sdist test
  still refuse it (Step 5 review, OD-705). The card
  differs from the shipped `injection` template (`case_templates.py:39-57`): it has `author: claude`,
  `rule: fraud_guard` and `must_escalate: true`, added in 8a4f0f2 (OD-252) while the template wasn't, so
  a pin to the template would refuse today's card. One constant (ID and sha256) shared by the builder,
  the sdist test and X5's script, run by a committed `.githooks/pre-push`
  hook (it reads each pushed range from its stdin, not the working tree, and runs X5's `--revs`
  entry point on it: `<remote>..<local>`, or for a new branch, whose remote sha is all zeros,
  `<local> --not <the pushed-to remote's refs>`; for every commit in the
  range it
  lists the files that commit changed (`git diff-tree -r -m --root --no-renames --diff-filter=d`, so
  each blob comes with
  its own path, and a card added in one commit and removed in the next, or restored with bytes seen
  before, is still checked), reads them through one `git cat-file --batch` (about 11 ms a call through
  the shim otherwise), dedupes by blob ID and path, and runs the hygiene scan and X5's script on every
  card and `.eml` blob under `tests/eval/`; then it lists each pushed ref's tip tree (`git ls-tree
  -r`, no build) to find an `eml/` or `.build/` file with no card (X5), and it refuses a pushed ref
  whose object doesn't peel to a commit (a tag on a tree or a blob, which the range walk would
  otherwise skip, round 25); only the remote being
  pushed to is excluded from the range, and with no remote-tracking ref for it the range is
  `<local> --not origin/main` (in the private repository, whose first push has no `origin/main`, all
  of `<local>`: every card there is private by design); CONTRIBUTING and
  AGENTS.md tell every checkout, Codex's included, to run `git config
  core.hooksPath .githooks`, and the private clone to set `core.hooksPath` to the public `.githooks` by
  absolute path; the hook finds the public repository top from its own location, not the current
  directory, and in `<top>/eval/private` runs the hygiene scan (every card there is marked by
  design) and allows a push only to the private repository recorded at Step 4 (`git config
  ecf.privateRemote <host>/<owner>/<repo>` in the private clone), compared as host, owner and
  repository name, so the SSH and HTTPS forms of one URL match and a fork or a new remote is refused),
  by the
    macOS merge gate, and by the card-scan workflow. A push from
  a checkout without the hook (or with `--no-verify`) is caught only by the card-scan workflow, after
  it is published (stated limit: by then it is public); the workflow checks each pushed commit, so a
  card added and removed within one push is still reported. The hook builds with the working tree's
  code and `keywords.yaml`; a historical card that no longer builds fails closed (X5's stated limit;
  the operator pushes
  from an up-to-date checkout); a test
  checks the card equals the template's output with `author` changed to `claude` (the template has
  `author: hand`, `case_templates.py:6`) and `rule: fraud_guard` and `must_escalate: true` added. The
  pin depends on LF line endings, so a new public `.gitattributes` marks `tests/eval/**/cases/*.md
  -text`.
- `ecf eval new-case --template injection` writes to the private set, or refuses without one. A
  hand-written private card (such as `long-093`) is written under `eval/private/<set>/cases/`, then
  `ecf eval build`, `ecf eval label <id>` (it finds the owning set) and a commit and push of the
  private repository; the merged version changes, so the gate needs a fresh C run (and, on M1, a
  re-upload).
- An `eml/` or `.build/` file with no card in its set fails the build. The pre-push and CI scans
  and the sdist test refuse one too (no card for it in the same tree), and refuse a card that doesn't
  parse (fail closed).

### Private repository

- First commit: `.gitattributes` with `* -text` (`.eml` files are CRLF and their sha256 depends on it),
  `.gitignore` with `.build/`, `*.tmp`, `.DS_Store`, `.merged/`, `heldout3/eml/` (heldout3's `.eml` files stay
  untracked, as in the public repository), and a README saying it holds the private parts of
  ecf's eval sets.
- The hygiene scan covers both sets; the pre-push hook runs it in both repositories. The atomic
  helper's temporary file is `.<name>.<random>.tmp` (a fresh name per write, never another writer's;
  a crashed write's leftover is ignored, by git and every reader). Every build of a half that writes
  (`ecf eval build`, and the commands that rebuild: generate, agree, settle, expect, card review and
  card-field label imports) then removes, best effort, the leftovers older than an hour from the
  half's folder and its `cases/`, `eml/`, `.build/`, `work/`, `work/call/` and `grades/` folders (a
  model call's schema file and a grades file are written there): it follows no link, never fails
  the build, and names in a `note:` line each one it couldn't remove and each folder it couldn't
  open or list (a link, not a folder, unreadable; a missing one is silent). The public `.gitignore` gains
  `tests/eval/**/*.tmp` and `tests/eval/**/.DS_Store` after its line 78 (`!tests/eval/*/cases/**`,
  which would otherwise re-include them), so neither a crashed write nor Finder makes a clone dirty.

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
- The merge gate requires the private repository clean, pushed and private (`gh repo view --json
  visibility`); the merge commit names both commits.
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
2. **Phase R:** done on draft 1 (Fable), drafts 2 to 27 (Opus delta). Draft 28 gets an Opus check
   of its changes (OD-577).
3. **Code** on a branch (Steps 3, 5 and 6 share it; the private repository's commit is pushed before
   Step 7): the atomic-write helper in every writer, `writable_root()`, `refresh_merged` with its lock,
   the resolver in `ecf.eval`, `merge.json` and the in-memory recomputation, the stored-root and draft
   registry changes, one-read loading and per-case re-check, missing-file reporting, the gate guard,
   path-only fingerprint with the `git` state stats, the hardened live dirty check, the builder refusal
   with the byte pin and orphan
   check, X5's script and `card_injection` in the hook, CI, sdist and merge-gate scans, the
   private-card marker refusal, the card-scan workflow and its `--ref`/`--revs` entry point, X9's start-up
   refresh, ID ordering,
      `new-case` routing, the sdist test, the test rewrites, a script
   listing the exact cards to move, `merge_sources()`, the drafts roots, `routes` and the whole-set
   modes, the per-request gate cache. Tests build public and private sets in temporary folders, so CI
   never needs the private repository. *Pass:* a property test splits a built set at several cut points
   (first row, last row, interleaved, neither source with `fixed_labels.jsonl`, only one with it, both
   with it (synthetic's real shape after the move: 29 of the 49 moving cards have `fixed_labels` rows,
   which the move script moves with them), an empty public or private set) and the merged bytes equal
   the unsplit files, with no merged `fixed_labels.jsonl` when no source has one; a row or
   `checks.yaml` key naming another source's card, or a duplicate key, is refused; a corrupted `.eml`
   is repaired by a rebuild; a source writer replacing a linked `.build/` file while the merged copy is
   held open leaves the open copy's bytes unchanged; with the hard-link path forced, an in-place write
   through the merged folder makes the loader report the sha256 mismatch and a for-gate run refuse;
   `git` failing or timing out makes a for-gate run refuse and is retried on the next tick; a commit, a
   push to a local bare remote, an untracked new card and an in-place edit of a tracked card each
   change the gate's fingerprint, in a
   normal clone and in a worktree, and a change to the upstream in the config file does too; a branch
   whose upstream contains HEAD passes, and a detached HEAD, a branch with no upstream, an upstream that
   is a local branch, and a reftable repository each fail; a hanging `git` costs one timeout per pass
   and then backs off; `stage set live` runs the check fresh; a resumed run re-checks the merged
   version; a crashed write's `.<name>.<random>.tmp` leaves the clone clean; an ignored card under `cases/`
   counts as dirty; a mixed-case path into `eval/private` is refused; a stale merged set during a tick
   gives a failed check and `advance_delays` still runs; a Claude run releases its lock at every exit; a merged path given as `--root` or stored as the root is refused;
   a refresh during a run refuses pointing to `ecf eval status`, and after the run ends `ecf eval build` leaves the
   merged set fresh; a run that can't take the shared lock refuses; a merged folder with no `merge.json`
   given as `--root` is refused by path; `--import-fixed` with one bad ID, or one bad answer, writes
   neither part; `evalrun.summarize` takes the membership lists so `null` runs record
   `public_cases` and `private_cases`; `git
   status` changes outside the set's path don't make it dirty; a status call over several addresses, and a tick,
   run the set-level check once (time recorded, not gated); the move script's real list applied to
   copies of all three real sets in a temporary folder gives merged label bytes equal to today's,
   `checks.yaml` parsing equal, and versions equal; a consistent hand edit of the merged files and
   `merge.json` is refused; a made-up run from before the guard is accepted only when its version
   equals the verified merged version (refused when it differs, when the private set is absent, and
   when the merged set is stale); a rebuild during a held-open merged file leaves its bytes unchanged;
   a public-only for-gate run is refused; X5's tests (above) pass, including a card added and removed
   within one push (refused by the hook, reported by CI), a pushed `.eml` with no card change, and a
   build error or missing `ecf_server` (refused); a private card without a front-matter marker is
   refused at build; `ecf.eval.membership` reads no card file; a stale merged set, then a refused `ecf
   eval run`, then `ecf eval
   build` succeeds; a run whose thread start fails leaves no lock and no running run, and a second
   `ecf eval run` starts; the same for a Claude run whose `remember_root` or spawn fails; a Claude run
   stopped while preparing releases its lock once, and one stopped between registering and its
   spawn is finished once, with a newer run's slot left alone; a lost run whose set went stale
   (a `git pull` without a build) has its results saved after the start-up refresh, its resume
   refused (new merged version) and its attempt over; a fresh set isn't refreshed; two and three
   services starting on one stale set at offsets from 0 to 1 s, with 0.03-0.5 s resolves and a 1-2 s
   refresh (a real refresh takes 1 s or less), all end with the set fresh and recover, and exactly one
   refreshes; at a 3 s refresh with three installs a set may close, as round 27 measured (3 of 6
   runs), and the test records that rate rather than requiring 0; a long `ecf eval
   build` holds no lock until its refresh; with another install's run holding the shared lock, the
   record closes after the 5 s limit with X13's exact end detail and the other sets recover; an open
   run past 48 hours still has its result saved; a
   record
   whose root is gone closes as today; a refresh that raises, or a set whose rows don't parse, leaves
   the other sets recovered and the service bound; a refresh killed part way is followed by one that
   succeeds; the script refuses a listed blob on `refs/heads/main` and on `refs/remotes/origin/main`
   and passes it on a tag; `git push origin foo:main` is scanned as main; a commit adding a card and
   its ID is refused by the script's own check; `--tree HEAD --no-skip` refuses a listed blob; a
   new public card X1 marks but trigger 10 doesn't flag is refused by the hook and the workflow; a
   marked blob already in
   main's history at the branch base passes X1 (a branch merging main, an edit main made before the
   move); a set whose steps 1-2 raise closes its unvisited records with X13's detail and the others
   recover, and a raise in step 3 closes only that record; a record already ended lost gets `end`
   "lost" kept; a commit followed by `git pack-refs
   --all` changes the gate's fingerprint; X5's "Which copy runs" tests pass; the card-scan workflow's
   triggers have no path filter and cover tags, `pull_request` and `schedule`; on a local bare
   remote, the `--revs` entry point catches a card commit, a card added and removed, a card added by
   a merge commit, a new branch and a pushed `.eml`, passes a card deletion and a listed known blob,
   refuses a new flagged card pushed straight to main (the CI side, with `origin/main` at the pushed
   commit) and a listed blob in the merge-gate tree scan; the private repository's hook
   allows its recorded remote by its SSH and its HTTPS URL and refuses the public one and a fork; the
   pre-push hook checks a new branch's commits, and an
   orphan `eml/` file and an unparsable card blob are refused by it; a script calls `gate.synthetic`
   (`gate.py:246`) on a database copy with the digest from `claude_pins.address_key`, as Step 5 does; a
   refresh between `ecf eval run`'s `load` and its
   thread start is
   excluded by the lock; a
   `cases/.DS_Store` leaves the clone clean; a refused refresh in a writer's `finally` doesn't mask the
   writer's error; full suite green. The known-blob list and `card-scan.yml` land in Step 3 (the list
   keeps the branch's own injection cards from being refused). Three guards would refuse today's
   public injection cards, so the move commit adds them, with no flag or setting that could leave one
   off: the builder's refusal of
   a marked public card (its call in `build_all`), the sdist test's marker assertion, and the merge
   gate's tree scan (its line in `testing-and-merge.md`). Until then Step 3's tests run them on fixture
   sets built in the test; Step 5's after-checks show each is on.
4. **Private repository:** the operator creates it (private, GitHub); cloned into the branch
   worktree's `eval/private/` for Steps 5-7, and into the main checkout's at Step 7, before its
   gate; first commit as above; in each clone `ecf.privateRemote` recorded and `core.hooksPath` set
   to that checkout's `.githooks` by absolute path (a nested clone doesn't inherit the public
   config), checked to resolve to an executable `pre-push` once the hook is in that checkout's
   tree. *Pass:* `git check-ignore
   eval/private` true; `.gitattributes` present; `gh repo view --json visibility` says `PRIVATE`
   (the merge gate checks it again).
5. **Move** (X6; rewritten in draft 12, OD-636; commands pinned in drafts 13 and 14, OD-637, OD-638).
   Check with the M1 session before starting, and ask that no session runs an eval on `accept`
   until Step 5 ends. The install read is `accept`, where `ce3be1df` lives (SPEC §16.2; it runs from
   main's checkout). The
   expected case has no R: `ce3be1df`, the last gate run, is on `a6642d21bfd5eaaf`, not today's
   `9370cd54d96f28bc`, so it no longer passes the gate. If the operator wants a fresh C `/ecf-eval`
   before the move, this step is re-planned for it and reviewed first.
   *Guarantees, each with its check:*
   - **G1, the install is only read.** Nothing stops, upgrades or installs anything; `accept`'s database
     is
     read once, opened read-only (`?mode=ro`), through SQLite's backup API, into `<scratch>/db/ecf.db`
     (`PRAGMA integrity_check`
     returns `ok` on the result, and no `-wal` or `-shm` file is copied: the backup is complete by
     itself), with its draft-run registry and null and calibration side files copied to
     `<scratch>/db/evals/`, where
     `draftruns`, `draftjudging.root_of` and `draftcalibration` read them (`draftruns.py:113-118`,
     `draftjudging.py:39-69`); no
     service ever opens that copy. `ECF_HOME` and `ECF_SOCKET` are never set in the shell; every `ecf`
     command
     names the dev service's socket, `ECF_SOCKET=<scratch>/before/dev/run/ecf.sock` (or `after`), and
     every `ecf eval run` passes `--copy-to <scratch>/results` (without it the result is also written to
     the checkout's `state-archive/evals/`, `cli.py:1469-1478`). Without `ECF_SOCKET` the client would
     talk to install `default`'s service (`cli.py:62-63`, `paths.py:52-54`) and the `null` run would
     write into it.
     `<scratch>` is a short folder, `mktemp -d /tmp/ecfmv.XXXX`: the dev socket path must stay under
     104 bytes (`paths.py:16`, `:85-90`), and under the session scratchpad it would be 137; its length
     is checked first. *Check:* `uv tool list` and the tool environment's `uv-receipt.toml` are
     unchanged at the end, and the `eval_runs` run IDs of `default`, `accept` and `ecf1009` (each opened
     read-only) are the
     same before and after.
   - **G2, nothing leaves this machine.** The `null` runs use `ecf-server dev` on fresh homes
     (`--home <scratch>/before` and `<scratch>/after`, `__main__.py:85-86`): memory secrets, no
     addresses, no `gh` release poll (only `local` starts it, `__main__.py:53`), and the `null` backend
     calls no model (dev services also show no notifications and start no Slack, `service.py:452`,
     `:725`). The database copy is read only by scripts. Every `uv run` is `uv run --project <worktree>
     --no-sync --offline`, after Step 3's `uv sync` in the
     worktree (without a synced `.venv` it fails rather than install).
   - **G3, P and the after-run read the same cards, apart from the move.**
     - The installed release reads the cards as the branch base has them: `git diff <installed release
       tag>..<branch base> -- src/` touches nothing the `null` run or `set_version` reads (on
       2026-11-05, `v3.2.0..19ed715` changes only `src/ecf/eval/draftjudge.py`'s
       `STATED_FAULT_CARDS`; a difference from the branch's own code shows in the after-check).
     - The branch base is main's tip, and `git diff main HEAD -- tests/eval/` is empty before the
       move commit (`main...HEAD` would compare from the merge base and miss cards main gained).
     - The worktree's sets are complete: `.build/` is untracked (`.gitignore:39-40`) and
       `evalrun.load` skips a missing file without a word (`evalrun.py:305-307`), so `.build/` is
       cloned from the main checkout (`cp -c`); a script finds every `synthetic` and `drafts` row's
       file present with its sha256 matching; `heldout3` has no `.eml` (ignored), so its 30 rows are
       checked by building each card in memory (30 of 30 match, 2026-11-05); `git status` is clean.
     - Each command names its set: `--root <worktree>/tests/eval/<set>` for `ecf eval run` and `ecf
       eval build` (the default is relative to the working folder, `cli.py:58`); the scripts take
       the path as an argument.
     - No card is added, removed or relabelled in any set from P until the after-checks pass
       (`long-093` comes after Step 7).
     *Check:* P's `null` run loads 398 cases.
   - **G4, every stored reference still resolves.** In the database copy and side files, every
     stored eval root under the main checkout (`EVAL_ROOT`; the draft registry's root and
     `options.root`, `claude_eval.py:689`, `draftruns.py:63`; the null side file's,
     `draftjudging.py:372`; the calibration side file's, `draftcalibration.py:144`; run metrics store
     none, `evalrun.py:632`) is rewritten to the same path under the worktree, where the private
     repository is cloned (the resolver reads a root by its shape, `<top>/tests/eval/<set>`).
     *Check:* a scan finds no remaining absolute path into the main checkout.
   *Sequence:*
   1. Make the worktree complete and check it (G3). Take the database backup and side files, rewrite
      the roots and scan (G1, G4).
   2. **P**, with the installed release: `ecf-server dev --home <scratch>/before` (the tool
      environment's binary, started in the background; it runs in the foreground, and `--keep` does
      nothing with `--home`, `__main__.py:176`) and `ECF_SOCKET=<scratch>/before/dev/run/ecf.sock ecf
      eval run --classifier-backend null --root <worktree>/tests/eval/synthetic --copy-to
      <scratch>/results`: case, confirmed and fraud-guard counts. With the tool
      environment's own Python, scripts read: `set_version` of each set; for `drafts`,
      `evalrun.load`'s case count and every `card_digest`, and the copied registry's `set_digest`; for
      `heldout3`, its 30 rows; and `gate.synthetic` (`gate.py:246`) on the database copy, with the gate's
      real digest, `claude_pins.address_key(conn, <one of the install's address IDs>)`
      (`claude_pins.py:160`; a wrong digest, such as the `null` runs' `"null"`, makes it return "no
      synthetic-set result" before it reads the root, `gate.py:248-253`, and the check would prove
      nothing); the digest and the message are recorded, and the message must start "the latest run
      (ce3be1df". Not
      `null-drafts`: its run ID is `sha256("null|" + version)`, so a before and after run would share
      a file. As of 2026-11-05: `synthetic` `9370cd54d96f28bc` (398 cards), `drafts`
      `ae1111e7bc117b23`, `heldout3` `07625d6cafa4b278`.
   3. **The move:** `git rm` the listed cards and their `.eml` and `.build/` files, remove their rows
      and keys from the public label, fixed-label, review and `checks.yaml` files, add them to the
      private repository; run `uv run --project <worktree> --no-sync --offline ecf eval build --root
      <worktree>/tests/eval/<set>` for each of the three sets, so all three merged sets are fresh and
      any file the build rewrites is in the commit; commit both, require `git status` clean in both,
      push the private repository.
   4. **After**, with the branch: `uv run --project <worktree> --no-sync --offline ecf-server dev --home
      <scratch>/after`
      in the background, the same `null` run with the branch's client (`uv run --project <worktree>
      --no-sync --offline ecf eval run`) and `ECF_SOCKET=<scratch>/after/dev/run/ecf.sock`, and the
      same scripts with `uv run --project <worktree> --no-sync --offline`, `gate.synthetic` with the
      digest computed again under the branch's code (if it differs from P's, stop); every after-check
      runs only now, since the live dirty-and-pushed check would otherwise refuse for a reason the move
      didn't cause.
   5. The results in `<scratch>/results` are moved to the session scratchpad (they name private IDs);
      `<scratch>` is deleted; the
      install first runs the change with the next release.
   *After-check pass:*
   - no moved ID is a path under `cases/`, `eml/` or `.build/`, nor a row or key in the public
     `labels.jsonl`, `fixed_labels.jsonl`, `reviews.jsonl` or `checks.yaml` (a parsed check); `git grep
     -lwF -f <ids>` finds them only in these names-only files (the full list on 2026-11-05, 107 IDs):
     CHANGELOG.md, SPEC.md, `docs/adr/0026*`, `docs/roadmap/m8-model-written-drafts.md`,
     `planning-docs/EVAL-PRIVATE.md`, `planning-docs/SYSTEMONE-MODEL-TESTING-PLAN.md`,
     `planning-docs/v3.0.0-PLANNING.md`, `planning-docs/v3.0.0-STEP6.md`,
     `planning-docs/v3.0.1-PLANNING.md`, `tests/data/s3b_noise_pair.json`, `tests/test_draft_rating.py`
     (an ID on a card built inside the test) and the 7 `tests/fixtures/archived_results/*.json`
     (historical results); `tests/test_claude_eval.py` and `tests/test_config.py` are cleared by
     Step 3's rewrites; and no test opens a path under a moved ID;
   - a fresh clone of the private repository: every `synthetic` and `drafts` row's sha256 matches its
     file (built rows after `ecf eval build`), and the 4 `heldout3` rows match their cards built in
     memory; `git ls-files --eol` shows CRLF for `.eml`;
   - merged versions equal P; the real-set copy check (Step 3) is one-off and local only; the merged
     drafts `set_version` equals the copied registry's confirmed `set_digest` (as `draftruns.py:219`
     checks); the merged `checks.yaml`, parsed, equal to the public one before the move; a drafts
     load with unchanged `card_digest`s;
   - no row's file missing; the after `null` run (without `--subset`) equals P's on a named list of
     keys: case count, confirmed, fraud-guard cases, unsafe and `fraud_guard_missed` (both compared as
     sorted lists), and every per-case field keyed by `id` except `classifier_ms`, `wait_ms` and
     `queue_ms` (not `created_at`, `run_id`, `wall_s`, `environment`, `power`, `measurements` or the
     membership keys, which `evalrun.summarize` writes for `null` runs too); `public_cases` 349 and
     `private_cases` 49, which add up to P's 398 and equal the move script's count;
   - every null-floor and calibration side file and the copied registry resolve, through
     `draftjudging.root_of` and the resolver, to the merged drafts set, and the stored `EVAL_ROOT`
     (written by the last for-gate run; a `null` run never writes it, `claude_eval.py:644`) resolves to
     the merged `synthetic` set;
   - `gate.synthetic` on a second throwaway copy of `<scratch>/db/ecf.db`, made after G4's rewrite and
     scan, in which `ce3be1df`'s `set_version` is set to the after merged version: the version
     comparison then passes (the gate reads `set_version` from that row, `evalrun.py:1486-1490`, and
     the root from `EVAL_ROOT`), the pre-guard rule accepts the run, the 0-count check skips it (no
     membership keys), and the references guard refuses it: it recorded 396 confirmed and 164
     fraud-guard cases, and the merged set has 398 and 166 (SPEC §16.2; `long-091` and `long-092`
     added since). The message must be exactly "run ce3be1df's counts don't match the set: confirmed
     396 (set 398), fraud_guard_cases 164 (set 166)" (the values read from SPEC, to be confirmed
     from the database copy in P; if the database differs, stop and re-plan). It shows the branch's gate
     resolved the stored root to the merged set: the
     worktree's public-only set has another version, which gives "older version" instead;
   - the three guards are on. In the branch worktree, `uv run --project <worktree> --no-sync --offline
     python scripts/check_card_injection.py --tree HEAD --no-skip` and `test_sdist_contents` pass on
     the real tree after the move. Then `git worktree add --detach <scratch>/guards <move commit>` (its
     own index and HEAD, and no `eval/private`, so no ID sits in both sets); copy `inj-spanish`'s card
     and `.eml` back under its public `cases/` and `eml/` (from the branch base: `git show
     <branch base>:tests/eval/synthetic/cases/inj-spanish.md` and its `eml/` file) (a card trigger 10
     flags: only 12 of the 49
     moving `synthetic` cards are), and commit there. Run each guard from `<scratch>/guards`, with the
     branch worktree's environment (`UV_OFFLINE=1 uv run --project <worktree> --no-sync --offline`,
     working folder `<scratch>/guards`; `UV_OFFLINE=1` reaches the `uv build` the sdist test starts,
     `test_sdist_contents.py:21`): `ecf eval build --root <scratch>/guards/tests/eval/synthetic` must
     refuse
     with "marked card in a public set"; `python scripts/check_card_injection.py --tree HEAD
     --no-skip` (reading `<scratch>/guards`'s committed tree, `ls-tree`, skipping nothing, so the
     listed blobs don't pass) must refuse naming `inj-spanish`; `pytest
     <scratch>/guards/tests/test_sdist_contents.py` must fail naming `inj-spanish` (the test takes its
     root from its own file, `test_sdist_contents.py:9`). Then `git worktree remove` it, and the
     known-blob list recomputed at the move commit equals the committed one;
   - `gate.synthetic` on the database copy, with the same digest, gives the same refusal message as
     in P, starting "the latest run (ce3be1df" (apart from the
     fields the change adds: the private commit and merged version, and `GATE_VERSION`), never
     "isn't where the last run found it" (`gate.py:264`); the gate's evidence needs a fresh C run
     after the move, with the operator's go-ahead; a public-only for-gate run is refused.
6. **Docs:** `.claude/rules/eval-synthetic.md`, `planning-docs/GENERATE-FAKE-TESTING-EMAILS.md`, SPEC
   §16 (the pins, the sdist fact, the gate guard, X1's markers), OD-654's wording ("main's installed
   code" becomes the installed release's code, with Step 5's `src/` precondition), CONTRIBUTING,
   AGENTS.md, CHANGELOG; `docs/agents/testing-and-merge.md` for the merge gate's tree scan.
7. **Merge:** a diff review on Fable; the private repository cloned into the main checkout's
   `eval/private/` (Step 4; the gate's private-present run and its clean, pushed and private check
   need it there); then, on the local merge commit, before the push:
   - the macOS merge gate (twice, as above), with `--tree HEAD --no-skip`;
   - the known-blob list recomputed over the merge's history (must equal the committed one);
   - the freeze check: everything the freeze covers (cards and `.eml` files compared as blobs by
     path; rows and keys parsed by ID) is equal at the freeze base and at `HEAD^1` (main), and X1
     and trigger 10 over `<freeze base>..HEAD^1` find no marked or flagged card; the freeze base,
     read from `state-archive/eval-private/`, is an ancestor of `HEAD^1`. Any difference stops for
     the operator (an edit on main the freeze missed).

   Then push, and watch CI. The branch worktree, which holds a private clone, is removed only with
   the operator's OK and only after that clone passes `clean_and_pushed`, `git status --porcelain`
   on the whole clone is empty, and `git log --all --not --remotes` and `git stash list` list
   nothing: `git worktree remove` deletes an ignored nested repository, unpushed commits and all,
   without a warning (round 23).

## Measured costs (performance reviewer, 2026-11-05)

`set_version` 0.7 ms today, 1.6-3.2 ms merged; `load_cards` 381 ms (round 1; 738 ms on
`synthetic`, 326 ms `drafts`, 62 ms `heldout3` in round 9); X5's script on every card: 14.2 s
`synthetic` with the PDF render (`scanned-invoice-17mb` 5.9 s, `padded-text-part` 3.6 s, the other 396
cards 2.9 s), 0.9 s `drafts`, 0.2 s `heldout3` (round 11); hygiene scan 275 ms; building the
394 small cards (then) 164 ms, `scanned-invoice-17mb` 2.8 s; `evalrun.load` 34 ms (about 63 ms in round 8, which also hashes `.build/`,
`evalrun.py:310`; Step 3 round 11, with the merged view's freshness by content: `read_set` 84-101 ms
before both sides were hashed, about 120-140 ms after, under load); sha256 of every file in the
set about 27 ms warm, stat about 1 ms (round 3). Round 5 (warm, medians): `synthetic` has 795 files,
2 of them `.build` (28.8 MB); stat of every file 0.7 ms; sha256 of the small files 9.5 ms and of the
two `.build` files 10.3 ms; a full copy of `cases/` and `eml/` 105 ms; `clonefile` 0.06-0.08 ms per
file; a per-card refresh estimated at 30-50 ms. Round 4: merging the label files 1.5 ms,
`checks.yaml` parse 23 ms, `git status` about 10 ms (17 ms in round 8). Round 8: `git rev-parse --symbolic-full-name @{u}`
about 6-7 ms, `merge-base` about 7 ms (about 12 and 14 ms through the `/usr/bin/git` shim, round 9; the
live check about 125 ms), `evalrun.load` unchanged by OD-632's two cards. The merged set
costs about 4.4 MB of copies across the three sets (`synthetic` cases 1.5 MB and `eml` 1.7 MB,
`drafts` 0.57 and 0.54 MB, `heldout3` 0.12 MB) plus linked `.build/`; Step 3 records the measured
refresh time. Rounds 21-25 (this Mac, load-dependent): building and scanning all 1,514 history blobs
9-46 s (about 6-30 ms a blob; 0 build failures, 45 flagged; run only at Steps 5 and 7); the merge
gate's tree
scan over 1,202 tip blobs 12-46 s; the hook on a push with no cards 0.25-0.7 s; the daily run about
13-14 s at load 4.5 (round 26; runner unmeasured). X13 with real `flock` (round 26): exactly one
refresh per set, no closes
for one install, three sets started within 8.8-16.4 s; Step 3 re-measures it in the real code.
