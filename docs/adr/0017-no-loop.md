# ADR 0017: No `/loop`; Claude runs only on demand

- **Status:** accepted (OD-018, 2026-09-26); implemented in V1.4 (`ecf claude` and `/ecf-review`)
- **Context source:** SPEC §4.1, §4.3, §10.3; design plan
  (`docs/history/design-plan-2026-09-27.md`) §1 "Operating model", decision 4, the superseded
  list ("`/loop` removed from v1") and the Claude Code notes on `/loop`

## Context

An early design ran Claude continuously inside Claude Code with `/loop`: an `/ecf-watch` command on
a Haiku router session, with a 7-day expiry, day-6 reminders, a standby watcher and a fallback when
the plan limit ran out. That needed a long-running Claude session on the operator's plan, raised
unclear questions about such sessions under the Consumer Terms (`/loop` sessions are grouped with
non-interactive ones; design plan, Claude Code notes), and added jitter of up to half the interval
for intervals under an hour. Meanwhile the local service already runs the scheduled checks and the
local model on its own.

## Decision

- **No `/loop` in v1** (OD-018, §4.1). ecf runs only while the computer is on and awake; the local
  service runs the scheduled pre-check and the local-model checks at each address's fetch interval
  (10 minutes in business hours, 30 otherwise, by default).
- **Claude is used on demand only:** the operator opens `ecf claude` and types `/ecf-review` (or
  `/ecf-eval`). `claude -p` and scheduled Claude runs are not used (§4.1). Items for Claude wait
  at `awaiting_claude` until a review (§4.3).
- **An optional local fallback** (`claude_queue_timeout`, off by default; OD-019) hands items that
  waited too long to the local model, under `local_high_risk` and its own gate (§4.3).
- **Unattended Claude** is the M4 Bedrock roadmap item (§1.2).

## Alternatives considered

From the design plan:

- **`/ecf-watch` via `/loop` with a Haiku router session**, its 7-day expiry, day-6 reminders,
  `ecf watch --standby` and the in-session plan-limit fallback: removed from v1 on 2026-09-26; a
  standby watcher returns in M4.
- **A launch-time `claude "/ecf-watch"`:** superseded the same day by removing `/loop` (decision 4).
- **Mailbox-latency alerts:** moved to the always-on roadmap item with `/loop`.

## Consequences

- With preset C, nothing beyond the model-free pre-check and rules happens to an email until you
  run `/ecf-review` (or the fallback fires); fraud and regulator mail is still flagged and
  escalated by the pre-check (§4.3).
- Claude uses plan quota only while you review; there is no idle usage (§4.2).
- A computer that's off or asleep processes nothing; `resident: true` covers a computer left on
  (§13.7), and always-on hosting is M4.
