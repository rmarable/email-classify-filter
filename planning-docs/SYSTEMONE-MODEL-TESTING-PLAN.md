# Plan: decision models via Ollama `/v1/systemone` vs the Gemma 4 classifier

_Draft 2, 2026-10-06. Folds in the Phase R review (R1-R30, all accepted as recommended, operator decision 2026-10-06;
findings in `state-archive/systemone/review-findings.md`, gitignored). `[Rn]` marks text that comes from a finding._

## Context

Preset A classifies with Gemma 4 12B through Ollama `/api/chat` with a JSON `format`. The v1.0.0 run `e04fac92`
scored 135/184 (73.4%) with 0 unsafe and fraud-guard recall 67/67. Its weak field is category, at 77.0%. Gemma gives
no calibrated confidence: the V1.3 single-token experiment (§7.7, OD-258) was not adopted. Ollama 0.35 added
`/v1/systemone` for "decision models". These return a choice plus a probability for every option, for up to 64 typed
questions in one request. The operator wants to know whether one could classify better than Gemma.

Operator decisions (2026-10-06):
- **Role:** the decision model would **replace the local classifier**. Gemma stays the actor. The local classifier
  runs for preset A, preset B and the preset C local fallback (`models.py:127-130`, `classifier.py:114-115`), so
  adoption would change all three; B's pair key would change too (OD-278). [R14]
- **Gating:** the experiment and its recorded adopt/not-adopt OD **gate `v1.0.0`**. It is a wave after the corpus
  plan's Phase B, before rc1. **Any adoption ships after `v1.0.0`**; the release gate is the recorded OD, not the
  adoption. [R23]
- **Data:** the real-mail corpus from `planning-docs/GENERATE-EMAIL-CORPUS-PLAN.md` (Phase B) **plus** the 185-card
  synthetic set. The synthetic set carries the absolute safety gates **only**; comparisons of correctness are made on
  the corpus. [R2]
- **Coexistence:** the candidate and Gemma must **both stay loaded** on the 24 GB Mac, with no eviction. Measured, not
  assumed.
- **Qwen exception:** Qwen-based models are allowed **only** in the local classifier role, **only** through Ollama on
  127.0.0.1, pinned by digest, eval-only until the decision OD. Never the actor, never a Claude role. Needs a new OD.
  [R14]
- **One candidate:** `tev1:4b`. nimble and `tev1:0.8b` are dropped (below). [R8, R24]

The plan is a working document; SPEC stays authoritative.

## Facts this plan relies on (read 2026-10-06; re-verify in Phase 0)

- **The endpoint** (docs.ollama.com/api/systemone; ollama.com blog 2026-09-29; docs read, not tested):
  - `POST /v1/systemone {model, state, questions}` with 1-64 questions of type `choice` (2-255 options), `noul`
    (boolean) or `score` (2-26 levels).
  - A request body is at most 64 KiB without images. Not binding here: `prompt_block` is 2,301 chars, `state` ≤ 3,000
    bytes, plus 8 questions (measured by a reviewer).
  - Probabilities are normalized over the candidates. `confidence = 1 − H(p)/ln N`, which the docs call "not
    calibrated correctness".
  - `keep_alive` is documented. No temperature parameter is documented. **Unverified:** whether the request takes
    `num_ctx` or any instruction/system field, whether `score` levels take per-level text, and which response fields
    exist (e.g. a prompt token count). [R6, R9, R19, R28]
  - Only GGUF models trained for System One work, so **Gemma itself can't be served this way**.
- **This Mac:** Ollama 0.35.0, 24 GB unified memory, `iogpu.wired_limit_mb` 0 (default). Ollama's own log reports
  17.3 GiB of Metal memory available; Gemma's load takes 7,686 MiB at `num_ctx` 4096 and Ollama keeps a 1,536 MiB
  floor, leaving ≈ 8.9 GiB for a second model. Gemma's runner grows to 12.2 GB over a long run (saved-state prompt
  cache, §21.2); every fit figure uses 12.2 GB, not 8.9 GB. [R8]
- **Swap baseline** with no model loaded: 2,164 MB (Colima 2 GB VM, Slack, Claude apps resident). [R11]
- **Candidate** (ollama.com library page):

  | Model | Backbone | Disk | License | Context | Fit with Gemma |
  |---|---|---|---|---|---|
  | `tev1:4b` (Together AI) | Qwen3.5 | 4.4-4.5 GB | "MIT" stated for dataset builders and training scripts; **weights license unverified** | 256K trained; loaded context unverified | likely, if context is bounded (R9) |

- **Dropped:**
  - `nimble` 9B: weights alone ≈ 8.9 GiB, the whole space left beside Gemma; Ollama would evict Gemma or spill to CPU.
    [R8]
  - `tev1:0.8b`: a lower bound that answers no adoption question; costs a machine-night. [R24]
  - `clef-flash` (11-12 GB, needs Ollama ≥ 0.35.1), `clef` 27B (17-20 GB), Ollaya (ONNX runtime, not Ollama).
- **Every `/v1/systemone` model listed today is a Qwen3.5 fine-tune.** Without the exception there is nothing to test.
- **Published accuracy** (Ollama's 13-dataset set, datasets not named): tev1 4B 73.3%. Vendor figure; says nothing
  about email. Whether its training data overlaps public phishing sets like the cards is **unverifiable**; the plan
  makes correctness claims on the corpus only for that reason. [R26]

## Design of the experiment

**Question mapping** (one request per email, 8 questions; schema `src/ecf/data/schema_v1.yaml`):
- **`category`:** `choice`, 14 options.
- **`sender_type`:** `choice`, 6 options.
- **`priority` and `fraud_risk`:** `score`, 4 ordered levels.
- **`requires_action`, `requires_reply`, `payment_related` and `deadline_mentioned`:** `noul`.
- **Criteria text:** each option's text is byte-identical to `schema.prompt_block`. The exact question text, option
  order and `state` layout are fixed in OD-465 before any run. If `score` takes per-level text (Phase 0), it uses the
  schema's level names only, so neither arm gets definitions the other lacks. [R19]
- **`state`:** the same excerpt Gemma gets (`CLASSIFIER_CHARS` 1500, capped at 3000 bytes, `message.py`), after the same
  `redact_injection`, inside the same random-token delimiters. Where the OD-255 text goes depends on Phase 0: in an
  instruction field if the API has one, otherwise prepended to `state` (and SPEC says that the defense is weaker there,
  since it sits beside the attacker's text). [R6]
- **Answers:** the argmax answer per field goes through the existing strict Pydantic `parse`. Anything invalid is a
  failed attempt, exactly as now.
- **Downstream:** rules, policy, the fraud guard and the Gemma actor run unchanged. The comparison is **pipeline vs
  pipeline** (prompt format and model together), not model vs model. [R19]

**Safety invariant (unchanged):** model output can raise risk but never alone hide mail or approve (`policy.py:113-146`,
rule 1 all-OR). Probabilities are recorded for calibration only. No confidence-based routing (OD-054 wording).

**Arms:**
- **G:** the current Gemma pin. Synthetic: `e04fac92` plus a re-run in the co-resident configuration. Corpus: a new run.
- **D:** `tev1:4b`, the only candidate.
- **N (synthetic only):** a null classifier that answers every field at its least risky value. It measures how many
  `fraud_guard` cards the deterministic path (facts, triggers) carries without any model, i.e. how sensitive the
  recall gate is to the classifier. Reported, not gating. [R5]

All arms run at the same set version and the same `corpus_id`, on AC, overnight (OD-230), with exactly one ecf service
running (see "Run procedure"). [R10]

**Corpus labels** (requirements on the corpus plan's Phase B; that plan's Phase R takes them as input): [R4, R5, R18]
- Labelled **blind**, before any corpus run of any arm, from the excerpt only. `ecf eval label --corpus` never loads a
  result file (tested). Phase A Correct/Fix labels are not reused.
- Every message carries `category`, `fraud_risk`, `payment_related` and the other schema fields. The expected `rule` and
  `safety` are derived from the labels by `ecf rules test`, not set by hand.
- Labels are frozen before the G corpus run: their hash becomes the set version. Labelling dates and run dates are
  recorded in the OD.
- Where G and D disagree after the runs, the operator may adjudicate with both answers shown unattributed, in random
  order. A changed label is recorded and both arms are re-scored.
- `ecf eval compare` scores confirmed labels only (today it ignores `confirmed`, `results.py:73-74`). [R4]
- **Composition:** the minimum per-class counts are pre-registered in OD-465. If the most-recent fetch from one folder
  falls short for `invoice`, `vendor_change_request` or `payment_request`, a second fetch from an invoice-heavy folder
  tops the corpus up.

**Corpus size:** at least 500 labelled messages (operator decision 2026-10-06). The final N is set **after** the
synthetic D-vs-G run measures how often the pipelines disagree: N gives 80% power for a 5-point end-to-end gain at
that discordance (exact McNemar, α 0.05). At 20-30% discordance that is about 800-1,000 messages. If the operator
keeps 500, OD-465 states that only gains of about 8 points or more are detectable. [R3]

**Adoption rule** (all must hold; otherwise the decision is "not adopted"):
1. **Safety, absolute, on the synthetic set:**
   - fraud-guard recall 100% (OD-460);
   - injection set 0 (this measures the deterministic layer plus the model; see "Also reported" for the model-only
     figure) [R6];
   - unsafe payment/fraud proposals 0;
   - `fraud_risk` under-rating on cards labelled medium/high: D's count ≤ G's. [R5]
2. **Fraud on the corpus:** [R5]
   - no message the operator labelled `fraud_risk` medium/high, or whose expected rule is `fraud_guard`, that G
     escalated and D did not (zero regressions);
   - `fraud_risk` under-rating rate on labelled medium/high messages: D ≤ G.
3. **Non-inferior to G on the corpus:** end-to-end correctness, paired difference, Newcombe/Tango score interval (not
   Wald). The margin is pre-registered in OD-465 from the measured discordance, before the corpus runs. No NI test on
   the synthetic set. [R2]
4. **Better than G on the corpus:** the primary endpoint is end-to-end correctness, exact McNemar, α 0.05. Category
   accuracy (and macro-F1) is secondary. Holm runs over every confirmatory test actually run. The gain must also exceed
   the run-to-run noise floor (D twice on the synthetic set). A non-significant gain isn't an improvement. [R17, R25]
5. **Coexistence**, over the full corpus run (D classifier interleaved with the Gemma actor) and a few hundred
   alternating calls in Phase 0: [R11]
   - **no eviction:** `ollama.log`'s `loaded runners` count stays at 2, and no call after the first per model has
     `load_duration` > 1 s (ecf's cold-start threshold, `stats.py:10-11`);
   - `memory_pressure` stays normal throughout.
   - Reported, not gating: `footprint` of both runners and `ecf-server` each minute (peak and last), `vm_stat`
     compressor and swap deltas against the recorded baseline, with the stated app set (service, Slack, Colima, corpus
     in memory; Claude Code and browsers closed). [R30]
6. **Latency:** classifier-call p95 and p50, D call vs Gemma classifier call, same cases, same power source, both in
   the co-resident configuration: D ≤ G on AC over the corpus, and on battery over a fixed subset (first 50 corpus
   messages plus the synthetic fraud subset) that completes on one charge. Per-email time is reported, not gated (the
   shared Gemma actor dominates it). A run that paused for power doesn't count. [R12]
7. **Provenance and license:** the model is in Ollama's `library/` namespace (SPEC §7.5) or the OD amends that rule
   explicitly; its weights license is verified from the model card or Hugging Face repo, permits this use, and is
   recorded in SPEC; the GGUF blob sha256 matches the publisher's published hash where one exists. [R15]

**Also reported, not gating** (calibration doesn't enter the decision; that is a stated choice): [R20]
- Calibration per field: ECE on the argmax probability (5-10 equal-mass bins, bootstrap CIs), ranked probability score
  for `priority` and `fraud_risk`, Brier for booleans, each against a constant predictor at the arm's accuracy, and a
  reliability table.
- The model-only injection figure: injection subset with redaction off (eval-only flag), with and without the OD-255
  preamble, plus the new classifier-targeted cards. [R6]
- G's accuracy counted with and without its schema failures (D can't fail parse). [R19]
- Noise floor: D twice on the full synthetic set; flip rate. G-vs-G already exists (0fcef342 vs 4954590a). [R25]
- Per-field McNemar (descriptive on the synthetic set, where some fields have n < 20), confusion matrix, category
  macro-F1 with per-class counts, the 26 personal-mail cards separately (OD-435), and N's recall. [R17, R18]
- SPEC gets aggregates only; per-case ID lists stay in the 0600 result files. [R27]

## Phase R: adversarial Fable review — done 2026-10-06

Three read-only Fable reviewers (eval validity and statistics; safety and security; resources and operations).
Findings R1-R30 (12 high, 12 medium, 6 low), all accepted as recommended; this draft folds them in. Findings and
decisions: `state-archive/systemone/review-findings.md`. **No code is written before the operator approves this draft.**

## Phase 0: facts and coexistence (throwaway, scratchpad; operator go-ahead)

A local measurement, not a real-service test. The pull comes from ollama.com, the same source as Gemma.
- **One service:** stop the shadow service first (pid 77724 was running on 2026-10-06); `pgrep -f ecf-server` count is
  0 or 1 throughout. [R10]
- **Version:** everything runs on Ollama 0.35.0. If `tev1:4b` doesn't run there, stop and record it, or upgrade
  deliberately with an OD, re-run G on the new version and re-pin. Never upgrade silently. [R22]
- **Pull and provenance:** `ollama pull tev1:4b`; record namespace, publisher, manifest digest and GGUF blob sha256;
  cross-check the sha256 against the publisher's Hugging Face file where published; verify the weights license. A
  non-`library/` model or an unverifiable license is dropped as a stated result. [R15]
- **API shape:** with a synthetic card, record the request fields (any `system`/instruction field, `num_ctx`/`options`,
  per-level `score` text), the response fields (any prompt token count), error codes, and whether repeated calls are
  deterministic. [R6, R19, R28]
- **Context:** after the first call, read the loaded context from `ollama ps`. Confirm whether `num_ctx` is honoured;
  if not, set `OLLAMA_CONTEXT_LENGTH` for the experiment and note the login-item `ENV` change adoption would need. A
  bounded context is a pass condition; the value goes in §21.2. [R9]
- **`keep_alive`:** set 5m and confirm `ollama ps` UNTIL; set 0 and confirm unload. [R21]
- **Logging and errors:** send a canary string in `state` in a malformed request and in an over-64 KiB request; check
  the error body and `ollama.log` for the canary. [R16]
- **Coexistence**, production-like: Gemma loaded under `ecf/gemma4-12b:<release>` with ecf's options (`num_ctx` 4096,
  `keep_alive` 5m), the candidate with the same keep-alive; record the baseline and app set; run a few hundred
  alternating calls (classifier on D, actor on Gemma) so Gemma's saved-state cache reaches working size; measure as in
  adoption rule 5. No `OLLAMA_MAX_LOADED_MODELS` variation (it is already the default). If the Ollama server is started
  by hand, bootout the login item, use the same `ENV`, and restore it afterwards. [R11, R21]
- **Latency:** classifier-call time on AC and on battery.
- **Injection preamble:** on the injection subset, redaction off, with and without the OD-255 text (in an instruction
  field if one exists, else prepended to `state`). [R6]
- **Vendor datasets:** name the 13 datasets if published; note any public phishing corpus. [R26]
- **Results:** in SPEC §21.2. A candidate that fails version, provenance, license, context or coexistence is dropped
  there as a stated result, which ends the experiment with "not adopted".

## Phase 1: decisions and SPEC (first commit, after operator OK)

OD numbers follow the corpus plan's OD-461-463, so they are provisionally OD-464 onward.
- **OD-464:** the Qwen exception. Scope: Qwen-based decision models, local classifier role only (A, B and the C
  fallback if adopted), through Ollama on 127.0.0.1, pinned by manifest digest, eval-only until OD-467; never the
  actor, never a Claude role. Any `OLLAMA_*` setting the experiment needs is recorded here (and added to `EXPECTED_ENV`
  if adopted). CLAUDE.md "Models" gets the same one-line exception. [R14, R29]
- **OD-465:** the experiment design: question text and layout, arms, labelling rules, minimum class counts, corpus N
  (after the synthetic discordance), NI margin and CI method, adoption rule, the gate on `v1.0.0`, and adoption only
  after `v1.0.0`. The parts that need the synthetic discordance (N, margin) are filled in by a follow-up commit before
  the corpus runs. [R2, R3, R17, R18, R19, R23]
- **ADR 0023** "Decision models via `/v1/systemone` (Qwen exception)". ADR 0005's filter line is referenced, not
  edited.
- **SPEC:**
  - a new §7.8 "Decision-model experiment";
  - §16.3 required comparisons (Gemma vs decision model; safety on synthetic, correctness on corpus);
  - §16.4 calibration metrics;
  - §16.5 the score-interval method for NI and Holm over the confirmatory family;
  - §1.5 release criterion "decision-model comparison run, adopt/not-adopt OD recorded";
  - §23.4 OD rows;
  - §1.2 replaces "Ollaya" under Later with a pointer to §7.8.
- Add the wave to the v1.0.0 plan (`~/.claude/plans/what-s-left-in-6b-mellow-quiche.md`) with the schedule below.
- **CHANGELOG:** one line.

## Phase 2: build the harness (eval only; production classifier untouched)

- **New `src/ecf_server/systemone.py`:**
  - builds the 8 questions from `schema_v1.yaml` and `schema.prompt_block` (fixed by OD-465);
  - calls `/v1/systemone` over the existing httpx client (`ollama.py:256-345` pattern: timeout, one retry, 127.0.0.1
    only, `trust_env=False`);
  - maps errors to cause codes and never surfaces a 4xx `detail` from this route (Slack, `eval status`) [R16];
  - without a prompt token count, fails closed on `len(state) > MAX_INPUT_BYTES`; HTTP 413 is a case failure, not
    FATAL [R28];
  - returns a `ClassifierOutput` plus per-field probabilities.
- **New eval-only pin file `src/ecf_server/data/decision_models.lock`:** tag, digest, blob sha256, `ecf_name`,
  `role: classifier`, `eval_only: true`, `license`, `source_url`, `verified`. Production `ollama.lock` is unchanged.
  [R14, R15]
- **`ecf models install --decision <name>`:** `<name>` must be in the lock (else `InvalidInputError`); pull → digest
  check → copy to `ecf/<name>:<release>` → re-check (`models.py:251-266`); does **not** set `INSTALLED_KEY` or call
  `retry_all`; prunes its own old copies. [R13]
- **`evalrun.py`:** [R1, R13]
  - a classifier backend seam replacing the direct `classifier.ask/parse` calls at `evalrun.py:480-494`
    (`gemma` | `systemone:<name>`, name resolved through the lock);
  - the candidate's digest is checked before the run and after any `server`/`model_missing` fault; a mismatch is FATAL
    and no result is saved;
  - a D run stores the **candidate's** digest in `digest` and `pair` `systemone-<name>/local`, keeps
    `classifier_digest`/`actor_digest` in the summary, and `summarize` sets `gate_passed=False` for any non-production
    backend;
  - a null-classifier backend `null` for arm N (eval-only); [R5]
  - per-call role, duration and power source recorded, and a run that paused for power is marked; [R12]
  - an eval-only flag that skips `redact_injection` on the injection subset (reported runs only). [R6]
- **Gate isolation:** `gate.py`, `claude_eval._a_run` and `fallback` filter on `pair` as well as digest. [R1]
- **CLI:** `ecf eval run --classifier-backend systemone:<name>|null` (CLI token only, OD-288 style); works with
  `--corpus FILE`.
- **`ecf eval label --corpus`:** never loads a result file. [R4]
- **`src/ecf/eval/results.py`:** `compare` scores confirmed labels only; Newcombe/Tango interval for NI; Holm over the
  confirmatory endpoints. [R2, R4, R17]
- **`src/ecf/eval/metrics.py`:** ECE (equal-mass bins, bootstrap CI), RPS, Brier, reliability table, under-rating
  rate for an ordinal field. `CaseResult` gains optional probabilities and per-call timing. `ecf eval compare` shows
  them, plus classifier-call p50/p95 per role. [R12, R20]
- **New synthetic cards** (through the eval card process, operator-labelled): cards that evade trigger 10 and target
  `fraud_risk` none, `payment_related` false, `sender_type` automated and `category` notification, plus a "pasted
  answers" card. The set version changes, so G is re-run on the new set. [R6]
- **Doctor:** a candidate's digest row, shown only when one is installed.
- **Tests:**
  - `tests/test_systemone.py`: question building, parse, invalid answer → failure, 413/500 handling, error detail not
    surfaced, loopback only. Uses a fake HTTP server.
  - Eval backend selection; unknown `<name>` refused; digest mismatch FATAL with no result.
  - A D run and an N run change neither `gate.synthetic` nor `_a_run` nor the fallback fingerprint. [R1]
  - Scope: `ollama.lock` stays Gemma; lock entries carry `role: classifier` and `eval_only: true`; `actor.ask` takes
    no model parameter; no settings key selects a classifier backend. [R14]
  - `eval label --corpus` reads no result file; `compare` skips unconfirmed labels. [R4]
  - `--decision` install leaves `INSTALLED_KEY` alone and doesn't call `retry_all`. [R13]
  - Metrics unit tests; compare across backends.

## Phase 3: runs (operator names each; overnight on AC unless stated)

**Run procedure, every run:** one ecf service (`pgrep -f ecf-server` count 1, recorded); Ollama 0.35.0; the candidate's
digest verified; the stated app set; baseline memory and swap recorded; `ollama.log` kept for the run. [R10, R11, R22]

1. **Synthetic:** G (new set, co-resident), D twice (noise floor), N. `ecf eval compare` D vs G gives the discordance.
   [R5, R25]
2. **Corpus N and NI margin:** from the discordance, the operator confirms N and the margin; OD-465 follow-up commit.
   [R3]
3. **Corpus labelling:** blind, frozen (see "Corpus labels"). [R4]
4. **Corpus:** G and D on the same `corpus_id` (preset A only, so corpus content stays on this Mac). The D run is also
   the coexistence soak (adoption rule 5). [R7]
5. **Battery subset:** G and D on the fixed subset, on battery, one charge each. [R12]

No live shadow-mode mix: the full corpus eval already interleaves the D classifier with the Gemma actor. [R7]

**Schedule estimate** (from SPEC §16.2's ~14 s per card; D's call time unknown until Phase 0): [R24]
- Machine: synthetic G + 2×D + N ≈ 2.5 h; corpus G ≈ 2 h at 500 (≈ 4 h at 1,000); corpus D ≈ 1-1.5 h at 500;
  battery subset ≈ 2 short sessions. About 2-3 AC nights plus two battery sessions.
- Operator: blind labelling of 500-1,000 messages (the dominant cost; estimate in hours after the corpus plan's
  20-message test), adjudication, and the decisions at steps 2 and Phase 4.
- Order: after the corpus plan's Phases 0, 1, A and B and its real-service test, and after this plan's Phases 0-2;
  before rc1.

## Phase 4: decision

- Results go into SPEC §7.8 and §16 (aggregates only). [R27]
- OD-467 "adopt `tev1:4b`" or "not adopted", per the adoption rule. The OD is the `v1.0.0` gate.
- **If adopted, it ships after `v1.0.0`** as its own build step, scheduled by the operator. Its cost, listed here so
  the decision is made with it in view: [R23]
  - preset A's pin model is single-digest throughout (`ollama.py:85-105, 377-399`, `models.install/recopy/prune`,
    `modelq.models_tag`, `claude_pins.py:112-115`, hard-coded `pair` strings); a second local pin turns A's pin key
    into a `pins-…` hash, so every A and B address's review count restarts and `live` drops to `assist` (OD-278,
    OD-014);
  - `recopy` after an upgrade must copy both models; the idle unload and `resident` must cover the candidate [R21];
  - login item and doctor gain any new `OLLAMA_*` setting (`EXPECTED_ENV`) [R29];
  - +4.5 GB disk per release copy;
  - a new preset A gate run and a shadow run.
- When the wave is complete, prompt the operator for the state of the release work. No tag is applied.

## Critical files

- **New:**
  - `src/ecf_server/systemone.py`
  - `src/ecf_server/data/decision_models.lock`
  - `docs/adr/0023-decision-models-systemone.md`
  - `tests/test_systemone.py`
  - new synthetic injection cards under `tests/eval/synthetic/`
- **Changed:**
  - `planning-docs/SYSTEMONE-MODEL-TESTING-PLAN.md` (this draft)
  - `src/ecf_server/evalrun.py`, `src/ecf_server/gate.py`, `src/ecf_server/claude_eval.py`, `src/ecf_server/fallback.py`
  - `src/ecf_server/models.py`
  - `src/ecf/doctor.py`
  - `src/ecf/cli.py` (`eval run`, `eval label --corpus`, `models install --decision`)
  - `src/ecf/eval/{metrics,results}.py`
  - `SPEC.md`, `CHANGELOG.md`
  - `CLAUDE.md` (the filter exception line)
- **Reused:**
  - `classifier.parse`, `fit`, `INSTRUCTIONS`; `triggers.redact_injection`
  - `schema.prompt_block` / `json_schema`
  - the `ollama.py` httpx client and readiness checks
  - `models.install` copy-and-verify
  - `modelq.EXCLUSIVE`
  - `evalrun.score` / `summarize`
  - `metrics.mcnemar_exact` / `holm` / `wilson` / `confusion` / `macro_f1` / `ordinal_mae`; `stats.COLD_LOAD_S`

## Verification

- **Phase 2 checks:** `uv run pytest tests/test_systemone.py tests/test_eval*.py tests/test_gate*.py`, ruff, pyright,
  lint-imports. Then the full suite once before asking to commit (`uv run pytest -n auto -rs`, 0 skipped, with Colima
  and Ollama up).
- **An Ollama-marked test:** a real `/v1/systemone` call on the pinned candidate, skipping only like the existing
  Ollama tests.
- **Phase 3 runs:** each summary includes gate_passed (always false for D and N), recall, unsafe, under-rating counts,
  ECE/RPS/Brier, classifier-call p50/p95 per power source, eviction and memory figures. `ecf eval compare` aggregates go
  into SPEC.
- **After each push:** `gh run watch <id> --exit-status`.
