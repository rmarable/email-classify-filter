# ADR 0009: The trust boundary is the local service

- **Status:** accepted (design plan "Decision summary", Trust boundary, reviewed by the operator
  2026-09-26/27; OD-013, OD-087, 2026-09-26; OD-073, OD-123, 2026-09-27); implemented from V1.0
  (socket, import rules), V1.1 (facts, triggers) and V1.3 (rules, policy)
- **Context source:** SPEC §3.2, §7.2, §7.4b, §10.4, §12.1, §12.2; design plan
  (`docs/history/design-plan-2026-09-27.md`) "Decision summary"; `pyproject.toml` import-linter
  contracts; `ecf_server/policy.py`

## Context

ecf hands email written by strangers to models: Gemma locally, Claude in `/ecf-review`. A model
can be steered by text in the email (prompt injection), and in presets B and C the model runs in a
Claude Code session the service doesn't control. Clients (the `ecf` CLI and `ecf-mcp`) are thin
and run in places where other code runs too. Something has to decide what is safe, and it has to
be a component that neither the email nor a model can talk round. The same component moves to
`ecf-api` in M1, so the line must not depend on running on one computer.

## Decision

- **The local service decides** (§3.2): `ecf_server` computes the facts (§7.2), fraud and
  regulator triggers, rules, policy, grants and every state transition, and is the only holder of
  mailbox credentials. Clients and models hold none of these.
- **Model output is input, not authority** (§3.2, §7.4b): classifications and proposals are
  validated strictly; they can raise risk but never, alone, hide mail, approve or send anything.
  Policy's invariants (I1-I4) run after the model: a hide needs corroboration from facts alone,
  nothing removes a label or flag, and high risk is the facts OR the classification.
- **Facts never go to or come from a model** (§7.2): `auth_result`, sender history and the rest
  are computed by the service and kept out of prompts (OD-279 for Claude).
- **MCP has no approval or admin tools** on any host (§10.4), and answering through MCP is not in
  v1 (OD-087). Profiles are enforced by the service, not by client arguments.
- **The service checks step-up itself**; a result reported by the CLI is never trusted (OD-073).
- **Enforced in the build**: import-linter forbids `ecf` from importing `ecf_server`, Slack,
  LocalAuthentication or PAM (OD-123; V1.2 security review).

## Alternatives considered

None recorded beyond the roadmap placement: in M1 the same rule holds with `ecf-api` as the
deciding component (design plan "Decision summary"; ADR 0002). The design plan records no option
of letting a model or client apply rules or policy.

## Consequences

- In v1 the boundary is a process boundary on one machine: anyone who controls your OS account,
  including a coding agent running as you, controls ecf (§12.2). Step-up confirms intent; it isn't
  a boundary against malware running as you.
- MCP profiles are hygiene against mistakes and prompt injection, not a boundary (§10.4).
- Every rule or policy change is a service change; clients need no update for one.
- A wrong or hijacked model answer costs a wrong label, a flag or a proposal you reject, not a
  hidden fraud email or a send (§12.1).
