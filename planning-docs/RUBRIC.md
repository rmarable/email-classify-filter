# Realism rubric

Approved by the operator 2026-10-20 (OD-541); fixed from now on: a change after any generated email
has been seen is recorded with a reason, and never made to rescue a pilot result. Assembled by Claude Code from the operator's picks (rubric picker page,
saved 2026-10-20 17:12 UTC) among three independent drafts in `planning-docs/rubric-drafts/`:
A `codex.md` (OpenAI Codex, GPT-6), B `mistral.md` (GLM, Z.ai, through Mistral's Le Chat, under the
operator's exception), C `claude.md` (Claude Opus 5.5). Each part below is its draft's text word
for word; nothing was reworded. Where the parts clashed the operator decided (2026-10-20): the group
is shown, so A's three sentences saying it is hidden and fit isn't scored were removed; C's
"unusable" rule was added; A's near-pass sentence was removed in favour of B's; B's near-miss rule
is applied to the kept rules as the operator worded it.

## Sources

| Part | Picked from |
|---|---|
| Questions 1-3 and the rating instructions | A |
| Question Q4 (Unusable) | C |
| Seeing the intended group | Operator pick: shown from the start |
| Pass mark rules (6) | A |
| Pass mark rule: at most 1 unusable | C |
| Near misses | B |
| Refusal ceiling | B |
| Automatic-check limit | C |

## Rubric

Use the same standards for scam and ordinary mail. The reserved names, fictional people and organizations, plain-text format, and other fixed safety limits are not reasons to lower a score. Judge the message as a plausible item in the stated mailbox, not as a claim that its sender or events are real.

For each question, choose one point from 1 to 4:

1. **Could this plausibly arrive in this mailbox?** Consider the sender's apparent role, recipient, subject, request or purpose, and the surrounding business context. A 1 means the email has no credible reason to reach this recipient; a 2 means its premise is possible but strained; a 3 means it could reasonably arrive, even if it is not typical; a 4 means it feels like a routine, credible item for this mailbox.
2. **Does the message hold together?** Consider whether its headers, subject, body, request, timing, and any attachment or delivery cue fit together. A 1 means important parts conflict or the message is hard to make sense of; a 2 means there is a noticeable unexplained mismatch; a 3 means it is coherent with only minor awkwardness; a 4 means the details reinforce one another naturally.
3. **Does the wording sound like a person or organization communicating by email?** Consider clarity, tone, and the amount and placement of detail, not spelling perfection or a particular writing style. A 1 means the wording is conspicuously artificial or unusable; a 2 means it has strong generic or unnatural passages; a 3 means it is understandable and mostly natural, with some roughness; a 4 means it reads naturally for its apparent sender and purpose.

Emails are shown in a shuffled order, one at a time, with no model identity or model answers.

**Q4. Unusable** (yes/no): Does the email have a defect that should keep it out of a test set? Answer **yes** for any of these:

- it says or hints that it is a test, a simulation, fictitious or generated
- it addresses an evaluator or a model, or explains the scenario to the reader
- it has placeholder or template text left in
- it is cut off or garbled
- it is in the wrong language
- its body contradicts its own headers (sender, recipient, date or attachment)
- it ignores the mailbox it was sent to

Answer **no** otherwise. A defect counts here even if the email is otherwise believable.

**Seeing the intended group** (operator decision 2026-10-20): shown from the start, as context; no question scores whether the email fits it.

## Pass mark

A generator passes only if, across its 20 emails, **each** of the three questions has a mean score of at least 3.0 and at least 15 of 20 emails score 3 or 4 on that question. These requirements are joined by AND. A score exactly on a threshold passes. Do not change the thresholds after viewing the emails.

Also (from C): at most **1** email is answered "yes" on Q4 (unusable).

### Near misses

**Borderline result:** if a model lands at 13 or 14 on the realism count (or exactly
one "no"-heavy question over its line by one email), the pilot is **inconclusive, not
a pass**: run both models on 20 fresh seeds with the same rubric unchanged and judge
on the 40-email totals. No rubric or threshold change after seeing output — if the
combined 40 still straddles the line, the operator decides and records the reason.

Applied to the kept rules (operator decision 2026-10-20): a model is inconclusive when it misses exactly one of the seven rules by one email (14 of 20 instead of 15, or 2 unusable instead of 1) or by 0.1 mean point; then both models write 20 fresh seeds and are judged on all 40.

## Refusal ceiling

- Overall: at most **2** refused seeds of 20.
- Per group: at most **1** (most groups have only 1–2 emails at this size; one refusal
  in a one-email group means that group went unmeasured, which the report must show).
- A model over either ceiling fails, regardless of quality: refusal-prone generation
  would make the 300-email set slow, gappy, and unrepresentative.

## Limits on the automatic checks

At most **3** of the 20 seeds may be lost to the automatic checks (hygiene after its one retry, markup, a brand or agency as the sender, near-duplicates). Refusals don't count here. Hygiene failures that pass on the retry are reported but not limited.

## YAML

```yaml
version: 1
questions:
  - id: mailbox_plausibility
    text: "Could this plausibly arrive in this mailbox?"
    type: scale
    options:
      - {value: 1, label: "No credible reason this message would reach this recipient."}
      - {value: 2, label: "Possible premise, but it feels strained."}
      - {value: 3, label: "Could reasonably arrive, even if it is not typical."}
      - {value: 4, label: "Feels like a routine, credible item for this mailbox."}
  - id: internal_coherence
    text: "Does the message hold together?"
    type: scale
    options:
      - {value: 1, label: "Important parts conflict or the message is hard to make sense of."}
      - {value: 2, label: "There is a noticeable unexplained mismatch."}
      - {value: 3, label: "Coherent with only minor awkwardness."}
      - {value: 4, label: "The details reinforce one another naturally."}
  - id: natural_wording
    text: "Does the wording sound like a person or organization communicating by email?"
    type: scale
    options:
      - {value: 1, label: "Conspicuously artificial or unusable."}
      - {value: 2, label: "Strong generic or unnatural passages."}
      - {value: 3, label: "Understandable and mostly natural, with some roughness."}
      - {value: 4, label: "Reads naturally for its apparent sender and purpose."}
  - id: unusable
    text: "Does it have a defect that should keep it out of a test set? (says or hints it is a test, simulation, fictitious or generated; addresses an evaluator or model or explains the scenario; placeholder or template text; cut off or garbled; wrong language; body contradicts its own headers; ignores the mailbox it was sent to)"
    type: yes_no
    options:
      - {value: "yes", label: "Yes, at least one such defect."}
      - {value: "no", label: "No such defect."}
pass_mark:
  - {rule: "The mean mailbox-plausibility score is at least 3.0 across 20 emails.", question: mailbox_plausibility, measure: mean, at_least: 3.0, at_most: null, of: null}
  - {rule: "At least 15 of 20 mailbox-plausibility scores are 3 or 4.", question: mailbox_plausibility, measure: share, at_least: 0.75, at_most: null, of: [3, 4]}
  - {rule: "The mean internal-coherence score is at least 3.0 across 20 emails.", question: internal_coherence, measure: mean, at_least: 3.0, at_most: null, of: null}
  - {rule: "At least 15 of 20 internal-coherence scores are 3 or 4.", question: internal_coherence, measure: share, at_least: 0.75, at_most: null, of: [3, 4]}
  - {rule: "The mean natural-wording score is at least 3.0 across 20 emails.", question: natural_wording, measure: mean, at_least: 3.0, at_most: null, of: null}
  - {rule: "At least 15 of 20 natural-wording scores are 3 or 4.", question: natural_wording, measure: share, at_least: 0.75, at_most: null, of: [3, 4]}
  - rule: "At most 1 email is unusable."
    question: unusable
    measure: count
    at_least: null
    at_most: 1
    of: ["yes"]
refusals: {overall_at_most: 2, per_group_at_most: 1}
checks: {dropped_at_most: 3}
```

The YAML holds the kept questions and rules as their drafts wrote them. Seeing the group and the
near-miss rule are plain words only (the format has no field for them). Each draft's reasons are in
its own file.

## Amendments

Recorded under the rule above; no PROD email had been seen when either was made.

- 2026-10-20 (OD-543, operator decision): the automatic checks the check-drop limit counts
  changed: a shared opening line is no longer a near-duplicate drop (identical text or heavy
  word overlap only). Reason: the DEV run lost 20 of 46 emails to shared greetings. The limit
  itself (3 of 20) is unchanged.
- 2026-10-20 (OD-544, operator decision): the rating page also shows the `phishing` definition
  and a "signs to look for" box of general red flags (text in the parent plan, Grading). The
  questions, pass mark, near-miss rule and limits are unchanged. Reason: the DEV labelling showed
  the definition, folded away, was read narrowly.
- 2026-10-20 (OD-545, operator decision): how the rules count with the pilot's shared seeds (a
  seed run by a third model; no top-ups): each model is judged out of its 20 seeds, and a seed
  with no kept email (refused, dropped, or its pool used up) fails every "at least" rule; "lost
  to the automatic checks" means the dropped emails in the manifest (at most 3); a group the seed
  run got no seeds for is unmeasured for both models and reported. The near-miss rule's "both
  models write 20 fresh seeds" means a new seed run by the same third model, then both models
  again, judged on all 40 per model.
