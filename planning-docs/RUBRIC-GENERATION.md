# Realism rubric for generated test emails: brief for three independent drafts

Draft 2 (2026-10-20): three models each draft a rubric independently, Codex (OpenAI), a Mistral
model and Claude, and the operator builds the final rubric by choosing parts from each (operator
decision 2026-10-20). Draft 1 (2026-10-20) had one non-Claude model draft it. Written by Claude
Code as a brief only: it states the job, the facts and the output format, and contains no rubric
content. The operator edits and approves the combined rubric and owns it. Parent plan:
`planning-docs/EVAL-GENERATE-GRADE.md` (draft 13; Phase R finding P25; build step 4).

## Why this exists

ecf (email-classify-filter) flags fraud in business mail. Its `ecf eval generate` command has a
model other than Claude write synthetic test emails, so ecf can be measured on mail that Claude
didn't write. The operator's rule is "no Claude bias": Claude writes the machinery, never email
content, wording, grades or labels. The judgement of what counts as realistic is the operator's,
chosen from three drafts (Why three drafts, below).

Before generated sets are trusted, a pilot compares two OpenAI generator models on the same 20
seeds: GPT-6.1 Sol (the default) and GPT-6 Luna (cheaper; used only if it passes). The operator
rates each of the 40 emails for realism, blind to which model wrote it. The rubric is what they
rate against; the pass mark decides whether a model is good enough. Both must be fixed before
the operator sees any generated email, including the all-Claude DEV run, so that no model's
output shapes the bar (P25).

**Why three drafts.** Each candidate drafter has a stake: Claude is ecf's classifier, the family
the generated sets measure; OpenAI makes both generators in the pilot; Mistral grades the sets.
With three independent drafts and the operator choosing every part of the final version, no one
family's view sets the bar, and each draft's blind spots show up against the other two.

## The job

Draft, for the operator to edit and approve:

1. **The rubric:** the questions the operator answers for each email, each with its answer type
   and, for a scale, a short description of every point on it.
2. **The pass mark:** the rule, in numbers, that a generator model must meet over the pilot's 20
   emails to pass.
3. **The refusal ceiling:** how many refused emails a passing model may have, overall and per
   group (refusals are recorded and never retried, so a group can come up short).
4. **Limits on the automatic checks** (optional): how many emails a passing model may lose to
   the checks listed under Facts.
5. **Your reasons**, a sentence or two per choice, and the questions you'd put to the operator.

## Who rates, and what they see

- One person, the operator: semi-technical, runs a small business mailbox, knows ecf. They also
  label every email of the first 300-email set later (5-10 hours), so the pilot's rating should
  be quick: plan on 40 emails in one sitting, on a phone or a laptop.
- Each email is shown as a mail client shows it: From, To, Cc, Reply-To, Subject and Date, the
  attachment names, then the plain-text body (no HTML; at most 4,000 characters shown). Hidden
  text, if any, isn't shown.
- Blind: the rater doesn't see which model wrote the email, and sees no model's labels or
  answers. Whether the rater is told the email's intended group or category is an open question
  for the operator; say which you'd choose and why.
- Answers are recorded per email; the page shows one email at a time, with the questions beside
  it.

## Facts about the emails

- **Kinds.** Half scam, half ordinary. Scams follow the FBI IC3 *2025 Internet Crime Report*
  crime types, in 10 groups: Phishing/Spoofing, Extortion, Investment, Non-Payment/Non-Delivery,
  Tech/Customer Support, Government Impersonation, Business Email Compromise, Employment,
  Confidence/Romance, and Other IC3 types (Credit Card/Check Fraud, Real Estate, Advanced Fee,
  Lottery/Sweepstakes/Inheritance, Ransomware, IPR/Copyright and Counterfeit, Overpayment,
  Malware, Charity). Each scam has a delivery method: an attachment, a link or a phone callback
  (MITRE ATT&CK T1566.001, .002, .004), or reply-only. Ordinary emails follow Gmail's inbox
  categories: Primary, Social, Promotions, Updates, Forums. The definitions are quoted in
  `src/ecf/data/eval_mix.yaml`.
- **Recipients**, fictitious: the organisation Acme at `acme.example`, its accounts-payable
  mailbox `ap@acme.example`, one named colleague (Dana Chief), and a personal mailbox
  `pat-lee@freemail.example`. No industry, size or location is given.
- **Safety rules every email follows, by design**, so they make an email look less real in fixed
  ways the rubric shouldn't penalise: only reserved domain names (ending `.example` or `.test`);
  phone numbers only 555-0100 to 555-0199; no real people's names; companies, regulators and
  agencies named by role in the text, never as the sender; bank and card numbers that are
  published examples or fail their checksum; plain text only. Attachments are plain text or a
  generated PDF invoice.
- **The pilot:** 20 seeds, each written by both models: 40 emails. How the 20 are spread isn't
  fixed yet; at a count of 20 the shipped mix gives 1 per scam group and 2 per ordinary category,
  so no group has more than 2. A pass mark over 20 emails is a small
  sample; a share of 16 in 20 (80%) has a 95% Wilson interval of about 58% to 92%. Choose a pass
  mark that means something at that size, and say how you'd treat a borderline result.
- **Automatic checks ecf already runs** (an email that fails is dropped and counted, never
  edited): the hygiene scan (the safety rules above, plus no real domains, addresses, phone
  numbers, card or bank numbers, wallet addresses or key shapes), HTML or script markup, a brand
  or agency named as the sender, and near-duplicates (the same opening line, or heavy word
  overlap with an earlier email). A hygiene failure gets one retry. Refusals are counted by group.

## What you may and may not use

- **Use:** this brief; `planning-docs/EVAL-GENERATE-GRADE.md`; `src/ecf/data/eval_mix.yaml`;
  public descriptions of how phishing and business email compromise are recognised, for your own
  understanding.
- **Don't use or read:** `planning-docs/rubric-drafts/` and `planning-docs/RUBRIC.md` (the
  other drafts and the combined version); anything under `tests/eval/` (the existing test cards
  were written by Claude), `src/ecf/eval/case_templates.py`, `src/ecf/eval/generate.py`'s prompt text, any
  generated set, or any example rubric shown in a Claude Code session. The operator saw one such
  example in chat on 2026-10-20; it is not an input.
- **Don't write** example emails, sample answers, or wording advice for the generator. The
  rubric judges emails; it doesn't describe how to write them.
- **Don't** copy real phishing text, or name real companies, people or domains.

## Three independent drafts

- The drafters are Codex (OpenAI), a Mistral model and Claude. Each works alone from the same
  inputs (What you may use, below) and never sees another's draft.
- Each draft is saved unedited as `planning-docs/rubric-drafts/<drafter>.md` (`codex.md`,
  `mistral.md`, `claude.md`).
- Draft a complete rubric on your own terms. Don't aim for agreement with anyone; a clear,
  argued choice is more useful to the operator than a cautious middle.

## Output

One file, `planning-docs/rubric-drafts/<drafter>.md`, with:

1. A short status line: draft, date, the model that drafted it, "one of three independent drafts,
   for the operator to choose from".
2. The rubric, the pass mark, the refusal ceiling and any check limits, in plain words.
3. The same in a fenced YAML block, so ecf can build the rating page from it without reworking
   the text:

   ```yaml
   version: 1
   questions:
     - id: <slug>
       text: "<the question as the rater sees it>"
       type: scale | yes_no | choice
       options:            # scale: one per point, low to high; choice: each option
         - {value: <value>, label: "<what it means>"}
   pass_mark:
     - {rule: "<plain-words rule>", question: <id or null>, measure: <mean | share | count>,
        at_least: <number or null>, at_most: <number or null>, of: <value or values or null>}
   refusals: {overall_at_most: <n>, per_group_at_most: <n>}
   checks: {dropped_at_most: <n or null>}
   ```

   The `pass_mark` rows are joined by AND. Put any rule that doesn't fit these fields in plain
   words, with `measure: other`, and ecf's side will be built for it once it's approved.
4. Your reasons, and your questions for the operator.

## If you are Codex working in this repository

Follow `AGENTS.md`: work on a branch `codex/rubric` in your own worktree made from `origin/main`;
add only `planning-docs/rubric-drafts/codex.md`; commit and push the branch only after the operator's OK; end
the commit with the `Handoff: ready` block (Tests: "none (document only)"; CHANGELOG: "none";
Numbers: "none"). Running evals, generating sets and editing `main` are reserved for Claude Code.

## If you are a Mistral model

Mistral has no route into this repository that keeps to the rules above: Vibe's core always offers
its file and shell tools to the model (parent plan, Facts), so it could read the files this brief
rules out. So the operator gives you only what you may use, and you work from that:

1. **The operator prepares** (before starting): turn off training on your content in the Mistral
   account's settings, for the product you'll use (training is on by default for individual
   users, and the Vibe and API settings are separate, per help.mistral.ai article 455207 as cited
   in the parent plan, (rv); whether Le Chat has its own setting is unverified). Copy three files' text, and nothing else: this
   brief, `planning-docs/EVAL-GENERATE-GRADE.md` and `src/ecf/data/eval_mix.yaml`.
2. **Where:** Le Chat (chat.mistral.ai) in a new conversation, pasting the three files; or, if
   the operator prefers Vibe, a new empty folder outside the repository holding copies of only
   those three files, started from that folder, never from the checkout. Which Mistral model
   answers in Le Chat, and whether a conversation's files are kept, are unverified: check the
   model picker and the settings first.
3. **You return** the full contents of `planning-docs/rubric-drafts/mistral.md` as one Markdown
   document, in the Output format above, with the status line naming the Mistral model that
   drafted it.
4. **The operator** pastes it to Claude Code, which saves it unedited as
   `planning-docs/rubric-drafts/mistral.md` and commits it on a branch (docs only), after the
   operator's OK.

## If you are Claude

Claude Code runs the Claude draft in a fresh agent with none of its session's context: the agent
gets the text of the three files you may use and these instructions, uses no tools (so it reads
nothing else, including the other drafts), and returns the full contents of
`planning-docs/rubric-drafts/claude.md`, with the status line naming the Claude model. Claude
Code saves the answer unedited.

## If you are another model outside the repository

Work the same way as a Mistral model: the operator gives you only this brief,
`planning-docs/EVAL-GENERATE-GRADE.md` and `src/ecf/data/eval_mix.yaml`, and you return the
file's contents to them.

## After the drafts

1. **Blind comparison.** Claude Code checks that each draft's YAML block parses (and says where
   one doesn't; it fixes nothing), then builds a comparison page: the three drafts as A, B and C
   in a random order, side by side, part by part (questions, scale wording, pass mark, refusal
   ceiling, check limits, the answer on showing the intended group). Model names stay hidden
   until the operator has chosen.
2. **The operator chooses** which parts to keep from each.
3. **Combined word for word.** Claude Code assembles `planning-docs/RUBRIC.md` from the chosen
   parts without rewording them; where parts don't fit together (a pass-mark row naming a
   question that wasn't kept, two questions with one id), it names the clash and the operator
   decides. The file records which draft each kept part came from.
4. **The operator edits and approves it**; it is theirs. Claude Code checks only that the YAML
   block parses and matches the plain words, records it as the operator's decision (the parent
   plan's next draft, and an OD row in SPEC), word for word, and builds the blind rating page
   from the YAML block.
5. The rubric and pass mark are then fixed: changes after any generated email has been seen are
   recorded with a reason, and never made to rescue a pilot result.

## Open questions for the operator

- **Whether the rater sees the intended group or category.** Each draft answers it with its
  reasons; the operator decides when combining.
