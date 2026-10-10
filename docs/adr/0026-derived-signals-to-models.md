# ADR 0026: derived signals may go to a model, by exemption

- **Status:** accepted (operator decision 2026-10-09, OD-483); amends ADR 0009 ("facts never go
  to or come from a model"); the first exemption, OD-484, is built in `v2.1.0` (classifier plan
  items 3-5)
- **Context source:** SPEC §3.2, §7.2, §10.4, §12.4; ADR 0009; classifier plan
  (`planning-docs/CLASSIFIER-MODEL-PERFORMANCE-IMPROVEMENTS.md`) §2 and Phase R findings P2, P3,
  P10, P18; runs `a0462682`, `883204b3` and `d880a988` (SPEC §21.2)

## Context

ADR 0009 kept every computed fact out of prompts: facts are the service's evidence, and a model
that sees them could be steered by an attacker who learns which facts matter. The cost shows in
the eval: rules were tuned to conditions the model can't see. `mid-exec-quick-favor` and
`fraud-w2-request` escaped until the service caught them with facts (OD-479, OD-482), and the
model's fraud and sender-type answers on those patterns stayed wrong. Telling the model what the
rules already know (that an outside sender opens like an executive-impersonation scam, or asks
for employees' tax forms) gives it the context a careful reader would have, without letting it
decide anything.

## Decision

- **Default unchanged:** no computed fact is sent to a model.
- **Exemptions by OD,** per signal and per role (local classifier, Claude classifier, actor). Each
  exempted signal is a composed boolean, the truth of a rule-level condition, sent only when true,
  in fixed service wording, built by one shared function with an allow-list test, so no trigger
  reason, address, keyword or sender detail can leak (P3, P18).
- **Never sent:** `auth_result`, `sender_seen_before`, `sender_verified`, `sender_origin`,
  `sender_hash`, `bulk_signal` (attacker-set headers, the only fact pushing toward a hide; P2),
  `reply_to_mismatch` and raw keyword hits.
- **Never taken from a model, without exception:** nothing a model returns becomes a fact, and
  policy's invariants (I1-I4) run after the model as before.
- **Placement** (P10): the signals go in a block before the email, carrying the request's random
  token; the instructions say only a block with this token counts. With no signal true the block
  is left out, so the request is the same as before.

## Alternatives considered

- **Keep ADR 0009 as written:** leaves the model blind to what the rules know; the misses above
  stay model limits.
- **Send raw facts:** more for an attacker to learn and steer, and facts like `bulk_signal` are
  set by the sender; rejected (P2, P3).

## Consequences

- An attacker who knows the signal wording can try to forge a block in the email; the token rule
  and two injection cards (`inj-forged-signals-block`, `inj-signals-block-is-wrong`) test that.
- On presets B and C the derived signals go to Anthropic with the classification request. They
  are not email content and add no destination (SPEC §12.4).
- Signals change the prompt, so the prompt digest and the gate key change and every address
  returns to `assist` until its gate passes again (Q3, Q6).
- Each new signal or role needs its own OD; the allow-list test lists what may be sent.
