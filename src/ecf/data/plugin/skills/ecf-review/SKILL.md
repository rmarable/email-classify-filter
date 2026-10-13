---
name: ecf-review
description: Work through the ecf review queue: claim waiting emails and hand each to the ecf agent the service names. Run only when the person types /ecf-review.
disable-model-invocation: true
---

You dispatch ecf's review queue. You never read, classify or act on an email yourself, and you
never call get_message, record_classification or propose_action.

Run this loop yourself, in this session: never hand it, or any part of it, to another agent.
The only Agent spawns are the ones in step 3, each with the `agent` value the service gives;
no other subagent type, and no spawn to wait, track progress or call a tool for you.

Repeat until done:

1. Call `mcp__ecf__review_queue` (no arguments needed).
2. If `stopped` is present, stop at once and tell the person that line as it is. If `items` is
   empty, do what `next` says: `call again` means go back to step 1, however many times it
   takes; `end` means go to the end. Never decide this yourself.
3. Group the items by `spawn`. Give each group to one spawn of the Agent tool, using the
   group's `agent` value exactly as the subagent type; the spawns may run in parallel. The prompt
   holds only one line per item, `id=<id> claim_token=<claim_token>`, and nothing else. Every
   item returned is already claimed for you: always spawn it, in this round.
4. Go straight back to step 1: don't wait for the spawns, and don't track which have finished.
   The service knows which claims are out; while they are, `review_queue` waits a little and its
   `next` says `call again`. A spawn finishing needs nothing from you but step 1. Don't use what
   a subagent says: outcomes come only from `results`. Never end your turn to wait: only `next`
   saying `end` ends the loop.

At the end, tell the person, in two or three lines: how many items were handed out, and the
outcomes from `results` counted by outcome. Quote nothing else.

Stop early if the person asks you to, or if a tool says the session isn't authorized. If a
spawn fails, don't retry it; its claim expires and the item comes back later.
