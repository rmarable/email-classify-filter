# Pre-classifier improvements: deterministic routes, input preparation (v2.1.0)

- **Status:** draft 2 (2026-10-10). Draft 1 (`31d1a22`) approved by the operator in session; Phase R
  R1 (§9) run the same day: findings P1-P28, all accepted as recommended (operator 2026-10-10). Draft 2
  folds them in; `(Pn)` marks text from a finding. Nothing here is built yet.
- **Base:** branch `v2.1.0-dev` at `1b4b5a4`; extends `CLASSIFIER-MODEL-PERFORMANCE-IMPROVEMENTS.md`
  (Stage 1), which stays in force.
- **Decisions:** SPEC once built; until then this document. Proposed ODs (numbers confirmed when
  written): OD-488 deterministic routes as fraud-trigger reasons, the sender-change fact, IBAN as a bank
  hit, link and attachment facts; OD-489 excerpt preparation and message signals (new signals need an OD
  under ADR 0026). No ADR 0027: the computed category is cut (P1), so nothing a model returns becomes a
  fact.
- **Cut in draft 2:** computed category (P1), classification reuse E (P11), `bank_details_present` and
  its `senders` column (P12), `confirmed_category_departed` (P28), the amount fact in rule 1 (P9).

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

A case is correct only on **category + rule + safety** (`evalrun.py:317`). Category is wrong on 87 of
the repeat's 101 wrong cases; 42 of those are scams labelled `spam_or_phishing` that the model files
under what they pretend to be. 39 fraud cards reach `fraud_guard` only through the model's answer.

**Goal:** route known scam patterns to `fraud_guard` without the model, including in the pre-check when
the model fails or is backed up (P5); prepare what the model sees. Never lower a route, never change
what the model returned, never set the category from ecf's checks (P1).

**Decided (operator 2026-10-10; P1, M1):** the category regression in §1 comes from the classifier
plan's `credential_request` signal (OD-485). Draft 1's computed category would have hidden it in the
score; draft 2 does not, so with the signal on, the stage-1 gate (§11) on model category would be
expected to fail as the repeat did (−4.9, lower bound −9.9). `credential_request` is **removed from the
signals sent to Gemma**; it stays a fact and a route (§3), which catch these scams without the model.
The prompt digest changes (gates restart); OD-485 and SPEC are amended in step 1, the signal change is
step 1a.

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
| bank + change keywords | 10 | 0 | 10/10 `vendor_change_request`; trigger 1 already routes all 10 (`triggers.py:322-333`) |
| regulator keywords | – | – | 8/11 `regulatory`: too mixed, not used |
| `payment_keyword` | 43 | 33 | not a route |

**What the routes change** (Phase R M2, reviewer's recomputation on the recorded answers; to be
reproduced by the committed rescore tool, step 7): on run 1 and the repeat, exactly one case changes its
rule (`mid-giftcard-staff-rewards`, `fraud_weak` → `fraud_guard`, the unsafe case); on run 0, none.
Model-only `fraud_guard` cards (null classification + rules, `evalrun.rules_alone`) fall from 39 to 24.
Draft 1's +8 / +19 / +17 headline gains were ~95% computed-category scoring, not routing (P1).

Entity patterns against labels:

| Signal | Label | TP | FP | FN |
|---|---|---|---|---|
| payment keywords | payment_related | 70 | 1 | 26 |
| amount pattern | payment_related | 55 | 3 | 41 |
| model (repeat) | payment_related | 96 | 6 | 0 |
| date pattern | deadline_mentioned | 47 | 18 | 12 |
| model (repeat) | deadline_mentioned | 59 | 8 | 0 |

Valid IBANs (mod-97): 5 cards, all scam or bank-change. Dates and amounts stay with the model (P9).

Tested and rejected (too noisy for a route): security words in the sender's domain (12 scam, 4
genuine incl. two real tax notices); an organisation name from free mail (3, 2); "Re:" without thread
headers (1, 3). 9 of the 15 fact-less model-only scams are content-only (extortion, SEO spam, "aunt with
a new address", parcel lures): the model's job.

**Caveat (P9, M3):** the cards and keywords were written together. Five of the six `gift_card_ask`
phrases were taken from frozen positives; "photo of the back" and "claim code" match no frozen card and
are **untested**. Each list's per-phrase frozen hit count is recorded in the step-3 commit and the lists
are frozen before any held-out card is written. The held-out set (§8) is a smoke check, not a measure of
the false-positive rate.

## 3. Safety routes (fraud trigger reasons) (P5)

The routes are reasons on the existing `fraud` trigger (`triggers.py`, the OD-453 impersonation shape,
:22-28, :301-304), not new rule clauses. So the pre-check escalates at fetch without the model
(`precheck.py:94-115`), and `policy.fraud_signal`, `high_risk`, `send_refusal`, `alert_echo` and step-up
follow with no new policy code. Rule 1 already matches `{trigger: fraud}` (`starter_rules.yaml:28`).

Each from outside and not self-sent unless stated:
- `credential_request` → fraud reason; from inside → `fraud_weak` (rule 1b) (P25).
- `gift_card_codes_request` (new fact: a `gift_card` phrase + a `gift_card_ask` phrase, new list in
  `keywords.yaml` — "send me the codes", "reply with the codes", "card numbers", "photo of the back",
  "scratch", "claim code") → fraud reason **from any origin**, not self-sent (P25: a compromised internal
  account is SPEC §12.1's case). No `credential_never` exclusion on it (P16).
- `crypto_payment` → fraud reason.
- `bank_change` is not added: trigger 1 already fires on bank + change wording.
- A checksum-valid IBAN (mod-97, on `normalize()` text, spaces and hyphens removed) counts as a `bank`
  keyword hit, so trigger 1 covers it with no new fact or column (P12).

The facts stay rule-readable (`rules.FACTS`) for custom rules and the signals. Reasons use fixed wording
and never quote an IBAN, wallet, URL or domain (P26; the crypto precedent, SPEC §7.2).

**Keyword lists (P16):** `credential` gains `codes`, `verification code(s)`, `passcode(s)`,
`one-time code(s)`. The `credential_never` veto ("we will never ask for your password") stays on the
signal to the model; on the route it is kept only if the frozen and held-out sets show it saves a
genuine card (measured in step 3, reported with the commit).

**Unicode (P15):** keywords already fold full-width, Cyrillic and Cf characters (verified). Draft 2
adds: nonspacing marks (Mn, e.g. U+034F, U+FE0F, U+0305) removed before keyword matching; amounts, IBANs
and wallets matched on `skeleton.normalize()` text, never on `fold()` text (`fold` maps `1`→`l`,
`0`→`O`, verified); `WALLETS` moves to `normalize()` (a zero-width space inside a wallet evades it
today). One evasion test per character class on a §3 phrase.

## 4. Scan speed and regex bounds (P2, P3) — before any new pattern

Measured on this Mac: `triggers.scan` takes ~3.9 s per 1 MB plain part (~38 s per 10 MB), un-time-limited
in the check thread (`analysis.py:61`), because `texts_of` passes plain parts twice (`visible` is `full`)
and 199 patterns each search the whole text. Each new literal pattern adds ~0.17 s per 10 MB.
- Scan each distinct text once (`dict.fromkeys`).
- One alternation per keyword group with the matching phrase recovered from the match (reviewer: 1.1 s
  for all groups over 10 MB, ~17× faster), or move `scan` into the isolate child under its time limit.
- Every new regex: fixed-width groups with bounded repetition and a lookbehind that forbids a start inside
  a digit/separator run (never `\d+(?:[.,]\d+)*`, measured > 15 s/MB); quote markers matched per line
  with `[^\n]{0,N}` bounds, never DOTALL across the text; "to the end" as a line-index cut.
- Tests: `scan(texts_of(…))` on a 10 MiB plain and a 10 MiB HTML part under a fixed bound; each new regex
  on its crafted worst input at 10 MiB, ≤ 1 s.

## 5. Post-model and entity facts (operator 2026-10-10: these augment, none replaces)

**A. Known sender, failing authentication** (raises risk only; never sent to a model).
- `known_sender_auth_regressed`: `_enough_history` (`facts.py:284`) of DMARC pass, and this message's
  `auth_result` is `fail` → fraud reason (rule 1). `none` does not fire: forwarding, lists, late
  verification and OD-187 all give `none` for exactly these senders (P14, SPEC §7.3).
- `confirmed_category_departed`: deferred (P28; needs the category, which isn't a fetch-time fact).
- Eval: `ruletest.with_expected` recomputes it from expected `sender_seen_before` and `auth_result`, as it
  does `bulk_corroborates` (`ruletest.py:150`). The held-out set tests the fact in isolation, not the
  end-to-end auth path (M12).

**A2. Entity facts** (recorded, never a route, never a signal):
- `amount_mentioned` (bounded patterns, §4): recorded for measurement only; not in rule 1's money
  clauses (P9: its one supporting card is the one `gift_card_codes_request` fixes). Reconsider on held-out
  and corpus evidence.

**C. Link and attachment facts:**
- `htmltext._Walker` (:42) records `link_text_mismatch` (an anchor's visible text names a different
  registrable domain than its href) and `link_count` (HTML and plain text). Bounds (P22): innermost
  anchor only, href cut to 2 KB before host extraction, anchor text cut to 200 chars, recording stops
  after N anchors (`link_count` saturates), booleans and counts stored, never URLs. Recorded for
  measurement only: **not a hide blocker** (P13: tracking redirects make nearly every newsletter
  mismatch, which would stop rules 7 and 8).
- `risky_attachment` from `Attachment` (`message.py:71`): HTML/HTM/SHTML/XHT/XHTML/MHT/MHTML,
  SVG/SVGZ, HTA, JS, VBS, LNK, executable or disk image (ISO/IMG/DMG), or `application/octet-stream`
  with such an extension (list in `keywords.yaml`). A hide blocker; with a payment, credential or
  gift-card keyword → `fraud_weak` (rule 1b) (P13).
- Attachment names (P24): extension and type tested on the uncapped name; the stored name capped as
  head…tail keeping the last 12 characters (today a 100-char cap can cut `.html` to `.h`).

## 6. Input changes (stage 2; each with an off switch, measured separately)

**B. Excerpt preparation** (classifier and actor excerpts; the trigger scan is untouched):
`ParsedMessage.excerpts` (`message.py:124-133`) becomes `redact(prepare(body))`.
- Body: all text parts joined (plain first, then visible HTML text where no plain part), not only the
  first text/plain part (P4, S10).
- Cleanup: `skeleton.normalize` (NFKC + Cf removal), then collapse runs of spaces and blank lines to one
  blank line (one is kept: `redact_injection` works per paragraph) (P18). Normalizing before redaction
  stops zero-width characters splitting an injection phrase past `redact_injection`.
- Quoted replies (`>` lines; `On … wrote:`, `-----Original Message-----`, `From: … Sent: …`; HTML
  `<blockquote>`, Gmail/Outlook quote containers), the `-- ` signature and footer phrases (new
  `excerpt_footers` list) are **moved after the new text, never deleted** (P4): the model sees the new
  text first and as much of the quote as the cap allows. A forged `From:/Sent:` line or `<blockquote>`
  can therefore reorder a scam, not hide it. Markers are attacker-writable; the instruction text says so,
  like `INJECTION_MARK`.
- Bounds (P22): prep runs on a bounded head of the body (64-256 KB, fixed in step 10); quote containers
  flagged in the HTML walker (a depth counter like `_hidden`), not by regex over the HTML source; line
  markers per §4.
- Gate key (P18): an `excerpt_prep` version joins `prompt_digest`'s inputs (as `MAX_INPUT_BYTES` does)
  and `INPUT_KEYS` in `ecf eval compare`.
- `ecf eval run --no-excerpt-prep` (not for the gate).

**D. Message signals** (fixed wording, no domains or names, ADR 0026): "A link's visible text names a
different site than the link goes to." and "The email has an attachment that can run code or open a web
page." A new group in `signals.WORDING` (`signals.py:20`) with `--no-message-signals`. The prompt
digest changes, which restarts A, B **and C** gates (the Claude pin digest includes the signals template,
`claude_pins.py:140`) (P23). Budget as a running total (P23): probe headroom 1,829 tokens; the decided
budget 1,500; used so far ≈ +300; D adds tens of tokens on the emails where it fires (estimate, measured
in step 11), reported as uncached tokens per call p50/p95 and the fraction of calls with a signals block.

## 7. Out of scope

- **Network:** this plan adds no network calls. Facts are local; links are compared, never fetched.
  Pre-classification calls stay the IMAP fetch and the cached, budgeted DKIM/DMARC lookups
  (`dnscache.py`); Ollama is loopback. The DNS cache cap (TTL capped at the check interval) stays as is
  (operator 2026-10-10).
- Not added: sender history as a hint to the model (it pulls the model toward routine on exactly the
  compromised-vendor case; groomable; a new disclosure to Anthropic on B/C; not measurable in eval), PDF
  parsing, Bayesian or rspamd scores, model summaries, network lookups.
- **Cut by Phase R:** a computed category (P1: a model-derived fact under ADR 0026, no route gain,
  scoring only); classification reuse (P11: each request carries a random token, `classifier.py:119`;
  no evidence of duplicate excerpts; SPEC §1.2 Later, step 0); a per-sender IBAN history (P12: a
  migration and an export break for no measured gain; a keyed hash of IBANs per sender is a Later item,
  it is the only way to catch a compromised vendor that always sent an IBAN, S7).
- Skipping the classifier for bulk senders: SPEC §1.2 Later only (step 0): rules 7 and 8 need the
  model's `category`, `sender_type` and `requires_action`.

## 8. Held-out set

`tests/eval/heldout/` (same format, `labels.jsonl`), run with `--root tests/eval/heldout
--not-for-gate` (operator decision 2026-10-10: the frozen set, run 0 and the C baseline stand). Labelled
blind by the operator **before any code**, then frozen. About 30 cards.

**Authors (P10, M4):** read-only agents given only SPEC §16.1 (labelling rules),
`GENERATE-FAKE-TESTING-EMAILS.md` and the card list below with a one-line brief ("business look-alikes
and new wordings of gift-card, credential, W-2, bank-change and crypto scams"); **not** this document,
`keywords.yaml` or `signals.py`. Each card's notes say so.

- Genuine look-alikes: a 2FA notice "never share this code"; a helpdesk "reply with your ticket number";
  a staff gift-card order receipt; an HR gift-card thank-you; a real vendor bank-change letter; a payroll
  provider W-2 notice; an exchange withdrawal; "send us the code from your invoice".
- New scams: one or two per pattern, worded differently from the set.
- Threads: a scam ask above a quoted genuine thread; a genuine reply quoting an old invoice; a scam below
  one benign line inside a forged `From:/Sent:` block; a scam inside `<blockquote>`/`gmail_quote` in an
  HTML-only body (P4).
- Links: a mismatched-link phish; genuine marketing with tracking redirects.
- Attachments: an HTML-attachment phish; a genuine PDF invoice; a long filename ending `.html` (P24).
- History: a seen sender with auth failing and `payment_related: false` (so the new fact is the only
  route, M12); a confirmed-invoice sender asking for a bank change.
- Entities: a staff-impostor ask with only an amount; genuine receipts in `1.234,56 €`, `USD 1,200`,
  `£50` from a seen sender; a known vendor's new IBAN without change wording (documents the known gap,
  §7).

**Realistic authentication** (operator 2026-10-10): the frozen set has `auth_result: none` everywhere.
Held-out cards set it as an expected fact: `fail` for spoofs of real or known senders; `pass` for
lookalike or attacker-owned domains and genuine mail; `none` only where the domain plausibly publishes
no DMARC. Each card's choice is in its notes. An expected `pass` does not recompute the triggers (SPEC
§16.1).

**Power (P10):** with ~16 genuine cards, 0 false positives still allows a rate up to ~20% (Wilson). The
held-out run is a smoke check. The false-positive measure is a rules-only pass over the corpus's stored
facts and excerpts (`evalrun.deterministic_noise`, no model; excerpts are truncated).

**Contamination (P10):** if a genuine card trips a rule, stop and report; a narrowed keyword list is
validated on a fresh batch (held-out 2), never on the batch that tripped. Held-out labels never change.

## 9. Phase R: adversarial review

**R1, this document — done 2026-10-10.** Four read-only Fable agents (safety S1-S13, measurement
M1-M14, cost and design C1-C16, performance perf-1..perf-10), merged into P1-P28 (3 critical, 12 high,
8 medium, 5 low); key claims spot-checked against the code (ADR 0026 wording, pre-check reads triggers
only, trigger 1 bank + change, random token per request, `fold` digit mapping, `payment_history` never
written, scan time 3.9 s per 1 MB × 2). All accepted as recommended (operator 2026-10-10). Findings in
`state-archive/pre-classifier/review-findings.md` (gitignored).

**R2, the code**, before each Ollama stage run: two read-only Fable agents in parallel, safety and
correctness (I1-I4, evasion, wiring, eval scoring), and performance (hot paths, worst-case regex
including `.*`, `\s*` and unbounded `*` in keyword-derived and hand-written patterns, and in stage 2 the
prompt and cache). Findings fixed or decided before the run.

## 10. Steps

Each committed with the operator's OK; full suite before each commit request. One CHANGELOG line per
commit that changes behaviour (CLAUDE.md), not one for the whole plan (P19).

- **00. CLAUDE.md rule** (operator decision 2026-10-10): an "Adversarial review" bullet: every plan that
  changes the classifier, rules, policy, facts or eval gets Phase R (four Fable agents: safety,
  measurement, cost and design, performance; findings numbered, the operator decides, the plan is
  amended); before any gate eval run, two Fable agents (safety and correctness, performance) review the
  diff. Written on `main`, merged into `v2.1.0-dev`.
- **0. SPEC §1.2 Later** (operator request 2026-10-10): "reuse of classifications for bulk templates"
  in the Later row and a paragraph after "Grouped model calls": why it isn't a plain skip (each request
  carries a random token; the model is not deterministic, replicate 32/257), what still forces a model
  call, a read-only hit-rate count over stored excerpts first (P11), then an indexed column, not JSON
  (perf-5); try shorter keys and grouped calls first. Also "keyed hash of IBANs per sender" (§7).
- **1. Docs (decisions only):** OD-488, OD-489. SPEC "as built" paragraphs are written with each step,
  not here (P19). Sections touched over the plan: §7.2 facts (which are fetch-time), §8.5 triggers, §8.6
  rule 1, §11.10 (rewrite "v2.1.0 adds no fact or field rules can use": the new facts are rule-readable;
  after a rollback a custom rule using one gives way to the starter rules with a System Error), §15.2
  audit data, §16.2 eval scoring, §21.2 entries; the classifier plan §4.
- **1a. Signals:** `credential_request` removed from `signals.WORDING` (§1 decision); signals
  allow-list test, prompt digest in `release.json`.
- **2. Held-out set** (§8), before any code; keyword lists frozen first (§2).
- **2a. Scan speed** (§4): dedupe, grouped alternation, 10 MiB timing tests; no behaviour change
  (same facts on the frozen set, checked).
- **3. Facts and keywords:** `gift_card_codes_request`, `credential` plurals, Mn removal, entity
  patterns on `normalize()`, IBAN as a `bank` hit, `WALLETS` on `normalize()` (+ keywords digest);
  `credential_never` measured; `tests/test_triggers.py`, `tests/test_facts.py`, evasion tests.
- **4. Routes:** fraud-trigger reasons (§3), `known_sender_auth_regressed` (§5 A),
  `ruletest.with_expected`; `tests/test_triggers.py`, `tests/test_rules.py`, `tests/test_precheck.py`;
  extend the `FACTS` strategy in `tests/test_policy.py` (:40-51) with every fact rule 1 reads
  (`impersonates_internal`, `bec_opener`, `tax_form_request`, `self_sent`, `gift_card_keyword`, the new
  ones) and run I1-I4 over several rule sets (starter, v2.0.0 starter, a hand-written custom set) (P17).
- **5. Link and attachment facts** (§5 C, P22, P24): `htmltext.py`, `message.py`, `policy.py`
  (`risky_attachment` blocker), rule 1b clause; hostile-HTML timing tests.
- **6. `amount_mentioned`** recorded (§5 A2), bounded patterns.
- **7. Eval tooling (P20, P21, P27):** a committed `ecf eval rescore --synthetic --through-policy`
  (recompute facts as `_inputs` does, re-plan the recorded classification; records `rescored_from`,
  `keywords_digest`, rules digest); `compare` warns on different keywords and different scoring;
  `genuine_hide_dropped` count (confirmed non-fraud cards expecting a hide-capable rule whose plan lost
  every hide), gated at 0; `model_only_fraud_cases` before/after in the summary; the per-stage
  measurements of §11; `_remember_root` skipped for not-for-gate runs; tests.
- — **End of stage 1** (deterministic, plus the one signal removal in step 1a).
- **10. B** and `--no-excerpt-prep` (`message.py`, `htmltext.py`, `keywords.yaml`, prompt digest
  inputs); quoted, forged-marker, Outlook, Gmail, signature, footer, all-quoted, multi-part,
  zero-width-split injection now redacted; 10 MiB timing tests.
- **11. D** and `--no-message-signals` (`signals.py`, `evalrun.Options`); signals allow-list; prompt
  digest in `release.json`; budget probe re-run.

## 11. Verification

- Targeted tests per step; ruff, pyright strict, lint-imports; full `uv run pytest -n auto -rs` before
  each commit request (0 skipped for the merge gate).
- **Noise (P7, M6):** with n = 267, the smallest effect this set can show is about 5 points end to end
  (about 13 net discordant cards); a claim below that reads "not distinguishable from noise". The noise
  reference is the replicate pair `a0462682`/`112ca628` (32/257 cases differ). Each stage is gated
  against run 0 as well as the previous stage, so −5 margins don't chain.
- **Stage 1 offline evidence (P8, M10)**, before any Ollama run, from the step-7 tool: an attribution
  table per mechanism (fraud reasons, IBAN-as-bank, `known_sender_auth_regressed`, `risky_attachment`,
  Unicode changes) listing the cases whose rule or actions change on run 0, run 1 and the repeat; three
  figures per rescore (rule/actions changed, `model_only_fraud_cases` before/after,
  `genuine_hide_dropped`), 0 unsafe; expected: one rule change (`mid-giftcard-staff-rewards`) on run 1
  and the repeat, none on run 0 (M2), or the difference explained.
- **Ollama** (peers asked first, `api/ps` checked, `accept` restarted from `../ecf-v2.1`, on AC):
  - **Held-out run first** (P8): no genuine look-alike routed to `fraud_guard`; every new scam routed to
    `fraud_guard`; the forged-quote and blockquote scams keep rule 1. Reported as a smoke check with its
    Wilson bound.
  - **Stage-1 live run** (frozen set, all on, `credential_request` signal removed per §1): gate against run
    0 on model `category` both sides (Newcombe lower bound > −5, 0 unsafe, recall 95/95, hide 0, `none`
    ≤ 2/71, `genuine_hide_dropped` 0, token budgets). It is also a wiring check: its rules and actions
    must equal the step-7 rescore of its own recorded classifications; any difference is a bug.
    Step 1a changes the prompt, so this run is also the measurement of removing the signal.
  - **Stage 2 (P6, M5):** offline first, count the frozen cards whose classifier excerpt `prepare()`
    changes and whose request D changes; run those cards live both ways as a paired subset. D's
    evidence is the held-out cards and the corpus (no frozen card fires it). Stage 2 lands only if
    non-inferior to stage 1 and to run 0 (OD-486), 0 unsafe, recall N/N, and **no card leaves rule 1**
    between `--no-excerpt-prep`/`--no-message-signals` and all on (per-card check, P4, S4); otherwise held
    back. A full all-on run, if made, is recorded as a replicate.
  - **Run 3** (`--no-signals --not-for-gate`) as in the classifier plan; **C final** against `d880a988`
    (paid, operator's go-ahead), noting its expected-facts caveat (SPEC §21.2).
- **Measurements per stage (P21, perf-10)**, in every result summary:
  - Offline: trigger scan time per message p50/p95/max and the number of compiled patterns; fact and
    prep time per email p50/p95 (run 0's recomputed offline as baseline); `senders` and `items` row
    counts in the environment block.
  - Ollama, per role, cold starts excluded: prompt, **uncached** and cached tokens p50/p95/max; calls
    with cached < 0.9 × system-prompt tokens, split "after an actor call / other", with prompt time for
    each group (the p95 rise 7.2 → 11.3 s in run 1 is cache restores after actor calls, not the prompt,
    perf-3); output tokens p50/p95 (must be unchanged); latency split into prompt and eval time;
    fraction of calls with a signals block and its size (D); classifier excerpt length p50/p95 and the
    count of excerpts prep changed or left by fallback (B).
