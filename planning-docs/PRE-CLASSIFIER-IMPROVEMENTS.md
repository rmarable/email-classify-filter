# Pre-classifier improvements: deterministic routes, computed category, input preparation (v2.1.0)

- **Status:** draft 1 (2026-10-10). Approved by the operator in session; Phase R (§9) not yet run.
  Nothing here is built yet.
- **Base:** branch `v2.1.0-dev` at `1b4b5a4`; extends `CLASSIFIER-MODEL-PERFORMANCE-IMPROVEMENTS.md`
  (Stage 1), which stays in force.
- **Decisions:** SPEC once built; until then this document. Proposed ODs (numbers confirmed when
  written): OD-488 deterministic routes, computed category, sender-change and entity facts, link and
  attachment facts, classification reuse; OD-489 excerpt preparation and message signals (new signals
  need an OD under ADR 0026). Open: whether OD-488 also needs ADR 0027 (SPEC §7.4 l.702 calls the
  stored classification "the model's"; ADR 0026 covers facts going *to* a model, not this).

## 1. Context

Run 1 (`66f2190b`, all on) scored 163/267 against run 0's 179 (`883204b3`): −6.0 points (Newcombe
−11.0 to −0.9), 1 unsafe, fraud-guard recall 94/95. The wording fix `1b4b5a4` restored the
phishing phrases item 2 had dropped; the repeat (`852e104b`) scored 166/267: −4.9 (−9.9 to +0.2),
the same unsafe case. sender_type rose 44.3% → 71.0% (39 vs 4, p 3e-8); fraud_risk 78.9% → 83.6%.

The pinned Gemma is unchanged (digest `6114515d…`, `num_ctx` 6,144, Ollama 0.35.0). Our input moved
it:
- **Category.** 15 phishing cards answered `account_security` instead of `spam_or_phishing`. On
  phishing-labelled cards where `credential_request` fires: 0/9 in run 0, 8/9 now; elsewhere 3/44 →
  11/44. The signal's words pull the model; the wording fix moved 2 of 15 back.
- **Unsafe.** `mid-giftcard-staff-rewards` ("HR Coordinator", ten gift cards of 50.00, "send me the
  codes") answered `payment_related: false` in both runs, so `fraud_weak` instead of `fraud_guard`. No
  signal fires on it; only the model routes it.

A case is correct only on **category + rule + safety** (`evalrun.py:316`). Category is wrong on 87 of
the repeat's 101 wrong cases; 42 of those are scams labelled `spam_or_phishing` that the model files
under what they pretend to be. 39 fraud cards reach `fraud_guard` only through the model's answer.

**Goal:** route known scam patterns to `fraud_guard` without the model; let ecf's checks set the
category in those cases; prepare what the model sees. Never lower a route, never change what the
model returned.

## 2. Evidence (offline, recorded answers and computed facts; no model calls)

Facts and keywords against the frozen set's labels (`4f64de5fb1fd2b22`; scam = `fraud_risk`
medium/high or rule `fraud_guard`: 95 scam, 172 genuine):

| Fact | Scam | Genuine | Labels where it fires |
|---|---|---|---|
| `credential_request` | 9 | 0 | 9/9 `spam_or_phishing` (model right 0) |
| `tax_form_request` | 3 | 0 | 3/3 `spam_or_phishing` (model 0) |
| `bec_opener` | 5 | 0 | 5/5 `spam_or_phishing` (model 3) |
| gift card + ask for codes/card numbers | 9 | 0 of 4 genuine gift-card cards | 9 `spam_or_phishing`, 1 `regulatory` |
| `crypto_payment` | 1 | 0 | 1 `vendor_change_request` |
| bank + change keywords | 10 | 0 | 10/10 `vendor_change_request` (model 7) |
| regulator keywords | – | – | 8/11 `regulatory`: too mixed, not used |
| `payment_keyword` | 43 | 33 | not a route |

Simulated on recorded answers (safety routes + computed category, §3-§4):

| Run | Now | With §3-§4 | Unsafe | Category right→wrong |
|---|---|---|---|---|
| run 0 `883204b3` | 179 | 187 | 0 | 0 |
| run 1 `66f2190b` | 163 | 182 | 0 | 0 |
| repeat `852e104b` | 166 | 183 | 0 | 0 |

Entity patterns against labels:

| Signal | Label | TP | FP | FN |
|---|---|---|---|---|
| payment keywords | payment_related | 70 | 1 | 26 |
| amount pattern | payment_related | 55 | 3 | 41 |
| model (repeat) | payment_related | 96 | 6 | 0 |
| date pattern | deadline_mentioned | 47 | 18 | 12 |
| model (repeat) | deadline_mentioned | 59 | 8 | 0 |

Valid IBANs (mod-97): 5 cards, all scam or bank-change. An amount in rule 1's money clauses changes one
case in three runs: `mid-giftcard-staff-rewards`, to `fraud_guard`.

Tested and rejected (too noisy for a route): security words in the sender's domain (12 scam, 4
genuine incl. two real tax notices); an organisation name from free mail (3, 2); "Re:" without thread
headers (1, 3). 9 of the 15 fact-less model-only scams are content-only (extortion, SEO spam, "aunt with
a new address", parcel lures): the model's job.

**Caveat:** the cards and keywords were written together, so these figures are optimistic; the held-out
set (§8) is the check.

## 3. Safety routes (rule 1 `fraud_guard`)

`starter_rules.yaml` rule 1 gains, each from outside and not self-sent (the shape of OD-479, OD-482):
`credential_request`; `gift_card_codes_request` (new fact: a `gift_card` phrase + a `gift_card_ask`
phrase, new list in `keywords.yaml` — "send me the codes", "reply with the codes", "card numbers",
"photo of the back", "scratch", "claim code" — with the `credential_never` exclusions); `crypto_payment`.

## 4. Computed category

**Never overwrite the model's answer.** `items.classification` stays as the model returned it. The
computed category is a fact, `computed_category`, in `items.facts` (no migration; `DATA_FORMAT` 3).
Policy uses an *effective* classification: the model's, with `category` replaced when set.

New `ecf_server/computed_category.py`, one pure function over facts and the model's category:
1. `bank_change` (bank + change keywords, exposed as a fact) → `vendor_change_request` (first in the
   order, always applies).
2. From outside, not self-sent, with `credential_request`, `tax_form_request`, `bec_opener`,
   `crypto_payment` or `gift_card_codes_request` → `spam_or_phishing`, unless the model said
   `vendor_change_request` or `regulatory` (earlier in the agreed order).
3. Otherwise none.

Every case step 2 applies to already routes to `fraud_guard` (§3; first match, `hide: never`), and
step 1's category routes there too, so the computed category can only add a fraud route.

**Policy** (`policy.py`, the one call every path shares: production `decide.plan_for`, fallback shadow
`fallback.py:390-400`, Claude eval `claude_eval.py:358-375`, `ecf eval run` `evalrun.py:641-643`, gate
replans `gate.py:184`):
- `rule_input` (:164) passes the effective classification to the rules.
- I1 kept: `hide_blockers` (:127) and `corroborated` (:139) read the **model's** category; the
  regulatory blocker ORs in the computed one; `fraud_signal` gains the new facts, so a computed
  `spam_or_phishing` never enables rule 6 `junk`, even under a person's custom rules.
- New invariant **I5** (docstring + Hypothesis property in `tests/test_policy.py`): for every
  classification and facts, the effective plan's escalations and flags ⊇ the model-only plan's, and
  its hides ⊆ it.

**Display** (operator decision 2026-10-10: both, labelled): `review.py:218`, `alert_items.py:132/138`
show `<computed> (ecf's checks; model said <model>)` when they differ. `claude_queue.py:165` batch
risk reads the effective category. Audit `policy.decided` (`decide.py:170-176`) adds
`computed_category`.

**Eval:** `category` and the headline score the effective category (`score_parts` :288,
`claude_eval.py`); new per-field `category_model` keeps model comparisons model-only (OD-486); summary
count `computed_category_applied`; `ecf eval compare` prints both. Old results read `category_model` as
unknown.

**Signals:** unchanged; the `credential_request` signal's harm was on exactly the cards §4 fixes.
Revisit if run 1 shows spill-over.

## 5. Post-model additions (operator 2026-10-10: these augment, none replaces)

**A. Escalate when a known sender changes** (raises risk only; never sent to a model). History exists:
`senders.dmarc_pass_count`, `first_pass_at`, `last_pass_at` (→ `sender_seen_before`),
`confirmed_category`, `expected_reply_to_domain` (`facts.py:152-160`, `_enough_history` :284).
- `known_sender_auth_regressed`: DMARC-pass history, but this message's `auth_result` isn't `pass`
  (a spoof, or a compromised mailbox) → rule 1.
- `confirmed_category_departed`: a person-confirmed category, and this message's effective category is
  `vendor_change_request`, `account_security` or `regulatory` while the confirmed one isn't → rule 1b.
- Eval: `ruletest.with_expected` recomputes them from expected `sender_seen_before`, `auth_result` and a
  new `sender_confirmed_category`, as it does `bulk_corroborates` (ruletest.py:150).

**A2. Entity facts** (join other conditions; never a route alone, never a signal):
- `amount_mentioned` (patterns in `keywords.yaml`: symbol or code with a number, `n.nn`, "n dollars",
  `1.234,56`, `USD 1,200`): joins the money ORs in rule 1's team-from-outside and impersonation
  clauses and the matching 1b clause, beside `payment_keyword`.
- `bank_details_present` (a valid IBAN, mod-97, or routing/account-number wording): for A, a sender with
  history sending bank details when its earlier mail had none → rule 1. Needs a per-sender bit, a new
  nullable `senders` column set like `payment_history`: a migration (check `DATA_FORMAT`, upgrade notes).
- Dates stay with the model.

**C. Link and attachment facts** (hide blockers only, no route yet):
- `htmltext._Walker` (:42) records visible `<a>` anchors: `link_text_mismatch` (the text shows a domain
  other than the href's), `link_count` (HTML and plain text).
- `risky_attachment` from `Attachment` (`message.py:71`): HTML, HTM, SVG, script, executable or disk
  image (list in `keywords.yaml`).
- A route waits for held-out evidence: tracking redirects in genuine marketing also mismatch.

**E. Reuse a classification for identical content** (performance only). Same excerpt, signals, prompt,
schema and model digest is the same request; at temperature 0, the same answer. `classify_item`
(`classifier.py:128`) first looks for an item of this install classified in the last 24 h with the same
**input digest**; if found, it copies the classification and skips Ollama. The digest and
`reused_from` go in `pinned_models` JSON (no migration). Facts, rules, policy and actions still run per
item and address. Never across installs, never from a failed or `truncated` call, local classifier only
(A, B, C's fallback); `human_correction` isn't copied. Audited; reuse count in `ecf status` and model
metrics. Unit-tested only (the eval has no duplicates).

## 6. Input changes (each with an off switch, measured separately)

**B. Excerpt preparation** (classifier and actor excerpts; the trigger scan is untouched):
`ParsedMessage.excerpts` (`message.py:124-133`) becomes `redact(prepare(body))`.
- Cleanup: `skeleton.normalize` (NFKC + Cf removal, `skeleton.py:41`), then collapse runs of spaces and
  blank lines. Normalizing before redaction also stops zero-width characters splitting an injection
  phrase past `redact_injection`.
- Strip quoted replies (`>` lines; `On … wrote:`, `-----Original Message-----`, `From: … Sent: …` to
  the end; HTML `<blockquote>`, Gmail/Outlook quote containers), the `-- ` signature, and footer phrases
  (new `excerpt_footers` list). Replace with `[earlier messages in this thread: removed]`; keep the
  original if nothing would remain.
- `ecf eval run --no-excerpt-prep` (not for the gate).

**D. Message signals** (fixed wording, no domains or names, ADR 0026): "A link's visible text names a
different site than the link goes to." and "The email has an attachment that can run code or open a web
page." A new group in `signals.WORDING` (`signals.py:20`) with `--no-message-signals`. The prompt
digest changes; the token budget is re-measured (at most 1,500 added; run 1 used about +300).

## 7. Out of scope

- **Network:** this plan adds no network calls. Facts are local; links are compared, never fetched.
  Pre-classification calls stay the IMAP fetch and the cached, budgeted DKIM/DMARC lookups
  (`dnscache.py`); Ollama is loopback. The DNS cache cap (TTL capped at the check interval) stays as is
  (operator 2026-10-10).
- Not added: sender history as a hint to the model (it pulls the model toward routine on exactly the
  compromised-vendor case; groomable; a new disclosure to Anthropic on B/C; not measurable in eval), PDF
  parsing, Bayesian or rspamd scores, model summaries, network lookups.
- Skipping the classifier for bulk senders: SPEC §1.2 Later only (step 0): rules 7 and 8 need the
  model's `category`, `sender_type` and `requires_action`.

## 8. Held-out set

`tests/eval/heldout/` (same format, `labels.jsonl`), run with `--root tests/eval/heldout
--not-for-gate` (operator decision 2026-10-10: the frozen set, run 0 and the C baseline stand). Written
by read-only agents and labelled blind by the operator **before any code**, then frozen. About 30 cards:

- Genuine look-alikes: a 2FA notice "never share this code"; a helpdesk "reply with your ticket number";
  a staff gift-card order receipt; an HR gift-card thank-you; a real vendor bank-change letter; a payroll
  provider W-2 notice; an exchange withdrawal; "send us the code from your invoice".
- New scams: one or two per pattern, worded differently from the set.
- Threads: a scam ask above a quoted genuine thread; a genuine reply quoting an old invoice.
- Links: a mismatched-link phish; genuine marketing with tracking redirects.
- Attachments: an HTML-attachment phish; a genuine PDF invoice.
- History: a seen sender with auth failing; a confirmed-invoice sender asking for a bank change; a
  confirmed sender's genuine change of topic.
- Entities: a staff-impostor ask with only an amount; genuine receipts in `1.234,56 €`, `USD 1,200`,
  `£50` from a seen sender; a genuine supplier's first IBAN; a known vendor's new IBAN.

**Realistic authentication** (operator 2026-10-10): the frozen set has `auth_result: none` everywhere.
Held-out cards set it as an expected fact: `fail` for spoofs of real or known senders; `pass` for
lookalike or attacker-owned domains and genuine mail; `none` only where the domain plausibly publishes
no DMARC. Each card's choice is in its notes.

## 9. Phase R: adversarial review

**R1, this document**, before step 0: four read-only Fable agents in parallel, told to attack, not
summarise:
- **Safety:** can anything lower a route or enable a hide (I1-I5)? Evasion: zero-width or full-width
  characters in amounts and IBANs; the ask inside a quoted block that B strips; groomed history; a clean
  IBAN from a compromised known vendor; a computed category reaching rule 6 under custom rules.
- **Measurement:** overfitting, held-out leakage, validity of the offline simulation, stage attribution,
  the comparison with run 0 (no `category_model`), detectability of stage 2 gains.
- **Cost and design:** migration and `DATA_FORMAT`, upgrade path, prompt budget, ADR 0026 and privacy
  fit, moving parts against the gain, simpler alternatives.
- **Performance:** per-email cost on the MacBook Air (prompt and output tokens, prefix-cache reuse with
  the signals block before the email, latency against run 0's 5.4 s p50 / 7.2 s p95); trigger-scan cost
  of the new patterns over 10 MB parts and regex worst cases; HTML anchor parsing and quote stripping on
  hostile HTML; the extra `senders` read and write; the 6-minute model-queue round. It names the
  measurements each stage reports, including signal-computation time per email.

Findings merged into P1…, by severity, key claims spot-checked; the operator decides each; this document
is amended before step 0.

**R2, the code**, before each Ollama stage run: two read-only Fable agents in parallel, safety and
correctness (I1-I5, evasion, wiring, eval scoring), and performance (hot paths, worst-case regex,
`senders` I/O, and in stage 2 the prompt and cache). Findings fixed or decided before the run.

## 10. Steps

Each committed with the operator's OK; full suite before each commit request.

- **00. CLAUDE.md rule** (operator decision 2026-10-10): an "Adversarial review" bullet: every plan that
  changes the classifier, rules, policy, facts or eval gets Phase R (four Fable agents: safety,
  measurement, cost and design, performance; findings numbered, the operator decides, the plan is
  amended); before any gate eval run, two Fable agents (safety and correctness, performance) review the
  diff. Written on `main`, merged into `v2.1.0-dev`.
- **0. SPEC §1.2 Later** (operator request 2026-10-10): "reuse of classifications for bulk templates"
  in the Later row and a paragraph after "Grouped model calls": why it isn't a plain skip, what still
  forces a model call, the corpus measurements first, try shorter keys and grouped calls first.
- **1. Docs:** OD-488, OD-489 (+ ADR 0027 if chosen); SPEC §7.2 facts, §8.5 triggers, §8.6 rule 1,
  policy I5, §16.2 eval scoring, §21.2 repeat-run entry; the classifier plan §4; CHANGELOG.
- **2. Held-out set** (§8), before any code.
- **3. Facts** `gift_card_codes_request`, `bank_change` (+ `keywords.yaml`, keywords digest);
  `tests/test_triggers.py`, `tests/test_facts.py` incl. `credential_never`.
- **4. Rules:** the three rule 1 routes; `tests/test_rules.py`.
- **5. Computed category, policy wiring, I5;** `tests/test_policy.py`, `tests/test_decide.py`,
  `tests/test_computed_category.py`.
- **6. Display and audit;** review and alert tests.
- **7. Eval scoring** (`category_model`, applied count, compare); `tests/test_evalrun.py`,
  `tests/test_claude_eval.py`, compare tests.
- **8. A + A2** (`facts.py`, `triggers.py`, `keywords.yaml`, the `senders` migration,
  `ruletest.with_expected`, rules); amount formats, IBAN checksum valid and invalid, migration tests.
- **9. C** (`htmltext.py`, `message.py`, `policy.py`); htmltext, message and policy tests.
- **9a. E** (`classifier.py`, `fallback.py` local path, status and metrics); reuse on identical input;
  none when any digest differs, after 24 h, after a failed call, or across installs.
- — **End of stage 1** (post-model only).
- **10. B** and `--no-excerpt-prep` (`message.py`, `htmltext.py`, `keywords.yaml`); quoted, Outlook,
  Gmail, signature, footer, all-quoted, zero-width-split injection now redacted.
- **11. D** and `--no-message-signals` (`signals.py`, `evalrun.Options`); signals allow-list; prompt
  digest in `release.json`; budget probe re-run.

## 11. Verification

- Targeted tests per step; ruff, pyright strict, lint-imports; full `uv run pytest -n auto -rs` before
  each commit request (0 skipped for the merge gate).
- **Stage 1 offline:** rescore the recorded answers of run 0, run 1 and the repeat through the new facts,
  rules and policy; expect 187 / 182 / 183 and 0 unsafe, or explain the difference.
- **Ollama** (peers asked first, `api/ps` checked, `accept` restarted from `../ecf-v2.1`, on AC):
  - **Run 1 on stage 1 code** (frozen set, all on): the gate against run 0 (Newcombe lower bound
    > −5, 0 unsafe, recall 95/95, hide 0, `none` ≤ 2/71, token budgets). Run 0 has no
    `category_model`: the compare uses `category` for both and says the effective category applies to
    run 1 only; run 0 rescored offline through the new policy is reported beside it.
  - **Held-out run:** no genuine look-alike routed to `fraud_guard` or given a computed category; every
    new scam routed to `fraud_guard`. If a genuine card trips a rule: stop, report, narrow the keyword
    list; never change held-out labels.
  - **Stage 2:** one run all on, plus `--no-excerpt-prep` and `--no-message-signals` runs (not for the
    gate). Lands only if non-inferior to stage 1's run 1 (OD-486), 0 unsafe, recall N/N; otherwise held
    back.
  - **Run 3** (`--no-signals --not-for-gate`) as in the classifier plan; **C final** against `d880a988`
    (paid, operator's go-ahead), noting its expected-facts caveat (SPEC §21.2).
