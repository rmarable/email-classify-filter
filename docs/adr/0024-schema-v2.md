# ADR 0024: classification schema v2 for work and personal mail, bound into the gate

- **Status:** accepted (2026-10-08; OD-475, OD-476); built on branch `schema-v2`, ships in `v2.0.0`
- **Context source:** SPEC §7.1, §7.4, §8.6, §9.3, §11.9, §11.10, §12.1;
  `planning-docs/SCHEMA-V2-PLAN.md` (draft 1, after Phase R: findings S1-S16, C1-C15, E1-E15)

## Context

Schema v1 (§7.1) was written for shared business mailboxes. Since V1.6 ecf also watches personal
Gmail accounts, whose mail (bills, receipts, deliveries, sign-in alerts, appointments, family and
school) v1 can only call `invoice`, `notification` or `other`. The operator wants one schema for
both, so an install watching both kinds of address needs nothing special, and wants users to be
able to extend it later (ADR 0025, `v2.1.0`).

Two base profiles (work and personal) were considered first and dropped for one combined schema.
An adversarial review (three reviewers) then showed that two of the first draft's changes would
weaken safety: dropping `fraud_risk`'s `none` level would have let mail the model is unsure about
be hidden automatically and moved Opus routing, the no-send guard and individual review; merging
`action_alert` into `notification` would have made action alerts hideable (OD-472 kept them in the
inbox). The operator kept both as they are.

## Decision

- **Schema v2** (OD-475): six new categories, four widened, `notification` reworded; `staff`
  renamed `team`, and `company`, `friend`, `family`, `person` added; `fraud_risk` and the other
  fields unchanged. A safety core (fields and values policy, the fraud checks and the starter rules
  read) is enforced by the loader.
- **Old data and old rules:** stored classifications are migrated in SQL (0034, every
  `db.migrate` path); rules files and applied rules are never rewritten: the compiler reads v1's
  `staff` as `team` and names the rules that did. Export bundles become data format 3.
- **The gate is bound to the schema:** the schema digest joins every gate key (A, B, C and the
  fallback). A new schema starts every address's gate again and moves live addresses to assist,
  as a new model does (ADR 0006, ADR 0016).
- **Rule 1b covers an unplaced `person`** from outside about money (OD-476), so the new
  personal sender types can't hide an impersonated colleague from the OD-262 clauses.
- **Release:** `v2.0.0`, a release candidate first; semver from here on (major when schema, rules
  or data format break).

## Alternatives considered

- **Work and personal profiles chosen at init:** two schemas, a roles mapping for policy and two
  eval sets; dropped by the operator for one combined schema.
- **Three `fraud_risk` levels** and **one merged notifications value:** rejected after review
  (above).
- **Rewriting stored rules at upgrade:** would change security-relevant config without step-up
  (OD-075) and could refuse to start; replaced by compiler aliases.
- **Leaving the gate keyed by model only:** a live address would stay live on a prompt it was
  never measured on.

## Consequences

- Every address goes back through its gate after the upgrade; the synthetic set needs a new run
  (and the 12 cards relabelled `team` need the operator's confirmation again).
- v1-era eval results can't be compared directly; the comparison maps v1 answers to v2 on the
  common cards (§16.5).
- A `v1.0.0` install can't import a v2 bundle; rollback after settle keeps v1-valid `senders`.
