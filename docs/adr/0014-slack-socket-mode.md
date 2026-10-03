# ADR 0014: Slack through Socket Mode, one app per install

- **Status:** accepted (design plan, reviewed by the operator 2026-09-26/27; OD-083, OD-084,
  OD-118, 2026-09-27; OD-213, OD-215, 2026-09-29); implemented in V1.2
- **Context source:** SPEC §10.1, §9.5, §9.9, §12.1, §21.1; design plan
  (`docs/history/design-plan-2026-09-27.md`) §10a, §9; `ecf_server/slack_admin.py`,
  `slack_runtime.py`, `slack_in.py`, `slack_render.py`, `slack_routes.py`

## Context

ecf asks a person to approve actions and answer questions, and that person is often away from the
computer. Slack is where the approval cards, digests and alerts go. In v1 ecf runs on one laptop
with no public address, so Slack can't call it over HTTP. Every click is also an input from the
internet that could approve something, and everything posted is kept under Slack's retention.

## Decision

- **Socket Mode** (design plan §10a): the service opens an outbound WebSocket with an app-level
  token (`connections:write`), so there is no public endpoint and no OAuth redirect. Interactivity
  only; no Events API subscriptions, and no `groups:history`, `reactions:write` or `commands`
  scopes (§10.1).
- **One Slack app per install**, created by `ecf slack install` from ecf's manifest with a one-time
  configuration token that isn't stored (§10.1). The tokens go over the socket to the service and
  are checked with Slack before they are stored (ADR 0013).
- **Clicks only from your member ID** (OD-084), confirmed by a DM button click; others are refused
  and audited. The listener also re-checks the app and workspace, drops repeated envelopes and
  loads every action from its grant, never from the payload (§10.1, §9.5).
- **Risky actions wait for the computer**: sends, irreversible actions, hiding fraud or regulator
  mail and risky answers need step-up there (OD-213, ADR 0015); SECURITY.md lists what a click can
  still do (§9.9).
- **Private channels per address plus a summary channel**; ecf invites you, and the daily summary
  lists other members, with a Security Notice when they change (OD-215).
- **`plain_text` Block Kit only**: email-derived text escaped, control characters stripped, links
  defanged, unfurls off; no body, short excerpts only on request (§10.1, §12.4).

## Alternatives considered

- **An HTTP request URL:** needs a public endpoint, which v1 doesn't have; it is the M1 design
  (`ecf-slack` behind API Gateway, signature and timestamp checks), and `ecf migrate` switches the
  same app over (design plan).
- No other chat surface was considered for v1; Teams is an M3 item (SPEC §1.2).

## Consequences

- Buttons work only while the computer is awake and connected; "Needs you" shows "last connected"
  (OD-118). A click on a sleeping Mac on AC was received in a brief wake, but a form wasn't shown,
  so form buttons need an awake Mac (§10.1, tested 2026-09-29).
- A compromised Slack account can approve reversible actions and cause denial (reject, cancel,
  pause), not send or act irreversibly (§9.9).
- Slack keeps subjects, senders and classifications under its own retention (§12.2, §12.4).
- Each install needs its own app and, for `ecf destroy`, a fresh configuration token to delete it
  (§11.11).
