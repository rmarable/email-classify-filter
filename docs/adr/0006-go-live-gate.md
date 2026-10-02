# ADR 0006: The go-live gate is bound to the pinned model

- **Status:** accepted (OD-069, 2026-09-26; OD-159, 2026-09-27; OD-234, 2026-09-30; OD-260,
  OD-261, OD-263, 2026-10-01); implemented in V1.3 (steps 6b and 12b)
- **Context source:** SPEC §9.1, §9.3, §16.2, §16.5; `ecf_server/gate.py`, `stages.py`,
  `evalrun.py`

## Context

An address moves through three stages (§9.1): `shadow` (ecf decides and posts, changes nothing),
`assist` (only label, flag, escalate and leave run; everything else is held) and `live` (the full
policy, including hiding mail and proposing sends). Going live trusts the
model's output more, so it needs evidence about *that* model on *this* mailbox, and evidence that
can't be gamed by a lucky run, a stale result or an override.

## Decision

`ecf stage set <address> live` passes only when the service, never a client, finds for the pinned
Ollama digest (§9.3):

- **Waivable** (by `--override --reason`, step-up, posted): at least 100 reviewed emails on a
  `standard` address (200 on `high`) and category accuracy of at least 85% (90%), with the Wilson
  95% lower bound shown.
- **Never waivable** (OD-234): no fraud-guard miss among your reviews (OD-069); no unsafe
  proposal on payment or fraud mail, counted on what the model proposed whether or not policy
  refused it (OD-260); and a synthetic-set result with 0 unsafe cases that is the latest run for
  this digest, on the current version of the set (OD-261), **ran to its last case, with both the
  classifier and the actor** (OD-263). A run that was stopped, hit the runtime cap, or skipped a
  model is saved for its figures and never passes.
- **Bound to the digest:** reviews and synthetic results count only for the digest they were made
  with. When the pinned digest changes, a `live` address moves back to `assist` (audited, posted)
  and review sampling returns to every email until the new digest's count is reached.
- An override records the change and its reason; the gate is recorded as passed only when it was
  met.

## Alternatives considered

- **Gates bound to model aliases only:** rejected in the design plan (appendix "Runtimes and
  models"); an alias can point at a different model later.
- **An override that waives everything:** rejected (OD-234); the safety checks measure harm, not
  confidence.
- **Counting only proposals policy let through as unsafe:** rejected (OD-260); that measures the
  safety net, not the model.
- **Any saved eval run counting:** replaced by OD-261 (latest run, current set) and OD-263
  (complete, both models) after the V1.3 review found a stopped run could pass.

## Consequences

- A model change costs a new synthetic run (`ecf eval run --fraud-only` is the shorter one) and
  new reviews before an address can be live again.
- The set's version is a hash of `labels.jsonl`, so any card change or confirmation also needs a
  new run.
- `v1.0.0` needs these safety gates passed for preset A (OD-159, SPEC §1.5).
