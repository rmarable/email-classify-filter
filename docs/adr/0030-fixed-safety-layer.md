# ADR 0030: the fixed safety layer: ecf's fixed items and fixed rules own safety

- **Status:** draft (2026-10-13, OD-500); built in v3.0.0 step 1c stage S3 (S3a 2026-10-13; S3b
  2026-10-13, uncommitted). **Amended** (2026-10-14, OD-502) by stage S4 for the user layer
  ("User layer" below; plan `planning-docs/S4-USER-LAYER.md` draft 3; built on branch
  `s4-user-layer`, behind `EXTENSIONS_ENABLED` until its measurement, SPEC §1.5a item 2b,
  passes). Supersedes ADR 0024's safety core (`SAFETY_VALUES`) and ADR 0025 (schema extensions)
  (Phase R P17).
- **Amended by ADR 0031** (2026-10-14, OD-506): presets A and B, which were still asked schema
  v2, are removed; only Claude (and Jev from step 4) is asked v3.
- **Context source:** SPEC §7.1, §7.4b, §8.3, §8.6, §9.2, §9.3, §11.9; plan
  `planning-docs/S3-READERS-SWITCH.md` draft 2 (Phase R P1-P20, operator-accepted 2026-10-13);
  `planning-docs/v3.0.0-OPERATOR-SCHEMA.md` draft 3

## Context

Until v3.0.0 ecf's safety rules read `category` and `sender_type` values
(`vendor_change_request`, `regulatory`, `spam_or_phishing`, `team`), so the safety logic and the
categories a user may want to replace were the same thing (ADR 0024 kept them in a fixed safety
core, `SAFETY_VALUES`; ADR 0025 let an install add fields and values on top). Schema v3 (stage
S2, OD-498) asks five yes/no fixed safety items instead (`bank_detail_change`,
`regulator_claim`, `phishing`, `claims_internal`, `private_individual`). For users to own their
schema (stage S4), safety has to stop depending on any value they could change.

## Decision

- **Fixed items.** ecf owns the five items and the safety fields (`SAFETY_FIELDS`); a shipped
  schema without them, or holding one of the four values they replace, is refused
  (`check_safety_core`), and an extension can't add a removed value back. The four values are
  gone from schema v3 (S3b); `sender_type` `regulator`, `person`, `friend` and `family` stay and
  are read as raise-only sources (`fixed_item`, P2).
- **Fixed rules.** `fraud_guard`, `regulatory`, `fraud_weak`, `unverified_payment_sender` and
  the junk rule (`spam_or_phishing`, id and label kept, P16) live in ecf's `fixed_rules.yaml`,
  read the items, run first in every composed rule set (`rules.compose`) and can't be redefined
  or tested on a removed value by a rules file. Applied rules replace only the default rules.
- **Policy.** The items are hide blockers (phishing blocks every hide but `junk`, and any hide of
  phishing needs approval, P6); they raise risk only.
- **User layer (S4, OD-502; D1-D15, Phase R P1-P31).** On preset C only (D8: refused both ways
  while any A or B address exists, the local model not being measured with them), an install adds
  its own fields and `category` values on top of the 11 fixed fields (`ecf.schema.FIXED_FIELDS`:
  the safety set, `priority` fixed for good (D12), the workflow fields) and the default fields
  (`category`, `sender_type`, only added to; default-off and value replacement are S4b, after
  v3.0.0, D1). A description on every field, value and level; catch-alls (`other`, `unknown`,
  `none`) reusable within user fields; a description writing a fixed field's name as a field name refused, plain English warned (P10,
  narrowed 2026-10-14); default,
  fixed and applied rule ids always reserved (P2). Rules come in three layers (`rules.compose`):
  fixed, then the user's new-id rules, then the defaults with same-id replacements spliced at the
  default's position and `default_rules_off` left out; a file ending in a catch-all replaces the
  defaults (D2, P4, P17); every validator checks the composed set; `current_rules` never raises.
  Any field ecf doesn't fix is unknown when absent or out of spec (P14). The gate counts a
  review accurate when no Fix touched the 13 fixed and default fields (`GATE_VERSION` 3, D4); C's
  pins hold the whole schema digest in S4, narrowed with Jev's key in step 4 (D3).
- **Local models.** Presets A and B are still asked schema v2; `from_v2` derives the items from
  their answer before reading a removed value as `other` or `unknown`, so no fixed-rule decision
  is lost (D3).

## Consequences

- Rules, policy, alerts, review and the gate no longer depend on `category` for safety; a user
  layer (S4) can replace the default fields without touching safety.
- Stored data moves once: migration 0036 (items) and 0037 (removed values read, gate passes
  cleared); applied rules copied from v2's starter rules are converted once (P7).
- The eval reads labels through a label map (`label_map: 2`) and `correct` is the rule, the
  safety checks and the labelled fixed items (P10); figures from before S3b aren't comparable.
- ADR 0024 stays for schema v2 itself; its safety core is superseded here. ADR 0025's extension
  model is superseded: S4 decided the user layer (above).
- S4: the user layer ships enabled in v3.0.0 only if M2 passes (SPEC §1.5a item 2b); otherwise
  `EXTENSIONS_ENABLED` stays False and the operator decides next. A pre-S4 stored rules file
  without a catch-all changes meaning (its rules now run before the defaults), named by the dry
  run and `ecf doctor`; default rule ids are part of the rules-file format (a rename is a major
  version, OD-480). Any schema edit on C demotes live C addresses until a new `/ecf-eval` and
  reviews pass (D3, P15).
