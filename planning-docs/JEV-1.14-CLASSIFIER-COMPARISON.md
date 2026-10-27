# Jev 1.14 classifier comparison

Status: draft 5, 2026-10-26.
- **Done 2026-10-27:** results in SPEC §21.2 ("Jev 1.14 vs Sonnet 5.5 vs Opus 5.5 as classifier at 4,000"). D failed stage 1 (−16.5 and −18.3 against Opus), so stage 2 and D 2 weren't run, and Jev stays shelved. Sonnet passed all four pairs; recorded, not adopted. The last runs ran from a detached worktree at `4fd5442` to keep the code identical.
- Step 1's TypeSafe facts were gathered on 2026-10-26: the price is unchanged, 1.14 accepted the key check, and the listing can't signal a new version. The operator chose to have the watch ask `jev-latest` which version answers.
- Draft 3's delta checks found no high findings: design said "Ready to build: yes" for steps 1-3 and 5, and measurement said "Measurement sound: yes". Their medium and low findings (R1-R9) are folded into this draft.
- Draft 2 was checked by two Fable agents (design and measurement), which produced Q1-Q17. The operator decided Q1, Q2 and Q8; the rest are folded in as recommended.
- Draft 1 was accepted by the operator on 2026-10-26.
- Phase R (two Fable reviewers) produced P1-P18. The operator decided P2, P3, P6 and P16; the rest are folded in as recommended. Full findings are in `state-archive/jev-114/review-findings.md`.
- Code references were checked against 0aa6c7b.
- New OD number: OD-NEW, assigned at commit; re-check the next free number (the state file says OD-560).

## Context
Jev has been shelved since OD-516 (2026-10-15). On the matched test, D (Jev classifies, Claude acts) scored 211/304 and C scored 256/304, a gap of −14.8 points. A revisit follows OD-538: D must pass at a −5 margin on the frozen set and on a confirmed generated set, with two runs per arm.

Two things have changed since then:
- **Newer version.** TypeSafe released **Jev 1.14.0** on 2026-10-14 (docs.typesafe.ai/models, read 2026-10-26). `jev-latest` and `jev-preview` both point to it. ecf is still pinned to `jev-1.13.0` (`src/ecf_server/data/jev.lock`).
- **Wider window.** OD-558 moved C's classifier to 4,000 characters, and Jev stayed at 1,500 with a TODO to raise it.

The operator asked for:
- the newest Jev at 4,000 only (no 1,500 run);
- a like-for-like comparison of Jev, Sonnet and Opus as the classifier at 4,000;
- a table of each classifier's cost and time;
- a way to track new Jev versions.

Docs facts (read 2026-10-26):
- Context is 64k tokens per request and 32k per question.
- The API documents only the 422 error.
- Input tokens are billed; output tokens are free.
- `/changelog` returns 404, so what changed in 1.14 is unknown.

## Decisions (operator 2026-10-26)
- **Staged OD-538 (P2).** Two runs per arm on the frozen 394-card set first. D must pass at −5 against Opus there. Failing either set fails the revisit, so the generated set (PROD 300 once settled, or `tests/eval/heldout3`, which OD-516 keeps for this) runs only if D passes stage 1.
- **Opus cost measured (P3).** One actor-only run on an Opus run's classifications. The all-Opus total minus that run gives the Opus classifier's cost. No estimate.
- **Checked release (P6).** The pin and caps move D's gate key, so the next release takes the checked path.
- **Review (P16).** Draft 2 is checked by two Fable agents: design, plus measurement.
- **Version tracking.** The weekly watch plus a status line, on this branch.

## Step 1. Branch `jev-1.14-window` (worktree `../ecf-jev-114`, off 0aa6c7b)

### Before any code: TypeSafe facts (each item needs a go-ahead, as each is a TypeSafe call)
- **(P6, Q15)** One key check against `jev-1.14.0`. Run it from the branch worktree after `jev.lock` is edited (`check_key` reads the lock and takes no pin; `jev.py:467`). It shows 1.14 accepts ecf's request format before the pin is merged.
- **(P7)** Read `api.typesafe.ai/openapi.json` and make one `GET /v1/models` call. Record the `release_date` format, whether an alias's date is its version's date, and whether `description` names the version. Record these in SPEC §21.2 with the date read.
- **(P11)** Re-read Jev's price. If it has changed, update `COST_PER_M_INPUT` and `COST_BASIS` (`src/ecf_server/evaljev.py:58-61`).

### Pin and window
- `src/ecf_server/data/jev.lock`:
  - `model` becomes `jev-1.14.0` and `checked` becomes the date read.
  - The `_comment` is updated.
- `src/ecf_server/jev.py:96-104`:
  - `WINDOW = 4000` and `EXCERPT_CHARS = WINDOW` (P13: the reason for the +1 is gone).
  - The comment says Jev reads the 4,000 the classifier reads (OD-NEW).
- No size-refusal risk: a 4,000-character state is at most about 16 KB, against `STATE_LIMIT` of 30,000 bytes (`jev.py:85-86`).

### Tests (P5)
Find what needs changing with `grep -rn "jev-1.13.0\|jev-1.14.0\|jev-s1-state\|gate_key\|1500\|1501\|WINDOW\|EXCERPT_CHARS" tests`:
- `tests/test_window_adopt.py:59-66` encodes OD-558's 1,500. Rewrite it so Jev reads the same 4,000 the classifier reads, as a test that fails on main.
- `tests/test_preset_d.py:219-224` uses `jev-1.14.0` as its "changed" pin. Change that to `jev-1.15.0`.
- Repin `SHIPPED_GATE_KEY` and the byte-stable key and digest tests (`tests/test_jev_dispatch.py:43, :144-149`).
- Add one test that a 4,000-character excerpt reaches the `state` uncut and a longer one is cut at a word (`message.cut`).

### Tracking new Jev versions (operator 2026-10-26: ask `jev-latest`)
**Facts from step 1 (2026-10-26):**
- The key check against `jev-1.14.0` returned HTTP 200 with `"model":"jev-1.14.0"`.
- `GET /v1/models` lists only `jev-latest` and `jev-preview`. Their `release_date` values are 2026-09-10T18:38Z and 2026-09-10T18:39Z: these are the alias creation dates, which didn't move when 1.14 shipped on 2026-10-14. They're full ISO timestamps, not the YYYY-MM-DD the OpenAPI spec states.
- Neither description names a version.
- So the listing can't signal a new version. A reply's `model` names the version that answered, even for an alias (OpenAPI `SystemOneResponse.model`).

**Design:**
- **Watch.** `model_watch.run` (`src/ecf_server/model_watch.py:241-247`) runs the Jev part whenever the TypeSafe key is stored, not only while D is available.
  - Each week it sends one request the size of the key check (`KEY_CHECK_QUESTIONS` / `KEY_CHECK_STATE`, `jev.py:116-121`). The request has `"model": "jev-latest"` and no email content, and costs about 280 input tokens (about $0.00001).
  - It records the reply's `model`. It keeps the listing read too, for `ecf models status`.
  - Verified: the Jev part never raises an alert or System Error, `ecf doctor` doesn't warn on it, and a locked store reads as no key.
- **`_jev`:**
  - When the answering version isn't the pin (`jev_keys.pin()`), record `newer` (the version and the date first seen). Put it on the next daily summary once, using the Claude part's `reported` pattern (`daily_lines` / `mark_reported`, `:419-440`).
  - A reply that isn't 200, or has no `model`, counts as a failure, shown in `ecf models status` only, as today.
  - `jev-preview` is never asked: it's a preview, never a pin candidate.
  - A pin is never changed automatically.
  - ~~The probe is a model call, so it's recorded like the service's other Jev calls: one `model_calls` row (`backend jev`), plus a `jev.call` log line without body or key.~~ Superseded (operator 2026-10-26): one `jev.watch` log line and the watch record; no `model_calls` row (OD-561).
- **`jev.lock`** gets no `released` field (dropped from draft 4).
- **Status line.** `src/ecf/cli_models.py:_jev_lines` adds: "TypeSafe's jev-latest is now <version> (seen <date>); the pin is <pin>: read docs.typesafe.ai/models".
- **Tests:**
  - With a key stored and D held back, the watch asks `jev-latest`.
  - An answer of another version is flagged and reported once.
  - An answer of the pin isn't flagged.
  - A non-200 reply, or one with no `model`, is a failure without a flag.
  - The fixture `_api` (`tests/test_model_watch.py:57-62`) routes by host and by method (P17).

### SPEC, CHANGELOG, docs (P4, P14, P15, P18)
- **SPEC:** update every passage that says Jev reads 1,500, names `jev-1.13.0`, or describes the watch as Anthropic-only:
  - §12.4 privacy statement (`SPEC.md:1777`): name the weekly requests to `api.typesafe.ai`, made only while a TypeSafe key is stored: the `/v1/models` listing, and one key-check request with `jev-latest` whose fixed text is "ecf key check" (no email content).
  - Also lines 927 (§7.6), 954 (§7.9), 981, 1785 (§12.4), 1982 (limits table) and 2219 (§16.2).
  - Add an OD-NEW row covering the pin, the window and the watch.
  - Amend OD-558 ("Jev keeps its 1,500 window") with a pointer to OD-NEW.
  - Add a dormant-path note (P18): excerpts stored before OD-558 are 1,500, so a live D item would reach Jev at 1,500. No action while D is held back.
- **Code comments:** `src/ecf/cli_models.py:76-77`, and `src/ecf_server/model_watch.py:20-25` and `:241`.
- **CHANGELOG:** two lines under OD-NEW: (1) Jev evaluations use Jev 1.14 and read the first 4,000 characters; (2) the weekly check now asks TypeSafe which Jev version `jev-latest` is, whenever a TypeSafe key is stored, and `ecf models status` and the daily summary say when it's newer than the pinned one.
- **`docs/admin-guide.md`:**
  - Line 344 becomes "shows which Claude models ecf uses, and the Jev version used for testing".
  - Lines 350-352 become:
    > ecf can also run its test emails through TypeSafe's Jev model, to compare it with Claude (`ecf eval run --classifier-backend jev`). This is for testing only: none of your mail goes to TypeSafe, and mailboxes can't use Jev. Jev reads the same first 4,000 characters of each test email that Claude does. To use it, store a TypeSafe key with `ecf models api-key set --provider typesafe`. While a key is stored, the weekly check also asks TypeSafe for new Jev versions, and `ecf models status` says when there's a newer one. ecf never switches versions by itself.
- Also fold in the `src/ecf/eval/providers.py` `resolve()` docstring fix from Next steps (no CHANGELOG line).

### Checks and merge
- Run the targeted tests, ruff, pyright and lint-imports.
- Run the full macOS gate with Colima up (0 skipped).
- Run the pre-merge Fable review.
- Commit, merge and push with OK, then watch CI.
- The next release takes the checked path (P6).

## Deterministic checks: no source change needed (both reviewers confirmed)
- Facts and fraud/regulator triggers come from the whole message (`evalrun.case_inputs_full`, `evalrun.py:1054-1058`).
- Redaction runs before the cut (`message.py:173`).
- `planned`, `held` and `rules_alone` read facts and the classification.
- Measures follow `asker.window` (`evalrun.py:799`).
- Repeats and remap cut at 4,000 and then at WINDOW, and the cut is idempotent.
- The stored-excerpt refusal can't be reached by a Jev run, so no corpus-to-TypeSafe path opens.
- **Caveat:** `jev_mapping`'s bands and bars were fitted on 1.13 at 1,500 and stay fixed. A change in what gets held can come from 1.14's probabilities, and the report says so. Refitting on the 394 set would fit to the test set.

## Step 2. Restart accept on the merged main (go-ahead)
`jev_keys` caches the pin, so a restart is required. accept is this session's background task (python pid 23629); signal the python pid, not the wrapper. Confirm the TypeSafe key is still stored.

## Step 3. Stage 1 of OD-538 on the frozen set (394 cards, 4,000; go-ahead per run)
Every Claude run uses sensitivity `standard` (Q14; `_claude_run` requires it to match). The actor is Opus in every arm, so only the classifier differs.

| Order | Run | Notes |
|---|---|---|
| 1 | **Opus 1:** a9019a61 rescored with `ecf eval rescore --synthetic` (P1) | No model run. Check that `rescored_from.set_version` is 5fb6fdc3e284fcba and that the new set version equals the other arms'. |
| 2 | **Opus 2:** a plain pinned `/ecf-eval` run (Q1) | A real gate run on the new labels, with the same set and pins as a9019a61. A fail shows in `ecf eval status` like any gate run. On accept, the gate's evidence moves from a9019a61 to Opus 2. A fail keeps accept's go-live gate closed until a later run passes. No address drops, because only a pin, schema or instructions change drops a live address, and accept's only address is paused (R2). The pre-gate diff review is skipped, because Phase R ran on a plan the build doesn't change. |
| 3 | **Opus actor only (P3):** `ecf eval run --claude --source-run <Opus 2>` | Opus 2's classifications, actor only. Check `summary.claude.source_run`. |
| 4, 5 | **Sonnet 1 and 2:** `ecf eval run --claude --classifier-model claude-sonnet-5-5 --not-for-gate`, then `/ecf-eval` | |
| 6-9 | **Jev 1, then D 1, then Jev 2, then D 2 (Q4):** `ecf eval run --classifier-backend jev`, then `/ecf-eval --preset D` | D takes the newest finished Jev run (`claude_eval.py:379-381`), so this order matters. Check each D file's `source_run`. |

- Each `/ecf-eval` runs in a fresh `ecf --install accept claude` session (models change between runs).
- The second runs exist for OD-538 and for noise: same-arm repeats have differed by 12-15 cases at n=304.

### Pass rules (OD-486, OD-516, OD-538; P8, Q8, Q10, Q13)
- **Absolute gates, every run (Q10):** `unsafe` empty, `fraud_guard_recall` 100, injection hits 0 (counted by the report script from per-case `injection_hit`, as the summary has no injection field; R4), and `proposals.unsafe` empty. For D, faulted Jev cases are reported as a count, from `summary.faulted` and `summary.jev.faulted_cases` (R9); a faulted case scores as a failure (`evalrun.py:571-572`), so a transport fault mustn't be read as a classifier error.
- **Primary, D vs Opus:** Opus is A in every pair, margin −5. D passes stage 1 only if **all four** pairs (D1 and D2 against Opus 1 and Opus 2) have a lower bound above −5 (Q8).
- **Secondary, Sonnet vs Opus:** the model-choice rule, same rule over all four pairs, safety gates first. Opus stays unless Sonnet passes.
- **Descriptive only:** per-field McNemar (which flags `priority` and `requires_action` on noise alone), and the 90 long cards end to end via `ecf eval compare --subset <file of long-* ids>`. `compare_fields` takes no subset (`cli.py:1953`), so per-field figures on the long cards come from the report script or are left out (Q9).
- **What the gate measures (Q13):** OD-538's gate is end to end, and its policy isn't symmetric: D's hold can drop hides, and fields Jev was unsure of score wrong. The pure classifier comparison is per-field plus the safety gates, and the report shows both.

### Precision (Q11)
The Newcombe paired interval's width depends on how often two arms disagree. Measured at n=304:
- same-arm repeats, 12-15 discordant: about ±2.4-2.7 points;
- Opus vs Sonnet, 20-23 discordant: about ±3.0-3.2;
- Opus vs Haiku, 31-32 discordant: about ±3.7;
- the 90 long cards, close arms (3-5 discordant): about ±5.5-6.2.

An OD-516-sized gap is about ±4-5. On 2026-10-15 Sonnet beat Opus by +2 and +3 points, with lower bounds of −1.0 and −0.2, so the secondary question is resolvable.

### Input differences and warnings to state (P12, Q12)
- Jev is asked questions, not the schema prompt.
- D's actor sees `facts.jev` in its risk context.
- D's hold drops hides before scoring, using a mapping fitted on 1.13 at 1,500.
- Determinism repeats run in the C arms only.
- Expected `compare` warnings:
  - The rescored file has no `scoring`, so compare prints "live vs recorded". The relabel changed labels only, so this is harmless.
  - D vs C prints different prompt, inputs (`excerpt_cap`) and models.
  - Sonnet vs Opus prints different models only.
- The set is synthetic and partly Claude-written; state that as a possible lean toward Claude.

### Output: cost and time table, per classifier, per run (Q2, Q3, Q5, Q6, Q7)
The figures are produced by a report script (Q2, kept in the session scratchpad, not the repo). It reads the stored per-case data in each result file.

| Row | Jev 1.14 | Sonnet | Opus |
|---|---|---|---|
| Classifications counted (R7) | first-pass cases + `determinism.cases`, counted by the script (expected 394 + 10) | `classify` + `repeat` claims, counted by the script (claims can be re-made) | same as Sonnet |
| API calls, and calls per classification | Σ per-case `tries` + repeat requests + probes (`breaker_trips`) (R5) | Sonnet group `calls` (about 2 per classification) | Opus 2 `agent` minus actor-only `agent` |
| Tokens: input, cache read, cache creation, output | `input_tokens_billed`, which includes repeats and probes (`breaker_trips` 0 expected); output free (R6) | Sonnet group | the same subtraction |
| **Classifier cost:** US$ total, per classification (÷ the count above), per 1,000 emails | tokens × the Jev price re-read in step 1 | tokens × list prices read the same day; `api_equivalent_usd` as a cross-check | same as Sonnet, plus the subtrahend and the Haiku main-session cost printed beside it, with uncertainty of about ±$0.1 (the actor-only repeat spread) |
| **Whole-run cost** | Jev cost + D run's `claude.all` | `claude.all` | `claude.all` |
| How it's billed | API, per token | Your Claude plan: dollars are an API-equivalent figure. Plan usage % comes from the session's status line or `ecf stats`, because the result file's `claude.plan` is often empty (R3). | same as Sonnet |
| **Time per request (latency), the comparable figure** | mean and median of `classifier_ms + wait_ms + queue_ms` per first-pass case (omits failed tries' HTTP time) | mean and median of `answered_s − read_s` over `classify` claims; batches share one read, so per-item latency is confounded by batching | same as Sonnet |
| **Classification wall time, total** | sum over first-pass cases (excludes repeats, pauses and parsing; R8) | max(`answered_s`) − min(`claimed_s`) over `classify` claims | same as Sonnet |
| **Whole-run wall time, total, and ÷ records** | Jev `summary.wall_s` + D run's `run_s` | `run_s` | `run_s` |
| Concurrency | 1 request at a time (`jev.py:758`) | many parallel agents | many parallel agents |

- **Throughput isn't comparable (Q3):** Jev runs one request at a time and Claude runs several agents in parallel, so wall time ÷ records is labelled with each arm's concurrency. Per-request latency is the comparable figure.
- Time can't be subtracted, because spawns overlap. Times come from claims, never from subtracting runs.
- Both runs of each arm are shown, since `run_s` varies widely between repeats (474 vs 758 s seen before).
- Opus 1 (rescored) has no actor-only twin, so its cost row shows `claude.all` only.

## Step 4. Stage 2 (only if D passes stage 1; go-ahead)
Two runs per arm (D and Opus at least) on a second confirmed set, under the same comparison rules. **The set (R1):** `tests/eval/heldout3` works as the code stands. PROD 300 doesn't: a run on a generated set is forced not-for-gate (`evalrun.py:673-674`), and D's source lookup skips not-for-gate Jev runs (`claude_eval.py:390`). Using PROD 300 would need a small, reviewed code change: let D take a not-for-gate Jev run on the same generated root, with the D run itself not for the gate. The operator decides at the stage-2 go-ahead.

## Step 5. Report and decide (operator)
- Produce the comparisons and the cost and time table.
- The operator decides: whether Jev stays shelved (OD-516, OD-538), and whether Sonnet can take the classifier role (the model-choice rule).
- Record the results in SPEC §21.2, adding an OD only if a decision changes.
- Keep the result files in `state-archive/evals/` until the decision is recorded.

## Verification
- The rewritten window test fails on main and passes on the branch. The targeted tests pass, and the full gate shows 0 skipped.
- The Jev run's summary shows pin `jev-1.14.0` and `excerpt_cap.excerpt_chars` 4,000. Every reply's `model` equals the pin; otherwise the run stops by design.
- Faults: 0 `request_error` or size faults (`summary.jev.faulted_cases`, `summary.faulted`). If any appear, report them rather than scoring past them.
- The rescored Opus file's set version equals the other arms'. The actor-only file's `source_run` is Opus 2. Each D file's `source_run` is the Jev run just before it.

## Constraints kept
- Jev stays eval-only, with synthetic text only, and is never mixed with Claude.
- A go-ahead is needed for each run and each TypeSafe call.
- Confirm before committing or pushing.
