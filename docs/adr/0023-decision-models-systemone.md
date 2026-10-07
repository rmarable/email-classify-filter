# ADR 0023: decision models via Ollama `/v1/systemone` (Qwen exception)

- **Status:** accepted (2026-10-07; OD-470, OD-471); experiment designed, not built
- **Context source:** SPEC §7.8, §16.3-16.5, §21.1, §21.2, §1.5 item 8;
  `planning-docs/SYSTEMONE-MODEL-TESTING-PLAN.md` (draft 3, after review round R, findings R1-R30)

## Context

Preset A classifies with Gemma 4 12B. Its weakest field is category (77.0% in the v1.0.0 run
`e04fac92`), and it gives no usable confidence (OD-258). Ollama 0.35 added `/v1/systemone`, which
serves "decision models": a model answers typed questions about a `state` and returns a probability
for every option. The operator wants to know whether one classifies better than Gemma, and made the
recorded answer a `v1.0.0` release criterion (§1.5 item 8).

Every decision model Ollama listed on 2026-10-06 is a Qwen3.5 fine-tune, and the project's model
filter excludes PRC-affiliated labs (CLAUDE.md; ADR 0005). Without an exception there is nothing to
test.

## Decision

- **Qwen exception** (OD-470): Qwen-based decision models are allowed only in the local classifier
  role, only through Ollama on 127.0.0.1, pinned by manifest digest, and only for evaluation until
  the decision is recorded. Never the actor, never a Claude role. The filter otherwise stands; ADR
  0005 is not edited.
- **The experiment** (OD-471): `tev1:4b` against Gemma as the local classifier, pipeline against
  pipeline (rules, policy, fraud guard and the Gemma actor unchanged). Safety is gated on the
  synthetic set; correctness is compared on the blind-labelled real-mail corpus (ADR 0022). The
  adoption rule, statistics and arms are in SPEC §7.8 and §16.5.
- **Co-residence:** both models stay loaded. That needs llama-server's prompt cache capped at
  1,024 MiB per runner (`LLAMA_ARG_CACHE_RAM=1024` on `ollama serve`); uncapped, swap grew 16 GB in
  10 minutes (§21.2). The experiment's runs use a capped server; the login item changes only if the
  model is adopted.
- **Release:** `v1.0.0` waits for the recorded decision, not for an adoption. Any adoption ships
  after `v1.0.0`.

## Alternatives considered

- **No exception (keep the filter absolute):** rejected by the operator; it would end the
  experiment before it started.
- **Serve Gemma through `/v1/systemone`:** not possible; only models trained for System One work.
- **nimble 9B:** doesn't fit beside Gemma on the 24 GB Mac.
- **Confidence-based routing on the probabilities:** out of scope (OD-054 wording); the
  probabilities are recorded for calibration only.
- **A 2 GB cache cap:** rejected (operator decision 2026-10-07); it would take free memory near the
  point where swap grows, for no gain in this experiment.

## Consequences

- A second local model and a cache cap are possible later, at a cost listed in the plan's Phase 4:
  preset A's pin key becomes a pin-set hash (review counts restart, `live` drops to `assist`),
  install, upgrade and doctor handle two pins, and Gemma's prompt cache is capped in production too.
- The model's weights licence is only what Ollama's package carries (Apache-2.0, MIT); Together's
  model card states none. Recorded as open in §21.2.
- The decision-model defense against prompt injection is weaker than Gemma's: the API has no
  instruction field, so the "email is data" text sits in `state` beside the email.
