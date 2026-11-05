# Move the adversarial eval subset to `eval/private/` (OD-618)

**Status: draft 10, 2026-11-05. Not built.** Draft 1 went through Phase R (four Fable reviewers:
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
withdrawn, and every other fix is folded in here with OD-634.
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

- **X1, one marker predicate, widened:** `ecf.eval.is_injection_card(card)`, with its rules in one
  constant, `INJECTION_MARKERS`, shared by the builder refusal, the pre-push and CI marker scans, the
  sdist test, the move script and the membership counts. A card is marked when it has
  `injection_target`, `injection_phrase` or `hidden_text` at any level of its front matter
  (`injection_target` also sits under `expected.safety`, e.g. `injection-delimiter-escape`), a
  `threat:` containing `inject` (case-insensitive; `evasion` is not a marker, V4),
  `expected.draft.group: injection`, or, by **content**, any string in the card that trigger 10's
  patterns match (X5). Why: a card whose only sign is a trigger-10 phrase in its body (OD-632's
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
  gone.
- **X5, one fold for the marker and trigger 10.** `src/ecf_server/skeleton.py` moves to
  `src/ecf/skeleton.py`, with its `data/unicode/` folder (`confusables.txt`, its licence and README;
  `scripts/check_licenses.py:54-56` follows), and the keyword compile helpers (`_START`, `_Pattern`,
  `_compile`, `_alternation`, `triggers.py:134-182`) move to `src/ecf/keywords.py`;
  `ecf_server.triggers`, `internal`, `draftcheck` and `excerpt` import them from there (import-linter
  forbids only `ecf` → `ecf_server`, `pyproject.toml:142-146`). It is a pure move: the trigger and
  skeleton tests pass with only their imports changed. `keywords.yaml` stays where it is
  (`keywords_digest` unchanged); `ecf` reads it at `Path(ecf.__file__).parent.parent /
  "ecf_server/data/keywords.yaml"`, which holds for an installed wheel and an editable install
  (`importlib.metadata` doesn't, and `importlib.resources` would import `ecf_server`); a test checks
  that file hashes to `keywords_digest()`. The content marker compiles the `injection` group's
  `acronyms` (case-sensitive) and `phrases` with those helpers and tests every string in the card
  (each front-matter key and value at any level, the body, and the raw file), each as written and
  with HTML tags removed and entities decoded (`html.parser`, the text pieces joined both with nothing
  and with a space), so a phrase split by tags, in the From display name or in an attachment name is
  caught. It is meant as a superset of what trigger 10 scans (`texts_of`, `triggers.py:465-469`:
  subject, From display name, attachment names, each part's visible and full text); over-marking
  only refuses a public card. Tests: for every built `.eml` of every set (`.build/` included), a
  non-empty `triggers.scan(triggers.texts_of(parsed))["injection"]` implies the card is marked
  (today the two agree exactly); each `injection` phrase and acronym in `keywords.yaml`, and the
  confusable, format-character and mark variants in `tests/test_triggers.py`, placed in a public
  test card's body, subject, From name or HTML, are refused. Checked 2026-11-05 on `bc98a9b`
  (scratch script, this rule): 50 `synthetic` cards with `starter-injection`, 4 `heldout3` and 54
  `drafts` are marked, the same as the non-content markers; content marks 13 `synthetic` cards, all
  already marked; `long-091` and `long-092` stay public.

SPEC rows: OD-626 (which cards; decisions 1, 4, V5), OD-627 (existing cards, the private repository; decisions 2, 3), OD-628 (V1), OD-629 (V2), OD-630 (V3, V4), OD-643 (the gate guard restated), OD-647 (W1, atomic writes and copies), OD-648 (W2, the shared resolver), OD-654 (draft 5: the `starter-injection` pin by its bytes; before-and-after measured on main's code), OD-633 (draft 9: X1-X3; amends OD-626 and OD-643), OD-634 (draft 10: X4, X5; amends OD-633). Their parent OD-618 is on branch `m1-aws-mode` until M1's planning merges.

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
  them and get no both-sets mode. One guard, `writable_root()`, runs inside every writer (`build_all`, `labels.write`,
  `fixed_labels`, `review.*`, `append_log`, `new-case` at `cli.py:931`, `generate`): it refuses a root
  whose resolved path or any ancestor is the merged folder or holds `merge.json`. A test searches
  `src/ecf/eval`, `src/ecf/cli.py`, `cli_evalgen.py` and `cli_card_review.py` for file writes, with a
  named allow-list: writes outside any set (`settle.export`, `mix.write_default`, `tools.py`,
  `codex_tool.py`, `corpus_labels.py`, `cli_evalgen._write_private`) and writes into a generated set's
  root only, guarded at entry by `generate`'s own check or `gen.verify` (`grade.py:165`,
  `generate.py:123`, `:802` and `:803`); plus `refresh_merged` (the one merged-folder writer) and the
  lock file's open. The scan matches `open(` with a `w`, `a` or `x` mode, `write_text`, `write_bytes`,
  `os.replace`, `os.rename`, `shutil.copy*`, `os.link` and the `ctypes` `clonefile` call (`mkdir`
  doesn't count), so a new writer can't skip the guard. `append_log` appends in place, which is safe: the merged `reviews.jsonl` is rebuilt,
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
  Ctrl-C still refreshes; `ecf eval label --review` refreshes after each changed card) and in `ecf
  eval build` (which builds both sets, then refreshes), under an exclusive lock on
  `eval/private/.merged/<set>.lock`, with `merge.json` written last; `refresh_merged` is the one writer
  of the merged folder and the only one that creates the lock file. Locks are `fcntl.flock` (per open
  file, so threads with their own descriptors exclude each other). A run takes a shared lock without
  blocking before the resolver's freshness check and `load`, opening the lock file read-only: `ecf
  eval run` in `evalrun.start()`, before the resolver and `load` (`evalrun.py:708`, on the request
  thread), inside one `try` that runs from taking the lock to a successful spawn (`:735`), whose
  `except BaseException` releases it and re-raises, so every refusal or failure in between releases it
  (the resolver, `load`'s own errors, no cases and Jev unconfirmed at `:709-716`, already running at
  `:718-719`, a failed thread start); once spawned, `work()` releases it in a `finally` around its body
  (`:723-733`). A Claude run (`/ecf-eval`) takes it in `start()`, keeps it on its `Run` and releases it
  in a `finally` around `_finish` (which does result I/O, `claude_eval.py:1008-1030`), in the
  prepare-failed branch (`claude_eval.py:654`) and in an `except` in `start()`. The lock is held as a
  file object; a release closes it and sets the field to `None` under `run.lock`, so a second release
  (a stop while preparing, `claude_eval.py:319-320` and `:1807-1816`, then `:654`) does nothing.
  No private set means no lock; with a private set and no lock file, a run refuses
  with "run `ecf eval build`". If it can't, it refuses with "a refresh is
  running; try again". A refresh takes the exclusive lock without blocking and, while a run holds the
  shared one, refuses with "a run is reading this set (see `ecf eval status`); run `ecf eval build`
  after it ends, or `ecf eval stop` it" (nothing refreshes by itself; meanwhile the resolver refuses
  readers of the stale merged set, which fails closed). A CLI writer whose `finally` refresh is refused
  after its own write succeeded prints the refusal as a note after its result and keeps its own exit
  code; if the writer failed, its own error is reported and the refusal is only a note, never masking
  it. `ecf eval status` without `--root` (the run list) never resolves a set, so the hint works while
  the merged set is stale. An abandoned Claude run keeps the lock until it
  expires (up to 24 h, `claude_eval.py:119`; expiry is checked lazily, `claude_eval.py:1179-1183`,
  when status or a session asks, and the refusal's `ecf eval status` hint triggers it) or is stopped. A resumed run takes the lock again and
  checks the merged version still equals its `set_version`; a service restart drops the locks
  (accepted). The
  service and the resolver never wait on this lock. One function, `merge_sources()`
  in `ecf.eval`, computes the merged content by kind (`merge_sources(kinds=…)`): `refresh_merged` asks
  for all four files, the gate and the resolver only for the two label files (a test: its output equals
  the merged files' bytes). In `labels.jsonl`, `fixed_labels.jsonl` and `checks.yaml`, which hold
  current state, it refuses any row or key whose ID isn't a card in that same source; in the
  append-only `reviews.jsonl` it refuses only rows naming a card of the other source (so a card removed
  later doesn't make its own history refuse); CI also refuses any public row or key matching the moved
  IDs (`d-inj-*`, `h3-inj-*`, the move list and `long-093`; IDs added later are guarded by the marker
  refusal, X1); and duplicate keys in `checks.yaml` (it loads through
  `ecf.yamlio`, whose loader refuses duplicate keys),
  so a private card's expected replies can't sit in a public file unnoticed; CI also runs this
  ownership check on the public sets alone. `cases/` and `eml/` are plain copies (small; a full copy
  measured 105 ms), made read-only; `.build/` files are links to the sources (large): an APFS clone
  where available (`clonefile` from `libSystem` through `ctypes`, since Python has none; cloned to a
  temporary name, then renamed, since `clonefile` refuses an existing destination; a non-zero return
  falls back), else a hard link, else a copy. The manifest records each merged file's own inode (a
  clone gets a new one) and reuses the previous sha256 when size, modification time and inode are
  unchanged. A hard-linked `.build/` file (the fallback) is writable by design: an in-place write through the merged folder would change the private
  source, which the sha256 check then refuses (fails closed); a writer's temporary-file-and-rename
  breaks the link instead. `labels.jsonl` and `fixed_labels.jsonl` merged by ID; `checks.yaml` merged by
  key; `reviews.jsonl` concatenated. The copy is incremental (only changed files rewritten, orphans
  removed last).
- **`merge.json` is a cache, not a trust root:** it records both sources' set versions, a manifest
  (path, size, modification time, inode, sha256) of every source file and every merged file, the
  merged version, each source's ID list, and the private commit and dirty flag. The resolver and the
  gate recompute the merge of the two sources' label and fixed-label files in memory (the merged
  `checks.yaml` and `reviews.jsonl` have no `src` reader, so only `refresh_merged` and the tests build
  them)
  (a few hundred lines) and require the merged files and the merged version to equal it, so a
  consistent hand edit of the merged files and `merge.json` is still refused.
- **The resolver (W2, OD-648), three cases:** no private set: the public set; private set present and
  the merged set fresh: the merged set; private set present and the merged set missing or stale:
  refused with "run `ecf eval build`". It refuses any path at or under `<repo top>/eval/private/`,
  comparing each ancestor's `(st_dev, st_ino)` with that folder's (APFS is case-insensitive, so a
  string comparison can be bypassed by letter case), and also any path with a `merge.json` in an
  ancestor; only `<repo top>/tests/eval/<name>`, or a path with no `tests/eval` parent, is read as a set.
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
- **Second guard: counts by membership.** Each run records `public_cases`, `private_cases` and
  `private_injection_cases`, counted over the cases the run loaded (a `--fraud-only` run counts its
  166, not the set's 398) and split by owning set; `private_injection_cases` counts only marked
  private cards (X1; `starter-injection`, V3, doesn't count). One function,
  `ecf.eval.membership(sources)`, computes them, and it runs only in `refresh_merged` (the build), CI,
  the sdist test and M1's `ecf-jobs` at upload: its `load_cards` costs about 740 ms on `synthetic`
  (round 9), too slow for a run start or the gate. `refresh_merged` stores each card's owning set and
  marked flag in `merge.json`'s manifest, keyed by the card file's sha256, and a run's start reads
  the counts from the manifest the resolver has just verified. The run also records the merged
  version; the gate refuses any of the counts being 0. The counts back up the version, which alone
  proves the run read the whole set, so a hand edit of the cached flags gains nothing.
- **References:** `fraud_guard_cases` and (unless `--fraud-only`) `confirmed` are compared with
  `load(merged, fraud_only=...)` on the current verified merged set, never with the run's own numbers.
- **Runs from before the guard** (such as `ce3be1df`): accepted only when their `set_version` equals
  the current verified merged version.
- **Order:** the existing version comparison (`gate.py:267`) runs first, then the membership and
  pre-guard checks, so a run on an older version gets the same "older version" message as today.
- **Dirty or stale clone:** a for-gate run (a `/ecf-eval` run with `for_gate` true,
  `claude_eval.py:154`; no `ecf eval run` backend is gate-eligible, `GATE_BACKENDS` is empty,
  `evalrun.py:144`) checks, live at its
  start and again whenever the gate's compute reruns, that neither `eval/private` nor `tests/eval/<set>` has uncommitted changes and that
  the private HEAD is on a branch whose upstream (`@{u}`) is a remote-tracking ref (its full name
  starts with `refs/remotes/`, read with one `git rev-parse --symbolic-full-name @{u}`, about 12 ms
  through the `/usr/bin/git` shim)
  and contains it (`git merge-base --is-ancestor HEAD @{u}`, no network; remote URLs count as trusted
  repository config); one function, `ecf.eval.clean_and_pushed(top, set)`, does this check for the
  service and for M1's upload; a detached HEAD, no upstream, or an upstream
  that is a local branch is not clean; `extensions.refStorage` is read from the config file and a
  reftable repository is refused, since its commits move no ref file; the run records the private commit and the merged version in its own metrics, and the
  gate's detail shows them. `git` runs in the service (the gate computes there, `gate.py:124`) as
  `/usr/bin/git -C <repository top> --no-optional-locks -c core.fsmonitor=false -c
  core.hooksPath=/dev/null -c core.attributesFile=/dev/null` with `GIT_OPTIONAL_LOCKS=0`,
  `GIT_CONFIG_NOSYSTEM=1` and `GIT_CONFIG_GLOBAL=/dev/null`, `status --porcelain --ignore-submodules
  --ignored=matching -- <set path>` (`-C <top>` and `tests/eval/<set>` for the public repository,
  `-C <top>/eval/private` and `<set>` for the private one; a pathspec, so changes elsewhere don't count;
  an ignored file under `cases/` or `eml/` counts as dirty, except `*.tmp` and `.DS_Store`, since an
  uncommitted ignore rule could otherwise hide a card the merge copies; the public `.gitignore`'s
  `!tests/eval/*/cases/**` (line 78) re-includes everything under `cases/`, so the two ignore rules
  for them go after it and the status parser also ignores those two names in `??` and `!!` lines; the
  check runs on the gate's set, `synthetic`, only, since `heldout3/eml/` is ignored in both
  repositories), a timeout of
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
  unchanged, measured 2026-11-05) and the index; and a stat walk of both source sets' `cases/`,
  `eml/` and label files (every file's name, size, `mtime_ns` and inode: about 2 ms for `synthetic`'s
  797 entries, measured in round 8; `.build/` is left out, its sha256 is checked on load), which catches an
  in-place edit that changes no folder or index time; a missing file is a value, not an error; the
  walk runs once per pass. In a worktree, where `.git` is a file, the paths come from its `gitdir:` line
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

- A card in a public set that X1's predicate marks is refused at build, except the exact ID
  `starter-injection` whose bytes hash to the pinned sha256 of its current file
  (`afebb16654e9867261b6dc457941ec5a8e2c2bb0093d63d5a8f35f7201d19a28`, 2026-11-05; OD-654). The card
  differs from the shipped `injection` template (`case_templates.py:39-57`): it has `author: claude`,
  `rule: fraud_guard` and `must_escalate: true`, added in 8a4f0f2 (OD-252) while the template wasn't, so
  a pin to the template would refuse today's card. One constant (ID and sha256) shared by the builder,
  the sdist test and a marker scan of `tests/eval/**/cases`, run by a committed `.githooks/pre-push`
  hook (it scans the blobs in each pushed range from its stdin, not the working tree, with the hygiene
  scan; CONTRIBUTING and AGENTS.md tell every checkout, Codex's included, to run `git config
  core.hooksPath .githooks`, and the private clone to set `core.hooksPath` to the public `.githooks` by
  absolute path; the hook finds the public repository top from its own location, not the current
  directory, and in `<top>/eval/private` runs the hygiene scan (every card there is marked by
  design) and refuses a push whose remote URL is the public repository's `origin` URL), by the
  macOS merge gate, and by CI on every push. A push from
  a checkout without the hook is caught only by CI, after it is published (stated limit); a test
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
  helper's temporary file is `.<name>.tmp`, and the public `.gitignore` gains `tests/eval/**/*.tmp` and
  `tests/eval/**/.DS_Store` after its line 78 (`!tests/eval/*/cases/**`, which would otherwise
  re-include them), so neither a crashed write nor Finder makes a clone dirty.

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
2. **Phase R:** done on draft 1 (Fable), drafts 2 to 9 (Opus delta). Draft 10 gets an Opus check
   of its changes (OD-577).
3. **Code** on a branch (Steps 3, 5 and 6 share it; the private repository's commit is pushed before
   Step 7): the atomic-write helper in every writer, `writable_root()`, `refresh_merged` with its lock,
   the resolver in `ecf.eval`, `merge.json` and the in-memory recomputation, the stored-root and draft
   registry changes, one-read loading and per-case re-check, missing-file reporting, the gate guard,
   path-only fingerprint with the `git` state stats, the hardened live dirty check, the builder refusal
   with the byte pin and orphan
   check, the move of `skeleton.py` and the keyword helpers into `ecf` (X5), ID ordering,
   `new-case` routing, the sdist test and CI marker scan, the test rewrites, a script
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
   version; a crashed write's `.<name>.tmp` leaves the clone clean; an ignored card under `cases/`
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
   a public-only for-gate run is refused; trigger 10 on every built `.eml` implies X1's predicate,
   and each `keywords.yaml` injection phrase and its confusable variants in a public card (body,
   subject, From name, HTML split by tags) is refused at build, by the pre-push scan and by the sdist
   test (X5); the trigger and skeleton tests pass after the move with only their imports changed;
   `keywords.yaml` read through `ecf`'s path hashes to `keywords_digest()`; `ecf.eval.membership`
   counts only marked private cards, and a run's start reads the counts from `merge.json` without
   calling it; a stale merged set, then a refused `ecf eval run`, then `ecf eval build` succeeds (no
   lock left behind), and so does a run whose thread start fails; a Claude run stopped while preparing
   releases its lock once; the private repository's hook refuses a push to the public `origin`; an
   orphan `eml/` file and an unparsable card blob are refused by the pre-push scan; on a dev install,
   a copy made as in Step 5 starts without secrets and writes nothing outside its scratch folder; a refresh between `ecf eval run`'s `load` and its thread start is excluded by the lock; a
   `cases/.DS_Store` leaves the clone clean; the pre-push hook in the private repository runs only the
   hygiene scan; a refused refresh in a writer's `finally` doesn't mask the writer's error; full suite
   green.
4. **Private repository:** the operator creates it (private, GitHub); cloned into the branch
   worktree's `eval/private/` for Steps 5-7, and into the main checkout's after the merge; first
   commit as above. *Pass:* `git check-ignore eval/private` true; `.gitattributes` present.
5. **Move** (X4). Check with the M1 session before starting. Nothing is installed or upgraded, and
   the install itself is only read.
   - **Preconditions:** `git diff <installed release tag>..<branch base> -- src/` touches nothing the
     `null` run or `set_version` reads (on 2026-11-05, `v3.2.0..bc98a9b` changes only
     `src/ecf/eval/draftjudge.py`'s `STATED_FAULT_CARDS`); no card is added, removed or relabelled in
     any set from P until the after-checks pass (`long-093` comes after Step 7).
   - **The copy:** stop the install's service (`ecf --install <install> service stop`), copy its data
     folder to `<scratch>/evalmove` (a short path: the socket path limit, `paths.py:89`), leaving out
     `run/` and `claude-config/` and copying `ecf.db` with its `-wal` and `-shm` files (or through
     SQLite's backup API), then start the install's service again; `PRAGMA integrity_check` on the
     copy returns `ok`. The copy keeps the install's role and settings; only its name differs. The
     secret store is keyed by install name (`ecf/<install>`, `secretstore/__init__.py:21`), so
     `evalmove` finds no IMAP, Slack, model or export-signing secret and can reach no mailbox, Slack
     workspace or model API; the eval checks need none. Before its first start, in the copy's
     database and side files: every `eval_runs.path` under the install's folder is rewritten to the same file under
     the copy (result paths are absolute, `evalrun.py:1463-1469`, and `rescore` writes next to its
     input, `:1615`); every stored eval root under the main checkout (`EVAL_ROOT`, the draft
     registry's roots and the side files' `root`) is rewritten to the same path under the branch
     worktree, where the private repository is cloned (the resolver reads a root by its shape,
     `<top>/tests/eval/<set>`, so it takes the same path either way); and a scan finds no other
     absolute path into the install's folder or the main checkout. Step 3 checks on a dev install
     that such a copy starts and writes nothing outside its scratch folder (its scheduled export
     included); if it would, the copy's `export_schedule` is set `off` first. *Pass:* the draft
     registry, the side files and R's result file (if there is an R) exist in the copy.
   - **Code:** P runs the installed release's own `ecf` and `ecf-server` (`ECF_HOME=<scratch>`;
     `ecf-server local --install evalmove --foreground` in one shell, `ecf --install evalmove` in
     another); the after-checks run the branch's with `uv run` from the branch worktree, with the same
     `ECF_HOME` and install name. *Pass:* `uv tool list` and the tool environment's `uv-receipt.toml`
     are unchanged at the end, and the install's service has run since the copy was made.
   - **P**, on the copy with the release's code, per set and read-only: for `synthetic`,
     `set_version` and a `null` run's case, confirmed and fraud-guard counts; for `drafts`,
     `set_version`, `evalrun.load`'s case count, every `card_digest` and the draft registry's
     `set_digest`; for `heldout3` (cards and labels only, no `.eml` or `.build/`), `set_version` and
     its 30 rows. Not `null-drafts`: its run ID is `sha256("null|" + version)`, so a before and after
     run would share a file. **R** is `evalrun.latest` for the current digest if it passes the gate,
     else there is no R (read from the gate's synthetic detail, which names its run ID); its gate
     check, `rescore --synthetic` and `compare` results are taken then too, and the `rescore` output
     file is copied aside (it is named by run ID and set version, `evalrun.py:1614`, so the
     after-rescore would overwrite it). As of 2026-11-05: `synthetic` `9370cd54d96f28bc` (398 cards),
     `drafts` `ae1111e7bc117b23`, `heldout3` `07625d6cafa4b278`; `ce3be1df` ran on
     `a6642d21bfd5eaaf`, so it no longer passes the gate and there is no R unless the operator runs a
     fresh C `/ecf-eval` on the install before the copy, with a go-ahead; the no-R branch below is the
     expected one.
   - **The move:** `git rm` the listed cards and their `.eml` and `.build/` files, remove their rows
     and keys from the public label, fixed-label, review and `checks.yaml` files, add them to the
     private repository, commit both, push the private repository. Then stop the copy's release
     service, start the branch's on the copy, and run `ecf eval build` from the branch; every
     after-check runs only then, since the live dirty-and-pushed check would otherwise refuse for a
     reason the move didn't cause.
   - **After:** the after-run results and the copied-aside `rescore` output stay in the session
     scratchpad (they name private IDs), the copy is deleted, and the install first runs the change
     with the next release.
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
     Step 3's rewrites; and no test opens a path under
     a moved ID;
   - a fresh clone of the private repository: every row's sha256 matches its file (built rows after
     `ecf eval build`); `git ls-files --eol` shows CRLF for `.eml`;
   - merged versions equal P; the real-set copy check (Step 3) is one-off and local only; the merged
     drafts `set_version` equals the draft registry's confirmed `set_digest` (as `draftruns.py:219`
     checks); the merged
     `checks.yaml`, parsed, equal to the public one before the move; a drafts load with unchanged
     `card_digest`s;
   - no row's file missing; a `synthetic` `null` run after the move (without `--subset`) equals the
     before-run on a named list of keys: case count, confirmed, fraud-guard cases, unsafe and
     `fraud_guard_missed` (both compared as sorted lists), and every per-case field keyed by `id` except `classifier_ms`, `wait_ms`
     and `queue_ms` (not `created_at`, `run_id`, `wall_s`, `environment`, `power`, `measurements` or
     the membership keys, which `evalrun.summarize` writes for `null` runs too);
     `public_cases + private_cases` equals the before-run's case count and `private_cases` equals
     the move script's count (both over the 398 cases a run without `--fraud-only` loads); every existing null-floor (`.null.json`) and calibration side file from before the
     move resolves, through `draftjudging.root_of` and the resolver, to the merged drafts set, and
     the stored `EVAL_ROOT` (written by the last for-gate run; a `null` run never writes it,
     `claude_eval.py:644`) resolves to the merged `synthetic` set;
   - if R exists: its gate check gives the same pass or fail and the same synthetic figures (apart from
     the fields the change adds: the private commit and merged version, and `GATE_VERSION`), and
     `rescore --synthetic` (compared with the copy set aside, excluding timings and `created_at`) and
     `compare` (R against the before-run and R against the after-run, excluding the two run IDs and
     `created_at`) give the same results; if not, the gate's synthetic refusal message is identical
     before and after (whichever run `evalrun.latest` picks, which can be a not-for-gate run,
     `evalrun.py:1486-1490`), never "isn't where the last run found it" (`gate.py:264`),
     apart from the same added fields (and the gate's evidence
     needs a fresh C run after the move, with the operator's go-ahead); a public-only for-gate run is
     refused.
6. **Docs:** `.claude/rules/eval-synthetic.md`, `planning-docs/GENERATE-FAKE-TESTING-EMAILS.md`, SPEC
   §16 (the pins, the sdist fact, the gate guard, X1's markers), OD-654's wording ("main's installed
   code" becomes the installed release's code, with Step 5's `src/` precondition), CONTRIBUTING,
   AGENTS.md, CHANGELOG.
7. **Merge:** a diff review on Fable, the macOS merge gate (twice, as above), push, CI watched.

## Measured costs (performance reviewer, 2026-11-05)

`set_version` 0.7 ms today, 1.6-3.2 ms merged; `load_cards` 381 ms (round 1; 738 ms on
`synthetic`, 326 ms `drafts`, 62 ms `heldout3` in round 9); X1's content match about 18 ms on
`synthetic` (first call about 70 ms, round 9); hygiene scan 275 ms; building the
394 small cards (then) 164 ms, `scanned-invoice-17mb` 2.8 s; `evalrun.load` 34 ms (about 63 ms in round 8, which also hashes `.build/`,
`evalrun.py:310`; re-measured at Step 3); sha256 of every file in the
set about 27 ms warm, stat about 1 ms (round 3). Round 5 (warm, medians): `synthetic` has 795 files,
2 of them `.build` (28.8 MB); stat of every file 0.7 ms; sha256 of the small files 9.5 ms and of the
two `.build` files 10.3 ms; a full copy of `cases/` and `eml/` 105 ms; `clonefile` 0.06-0.08 ms per
file; a per-card refresh estimated at 30-50 ms. Round 4: merging the label files 1.5 ms,
`checks.yaml` parse 23 ms, `git status` about 10 ms (17 ms in round 8). Round 8: `git rev-parse --symbolic-full-name @{u}`
about 6-7 ms, `merge-base` about 7 ms (about 12 and 14 ms through the `/usr/bin/git` shim, round 9; the
live check about 125 ms), `evalrun.load` unchanged by OD-632's two cards. The merged set
costs about 4.4 MB of copies across the three sets (`synthetic` cases 1.5 MB and `eml` 1.7 MB,
`drafts` 0.57 and 0.54 MB, `heldout3` 0.12 MB) plus linked `.build/`; Step 3 records the measured
refresh time.
