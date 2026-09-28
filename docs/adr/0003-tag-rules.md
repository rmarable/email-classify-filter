# ADR 0003: milestone tags and release tags

- **Status:** accepted (operator decision 2026-09-26, OD-012)
- **Context source:** SPEC §1.4; `CLAUDE.md` (Tags)

## Context

The build is split into milestones V1.0-V1.6 and later M1-M4. Progress needs recording, but a
milestone being done doesn't mean the software is fit for anyone else to install.

## Decision

- **Milestone tags** are annotated tags named `ms-…` (`ms-v1.0-foundations` … `ms-v1.6-linux`,
  later `ms-m1-aws` and so on). They record internal progress only.
- **Release tags** are `vX.Y.Z`, optionally `-rcN`. They are the only tags built and published
  from, and the only ones `ecf upgrade --to` accepts. `v1.0.0` needs every V1.x milestone and the
  release criteria in SPEC §1.5.
- Every tag is created and pushed only after the operator confirms and approves both the tag and
  the push. Claude prompts when a milestone looks complete and never tags
  unprompted (`CLAUDE.md`, Tags).
- `CHANGELOG.md` gets an entry for each tag, kept up to date commit by commit (`CLAUDE.md`,
  Changelog).

## Alternatives considered

- **Milestone tags named like releases** (`v1.0-foundations`): rejected; easy to mistake for a
  release (consistency check, 2026-09-26).
- **No milestone tags:** rejected; progress would live only in commit messages.

## Consequences

- `ecf upgrade --to` refuses `ms-…` tags.
- Tag creation is always a human decision.
