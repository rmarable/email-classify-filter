# Eval and review: free a dropped spawn's claims (build plan)

Draft 2, 2026-10-14: draft 1 + Phase R P1-P9, operator accepted all as recommended (P5 option A:
review too). Findings: `state-archive/eval-dropped-spawn/review-findings.md`. Status: approved
for build. Base: `main` at `bc38d45`. File:line refers to `src/` there. OD-501.

## What went wrong

S3b run 1 (`08016ba3`, preset C, 2026-10-13): round 6 handed out one `ecf-classifier` spawn of 3
cases (`ctrl-payroll-provider-w2-ready`, `ctrl-vendor-one-opener`, `custreq-add-seats`). The main
session never spawned it and called `eval_next` again; rounds 7 onwards were spawned and read
within about 3 s each. Nothing read the 3 claims, so they waited `CLAIM_TTL` (300 s), then `GRACE`
(300 s) held every other agent type back (`claude_eval.py:577-583`, `_round` 611): 68 actor cases
waited, about 10 minutes idle. Scores unaffected (the cases were re-claimed and recorded).

EVAL-CLAIM-MIX-UP (OD-499) P5 left this shape at `CLAIM_TTL` + `GRACE` (option a).

## Measured (21 Claude eval runs in accept `evals/`, 2026-10-11 to 2026-10-14)

`Claim.read` (`claude_eval.py:120`) is the first `get_message`; result files keep it as `read_s`.
A spawn's first read is the earliest `read_s` among its claims (same session, round, spawn label).

- Spawn first read after claim: n 2,555, p50 4.5 s, p95 8.7 s, p99 11.5 s, **max 73.5 s**
  (`1a8708cf` round 66, 10 `ecf-actor-high` spawns read at 12-73.5 s). A flat unread limit would
  need well over 73.5 s; 60 s would have freed 3 live spawns.
- **Why the tail** (session transcript `56494d2c`, run `1a8708cf`): the main session wrote each
  Agent call of round 66 in its own message, 3-11 s apart (11:39:55 to 11:40:56Z); each call
  returned in under 0.1 s (background launch); then it called `eval_next` (11:40:59Z). The slow
  starts are the session's own pacing, finished before its next call.
- Spawn first read after the session's **next round** was claimed: at most 12.3 s (n 2,494;
  `845303ba` round 15). Result files hold only rounds that handed out items, not empty calls, so
  this is against the next round, not the next call (P1 caveat).
- Unread spawns that ended `claim_expired`: `08016ba3` round 6 (this stall), `d157a948` round 99
  (the token mix-up, fixed by OD-499), `ee1915bc` round 20 (H1, 2026-10-12: the session's last
  classifier round; its next round, actor-high, came 1,311 s later). Draft 1's "later round" signal
  would have freed only the first two in time (P1).

## Change

1. **Signal (P1).** The service keeps, per session, the times of its `eval_next` calls (in memory;
   set at the start of `eval_next`, `claude_eval.py:577`, before the expiry loop). A spawn is
   **dropped** when none of its claims in that round has been read (P3: judged over every `Claim`
   of the round with that spawn label, not `claims[-1]`, since a re-claim appends) and the same
   session called `eval_next` after the round was handed out, at least `UNREAD_MIN` (60 s) ago.
   Another session's calls don't count. The session issues every Agent call of a round before its
   next call (Measured), so by then every spawn it will make has launched; a spawn that hasn't read
   60 s after that was not made. A spawn the hook refused (OD-497, exit 2) looks the same and is
   covered.
2. **Free.** In `eval_next`'s expiry loop (no timer: the session polls every few seconds while
   claims are out, `eval_next_waiting`), each dropped spawn's claims go back: outcome
   `claim_unread` (new), no `GRACE` (nothing ran). Its agent type counts as out for `TAIL` (15 s)
   from the free, as `_mismatch` does (`claude_eval.py:800-801`).
3. **A late agent (P2).** The freed tokens' hashes are kept per session in their own set (not
   `run.freed`, so `_anothers` never treats them as a mix-up), until their old expiry. A refused
   call that presents one extends `grace[agent]` by `TAIL` from then, so the late agent's final
   request can't overlap another type's window. Residual risk: a session that spawns after its
   next call (against the skill) can have its late agent's requests seen in another type's window,
   and the model check stops the session (fails closed, visible).
4. **Existing hole (P4).** `_anothers` excludes only the case's current hash
   (`claude_eval.py:779-780`): a case's own earlier, mismatch-freed token, presented by its late
   agent after the case was re-claimed, frees the new live claim. Exclude every earlier hash of the
   same case (keep them on the `Work`); review twin (`claude_review.py:416-423`, `438-450`).
5. **Review (P5, option A).** The same rule in `review_queue` (`claude_review.py:140`): per-session
   call times and a read stamp in memory under `_MEM`, keyed `(stable_id, fence)`, set in
   `get_message` before its audit row (`claude_review.py:494`, 530); the spawn's members from
   `claim_batches` (`claude_review.py:196-200`; a one-item spawn has no batch row, its claim is the
   spawn). The free is fenced as `_mismatch`: one `write_tx`, `UPDATE ... WHERE stable_id = ? AND
   fence = ? AND state = 'claimed' AND expires_at > ?` (`claude_review.py:438-443`), `reported = 0`
   so `results` carries `claim_unread`, and an audit row (as 452-455). Nothing touches items,
   grants or approvals. A service restart releases every claim (`release_all`, `service.py:193`),
   so losing the in-memory stamps costs nothing.
6. **`next` (P7).** `_next`'s `frees_in_s` (`claude_eval.py:641-649`; `claude_review.py:266-273`)
   includes the time a dropped spawn will be freed, so the session isn't told 300 s when it frees
   in 60.
7. **Visible (P6, P9).** `progress_line` counts unread frees as it counts mismatches
   (`claude_eval.py:1053-1066`). Log `claude_eval.claim_unread` / `claude.claim_unread` with round,
   spawn size and seconds since the hand-out, as the mismatch logs (`claude_eval.py:802`;
   `claude_review.py:450`).
8. **Unchanged:** a spawn with any claim read (an agent is running; its unread claims wait for
   `CLAIM_TTL` and `GRACE` as today); `CLAIM_TTL` 300 s; `GRACE` 300 s for real expiries; the
   skills and agent prompts (their "its claim expires and the case comes back later" stays true);
   the spawn guard hook (it makes no service call, `spawn_guard.py:9-10`, and blocks on failure).

## Not covered

- A session that stops calling `eval_next` (crash, turn ended): its claims wait as today; the
  session-end path already frees them.
- A late agent launched after the session's next call (P2 residual).

## Docs, tests, review

- `ClaimOutcome` adds `claim_unread` (`ecf/eval/results.py:55`); `ClaimRecord` is strict, so older
  clients refuse such result files; OD-499's release note (v3.0.0 files already need a v3 client)
  is extended rather than a new one.
- SPEC §10.4 **Claims** paragraph (SPEC.md:1410), §14.3 limits row (SPEC.md:1872), §7.5 history
  line; OD-501 row; CHANGELOG. MCP tool descriptions and `tests/snapshots/mcp_tools_*.json` don't
  change.
- Tests (injected clock), eval and review: a dropped spawn is freed `UNREAD_MIN` after the
  session's next call, and not before; a spawn with any claim read is never freed; a re-claimed
  sibling's new claim doesn't make a running spawn look unread (P3); another session's call
  doesn't count; no `GRACE`, `TAIL` set; a late agent's refused call with a freed token extends
  `TAIL` from then and isn't treated as a mix-up; a case's own earlier token never frees its new
  claim (P4); `frees_in_s` reflects the unread free; the progress line counts it; review's free is
  fenced, audited and reported, and touches no item, grant or approval.
- Diff review (two agents: safety and correctness, performance) before any gate run.
