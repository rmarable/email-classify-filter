# ADR 0015: Approvals in Slack or the CLI, with step-up at the computer

- **Status:** accepted (OD-041, 2026-09-26; OD-071 to OD-073, OD-076, OD-077, OD-084, OD-087,
  2026-09-27; OD-207, OD-208, OD-213, 2026-09-29; OD-223, OD-224, 2026-09-30); implemented in V1.2,
  real sends in V1.5; Linux step-up unverified until V1.6
- **Context source:** SPEC §9.5, §9.6, §6.5, §10.4, §12.2; design plan
  (`docs/history/design-plan-2026-09-27.md`) §7; `ecf_server/approvals.py`, `stepup.py`,
  `stepper.py`, `execute.py`, `ecf/cli_items.py`

## Context

A model proposes actions on mail that attackers can write, so anything that can't be undone must
be approved by a person, and the approval must be for exactly what will run. v1 has one person,
who is both admin and approver, and one computer. Slack is convenient but is a remote surface; an
MCP client is driven by a model.

## Decision

- **Approvals come from Slack or the CLI only, never MCP** (design plan "Approvals", OD-087): MCP
  has no approval or answer tools, and step-up routes refuse MCP profile tokens (§9.6).
- **Grants:** an approval issues a grant bound to the item, its content hash and the exact
  actions; the action is always loaded from the grant; the first decision wins; grants expire
  after 4 days for sends and 14 otherwise (OD-041, OD-208), and `ecf approve` can re-offer an
  expired one (OD-223).
- **Reversible actions are one click in Slack** (mark read, archive, move, junk, drafts) from your
  member ID (OD-084) and can be undone (§9.5).
- **Step-up at the computer** for every send, every irreversible action, hiding fraud or regulator
  mail, risky answers and confirmations, and security-relevant changes (OD-071, OD-072, OD-076,
  OD-077, OD-213; full list §9.6). The service always performs the check; a result reported by the
  CLI is never trusted (OD-073). It issues a single-use nonce bound to the target's hash and
  computes the dialog text itself: LocalAuthentication (Touch ID or password) on macOS; PAM on
  Linux, polkit from V1.6 (OD-224). A Slack click on such an action waits at `awaiting_stepup`.
- **Sends on `high` addresses** wait 10 minutes of awake time after step-up, announced with
  Cancel (§9.5, OD-207).

## Alternatives considered

- **Approving through MCP:** rejected (design plan); a model-driven client must not approve.
- **Trusting a step-up result from the CLI:** rejected (OD-073); the service runs the check
  itself.
- **Two approvers for `high` sends, TOTP step-up:** the design for teams (M2) and AWS mode (M1);
  in v1, with one person, the design plan (§7) sets approver, step-up and the delay instead.

## Consequences

- Step-up confirms intent inside one OS account; it is not a boundary against malware running as
  you (§9.6, §12.2).
- Anything needing step-up waits until you are at the computer; Slack shows "Queued for your
  computer (N waiting)" and a desktop notification names the command (§9.5).
- `ecf approve --pending` handles at most 10 non-sends with one authentication; each send gets
  its own (§9.5).
- At most 5 verifications per 10 minutes, service-wide, so a same-user process can't lock the
  account (§9.6).
