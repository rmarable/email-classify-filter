# Timing per spawn in Claude evals (v2.1.1)

- **Status:** draft 1 (2026-10-11). Release `v2.1.1`, worked on after `v2.1.0` ships (operator
  decision 2026-10-11). Not built.
- **Base:** `main` after the `v2.1.0` merge.
- **Decisions:** SPEC once built; until then this document. SPEC §1.2 holds the one-paragraph
  summary.
- **Review:** this changes eval tooling, so Phase R applies (CLAUDE.md, Adversarial review) before
  step 1.

## 1. Why

SPEC §16.3 plans "1 vs 5 messages per Sonnet spawn" (`--batch 1` against `--batch 5` on
`--sensitivity high`); it has not been run. The question is whether more messages per spawn change
accuracy, cost and **time**. Today a Claude eval can't answer the time part except for whole runs.

## 2. What is recorded today

Checked on the C baseline `d880a988` and the code, 2026-10-11:

- **Per case** (result `cases[]`): answers, field scores, actions. `classifier_ms` is null for
  every Claude case (`ecf/eval/results.py:56` is filled only by `evalrun`); no spawn, no claim.
- **Per run** (`summary.claude.groups`): per model and source, API calls, tokens, cost, seconds
  per API call (median, p95).
- **`claude_calls`** (migration 0022): one row per API request: `ts`, `session_id`, `model`,
  `source` (`main`, `agent`, `agent_summary`), tokens, `duration_ms`, `cost_usd`. No agent, spawn
  or case.
- **Claims** (`claude_eval.Work`): `session`, `expires`, `claim`, `need`, `agent`, held in service
  memory only; no claim or answer time is kept. The spawn label (`_spawns`,
  `claude_eval.py:571`) is set per round (`ecf-classifier-high-1`, `-2`, …) and reused next round.
- `--batch` sets only the items per `-high` spawn. On `standard`, one `ecf-classifier` spawn takes
  each round's items (up to 10, `claude_review.LIMIT_DEFAULT`).

So only whole runs compare (first to last call, Sonnet agent calls, tokens, cost), and a stall
(`d880a988` stopped about 35 minutes) distorts the time.

## 3. Change

Eval data only: closed values, IDs and times; no message content (privacy statement unchanged).

1. **Per claim, in memory** (`Work`): when `eval_next` claims an item, record the round number
   (a counter on `Run`, one per `eval_next` that returns items), the spawn label, the agent, the
   stage (`classify`, `act`, `repeat`) and the claim time. When `record_classification` or
   `propose_action` accepts the answer, record the answer time. An expired or invalid claim is
   recorded with its outcome and no answer time.
2. **Per case, in the result**: `claims`, a list of `{stage, agent, spawn, round, claimed_s,
   answered_s, outcome}` with times in seconds from the run's registration (no wall-clock dates
   per case). For a classify claim, `classifier_ms` = answer time − claim time, so the existing
   field is no longer null (it then includes the session's spawn delay; documented, see §5).
3. **Per run, in the summary** (`summary.claude.spawns`): per agent, the number of spawns, items
   per spawn (p50, max), claim-to-answer seconds per item (p50, p95), spawn duration (first claim
   to last answer of one spawn label in one round; p50, p95), idle time between rounds (sum), and
   active time (run duration minus gaps over a threshold, so a stall is shown and set aside).
4. **`ecf eval compare`** prints the spawn figures for each Claude side and warns when the two
   runs used a different `batch`.
5. **SPEC**: §16.2 (result fields), §16.3 (the comparison now has its time figures), §1.2 (item
   moved out), CHANGELOG line.

Not changed: `claude_calls` (no case IDs on telemetry rows; the claim times are enough), the
`/ecf-eval` skill, MCP tools, preset behaviour in production (`classifier_high_batch`).

## 4. Steps

0. Phase R on this plan (four read-only Fable agents; findings in
   `state-archive/timing-spawn/review-findings.md`); operator decides; draft 2.
1. Claim and answer times on `Work`, round counter on `Run`; tests with a fake clock (claim,
   answer, expiry, re-claim, invalid answer).
2. `claims` per case and `classifier_ms` for Claude classify claims in the result; result-format
   tests; older results still load (fields optional).
3. `summary.claude.spawns`; `ecf eval compare` lines and the batch warning; tests on a synthetic
   result.
4. SPEC and CHANGELOG; full suite; macOS merge gate.
5. **Measurement** (paid, operator's go-ahead each run): two C runs on the frozen set,
   `--sensitivity high --batch 1` and `--sensitivity high --batch 5`, same pins, same day if
   possible; compare accuracy (Newcombe, per field), cost and the spawn figures. Results into
   SPEC §21.2; an ADR only if the result changes `classifier_high_batch`'s default (OD-052).

## 5. Limits and open questions

- Claim time is when the service hands out the item, not when the spawn starts; the gap is the
  session's own delay. Per-item time inside a batch spawn is sequential, so later items include
  earlier ones' time; spawn duration is the fairer figure.
- One session per run is assumed for spawn figures; a run continued in a second session (typing
  `/ecf-eval` again) restarts the round counter per run, not per session (to confirm in step 1).
- Version: an added eval feature; under OD-480 that could be a minor release. The operator chose
  `v2.1.1` (2026-10-11).
- Whether `standard` runs need a `--batch`-like split for `ecf-classifier` is out of scope.
