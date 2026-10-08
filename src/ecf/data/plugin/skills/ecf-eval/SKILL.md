---
name: ecf-eval
description: Run the ecf eval registered with `ecf eval run --claude`: claim its cases and hand each to the ecf agent the service names. Run only when the person types /ecf-eval.
disable-model-invocation: true
---

You dispatch ecf's eval. You never read, classify or act on a case yourself, and you never call
get_message, record_classification or propose_action.

Run this loop yourself, in this session: never hand it, or any part of it, to another agent.
The only Agent spawns are the ones in step 4, each with the `agent` value the service gives;
no other subagent type, and no spawn to wait, track progress or call a tool for you.

Repeat until done:

1. Call `mcp__ecf__eval_next` (no arguments needed).
2. If `done` is true, go to the end. If `stopped` is present, stop at once and tell the person
   that line as it is.
3. If `items` is empty: when `preparing` is true or `in_progress` is above 0, call
   `mcp__ecf__eval_next` again (at most 5 times in a row with no items); otherwise go to the end.
4. Group the items by `spawn`. Give each group to one spawn of the Agent tool, using the
   group's `agent` value exactly as the subagent type. The prompt holds only one line per item,
   `id=<id> claim_token=<claim_token>`, and nothing else. Hand out one agent type at a time:
   spawns of the same `agent` may run in parallel, but wait for them all to finish before
   starting another agent's spawns.
5. When every spawn of the round has finished, go back to step 1. Don't use what a subagent
   says: outcomes come only from `results` in the next `eval_next` call.

At the end, call `mcp__ecf__eval_results` and tell the person, in three or four lines: the
state, how many cases are done of the total, and, when there are metrics, the correct count of
the confirmed cases, the accuracy, the unsafe count and whether the gate passed. Quote nothing
else. If the eval isn't done, say that typing /ecf-eval again carries on.

Stop early if the person asks you to, or if a tool says the session isn't authorized. If a
spawn fails, don't retry it in this round; its claim expires and the case comes back later.
