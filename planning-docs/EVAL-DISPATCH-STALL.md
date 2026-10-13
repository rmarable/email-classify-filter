# Eval and review dispatch stalls

- **Status:** draft 2 (2026-10-12), findings S1-S13 accepted by the operator; not built. No release
  assigned.
- **Base:** `main` at `ed31aaa` (2.2.3).
- **Evidence:** run H1 `ee1915bc` (SPEC §21.2, "Haiku 5.5 as the classifier", Stalls), the
  `d739c93b` stall (SPEC §21.2, timing plan 6a), and Claude Code's MCP logs for the `ecf claude`
  work folder (`~/Library/Caches/claude-cli-nodejs/<work-folder slug>/mcp-logs-<agent>/`).
- **Review:** two read-only Fable agents on draft 1 (safety and correctness, design; CLAUDE.md,
  review sized to the change). Findings S1-S13 in `state-archive/eval-dispatch-stall/
  review-findings.md` (gitignored); draft 2 applies them.

## 1. What happens

- **Lost submissions (S1, S2).** Parallel spawns of one agent type share one `ecf-mcp` agent
  server in Claude Code. When the first of them finishes, Claude Code restarts that server
  (MCP log: SIGINT, then "Connection closed" on every call in flight, then a new connection; one
  start per burst, not per spawn). A call whose reply was lost had often succeeded: the agent
  retries, the item is already held or settled, and the retry fails "that claim isn't current"
  (`claude_eval.py:686-689`), using up its tries. Agents then report done with items unrecorded.
  Seen in H1 (Haiku classifier) and in Sonnet and Opus runs (`mcp-logs-ecf-classifier-high`,
  `mcp-logs-ecf-actor-high`, 2026-10-12): not model-specific.
- **One stuck claim blocks the run.** `eval_next` hands out one agent type per round and none
  while the session holds a claim for another (`claude_eval.py:569-580`); the round's type is
  the first free case's (`:577`, S7). A claim lasts `CLAIM_TTL`, 15 minutes
  (`claude_review.py:89`).
- **The session gives up early.** The skill says to call `eval_next` "at most 5 times in a row
  with no items" (`ecf-eval/SKILL.md` step 3); each call waits at most `WAIT_S` 6 s
  (`claude_eval.py:601`). So about 30 s against 15 minutes. In H1's second stall the session also
  claimed a round and spawned nothing, judging that earlier spawns hadn't returned; step 5 both
  requires "when every spawn has finished" and allows "go back to step 1 anyway".
- **`/ecf-review` has the same rules** (`claude_review.py:158-168`, its skill's "at most 5 times",
  the same TTL), and its claims are held by subagents too (S9).

## 2. Change

1. **No overlapping spawns of one agent type** (S1; `claude_eval.eval_next`,
   `claude_review.review_queue`): a session gets new items for an agent only when it holds no
   `claimed` item for that agent, and one round hands out one spawn's items (for the `-high`
   agents, the first spawn group only). Same-type spawns then never share the agent server while
   one of them ends. The rule against two agent types at once stays.
2. **Resubmission is idempotent** (S2; `_claimed`, `record_classification`, `propose_action`,
   and the review equivalents): each work item keeps its last accepted submission (session,
   fence, token hash, reply). A resubmission with that same token returns the stored reply instead
   of "isn't current". Any other token is refused as today.
3. **Submissions re-check the claim under the lock** (S4): `record_classification` and
   `propose_action` repeat the session, fence and token check inside `run.lock` before `_hold`,
   so an answer for a released claim can't land on the new one. `get_message`'s read stamp moves
   under the lock for the same reason.
4. **One shorter claim limit, with a grace window** (S3, S5, S10): eval and review claims last
   `WORK_CLAIM_TTL` 300 s (replacing the 15-minute `CLAIM_TTL` of the 2026-10-02 operator
   decision; SPEC records the change). Expiry keeps the existing outcome `claim_expired` (no new
   `ClaimOutcome` value). A released claim's agent counts as out for this session for a further
   `GRACE_S` 300 s, or until that agent's next submission is refused, so a still-running agent
   never overlaps another model's work (the telemetry model check stays sound).
5. **Prefer the agent already out** (S7): a round's agent is the one this session already has
   claims or grace for; otherwise the first free case's.
6. **The service says what to do next** (S6, S8): every `eval_next` / `review_queue` reply with no
   items carries `next`, fixed wording: `"call again"` with `frees_in_s` (seconds until the oldest
   claim or grace ends) while claims or grace are out or the run is preparing; `"end"` otherwise.
   Holds (answered, waiting for telemetry) count for `"call again"` for at most `HOLD_WAIT_S`
   120 s from their answer; after that `next` is `"end"` and they are judged at session end as
   today. The skills drop "at most 5 times" and obey `next`; step 5 becomes one rule: after a
   round's spawns, call `eval_next` again.
7. **Evidence correction** (S11): `5f5772a7`'s stall (a miscount, fixed in v2.2.0) is not this
   shape.

Not changed: the rule against two agent types at once, fencing, scoring, the main session pin
(S13: Sonnet and Opus runs show the same bursts).

Separate (S12): Claude Code's MCP logs under `~/Library/Caches/claude-cli-nodejs/` keep service
error text and outlive `ecf claude`'s transcript purge (SPEC §12.4). What they hold is checked
first; any fix is its own change.

## 3. Steps

1. Changes 1, 3, 5 with tests: no overlapping same-type spawns (eval and review); one spawn group
   per round for `-high` agents; submission refused under the lock after a release and re-claim
   (a fake clock between `_claimed` and `_hold`); preference for the agent already out.
2. Changes 2, 4 with tests: idempotent resubmission returns the stored reply and records once;
   a different token still refused; expiry at 300 s with `claim_expired`; grace keeps the agent
   out; holds are never released by expiry; result files save with every outcome.
3. Change 6: `next` and `frees_in_s` in both replies; skills updated (plugin digest changes, so
   client and service must match, §10.3); `eval_next_waiting`'s docstring and
   `tests/test_claude_eval.py` lose "5 empty replies"; MCP snapshots only if a tool description
   changes.
4. SPEC §10.3-10.4 (claims, rounds, `next`), §21.2 note; CHANGELOG line.
5. Diff review by two agents before the next gate eval; H2 is the live check, then a Sonnet C run
   gives claim timings for 10-item Sonnet spawns (S10).

## 4. Limits

- Serial spawns per agent type lower parallelism: rounds of one spawn each. H1's 10-item
  classifier spawns took 31 s median; a C run then takes longer in wall time but no longer stalls.
  Measured in H2.
- `WORK_CLAIM_TTL` 300 s and `GRACE_S` come from H1 and the 6b actor runs; a slower model or a
  larger batch could reach them, costing a redo, not a wrong result.
- Whether Claude Code restarts a shared agent server on every spawn end is inferred from its
  logs (2026-10-12, Claude Code 2.1.296), not from its documentation.
