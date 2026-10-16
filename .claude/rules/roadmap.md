---
paths:
  - "docs/roadmap/**"
---

# Roadmap documents

- One document per milestone, written at its start (`docs/roadmap/<milestone>.md`); until then
  the milestone's design is SPEC §1.2 and its outline in `planning-docs/`.
- SPEC is authoritative: a decision made in a roadmap document gets its OD row in SPEC in the
  same commit.
- Shape: status line (draft, date, ODs), Scope, Decisions, Design, numbered Steps (real-service
  tests are their own steps, each needing the operator's go-ahead), Plan review findings, Facts.
- Facts about outside services cite the page and the date fetched; anything else reads
  "unverified".
- A plan that changes the classifier, rules, policy, facts or eval gets Phase R before anything
  is built (CLAUDE.md, Hard constraints); findings are recorded in the document and the draft
  number goes up.
- Code references name the commit they were checked against.
