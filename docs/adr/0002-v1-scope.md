# ADR 0002: v1 is single-user local mode

- **Status:** accepted (operator decision 2026-09-26, OD-013; platforms OD-001, OD-002)
- **Context source:** SPEC §1; design plan (`docs/history/design-plan-2026-09-27.md`), Scope and §17

## Context

The design covers a team email-triage tool: an AWS back end, several people with roles, remote
access from claude.ai, and always-on processing. Building all of it before anything works would
delay every real test and every lesson from real mail.

## Decision

v1 builds only **single-user local mode**: one person, one computer (macOS or Linux; Linux
desktops through Secret Service, headless Linux best-effort on systemd 256+), no AWS. The OS user
is the sole admin and approver. One local service holds state (SQLite), secrets (the OS secret
store) and Slack (Socket Mode), and runs while the computer is on. Everything else stays fully
designed and moves to milestones executed in order: M1 AWS mode, M2 teams, M3 remote access,
M4 always-on, then later items.

Security-relevant code (facts, triggers, rules, policy, transitions) runs in the service, not in a
model or client, so M1 moves it to Lambdas without redesign.

## Alternatives considered

- **Everything in v1** (AWS, teams, remote MCP): rejected; too much to build before the first real
  test (superseded 2026-09-26).
- **A reduced v1** (R4-25): rejected in Review 4.
- **macOS only:** rejected; Linux is in v1 but unverified until V1.6.

## Consequences

- No processing or alerts while the computer is asleep or off; approvals and Slack clicks wait.
- The server-side trust boundary is a process boundary on one machine; SPEC §12.2 states the limit.
- Each roadmap milestone gets its own document at its start (`docs/roadmap/`).
