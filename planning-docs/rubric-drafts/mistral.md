# Realism rubric draft — Mistral slot

Draft 1, 2026-10-20. Drafted by GLM (glm-5-latest-short, Z.ai) served on Mistral
infrastructure — NOT a Mistral-authored model; the operator asked for Mistral-authored
and must decide whether this counts as the Mistral draft or a fourth outside draft.
One of three independent drafts, for the operator to choose from.

## The rubric

Four questions per email, answerable in under a minute each, one email at a time:

1. **Realism** (scale 1–4). "Setting aside the things the safety rules force on every
   email (`.example` domains, 555-01xx phones, made-up company names, plain text,
   checksum-failing account numbers), does this read like a real email someone could
   have found in a mailbox like this?"
   - 1 — Clearly artificial. Reads as generated, a caricature, or an exercise.
   - 2 — Mostly plausible, but something core is off (wording no one would write,
     a story that doesn't hold together, a detail that breaks the spell).
   - 3 — Plausible. It would pass without remark in a real inbox.
   - 4 — Convincing. Natural voice, specific and self-consistent; indistinguishable
     from the real thing apart from the forced safety details.
2. **Premise fit** (yes/no). "Does the email's premise make sense for the mailbox it
   was sent to and the kind of email it claims to be?" (An invoice dispute belongs at
   `ap@acme.example`; a family emergency does not.)
3. **Ask fit** (yes/no). "Does the way the email asks for action (attachment, link,
   phone number, or reply-only) fit naturally with the story?" (A reply-only
   extortion or a link in a newsletter can both fit; a jarring mismatch is "no".)
4. **Distinctness** (yes/no). "Does this read as one specific email, rather than a
   fill-in template or a committee compromise?" (Generic openings that could belong
   to any email of the type, or visible pattern-book phrasing, are "no".)

Rate what's shown (headers, attachment names, up to 4,000 characters of body); hidden
text is not the rater's concern.

**Should the rater see the intended group or category? Yes.** Questions 2 and 3 are
unanswerable without knowing what the email is supposed to be — an email can be
perfectly realistic as *an* email and still fail as a BEC or a Promotions mail. The
bias the pilot must avoid is *model* bias, and blinding to the generator model already
handles that; blinding to the type would add noise, not protection.

## The pass mark

A generator passes the pilot if, over its 20 kept emails, **all** of these hold:

- At least **15 of 20** emails rate **3 or 4** on realism (75%; the share's 95%
  Wilson interval still spans about 51–91%, so treat this as a coarse sieve, not a
  precise estimate).
- At most **2 of 20** rate **1** on realism (bottom-scored emails are the clearest
  signal at this sample size; two or fewer is forgiveness for one-off misses,
  three means a systematic problem).
- At least **16 of 20** are "yes" on premise fit.
- At least **14 of 20** are "yes" on ask fit (some scam types genuinely strain the
  delivery method they were assigned; the bar is lower here on purpose).
- At least **16 of 20** are "yes" on distinctness (near-duplicate openings already
  get dropped by the automatic check; this catches template-flatness the checks miss).

**Borderline result:** if a model lands at 13 or 14 on the realism count (or exactly
one "no"-heavy question over its line by one email), the pilot is **inconclusive, not
a pass**: run both models on 20 fresh seeds with the same rubric unchanged and judge
on the 40-email totals. No rubric or threshold change after seeing output — if the
combined 40 still straddles the line, the operator decides and records the reason.

## The refusal ceiling

- Overall: at most **2** refused seeds of 20.
- Per group: at most **1** (most groups have only 1–2 emails at this size; one refusal
  in a one-email group means that group went unmeasured, which the report must show).
- A model over either ceiling fails, regardless of quality: refusal-prone generation
  would make the 300-email set slow, gappy, and unrepresentative.

## Limits on the automatic checks

At most **4** emails of 20 dropped by the automatic checks (hygiene, HTML/markup,
brand-as-sender, near-duplicates), after the single hygiene retry. More than that
suggests the model is fighting the constraints rather than working within them, and
would make reaching 300 kept emails expensive.

## YAML

```yaml
version: 1
questions:
  - id: realism
    text: "Setting aside what the safety rules force on every email (.example domains, 555-01xx phones, invented companies, plain text), does this read like a real email someone could have found in a mailbox like this?"
    type: scale
    options:
      - {value: 1, label: "Clearly artificial; reads as generated or a caricature"}
      - {value: 2, label: "Mostly plausible but something core is off"}
      - {value: 3, label: "Plausible; would pass without remark in a real inbox"}
      - {value: 4, label: "Convincing; natural, specific, self-consistent"}
  - id: premise-fit
    text: "Does the email's premise make sense for the mailbox it was sent to and the kind of email it claims to be?"
    type: yes_no
  - id: ask-fit
    text: "Does the way the email asks for action (attachment, link, phone number, or reply-only) fit naturally with the story?"
    type: yes_no
  - id: distinctness
    text: "Does this read as one specific email rather than a fill-in template?"
    type: yes_no
pass_mark:
  - {rule: "At least 15 of 20 emails rate 3 or 4 on realism", question: realism,
     measure: count, at_least: 15, at_most: null, of: [3, 4]}
  - {rule: "At most 2 of 20 emails rate 1 on realism", question: realism,
     measure: count, at_least: null, at_most: 2, of: [1]}
  - {rule: "At least 16 of 20 are yes on premise fit", question: premise-fit,
     measure: share, at_least: 16, at_most: null, of: yes}
  - {rule: "At least 14 of 20 are yes on ask fit", question: ask-fit,
     measure: share, at_least: 14, at_most: null, of: yes}
  - {rule: "At least 16 of 20 are yes on distinctness", question: distinctness,
     measure: share, at_least: 16, at_most: null, of: yes}
refusals: {overall_at_most: 2, per_group_at_most: 1}
checks: {dropped_at_most: 4}
