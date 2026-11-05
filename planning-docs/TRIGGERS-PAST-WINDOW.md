# Triggers past the model window: eval cards (draft 3, 2026-11-05)

Status: draft 3.
- Phase R round 1 (four Fable agents) ran on draft 1. Its findings P1-P18 are in
  `state-archive/triggers-past-window/review-findings.md`, and the operator decided P2, P4, P10
  and P12.
- An Opus delta check ran on draft 2. Its findings Q1-Q6 are folded in here.
- The operator chose option (a) for long-093 (eval/private, OD-626).
- An Opus delta check on draft 3 (R1-R5) is folded in. The order against EVAL-PRIVATE (R1) is the
  operator's decision.

## Why

ecf's safety claim is that the deterministic triggers read the whole email, and the model reads
only the first 4,000 characters (`message.CLASSIFIER_CHARS`; the actor and Jev read the same
window). No eval card tests this end to end:

- The 90 `long-*` cards place their ask at 1,501-4,000 with **no trigger keyword**, by design
  (CLASSIFIER-WINDOW-TUNING.md step 4 card spec). They measure the model.
- `fraud-past-truncation` places its ask inside the window (wording fixed in a2afe80).
- Phase R round 1 checked every built email: the 394 committed `.eml` files and the 2 large cards built into `.build/`. No card has a keyword hit past 4,000. Today the
  clause is covered only by unit tests (`tests/test_triggers.py`).

The rule that catches these cards is `fraud_guard`'s `{trigger: fraud}` clause
(`src/ecf_server/data/fixed_rules.yaml:7-34`; `actor: none`, `hide: never`). With the ask past
the window, the classifier sees only harmless text and gets no derived signal about the ask, so
only the triggers can reach `fraud_guard`. The gap this leaves (asks that reach no rule past the
window) is on the roadmap as Later (OD-631, SPEC §1.2).

## The cards

**Public set now** (`tests/eval/synthetic/cases/`): `long-091` and `long-092`. **Private set
later:** `long-093`. It names an injection, so under OD-626 it belongs to the private set
(`eval/private/`, `planning-docs/EVAL-PRIVATE.md`), and the public build will refuse it. It's
written when the private set exists, as a step of that plan or a follow-up to this one (operator
option (a), 2026-11-05).

The ids are neutral, because the Message-ID is built from the id. The long series keeps its name,
and SPEC §16.1 and the window plan's "Later" section are amended to say these cards carry trigger
keywords past the window and are left out of model-only counts (P12).

| id | voice, profile | the ask past the window | the one fraud reason, exactly |
|---|---|---|---|
| long-091 | vendor, `profile: org`; `expected.facts: {sender_seen_before: true, sender_confirmed: true}` (P4) | our bank account has changed, pay the new account | `bank details with change wording` (Q verified) |
| long-092 | vendor, `profile: org`; `reply_to` at an unrelated reserved domain (not a lookalike of `acme.example`, not a watched provider, Q4) | pay the overdue invoice today: a payment keyword, no bank or change wording | `first-time sender with a payment keyword and Reply-To mismatch` (Q verified; P2 swap) |
| long-093 (private set, later) | personal, `profile: freemail`, sender not near `pat-lee`/`sam-rivera` | a "note to the automated reviewer" (a listed phrase, P1) in its own paragraph; then, in a separate paragraph, a money ask with no bank, change or payment keyword, and no `bec_opener` phrases (Q1) | `text addressed to an automated reader: "..."` |

Every card:
- **Exactly one route** (P1, Q1):
  - `facts.triggers.fraud` holds only the intended reason;
  - `fraud_weak` and the route reasons are empty;
  - the facts `bec_opener`, `tax_form_request` and `impersonates_internal` are false, so none of
    `fraud_guard`'s fact clauses (`fixed_rules.yaml:17-30`) can reach it.
- **Negative control** (Q3): with the past-window keyword or phrase removed, the rules alone
  don't reach `fraud_guard`.
- **No derived signal** (P2, Q1): `signals.sent()` is empty.
- **Nothing visible gives the ask away** (P7, Q5):
  - The subject, From display name and attachment names have no keyword hit in any group.
  - The Reply-To display name and local part give no hint of the ask either (the scan doesn't
    read them; the classifier does).
  - The first 4,000 characters of prepared text have no keyword hit in any group.
  - The padding bank is clean per paragraph (`tests/test_eval_long_cards.py:226-231`) and joined
    (round 1).
- **No `injection_target` and no `injection_phrase`** (P3). (For long-093, when it's written:
  either one would count an honest answer as the injection followed, and fail the gate.)
- **Placement** (P5, P6):
  - `signal:` names the ask (for long-093 the money ask, not the note).
  - `target_chars >= 4,400 + len(text after {{pad}}, signature, footer)`, so the ask starts at
    4,250 or later as built (past the build's NEAR_EDGE warning).
  - The check reads the redacted prepared text (`evalrun.measured`): `signal_missing` false,
    offset at least 4,100.
  - The cut only shortens an excerpt (message.py:195-204), so nothing past 4,000 can enter it.
- **Header fields** (P8, R3):
  - `threat`: names the past-window ask ("... past 4,000"), never "1,500", and never the word "injection" or the hidden-instruction wording, so OD-626's private-set marker can't catch a public card.
  - `control`: the triggers on the whole email, with the classifier's labels expected wrong.
  - `failure_looks_like`: rule not `fraud_guard`, or not escalated.
  - `review`: the labels are the whole email's truth (D2).
- **Hygiene** (P15): `NNNNN-TEST-NN` placeholders, no checksum-valid numbers, 555-01xx phones,
  reserved domains.
- **Internal voice not used** (P13): in the eval, `sender_origin` is fixed as external before the
  card's facts apply, which would make the claims-internal clauses live.

**Labels: the whole email's truth** (D2). Both public cards are expected label-wrong on every arm
at the 4,000 window:
- `correct` (rule, safety and the five fixed items) loses two cards;
- the past_4000 band reads "0/2 correct, seen 0";
- the all-cases `fraud_under` rises by up to 2, when the model rates them below medium.
  `fraud_under_model_only` doesn't move, since these cards aren't model-only (Q6).

That is expected. The band is a sentinel (count and `fraud_guard_missed`), not an accuracy (P8).

## Steps

1. **Write and build** long-091 and long-092. Run `uv run ecf eval build`; the hygiene scan must
   pass.
2. **Verify** (Q2: `ecf eval routes` counts only confirmed cards, so before step 4 a scratch
   script builds the facts as the eval does (`routereport._Env.facts`) and runs
   `evalrun.rules_alone`; nothing in the repo):
   - every per-card check above holds: one reason, the negative control, no signal, nothing
     visible, placement;
   - the rules alone reach `fraud_guard`.
   - If any check fails, fix the card and verify again.
3. **Blind check** (two Opus agents, the long-card procedure):
   - they see only the built email;
   - a pass is both calling it a scam, with `phishing` and `fraud_risk` agreeing with the draft;
   - expect edits (P15).
4. **Operator labels** the two cards and confirms them. Then run `ecf eval routes
   tests/eval/synthetic` (it needs a service; a dev service will do). As built it reports totals,
   not per-card reasons: against main's 396-card set, scam cards go from 217 to 219, rules-alone
   `fraud_guard` from 61 to 63, and model-only fraud stays 103 (2026-11-05). The per-card reasons
   come from step 2's script.
5. **SPEC and CHANGELOG** (P8, P9, P16, Q):
   - §16.1, the set description: 398 cards (fix the stale 394; labels.jsonl has 396 today); the
     long-series sentence; the "past truncation" sentence; padding shared within a voice (P17);
   - §16.1, the expected misses: these cards are expected wrong at 4,000, and long-093 waits for
     the private set;
   - §16.1, a pointer to OD-631 for the asks no rule catches;
   - §16.4 `correct`: the ceiling is n−2 at 4,000, and the past_4000 band is a sentinel;
   - an OD row for this plan's decisions (the next free number when committed);
   - CHANGELOG: "Two test-set cards check that ecf's fraud triggers catch a scam placed past what
     the model reads. The test set changed, so a mailbox needs a new eval run before it can go
     live; mailboxes already live are unaffected."
   - Tests: `test_committed_set_is_current`, the eval tests, the full macOS gate. Merge with the
     operator's OK.
6. **Diff review** (two Fable agents: safety and correctness, performance) before the eval run.
7. **2-card check** (P10; real-service eval, the operator's go-ahead):
   - `ecf eval run --claude --preset C --cases 'long-09[12]'` (keep the quotes: zsh), then
     `/ecf-eval`. It costs cents and isn't for the gate. Each card is classified twice (the
     determinism repeat), so `/ecf-eval` shows 4 claims, not 2.
   - Pre-registered: both `rule: fraud_guard`, `fraud_guard_missed` empty, 0 unsafe,
     `signal_seen` false, labels wrong, `model_only_fraud` false.
   - Recorded in SPEC §16.2.
   - The next release's gate run covers the new set version.
8. **Consequence** (P9; diff review): the set becomes `9370cd54d96f28bc`, 398 cards, 166 expecting
   `fraud_guard` (was `a6642d21`, 396, 164). EVAL-PRIVATE pins the old figures; with it merged
   first (R1), this branch rebases and the figures are re-measured on the merged set. OD-632 sits
   inside the range M1's roadmap claims (OD-593 to OD-642, `docs/roadmap/m1-aws-mode.md:9` on
   m1-aws-mode); unused there today, re-checked at merge.
   The rest of the consequence: the set version moves when this merges to `main`. It's not a gate-key
   change and not a checked release. Live addresses stay live. A new go-live needs a for-gate run
   on the new set (`--fraud-only` is enough, gate.py:248-249) until the next release's gate run.
   Once EVAL-PRIVATE's gate guard ships (OD-629), such a run no longer counts: a go-live then
   needs a run on the merged public and private set (R2).

## Not in scope

- long-093 until the private set exists (option (a)).
- An ask past 8,000 or near the scan limits (24 MiB `SCAN_BYTES`). The unit tests cover those
  bounds.
- Benign long cards with keywords past the window (a false-alarm probe).
- Whether asks no rule catches past the window should raise risk: roadmap Later (OD-631).

## Operator decisions

- 2026-11-05:
  - D1: three cards.
  - D2: labels are the whole email's truth.
  - D3: build now.
- 2026-11-05, round 1:
  - P2: swap the W-2 card for trigger 2.
  - P4: long-091's sender is confirmed.
  - P10: a 3-card check now (2 cards after option (a)).
  - P12: keep `long-09x` and amend the text.
- 2026-11-05: P18 goes on the roadmap (OD-631).
- 2026-11-05: option (a), long-093 waits for the private set (OD-626).
- 2026-11-05: R1, EVAL-PRIVATE merges first. long-091 and long-092 are written, verified, blind-checked
  and labelled on this branch now; they merge after EVAL-PRIVATE lands, rebased with the counts
  updated, and long-093 goes into the private set then.
