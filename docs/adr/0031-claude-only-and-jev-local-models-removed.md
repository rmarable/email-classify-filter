# ADR 0031: Claude-only and Jev + Claude; local models removed

- **Status:** accepted (operator decisions 2026-10-14, OD-506: R-D1 to R-D14 and Phase R P1-P28
  of the removal plan); part of v3.0.0; not built yet (stages R1-R3 of the plan). Supersedes ADR
  0005 and the Ollama half of ADR 0023; amends ADR 0006, 0017, 0022, 0025, 0028 and 0030.
- **Context source:** `planning-docs/REMOVE-LOCAL-MODELS.md` (draft 2, approved 2026-10-14);
  its Phase R review (four read-only agents; findings P1-P28 in
  `state-archive/remove-local/review-findings.md`, gitignored); `planning-docs/JEV-STEP2.md`
  (draft 5); `planning-docs/v3.0.0-PLANNING.md`; SPEC §4, §5, §9.3, §11.9-§11.11, §12.4, §16

## Context

Preset A (Gemma 4 12B on Ollama, ADR 0005) was not prioritized from 2026-10-12 (OD-495): on the
frozen set preset C scored 83.2% against Gemma's 68.3% (SPEC §21.2), and on the fanless
development Mac Gemma slows to 11-16 tokens/s within minutes of sustained work. v3.0.0 makes C the
default and adds preset D (TypeSafe Jev classifier + Claude actor, ADR 0028). Keeping Ollama meant
keeping a model queue, battery and heat rules, a fallback with its own gate, a second schema (the
local model was still asked v2, ADR 0030) and the eval and test paths that serve them. The
operator asked (2026-10-14) for the full removal of Gemma and Ollama: "For now, we will be
Claude-only or Jev-Claude. Leave Bedrock on the roadmap (if TypeSafe goes to Bedrock, we can
revisit)."

## Decision

- **Presets C and D only** (R-D1, R-D14): A and B go in v3.0.0; D arrives with v3.0.0 step 4.
  Gone with them: Ollama, Gemma, local decision models, the model queue, the battery and heat
  rules, `local_high_risk`, the `resident` setting and ecf's Ollama login item. Module names
  `actor.py` and `classifier.py` stay; only Claude's parts remain in them.
- **Converted addresses** (R-D3, P3, P8, P12): migration 0038 makes every A and B address C
  **and pauses it**, removed rows included; it never refuses. The importer does the same for
  older bundles. A converted address drops to `assist` through `set_stage` (audited, its own
  posted reason). Resuming it (`ecf address resume`) is the consent act: the operator confirms
  the account owner agreed to mail going to Anthropic (OD-427, reworded for this case), or
  removes the address.
- **No local fallback** (R-D4, P10): `claude_queue_timeout`, its shadow runs and its gates go.
  C mail waits at `awaiting_claude`. The 24 h `claude_review` reminder stays; a second alert
  kind fires at 2x `claude_review_reminder_hours` (48 h by default), loud (a Slack mention),
  counts and dates only.
- **Schema** (R-D7, R-D8, P1, P2, P11, P27): one rebuild of `addresses` with
  `CHECK (preset IN ('C','D'))` through a new no-FK migrate path (`foreign_key_check` after);
  `addresses.PRESETS = ('C',)` until step 4. Dead `addresses` and `gate` columns,
  `items_model_waiting` and `fallback_shadow` are dropped; the four dead `items` columns stay,
  unused, until v4 (diff review R1, 2026-10-14: each `DROP COLUMN` rewrites `items`); alert rows
  of removed kinds and dead settings rows are deleted.
  `model_calls` (Jev logging), `claude_since` and `pinned_models` stay. Gate rows are not purged
  (a later release does). `DATA_FORMAT` stays 4 (never released), with the drops folded in;
  format-3 bundles still read.
- **Release metadata** (R-D9, R-D10, P4): `release.json`'s `prompt_digest` and
  `classifier_schema` are Claude's. The wheel ships a stub `ollama.lock` (`{"retired": true}`)
  through v3.0.x so a v2.2.x client can read it; removed in v4. The new client treats a pre-3.0
  digest as unchanged.
- **Clean-up** (R-D13, P13): when the rollback window closes (`upgrade_state.settle`), a trimmed,
  idempotent `retire()` unloads and removes ecf's Ollama login item and `<root>/ollama/`. Model
  copies are the operator's to remove (`ollama rm`; release notes). `ecf destroy` still names
  Ollama as residue until v4.
- **Eval** (R-D5, R-D6, R-D12, P5, P6, P14, P15, P16): `ecf eval run` keeps the `null` backend
  (the rules-alone floor and a wiring check) and gains `jev` in step 2d; `gemma` and
  `systemone:` are refused. A run counts for the gate only when `backend in GATE_BACKENDS and
  redact` (`GATE_BACKENDS` empty until step 4), so a `null` run never passes. Claude is measured
  through `/ecf-eval` (preset C; D from 2d). The model-driven `ecf eval run --corpus` goes; the
  corpus, its labels, `eval routes --corpus` and `eval rescore --corpus` stay. Every old result
  file stays readable and rescorable; result models keep every field.
- **Order** (R-D2, P7, R-D11): S4, then `jev-step2` rebased and merged, then this removal, then
  Jev step 2d (JEV-STEP2 draft 5: no actor in a Jev eval run; Jev compared with C only).

## Alternatives considered

- **Refuse the upgrade while an A or B address exists** (draft 1's recommendation, with a v2.2.x
  patch adding `ecf address preset`): rejected by the operator for auto-conversion; P3 added the
  pause so no owner's mail reaches Anthropic without the consent step.
- **Keep A, not prioritized** (OD-495): rejected; it kept every local-model path alive for a
  preset nobody runs in production.
- **Keep the fallback for C** (OD-019): rejected; mail waits, with the 48 h escalation.
- **Data format 5**: rejected (P2); v2.2.3 ships format 3 and its client refuses a jump of two.
- **Purge Gemma and fallback gate rows in 0038**: rejected (P13); a rollback merges `gate` from
  the live database and would lose them.

## Consequences

- **Consent and pause:** every converted address is paused after the upgrade; `ecf doctor`, the
  new CLI's upgrade output (`converted` in `GET /v1/upgrade/state`) and a Security Notice name
  each with its waiting-item count. Nothing goes to Anthropic for it until resumed.
- **Service level** (P18): nobody classifies a C address's mail until someone runs
  `/ecf-review` in an `ecf claude` session. Former A addresses go from "within one check
  interval" to "at the next review". The model-free pre-check still flags fraud and regulator
  mail at fetch.
- **Cost** (P18): about 1.7-2.3k input tokens per item on Sonnet agents (SPEC §10), on the
  reviewer's Claude plan; every synthetic gate run is a C `/ecf-eval` (plan usage) or, from 2d,
  a Jev run (TypeSafe charges).
- **Safety** (P22): `local_high_risk` goes, so on a converted address a high-risk non-fraud item
  can get a send proposed; approval and step-up still apply, and fraud and regulator rules are
  unaffected.
- **Rollback:** `ecf upgrade --to <v2.2.x>` restores A and B addresses from the snapshot with
  Ollama still installed (clean-up waits for settle); gate rows survive the merge.
- **Upgrade message** (P4): a v2.2.x client's pre-check says every address drops to `assist`
  (the digest it compares changes); that over-reports and can't be patched, so the release notes
  say so. Only C's gate key is unaffected by the digest change.
- **Old results:** archived Gemma, `systemone:` and preset B results stay readable and
  rescorable as history; `ecf eval compare` still refuses different label maps.
- **Roadmap:** Bedrock stays (M4; SPEC §1.2, §4.1, OD-133, OD-135): if TypeSafe offers Jev on
  Bedrock, revisit. The M4 "GPU server" item and the Later items heat, Ollama MLX, shorter
  classifier keys and grouped model calls go; the branches `grouped-model-calls` and
  `custom-local-classifier` are closed (P25). A local model's return is not decided here.
- **Release path:** checked (a migration, `prompt_digest`, upgrade code; SPEC §1.4), with the
  `ecf1009` wheel, rollback, wheel cycle (P26).
- **Linux (M5):** its Ollama systemd work is moot; amended when M5 starts.
- Superseded or amended ODs: OD-003, OD-019, OD-020, OD-029, OD-054, OD-470, OD-473, OD-491,
  OD-495 (SPEC §23.4).
