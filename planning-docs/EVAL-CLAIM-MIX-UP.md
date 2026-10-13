# Eval claim-token mix-up: fix plan

Draft 2, 2026-10-13 (draft 1 + Phase R P1-P9, operator accepted all, P5 option a; findings in
`state-archive/eval-claim-mix-up/review-findings.md`). Status: planned; build on `main` (S2 merged
at `845af58`). OD-499.

## What went wrong

S2 run 2 (`d157a948`, preset C, main session Haiku 5.5): at 17:48:55Z the main session spawned
the agent for case `6bc2472b…` with case `704b6d94…`'s claim token. The service handed out the right
pair; the session copied the wrong token into the spawn prompt. The agent's `get_message` got
"that claim isn't current" (correct), and the agent, as `classifier.md` tells it, moved on and
replied `done`. The claim stayed out until `CLAIM_TTL` (300 s, 17:53:55Z); then `GRACE` (300 s)
held back every other agent type, so all 67 remaining cases (actor-high, Opus) waited until
about 17:59Z. About 10 minutes idle; scores unaffected (the case was re-claimed and recorded in
6 s).

Rate: first expired claim in the last five full runs (6846d041, e11c92a3, 845303ba, f60fb8bc:
1,466 claims, all `recorded`; then d157a948).

## Changes

### 1. The service writes each spawn's prompt (prevents)

- `eval_next` adds `spawns: [{agent, prompt}]`, one per Agent spawn; `prompt` is the exact text
  (the `id=<id> claim_token=<token>` lines of that spawn's items, which `classifier.md` and
  `actor.md` already expect).
- `items` keep `id`, `need`, `agent`, `spawn` and **drop `claim_token`** (P3): tokens appear only
  inside `spawns[].prompt`, so a session that groups `items` the old way has nothing to copy, and
  can't spawn a batch twice. No compatibility cost: a session whose plugin digest differs from
  the service's is refused at open (`api.py:448-453`).
- `ecf-eval` skill step 4: give each spawn's `prompt` to one Agent spawn exactly as given, with
  its `agent` as the subagent type.
- Same for `/ecf-review` (`claude_review.py`, `ecf-review` skill): same rules, same risk, on real
  mail. Its `spawns` follow `_spawn`'s grouping, including per-address `classifier-high` batches.
- The spawn guard (OD-497) is unaffected: the session still sets only `subagent_type`,
  `description` and `prompt`; a multi-line prompt passes (`spawn_guard.py`).

### 2. A mixed-up token frees the case at once (recovers)

- **Signature (P1):** an agent presents, for case X, a token whose hash equals the **live**
  `token_hash` of **another** case Y claimed by the **same session**, and X's claim is
  `claimed` (not held, not expired). Only then does the service end X's claim at once with the
  new outcome `claim_mismatch` and return X to the free pool. Y's claim is untouched.
- **Wrong agent type (P6):** an agent of the wrong type presenting X's own live token from the
  same session (today `ForbiddenProfileError`, checked before the token) frees X the same way.
- **Everything else keeps today's refusal** and expiry: a stale-fence token (a late agent after
  an expiry, the case GRACE exists for), a garbage token, another session's token, a held claim.
  This also bounds abuse: an agent can free only cases whose tokens it already holds.
- **Grace (P2):** a mismatch sets `grace[agent] = max(existing, now + TAIL)` for the presenting
  agent's type, as `_hold` does (`claude_eval.py:880`; review twin), not `GRACE` and not none:
  the refused agent still makes its final request, which must not overlap another agent type's
  window (the model check, `telemetry._judge`, would refuse it and stop the session).
- `/ecf-review`: the free is a fenced write (`UPDATE claims … WHERE stable_id=? AND fence=? AND
  state='claimed'` inside `write_tx`) and writes an audit event (P7); grants, approvals and
  step-up are downstream of `settle` and untouched. The signature needs no read stamp, so no
  migration.

### 3. Count it

- Add `claim_mismatch` to `ClaimOutcome` (`ecf/eval/results.py:55`; without it the run ends with
  no result file, P4). In telemetry and the progress line when non-zero.
- Older clients' `ecf eval compare` refuse a result file with the new outcome (SPEC §16: readable
  by the writing version or newer): a release note.

## Not covered (P5, option a)

A prompt line the session drops, or an item an agent skips, makes no call, so its claim still
waits `CLAIM_TTL` + `GRACE`. With change 1 the session no longer edits lines, so this should be
rare; if it recurs, the options are in the findings file (free an unread claim once its spawn's
other items are answered, plus TAIL; or a flat unread limit).

## Not changing

`CLAIM_TTL` 300 s; `GRACE` 300 s for real expiries (the service can't know whether an agent is
still running); agent prompts (`classifier.md`, `actor.md`: "move on" stays right).

## Docs, tests, review

- SPEC §7.5 (dispatch), §10.4 (tool rows) and the V1.4 text "with only `id` and `claim_token`";
  MCP tool descriptions (`mcp_server.py`, `eval_next` and `review_queue`) and
  `tests/snapshots/mcp_tools_*.json` (P4); OD-499 row; CHANGELOG line; release note.
- Tests (P8): a mixed-up token frees X at once with TAIL grace, Y untouched; X's right token is
  refused after the free (assert the refusal, not the fence); stale-fence, garbage,
  other-session and held-claim tokens unchanged; wrong agent type with X's own token frees X;
  `spawns` prompts carry every claimed item exactly once and `items` carry no token;
  `ClaimOutcome` accepts `claim_mismatch` and the result file is written; the same for
  `/ecf-review` (including per-address `classifier-high` grouping and the audit event).
- Phase R done (two Fable agents, P1-P9). Two-agent diff review (safety and correctness,
  performance) before the next gate eval run.

## When

On `main`, now that S2 is merged. About 200 lines of code plus tests.
