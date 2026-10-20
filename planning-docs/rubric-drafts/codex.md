# Draft, 2026-10-20 — OpenAI Codex (GPT-6), one of three independent drafts, for the operator to choose from

## Rubric

Rate each email on its own, without seeing its intended group or category. Use the same standards for scam and ordinary mail. The reserved names, fictional people and organizations, plain-text format, and other fixed safety limits are not reasons to lower a score. Judge the message as a plausible item in the stated mailbox, not as a claim that its sender or events are real.

For each question, choose one point from 1 to 4:

1. **Could this plausibly arrive in this mailbox?** Consider the sender's apparent role, recipient, subject, request or purpose, and the surrounding business context. A 1 means the email has no credible reason to reach this recipient; a 2 means its premise is possible but strained; a 3 means it could reasonably arrive, even if it is not typical; a 4 means it feels like a routine, credible item for this mailbox.
2. **Does the message hold together?** Consider whether its headers, subject, body, request, timing, and any attachment or delivery cue fit together. A 1 means important parts conflict or the message is hard to make sense of; a 2 means there is a noticeable unexplained mismatch; a 3 means it is coherent with only minor awkwardness; a 4 means the details reinforce one another naturally.
3. **Does the wording sound like a person or organization communicating by email?** Consider clarity, tone, and the amount and placement of detail, not spelling perfection or a particular writing style. A 1 means the wording is conspicuously artificial or unusable; a 2 means it has strong generic or unnatural passages; a 3 means it is understandable and mostly natural, with some roughness; a 4 means it reads naturally for its apparent sender and purpose.

Do not score whether the email is correctly labeled as a scam, ordinary mail, or a particular category. The intended group stays hidden during rating; the rubric measures whether the email itself is credible and coherent, not whether the generator followed its private assignment. Emails are shown in a shuffled order, one at a time, with no model identity or model answers.

## Pass mark

A generator passes only if, across its 20 emails, **each** of the three questions has a mean score of at least 3.0 and at least 15 of 20 emails score 3 or 4 on that question. These requirements are joined by AND. A score exactly on a threshold passes. If a model misses any threshold by just one email or 0.1 mean point, record it as a near-pass but do not advance it as a passing model; the operator should decide whether to authorize a separately fixed follow-up pilot before seeing its output. Do not change the thresholds after viewing the emails.

## Refusal ceiling

A passing model may have at most 2 refused emails across the pilot and at most 1 refusal in any group. Refusals count against the 20 planned emails, are never retried, and do not get replaced. Report them by group. This permits a small number of policy refusals while preventing a passing result from depending on a group being almost absent.

## Automatic-check limit

A passing model may lose at most 2 of its 20 planned emails to the automatic checks. Count a dropped email once even if it has multiple findings. Keep refusals separate from check drops. This is a ceiling on reliability, not permission to relax or bypass a check.

## Reasons

Three separate ratings keep a high score in natural wording from concealing an implausible recipient or a contradictory message. Four points are enough to distinguish poor, borderline, acceptable, and especially convincing work while staying quick to use on a phone. The shared criteria let scam and ordinary messages be rated without changing standards or requiring the rater to infer a hidden label.

At only 20 emails, even 16 acceptable ratings would have a wide interval, so the thresholds are a screening rule for this pilot, not proof of general quality. Requiring both a mean and 15 acceptable emails on every dimension limits the effect of a few very strong emails masking recurring weak ones. The near-pass rule makes a close result visible without quietly treating it as a pass or moving the bar after inspection.

The refusal limit allows up to two refusals overall but no more than one in a group. The check-drop limit similarly allows occasional format or hygiene failures while exposing a model that often produces unusable output. Neither limit authorizes retries or changes to the fixed safety checks.

Hiding the intended group reduces priming: a message should not feel realistic merely because the rater has been told what kind it is meant to be. Since group-specific realism is not measured under this choice, the operator may instead choose to reveal the group if evaluating category fit is a priority; that would require a different rubric question and should be fixed before rating.

## Questions for the operator

- Should the intended group and category remain hidden, as proposed, or be shown so the rating can include fit to the assignment?
- Is a near-pass worth a separately authorized follow-up pilot, or should any pilot failure rule the model out for this comparison?
- Is a ceiling of two automatic-check drops out of 20 acceptable, given that these checks are safety and integrity requirements?

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
pass_mark:
  - {rule: "The mean mailbox-plausibility score is at least 3.0 across 20 emails.", question: mailbox_plausibility, measure: mean, at_least: 3.0, at_most: null, of: null}
  - {rule: "At least 15 of 20 mailbox-plausibility scores are 3 or 4.", question: mailbox_plausibility, measure: share, at_least: 0.75, at_most: null, of: [3, 4]}
  - {rule: "The mean internal-coherence score is at least 3.0 across 20 emails.", question: internal_coherence, measure: mean, at_least: 3.0, at_most: null, of: null}
  - {rule: "At least 15 of 20 internal-coherence scores are 3 or 4.", question: internal_coherence, measure: share, at_least: 0.75, at_most: null, of: [3, 4]}
  - {rule: "The mean natural-wording score is at least 3.0 across 20 emails.", question: natural_wording, measure: mean, at_least: 3.0, at_most: null, of: null}
  - {rule: "At least 15 of 20 natural-wording scores are 3 or 4.", question: natural_wording, measure: share, at_least: 0.75, at_most: null, of: [3, 4]}
refusals: {overall_at_most: 2, per_group_at_most: 1}
checks: {dropped_at_most: 2}
```
