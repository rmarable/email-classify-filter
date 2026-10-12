# ADR 0027: Qwen3.5 9B may be evaluated as the local model

- **Status:** accepted (operator decision 2026-10-12, OD-491); widens the Qwen exception of ADR
  0023 (OD-470) for one candidate; evaluated 2026-10-12: fails the safety gate and
  non-inferiority, Gemma stays; exception revoked 2026-10-12 (OD-494, ADR 0029)
- **Context source:** SPEC §7.5, §7.8, §16.5; ADR 0005, ADR 0023; the grouped-model-calls
  measurements of 2026-10-11 (branch `grouped-model-calls`, not merged)

## Context

Preset A runs both local roles, classifier and actor, on Gemma 4 12B (ADR 0005). Speed work on
Gemma has run out of cheap gains: grouping model calls showed no measurable benefit under steady
mail (2026-10-11), and every actor call re-reads its whole prompt because Gemma's sliding-window
attention leaves llama-server no checkpoint to resume from. The project's model filter excludes
PRC-affiliated labs (CLAUDE.md; ADR 0005); ADR 0023 allowed Qwen-based decision models in the
classifier role only. The operator wants to consider a Qwen model as a replacement for Gemma.

On the 24 GB development Mac, only small dense Qwen models fit in Gemma's place (Ollama library,
read 2026-10-12): `qwen3.5:9b` is 6.6 GB at Q4_K_M against Gemma's 8.02 GB; the mixture-of-experts
models (`qwen3:30b-a3b`, `qwen3.5:35b-a3b`, `qwen3.6:35b`) are 19 GB or more. Qwen3.5 9B
(Alibaba; Apache-2.0, unverified, confirm before adoption) was released about 2026-03-02 (sources
disagree by a week); Gemma 4 12B on 2026-06-03 (Google's release log).

## Decision

- **Second Qwen exception** (OD-491): Qwen3.5 9B (Ollama `qwen3.5:9b`) may be evaluated as the
  local model in place of Gemma 4 12B, in both local roles (classifier and actor), only through
  Ollama on 127.0.0.1, pinned by manifest digest, and only for evaluation until the decision is
  recorded. Never a Claude role. Other Qwen models stay under OD-470 only; the filter otherwise
  stands, and ADR 0005 is not edited.
- **How it is judged:** by the evaluation rules (OD-486, SPEC §16.5): the frozen synthetic set
  through `ecf eval run`, every schema field, the same signals block, per-role figures and the
  prompt digest, compared with Gemma by the one rule (Newcombe, `--margin`), input differences
  stated. Speed is measured the same way (p50/p95 per role, on AC).
- **Adoption is a separate decision**, with its own OD and an amendment here.

## Alternatives considered

- **Keep the filter as it was:** rejected by the operator (2026-10-12).
- **A Qwen mixture-of-experts model:** faster per token in principle, but doesn't fit safely on
  the 24 GB Mac.
- **`qwen3:14b`** (9.3 GB, 2025): older and larger than Gemma, so unlikely to be faster.
- **Smaller Qwen3.5 (4B):** `tev1:4b`, a Qwen3.5 4B fine-tune, lost to Gemma by 15.9 points
  (OD-473).

## Consequences

- An adoption would change the pinned local model: `ollama.lock`, `ecf models install`, the
  shipped tag and probably every address's review counts, as with any pin change (SPEC §7.5),
  and the prompts, which were tuned on Gemma.
- The model filter now has two exceptions; CLAUDE.md states both.

## Outcome (2026-10-12)

Qwen3.5 9B can't be adopted: on the frozen set it had 1 unsafe case (`bug-outage-urgent`, an
urgent outage rated high, so not escalated) and was 1.9 points behind Gemma (176/268 against
181/268; Newcombe −7.7 to +4.0, not non-inferior), with more genuine mail on fraud routes (15
against 6). It wasn't faster either: classifier median 7.8 s against 5.7 s, because llama-server
resumes its cached prompt only about halfway (770 tokens re-read against Gemma's 109) and it
writes fewer tokens per second; its actor calls were quicker (6.9 s against 9.9 s). Both runs on
the same code, service setup and power, back to back (runs `41c2a2be`, `445c9075`; SPEC §7.5).
Gemma stays the local model.
