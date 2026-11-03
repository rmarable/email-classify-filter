# Reviews and model evaluations

Rules for coding agents (Claude Code and Codex), moved out of `AGENTS.md` so they load only when
needed. `AGENTS.md` says when to read this file. The text is unchanged from `AGENTS.md`.

## Model choice

Operator decision 2026-10-12. Each role gets the best model for the job: the larger model stays
unless a smaller one (Opus, Sonnet, Haiku) is shown to match it on that job, safety gates and
safety-relevant fields first, under the evaluation rules below; cost and time decide only between
equals. Jev (TypeSafe) was the candidate for the classifier role and is shelved (OD-516: it didn't
match C). Local models are removed (ADR 0031); Bedrock is the stated path to revisit (if TypeSafe
offers Jev on Bedrock).

## Model evaluations

OD-486; SPEC §16.5 "Evaluation rules". Any model evaluated as a classifier answers every schema
field (decision models mapped first), runs through `ecf eval run` on the frozen synthetic set with
the same signals block, records per-role figures and the prompt digest, is compared with the one
rule (Newcombe, `--margin`) with input differences stated, and keeps its result file until the
decision using it is recorded.

## Adversarial review (Phase R)

Operator decision 2026-10-10. Every plan that changes the classifier, rules, policy, facts or eval
gets Phase R before any of it is built, sized to the change (operator decision 2026-10-12):

- two read-only Fable agents (safety and correctness, design) for a small, contained change such
  as a bug fix;
- four (safety, measurement, cost and design, performance) for a plan that changes them;
- eight for a major release (those four plus four lenses specific to the plan).

The agents run in parallel, told to attack, not summarise, and to cite file:line. Findings are
merged into one numbered list (P1…) by severity, key claims are spot-checked against the code, the
operator decides each, and the plan is amended (a new draft) before step 0. Full findings go in
`state-archive/<plan>/review-findings.md` (gitignored).

## Diff review

Operator decision 2026-10-14. Only before a gate eval run, two read-only Fable agents (safety and
correctness, performance) review the diff, and findings are fixed or decided before the run; skip
it when Phase R already ran on a plan that didn't change in the build. Other branches rely on
Phase R and the full suite.

## Who reviews, on which model

- Claude Code runs Phase R and diff reviews (OD-532; Claude agents only). Codex doesn't run them,
  and doesn't start building a plan that needs Phase R until it is done.
- Operator decision 2026-10-29, OD-577: Fable agents run a plan's Phase R and a branch's first diff
  review; Opus agents run the later rounds of the same work (plan-draft and delta checks, fix
  checks, later diff reviews), with the same reviewer counts and lenses.
- When Fable is out of credits, Opus agents run a Fable round's brief instead, and the findings say
  which rounds ran on Opus (operator decision 2026-10-27, OD-562).
- Sonnet doesn't review.
