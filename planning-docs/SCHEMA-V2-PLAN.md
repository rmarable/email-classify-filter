# Plan: schema v2 (work + personal), schema extensions, num_ctx and the Haiku 5.5 session

_Draft 1, 2026-10-08, approved by the operator. It folds in the Phase R review (three Fable reviewers: safety S1-S16, code C1-C15, eval and process E1-E15; the four open questions decided by the operator, the rest accepted as recommended); findings in `state-archive/schema-v2/review-findings.md`, gitignored. `(Sn)`, `(Cn)` and `(En)` mark text from a finding. OD numbers are provisional until each is committed; OD-474 is the Haiku session._


## Context

**Today:** the classifier schema (SPEC §7.1, `src/ecf/data/schema_v1.yaml`) is fixed and work-oriented, and every consumer calls `load_schema_v1()`.

**The operator wants (2026-10-08):**
- one combined schema that serves personal mail as well as shared work mailboxes;
- users able to extend that schema in YAML through `ecf config apply`;
- more room in Gemma's context;
- the `ecf claude` main session on Haiku 5.5.

**Review:** three Fable reviewers (safety, code, eval/process) reviewed the plan adversarially on 2026-10-08. Their findings are folded in below, and the operator decided the four open questions.

**Operator decisions (2026-10-08). OD numbers are assigned at commit, starting at OD-474.**

Schema content:
- **One combined schema v2:** overlapping categories merged, 6 personal categories added.
- **`fraud_risk` keeps 4 levels** (none/low/medium/high). This reverses the earlier 3-level idea after review: dropping `none` would have widened automatic hiding, moved Opus routing, the no-send guard and individual review, and needed a lossy data mapping.
- **`notification` and `action_alert` stay separate.** `action_alert` is still never hidden (OD-472).
- **`sender_type`:** `staff` is renamed `team`; new values are `company`, `friend`, `family` and `person`.

Extensions:
- They can add fields and `category` values; the safety core can't change. They work in rules with every action, and I1 still gates hiding.
- Gemma and Claude both see extension descriptions.
- They have fixed caps. A file over a cap is refused with every violation listed; the user is warned at 80%.

Gate, process and models:
- **The schema digest joins the go-live gate key.** v2, or any extension change, drops live addresses to assist until the gates pass again.
- **Replies are out of scope** (SPEC §1.2 Later, OD-424).
- **Split work, semver:**
  - Haiku pin;
  - `num_ctx` on v1;
  - schema v2 = **v2.0.0** (rc first);
  - extensions = **v2.1.0**.
- **`num_ctx` 6,144.**
- **Main session on `claude-haiku-5-5`**, adopted only if the acceptance runs pass.

**Process:** this file is the plan; the review findings are numbered S1-S16, C1-C15 and E1-E15 (S = safety, C = code, E = eval/process). `docs/CURRENT-DESIGN-PLAN.md` is not repointed, because it holds the M1-M4 design.

## Step 1: Haiku 5.5 main session (own commit; independent)

**Verified 2026-10-08** on platform.claude.com (models overview and deprecations):
- `claude-haiku-5-5` is Active, with retirement not sooner than 2027-10-07;
- it is priced from $0.10/$0.50 per MTok;
- the release date of 2026-10-07 is unverified, because neither page states it.

**Changes:**
- `src/ecf_server/data/models.lock`: `main_session` set to `claude-haiku-5-5`, plus a lifecycle row so the Monday canary covers it.
- Wording: the `_comment` and the `claude_pins.py` docstring.
- Tests: `tests/test_claude_pins.py:51,101`.
- `scripts/model_canary.py`: check how it reports Haiku entries.
- `upgrade_check`: must not list "affected addresses" for a role that isn't a gate pin.
- Decide whether `claude-haiku-*` overrides become allowed (they would by construction).

**Acceptance** (paid; the operator says go first):
- 2 C runs (`standard` and `high`) and 1 B run;
- each with 0 built-in agent spawns, 0 `model_check` refusals and 0 restarts;
- the loop's share of the cost recorded from `claude_calls`, and the safety gates holding.

The main session isn't a gate pin (OD-278), so no gate re-run is needed.

**Records:** an OD reversing OD-461 for the main session only, an ADR 0016 amendment, SPEC §4.2, §7.5 and §10.3, and the CHANGELOG.

## Step 2: Measure, then `num_ctx` 6,144 on schema v1

**Measure first** on local Ollama at 4,096, 6,144 and 8,192, with the Ollama window agreed and the other sessions told. Record:
- the resident size from `ollama ps`, and swap;
- the context memory (544 MiB at 4,096 today);
- classifier p50 and p95;
- prompt tokens for v1, for the draft v2, and for v2 with a maximal extension;
- the worst-case CJK excerpt against `NEAR_CTX`;
- `prompt_eval_cached_count` under the 1,024 MiB cache cap.

The size of the sliding-window saving is unverified until measured.

**Changes:**
- `ollama.NUM_CTX` becomes 6,144. It's shared by the classifier and the actor, so the model loads once.
- `NUM_PREDICT["classifier"]` is raised only if the measurement shows extension output needs it (measured output is ~60 tokens today).
- `MAX_INPUT_BYTES` stays at 3,000.
- Comments and tests: `classifier.py:40`, `tests/test_ollama.py:194`, `tests/test_systemone.py:453`.
- SPEC: §7.4a ("224 tokens"), §14.3 (the `num_ctx` row), §21.2 (the results).

KV-cache quantization isn't a fallback here. OD-246 deliberately leaves it off; turning it on would need its own OD and eval.

**Eval:** preset A on the unchanged set, compared with f357108a using `ecf eval compare` (same set version, so it's directly comparable).
- Adoption rule, fixed before the run: 0 unsafe, fraud recall 71/71, and the score-interval lower bound above −5 points.
- McNemar and the Holm per-field table are reported.

## Step 3: Schema v2, released as v2.0.0 (rc first)

### 3.1 The schema (`src/ecf/data/schema_v2.yaml`; `schema_v1.yaml` kept for old results)

**`category`, 22 values:**

| Status | Values |
|---|---|
| Unchanged | `remittance`, `billing_inquiry`, `customer_request`, `sales_inquiry`, `partnership`, `bug_report`, `marketing`, `notification`, `action_alert`, `spam_or_phishing`, `private`, `other` |
| Widened | `invoice` (personal bills too), `payment_confirmation` (receipts and orders), `vendor_change_request` (account bank or login changes, sending money a new way), `regulatory` (government agencies, courts, tax authorities) |
| New | `account_security`, `shipping`, `appointment`, `travel`, `finance`, `school_or_family` |

`notification` is reworded so it no longer overlaps the new values: "automated message that only informs and fits no other value".

**`sender_type`, 10 values:**

| Status | Values |
|---|---|
| Unchanged | `vendor`, `customer`, `regulator`, `automated`, `unknown` |
| Renamed | `team` (was `staff`) |
| New | `company` (any other business), `friend`, `family`, `person` (an individual you can't place) |

**`fraud_risk` and the other fields:** unchanged.

**Safety core:**
- fields: `fraud_risk`, `payment_related`, `priority`, `requires_action`, `requires_reply`, `deadline_mentioned`;
- values: `regulatory`, `vendor_change_request`, `spam_or_phishing`, `team`, `notification`, `action_alert`.

**SPEC and tests:** the §7.1 heading changes, so `tests/test_schema.py:23-30` anchors on the new heading. The JSON snapshot gets a v2 file.

### 3.2 Rules and policy

- **Rules 1 and 1b:** `staff` becomes `team`.
- **Rule 1b also covers `person ∧ external ∧ money`** (S5, accepted as recommended; this gets its own OD). Without it, a colleague impersonated from a personal account that the model calls `person` or `friend` would escape the OD-262 clauses. Update the §12.1 threat-model row to match.
- **New starter rules** (none of them hides mail; each continues to the actor if `requires_reply`):
  - `account_security`: label, flag;
  - `shipping`: label, leave;
  - `appointment` and `school_or_family`: label, plus flag if `deadline_mentioned`;
  - `travel` and `finance`: label.
- **Policy, I1-I4 and `FRAUD_RISKY` don't change.**
- **Readers to update or confirm:**
  - `alert_items.py:131-139`
  - `review.py:69-78`
  - `corpus.py:870-895`
  - `claude_queue.py:150,165`
  - `precheck.py:79`
  - `digest_actions.py:167`
  - `cli_admin.py:259` (help text)
  - `mcp_server.py:259` ("schema v1") and the `tests/snapshots/mcp_tools_*` files
  - `ecf/eval/case_templates.py:92`
  - `systemone.py` (comments; tev1's 2,400-byte fit may overflow with 22 categories, which would show up as failed, i.e. unsafe, answers in that eval-only arm)

### 3.3 Data migration (only `staff` → `team` changes value)

**A Python data-migration hook in `db.migrate`** (C1, S6): `0034_schema_v2.sql`, plus a Python step for the same version. It runs on every `db.migrate` path:
- service start;
- `importer.load` and `restore`;
- `ruletest`;
- `destroy` and `regrant`.

**What it maps:**
- `items.classification` and `human_correction`, plus `fallback_shadow.classification`, with a single `CASE`/`json_set` per column;
- items are gated on `items.schema_version = 1` and then set to 2 (the column exists and is never written today; C3);
- `senders.confirmed_category` needs no change, because no category is renamed.

**Stored rules are never rewritten** (E6, S7):
- The rules compiler accepts v1 names as aliases (`staff` → `team`).
- A Security Notice names each affected rule by ID only, never its text.
- `doctor` warns until a v2 rules file is applied.
- `config apply` and `ecf rules test` accept v1 files and print the mapping.
- Nothing refuses startup.

**Old plans** (C6): plans held or awaiting approval keep working. No label is renamed.

**Rollback** (C4, E7):
- whole-snapshot rollback works before settle;
- after settle, `downgrade.prepare` maps `team` → `staff` in `senders` and leaves no other v2-only value behind (to be verified);
- `release.json` moves to schema 34 and data format 3 and gains a `classifier_schema` field;
- `GET /v1/upgrade/state` reports it;
- a v1.0.0 install refuses format-3 bundles, and format-1 bundles are no longer read (SPEC §11.9 and the CHANGELOG say so; S11).

### 3.4 Schema digest and the gate

- **The digest** goes in `pinned_models.schema` (`classifier.py:166-169`). Old rows hold `1`, read as "v1, no extension" (S16).
- **The gate key:** the digest joins `claude_pins.address_key`, `item_key` and the gate key (C7, S10, E3):
  - a changed digest drops live addresses to assist and restarts the review count;
  - `ecf upgrade` names the eval to re-run (§11.10);
  - ADR 0006, SPEC §9.3 and §8.4 are amended.

### 3.5 Eval

**Synthetic rescore** (E1): a new `ecf eval rescore` for the synthetic set maps the recorded `got` values in f357108a to v2 (`staff` → `team`), then rescores them against the v2 labels on the 189 common cards. Paired McNemar and the Newcombe interval are computed on those cards. Relabelled cards and new cards are reported as separate subsets. SPEC §16.5 gains this as the rule for a schema change.

**Relabelling:**
- labels are mapped by script for `staff` → `team` only;
- cards where a new category or sender type fits better are proposed;
- the operator confirms every change (as in OD-472).

**New cards** (E5): written to a coverage table:
- 10 or more per new category;
- every new sender type;
- phishing-vs-real pairs for `account_security`, `shipping` and `finance`;
- impersonation from a personal address (S5);
- all eight fields labelled on each card;
- some `author: hand` cards;
- a short labelling guide for `friend`, `person` and `unknown`;
- the `.claude/rules/eval-synthetic.md` hygiene rules apply.

**Gate**, written down before the run (E4):
- fraud-guard recall N/N, with N re-derived after relabelling;
- 0 unsafe;
- on the common cards, the score-interval lower bound above −5 points;
- the Holm per-field table;
- `fraud_under` reported;
- the null arm (`3d7b79f5`, no model cost) re-run on the v2 set.

**Runs:** A first, then B and C (paid; the operator says go first; roughly C `standard` $6, C `high` $13.50 with a Sonnet session, B under $1, per §16.2).

**Corpus** (E14): `ecf eval label --corpus --migrate` writes a v2 labels file next to the v1 one and marks rows for blind relabelling. v1 corpus results aren't comparable with v2.

### 3.6 Release

Release as v2.0.0-rc1, then v2.0.0 (E8). This needs:
- an OD adopting semver for `vX.Y.Z`;
- CHANGELOG heading `## v2.0.0 (not yet tagged)`, with a Security line for the gate-key change;
- an upgrade section in the admin guide (old rules files, aliases, re-running evals);
- an update to SECURITY.md's supported versions;
- no `ms-` tag.

## Step 4: Schema extensions, released as v2.1.0

### 4.1 Config section `schema`

```yaml
schema:
  fields:
    contract_stage:
      type: enum
      description: Where a contract discussed in the email stands.
      values: {none: No contract discussed., draft: A draft is being exchanged., signature: Waiting for signature.}
  category_values:
    legal_notice: Letter from a lawyer or court about us.
```

**Caps** (final values set from the step 2 measurement):

| Item | Cap |
|---|---|
| Fields | 8 |
| Values per field | 16 |
| Added category values | 4 (22 + 4 = 26; a convenience limit for the decision-model endpoint, which isn't adopted, E15) |
| Description length | 180 characters |
| Total extension text | about 4,000 characters |

**Content rules** (S8, S9):
- Descriptions are single-line and printable: no control or format characters, no leading `-` or `:`.
- Names match `FIELD_NAME`/`VALUE_NAME`.
- An extension value can't equal any value of any field, base or extension, any `BUILTIN_LABELS` entry, or any applied rule ID. This is checked both at apply time and at rule compile time.
- Ordinal levels are unique and checked against the regex.

**Alerts about limits:**
- Over a cap: the dry run refuses the file before step-up and lists every violation with its usage and limit (`schema_limit`, §15.3).
- Within the caps: the dry run always prints the budget line.
- Near a cap: at 80%, it adds "near the limit".
- `ecf doctor` gets a `schema` row.
- The `truncated` alert names the extension as a possible cause.

**What the user sees before step-up** (S9):
- the dry run prints the resulting `prompt_block` diff;
- `_describe_change` lists the names added and removed, kept short for the dialog's 200-character summary;
- the dialog and the Security Notice say "changes the classifier prompt";
- applying the change drops live addresses to assist (step 3.4).

**Plumbing** (C10):
- `schema` is added to `SECTIONS`, `KEY`, `RISK_ORDER` (before `rules`), `_SHIPPED_NAME`, `_shipped_value` and `_describe_change`;
- validation order is `schema`, then `rules`, in both `config.validate` and the importer;
- removing a field or value that a rule uses is refused (the OD-452 pattern);
- the importer allow-list picks it up through `config.KEY`.

### 4.2 The effective schema

- **`src/ecf/schema.py`:** `extend_schema(base, ext)`, with extension fields placed after the base fields; `CompiledSchema.digest`.
- **`config.current_schema(conn)` and `current_rules(conn)`:** they replace every `load_schema_v1()` call. They're cached on the canonical `config.schema` value (C11).
- **Call sites:**
  - `classifier`, `decide`, `actor` and `stages`;
  - `claude_review` and `claude_eval`;
  - `review`, `senders`, `ruletest`, `gate`, `fallback` and `evalrun`.
- **Functions without a `conn`** take a schema parameter: `review._choices`, `correction_from`, `senders._checked`, `check_classification` and `corpus_labels.check_values`.
- **Eval runs** use the effective schema and record its digest. `claude_eval._a_run` also matches on the digest (C5).
- **`GET /v1/schema`** returns `{version, extension, digest}`, for the CLI and MCP profiles only.
- **Rules on missing fields** (C2):
  - a `{label: {field: x}}` action drops when the field is missing from an old row;
  - `eq`/`in` evaluate as false;
  - `gte`/`lte` use the effective schema.
- **`policy.labels`** adds the added category values and the extension enum values used by label rules.

### 4.3 Claude, Slack, corpus

- **Claude:**
  - `get_message` returns `schema_text` as a separate trusted field, and `classifier.md` says so;
  - `check_classification` uses the effective schema;
  - the B and C runs are paid; the operator says go first.
- **Slack:** the Fix form gets the extension fields, and the review line gets `ext: …`.
- **Corpus:** `corpus_labels`: base fields required, extension fields optional, unknown fields refused.
- **Privacy** (§12.4, S14): one line saying operator-written descriptions go to Anthropic (B/C) and their names go to Slack. Neither is email content. Logs record rule IDs and counts only.

## Docs (alongside each step)

- **SPEC:**
  - §4.2, §7.1, §7.4, §7.4a, §7.5;
  - §8.6 and §12.1 (rule 1b);
  - §9.3 and §8.4 (gate key);
  - §9.7, §10.1, §10.3;
  - §11.9 and §11.10 (format 3, rollback);
  - §13.2, §14.3, §15.1, §15.3;
  - §16.5 (the schema-change comparison rule);
  - §21.2 and §23.4.
- **ADRs:** 0024 (schema v2 and the gate key) and 0025 (extensions).
- **Admin guide:** upgrade section, extensions, limits, mixed work and personal installs. Also `docs/gmail-setup.md`.
- **CHANGELOG:** a line for each behaviour change.

## Critical files

- **Schema and config:** `src/ecf/schema.py`, `src/ecf/data/schema_v2.yaml`, `src/ecf_server/config.py`
- **Rules and policy:** `rules.py`, `policy.py`, `data/starter_rules.yaml`
- **Database and versioning:** `db.py` (data-migration hook), `migrations/0034_schema_v2.sql`, `downgrade.py`, `data/release.json`, `upgrade_state.py`
- **Gate:** `claude_pins.py`, `gate.py`, `stages.py`
- **Model calls:** `classifier.py`, `ollama.py`, `claude_review.py`, `claude_eval.py`, `data/models.lock`
- **Import and export:** `importer.py`, `export_bundle.py`, `bundle_reader.py`
- **Eval:** `evalrun.py`, `src/ecf/eval/results.py`, `corpus_labels.py`, `cli_corpus_label.py`, `tests/eval/synthetic/`

## Verification

**Step 1:** the pins tests, then the acceptance runs.

**Step 2:**
- the measurement table;
- `tests/test_ollama.py`;
- the A eval and `ecf eval compare` against f357108a.

**Step 3 tests:**
- **Schema:** `test_schema` (v2 snapshot, safety core, heading anchor).
- **Policy and rules:** `test_policy` (Hypothesis over v2: I1-I4 unchanged); `test_rules` (`team`, rule 1b `person`, aliases).
- **Migration:**
  - every `db.migrate` path;
  - idempotent;
  - format-2 import;
  - rollback after settle.
- **Gate:** reset on a digest change.
- **Eval:** synthetic rescore.
- **Broken tests to update:** the list in C14 (rules, review, alert_items, corpus, evalrun, eval_tooling, classifier, decide, senders, claude_review, config, systemone, mcp snapshots).

**Step 4 tests:**
- **Config:**
  - every cap, with all violations listed;
  - budget line and 80% warning;
  - prompt diff;
  - clashes;
  - rule-dependency refusal.
- **Rules:** an old row under an extension label rule.
- **Cache:** invalidation.
- **Claude:** `schema_text`.
- **Fix form.**
- **Dev service end to end:** `config apply`, `rules test`, an `ECF_DEV_MODEL=1` classification, then `ecf items show`.

**Every step:** ruff, pyright and lint-imports; the full suite once before each commit; CI after each push; the macOS gate (0 skipped) before the merge to `main`. The operator confirms each commit, push, Ollama window, paid eval, label change and tag.
