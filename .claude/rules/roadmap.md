---
paths:
  - "docs/roadmap/**"
---

# Rules for `docs/roadmap/**`

Shared with Codex: `AGENTS.md` points here.

- One document per milestone, written at its start (`docs/roadmap/<milestone>.md`); until then
  the milestone's design is SPEC §1.2 and its outline in `planning-docs/`.
- SPEC is authoritative: a decision made in a roadmap document gets its OD row in SPEC in the
  same commit.
- Shape: status line (draft, date, ODs), Scope, Decisions, Design, numbered Steps (real-service
  tests are their own steps, each needing the operator's go-ahead), Plan review findings, Facts.
- Facts about outside services cite the page and the date fetched; anything else reads
  "unverified".
- A plan that changes the classifier, rules, policy, facts or eval gets Phase R before anything
  is built (Hard constraints above); findings are recorded in the document and the draft
  number goes up.
- Code references name the commit they were checked against.
- A design checked by executable models (operator decisions 2026-11-06, after M1 Phase R round 19):
  - No rule enters the text without a model event and a seeded fault that fails without it, and
    nothing that decides whether a job can finish is left "not modelled" (a model that over-approximates
    proves safety, not that a job finishes).
  - The plan keeps a "Rule → model" table, and each rule has one owning paragraph; other sections
    cite it by its name (e.g. "F10") instead of restating it, since restated copies drift apart.
  - A script checks the plan against the models and must pass before each Phase R round: every name
    in the table occurs in the models' source (a function, field, action, fault or model file); every
    fault the text names is in the runner's table, and the reverse; every count the text quotes
    matches the runner's latest output; no superseded model file is named outside the history
    sections; every F-rule, invariant and OD the text cites exists. Fix what it finds before the
    round starts; the milestone's tests take the script over when it is built.
