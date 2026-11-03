---
paths:
  - "README.md"
  - "docs/operator-guide.md"
  - "docs/admin-guide.md"
  - "docs/gmail-setup.md"
---

# Rules for user-facing docs

Shared with Codex: `AGENTS.md` points here. Applies to the files above and to any new user-facing
doc.

Operator decision 2026-10-16. `README.md`, `docs/operator-guide.md`, `docs/admin-guide.md` and
`docs/gmail-setup.md` are written for someone new to ecf who doesn't know what it does or how it
works.

- Plain sentences in the second person; explain a term the first time it appears (step-up, stage,
  app password); worked examples over lists of conditions.
- No OD, ADR or SPEC § references, version-history asides or internal names (presets, rule ids,
  milestone codes) in their text; link to SPEC or the CHANGELOG for detail instead.
- Every edit keeps that standard, including small ones (a new flag, a release's Status line):
  don't drift back to spec-style.
- Each one, and any new user-facing doc, opens with the disclaimer callout right after its first
  paragraph (operator decision 2026-10-16): "**Before you rely on ecf, read the
  [disclaimer](DISCLAIMER.md).** ecf makes fraud harder to miss but can't catch everything, comes
  with no warranty, and leaves you responsible for what you approve and for the mailboxes you
  connect." (from `docs/`, the link is `../DISCLAIMER.md`).
- The style rules in `AGENTS.md` (Documentation style) still apply to SPEC, ADRs, CHANGELOG,
  CONTRIBUTING and planning docs.
