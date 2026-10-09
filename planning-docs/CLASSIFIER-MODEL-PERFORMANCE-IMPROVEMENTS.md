# Classifier model performance improvements (v2.1.0, v2.2.0)

- **Status:** draft 3 (2026-10-09). Draft 1 approved by the operator in session; Phase R (three
  Fable reviewers) gave 23 findings, P1-P23, all accepted as recommended by the operator
  2026-10-09 (`state-archive/classifier-plan/review-findings.md`, local). A second-round review of
  draft 2 gave Q1-Q10, all accepted as recommended by the operator 2026-10-09 and applied here.
  Nothing here is built yet.
- **Base:** tag `v2.0.0` (`1dfbaf6`); branch `v2.1.0-dev`.
- **Decisions:** SPEC once built; until then this document. Proposed ODs (numbers confirmed when
  written): OD-483 relaxed facts rule; OD-484 signals sent to the classifier (Gemma and Claude);
  OD-485 two new signals; OD-486 one comparison rule and the evaluation rules (SPEC §16); OD-487
  extensions move to `v2.2.0` (amends OD-481). New ADR 0026 (amends ADR 0009).

## 1. Context

Gemma v2 run `a0462682` (181/257, 0 unsafe, fraud-guard recall 90/90): sender_type 51%,
fraud_risk 80%. Most misses cost score and Slack-card accuracy, not routing (fraud_risk 1 of 41
changed the rule; sender_type 3 of 55, all false `fraud_weak` flags). The two escapes seen
(`mid-exec-quick-favor`, `fraud-w2-request`) were fixed with facts (OD-479, OD-482). Claude
Sonnet (`bd86280c`) scores 38.9% on sender_type, so labels are part of the problem (P1).

Operator decision 2026-10-09: "facts never go to a model" (ADR 0009, SPEC §7.2) becomes a
default that an OD can exempt, per signal and role. "Never taken from a model" stays a hard rule.

**Releases** (operator decision 2026-10-09, OD-487):
- `v2.1.0`: this plan; extensions stay off (`EXTENSIONS_ENABLED` False).
- `v2.2.0`: schema extensions on (ADR 0025), gated against the `v2.1.0` baseline. Extension run
  `6b80a149` lost 4.3 points on built-in fields (`category` 12 worse / 3 better, `fraud_risk`
  9 worse / 3 better); this plan is the dilution fix OD-481 asks for.

**Performance rule:** effort only where a measured miss or escape points; no per-email cost that
shows in tokens; runs kept to: `v2.1.0` run 0, run 1, run 3 (A) and C baseline + C final (about
$6 each, API-equivalent, `bd86280c` $5.60); `v2.2.0` run 2.

## 2. What the model is sent (items 1-4)

**Item 1: relaxed rule, docs** (OD-483, ADR 0026). SPEC §7.2 heading and rule; §3.2; §10.4
`get_message` row (l.1203); OD-279 row (l.2807); §12.4 gains a bullet for derived signals sent to
Anthropic in presets B and C (it covers email content, not derived booleans, P18); the privacy
line in `CLAUDE.md:75` gains derived signals sent to Anthropic (Q9); ADR 0009 status line;
docstrings `facts.py:1`, `classifier.py:6`, `claude_review.py:20`; CHANGELOG.

**Item 2: schema wording** (schema `version` stays 2; digest changes; minor under OD-480).
- `fraud_risk` per-level text (P9): `none` ordinary mail, including genuine service notices with
  no request; `low` any doubt; `medium` the general rule, "any request to move money, data,
  credentials or access on this email's say-so, whatever the pattern"; `high` a known fraud
  pattern, with examples (changed bank details, gift cards, employee tax or payroll data,
  credentials, someone posing as a colleague or executive) marked as examples, not a list.
- `sender_type`: `automated` takes precedence over vendor/company; `team` = a colleague at your
  own organization acting in that role; `vendor` = supplies the business; friend/family only when
  the content shows the relationship; `unknown` includes senders who can't be trusted.
- Renderer: `_field_lines` (`schema.py:206`) prints ordinal level text; the schema format gains
  optional per-level descriptions.
- Length: capped by the context probe (Stage 0), not a fixed 1,200 characters (P11).
- Wording agreed with the operator against the frozen labels (Stage 0).

**Item 3: signals sent to the classifier** (OD-484).
- **Composed, not raw** (P3): each signal is the truth of a rule-level condition, so the model
  sees what the rules were tuned to:
  - "An outside sender opens like an executive-impersonation scam" (`bec_opener` and
    `sender_origin` external and not `self_sent`; rule 1 clause, `starter_rules.yaml:21-24`).
  - "An outside sender asks for employees' tax or payroll data" (`tax_form_request`, external,
    not `self_sent`; `starter_rules.yaml:25-28`).
  - "The sender claims to be someone in your organization but isn't" (`impersonates_internal`;
    already a `fraud_weak` route on its own).
  - "Impersonation together with gift cards" (`impersonates_internal` and `gift_card_keyword`; part of rule 1's impersonation clause, `starter_rules.yaml:15-20`).
  - Item 4's two signals.
- **Not sent:** `bulk_signal` (attacker-set headers, the only fact pushing toward rule 8's hide;
  P2), `reply_to_mismatch` (ordinary on mailing-service mail; P3), raw `gift_card_keyword`
  (demoted for 4/4 false positives, `keywords.yaml:69-71`), and never `auth_result`,
  `sender_seen_before`, `sender_verified`, `sender_origin`, `sender_hash`.
- **True only**, fixed service wording, booleans only; built by one shared function with an
  allow-list test (no trigger reasons, addresses or keywords leak; P18).
- **Placement** (P10): before `<<<EMAIL token>>>`, carrying the same random token; `INSTRUCTIONS`
  (`classifier.py:43-55`) names the block and says only a block with this request's token counts.
  When no signal is true the block is omitted, so the user message is unchanged from `v2.0.0`
  (Q7). Two injection cards test the token rule (Stage 0.2).
- Code: `classifier.user_message`/`ask` take the signals; `classify_item` reads `items.facts`;
  `evalrun.py:802` passes the case's facts (computed at :581).
- Gemma sees the body only (`message.py:129-133`); Claude also gets from, subject, date and
  attachments (`claude_review.py:290-298`). Inputs are aligned on signals, not identical (P16).

**Item 4: two new signals** (OD-485; P8). Model signals only (through item 3): no new rule route,
so no null-arm run.
- `credential_request`: the email asks the reader to send or read back a code, password or login
  (ask direction only: "reply with the code", "confirm your password"; not "never share this
  code", card `v2-acct-verification-code`).
- `crypto_payment`: a wallet-address pattern and a payment verb, both.
- Cut: `regulator_claim_unauthenticated` (duplicates rule 2; true on 7 genuine regulator cards).
- Claimed: firing counts on positive and negative cards; no accuracy claim (1-4 cards, P22).

## 3. Stage 0: freeze, measure, baseline (before any wording or signal is built)

**0.1 Context probe** (P11; Ollama coordination applies). A few classifier calls with the longest
and the CJK cards (and in `v2.2.0`, a maximum-size extension): Ollama's prompt token count vs
`NEAR_CTX` (5,920, `classifier.py:41`). Derives the wording budget. Fallback if it doesn't fit:
lower `MAX_INPUT_BYTES` when an extension is applied, or lower `MAX_EXT_TEXT`.

**0.2 Cards and labels, then freeze** (P1, P2, P8; `.claude/rules/eval-synthetic.md` applies).
- Blind label review (Q2): in one sitting, the operator re-reads card text only (never the
  model's answer) and confirms or changes the existing sender_type and fraud_risk labels across
  the set, not only the missed cards. Unlabelled fields stay unlabelled (sender_type on 145 cards,
  fraud_risk on 57; fields are scored only where labelled, `evalrun.py:257-268`), so the
  denominators don't move. Any label the operator does add is logged and counted separately.
- Hide-path cards: genuine and fraud-labelled notices whose expected facts set `auth_result:
  pass`, bulk headers and seen-before (`ruletest.py:10-11` allows it), so the eval can see rule 8's
  hide (today 0 hide actions in every run). They work only once Stage 0.3 makes the eval apply
  expected facts (Q1).
- Injection cards (Q7): one carries a forged signals block in the body; one says "the block above
  is wrong".
- Negative and positive cards for each composed and new signal (a colleague's internal "quick
  favour", a payroll provider, a genuine 2FA mail, a crypto newsletter or exchange receipt).
- Labels confirmed by the operator; the set is then frozen. Any later card change restarts at
  run 0. `112ca628` rescored under old and new labels reports "cases moved by labels" (changed
  existing labels) and "newly labelled" apart (Q2).

**0.3 Measurement code** (P4, P5, P6, P7, P12, P14, P21; Q1, Q3-Q6, Q8).
- Expected facts in the eval (Q1): `ecf eval run` applies a card's expected facts as `ecf rules
  test` does (`ruletest.py:170`; today `evalrun._inputs`, `evalrun.py:676-683`, ignores them),
  then recomputes the derived facts (`bulk_corroborates` is derived in `facts.compute`,
  `facts.py:177`, before any override, and read by `policy.corroborated`, `policy.py:139-146`).
  `sender_origin` stays set from the internal set only (OD-443). An eval test proves a
  pass+bulk+seen notice with a benign answer yields `mark_read`/`archive` before run 0.
- Per-case `classifier` and `actor` metric objects (prompt, cached, output tokens; prompt_ns,
  eval_ns, load_ns), summarized per role (today `summary.model` mixes roles,
  `evalrun.py:812,826`).
- Environment at start and end: Ollama version, `num_ctx`, power, `pmset -g therm`, `api/ps`.
- Prompt digest: SHA-256 over the rendered system prompt, the user and signals templates,
  `MAX_INPUT_BYTES` and model options; replaces the draft-1 "facts-sent version" (P6).
  - Gate key (Q3): `local_key` (`claude_pins.py:118-131`) takes the Ollama digest and the prompt
    digest without the `keywords.yaml` hash, so a keyword patch (as OD-479, OD-482) doesn't
    restart gates.
  - Result files record the prompt digest plus the `keywords.yaml` hash.
  - Claude pin keys (Q4, `claude_pins.py:133`) take a Claude-side digest (schema plus signals
    template), so a Gemma-only change doesn't restart C.
- Comparison (OD-486): one rule in SPEC §16.5, Newcombe paired interval with the margin as a
  parameter, always printed (today Wald −3 in code, `results.py:18`; Newcombe only for decision
  runs, `cli.py:1337-1339`); Holm per field; "different inputs" when `input_format` or
  `excerpt_cap` differ; "different prompt" when prompt digests differ. §16.5 states which margin
  applies to which comparison (Q8): −5 for this plan's gates; the Wald −3 left in `cli.py:1336`
  and `tests/test_eval_tooling.py:290` is replaced; `gate.synthetic` (`gate.py:211-262`) is
  unaffected.
- Determinism check: stays 10 cases, but a fixed seeded sample spread across the set (today the
  first 10, all fraud/injection), per-field flips reported. A smoke check only (about 1.2 expected
  flips; Q8). No "gain > flip rate" test; the noise
  reference is the replicate pair `a0462682`/`112ca628` (32/257 cases differ somewhere,
  end-to-end discordance 4 vs 4).
- New gated metrics: fraud-labelled cards whose plan keeps a hide (must be 0); fraud-labelled
  cards answered `none` (not above run 0); `fraud_under` on the 38 cards only the model routes to
  `fraud_guard` (null arm `11e65eb9`), reported.
- Runs marked "not for the gate" (run 3), as `--no-actor` runs are (OD-263).
- `ecf eval run --copy-to <dir>`: copies the result out of the install (default
  `state-archive/evals/` when run from a checkout).
- Eval-only switches: `ecf eval run` options turning off the new wording (the v2.0.0 schema text
  kept for this), the signals sent, and the new signals. Production always runs with all on.

**0.4 Evaluation rules in SPEC §16** (P14; OD-486). One paragraph: any model evaluated as a
classifier answers every schema field (decision models mapped first), runs through `ecf eval run`
on the frozen set with the same signals block, records the per-role metrics and prompt digest, is
compared with the one rule, states input differences, and keeps its result file until the decision
using it is recorded. CLAUDE.md gets a four-line pointer under "Hard constraints".

**0.5 Run 0 and C baseline.** Run 0: Stage 0 build, all switches off, frozen set. It records its
prompt digest and an empty `git diff v2.0.0` over `INSTRUCTIONS`, `schema_v2.yaml`,
`keywords.yaml`, `OPTIONS` and `MAX_INPUT_BYTES`, in the result and in SPEC §21.2 (Q5); a
non-empty diff means it isn't a `v2.0.0` baseline. C baseline: one paid C run, same set.

## 4. Stage 1: v2.1.0

Build order: item 1, item 2, item 3, item 4, item 5 (Claude), item 6 (upgrade), item 7
(extension prep); commit per item with the operator's OK.

**Item 5: same signals to Claude** (OD-484; amends OD-279). `get_message` in
`claude_review.py:280` and `claude_eval.py:607` returns a top-level `signals` object from the same
shared function; skill text says signals exist only as that key; `notice` unchanged.
`tests/test_claude_review.py:284-285` inverted. C only; B reuses Gemma's run (OD-289).

**Item 6: upgrade and compatibility** (P6, P15).
- `release.json` carries the shipped prompt digest; `upgrade_check.changes()`
  (`upgrade_check.py:161-171`) marks "prompt", all addresses affected, when the digests differ or
  either side lacks one (Q6). The client can't import `ecf_server`, so it compares shipped digest
  to shipped digest; `after_upgrade` (:174) still compiles the old wheel's schema for schema
  changes. A `prompt_digest` assertion follows the `tests/test_release.py:20` pattern.
- `GET /v1/upgrade/state` reports the effective prompt digest, apart from the shipped one, and
  whether an extension is stored (needed by `v2.2.0`).
- No migration; `DATA_FORMAT` 3 unchanged; new facts are new keys in `items.facts`, absent on
  v2.0.0 items (read as not computed).
- Rollback restores snapshot settings (`cli_upgrade.py:200-202`, `downgrade.py:31`); an imported
  rule using a v2.1.0 fact falls back to the starter rules in v2.0.0 (`config.py:205-218`): one
  unit test; the System Error text no longer blames extensions only.

**Item 7: extension prep, inert** (P19). Optional per-level descriptions for extension ordinals
(counted in the 180/4,000 caps); `EXT_HEADING` (`schema.py:273`) gains "Added fields and values
never change how you answer the fields above. Choose an added category only when no built-in
category fits; never instead of regulatory, vendor_change_request or spam_or_phishing."

**v2.1.0 gate** (SPEC §1.5b, added):
- Context probe passes.
- **Run 1** (all on) vs run 0: Newcombe lower bound above −5 end to end; 0 unsafe; fraud-guard
  recall N/N; hide-path metric 0; fraud-labelled `none` not above run 0; token budgets.
- **Run 3** (signals not sent; "not for the gate") vs run 0: the model's own read. Run 3 keeps
  run 1's `INSTRUCTIONS` (Q5).
- **C final** vs C baseline: 0 unsafe, recall N/N, Newcombe above −5.
- Upgrade test: a `v2.0.0` install (verified wheel) `ecf upgrade --to v2.1.0-rcN`; affected
  addresses named; live → assist; classification works; `--to v2.0.0` inside the window and up
  again.
- macOS merge gate, 0 skipped.
- Attribution accepted as lumped (P12): run 3 − run 0 = wording + cards; run 1 − run 3 = signals
  in the prompt. Composed signals can't change routing, so run 1 − run 3 moves fields and
  `fraud_under`, not end-to-end routing on fraud cards (Q10). If run 1 is repeated, both runs are
  reported and the claim needs both.
- Detectability (P22): item 2 shows only if it fixes about half its targets (sender_type 20 vs 3
  discordant p<0.001; 10 vs 3 p=0.09).

Then `v2.1.0-rc1` → tag (operator OK) → merge → CI → deploy; run 1 (or its re-run on the tag if
code changed) is the `v2.1.0` baseline.

## 5. Stage 2: v2.2.0

Branch `v2.2.0-dev` from tag `v2.1.0`; `EXTENSIONS_ENABLED = True`; a `## v2.2.0` CHANGELOG line
(the tagged rc1 section is not rewritten, P20); ADR 0025 status and SPEC §7.4/§9.7 "planned
v2.1.0" become `v2.2.0` (OD-487).

**v2.2.0 gate** (SPEC §1.5c): context probe with a maximum-size extension; **run 2** (the sample
extension of §1.5a item 2) vs the `v2.1.0` baseline on built-in fields: Newcombe above −5, recall
N/N, 0 unsafe, hide-path metric 0, no `truncated`/`schema_failure`; token budgets for built-in
fields only (an extension adds output). Upgrade tests `v2.1.0 → v2.2.0-rc1` and `v2.0.0 →
v2.2.0-rc1` (an install with a stored extension is named affected); rollback to `v2.1.0` with an
extension stored keeps it but ignores it. macOS merge gate. Fail → extensions stay off.

## 6. Performance measurement (every run, from Stage 0's measurement code)

| Measure | Budget |
|---|---|
| Uncached prompt tokens per classifier call (p50, max) | max below `NEAR_CTX`; p50 within the probe-derived budget |
| Output tokens per classifier call (p50) | within +10% of run 0 (runs 1 and 3; built-in fields in run 2) |
| Cached prompt tokens per call, per role | not below run 0's median (prefix cache intact; SPEC §21.2 notes it resumes at ~400 of ~900 tokens) |
| Classifier and actor latency p50/p95 (prompt + eval durations, cold starts excluded) | reported; flagged only outside the replicate spread (about ±15% p50, ±25% p95) |
| Signal computation time per email (boundary: `facts.compute` + triggers) | reported, with run 0 as baseline |
| Environment (Ollama version, `num_ctx`, power, thermal, `api/ps`) | recorded; AC at start and end or the run is repeated |
| Memory | sampled only if a run reloads a model or swap grows (not otherwise gated) |

Battery: one 40-card spot check on the final build, reported, not gated.

## 7. Ollama coordination

One Ollama user at a time on this Mac. Before the context probe, any eval run, a full-suite or
merge-gate run, or `ecf models install`: `ListAgents`, `SendMessage` every running peer session
with what will use Ollama and for how long (an A run about 45 minutes), and wait for their answer;
`curl -s 127.0.0.1:11434/api/ps` shows nothing else loaded. During a window nothing else in this
session calls Ollama; subagents (reviewers included) are read-only and never call it. Afterwards,
tell the peers it's free, with the run ID. C runs don't use Ollama.

## 8. Cut or deferred

Link facts (no card has a link); `risky_attachment`, `calendar_invite` (no card carries them);
`pressure_deadline`, `bank_details_present`, `small_fee_demand`, `callback_lure` (false-positive
risk, misses already caught); `tracking_number`, `statement_marker`, `personal_mailbox` (wording
first); `reply_to_own_mail` (forgeable); `regulator_claim_unauthenticated` (P8); `bulk_signal` and
`reply_to_mismatch` to the model (P2, P3); the downgrade pre-check (P15); the facts-sent version
(P6); a wording-only run (P12); raising the determinism sample (P7); signals to the decision
model (when one is next evaluated).

## 9. Caveats

- Circularity: wording and signals come from these cards' misses; new cards reported separately;
  no phrase list tuned to a named card.
- Evasion: keyword signals can be paraphrased around; absence never lowers risk (true only).
- English only: the new phrase groups; a limit in SPEC §12.
- Inputs differ between Gemma and Claude (P16).
- Nothing is claimed before its run.

## 10. Verification

While building: targeted tests (classifier, facts/triggers, schema, gate, eval, claude_review,
upgrade_check), ruff, pyright, lint-imports; the full suite once before asking to commit. End to
end: Stage 0 (probe, run 0, C baseline), the `v2.1.0` gate, then the `v2.2.0` gate. Every tag,
push and deploy waits for the operator's OK; after each push, CI is watched and reported.
