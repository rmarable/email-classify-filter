# ADR 0029: Qwen and Qwen-based models are banned in every role

- **Status:** accepted (operator decision 2026-10-12, OD-494); revokes the Qwen exceptions of
  ADR 0023 (OD-470) and ADR 0027 (OD-491)
- **Context source:** CLAUDE.md (model filter); ADR 0005, ADR 0023, ADR 0027; SPEC §7.5, §7.8

## Context

The model filter excludes PRC-affiliated and Meta/X-affiliated labs (ADR 0005). Two exceptions
let Qwen-based models be evaluated: `tev1:4b` as a local decision model (OD-470; not adopted,
OD-473) and Qwen3.5 9B in place of Gemma (OD-491; failed the safety gate and non-inferiority,
ADR 0027 Outcome). Neither beat Gemma.

## Decision

- Qwen and Qwen-based models (fine-tunes and derivatives) are banned in every role, including
  evaluation. OD-470 and OD-491 are revoked.
- A model whose base is undisclosed (e.g. a hosted model) is checked before use; an undisclosed
  answer is raised with the operator.
- Local decision models that aren't Qwen-based stay on the to-look-at list (SPEC §1.2): laya /
  Julia-1, Ollaya (lineage unverified), a fine-tuned local decision model on a non-Qwen base.

## Consequences

- The tev1 entry in `data/decision_models.lock` and Qwen tags are removed when the code is next
  changed (v3.0.0 work, not assigned). `ecf_server/systemone.py` and the Ollama `/v1/systemone`
  transport stay: they serve any System One model, including Jev (ADR 0028).
- ADR 0023 and 0027, OD-470/491 and their results stay as history, marked revoked. The archived
  Qwen result `41c2a2be` is read-only; no Qwen model is run again.
