# TypeSafe Jev: what we tested, what we found, why v3.0.0 doesn't use it

Summary written 2026-10-15. Sources: SPEC §7.9, §7.10, §21.2 (step 3 and the matched evals) and
the OD table (OD-503 to OD-516); reports in `state-archive/jev-step3/` (gitignored). Every figure
is on the frozen synthetic eval set (304 cards, `43f650c9…`); no real mail ever reached TypeSafe.
"C" is the all-Claude preset (Claude classifies and acts); "D" is Jev classifies, Claude acts.

## Bottom line

- **Jev alone is about 15 points less accurate than Claude.** On the matched head-to-head test
  (same code, set, rules, scoring and pinned actor, same day), D got **211/304 (69.4%)** and C
  **256/304 (84.2%)**: −14.8 points, Newcombe 95% [−19.6, −10.1], McNemar p 1.4e-9 (7 cards right
  only under D, 52 only under C).
- **Jev is as safe as Claude on the hard gates, and better at catching phishing.** Both had 0 unsafe
  outcomes and fraud-guard recall 104/104 in every run.
- **Making a Jev-first setup as accurate as Claude means sending about 60% of mail to Claude
  anyway.** At that point Jev saves little: the mail still waits for Claude, still costs most of
  Claude's price, and is now exposed to two providers instead of one.
- **Decision (OD-516, operator 2026-10-15):** Jev is shelved. v3.0.0 ships preset C only; preset D
  is built but held back and dormant; Jev stays eval-only. The operator's rule: Jev is used only if
  it replaces Claude as the classifier on its own. Jev and Claude are never mixed for
  classification: no hybrid routing, no Jev alarms beside Claude.

## What we ran

| Stage | What | Runs |
|---|---|---|
| Step 2 (OD-503 to OD-505) | Adapter, key handling, live check against the real API | 39 live calls on invented text |
| Step 3 R1 | Noise: the default setup four times | 4 Jev runs |
| Step 3 R2 | Six variants (J1-J6) against the default: prompt wording, redaction off, no signals, `fraud_risk` as a choice, reversed option order, injection question | 18 runs |
| Step 3 R3 | The committed setup (with an "unsure" band) against C | 2 + 2 |
| Step 3 R6 | Rewording Jev's weak fields (K1, K3, K5) and a newer Jev version (`jev-1.14.0`) | 16 |
| Offline | Routing tables: Jev first, Claude for the least certain (OD-513) | rescoring only |
| Matched evals | Preset D against preset C, head to head | 2 D + 1 C |

All runs used `jev-1.13.0` unless noted.

## Where Jev is strong

**Phishing recall.** Jev catches more phishing than Claude, consistently across runs.

| | Jev | Claude (C) |
|---|---|---|
| `phishing` recall, matched test | 96% | 90% |
| `phishing` recall, R1 (4 runs; C from one run) | 89/93 every run | 89.2% (83/93) |
| Jev − C, R3 pairings | +6.5 to +7.5 pts, lower bounds +0.9 to +1.8 (significant) | |

Claude's phishing precision was higher (R1: C 97.6% vs Jev 90.8%), so Jev's extra catches come
with some extra false alarms.

**Safety gates.** Jev passed all of them in every run: 0 unsafe outcomes, fraud-guard recall
104/104, and 0 injection misses on the 25 injection cards, even with redaction turned off (R2).

**Fixed safety items.** Recall was steady run to run (R1, four runs):

| Item | Recall |
|---|---|
| `claims_internal` | 17/17 |
| `private_individual` | 21/25 |
| `bank_detail_change` | 18/23 |
| `regulator_claim` | 8-9/13 |

**Speed and cost.** Jev is far faster and cheaper:

| | Jev | Claude C run |
|---|---|---|
| Wall time for 304 cards | 40-45 s | about 10-15 min |
| HTTP p50 / p95 per request (warm) | 100-104 / 156-172 ms | n/a |
| Cold connection (10 min idle) | median 203 ms, p95 332 ms | n/a |
| Cost per 304-card run | about US$0.03 (~711k tokens) | about US$4.7-6.2 |

**Stability.** No faults in any step 3 call, and the result was steady across runs: the four R1
runs scored 228-234. The answers are not deterministic, though: the same card's probabilities
moved by up to 0.21 between runs.

## Where Jev is weak

**Overall accuracy.** Jev was behind Claude in every pairing we ran:

| Comparison | Jev | C | Jev − C (pts, 95%) |
|---|---|---|---|
| Matched test (D vs C) | 211 | 256 | −14.8 [−19.6, −10.1] |
| R1, no band (4 runs × 2 C runs) | 228-234 | 253, 259 | −6.2 to −10.2 |
| R3, committed setup with band | 216, 220 | 253, 259 | −10.9 to −14.1 |

Our bar for "as good as" is a lower bound above −5. Jev never cleared it.

**Fields that decide what ecf does with an email.** On the matched test, these were the cards each
side got wrong (Holm-significant):

| Field | Jev wrong | C wrong | Note |
|---|---|---|---|
| The rule chosen | 33 | 5 | the downstream effect of the fields below |
| `requires_action` | 28 | 6 | Jev's three-question form: recall 97%, precision 60% (C 90% / 80%) |
| `priority` | 28 | 9 | |

R1 (before the rewording) gave the same picture: `category`, `priority`, `requires_action` and the
rule were Jev's Holm-significant losses against C, and Jev's `requires_action` recall was 49%
against Claude's 92%.

**`fraud_risk` calibration.** Jev rates fraud risk too low on 19-23 cards per run (R2, default
setup). Asking it as a choice instead (J4) cut that to 12-13, which was the only R2 variant to pass
its test. Rewording (K5) cut over-rating (36 → 20 cards), but lost 9 rule flags or escalations and
dropped 5 `low` cards to `none`, which is a real loss of caution. K5 was rejected.

**Sensitivity to how the question is asked.** Results moved with wording rather than with the
email:

| Variant | Effect |
|---|---|
| Reversed option order (J5) | `category` agreement with the default run fell to 0.90-0.91 |
| Instruction wording (J2) | `requires_action` agreement fell to 0.76-0.79 |
| Injection question (J6) | 33% false positives |

**The "unsure" band costs accuracy.** Holding Jev's low-confidence answers for a person caught
about 31% of cases (93-96 per run), and Jev's score fell from 228-234 to 216-220.

**Newer isn't better.** `jev-1.14.0` (a preview version) scored 192 alone, against 213-225 for
1.13.0. Its hybrid lower bound was −7.6. Rejected; we stayed on 1.13.0.

## Why we aren't using it: about 60% of mail still goes to Claude

The only way Jev reached Claude's accuracy was as a first pass: Jev classifies everything, and the
cases where Jev is least certain go to Claude (OD-513). How much has to go to Claude:

**Defer on low margin in any field** (R3 runs; `escalation-r3-union.txt`, in-sample):

| Threshold | Sent to Claude | Correct /304 | vs C (pts) | Worst pairing LB |
|---|---|---|---|---|
| 0.0 (Jev alone) | 0% | 218.0 | −12.5 | −18.9 |
| 0.1 | 44% | 243.0 | −4.3 | −8.7 |
| 0.2 | 66% | 253.2 | −0.9 | −4.0 |
| 0.3 | 78% | 256.5 | +0.2 | −2.6 |
| 0.4 | 86% | 257.5 | +0.5 | −1.2 |

Out of fold (threshold chosen on one half of the cards, read on the other), 0.25 sends **68-75%** to
Claude, worst lower bound −4.8. 132 of the 304 cards sit within 0.16 of that threshold, so routing
flips run to run for many emails.

**Best routing found** (defer only when a safety field is low-margin; on the K1 reworded runs,
`routing-k1.txt`):

| Rule | Sent to Claude | Out of fold |
|---|---|---|
| Safety fields only, margin < 0.6 | **~59%** | passes both folds (worst −4.1 / −3.1) |
| Safety fields only, margin < 0.5 | ~50% | fails fold B (−5.9 / −5.0) |
| All fields, 0.25 (after K1) | 65-66% | passes |

Even the best routing that holds up sends about 60% of email to Claude. What that leaves:

- **Speed:** most mail still waits for a Claude review round, so the quick turnaround that was D's
  case applies only to about 40% of mail, and not reliably to the same 40%.
- **Cost:** Claude is still paid for about 60% of classifications, plus every action. Jev's
  US$0.03 per run is noise beside that.
- **Accuracy:** at best equal to C. No routing beat C by a meaningful margin.
- **Privacy:** every email goes to TypeSafe, and most also go to Anthropic. That's two providers
  where C needs one. The privacy statement allows only the destinations ecf needs.
- **Complexity:** two classifiers, a routing threshold that flips cards run to run, Jev-down
  handling, a second key, a second gate key and a second set of evals to keep current.

"Claude classifies, Jev adds alarms" (100% to Claude, Jev's phishing flags on top) kept C's 256/304
and raised phishing catches to Jev's level (178/186 across the pair). It also added about 20 extra
escalations on genuine mail per run, and it is a mix of providers that the operator ruled out
(OD-516).

## What would bring Jev back

- A future Jev version that **replaces Claude on its own**: preset D against preset C on the
  matched test, non-inferior at 5 points (Newcombe lower bound above −5), with the same safety
  gates.
- Held-out batch 3 (`tests/eval/heldout3`, 30 blind cards, labels confirmed 2026-10-15) is kept for
  that re-test; R4 was never run.
- Jev on AWS Bedrock is the stated path to revisit if TypeSafe offers it (CLAUDE.md, Model choice).
- Preset D's code stays on `main`, dormant (SPEC §7.10), so a re-test needs no rebuild.

## Limits of this evidence

- Synthetic cards only, written by us; real mail may differ in either direction.
- The routing tables are mostly in-sample. The out-of-fold figures are noted where they exist.
- The noise runs used one Jev version (1.13.0) over two days; TypeSafe can change `jev-latest`
  without notice (60 days' notice before deprecating 1.13, per its 1.14 announcement).
- Retries, backoff, the breaker and the waiting budget were never exercised, because no Jev call
  failed in step 3.
