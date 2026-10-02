---
name: ecf-review
description: Work through the ecf review queue: claim waiting emails and hand each to the ecf agent the service names. Run only when the person types /ecf-review.
disable-model-invocation: true
---

You dispatch ecf's review queue. You never read, classify or act on an email yourself, and you
never call get_message, record_classification or propose_action.

Repeat until done:

1. Call `mcp__ecf__review_queue` (no arguments needed).
2. If `items` is empty, stop and go to the end.
3. Group the items by `spawn`. Give each group to one spawn of the Agent tool, using the
   group's `agent` value exactly as the subagent type. The prompt holds only one line per item,
   `id=<id> claim_token=<claim_token>`, and nothing else. Hand out one agent type at a time:
   the spawns of the same `agent` may run in parallel, but wait for them all to finish before
   starting another agent's spawns.
4. When every spawn of the round has finished, go back to step 1. Don't use what a subagent
   says: outcomes come only from `results` in the next `review_queue` call.

If `review_queue` returns `stopped`, stop at once and tell the person that line as it is.

At the end, tell the person, in two or three lines: how many items were handed out, and the
outcomes from `results` counted by outcome. Quote nothing else.

Stop early if the person asks you to, or if a tool says the session isn't authorized. If a
spawn fails, don't retry it in this round; its claim expires and the item comes back later.
