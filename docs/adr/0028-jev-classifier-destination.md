# ADR 0028: TypeSafe Jev may receive email content as the classifier

- **Status:** accepted (operator decision 2026-10-12, OD-493); decided, not built; amends the
  privacy rule ("never add another destination", CLAUDE.md; SPEC §12.4) for this one destination
- **Context source:** `planning-docs/v3.0.0-RAW.md` (operator draft, not approved); its Phase R
  review of 2026-10-12 (eight agents, findings P1-P26 in `state-archive/v3.0.0/`, gitignored);
  SPEC §7.8, §12.4; ADR 0005, ADR 0023

## Context

Local models are slow under sustained load on the fanless development Mac: Gemma 4 12B drops from
about 26 to 11-16 tokens/s within minutes (SPEC §21.2), and Ollama MLX doesn't fix it (OD-257
check, 2026-10-12). The operator's long-term plan is to optimise on Jev-style classifier modelling,
then go local again. Jev is a hosted "System One" model: it answers typed questions (`choice`,
`score`, `noul`) about a `state` with a probability per option (docs.typesafe.ai, read 2026-10-12,
(rv)), the same interface `ecf_server/systemone.py` already maps the schema to.

## Decision

- TypeSafe AI, Inc. (api.typesafe.ai) is an allowed destination for email content, in the
  classifier role only. Every other destination rule stands; no payload logging stands.
- Nothing is sent until it is built. Before it ships, SPEC §12.4 states exactly what Jev receives
  and on what terms, and README, SECURITY.md and DISCLAIMER.md change with it.

## Open before building (from the review)

- **Payload:** what goes to Jev (today the Claude classifier gets a capped, redacted excerpt and
  fixed-wording signals, OD-483); a test bounds the request size.
- **Vendor terms (unverified unless marked):** retention period for `state` (not published; zero
  retention is offered to enterprise customers, (rv)); sub-processors; which agreement governs API
  use (the site terms say not to submit confidential information through the site, (rv)); no
  training on customer requests is stated, (rv); US hosting is stated, (rv).
- **Lineage:** Jev's base model is undisclosed; every open System One model seen so far is a
  Qwen3.5 fine-tune. Under OD-494 a written vendor answer is needed before use.
- **Pin:** an exact model ID (e.g. `jev-1.13.0`), never a moving alias; the response's model
  checked per call; part of the gate key.
- **Key:** in the OS secret store, written by the service; never `$TYPESAFE_API_KEY`.
- **Consent:** OD-427 consent for watched third-party Google accounts names Anthropic only.

## Alternatives considered

- **Keep all classification local or on Claude:** preset C already scores 83.2% on the frozen set
  against Gemma's 68.3% (SPEC §21.2); the operator chose to build C as the default and a Jev +
  Claude-actor preset side by side, and to compare Jev against both.

## Consequences

- One new destination; the privacy statement grows a Jev section when it ships.
- Jev is evaluated under the evaluation rules (OD-486) against C and Gemma before any adoption.
