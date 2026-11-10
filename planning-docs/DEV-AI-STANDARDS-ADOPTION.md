# Adopting dev-ai-standards in ecf

Status: planned (operator decision 2026-11-10); starts at gate G. No ODs yet: the decisions below
get their OD rows in SPEC in step B2, when ecf adopts them.

## What and why

`dev-ai-standards` (public, `rmarable/dev-ai-standards`, v0.1.0, 2026-11-10) is a team kit
distilled from ecf's own practices: one shared rule file for Claude Code and Codex, a one-page
core, opt-in add-ons, three checks, git hooks, CI templates and a one-command project launcher.
ecf becomes its first real adopter. The full plan is Part B of the kit's
`planning-docs/P001-dev-ai-standards.md`; this file is ecf's pointer to it.

## Gate G (nothing below starts before it)

- eval/private (`eval-private-build`) merged to main, and the main checkout clean.
- M1 (`m1-aws-mode`) told; B3a and B6-type changes wait for M1's own merge.
- Every step is a separate branch and merge, with the macOS gate unless docs only.

## Steps (from P001 Part B)

1. B1: rewrite `CLAUDE-STATE.md` (local) in the standard shape; each session shortens its own lines.
2. B2: `dev-ai-standards apply --high-stakes --stack python --codex` on a branch; slim
   `AGENTS.md` and `CLAUDE.md`; record as ODs: Fable only for big-picture reviews (1-8 agents;
   amends OD-577, OD-562), reviewer sizes 1/2/4/8/12/16 (12-16 Opus only), scoped approval from
   an approver's own message; merge the kit's hooks into eval/private's existing `.githooks/`.
3. B3: no renames of existing docs; retire finished planning docs to an archive; trim memories.
4. B3b: changelog content rules and `merge=union`; no `changelog.d`.
5. B4: dependency and tooling updates, as separate merges with the full gate.
6. Move ecf to pull requests: a later, separate plan (ecf's merge gates are built on local merges).

## Not changing

SPEC stays authoritative; existing doc names, OD numbering and the macOS merge gate stay; ecf
keeps local `--no-ff` merges until its own pull-request plan.
