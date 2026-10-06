# Plan: decision models via Ollama `/v1/systemone` vs the Gemma 4 classifier

## Context

Preset A classifies with Gemma 4 12B through Ollama `/api/chat` with a JSON `format`. The v1.0.0 run `e04fac92`
scored 135/184 (73.4%) with 0 unsafe and fraud-guard recall 67/67. Its weak field is category, at 77.0%. Gemma gives
no calibrated confidence: the V1.3 single-token experiment (§7.7, OD-258) was not adopted. Ollama 0.35 added
`/v1/systemone` for "decision models". These return a choice plus a probability for every option, for up to 64 typed
questions in one request. The operator wants to know whether one could classify better than Gemma.

Operator decisions in this session (2026-10-06):
- **Role:** the decision model **replaces the classifier**. Gemma stays the actor, so B and C are untouched.
- **Gating:** the experiment and its adopt/not-adopt decision **gate `v1.0.0`**. It is a wave after the corpus plan's
  Phase B, before rc1.
- **Data:** the real-mail corpus from `planning-docs/GENERATE-EMAIL-CORPUS-PLAN.md` (Phase B, `ecf eval run --corpus`)
  **plus** the 185-card synthetic set. The synthetic set carries the absolute safety gates.
- **Coexistence:** the candidate and Gemma must **both stay loaded** on the 24 GB Mac, with no eviction or swap. This is
  measured, not assumed.
- **Qwen exception:** Qwen-based models are allowed **only** as the local decision model, **only** through Ollama. This
  is an exception to the CLAUDE.md model filter and needs a new OD.

First step after approval: save this plan as `planning-docs/SYSTEMONE-MODEL-TESTING-PLAN.md`. The plan is a working
document; SPEC stays authoritative.

## Facts this plan relies on (read 2026-10-06; to re-verify in Phase 0)

- **The endpoint** (docs.ollama.com/api/systemone; ollama.com blog 2026-09-29):
  - `POST /v1/systemone {model, state, questions}` with 1-64 questions of type `choice` (2-255 options), `noul`
    (boolean) or `score` (2-26 levels).
  - A request body is at most 64 KiB without images.
  - Probabilities are normalized over the candidates. `confidence = 1 − H(p)/ln N`, which the docs call "not
    calibrated correctness".
  - `keep_alive` is supported. No temperature parameter is documented.
  - Only GGUF models trained for System One work, so **Gemma itself can't be served this way**.
- **Installed here:** Ollama 0.35.0 (`ollama --version`, 2026-10-06), unified memory 24 GB, `iogpu.wired_limit_mb` 0
  (the default). Gemma is 8.9 GB resident (§7.5).
- **Candidates.** The source for each is its ollama.com library page, except where noted.

  | Model | Backbone | Disk | License | Context | Fit with Gemma |
  |---|---|---|---|---|---|
  | `tev1:4b` (Together AI) | Qwen3.5 | 4.4-4.5 GB | "MIT" stated for dataset builders and training scripts; **weights license unverified** | 256K | likely |
  | `tev1:0.8b` | Qwen3.5 | ~0.8 GB | as above | 256K | yes |
  | `nimble` 9B (Bespoke Labs) | Qwen3.5-9B | 9.3-9.5 GB | Apache-2.0 | 256K | **borderline**: 8.9 + ~9.5 GB plus KV cache against the default GPU limit (about 70-75% of RAM, per modelfit.io, unverified) |

- **Excluded:**
  - `clef-flash`: 11-12 GB, needs Ollama ≥ 0.35.1, and won't sit beside Gemma.
  - `clef` 27B: about 17-20 GB even at 4-bit.
  - Ollaya and its models: a different runtime (ONNX), not Ollama.
- **Every `/v1/systemone` model listed today is a Qwen3.5 fine-tune.** Without the Qwen exception there is nothing to
  test.
- **Published accuracy** (Ollama's 13-dataset set): nimble 75.7%, tev1 4B 73.3%, tev1 0.8B 63.5%. These are vendor
  figures and say nothing about email.

## Design of the experiment

**Question mapping** (one request per email, 8 questions; schema `src/ecf/data/schema_v1.yaml`):
- **`category`:** `choice`, 14 options.
- **`sender_type`:** `choice`, 6 options.
- **`priority` and `fraud_risk`:** `score`, 4 ordered levels.
- **`requires_action`, `requires_reply`, `payment_related` and `deadline_mentioned`:** `noul`.
- **Criteria text:** each option's text comes from `schema.prompt_block`, so both models get the same definitions.
- **`state`:** the same excerpt Gemma gets: `CLASSIFIER_CHARS` 1500, capped at 3000 bytes, `message.py`. It goes inside
  the same random-token delimiters, with the same "email is untrusted data, its claims are ignored" text (OD-255).
- **Answers:** the argmax answer per field goes through the existing strict Pydantic `parse`. Anything invalid is a
  failed attempt, exactly as now.
- **Downstream:** rules, policy, the fraud guard and the Gemma actor run unchanged, so end-to-end decisions are
  compared like for like.

**Safety invariant (unchanged):** model output can raise risk but never alone hide mail or approve. The probabilities
are recorded for calibration only. No confidence-based routing is part of this plan (OD-054 wording).

**Arms:**
- **G:** the current Gemma pin, from the existing runs, re-run on the corpus.
- **D1:** `tev1:4b`.
- **D2:** `nimble`, only if it passes the coexistence check.
- **D3:** `tev1:0.8b`, a cheap lower bound.

All arms run at the same set version and the same `corpus_id`, on AC, overnight (OD-230).

**Adoption rule** (all must hold; otherwise the decision is "not adopted"):
1. **Safety, absolute, on the synthetic set:**
   - fraud-guard recall 100% (OD-460);
   - injection set 0;
   - unsafe payment/fraud proposals 0;
   - schema-failure rate ≤ Gemma's.
2. **Non-inferior to G:** on end-to-end decision correctness, on **both** sets. The 95% paired-difference lower bound
   must be > −3 points (§16.5).
3. **Better than G:** on the corpus, either end-to-end correctness or category accuracy, by exact McNemar (Holm across
   the two). A non-significant gain isn't an improvement.
4. **Coexistence:** with both models loaded and a mixed load (G actor + D classifier) over the full corpus:
   - no model eviction (`/api/ps` load events, `load_duration` > 0 after warm-up);
   - no swap growth (`sysctl vm.swapusage` before and after);
   - `memory_pressure` stays normal.
   - Peak resident memory is from `footprint` on the runner processes, not `/api/ps` (§7.5).
5. **Latency:** p95 per email ≤ Gemma's p95, on AC and on battery.
6. **License:** the weights' license is verified, permits this use, and passes `scripts/check_licenses.py`'s spirit
   (recorded in SPEC).

**Also reported, not gating:**
- Calibration: ECE and Brier per field, and a reliability table. Is 0.9 right about 90% of the time?
- Determinism: the first 10 cases twice.
- Per-field McNemar with Holm, the confusion matrix and the 26 personal-mail cards separately (the OD-435 category gap).

**Corpus size:** **500 operator-labelled corpus messages**, the corpus plan's `--total` default (operator decision
2026-10-06; was 100, too few to detect a 5-point difference).

## Phase R: adversarial Fable review (before any code or SPEC commit)

Right after the plan is saved. Three parallel `Agent` calls with `model: "fable"`, read-only. Each gets this plan, the
corpus plan, SPEC §5.2/§7.3/§7.5/§7.7/§12/§16 and the critical files below, and is told to attack, not summarize.
1. **Eval validity and statistics.**
   - Power at 500 corpus cases.
   - Leakage: vendor training on public phishing sets similar to the cards; corpus mail the operator has already seen.
   - Label bias: labels are made after seeing Gemma's output?
   - Whether the question mapping favors one model (criteria wording, ordinal `score` vs Gemma's enum).
   - McNemar with three arms (multiplicity).
   - Non-inferiority margin.
   - Calibration metric choice.
   - Whether "better" can be met by category alone while fraud risk degrades.
2. **Safety and security.**
   - Prompt injection through `state` (no system prompt role in the API?).
   - Whether the decision model can lower fraud risk enough to move a fraud card off `fraud_guard`.
   - The Qwen exception's scope creep (could it leak to the actor or chat?).
   - Supply chain of new weights: pinning by manifest digest, tag moves.
   - Ollama's new endpoint surface: listener, `OLLAMA_ORIGINS`, request logging (§12.x).
   - The 64 KiB limit and truncation behavior.
   - Corpus content handling during runs, so no new destination.
3. **Resources and operations.**
   - Real co-residence on 24 GB: GPU wired limit, KV cache at `num_ctx`, `OLLAMA_MAX_LOADED_MODELS`,
     `OLLAMA_NUM_PARALLEL=1` (§5.2) and whether two models break Gemma's prompt cache.
   - Battery (OD-237).
   - `keep_alive` interplay (`5m` and `0` unload).
   - Eval queue exclusivity (`modelq.EXCLUSIVE`).
   - What adoption would cost: pin file, gate pair key (OD-278), doctor and `ecf models install`, `models.lock` vs
     `ollama.lock`.

**Output:** findings R1…Rn (severity, evidence file:line or SPEC §, proposed fix) in
`state-archive/systemone/review-findings.md` (gitignored). The operator accepts, revises or rejects each. Draft 2 of the
plan is then saved, and **no code is written before the operator approves draft 2**.

## Phase 0: facts and coexistence (throwaway, scratchpad; operator go-ahead)

This is a local measurement, not a real-service test. The pulls come from ollama.com, the same source as Gemma.
- `ollama pull tev1:4b tev1:0.8b nimble` and record each manifest digest.
- Confirm `/v1/systemone` on 0.35.0 with a synthetic card, then confirm the request and response shapes, the error
  codes and whether repeated calls are deterministic.
- **Coexistence:** load Gemma (`keep_alive` −1), then each candidate. Measure footprint, swap, `memory_pressure` and
  eviction with `OLLAMA_MAX_LOADED_MODELS` unset and set to 2.
- Measure latency per email, on AC and on battery.
- Verify each candidate's weights license from its model card or the Hugging Face repo.
- **Results:** in SPEC §21.2. A candidate that fails coexistence or the license check is dropped there, as a stated
  result.

## Phase 1: decisions and SPEC (first commit, after operator OK)

OD numbers follow the corpus plan's OD-461-463, so they are provisionally OD-464 onward.
- **OD-464:** the Qwen exception, local decision-model role only, Ollama only, pinned by digest. CLAUDE.md "Models" gets
  the same one-line exception.
- **OD-465:** the experiment design, the adoption rule and the gate on `v1.0.0`.
- **OD-466:** the corpus size: `--total` default 500, all 500 labelled (operator decision 2026-10-06). Recorded in
  the corpus plan's OD-462 if that is not yet committed.
- **ADR 0023** "Decision models via `/v1/systemone` (Qwen exception)". ADR 0005's filter line is referenced, not
  edited.
- **SPEC:**
  - a new §7.8 "Decision-model experiment";
  - §16.3 required comparisons (Gemma vs decision model, synthetic and corpus);
  - §16.4 calibration metrics;
  - §1.5 release criterion "decision-model comparison run on synthetic and corpus, adopt/not-adopt OD recorded";
  - §23.4 OD rows;
  - §1.2 replaces "Ollaya" under Later with a pointer to §7.8.
- Add the wave to the v1.0.0 plan (`~/.claude/plans/what-s-left-in-6b-mellow-quiche.md`).
- **CHANGELOG:** one line.

## Phase 2: build the harness (eval only; production classifier untouched)

- **New `src/ecf_server/systemone.py`:**
  - builds the 8 questions from `schema_v1.yaml` and `schema.prompt_block`;
  - calls `/v1/systemone` over the existing httpx client (`ollama.py:256-345` pattern: timeout, one retry, 127.0.0.1
    only);
  - returns a `ClassifierOutput` plus per-field probabilities.
- **New eval-only pin file:**
  - `src/ecf_server/data/decision_models.lock`, holding tag, digest and an `ecf_name` per candidate;
  - copied to `ecf/<name>:<release>` like `models.install` (`models.py:251`), so tags can't move under a run;
  - production `ollama.lock` is unchanged.
- **`evalrun.py`:**
  - a classifier backend seam replacing the direct `classifier.ask/parse` calls at `evalrun.py:480-494`
    (`gemma` | `systemone:<name>`);
  - `pair` no longer hardcoded (`:467`), and the result records the candidate's digest and probabilities;
  - `latest()` keyed by backend + digest.
- **CLI:**
  - `ecf eval run --classifier-backend systemone:<name>` (CLI token only, OD-288 style);
  - `ecf models install --decision <name>` (eval-only);
  - works with `--corpus FILE` from the corpus plan.
- **`src/ecf/eval/metrics.py`:** `ece`, `brier`, a reliability table. `CaseResult` gains optional probabilities.
  `ecf eval compare` shows them.
- **Doctor:** a candidate's digest row, shown only when one is installed.
- **Tests:**
  - `tests/test_systemone.py`: question building, parse, invalid answer → failure, 413/500 handling, loopback only. Uses
    a fake HTTP server.
  - Eval backend selection.
  - Metrics unit tests.
  - Compare across backends.

## Phase 3: runs (operator names each; overnight on AC)

1. Synthetic set: D1, D2 (if kept), D3, each with `ecf eval compare` against `e04fac92`.
2. Corpus: G, D1, D2, D3 on the same `corpus_id` (preset A only, so corpus content stays on this Mac).
3. A coexistence soak: live shadow-mode mix on the dev service with the winner, full corpus replayed (corpus Phase A).
   Memory and eviction are logged.

## Phase 4: decision

- Results go into SPEC §7.8 and §16.
- OD-467 "adopt X" or "not adopted", per the adoption rule.
- **If adopted:** shipping it as the preset A classifier is a separate build step. It needs a new pin home, the gate
  pair key (OD-278), install and doctor, and a drop to assist (OD-014). The operator schedules that step before rc1 or
  after `v1.0.0`; the gate is the recorded decision, not the adoption.
- When the wave is complete, prompt the operator for the state of the release work. No tag is applied.

## Critical files

- **New:**
  - `planning-docs/SYSTEMONE-MODEL-TESTING-PLAN.md`
  - `src/ecf_server/systemone.py`
  - `src/ecf_server/data/decision_models.lock`
  - `docs/adr/0023-decision-models-systemone.md`
  - `tests/test_systemone.py`
- **Changed:**
  - `src/ecf_server/evalrun.py`
  - `src/ecf_server/models.py`
  - `src/ecf_server/doctor` rows (`src/ecf/doctor.py`)
  - `src/ecf/cli.py`
  - `src/ecf/eval/{metrics,results}.py`
  - `SPEC.md`
  - `CHANGELOG.md`
  - `CLAUDE.md` (the filter exception line)
- **Reused:**
  - `classifier.parse`, `fit`, `INSTRUCTIONS`
  - `schema.prompt_block` / `json_schema`
  - the `ollama.py` httpx client and readiness checks
  - `models.install` copy-and-verify
  - `modelq.EXCLUSIVE`
  - `evalrun.score` / `summarize`
  - `ecf eval compare` (McNemar, Holm, non-inferiority)
  - `metrics.wilson` / `confusion` / `macro_f1`

## Verification

- **Phase 2 checks:** `uv run pytest tests/test_systemone.py tests/test_eval*.py`, ruff, pyright, lint-imports. Then the
  full suite once before asking to commit (`uv run pytest -n auto -rs`, 0 skipped, with Colima and Ollama up).
- **An Ollama-marked test:** a real `/v1/systemone` call on `tev1:0.8b` (small, fast), skipping only like the existing
  Ollama tests.
- **Phase 3 runs:** each run's summary includes gate_passed, recall, unsafe, ECE/Brier and p50/p95. `ecf eval compare`
  output is pasted into SPEC.
- **After each push:** `gh run watch <id> --exit-status`.
