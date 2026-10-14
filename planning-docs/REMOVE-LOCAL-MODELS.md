# v3.0.0: remove Gemma and Ollama (plan, draft 2)

- **Status:** Draft 2, 2026-10-14, **approved by the operator 2026-10-14**. Draft 1 plus R-D1..R-D14 (operator, 2026-10-14) and Phase R
  P1-P28 (four Fable agents; all accepted as recommended, operator 2026-10-14). Next: R0.
  - Decisions: `state-archive/remove-local/operator-decisions.md`. Findings:
    `state-archive/remove-local/review-findings.md` (+ safety, measurement, cost-design,
    performance). Draft 1 is kept in the session scratchpad.
- **Request (operator, 2026-10-14):** "plan the full removal of Gemma and Ollama from the entire
  codebase and documentation. For now, we will be Claude-only or Jev-Claude. Leave Bedrock on the
  roadmap (if TypeSafe goes to Bedrock, we can revisit)." Tests involving Ollama/Gemma are out of
  scope by default from the same day.
- **Base:** `main` at `f88971b` (S4 merged). Draft 1's inventory was taken at `4d13ab1`; it is
  **re-taken** on the removal branch after `jev-step2` merges (P7). Line numbers are approximate.
- **Numbers:** OD-506 (this design) and ADR 0031, to reserve in `CLAUDE-STATE.md` at R0.
- **Release path:** checked (migration, data change, gate-key-adjacent `prompt_digest`, upgrade
  code; SPEC §1.4), with the `ecf1009` wheel → rollback → wheel cycle (P26).

## 1. End state

- **Presets:** C now; D (Jev + Claude actor) from v3.0.0 step 4. A and B gone. No Ollama, Gemma,
  local decision models, model queue, battery or heat rules.
- **Converted addresses (R-D3 + P3):** every A and B address becomes C at upgrade **and is
  paused**. Resuming it (`ecf address resume`) is the consent act: the operator confirms the
  account owner agreed to mail going to Anthropic (OD-427), or removes the address.
- **When Claude can't run:** C mail waits at `awaiting_claude`. The existing `claude_review`
  reminder (24 h) stays; a new escalation kind fires at 2× `claude_review_reminder_hours` (48 h),
  in `LOUD_KINDS` (a Slack mention), counts and dates only (R-D4, P10). No local fallback.
- **Service level (P18):** nobody classifies a C address's mail until someone runs `/ecf-review`
  in an `ecf claude` session. Former A addresses go from "within one check interval" to "at the
  next review". Release notes, SPEC §4.1 and the per-address notice say so, with an expected
  cadence and a per-email figure (about 1.7-2.3k input tokens per item on Sonnet agents, SPEC
  §10; Claude Code plan usage).
- **Kept because Claude or Jev uses it:** the scoring core of `evalrun.py`; `CorpusRun`,
  `corpus_run`, `rescore(session)`, `deterministic_noise` (P14); the parts of `actor.py` and
  `classifier.py` that `claude_review`/`claude_eval` call (`classifier.store`; `actor.problem`,
  `offered`, `decide_one`, `allowed`, constants); `systemone.questions`, `map_answers`,
  `PREAMBLE` (inlined byte-identical, P17) and `NOUL_TRUE`; `schema.from_v2` and friends and
  `load_schema_v2_0_0` (old results, labels, 0037; P6); `model_calls` (Jev, step 4); every field
  of the result models (P15).
- **Roadmap:** Bedrock stays (SPEC §1.2 M4, §4.1, OD-133/135) with "if TypeSafe offers Jev on
  Bedrock, revisit". The M4 "GPU server" item and the §1.2 Later items Heat, Ollama MLX, Shorter
  classifier keys and Grouped model calls go (P25).

## 2. Decisions (all made 2026-10-14)

| # | Decision |
|---|---|
| R-D1 | Part of v3.0.0. |
| R-D2 (P7) | Order: S4 (merged `f88971b`) → `jev-step2` rebased on `main` and merged (its own macOS gate) → this removal (inventory re-taken) → Jev 2d. |
| R-D3 (P3, P8) | 0038 converts every A and B address to C **and pauses it**; it never refuses. The importer does the same for older bundles. |
| R-D4 (P10) | Fallback removed; escalation kind at 2× reminder hours (48 h), LOUD, counts and dates only. |
| R-D5 (P14) | Delete only the model-driven `ecf eval run --corpus`; keep corpus session, labels, `eval routes --corpus`, `eval rescore --corpus` and their tests. |
| R-D6 (P5, P16) | Runner keeps `null` (now) and `jev` (2d). `production` becomes `gate_eligible = backend in GATE_BACKENDS and redact` (`GATE_BACKENDS` empty until step 4). |
| R-D7 (P1) | One rebuild of `addresses` with `CHECK (preset IN ('C','D'))`, through a new no-FK migrate path; `addresses.PRESETS = ('C',)` until step 4. |
| R-D8 (P2, P13) | Drop the dead columns and `fallback_shadow`; keep `model_calls`, `claude_since`, `pinned_models`; **do not purge gate rows** (a later release does); `DATA_FORMAT` **stays 4** (unreleased) with the drops folded in; bundles of format 3 still read. |
| R-D9 | Stub `ollama.lock` (`{"retired": true}`) in v3.0.x; removed in v4. |
| R-D10 (P4) | `release.json` `prompt_digest` and `classifier_schema` are Claude's; the upgrade output treats the change as expected. |
| R-D11 (P7) | JEV-STEP2 draft 5, plus the AST-test list, the contract text and `test_systemone`'s `local_schema` use. |
| R-D12 (P6) | Any old result file is readable and rescorable; only `eval run` refuses `gemma` and `systemone:`. |
| R-D13 (P13) | `upgrade_state.settle` (rollback window closed) runs a trimmed, idempotent `retire()`: unload and remove ecf's Ollama login item, remove `<root>/ollama/`. Release notes give `ollama rm` for model copies. `cli_destroy.SHARED` keeps "ollama" until v4. |
| R-D14 | Module names `actor.py`, `classifier.py` kept. |

## 3. Work, by stage

### R0. Docs and decisions (docs-only commit)
- **ADR 0031** "Claude-only and Jev + Claude; local models removed": supersedes ADR 0005 and the
  Ollama half of 0023; amend lines on 0006, 0017, 0022, 0025, 0028, 0030. OD-506 row; supersedes
  OD-003, -019, -020, -029, -054, -470, -473, -491, -495.
- **SPEC** (about 245 matching lines, P14): as draft 1, plus §1.5a item 2 closed and §16.5
  naming the surviving instruments (C via `/ecf-eval`, Jev via `eval run`, `null` as the
  rules-alone floor and wiring check) (P20); §4.1/§4.3 service level (P18); §5.3 backlog keyed on
  `awaiting_claude` (P9); §12.4 privacy and OD-427 reworded for the A→C case (P3); §11.10 upgrade
  and rollback (P4, P13); §15/§13.3 alert kinds (P10, P11).
- **CLAUDE.md** (operator OK at R0): presets; merge gate without Ollama, `ecf models install`
  dropped, timing figure re-measured after R3 (P24); privacy; model choice (Opus, Sonnet, Haiku;
  Jev for the classifier role).
- **README, SECURITY, CONTRIBUTING (incl. :17-29), DISCLAIMER, admin-guide, operator-guide**;
  admin-guide "v3.0.0: converted addresses" (pause, consent, resume, remove).
- **Planning:** v3.0.0-PLANNING draft 5; OPERATOR-SCHEMA; JEV-STEP2 draft 5 (R-D11);
  v3.0.1-PLANNING (its actor_high evidence used preset B source runs; D's source query may accept
  a C result, decided in 2d, P19). Finished plans stay as history.
- **Branches (P25):** close `grouped-model-calls` and `custom-local-classifier` (operator OK at R0).
- **CHANGELOG:** "Removed" line citing ADR 0031; release-note lines (P3, P4, P18, P22, P26).

### R1a. Service: fallback and model-queue path (green sub-commit, P21)
- Delete `fallback.py`, `modelq.py`, `pipeline.py`, `models.py`, `ollama.py`, `ollama_log.py`,
  `data/decision_models.lock`, `data/starter_rules_v2.yaml` (+ `rules.load_v2_starter`).
  `data/ollama.lock` becomes the stub (R-D9).
- `service.py` (model rounds, `RoundSchedule`, fallback ticks, Ollama log, model check,
  `daily.record_battery` and "On battery"), `api.py` (`ServiceState` model fields, status
  `model`/`fallback`, `/v1/checks` model round and `ecf check --until-empty`,
  `claude_queue_timeout` step-up, `/v1/models` GET/install/prune), `schedule.interval_offhours`,
  `Throttle`, `jobs.Queue.MODEL` (P17).
- `decide.py`/`approvals.py`/`daily.py`: backlog keyed on `awaiting_claude` (P9); `model_failed`
  readers incl. `cli_items`, `needs_you`, `inbox` (P12).
- Defaults "A" → "C" first: `cli.py:671`, `claude_pins.for_address`, `gate.Gate.preset`,
  `claude_queue.preset` (P12). `claude_pins`, `gate`, `claude_queue` (B edges), `policy`/`actor`
  (`local_high_risk`), `classifier` (Gemma half; `prompt_digest` callers → 
  `claude_review.prompt_digest`), `systemone` (keep `questions`, `map_answers`, inline `PREAMBLE`,
  `NOUL_TRUE`), `health` (`TITLES.get` guard), `restore.py:71`, `settings.py`, `stats.py`,
  `digests.py`, `initsetup.py`, `upgrade_state.py` (Claude digest, P4; `converted` field, P3).
- New alert kind for the 48 h escalation (P10).

### R1b. Migration 0038 (in R1a's commit or its own, green)
- **`migrate()` gains a no-FK mode** for migrations marked in a set (like `CHECKPOINT_AFTER`):
  `PRAGMA foreign_keys=OFF` before the transaction, the statements in `write_tx`,
  `PRAGMA foreign_key_check` after (any row aborts and restores), FKs back on (P1).
- 0038:
  1. `UPDATE addresses SET preset='C', paused=1` where preset in ('A','B'), removed rows too;
     the stage drop to `assist` and its audit/post go through a Python step at `_open_state`
     with a settings marker (`set_stage`, own reason; P12, P3).
  2. Rebuild `addresses` with `CHECK (preset IN ('C','D'))`, without `fallback_enabled`,
     `claude_queue_timeout_h`, `fallback_since`.
  3. `DROP INDEX items_model_waiting`; drop `items.fallback_at`, `model_attempts`,
     `model_failed`, `model_failed_at`; `gate.ollama_digest`; `DROP TABLE fallback_shadow`.
  4. `claude_since` = now for items the fallback had handed off (P12).
  5. Delete alert rows of removed kinds and settings rows `resident`, `ollama_log_*`,
     `daily.model_backlog` (P11).
  - `CHECKPOINT_AFTER` gains 38 (P27). Gate rows kept (R-D8).
- `release.json`: `schema_version` 38, `data_format` 4, `prompt_digest`/`classifier_schema`
  Claude's, comment rewritten. `importer.CLEAR` loses `fallback_shadow` (S10).

### R1c. Eval runner (green sub-commit)
- `evalrun.py`: delete `gemma`/`systemone:` backends, `--actor`, battery floor, Ollama
  readiness, `EXCLUSIVE`, `_classify_with` Ollama arms, `environment()`'s Ollama calls, model
  `--corpus` run, switches `new_wording`, `new_signals`, `excerpt_prep`, `message_signals` and
  `asked_only`/`planned_view` (P16). Keep `options` keys (`actor: false`), `pair_for(NULL) ==
  "null/local"` (P28), no determinism figure for `null`, `_remember_root` only from
  `claude_eval` (P16), `gate_eligible` (R-D6). Rescore accepts every result (R-D12).
- `claude_eval.py`: preset B gone; `--preset` C (D in 2d).

### R2. Client (green sub-commit with R1c or its own)
- `ollama_unit.py` → trimmed `retire()` only (R-D13), called from `settle`. `cli_models.py`
  (keep `api-key`, `watch`, Claude `status`), `cli.py` (`eval run`, `check`, `address add`,
  `eval stop`), `doctor.py` (no model rows; converted-and-paused addresses named, P3),
  `cli_init.py`, `cli_destroy.py`, `cli_stats.py`, `upgrade_check.py` (`read_wheel` without
  `ollama.lock`; `after_upgrade` treats a pre-3.0 digest as unchanged, P4; prints `converted`),
  `schema.py` (drop `local_schema`, `local_fields`, `_local_extended`, `LOCAL_VERSION`).

### R3. Tests, scripts, release machinery
- **Delete** 13 files (227 test functions, 297 parametrised cases; P24) after moving shared
  helpers (`check_kw`, `GOOD`, `BEC`, `add_address`) to `tests/helpers.py`; the kept parts of
  test_systemone (Jev's mapper), test_stats and test_corpus_eval (routes, rescore,
  `deterministic_noise`) move to their homes.
- **Rewrite** the ~20 tests from draft 1, plus test_addresses, test_digests_daily,
  test_slack_out, test_signals, test_schema_v3, test_user_layer (P17).
- **Preset literals:** `'A'`/`'B'` → `'C'` in the files that seed them (counts differ, 17-60;
  re-counted on the branch).
- **New tests:** no-FK migrate path on a populated database (items, grants, downloads, leases)
  with `foreign_key_check` clean; 0038 on A/B/C, removed rows included; a v2.2.3 (format 3)
  bundle restores; a format-4 bundle with A/B addresses and `model_*` columns imports as C,
  paused; a `null` run never passes the gate; one archived result of each shape loads and
  rescores (P15); the escalation kind; `alerts.sweep` with an unknown kind; `retire()`
  idempotent; the v2.2.x client's `read_wheel` accepts the wheel.
- **Scripts/CI/release:** `scripts/model_canary.py`, `canary.yml` comment, ecf-release skill
  (no `ecf models install`). CI unchanged.

## 4. Order

1. R0 docs commit (ADR 0031, OD-506), operator OK; close the two branches (P25).
2. Rebase `jev-step2` on `main`, macOS gate, merge (P7). Then branch `remove-local` from `main`
   in `../ecf-remove-local`, re-take the inventory.
3. Helpers move (green).
4. R1a, R1b, R1c+R2 as green sub-commits (P21); R3's deletions and rewrites go with the code
   they cover.
5. Preset-literal sweep.
6. Full suite (no Ollama anywhere), two-agent diff review, the checked release path's `ecf1009`
   cycle (P26), macOS gate, merge.

## 5. Verification

- ruff, pyright, lint-imports clean.
- `grep -rniE "ollama|gemma|modelq|preset ['\"]?[AB]\b" src tests scripts` returns only allowed
  hits: `from_v2`, the stub, `retire()`, `cli_destroy.SHARED`, results.py's archived-field
  names, `author: gemma` on old cards.
- Full suite, 0 skipped, Ollama not installed or not running.
- Migration and bundle tests as in R3.
- Upgrade on `ecf1009` (an A address today): converted, paused, named with its waiting-item count
  in the new CLI's phase 2; no "every address drops to assist" line from the new CLI; rollback
  restores A with Ollama still present; `settle` then retires the login item.
- Dev service: `ecf doctor` shows no model rows and names paused converted addresses;
  `ecf address add` offers C only; `/ecf-review` runs end to end; `ecf eval run
  --classifier-backend null` runs and doesn't pass the gate; `ecf eval rescore` of an archived
  Gemma result works.
- Docs grep as in draft 1.

## 6. Not decided here

- Corpus runs on Claude or Jev (privacy, later).
- A local model's return (Bedrock is the stated path to revisit).
- Linux M5 (branch `m5-linux`): its Ollama systemd work is moot; amend when M5 starts.
- Whether D's source query accepts a C result (2d, P19).
- Purging the old gate rows (a later release, R-D8).
