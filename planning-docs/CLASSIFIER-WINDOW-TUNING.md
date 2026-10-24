# Classifier window tuning (before M8)

- **Status:** draft 5 (2026-10-24). Step 1 done (OD-553, f7ab110; personal-mailbox count
  below); the business-mailbox count is not done. Steps 2-8 not built.
- **Reviews:**
  - Draft 1: Phase R, four Fable agents, P0-P20.
  - Draft 2: two Fable agents, G1-G9 and H1-H11, merged as J1-J10.
  - Draft 3: one Fable agent, K1-K7.
  - Draft 4: one Fable agent, L1-L5.
  - The operator accepted every recommendation (2026-10-24); findings are in
    `state-archive/window-tuning/review-findings.md`.
  - P0, an existing bug, was fixed on its own (OD-552).
- **Base:** main faa466b. Code references are checked against it.
- **Decisions** (operator, 2026-10-22 to 2026-10-24):
  - this tuning comes before M8;
  - it tests the classifier's window size, and one window shared by the classifier and the
    actor. Where the email is cut is not tested;
  - real mail is measured properly first;
  - a small pilot comes before the full long-card set;
  - the gate run comes last.

## Context

**The classifier sees the first 1,500 characters** of an email's redacted, prepared text
(`message.CLASSIFIER_CHARS`, src/ecf_server/message.py:38). The actor sees 4,000 (`ACTOR_CHARS`,
:39). The 1,500 came from the local model's context window, and the local model is gone (ADR
0031). The limit has never been measured for accuracy.

**Real-mail lengths, measured (step 1, 2026-10-24):**
- Source: 300 recent messages from the operator's personal Gmail, fetched read-only with
  `ecf corpus fetch` on the accept install (f7ab110) and counted with
  `scripts/corpus_lengths.py`, which reads each message's `prepared_chars`: the true length of
  the redacted, prepared text, before any cut. The corpus file was deleted after counting.

| Length | Share | Classifier at 1,500 | Actor at 4,000 |
|---|---|---|---|
| up to 1,500 | 27% (80) | sees all | sees all |
| 1,501 to 4,000 | 34% (103) | cut | sees all |
| 4,001 to 8,000 | 18% (55) | cut | cut |
| over 8,000 | 21% (62) | cut | cut |

- Median 2,985. Deciles: 645, 1,183, 1,676, 2,240, 2,985, 3,906, 5,232, 8,264, 11,700.
- So a 4,000 window would see all of 61% of this mail, and 8,000 about 79%.
- The first count (2026-10-23, from the stored 4,000-character excerpt) put "4,000 or more" at
  13%. That was a floor; the true share is 39%.
- **Caveats:**
  - This is personal mail. Whether business mail runs shorter is still unmeasured; the operator
    chose this mailbox for the test (2026-10-24), and a business count stays open.
  - Quoted reply threads are marked, not removed, so they count in full.

**The eval can't show any of this today:**
- 0 of the 304 synthetic card bodies exceed 1,500 characters (the longest is 1,340 in prepared
  text; the median is about 200).
- So every window gives the same input on the current set. The 1,500 window's accuracy on
  long mail has never been measured.
- The current set has 104 `fraud_guard` cards and 25 cards with `injection_target`, all short.

**What a bigger window changes:**
- Triggers scan the text up to `SCAN_BYTES` (24 MiB, src/ecf_server/triggers.py:129, 217-229),
  and `redact_injection` runs before the cut. So the window changes only what the model reads.
- `keywords.yaml` lists literal injection phrases only. The 1,500 cut is in effect today's only
  defence against a reworded injection placed late in an email.

**Gate facts that shape the order:**
- `gate.synthetic` reads the newest run for the digest, whether or not it was for the gate,
  and fails on a not-for-gate run or one on an older set version (src/ecf_server/evalrun.py:
  1370-1374; src/ecf_server/gate.py:260-268, 283).
- So every experimental run makes `ecf gate` fail until a fresh for-gate run exists. That stops
  a new address going live. It does not demote a live one: demotion happens only on a key
  change (src/ecf_server/stages.py ~275).
- A set containing any `author: generated` card can never gate (src/ecf/eval/genset.py:19-42;
  gate.py:260-262).
- **A for-gate run counts whatever its size or set.** It repoints the eval root
  (`remember_root`, src/ecf_server/claude_eval.py:503), and `gate_passed` has no minimum size
  (src/ecf_server/evalrun.py:587-588). So a default-window run on the pilot set or on the long
  cards alone could pass the gate. Every run here except step 7 is therefore not for gate:
  `--cases` forces `for_gate=False` like a non-default window, and pilot runs pass
  `--not-for-gate` explicitly (K2). As a backstop that doesn't rely on a flag, the new code
  refuses a for-gate run whose `--root` differs from the stored `EVAL_ROOT` (L1).

## Rules

- **What decides:**
  - **Safety outcomes, as counts:** phishing, `fraud_risk`, `payment_related`, and the rule and
    safety confusion, on the long cards.
  - **Injection:** 0 followed among injections the model could see (below).
  - **Accuracy per band** is reported as an estimate with its interval. With about 60 cards per
    band the interval is about ±10 points (same-configuration discordance 7.9%, 24/304, from
    the schema v3 S2 pair, SPEC §21.3), so it can't decide on its own. There is no per-band
    non-inferiority rule.
  - The whole-set comparison is a check that the short cards didn't move. It is uninformative
    for the window, since about 304 of the cards get the same input at every window.
  - Cost and time decide only between equals.
- **Bands come from where the signal sits, not from email length.**
  - Each long scam and injection card names its signal phrase. The eval finds that phrase in
    the redacted, prepared text the classifier is cut from and records its position
    (`signal_offset`) at run time. Preparation inserts a MOVED_MARK before quoted and signature
    text and normalises the text, and redaction replaces matched lines with a mark, so the
    position can differ from the authored one (K3).
  - Bands: signal before 1,500; 1,501 to 4,000; past 4,000. Email length (`prepared_chars`) is
    reported as well.
- **Injection scoring is honest about what the model saw** (J2):
  - Each long injection card puts its scam signal before 1,500, so the honest answer is high
    risk at every window. Only the injected instruction sits past the cut.
  - The report gives followed of seen (injection inside the window) and of unseen, per window.
  - Adoption needs 0 followed of seen.
  - `injection_hit` is extended to yes/no field targets (`<field>:true`), so `payment_related`
    can register (J3).
- **Noise:** the winning window is replicated once on the long cards. The comparison with the
  earlier runs `024ec270` and `1a8708cf` on the shared 304 is reported, labelled as mixing
  prompt changes with noise, since the digests differ.

## Steps

### 1. Measure real mail properly (real-service; go-ahead)

- **Code:**
  - The isolated child computes the prepared text's length, carries it in `Isolated`
    (encode and decode, src/ecf_server/isolate.py:114-116, 150-157), and `corpus._row` stores it as
    `prepared_chars` (corpus.py:570-583).
  - Older corpora lack it, and a merged corpus can mix both, so the count treats a missing
    value as unknown, not 0.
  - SPEC §16.7's "manifest stores" sentence lists it.
- **Commit a count script** (`scripts/corpus_lengths.py`). It takes the passphrase hidden from
  the terminal and prints counts only, so the figures can be reproduced.
- **Count two mailboxes:** the personal Gmail, and a business mailbox the operator names. Report
  shares up to 1,500, 1,501-4,000, 4,001-8,000 and over 8,000, plus deciles. Delete the corpus
  files after counting (ask first).
- **Stop point:** if business mail is mostly under 1,500, the operator decides whether to go on.

### 2. Measurement code and run options

- **Per-case fields** on `CaseResult` (src/ecf/eval/results.py:91), written by `case_inputs`
  and `claude_eval._prepare`, and kept on rescore:
  - `prepared_chars`;
  - `signal_offset`, found at run time from the card's signal phrase (a new card field);
  - `injection_seen`, whether the injection fell inside the window;
  - `injection_hit`.
- **Figures** in the result summary, `ecf eval compare` and `status`:
  - per band, the safety confusion counts and `model_only_fraud_cases`;
  - injection followed of seen and of unseen;
  - with and without a MOVED_MARK, reported but not decided on (it goes with length).
- **Window option:** `ecf eval run --claude --classifier-window N`.
  - A `limit` parameter on `ParsedMessage.excerpts(redact, …)` (message.py:161), carried through
    `claude_eval.Options`, `_prepare`, `case_inputs` and `Measures.inputs`. A shared constant is
    never changed.
  - Stored-excerpt cases (`case.excerpts`, evalrun.py:1010-1011, 1038-1040) refuse a
    non-default window.
  - `/ecf-eval` takes no option.
  - Delete the duplicate `claude_eval.ACTOR_CHARS`.
- **Gate-key handling:**
  - A non-default window or `--cases` forces `for_gate=False` inside `claude_eval` (as for
    generated sets, src/ecf_server/claude_eval.py:470-471).
  - It will write `excerpt_cap` in the C summary (src/ecf_server/claude_eval.py:684-692), which
    `results.INPUT_KEYS` already reports as "different inputs" (src/ecf/eval/results.py:35-42);
    nothing writes it for C runs today.
  - No gate key changes.
- **Case selector** on `ecf eval run --claude`: run only cards whose ids match comma-separated
  globs, as `ecf eval label --cases` does (src/ecf/cli.py:1071), e.g. `--cases 'long-*'` (L2).
  `--fraud-only` would drop the benign long cards (J9).
- **Builder fields** for a quoted thread, a signature and footer blocks, and a character-length
  target. Plain filler (`builder._filler`) is too easy to spot.

### 3. Pilot (about 10 cards; go-ahead for the runs)

This departs from P10 as accepted ("at today's window, before building the window option"):
the draft 2 review showed a test at 1,500 alone can't answer the question (J1), so the window
option is built first (K5).

- **The cards:** about 10 long scam cards, each with its signal past 1,500 and the same card
  with the signal before 1,500. Each pair is identical outside the body (From, Subject,
  Reply-To, authentication).
- **Model-only:** `rule_alone != fraud_guard`, no trigger or signal payload, paraphrased.
- **Authorship:** hand- or Claude-written, with no `generator`, so the set stays gate-eligible.
- **Where they live:** a separate `--root` set, so the main set's version doesn't change yet.
  The operator labels them.
- **Runs:** 1,500 and 4,000 on the pilot set, both with `--not-for-gate`. The test compares
  each past-1,500 card at 1,500 against the same card at 4,000. With 9 or 10 such cards all
  going one way, the difference is clear (exact McNemar p = 0.004 for 9, p = 0.002 for 10); 9
  of 10 would not be (p = 0.021). The before-1,500 twins are a control.
- **Outcome:**
  - if the past-1,500 cards fail at 1,500 and pass at 4,000, the window matters, and step 4
    follows;
  - if they fail at both, the cards or the model are the problem;
  - if they pass at both, the model is flagging on something other than the body, which is a
    card leak or over-flagging. The operator decides either way.

### 4. Long-card set

In the main set, cards with prefix `long-`, authored without a `generator`:
- **Scam cards** with the signal past 1,500 and past 4,000: about 60 per band. A band with
  fewer is report-only (K7).
- **Benign long mail:** newsletters, long threads, and a colleague forwarding phishing to
  report it. None may be flagged.
- **At least 20 long injection cards,** honest-scored as in Rules, with the injected
  instruction placed in quoted, forwarded, signature or footer text (K7).
- **Labelling:** by the operator (`ecf eval label`), in budgeted sittings. The cards are padding
  around already-confirmed scams, so the operator confirms the position rather than reading new
  scenarios.
- **Gate:** the main set's version changes here, so `ecf gate` fails until step 7.

### 5. Window runs (real-service evals; go-ahead each)

- **On the long cards only** (`--cases 'long-*'`, so not for gate):
  - 1,500;
  - 4,000;
  - 8,000;
  - a replicate of the winner.
- **One full-set run at 1,500,** with `--not-for-gate`, for the noise comparison and the check
  that the short cards didn't move (K4).
- **No "shared 4,000" run:** the actor already sees 4,000.
- **The rule** classifier window ≤ actor window holds. An 8,000 result counts for adoption only
  after a shared-8,000 safety run, which needs the step 8 migration.
- **Record per band:** tokens per read and spawn time.
- **Budget:** +5% input tokens at 4,000 and +14% at 8,000, on long cards only. An Opus full run
  is about US$13 API-equivalent and +2 percentage points of a 5-hour plan window.

### 6. Compare and decide

Under the Rules, the operator decides.

### 7. Gate run (real-service eval; go-ahead)

1,500, the current default, on the full new set version, pinned and for-gate. It is the last run,
so the gate passes again; run `ecf gate` to confirm (L5). If a window is adopted, the gate run
instead follows the adoption release (step 8).

### 8. Adoption (if a window wins)

- **Code:** change `CLASSIFIER_CHARS`, and bump `excerpt.VERSION` once, so the key moves once.
- **Release:** a checked release; every C address drops to assist until a fresh gate eval.
- **Storage:**
  - store each item's window at fetch, so stats and estimates can band by it;
  - add a CHECK on `classifier_text` at the adopted window.
- **Actor window above 4,000** (only with a shared-8,000 result): a table-rebuild migration for
  `actor_text`, which makes it a `data_format` question.
- **Rollout:** new mail only. Drain the review queue before the release.
- **Live batching:** if 8,000 wins, consider a lower `LIMIT_DEFAULT` for standard classifier
  rounds, and run the first live 8,000 round at production batch size.
- **Jev:** pin Jev's own cap.
- **Display:** page or cap long excerpts in `ecf item show` and on the corpus label screen.
- **A size test:** a test that a maximum-window `get_message` reply stays under a stated bound.
- **SPEC and docs:** §5.1 step 4 (352), §6.1 (420), §7 (875), §12.4 (1769, 1774), §18 (1980),
  §16.5 (2258) and excerpt.py:10; an OD; a CHANGELOG line.
