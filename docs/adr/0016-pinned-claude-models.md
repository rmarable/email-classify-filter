# ADR 0016: Claude models are pinned by full ID per release, with a weekly watch

- **Status:** accepted (OD-014, OD-053, 2026-09-26; OD-268, OD-272, OD-273, OD-277, OD-278,
  OD-285, OD-307, 2026-10-02); implemented in V1.4 (steps 2, 6, 10 and 13)
- **Context source:** SPEC §7.5, §7.6, §9.3, §21.2; design plan
  (`docs/history/design-plan-2026-09-27.md`) "Runtimes and models"; `ecf_server/claude_pins.py`,
  `data/models.lock`, `telemetry.py`, `claude_review.py`, `model_watch.py`

## Context

Presets B and C use Claude models (§4.2). The go-live gate (ADR 0006) says an address may act on
its own only after its models have been reviewed and evaluated, so the gate has to know exactly
which model did the work. A family alias can move to a newer model without notice, a subagent's
model can be overridden per invocation or by an organization's `availableModels` (verified
2026-09-27, code.claude.com), and Anthropic retires models on a schedule (§7.5).

## Decision

- **Full model IDs, one per role, ship in the wheel** in `models.lock` (OD-014): the main session,
  `classifier`, `classifier_high`, `actor` and `actor_high` (§7.5). A release moves a pin; nothing
  else does, except an install-wide override per family (step-up, Security Notice; OD-277).
- **The gate binds every model the address's preset uses** (OD-278). Any pin change starts the
  review count again and moves a `live` address back to `assist` (§9.3).
- **The service checks the model that actually did the work.** A Claude submission is held until
  Claude Code's telemetry shows every subagent request in its window on the pinned model, and is
  refused otherwise, or when unbound at session end (OD-268, rebuilt as OD-307 after the B shadow
  run showed the first binding could accept a wrong call). Refusals stop the review and raise a
  System Error.
- **A weekly model watch** (OD-053, §7.6): locally the Models API, with an optional API key;
  page parsing of Anthropic's models and deprecation pages in a weekly CI canary. A scheduled
  retirement raises `Model Retirement Scheduled`; a newer model goes on the daily-summary line.
  A newer model is never adopted automatically. `ecf claude` refuses to open with a retired pin
  (OD-273).
- **The release-index part of the watch waits for `v1.0.0`**, since no index exists yet (OD-272).

## Alternatives considered

From the design plan's superseded list ("Runtimes and models") and the Group 1 decisions:

- **Family aliases** (`haiku`, `sonnet`): replaced by full IDs pinned per release (R4-24); an alias
  can change the model under a passed gate.
- **The original version pinning** and **gates bound to aliases only:** replaced by full IDs plus
  the weekly watch.
- **Trusting the subagent frontmatter's `model`:** not enough, since an invocation or an
  organization setting can override it (§7.5); hence the telemetry check.
- **Binding each call to the API request just before it** (OD-268 as first built): replaced by
  OD-307's window check after a capture showed tool events are logged before their request (§21.2).

## Consequences

- A model retirement before a release needs the override, and the affected addresses drop to
  assist as with a release (§7.5).
- Every pin change costs a new gate: reviews and the synthetic eval on the new model.
- `/ecf-review` depends on Claude Code's telemetry; if telemetry stops, submissions are refused
  rather than applied.
- Without a stored API key the local watch learns of retirements only through ecf releases and
  `ecf models status`.
