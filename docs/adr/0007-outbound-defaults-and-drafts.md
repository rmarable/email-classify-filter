# ADR 0007: Outbound is off by default; drafts never send

- **Status:** accepted (OD-015, OD-058, OD-059, 2026-09-26; OD-317 to OD-325, OD-329, 2026-10-02);
  implemented in V1.5 (steps 1-6)
- **Context source:** SPEC §8.4, §9.5, §9.8, §12.1, §12.4; design plan
  (`docs/history/design-plan-2026-09-27.md`) "Outbound v1"; `ecf_server/send.py`,
  `send_actions.py`, `outbound.py`, `outbound_plan.py`, `send_limits.py`, `outbound_msg.py`

## Context

Anyone can write to the mailboxes ecf reads, and a model proposes actions on that mail, so
anything ecf sends is a path from an attacker's text to a real recipient: a reply that confirms a
fake invoice, a forward that leaks a document, or a loop between two auto-responders. Labels can be
undone; sent mail can't. At the same time, routine replies and internal hand-offs are much
of the work on an `info@` or `billing@` mailbox, so some sending is worth having.

## Decision

- **Three outbound actions, nothing more** (§8.3, §8.4): `draft_reply` (saved to Drafts, never
  sent), `reply_template` (an operator-written template) and `forward_internal` (the original, to
  an entry on a forward allow-list within your org domains). No model-written text is ever sent;
  external forwards and deletes aren't in v1.
- **Sends are off per address by default** (OD-058). `ecf outbound enable` needs step-up and sends
  a Security Notice; a `high` address first needs ≥ 20 reviewed suppressed proposals, ≥ 95% marked
  correct (§9.8). While off, a proposed send is recorded as `suppressed_action` and the item is
  flagged. Reminders run weekly and then monthly until you decide (§9.8).
- **Drafts aren't gated by the switch but always need approval** (OD-015, OD-058): a draft only
  lands in your Drafts folder, addressed to the From address, and you send it yourself. The actor
  may write its text (≤ 4,000 characters), shown in full before approval and labelled as model
  output (OD-317).
- **Every send needs approval and step-up**; on a `high` address a 10-minute delay with Cancel
  follows (§9.5, OD-329). The grant binds the template, the rendered text's hash and the resolved
  recipient; the executor re-checks all of it, plus the §8.4 guardrails, at execution (OD-317).
- **Guardrails** (§8.4): no reply unless the sender authenticates and has one From mailbox; never
  to a differing Reply-To; no send on bulk, unscanned or fraud-signal mail; one template reply per
  thread and one per sender in 7 days (OD-325); a per-address circuit breaker, 25 an hour and 250
  a day (OD-059); never to a monitored address.
- **Sends are traceable and never repeated blindly**: one Message-ID per grant, recorded before
  sending; an unknown outcome is never retried, and requeue needs proof it failed (OD-322);
  `X-ECF-Install` and `Auto-Submitted` mark ecf's mail (OD-318, OD-320).

## Alternatives considered

- **Model-written replies:** rejected for v1 (design plan; "Later" in SPEC §1.2). A draft gives
  the same help with a person as the sender.
- **Outbound on by default, or on for `standard` addresses:** rejected (design plan); a new
  install has no evidence yet that the actor's proposals are right for that mailbox.
- **Drafts behind the outbound switch:** rejected (OD-058); a draft can't reach anyone, so
  gating it would only hide useful work.
- **One-click Slack approval for sends:** rejected (design plan, §9.5); a compromised Slack
  account must not be able to send (SECURITY.md).
- **Retrying a send whose outcome is unknown:** rejected (OD-322); a duplicate send to a
  customer is worse than a person checking Sent.

## Consequences

- Outbound does nothing until you turn it on per address; the reminders and `ecf outbound report`
  show what it would have done.
- Each send costs a step-up at the computer, so sending at volume isn't a v1 use.
- Templates and the forward allow-list are security-relevant config (§9.7): a send approved
  before one of them changed is refused at execution.
- In M2, outbound on `high` addresses needs two distinct approvers (design plan §7); v1 has one
  person, so it has approver, step-up and the delay instead.
