# v3.0.0 step 1c, stage S4: user layer (build plan)

- **Draft 3, 2026-10-14: Phase R P1-P31 applied** (four agents; findings
  `state-archive/s4-user-layer/review-findings.md`, operator decisions 2026-10-14 in
  `operator-decisions.md` there; P19-P31 as recommended). Draft 2 (D1-D15, operator 2026-10-14)
  was kept in the session scratchpad, not committed. **Approved by the operator 2026-10-14**;
  step 0 done (schema doc amended in the same commit; `accept` restarted on `main` with
  `schema: default`). OD-502 when built. **Next:** the build.
- **Base:** `main` at `20acf33` (dropped-spawn `7cd48b1`, review progress line `20acf33`).
  `config.py`, `rules.py`, `schema.py`, `policy.py`, `review.py`, `gate.py` are unchanged since
  `7c438bd`; `claude_review.py`, `claude_eval.py` line numbers are re-checked on `20acf33`.
  File:line refers to `src/` there unless stated. "Unverified" = not checked in code or docs.
- **Scope:** schema doc (`planning-docs/v3.0.0-OPERATOR-SCHEMA.md`, draft 3, "SCH") §15 S4
  bullet; step 1c Phase R P11, P12, P14, P15, P16; S3 plan decision 3 and S3b review B5.
  Requirements R1-R58 from the requirements list (session scratchpad, not committed); coverage table §13.
- **Numbers:** OD-502 (next free; OD-501 is EVAL-DROPPED-SPAWN) records S4's decisions and
  supersedes OD-478/OD-481 (D13). ADR 0030 amended; no new ADR (D13).
- **Facts checked (2026-10-14, `sqlite3 ?immutable=1`):** `accept` holds §1.5a item 2's sample in
  `settings` `config.schema` (939 bytes, applied 2026-10-09; `vendor_tier` levels a bare list),
  ignored today; `accept` has 0 addresses and its `eval_root` points at the removed `ecf-s3b`
  worktree (P13). `ecf1009` has no stored schema and one preset A address (assist). No install
  has applied rules.

## 0. Step 0: before the build

1. **Schema doc amendment** (P14), one commit with this plan (draft 3), docs only. SCH changes:
   - **D12:** `priority` is fixed for good (stays in `SAFETY_FIELDS`); the §1 layer table's
     "Replaceable default fields (3)" becomes `category`, `sender_type`; "default off" removes
     those two; §8 "same treatment for `sender_type` and `priority`" loses `priority`.
   - **D7:** §2 "Size": today's caps apply to C in v3.0.0; the 16 KB budget and P11 move to
     step 3; §15 S4 bullet loses P11.
   - **D8:** user fields are for preset C only; the "Gemma caps while any A/B address"
     sentence (SCH:67-71) goes; a user schema is refused while any A/B address exists, and the
     reverse. C's fallback: none (C is Claude-only, PLANNING §2; P1).
   - **P5/P6:** §10 / Verification: S4's measurement is this plan's M2 (criteria §7), not
     "compared with `61686db4`".
   - **P10:** §2: a description naming one of the 11 fixed field names is refused.
   - **P12:** §2 catch-all: a user enum may reuse `other`, `unknown` or `none`; a `{label:
     {field: x}}` rule is refused on a field whose values repeat another field's value or a label
     name.
   - **D1** (also in §15): S4b after v3.0.0.
2. **accept** (P13; operator OK at the time): restart `accept` on `main`, apply `schema: default`
   there (allowed while extensions are off: `parse` seeds every `default` section,
   `config.py:337`, so `_schema_and_rules` skips the refusal, `config.py:372-374`; SPEC §9.7).
   M2 runs are registered from the S4 worktree, which rewrites `eval_root` (`evalrun.py:640`;
   `claude_eval._remember_root`). With 0 addresses the stale `ecf-s3b` path breaks nothing now.

## 1. What S4 is and is not

**S4 is** the additive half of "your own schema" (RAW:51) for preset C: user fields and added
`category` values on top of the fixed set and the default fields, through the `schema` section of
`ecf config apply`; rules in three layers (fixed, user, default); unknown handling for every
non-fixed field (P14); the Fix form, review line and display caps for user fields (P15); the live
gate's accuracy with user fields present; M2, which decides whether v3.0.0 ships the layer on.

**S4 is not:**
- **S4b, after v3.0.0** (D1; default off, value replacement of `category` and `sender_type`;
  SCH:490, P7): the fixed-rule drop path (S3 decision 3), disabled-rule reporting for removed
  values, `from_v2`/`local_schema` for replaced values, sender confirm without a category
  (S3P:100). v3.0.0 meets RAW:51 additively only.
- **`priority`**: fixed for good (D12), never replaceable.
- **The `spam_or_phishing` rename** (D6b): step 5 (§12a).
- P11 and the 16 KB budget (step 3, D7); the Jev adapter, gate key and privacy wording (steps 2
  and 4); user fields on Slack item cards (step 5, D9); reprocessing (step 5); custom actions
  (Later).

## 2. Current state: the extension, built and switched off

| Where | What |
|---|---|
| `ecf/schema.py:448-454` | `EXTENSIONS_ENABLED = False`, `EXTENSIONS_OFF`, `extensions_enabled()` |
| `schema.py:459-464` | caps: 8 fields, 16 values, 4 category values, 180 chars per description, 4,000 chars of text; `RESERVED_FIELDS` {rule, safety} |
| `schema.py:560-599` | `_ext_field`: shipped names refused (:568-571); field and enum-value descriptions required (`_check_text`, :487-504); ordinal levels may be a bare list (`_levels`, :296-312) |
| `schema.py:626-646` | `_clashes`: an added value can't be any enum value of any field, a removed v3 value, a built-in label or a reserved rule id |
| `schema.py:658-681` | `extend_schema`: fields after the shipped ones, category values appended, `EXT_HEADING` (:467-471) |
| `schema.py:403-426` | `local_schema`: v2 plus the extension (the path run `6b80a149` measured) |
| `schema.py:335-344` | `_model`: every field required, `extra="forbid"`, strict |
| `ecf_server/config.py:134-160` | `current_schema` → `_tolerant`/`_extended`; a broken stored extension → `shipped_fallback` keyed apart + System Error (`schema_tick`, :176-196) + doctor FAIL |
| `config.py:366-395` | `_schema_and_rules`: refusal while off (:372-374); rules in force must compile (:382-393) |
| `config.py:555-568` | `_schema`: reserves built-in labels + the applied file's ids, or the starter's when none is applied (:562), + fixed ids |
| `config.py:853, 867-870` | demotion on a schema change, only with the switch on |
| `rules.py:299-300` | absent extension field → unknown |
| `review.py:306-309`, `:251-258` | Fix form and review line add extension fields |
| `policy.py:108-123` | labels from extension enum fields a rule labels from |
| `claude_pins.py:116-143` | A key and C pins hold the whole effective schema digest |
| `ops_doctor.py:63-88` | schema row; WARN "stored but extensions are off" |
| `tests/conftest.py:102` | `extensions_on`: 23 uses in 9 files (P25) |

Why off: SPEC §1.5a item 2 (SPEC.md:140), run `6b80a149` (SPEC.md:2355): preset A with a sample
extension lost 4.3 points on the built-in fields (lower bound -7.7), recall 89/90. Never measured
on C.

## 3. Changes

### A. Switch, presets, caps

1. **The switch stays until M2 passes** (P5; replaces draft 2's removal). `EXTENSIONS_ENABLED`
   and its branches stay; `EXTENSIONS_OFF`'s text changes ("user fields are off in this
   release"). The S4 branch sets it True for its tests and M2; it merges True only if M2 passes
   (§7); on a fail it merges False and the operator decides next (§7). The doctor's "extensions
   are off" row and the dialog gating (`config.py:794-796, 853`) stay as they are while off.
   User-facing text says "your fields"; code names (`extend_schema`, `extension`) stay.
2. **User fields for preset C only** (D8). `ecf config apply` refuses a `schema` section other
   than `default` while any address is preset A or B; adding an A or B address
   (`addresses.add_address`, `addresses.py:166-181`) is refused while a user schema is stored.
   Each refusal names the reason (the local model isn't measured with user fields, §1.5a item 2)
   and the fix. The check in `add_address` reads `settings` `config.schema` directly (config
   imports addresses, `config.py:88-89`; P26). `local_schema` stops adding the extension: A and B
   are asked plain v2. D8 partly reverses R13 (no "Gemma caps while any A/B address").
   **C's fallback (P1)** is settled by an existing decision, not a new refusal: C is Claude-only
   (`planning-docs/v3.0.0-PLANNING.md` §2, lines 64-67, operator 2026-10-12): `claude_queue_timeout`
   can't be enabled on a C address and its shadow runs and gate don't apply to C. Without it, C's
   fallback would validate a plain-v2 answer against the extended model (`classifier.py:117-129`,
   all fields required) and fail every call (`fallback.py:413-428`). That decision is built in
   step 4 (PLANNING §4.5, "C fallback removed"); until then the code still lets
   `fallback.set_timeout` (`fallback.py:114`) turn it on for C, so the gap exists on the branch
   between S4 and step 4 (dev installs only; v3.0.0 ships both). **S4 builds that one refusal
   itself** (operator decision 2026-10-14): `fallback.set_timeout` refuses enabling it on a C
   address; a test asserts a C address can't have the fallback while a user schema is stored.
3. **No stored-but-ignored state** (P11, replaces draft 2's "kept, ignored, doctor WARN"):
   `current_schema` stays pure on `settings` (no addresses query). Import or restore drops
   `config.schema` from the staged copy and says so in the import output, naming its fields, in
   every case: into an A/B install with the reason; into a C-only install with "re-apply it
   with `ecf config apply`" (dialog, step-up, Security Notice) before it is used (judgment
   call: the decision says "needs a re-apply"; dropping it is how no inactive copy is kept).
4. **Caps** (R13, D7): today's caps stay for C (8 fields, 16 values, 4 category values, 180
   characters, 4,000 characters). Per classify read, today 7,663 bytes (5,999 text + 1,664 JSON
   schema); with draft 2's sample 8,729; at the caps about 13 KB (performance review F3, measured
   2026-10-14) (P16 fixes draft 2's "6.9 KB", which omitted the JSON schema). The 16 KB budget and
   P11 move to step 3. If M2 fails, the first option offered is lower C caps (P16, §7).

### B. Validation (`ecf/schema.py`, `config._schema`)

5. **Descriptions on every level** (R10): an ordinal user field gives `levels` as a mapping of
   level to text; a bare list is refused. A test asserts the shipped v3 has a description on every
   field, value and level.
6. **Catch-all** (R12, D15, P12): an enum user field with no catch-all warns. Catch-alls are
   `other`, `unknown`, `none`; a user enum field may reuse them as its own values (`_clashes`
   exempts exactly these three for user fields; two user fields may both have `other`). A
   `{label: {field: x}}` rule is refused at apply on any field whose values repeat another
   field's value or a label name (`policy.labels`, `policy.py:108-124`, turns label-from values
   into keywords). Added `category` values still need new names. Per-field handling verified by
   the operator's check (P12): rule compares, Fix form (`review.py:366-371`, `block_id` = field),
   prompt and digest (`schema.py:261, 358-361`), `evalrun.py:622-628, 1148-1169`, systemone,
   removed-value maps.
7. **Fixed names in descriptions** (P10, narrows D9b): a user field or value description that
   names one of the 11 fixed fields (`fraud_risk`, `payment_related`, `priority`,
   `requires_action`, `requires_reply`, `deadline_mentioned`, the five items; whole word) is
   refused, listing the names. A description naming a default or user field (`category`,
   `sender_type`, another user field) warns (D9b). Values aren't checked (common words). SPEC
   §12.4 and the threat model say user text is classifier instruction (`classifier.md:5-7`
   "follow it"), that this check stops only literal names, and that the fixed triggers, the fixed
   rules' facts-only clauses and the gate are the controls.
8. **Reserved ids** (P2): `_schema` reserves the built-in labels, the fixed ids, the **default**
   rule ids and the applied file's ids, always (today `config.py:562` takes the applied file's
   ids or the starter's, not both). Kinds (R9) and reserved field names (R7, R8) unchanged.

### C. Rules (`ecf_server/rules.py`, `config.py`)

9. **Unknown for every non-fixed field** (P14, R17). In `_compare` (`rules.py:295-317`), for a
   field condition:
   - a **fixed field** (the 10 in `review.FIXED_FIELDS`, `review.py:67-69`, plus `priority`,
     D12): unchanged; an absent fixed item reads false (step 1c P13, `rules.py:487-488`);
   - **any other field**: absent, or a value not in the field's current values, or a non-boolean
     on a boolean → unknown (`None`); Kleene as today (`_eval`, `rules.py:283-292`); `evaluate`
     matches only on True (`rules.py:256`).
   Only results under `not` change; today's composed rules have no `not` over a field
   (`fixed_rules.yaml:26,30,67,68` negate facts; `starter_rules.yaml` none). The list moves to
   `ecf.schema` as `FIXED_FIELDS` (11). P28: a test asserts every stored classification
   validates against its schema's required fields, so "absent fixed field" can't occur (the
   premise behind keeping it False).
10. **Rule layers** (R20, R21; D2, P3, P4, P17). One `compose(schema, user_text)`
    (`rules.py:553-566`) builds:
    1. ecf's fixed rules (unchanged);
    2. the user's new-id rules, in file order;
    3. the default rules (`starter_rules.yaml`, 11), in their own order, with each default the
       user file **replaces** (a user rule with the same id) spliced in at the default's position
       (P4), and each default in `default_rules_off: [ids]` left out (new optional `RuleSet` key,
       `rules.py:217-220`);
    4. ecf's `otherwise`, unless something before it matches everything.
    The user layer compiles **without** the `otherwise` append (`rules.py:398-400` today appends
    to every non-fixed file; P3). A file whose last rule has no `when` is a catch-all and stays the
    replace case (D2; the defaults are unreachable). Only a bare last rule counts; a de-facto
    catch-all (`requires_reply in [true, false]`) isn't detected, said in the admin guide (P23).
    **Refused at apply** (P17): an unknown id in `default_rules_off`; a fixed id or `otherwise` in
    it; an id both replaced and turned off. **At load** an unknown off id warns and is skipped (a
    later release may rename a default). **Apply output** (D14, P17): each replacement ("your rule
    `invoice` replaces ecf's default `invoice`, at its position; rename it to keep both"), each
    default turned off, the user rules that can hide (`HIDE_ACTIONS`, `rules.py:82`), and which
    case applies ("your rules run before the default rules" or "your last rule matches
    everything, so the default rules don't run"). SPEC §8.6: default rule ids are part of the
    rules-file format; renaming one is a major version (OD-480).
    **Stored files** (P3): a pre-S4 stored file without a catch-all (possible only on S3-era dev
    installs; v2 required one, v2.2.3 `rules.py:352`) changes meaning: the dry run of any `ecf
    config apply` and `ecf doctor` name it (judgment call: the decision says "apply output"; a
    stored file has no apply, so the next dry run and doctor carry it), plus a release note.
    **Converted v2 files** (P24): the P7 conversion (`config.convert_rules`, `config.py:244`;
    `rules.convert_v2`, `rules.py:618-651`) also strips each default-id copy **identical** to the
    shipped default, so converted files don't pin all 11 defaults (no install has applied rules
    today, so it costs nothing now).
11. **The rules fallback never raises** (P2): `current_rules` (`config.py:218-238`) falls back to
    the starter composition, and if that also fails, to fixed rules + `otherwise` (mail is still
    decided, fail closed), with the System Error from `schema_tick`. Invariant: `current_rules`
    never raises for any stored (schema, rules) pair.
12. **Every validator checks the composed set** (P2, P3): apply (`config._rules`,
    `config.py:533-552`), the rules-in-force check (`config.py:382-393`), `rules_problem`
    (`config.py:199-216`), `ecf rules test` (`ruletest.py:241`, already `compose`) and the dry run
    all compile and check the set `compose` builds, never the user file alone. §4a traces each
    check.
13. **Fixed rules reading default fields** (risks 2-3; C4; R22, R23). `priority` (risk 2):
    settled by D12. `sender_type` (risk 3): deferred to S4b, explicitly. In S4 nothing can
    remove or redefine it or `regulator`, `person`, `friend`, `family` (`_ext_field`
    `schema.py:568-571`; P12's exemption covers only `other`/`unknown`/`none`, and only within
    user fields), so `fixed_rules.yaml:40,55` and `KEPT_SOURCES` (`schema.py:128-143`) can't
    break. S4b must: drop a fixed clause on a removed `sender_type` value with an apply message (S3
    decision 3); guard kept-source names against a user value named `person` (code map risk 4). A
    test pins the premise: no accepted user schema changes `priority` or a field or value the
    fixed rules or `KEPT_SOURCES` read.

### D. Policy

14. **Hypothesis invariants** (R26, G7; `tests/test_policy.py`). Strategies sample a prebuilt
    pool built at import (P25): 5-8 extended schemas (0, 1, 4, 8 user fields; enum, ordinal,
    boolean), each composed with: no user file, a replace, `default_rules_off`, a catch-all file;
    only classification values are drawn per example (per-example recompiles would add ~85-130 s).
    1. With any user fields and user rules, a matching fixed rule decides first.
    2. No hide survives a blocker (I1); a user rule archiving phishing is blocked.
    3. A condition on an absent non-fixed field or an out-of-spec value is never True through
       `not`; an absent fixed item plans as false.
    4. The decision equals the first matching rule of fixed + user + (defaults with replacements
       spliced, off left out) + catch-all; `current_rules` never raises for any stored pair.
    5. Replacing a default rule with an identical copy changes no decision (P4).
    6. Existing I1-I4 and S3's invariants unchanged.
    The actor trigger (R27) and limits (`actor.allowed`, `actor.py:94-96`, R28) are unchanged.

### E. Review, Fix form, gate

15. **Fix form and line** (R36, R38, R39): user fields already follow the 13 shipped ones
    (`review.py:306-309`) and the line's `ext:` part (≤ 80 chars, `review.py:74, 251-258`). At the
    caps: 21 input blocks (Slack's 100-block modal limit, not re-checked against Slack's docs) and
    ≤ 23 options per select (`OPTIONS_MAX` 100, `review.py:73`). A test builds the largest form;
    user fields labelled "(yours)". Confusion matrices stay `category` only
    (`ecf/eval/results.py:624`; judgment call, per-item precision/recall covers the items).
16. **Gated accuracy** (R34, D4, P30): `review.record` (`review.py:278-293`) writes `gated_ok`
    (no Fix changed one of the 13 `FIXABLE` fields: 11 fixed, `category`, `sender_type`);
    `reviewed()` (`review.py:99-121`) counts it, reading a review without the key (pre-S4) as
    `verdict == "correct"`. User-field Fixes are reported. `GATE_VERSION = 3` (`gate.py:56`); no
    demotion (a version 2 pass counted every Fix and no user field existed). The first recompute
    under version 3 writes an audit line; SPEC §9.3 says `gate` rows carry no version. The shown
    figure lists items and fields apart ("items 97%, fields 84%") so `priority`, the noisiest
    field, doesn't hide the items (P22).
17. **Sender confirm** (R47): unchanged (`config.py:577-593`).

### F. Claude schema delivery

18. **P11 moves to step 3 with the 16 KB budget** (R40, D7). Nothing in S4 touches delivery
    (`claude_review.py:715`, `claude_eval.py:908`). For step 3: once per spawn with a digest per
    read; MCP tool text and snapshots, plugin `classifier.md`, both skills; its own run pair.

### G. Config UX, doctor, eval, API

19. **Dry run and apply** (`config.schema_preview`, `config.py:606-618`; `cli_admin._echo_schema`,
    `ecf/cli_admin.py:167-196`): the budget line gains the per-read bytes (text + JSON schema;
    P16); the warnings (C6, C7), the rules-layer lines (C10) and the privacy line (§5). The
    step-up dialog's schema lead (`SCHEMA_LEAD`, `config.py:103`) becomes "changes the classifier
    prompt; live addresses go back to assist; a /ecf-eval run and N reviews are needed before live
    again", N per address from `GATE_COUNT` (P15). The Security Notice keeps the lead.
20. **Doctor** (`ops_doctor.py:63-88`): per-read bytes in the budget line (P16); the pre-S4
    rules-file line (C10). SPEC §13.2's row text (still "v2", SPEC.md:1732) rewritten (P27).
21. **First start with a stored extension** (D11, P11): a stored extension that passes is used;
    the first start writes an audit row (`config.schema_activated`) and a Security Notice naming
    the schema and the C addresses demoted (`SCHEMA_REASON`, not the gate's); one that fails the
    new checks falls back closed as today (`config.py:148-154, 176-187`). The `ecf upgrade`
    pre-check lists the C addresses when `extension_stored` is set (`upgrade_state.py:52-53`).
22. **Eval** (R51, R52): results record `schema_digest` with the extension (`results.py:258`);
    user fields are unlabelled, so scoring skips them; Claude eval registration takes the
    effective schema (`claude_eval.py:174, 348`). `ClaimRecord` (`ecf/eval/results.py:61`) gains
    `invalid` (tries refused before the outcome; P19); additive, but `ClaimRecord` is strict, so
    the v3 client note (OD-499/OD-501) is extended. Criteria script: committed under `scripts/`
    with a test reproducing the S3b figures in SPEC.md:2300 (P20; judgment call over adding
    `ecf eval compare --field/--pooled`).
23. **API**: `GET /v1/schema` (`api.py:905-918`) unchanged; `api_version` stays 1 (unverified that
    no client reads the switch; check at build).

## 4. Gate key

S4 keeps today's key (D3): C pins hold the whole effective schema digest and C's prompt digest
(`claude_pins.py:132-143`); A's `local_key` no longer includes user fields (C2), so A/B keys don't
move. Any schema edit on C, a description typo included, drops live C addresses to assist
(`config.py:867-870`), restarts the review count under the new key (`review.py:113`) and needs a
passing `/ecf-eval` (`gate.synthetic`). Stated in the dialog lead (C19), the admin guide (with the
figures: 100 reviews standard, 200 high; a full or `--fraud-only` run) and SPEC, which says the
cost is temporary: step 4 narrows the key (P12, with Jev's) (P15).

## 4a. Existing checks against the new rule order (operator request at P2)

| Check | Where | Assumes | S4 treatment |
|---|---|---|---|
| Id reservation for added values | `config.py:562` | the applied file's ids **or** the starter's | both, plus fixed ids, always (C8) |
| Added value = rule id | `rules.py:383-387` (`_compile`) | per compiled file | unchanged; C8 makes it unreachable at load for the default file |
| Rules fallback | `config.py:218-238` → `rules.py:569-571` | the starter set compiles | starter, then fixed + `otherwise`; never raises (C11) |
| `otherwise` append | `rules.py:398-400` | the user file is the whole set | not for the user layer; ecf's catch-all after the defaults (C10) |
| `otherwise` placement, fixed ids refused | `rules.py:404-422` (`_check_user_rule`) | user file alone | kept; message changes (no append); off-list refusals added (C10) |
| Duplicate ids | `rules.py:390` | per file | per file; a user id equal to a default id is a replacement, not a duplicate |
| Apply validation | `config._rules`, `config.py:533-552` | compiles the user file alone | compiles the composed set (C12) |
| Move targets in `move_folders` | `config.py:547-551` | user file | composed set (defaults have no `move`, `starter_rules.yaml`) |
| Rules in force vs a new schema | `config.py:382-393` | applied file, or `_starter()` compiled as a user file | composed set; `_starter()` as a user file stays the replace case |
| `rules_problem` / System Error / doctor FAIL | `config.py:199-216`; `ops_doctor.py:72-78` | user file alone | composed set; unknown off id: warning, not FAIL (C10) |
| P7 conversion | `config.py:244`; `rules.py:618-651` | strips fixed copies | also strips identical default copies (C10, P24) |
| `ecf rules test` | `ruletest.py:241` | `compose` | new `compose`; same set as mail |
| Dry run | `config.apply(dry_run)`, `config.py:831-846` | `parse` + preview | composed set + C10's lines |
| Import | `importer.py:197-214` | `config.validate(imported=True)` | composed set; `config.schema` dropped from the staged copy (C3) |
| `_clashes` | `schema.py:626-646` | added values can't repeat any value | exempts `other`/`unknown`/`none` within user fields (C6) |
| `policy.labels` | `policy.py:108-124` | label-from values are unique | label-from refused on repeating fields (C6); labels from the composed rules |
| Rules cache | `rules.py:520-566` | key (schema, body, source) | body includes the off list; unchanged shape |
| Eval, rescore, route report | `evalrun.py:250, 777, 1558`; `claude_eval.py:351`; `routereport.py:109` | starter rules only | unchanged (no applied rules in evals) |

## 5. Privacy

- **Anthropic (preset C)** (P29): the field and value names and descriptions in every classify read
  (`schema_text`, `claude_review.py:768`), and the chosen values in every actor read
  (`classification`, `claude_review.py:721`). Operator-written, not email content; no new
  destination (SPEC.md:1674 says B and C; with C2, C only).
- **Slack:** field and value names on the Fix form and review line.
- **TypeSafe (Jev):** set when Jev is built (step 2).
- **Apply-time line** (D10, P29): "Your field and value names and descriptions go to Anthropic with
  each Claude classification request, and the chosen values with each actor request (preset C);
  never put email content or secrets in them." Step 2 adds TypeSafe. SPEC §12.4 updated, and the
  threat model per C7 (P10).
- No free-text type (R9).

## 6. Data, migration, rollback, export

- **No migration** (R45); stored form unchanged.
- **Stored extensions** (R46, D11, P11): §3 C21.
- **Rollback** (R50, P8): the snapshot restores settings, so v2.2.3 gets none; v2.2.3 has the switch
  off (`git show v2.2.3:src/ecf/schema.py:277`). `senders` from live (`downgrade.py:31`): a
  user-added `confirmed_category` never equals a v2.2.3 category, so never corroborates (equality,
  v2.2.3 `policy.py:147-149`). **Where tested:** a suite test (a v3 install with a C address and a
  user schema applied → downgrade snapshot → settings restored, senders merged, corroboration fails
  closed); `ecf1009` runs the checked release path as usual without a user schema (its A address
  blocks one, D8).
- **Export:** `DATA_FORMAT` stays 4; `config.schema` travels in `settings`; on import, C3.

## 7. Measurement

**M0, no model run** (P7):
- **M0a control:** rescore the S3b pair (`024ec270`, `1a8708cf`) on the S4 base commit
  (`20acf33`, or whatever S4 branches from) with `ecf eval rescore --synthetic --through-policy`.
- **M0 S4:** the same on S4 code, no user schema; must equal M0a case by case; both reported
  against live.
- **M0b:** `ecf rules test` with a user file identical to `starter_rules.yaml` gives the same
  decision as `rules: default` on every card (C10's composition and the replace path).
- **M0c:** over the S3b pair's answers, one default field deleted or set to an unknown value per
  case: the starter rules decide as before, and a fixture rule `not: {field: category, eq: x}`
  never matches (C9).
- Plus the Hypothesis invariants (C14).

**M2, the user-layer check** (replaces §1.5a item 2 for C; D11b). Two pinned C runs (as S3b: C
standard, Sonnet x3, Opus `actor_high`; set `43f650c9d1839407`, 304 cards, checked in both S3b
result files; P18), registered from the S4 worktree on `accept` (§0) with the **hard-case sample**
applied by `ecf config apply` (operator step-up; P16): 8 fields at 3,600-4,000 characters (90-100%
of the cap), up to 4 category values, and one field overlapping a fixed item in meaning while
passing C7 (e.g. a `money_request` boolean, "the email asks us to pay, transfer or refund
money", overlapping `payment_related`). Proposed fields: `contract_stage`, `needs_signature`,
`vendor_tier` (with level texts), `money_request` and four more; `legal_notice`,
`subscription_renewal`; full wording shown to the operator before the runs (judgment call on the
field list; P16 sets only the shape).

**Input differences vs the S3b pair** (P18), recorded in the SPEC entry: (1) the user fields (the
intended one); with them the schema digest and C's prompt digest; (2) actor reads carry the user
fields' values and the labels include the added category values (`claude_eval.py` act reads,
`policy.labels`); (3) service code since the S3b pair: the dropped-spawn fix (`7cd48b1`) and the
review progress line (`20acf33`), plus S4 itself; `plugin_digest`, `keywords_digest`,
`excerpt_prep` and M2's base commit named; (4) (diff review R9, 2026-10-14) the hard-case
sample's bytes of schema per classify read, from the dry run's budget line when it is applied
(shipped 7,663; about 12-13 KB expected at 3,600-4,000 characters), and with them the expected
Sonnet classifier input per case: +30-45% over the S3b pair (scaled from S2's +2.2 KB giving
+16-21%); criterion 8's ±10% is the S3b pair's own noise, not a target. Each C result records
`read_bytes` in its summary (`ecf eval compare` warns when two runs differ).

**Criteria, named before the runs** (D5 as amended by P5, P6, P21):
1. Both runs: 0 unsafe, fraud-guard recall 104/104, gate passed. Refused `record_classification`
   tries reported from `ClaimRecord.invalid` (C22; P19).
2. `correct` (rule + safety + labelled fixed items): Newcombe paired lower bound > -5 in each of
   the four cross pairings (S4 run i vs S3b run j). This is **not** §1.5a item 2's built-in-fields
   figure (P6); criterion 3 is closer.
3. Every labelled shipped field right per case (old-style `correct`): > -5 per pairing (P6).
   Defined (diff review R6, 2026-10-14) as schema v2's labelled fields (`category`, `priority`,
   `sender_type`, `fraud_risk`, `payment_related`, `requires_action`, `requires_reply`,
   `deadline_mentioned`) plus the rule, the definition that reproduces the noise table's -0.66
   (`scripts/s4_criteria.py`); the five fixed items and `safety` fall under criterion 2.
4. `fraud_risk` right: > -5 per pairing (P6; n 249).
5. Rule right: > -3.5 per pairing (P5; the S3b pair against itself gives [-2.27, 2.96]).
6. `category`: > -5 per pairing; every case answered an added category value listed.
7. `phishing` recall: > -5 in each run pairing on n = 93 (P21); the pooled figure reported.
8. Report only, against the within-arm table below (P22): `priority` and the workflow fields;
   per-field McNemar/Holm; within-arm discordance (S3b pair: `correct` 24 discordant, 15/9, P31);
   user fields' answer distribution (from each case's `got`); per-read bytes and Sonnet classifier
   input tokens per case (as S2's F6; S3b pair ±10%) (P16).

**Noise reference** (pre-registered, P5/P22; S3b pair `024ec270` vs `1a8708cf`, measurement
review):

| figure | n | diff | Newcombe 95% |
|---|---|---|---|
| `correct` | 304 | +1.97 | [-1.30, 5.32] |
| rule | 304 | +0.33 | [-2.27, 2.96] |
| category | 303 | +0.99 | [-2.11, 4.12] |
| `fraud_risk` | 249 | 0.00 | [-3.20, 3.20] |
| `priority` | 116 | -9.48 | [-16.52, -2.50] |
| `requires_action` | 150 | -6.00 | [-11.31, -1.16] |
| every labelled shipped field | 304 | -0.66 | [-4.55, 3.24] |
| `phishing` | 304 | 0.00 | [-2.05, 2.05] |

Differences inside these ranges carry no attribution.

**Repeat, control, consequence** (P5, P18):
- **One repeat**, stated now: only if exactly one pairing misses its margin by < 1 point, one more
  S4 run; then the pairings with the new run (against both S3b runs) decide, with every other
  criterion re-checked on them (judgment call on "all pairings with the new run").
- **Control:** if a gated criterion fails (after any repeat), one C run with `schema: default` on
  S4 code before attributing the loss to the user fields. Not a second repeat.
- **Fail:** the user layer isn't shipped enabled in v3.0.0 (`EXTENSIONS_ENABLED` stays False,
  C1); the operator decides next; the first option offered is lower C caps measured by a new pair
  (P16).
- **Pass:** SPEC §1.5a item 2b records the measured envelope (8 fields, N characters, one
  overlapping field, per-read bytes) (P16).

Computed with the committed criteria script (C22). After the runs the operator restores `accept`
(`schema: default`). Operator go-ahead per run.

**§1.5a item 2** (R44, D11b): item 2 stays the gate for A/B asking user fields (later, OD-495);
item 2b = M2 for C. OD-481's row points to OD-502.

**Not measured:** user rules on user fields (eval applies no rules file; `ecf rules test` replays
them); A/B with user fields (refused, C2); C has no fallback (PLANNING §2).

## 8. Docs

- **Step 0:** the schema doc amendment (§0), with this plan.
- **SPEC:** §1.5a item 2 and 2b; §7.1 (switch kept until M2; C2, C3, C5-C8; P14 unknown;
  `priority` fixed, D12); §7.4; §8.6 (layers, splice, off list and its refusals, catch-all case,
  default ids are format, OD-480; D2, P4, P17); §8.3 (no I2 exception in S4; step 5 adds one,
  §12a); §9.3 (`gated_ok`, `GATE_VERSION 3`, no version on `gate` rows, items and fields shown
  apart; edit cost, P15); §9.7 (`schema` section: refusals, warnings, privacy line, import drops
  the schema, first-start activation); §12.4 and the threat model (P10, P29); §13.2 doctor row
  (P27); §16 (M2 rules, noise reference); §21.2 (M0, M2); §1.2 Later (labelled examples for user
  fields, per-mailbox schemas, more `priority`-labelled cards, P22); OD-502 row; OD-481 pointer.
- **ADR 0030 amended** (D13); ADR 0025 status line.
- **docs/admin-guide.md:** your own fields (format, descriptions everywhere, catch-alls, the fixed
  names you can't use or mention, caps and per-read bytes, rule layers with splice, off list, the
  bare catch-all, de-facto catch-alls, gate cost of an edit with figures, privacy, C only, fallback
  none for C, PLANNING §2). Default-off and value replacement: "not yet" (S4b).
- **docs/operator-guide.md:** "(yours)" fields; which Fixes the gate counts.
- **README:** "your own fields" line (additive; D1).
- **CHANGELOG** (P14): the S3 "Unreleased" line ("applied rules replace only the other default
  rules", `CHANGELOG.md:14`) rewritten in place in the S4 build commit; S4 lines (user fields for C
  if M2 passes; rule layers; unknown for non-fixed fields; gated accuracy; refusals; import drops
  the schema; stored pre-S4 rules note; `ClaimRecord.invalid`).
- **PLANNING:** step 5 gains the rename and relabel (§12a); step 3 gains P11 and the 16 KB budget.
- **Stale text** (C3, C8): PLANNING:352-353, :537; SCH placeholders and line refs (G5).
- **Code docstrings** in the build commit: `config.py:1-40`, `schema.py:1-30`, `rules.py:1-17`
  (P14).

## 9. Tests

- New: A/B refusals both ways; a C address can't have the fallback while a user schema is stored (C2, P1); import drops the schema (A/B and C-only); first
  start activation audit and notice; list-form levels refused; shipped v3 fully described;
  catch-all warning and reuse; label-from refusal on repeating fields; fixed names refused,
  default/user names warned (C7); reserved default + applied ids (C8); P14 cases each under `not`;
  stored classifications validate (P28); layers (splice, off list, every refusal, unknown off id at
  load, catch-all file, apply lines, P7 strip of identical defaults, cache keyed on the off list);
  `current_rules` never raises; validators check the composed set; Hypothesis pool (C14); Fix form
  at the caps; `gated_ok` and `GATE_VERSION 3` audit; doctor rows; per-read bytes; `ClaimRecord.invalid`;
  Claude eval with a user schema records its digest; the criteria script on the S3b pair; RAW's
  "Soccer" example end to end (R5); the S4b premise test (C13); the rollback suite test (§6).
- Changed: the 23 `extensions_on` uses in 9 files (P25); `test_ops_doctor.py:68`;
  `test_schema.py:212-434`; `test_schema_v3.py:302` (A no longer asked the
  extension); `test_classifier.py:149`, `test_modelq.py`; `test_importer.py:266`.

## 10. Review and size

- Diff review: two agents (safety and correctness, performance) before M2.
- One branch, `s4-user-layer`. Size (estimate, unverified; re-estimated at build, P25): ~14
  source files, ~600 lines; ~15 test files. macOS gate with 0 skipped; CHANGELOG lines.

## 11. Not covered

- S4b scope (D1, D14); P11 and the 16 KB budget (step 3, D7).
- `{label: {field: x}}` on `sender_type` gives labels I4 drops (`policy.py:114, 121`): unchanged.
- User rules on user fields aren't measured by an eval.
- `items.schema_version` stays 3 with or without user fields (`classifier.py:236`).
- The local actor's view (B5; D6); the `spam_or_phishing` rename (step 5, §12a).
- User fields on Slack item cards (step 5; D9).
- De-facto catch-alls (documented, not detected; P23).

## 12. Decided (operator 2026-10-14)

1. **D1** S4b after v3.0.0.
2. **D2** User rules before the defaults; a same-id rule replaces a default (spliced at its
   position, P4); `default_rules_off`; a file ending in a catch-all keeps today's replace.
3. **D3** Whole schema digest in S4; narrowed with Jev's key in step 4; cost stated (P15).
4. **D4** Accurate = no Fix to the 13 fixed and default fields; user-field Fixes reported;
   `GATE_VERSION 3`, no demotion; `gated_ok` at record time (P30).
5. **D5, amended by P5/P6/P21:** rule > -3.5 per pairing; `correct`, every-shipped-field,
   `fraud_risk`, `category` > -5 per pairing; `phishing` > -5 per run pairing on n = 93; one
   pre-stated repeat; fail = not shipped enabled.
6. **D6** Local actor: no change in S4.
7. **D6b** Rename `spam_or_phishing` and a one-time relabel command, in step 5 (§12a).
8. **D7** Caps unchanged in S4; the 16 KB budget and P11 in step 3.
9. **D8** User fields for preset C only (partly reverses R13). P1 (C's fallback) is satisfied by
   PLANNING §2's C-only decision; S4 builds the C fallback refusal itself (operator 2026-10-14).
10. **D9** User fields on Slack item cards in step 5.
11. **D9b, narrowed by P10:** a description naming one of the 11 fixed fields is refused; naming a
    default or user field warns; values not checked.
12. **D10** Apply-time privacy line for Anthropic now (names, descriptions, chosen values, P29);
    TypeSafe at step 2; SPEC §12.4.
13. **D11** Stored extensions used, failing closed; activation audited with a Security Notice and
    the upgrade pre-check lists C addresses (P11). **D11b** §1.5a item 2 kept for A/B; 2b = M2.
14. **D12** `priority` fixed for good; replaceable defaults are `category`, `sender_type`; the
    schema doc amended (§0).
15. **D13** ADR 0030 amended; ADR 0025 status; OD-502 supersedes OD-478/OD-481.
16. **D14** Report replaced and turned-off defaults (and where each runs); removed-value disable and
    the fixed-clause drop in S4b.
17. **D15** Catch-alls `other`, `unknown`, `none`, reusable within user fields (P12).
18. **Phase R P1-P31** applied as in `operator-decisions.md`; P19-P31 as recommended.

### 12a. Decided for step 5: the `spam_or_phishing` rename (D6b, P9)

Not built in S4. Step 5's plan (its own Phase R) covers:
- **Rename:** rule id and label `spam_or_phishing` → `phishing` (name confirmed in step 5) in
  `fixed_rules.yaml`; built-in labels and reserved names; the old id refused in user files with a
  hint; eval expected rule ids, result comparison across the rename, set version.
- **Relabel command:** one-time, operator-started, step-up, run by the service (not a rule action
  or actor proposal), Security Notice. A stated I2 exception in SPEC §8.3, limited to this swap.
- **Mechanism** (P9): per folder ecf may have junked into (`\\Junk`, and INBOX for items Undo moved
  back), `SEARCH KEYWORD` for the install's old keyword (keywords are install-scoped,
  `actions.py:51-53`); act only on matches that are items ecf still holds (D6b, tracked only);
  read their flags, then batched `STORE` (UID sets in chunks): add the new keyword, then remove the
  old, only where the old keyword is present (a person's removal is not undone). In the same
  transaction, rewrite the item's recorded label target, so Undo (`mailbox_actions.py:290-295`,
  which removes recorded labels) and the audit stay true. One audit row per message (found,
  swapped, skipped). Resumable per folder (progress in `settings`), a wall-time cap per check so
  fetch and approvals aren't starved (mailbox work runs under the address lease,
  `mailbox_actions.py:380-399`), progress shown. Untracked matches are counted and reported, not
  changed.
- **Reach, from code (main 2026-10-14):** retention deletes terminal items `log_retention_days`
  after their last change (default 90, range 1-3650), except items that fired a fraud,
  weak-fraud or regulator trigger or were quarantined (`retention.py:5-8, 35-38`; OD-217); an
  item's `locator` holds its INBOX UID (`fetch.py:603-605`); a junk move records the folder and
  Gmail's `X-GM-MSGID` (`mailbox_actions.py:215-223`). So the swap reaches tracked items whose
  message still carries the old keyword in a searched folder; older or untracked mail keeps the
  old keyword (release note). `spam_or_phishing` is never copied to `label_folder`
  (`LABEL_FOLDER_FOR`, `mailbox_actions.py:73`). Unverified: Gmail answering `SEARCH KEYWORD` for
  a custom keyword; provider purging of Junk; Purelymail's stale-UID behaviour on SEARCH (OD-450).

## 13. Coverage

| R | Plan | R | Plan | R | Plan |
|---|---|---|---|---|---|
| R1 | §1, §3 | R21 | §3 C10; D2 | R41 | unchanged (§3 C22) |
| R2 | §1 (S4b after v3.0.0, D1) | R22 | §3 C13 (`sender_type` S4b; `priority` D12) | R42 | §1 (step 2) |
| R3 | §7, §10 | R23 | §3 C13; S4b (D14) | R43 | §3 C2; D8, P1 |
| R4 | §1 (additive only, D1) | R24 | existing (`rules.py:483-512`); §3 C12 | R44 | §7; D11b |
| R5 | §9 (test) | R25 | split (D14): user's replace/off reported §3 C10; removed-value disable S4b | R45 | §6 |
| R6 | §1 (`priority` fixed, D12) | R26 | §3 C14 | R46 | §3 C3, C21; D11, P11 |
| R7 | §3 C8 (existing) | R27 | §3 C14 (unchanged) | R47 | §3 C17 |
| R8 | §3 C6, C8 | R28 | §3 C14 (unchanged) | R48 | §1 (S4b) |
| R9 | §3 C8, §5 | R29 | §3 C10, §4a | R49 | §1 (step 5) |
| R10 | §3 C5 | R30 | step 5 (D6b, §12a) | R50 | §6 (P8) |
| R11 | §3 C7; D9b narrowed by P10 | R31 | §11; D6 | R51 | §3 C22, §7 |
| R12 | §3 C6; D15, P12 | R32 | §7 (gate unchanged) | R52 | §3 C22 |
| R13 | §3 C2, C4; partly reversed by D8; D7 | R33 | §4; D3 | R53 | §7 M2 (8) for C; step 3 for Jev |
| R14 | §6 (existing section) | R34 | §3 C16; D4 | R54 | header, §8; D13 |
| R15 | unchanged | R35 | §4, §3 C19 | R55 | §0, §8 |
| R16 | unchanged (`config_apply` step-up; no MCP tool) | R36 | §3 C15 | R56 | §5; D10 |
| R17 | §3 C9 | R37 | §11; D9 | R57 | §1 (Later) |
| R18 | §2 (reused) | R38 | §3 C15 (matrices `category` only, judgment call) | R58 | §1 (step 3) |
| R19 | §3 C1 (switch kept until M2) | R39 | §3 C15 (existing) | | |
| R20 | §3 C10 | R40 | step 3 (D7); §3 C18 | | |

Conflicts and gaps: C1 D1; C2 §3 C10-C12, §4a; C3 §8; C4 §3 C13 (D12 for `priority`); C5 D14; C6
§4; C7 D9; C8 §6, §8; C9 D13; G1 §3 C1; G2 §7; G3 §3 C3, C21; G4 §7; G5 §8; G6 §4; G7 §3 C14.

Phase R: P1 §3 C2; P2 C8, C11, C12, §4a; P3 C10, C12; P4 C10, C14; P5 §7, C1; P6 §7; P7 §7 M0;
P8 §6; P9 §12a; P10 C7, §5; P11 C3, C21; P12 C6; P13 §0; P14 §0, §8; P15 §4, C19; P16 C4, C19,
§7; P17 C10; P18 §7; P19 C22, §7; P20 C22; P21 §7; P22 C16, §7; P23 C10; P24 C10; P25 C14, §9,
§10; P26 C2; P27 C20; P28 C9; P29 §5; P30 C16; P31 §7.
