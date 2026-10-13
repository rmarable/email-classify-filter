# v3.0.0 step 1c, stage S3: readers switch (build plan)

- **Draft 2, 2026-10-13: draft 1 + Phase R P1-P20, operator accepted all**
  (`state-archive/v3.0.0/s3-review-findings.md`). Operator 2026-10-13: wording (§12) approved;
  §11 decided as recommended. Approved for build.
- **Base:** `main` at `5a46634` (S2 merged, `845af58`). File:line refers to `src/` there unless
  stated. "Unverified" = not checked in code or docs.
- **Scope:** schema doc draft 3 §15, S3 bullet; step 1c Phase R P1, P4, P5, P13, P14 honoured.
- **Two branches** (§11): **S3a** readers switch with no schema change, never released alone
  (P3); **S3b** the four values go, new wording, migration 0037, relabelled cards.

## 1. Current state: who reads the overlapping values

| Where | Reads | Does |
|---|---|---|
| `ecf_server/data/starter_rules.yaml:9` | category `vendor_change_request` | fraud_guard clause |
| `starter_rules.yaml:11,46` | sender_type `team` | fraud_guard (external + money), fraud_weak (external) |
| `starter_rules.yaml:37` | category `regulatory` | regulatory rule (or the regulator trigger) |
| `starter_rules.yaml:49` | sender_type `person` | fraud_weak (OD-476) |
| `starter_rules.yaml:111` | category `spam_or_phishing` | junk rule (12th of 16) |
| `starter_rules.yaml:122` | category `notification` + sender_type `automated` | automated_notification (default, stays) |
| `policy.py:108` | `schema.fields["category"]` | `labels()`; KeyError without category |
| `policy.py:133` | category `regulatory` | hide blocker "regulatory" (also `_alert_echo`, `policy.py:211`) |
| `policy.py:147-149` | category | `corroborated()`: confirmed sender category == this category |
| `alert_items.py:132,138` | `vendor_change_request`, `regulatory` | fraud / regulatory alert email |
| `claude_queue.py:165` | `spam_or_phishing` | `batch_risky` (hides need approval) |
| `review.py:82` | `regulatory`, `vendor_change_request` | `needs_own_review` |
| `review.py:60` | `FIXABLE` (5 fields) | Fix form |
| `review.py:218` | category | review line |
| `review.py:264` | `category_ok` | the only accuracy recorded |
| `review.py:278-288` | category/sender_type in a Fix | `_with_items` derives the items (S2) |
| `gate.py:52,116` | `category_ok` | go-live accuracy 85/90% |
| `fallback.py:203` | category | local fallback's shadow accuracy |
| `senders.py:100,169`, `api.py:1448` | `schema.fields["category"]` | sender confirm; KeyError without category |
| `digest_actions.py:167` | category | "Confirm sender" hint |
| `decide.py:70-76`, `facts.py:159` | `senders.confirmed_category` | corroboration; "seen before" (non-null only) |
| `evalrun.py:330-338` | `FIXED_FROM_V2` category sources | `injection_hit` target mapping |
| `evalrun.py:375` | category label | `correct` = category + rule + safety |
| `ecf/eval/results.py:196,603` | category | Holm endpoint; confusion matrix |
| `ruletest.py:119,229` | raw card labels | `ecf rules test` stand-in classification; no hand-set or derived items |
| `ecf/eval/corpus_labels.py:55-73,92` | values against `load_schema()` | corpus rows refused if a value is gone |
| `ecf/cli_card_review.py:194` | `load_schema()` values | card review choices |
| `ecf/schema.py:62-66,297-307` | `SAFETY_VALUES` | loader refuses a schema without them |
| `ecf/schema.py:394-399` | `EXT_HEADING` names the three values | extension prompt text (extensions off, `schema.py:375`) |
| `ecf/schema.py:72` | `V1_VALUE_ALIASES` staff→team | v1 rules and labels |
| `rules.py:403-406` | `FIXED_ITEMS` | refuses conditions on fixed items "until S3" |
| `rules.py:351-352` | last rule | must be a catch-all, user-removable (P1 of step 1c) |
| `config.py:207-222` | applied rules | replace the starter set whole; on a compile failure all fall back to starter + System Error (`config.py:174-189`) |

Unaffected: `escalations.py:114`, `corpus.py:884`, `gate.py:53` (rule ids); `actor.py:94-96`
(fixed workflow fields, P4 of step 1c); `action_alert` has no reader.

## 2. Values removed, the resulting schema, per preset

**Removed:** category `vendor_change_request` (→ `bank_detail_change`), `regulatory` (→
`regulator_claim`), `spam_or_phishing` (→ `phishing`); sender_type `team` (→
`claims_internal`). **Kept:** sender_type `regulator`, `person`, `friend`, `family`; the fixed
rules read them too, as raise-only sources (P2). Wording: §12.

**Version:** stays `version: 3` (v3 never shipped; `release.json classifier_schema: 3`). The
released v3 text is S3b's; SPEC §7.1 says so (S2's text never shipped, P9). The schema digest
and C's prompt digest change, which is what keys gates.

**Per preset:**
- **C:** asked the new v3; gate key (`claude_pins.py:138-140`) changes; C gates restart (they
  already do with S2; both ship in v3.0.0).
- **A, B (local classifier):** still asked v2 (P6 of step 1c), so prompt, `num_predict`, prompt
  digest and `local_key` don't change. Its answer is validated against v3 after `with_fixed`
  (`classifier.py:127`, `systemone.py:182`), which would fail on a removed value, so a fixed
  boundary map **`from_v2()`**: derive the items (`with_fixed`), then category removed value →
  `other`, sender_type `team`/`staff` → `unknown` (the values `evalrun.null_classification`
  already treats as least informative, `evalrun.py:545-556`). Used by the classifier,
  `systemone`, `claude_eval.py:306`, `rescore`, migration 0037.
- **A/B behaviour still changes through the rules** (rules aren't in the gate key): with the
  P1/P2 clauses the fixed rules fire exactly as before on A's v2 values, plus raise-only items.
  Upgrading to v3 lists every address as affected (SPEC §21.2, S2 open item 1).

## 3. Readers' changes

**Bridge helper (S3a, P3):** `ecf.schema.fixed_item(cls, name) -> bool`: the item is true, or
a source value from `FIXED_FROM_V2` holds. Every Python reader below goes through it. S3b deletes
its removed-value half; the kept-source half (sender_type `regulator`, `person`/`friend`/`family`)
stays so Python readers agree with the fixed rules (P2). `review._with_items` stays until S3b
(P3).

| Where | Change |
|---|---|
| `policy.labels` (`policy.py:108`) | `schema.fields.get("category")`; no category → no category labels |
| `policy.hide_blockers`, `_mode` | §5 |
| `policy.corroborated` | unchanged; an absent category never corroborates (fail closed) |
| `alert_items.py:132,138` | `fixed_item(bank_detail_change)`; `fixed_item(regulator_claim)`. `phishing` adds no alert email (parity) |
| `claude_queue.py:165` | `fixed_item(phishing)` only; plain spam no longer makes a batch risky (P14; recorded in the OD) |
| `review.py:82` | `regulator_claim`, `bank_detail_change`, `phishing`, `claims_internal` (all through the helper; raise risk only) |
| `review.py:60` FIXABLE | category, priority, sender_type, `fraud_risk`, `payment_related`, the five items, `requires_action`, `requires_reply`, `deadline_mentioned` (13 selects) |
| `review._with_items` | removed in S3b (the items are on the form); 0036 covers old Fixes |
| `review.py:218` | line adds the true items ("phishing, bank change") |
| `gate.py:116`, `review.reviewed`, `fallback.py:203` | §5a (P4) |
| `senders.py:100,169`, `api.py:1448` | `.get("category")`; with no category, confirm refuses with a message (the "confirmed without category" design is S4b). Rows holding removed values stay: "seen before" (`facts.py:159`), never corroborate a hide, valid after a rollback (`downgrade.py:31` merges senders). Count unverified |
| `digest_actions.py:167` | a category that isn't a current value: say so instead of suggesting a refused command |
| `ruletest.py:119,229` | stand-in = card labels + hand-set + derived items, through the label map (§6) |
| `corpus_labels.load` / `check_values` | removed values read as unlabelled (as v1 aliases are); new labels can't use them |
| `cli_card_review` | a removed label shows as "removed in v3" |
| `schema.SAFETY_VALUES` | removed (S3b); `check_safety_core` keeps `SAFETY_FIELDS` and refuses any `REMOVED_VALUES` in category/sender_type |
| `schema._clashes` | reserves the removed values (an extension can't re-add them) |
| `EXT_HEADING` | drop the three names |
| `rules.py:403-406` | lifted (S3a): fixed and user rules may read the items; policy blocks hides |
| `evalrun.injection_hit` | unchanged (`TARGET_ITEMS` from `FIXED_FROM_V2`) |

## 4. Fixed rules

New `ecf_server/data/fixed_rules.yaml`, ecf-owned; `starter_rules.yaml` keeps the 11 default
rules (bug_report ... requires_reply, otherwise). S3b text:

```yaml
version: 1
rules:
  - id: fraud_guard
    when:
      or:
        - {field: fraud_risk, in: [medium, high]}
        - {field: bank_detail_change, eq: true}
        - and:
            - {field: claims_internal, eq: true}
            - {fact: sender_origin, eq: external}
            - or: [{field: payment_related, eq: true}, {fact: payment_keyword, eq: true}]
        - and: [{field: payment_related, eq: true}, {fact: auth_result, eq: fail}]
        - and:
            - {fact: impersonates_internal, eq: true}
            - or:
                - {field: payment_related, eq: true}
                - {fact: payment_keyword, eq: true}
                - {fact: gift_card_keyword, eq: true}
        - and:
            - {fact: bec_opener, eq: true}
            - {fact: sender_origin, eq: external}
            - not: {fact: self_sent, eq: true}
        - and:
            - {fact: tax_form_request, eq: true}
            - {fact: sender_origin, eq: external}
            - not: {fact: self_sent, eq: true}
        - {trigger: fraud}
    then: [{label: suspicious}, flag, escalate]
    actor: none
    hide: never

  - id: regulatory                       # P1: the item, its kept source, or the trigger
    when:
      or:
        - {field: regulator_claim, eq: true}
        - {field: sender_type, eq: regulator}
        - {trigger: regulator}
    then: [{label: regulatory}, flag, escalate]
    actor: none
    hide: never

  - id: fraud_weak
    when:
      or:
        - {trigger: fraud_weak}
        - and: [{field: claims_internal, eq: true}, {fact: sender_origin, eq: external}]
        - {fact: impersonates_internal, eq: true}
        - and:                           # P2 + step 1c P5: private individual + external + money
            - or:
                - {field: private_individual, eq: true}
                - {field: sender_type, in: [person, friend, family]}
            - {fact: sender_origin, eq: external}
            - or: [{field: payment_related, eq: true}, {fact: payment_keyword, eq: true}]
    then: [{label: suspicious}, flag]
    actor: none
    hide: never

  - id: unverified_payment_sender
    when:
      and:
        - {field: payment_related, eq: true}
        - {fact: auth_result, eq: none}
        - not: {fact: sender_verified, eq: true}
        - not: {fact: self_sent, eq: true}
    then: [{label: unverified_sender}, flag]
    actor: none
    hide: never

  - id: spam_or_phishing                 # id and label kept in S3 (P16)
    when: {field: phishing, eq: true}
    then: [{label: spam_or_phishing}, junk]
```

- **S3a only** (P3): each item clause is an `or` with its removed source value too
  (`category: vendor_change_request`, `category: regulatory`, `category: spam_or_phishing`,
  `sender_type: team`), so split answers lose nothing while C still answers the old values.
  S3b deletes those clauses.
- **Order:** these five, then the applied rules (or the default rules), then ecf's catch-all.
  The junk rule moves from 12th to 5th: without the category value a phishing email answered
  `account_security` would otherwise match the default rule first.
- **Composition** (`rules.compose`): `config.current_rules`, `load_starter_rules`, `ecf rules
  test`, eval and rescore all compile fixed + applied-or-default + tail through one function,
  with the same compiler. `starter_digest()` hashes both files.
- **Caching (P8):** rules are re-parsed per item today (7.3 ms, Phase R F1); compose would double
  it (gate compute ~1.5 → 3 s). Cache the compiled fixed file per schema digest and
  `current_rules` on (schema digest, canonical stored rules JSON).
- **Refusal:** `compile_rules` for a user file refuses a rule whose id is a fixed id ("rule
  fraud_guard is one of ecf's fixed rules, run first; remove it from your file"), and a
  condition on a removed value with a pointer (`category: regulatory` → "use `regulator_claim:
  true`").
- **Catch-all:** a user file no longer must end with one (`rules.py:351`). If its last rule has
  no `when`, nothing is appended; else ecf appends `otherwise` (no actions, `actor: continue`,
  as today). The id `otherwise` is refused anywhere but a last, `when`-less rule. `evaluate`
  (`rules.py:249`) can't raise for an accepted file.
- **Stored applied rules at upgrade (P7):** every applied set copied from the starter set holds
  the fixed ids. A one-time conversion strips each fixed-id rule **identical** to the shipped
  v2 starter text and keeps the rest; that text is the same in v2.0.0-v2.2.3 and `main`
  (`starter_rules.yaml` SHA-256 `432aec87…` at every v2 tag, checked 2026-10-13; v1.0.0's
  differs, `staff`). A **customised** fixed rule is refused: the conversion leaves the stored
  rules as they are and the existing fallback applies (starter rules + System Error naming the
  rule and the fix). Shown in the pre-upgrade report (the new version's check, merged by
  `ecf/upgrade_run.py:193-205`; whether it runs before the swap is unverified, else in `ecf
  doctor` and the System Error). Runs once at service start after migration, audited
  (`config.rules_converted`), idempotent; the same conversion at bundle import. SPEC §8.6 and
  `docs/admin-guide.md` lose "the upgrade never rewrites applied rules".

## 5. Policy

`hide_blockers` gains (step 1c P1), each through `fixed_item`:
- `bank_detail_change` → "bank detail change"
- `regulator_claim` (or sender_type `regulator`) → "regulatory" (replaces `policy.py:133`;
  same string, so `_alert_echo` keeps working)
- `claims_internal` and `sender_origin` external → "claims to be a colleague, from outside"
- `phishing` → "phishing": blocks `mark_read`, `archive`, `move`; **not `junk`**, so the fixed
  rule can still junk. Needs the action name in `_hide_refusal` (`policy.py:286`).
- **P6:** any hide action while `phishing` is true needs approval (`_mode`, `policy.py:298-305`,
  beside `batch_risky`): a compromised, confirmed sender's phishing would otherwise be
  auto-junked. Recorded in the OD.

Applies to rule actions and actor proposals alike (`policy.proposal`, `policy.py:232-234`).
`fraud_signal` unchanged (no new high-risk routing or send refusals in S3). I2-I4 unchanged.
"Unsure blocks hiding" (Jev's middle band) is step 3.

**Hypothesis invariants** (`tests/test_policy.py`, strategies extended with the five items and
the kept sender_type values):
1. `bank_detail_change`, `regulator_claim`, sender_type `regulator`, or `claims_internal` +
   external: no hide survives, for the starter set, a hand-written custom set and any proposal.
2. `phishing`: no `mark_read`/`archive`/`move` survives; `junk` only when corroborated, no other
   blocker, and in mode `approve`.
3. For every composed set and classification: if a fixed rule's `when` is true, the decision is
   the first such fixed rule.
4. Every composed set ends in a catch-all; `evaluate` never raises.
5. A classification missing a fixed item behaves as with it false (step 1c P13; P14's unknown is
   for user and absent default fields, S4).
6. S3a: for any classification, the composed rules' decision equals the old starter rules'
   decision whenever items and old values agree (`with_fixed` of the answer).
7. Existing I1-I4.

### 5a. Go-live gate (P4)

- **Gated accuracy:** reviewed items with no Fix (verdict `correct`) over reviewed, 85% / 90%,
  until S4 gives user-field data. Fixed-field accuracy (the 10 fixed fields, from
  `classification` vs `human_correction`) and category accuracy are reported, not gated. Same
  for `fallback.py:203`. No real review data to measure the change on (accept 0 items, ecf1009 1;
  Phase R).
- **Old passes don't carry over:** migration 0037 clears `gate.passed_at` (counts kept) and
  `GATE_VERSION = 2` joins `gate.inputs` (`gate.py:146-169`), so a stored pass under an
  unchanged A/B key isn't reused. A rollback merges `gate` from live (`downgrade.py:31`): v2
  then also has no pass; addresses are paused and in assist after a rollback anyway.

## 6. Data

- **Migration 0037** (P9): separate from 0036 (applied on the accept database; migrations are
  tracked by number only, `db.py:117`). Filtered UPDATEs on values only, never on
  `schema_version` (S2-era rows are already at 3): `items.classification`,
  `items.human_correction`, `fallback_shadow.classification`: category in the three → `other`;
  sender_type `team`/`staff` → `unknown`. Plus `gate.passed_at` (§5a). Items were set by 0036,
  which runs first. Add 37 to `db.CHECKPOINT_AFTER` (`db.py:109`). Tests: on a database with
  0036 applied and v3 rows holding removed values; time at 100,000 items, as 0036's.
- **Rollback:** snapshot; `downgrade._merge` copies only `sent`, `threads`, `senders`, `gate`
  (`downgrade.py:31`): no mapped classification reaches v2. Tested on ecf1009 by the checked
  release path (OD-492; v3.0.0 is checked already), now including 0037 and the P7 conversion.
- **Export:** `DATA_FORMAT` stays 4. Import migrates the staged database to the bundle's version,
  then runs the normal migrations (`importer.py:7-9,142`), so older bundles get 0037; applied
  rules in a bundle get the P7 conversion.
- **Eval labels (P11):** before S3b's runs the operator relabels `category` on the 91 cards
  labelled with a removed value (59 `spam_or_phishing`, 23 `vendor_change_request`, 9
  `regulatory`) and `sender_type` on the 17 `team` cards, on a phone card review page (claude.ai
  artifact, db answers read back; precedent: the phishing page), against the S3b wording. Import
  by generalising `ecf eval label --import-fixed` to these two fields (card front matter and
  `reviews.jsonl` updated, the operator confirms; set version changes once). Card text and
  `.eml` hashes unchanged.
- **Label map** stays for anything not relabelled (corpus labels, old results): a removed value
  is unlabelled, never `other`. Results record `label_map: 2`; `ecf eval compare` refuses mixed
  maps whether or not `--subset` is given (P10).
- **Trigger-less regulatory cards (P1):** Phase R found the regulator trigger fires on 10 of the
  11 cards expecting `regulatory` (I didn't re-check this), so the gate can't see a weak
  `regulator_claim`. Add 4 cards with no trigger words (written per `.claude/rules/eval-synthetic.md`,
  two-agent review of the list, operator labels blind), merged with the relabel so the set
  version changes once.

## 7. Eval

**S3a criterion (P13)**, no model run: `ecf eval rescore --synthetic --through-policy` of B0
(`e11c92a3`, `845303ba`) and S2 (`6846d041`, `d157a948`) under S3a code. Pass: 0 unsafe and
fraud-guard recall 104/104 in all four, and every rule change attributed to an expected cause
(the junk move, the `regulator`/`friend`/`family` clauses, the new hide blockers or P6); any
other change is a bug. B0's answers are v2, so its rescore tests the derivation path preset A
uses (P19). Preset A itself: no Gemma result exists on the 300-card set (accept `evals/`,
checked 2026-10-13); rescore `d80ea43d` (`~/Library/Application
Support/ecf/accept/evals/d80ea43d…json`, Gemma, 268 cards, 2026-10-11), the 32 newer cards left
out. No Ollama run.

**S3b criteria (P5, P1)**, named before the runs: two pinned C runs (as S2) against S2's two
runs rescored once under S3b code with the redefined `correct` (P10). All must hold:
1. Both runs: 0 unsafe, fraud-guard recall 104/104, gate passed.
2. Per item, recall pooled over the two runs vs S2 pooled, Newcombe: `phishing` lower bound
   > -5 points. `bank_detail_change`, `private_individual` and the report-only items
   (`regulator_claim`, `claims_internal`, under 20 positives): recall **and** precision reported
   (P19), no criterion.
3. Rule right: Newcombe lower bound > -2.0 points in each of the four cross pairings.
4. Every trigger-less regulatory card reaches `regulatory` in both runs (P1).
5. Listed for the operator regardless of p: every rule loss on a card expecting `fraud_guard`,
   `regulatory` or `fraud_weak`; every case whose `rule_alone` (`evalrun.py:962`) isn't the
   expected fraud or regulatory rule; removed-value labels that fall to `otherwise` in rescore,
   corpus and `test_corpus_expected` STRICTER (P15). `safety` reported (McNemar vacuous at 0/0).

**`correct` (P10):** redefined in S3b as rule + safety + every labelled fixed item, applied in
the one rescore of S2 and in the S3b runs. Reports state category's labelled n and the label
map's effect; SPEC marks category figures from before and after S3b as not comparable.

**Diff review:** two read-only agents (safety and correctness, performance) on each branch
before its measurement.

**Cost (P20):** the S3b wording shortens C's `schema_text` from 6,555 to 5,923 bytes (-632,
about 160 tokens per classify read; measured 2026-10-13 by compiling the §12 text with
`ecf.schema.compile_schema` in the scratchpad). Recorded in SPEC as F6 was.

## 8. Docs

- **SPEC:** §7.1 (removed values, wording, `from_v2`, safety core without `SAFETY_VALUES`; the
  released v3 text is S3b's), §7.4b, §8.3 (hide blockers, junk exemption, P6 approval), §8.6
  (fixed vs default rules, P1/P2 clauses, refusal, appended catch-all, P7 conversion replacing
  "never rewrites"), §9.3 (gated accuracy, `GATE_VERSION`), §10.4 (schema text cost, P20),
  §11.9 (0037), §11.10 (upgrade: conversion, report), §16.1 (relabel, label map, new cards),
  §16.4/16.5 (`correct`, `label_map`, category non-comparable), §21.2 (S3a and S3b results), OD
  table.
- **OD-500** (next free after OD-499; check at build) for S3's decisions, including P6 and P14.
- **One ADR 0030 (P17):** the fixed layer: ecf's fixed items and fixed rules own safety, run
  first, can't be redefined; amended by S4 for the user layer. ADR 0024 (its safety core) and
  ADR 0025 (extension model) marked superseded by it.
- **CHANGELOG:** rules read the fixed items; four values gone; phishing can't be archived and
  any hide on it needs approval; Fix form gains the fixed fields; gate accuracy = reviews with no
  Fix; applied rules' starter copies converted at upgrade; `spam_or_phishing` label kept for
  now, rename considered in S4 (P16).
- **docs/admin-guide.md** (schema text :72, live gate :185, rules, the v2 upgrade section
  :321-347 that says applied rules are never rewritten; P18), **docs/operator-guide.md** (Fix
  form :165, gate :172, sender confirm :177-183), ADR 0025 status; code docstrings
  (`schema.py:1-25`, `rules.py:1-13`, `policy.py:7-16`). Plugin `classifier.md` names no
  removed value (checked).

## 9. Tests

- New: `test_fixed_rules.py` (composition, order, refusal, catch-all, removed-value hints, P7
  conversion: identical stripped, customised refused, idempotent, import); Hypothesis
  invariants (§5); `fixed_item` helper both halves; 0037 (values-only filter, Fixes, shadow,
  gate passes, with 0036 applied); `from_v2` in classifier, systemone, claude_eval, rescore;
  rules cache keyed correctly (schema or stored rules change invalidates); gate accuracy and
  `GATE_VERSION`; P6 approval mode; Fix form with 13 fields; sender confirm without category;
  corpus labels with removed values; `ruletest` reaches fraud_guard via `bank_detail_change`;
  label import for category/sender_type; `compare` refuses mixed `label_map` with and without
  `--subset`.
- Changed: ~28 test files use the removed values (grep 2026-10-13: `test_policy`,
  `test_rules`, `test_gate`, `test_review`, `test_evalrun`, `test_claude_eval`, `test_schema`,
  `test_schema_v3`, `test_fallback`, `test_systemone`, `test_corpus_labels`, ...); fixtures move
  to item-based classifications in S3b.

## 10. Size and sub-staging

- **S3a:** fixed rules file (with the bridge clauses), compose, cache, refusal, catch-all, P7
  conversion, rules may read items, `fixed_item` helper, hide_blockers and P6, alert_items,
  claude_queue, review (form, own review, line), gate (§5a, except 0037), fallback,
  labels/senders tolerance, ruletest. ~12 source files, ~500 lines; ~15 test files. Measured by
  §7 S3a criterion.
- **Between:** the relabel page and the 4 regulatory cards (operator time; no code beyond the
  import).
- **S3b:** wording (§12), removed values, `SAFETY_VALUES`, `REMOVED_VALUES` checks, `from_v2`,
  0037, bridge clauses removed, `_with_items` removed, label map, `correct`, `label_map`, docs,
  ADR. ~14 source files, ~350 lines plus ~28 test files' fixtures. Measured by §7 S3b criteria
  (operator go-ahead per run).
- Each: two-agent diff review, macOS gate with 0 skipped, CHANGELOG line.

## 11. Decisions (operator 2026-10-13: all as recommended)

1. **The helper's kept-source half in S3b.** P3 says the helper is deleted in S3b; P2 makes the
   fixed rules read sender_type `regulator`/`person`/`friend`/`family`. Recommend: delete only
   the removed-value half, keep the kept-source half so alerts, own review and hide blockers
   agree with the rules.
2. **Number of trigger-less regulatory cards.** Recommend 4 (genuine agency notice, claimed
   agency from a free-mail-style domain, court notice, tax authority letter, none with trigger
   words). Alternative: report the 1 existing card only and leave criterion 4 report-only.
3. **For S4, decided now or then:** the fixed rules read sender_type, which S4 makes
   replaceable. Recommend: in S4 a fixed clause on a default field or value the user removed is
   dropped (it only raises risk), and `ecf config apply` says so.

## 12. Wording (approved by the operator 2026-10-13)

Every changed text, old → new. Each description stands alone: no field or value names (P12;
Jev sees descriptions only). Fields and values not listed are unchanged.

**`category` description**
- Old: "What this email is primarily about. Pick the first that fits: vendor_change_request,
  regulatory, spam_or_phishing, account_security; then what the email is about (money: invoice,
  then payment_confirmation or remittance, then finance); notification, private and other last."
- New: "What this email is primarily about. A fake email gets the value of what it pretends to
  be (a fake invoice is an invoice). When several fit, choose in this order: a sign-in or
  security alert; money (a bill to pay, then a payment made or received, then an account or
  policy we hold); then what the email is about; a plain notice, a personal note or none of
  these last."

**`category` values removed:** `vendor_change_request`, `regulatory`, `spam_or_phishing` (texts
as in SPEC §7.1, deleted).

**`category: account_security`**
- Old: "Sign-in alert, password reset, verification code, or security warning from a service we
  use, that asks for nothing beyond signing in as usual. One that asks you to send a code or
  password, click to verify, or call a number is spam_or_phishing."
- New: "Sign-in alert, password reset, verification code, or security warning about an
  account, genuine or fake."

**`category: marketing`** (P11)
- Old: "Sent to many: newsletters, promotions, advertising."
- New: "Sent to many: newsletters, promotions, advertising, and other unsolicited bulk mail."

**`sender_type` values removed:** `team` ("Colleague or co-worker acting on behalf of the
organization.").

**`sender_type: unknown`**
- Old: "Can't tell the role (an impostor takes the role they claim)."
- New: "Can't tell the role, or a colleague or co-worker of ours, genuine or not. Otherwise an
  impostor takes the role they claim."

Checked unchanged for P12 (no cross-reference): `sender_type` description, the other values,
`priority`, the workflow fields, `fraud_risk`, `payment_related` and the five fixed items.
