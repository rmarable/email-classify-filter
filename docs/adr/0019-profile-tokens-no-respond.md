# ADR 0019: MCP access by service-issued tokens; no approvals and no answers through MCP

- **Status:** accepted (OD-087, 2026-09-26; OD-267, OD-280, OD-281, OD-307, 2026-10-02;
  OD-456, 2026-10-05); implemented in V1.4 (steps 3 and 13)
- **Context source:** SPEC §9.9, §10.3, §10.4, §12.1, §12.2; design plan
  (`docs/history/design-plan-2026-09-27.md`) "MCP tool profiles" and the superseded list
  ("Approvals and answers in chat"); `ecf_server/claude_review.py`, `ecf/mcp_server.py`,
  `ecf/claude_wrapper.py`

## Context

A model reading attacker-written email holds MCP tools, so a prompt injection can call any tool the
session has (§12.1, confused deputy). Any Claude Code session on the computer could also start
`ecf-mcp`, including the operator's own coding sessions. Which tools a caller gets therefore can't
depend on what the client says about itself, and no tool may let a model approve, answer for a
person or change settings.

## Decision

- **Profiles are enforced by the service, by token** (§10.4): `ecf claude` obtains a per-session
  profile token over the socket and passes it in `ECF_PROFILE_TOKEN`; every such session gets
  WORK, since the token is issued before anything is typed, and it is revoked when the wrapper
  exits. A request with no token is OBSERVE: `status` and `counts` only, no email-derived fields
  (OD-280). The service refuses decision and settings routes to any review token.
- **Only ecf's subagents read and submit email work** (OD-307): each agent runs its own
  `ecf-mcp --agent` server with the session's separate agent token, which reaches only the claim
  routes; `review_queue` claims items for a named agent (OD-267), a claim lasts 15 minutes
  (OD-281), and a claim answers only that agent. The main session never sees a message body.
- **No approval or admin tools on any host**, and **no RESPOND profile in v1** (OD-087): answers
  come only from Slack's Answer form or `ecf answer`, and no MCP answer would count as human
  confirmation on payment, fraud or `high` items (§9.9).
- **Profiles are hygiene, not a boundary** (§12.2): they limit mistakes and injection, not another
  process running as you.

## Alternatives considered

From the design plan's superseded list and notes:

- **Profiles chosen by client argument:** rejected; any caller could ask for more.
- **Elicitation-gated MCP approvals:** rejected; approvals stay in Slack or the CLI, with step-up
  at the computer for risky ones (§9.5, §9.6).
- **`answer`/`ask_team` in watch sessions:** removed with `/loop` (ADR 0017); RESPOND (`answer`,
  `ask_team`) is a later item (its first host, VS Code, was dropped by OD-456), and M3 gives claude.ai only a proposal-only
  `answer` confirmed in Slack.
- **Main-session reads checked by telemetry** (OD-274) and submissions bound by telemetry alone
  (OD-268, OD-285): replaced by OD-307's per-agent servers after the B shadow run (§21.2).

## Consequences

- A coding session outside `ecf claude` sees only counts; the plugin lives only in `ecf claude`'s
  config (§10.3).
- Every person-facing decision is made in Slack or at the CLI; Claude can only propose.
- Profiles are hygiene, not a boundary: anyone who controls your OS account controls ecf, and
  SPEC states both limits (§12.2).
