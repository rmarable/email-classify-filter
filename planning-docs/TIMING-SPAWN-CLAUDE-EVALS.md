# Timing per spawn in Claude evals (v2.2.0)

- **Status:** draft 2 (2026-10-11). Release `v2.2.0`, worked on after `v2.1.0` ships (operator
  decision 2026-10-11; was `v2.1.1`). Steps 1-5 built (2026-10-11); step 6 (measurements) not run.
- **Base:** `main` after the `v2.1.0` merge.
- **Decisions:** SPEC once built: §16.2 (what is recorded and printed), §16.3 (the comparisons),
  §7.5 (eval batching); the §1.2 roadmap item was removed when it was built.
- **Review:** Phase R done on draft 1 (2026-10-11; four read-only Fable agents; findings P1-P20 in
  `state-archive/timing-spawn/review-findings.md`). The operator accepted every recommendation and
  added the Opus-value test (step 6b). Draft 2 applies both; the P numbers below point to the
  findings.

## 1. Why

SPEC §16.3 plans "1 vs 5 messages per Sonnet spawn" (`--batch 1` against `--batch 5` on
`--sensitivity high`); it has not been run. The question is whether more messages per spawn change
cost and **time** without losing accuracy, to inform `classifier_high_batch`'s default (OD-052).
Time matters because SPEC §7.5 names plan usage, which grows with session turns and spawns, as the
reason to raise it; today a Claude eval gives time only for whole runs.

The operator also wants to know that Opus (`actor_high`) is used where it brings value (step 6b):
in the eval nearly all actor work goes to Opus, because nearly every card is a first-time sender
and so high risk (§16.2; `policy.high_risk`, `policy.py:159-164`).

## 2. What is recorded today

Checked on the C baseline `d880a988` (`standard`, batch 1) and the code, 2026-10-11:

- **Per case** (result `cases[]`): answers, field scores, actions. `classifier_ms` is null for
  every Claude case (`ecf/eval/results.py:56` is filled only by `evalrun`, `evalrun.py:676`); no
  spawn, no claim.
- **Per run** (`summary.claude.groups`): per model and source, API calls, tokens, cost, seconds
  per API call (median, p95). Plan usage (`plan`) is null in C results: it is read from ended
  sessions only (`claude_usage.py:139-141`) and the result is saved inside the live session.
- **`claude_calls`** (migration 0022): one row per API request: `ts` (the telemetry flush time,
  one value per batch, not the request time; `claude_usage.py:42-52`), `session_id`, `model`,
  `source` (`main`, `agent`, or the raw `query_source`, e.g. `agent_summary`,
  `generate_session_title`), tokens, `duration_ms`, `cost_usd`. No agent, spawn or case.
- **Claims** (`claude_eval.Work`): `session`, `expires` (claim time + 15 min), `claim`, `need`,
  `agent`, in service memory only. The first read of each claim is timestamped
  (`api.py:1147` -> `telemetry.read`, `telemetry.py:172-177`) and a submission carries `Hold.at`
  and `Hold.read_at` (`telemetry.py:105-106`, set at `api.py:1120`), but neither is kept on `Work`
  or in the result. `eval_next` takes one time per round (`claude_eval.py:496`). The spawn label
  (`_spawns`, `claude_eval.py:571-582`) exists only in the reply, restarts at `-1` each round and
  is reused.
- `--batch` sets the items per spawn of **every** `-high` agent (`claude_eval.py:577-580`),
  including `ecf-actor-high`, which production keeps at one item per spawn
  (`claude_review.py:192-194`, OD-297). SPEC §7.5's "the eval's `--batch` measures the same
  choice" is therefore wrong today (P1).
- The session sets the round size: `eval_next` takes `limit` 1-50 from the model
  (`mcp_server.py:290-297`); the skill passes none, so 10 (`claude_review.LIMIT_DEFAULT`).
- `ecf eval rescore` rebuilds each case and the summary from fixed field lists
  (`evalrun.py:1248-1249, 1257-1260, 1341-1347, 1396-1400, 1451-1452`), so new fields are dropped
  unless carried (P8).

## 3. Change

Eval data only: closed values, IDs and times; no message content and no new destination (privacy
statement unchanged). The result goes where results go today (`evals/<run>.json` 0600,
`--copy-to`); `eval_runs.metrics` and export bundles get the summary only; MCP `eval_results` is
unchanged (fixed keys). Production paths (`claude_review`, `classifier_high_batch`) are untouched,
except that `policy.high_risk` is rewritten over a reasons helper with identical results (item 1).

1. **Eval batching matches production** (P1): `_spawns` batches only the classifier-high agent
   (`role_of(agent) == "classifier_high"`); `ecf-actor-high` takes one item per spawn, as in
   `/ecf-review`. A behaviour change: test, SPEC §7.5 and §16.3, CHANGELOG.
2. **Per claim, in memory** (`Work`; P4-P6, P9, P12): when `eval_next` claims an item, record the
   session ordinal (`run.sessions`), the round (a counter on `Run`, one per `eval_next` that
   returns items; it carries across sessions), the round's `limit` and item count, whether the run
   was still `preparing`, the spawn label, the stage (`classify`, `act`, `repeat`) and the claim
   time. Then the first-read time (`get_message`), the answer time at submission (`_hold`, the
   moment `Hold.at` records) and the settle time. The outcome is one of `recorded`, `invalid`,
   `claim_expired`, `model_refused`, `session_ended` (`release_session`), `run_ended` (`_finish`),
   set on every path. For an act claim, the high-risk reasons (step 6b): a closed set
   `high_sensitivity`, `first_time_sender`, `fraud_signal`, `payment`, from a
   `policy.high_risk_reasons` helper that `high_risk` then uses (`high_risk(ctx) ==
   bool(high_risk_reasons(ctx))`, tested). All times from the injected `Clock` (P20).
3. **Per case, in the result** (P7, P8, P17, P18): `claims`, a list of `{stage, spawn, round,
   session, preparing, claimed_s, read_s, answered_s, settled_s, outcome, high_risk}` with times in
   seconds from the run's registration; `summary.claude.registered_at` holds the registration time.
   Fields are `Literal` where closed; no `session_id` (the ordinal only); no `agent` (the spawn
   label gives it). Claims are merged into each `CaseResult` at `_finish` (cases are scored before
   their repeats, `claude_eval.py:393-407`). `classifier_ms` stays null for Claude cases; SPEC
   §16.2 says it is a local model call's wall time. Relative times plus `registered_at` let a case
   be joined to its `claude_calls` rows: numbers, not content.
4. **Per run, computed in `ecf/eval/results.py` from `cases[].claims`** (P10, P11, P13), like
   `call_figures`, so a rescored copy gets them too; stored as `summary.claude.spawns`:
   - per agent: spawns, items per spawn (p50, max), read -> answer seconds per item (p50, p95),
     claim -> first read (dispatch delay; p50, p95), spawn duration (first read to last answer per
     session, round and label; p50, p95), settle lag (settle − answer; p50, p95);
   - per round: duration (claim to last answer) and the gap to the next round (the session's
     turn), summed per session;
   - run duration (first claim to last answer), the largest gap between consecutive events with
     its value (no threshold), rounds with `preparing` reported apart;
   - for act claims: the share routed to `actor_high` and a count per high-risk reason.
5. **`ecf eval compare`** (P16, P17): prints the spawn figures, `batch` and the per-model seconds
   per API call for each Claude side; warns through `input_warnings` (nested lookup, guarded on
   both results having `claude`) when `sensitivity`, `preset` or `models` differ; no warning on
   `batch`. Missing blocks print "no spawn figures"; reads use `.get` and type checks.
6. **Rescore** (P8): carries `claims` and `summary.claude`; a Claude result without a `claude`
   block compares as "batch unknown".
7. **SPEC and CHANGELOG** (P1, P19, P20): §7.5 (eval batching), §16.2 (result fields,
   `classifier_ms`, results readable by their version or newer), §16.3 (the comparisons and their
   figures), §1.2 (item moved out); CHANGELOG `## v2.2.0 (not yet tagged)` with the batching line
   and the new figures, and a note that a v2.1.0 client can't read a v2.2.0 result
   (`extra="forbid"`, `results.py:17, 43, 82`).

Not changed: `claude_calls` (no case IDs on telemetry rows), the `/ecf-eval` skill, MCP tools,
`classifier_high_batch`, the `actor_high` pin and routing. Size: about +40-70 KB on a ~216 KB
result; claims per case are bounded by rounds and dropped with the `Run`.

## 4. Steps

0. Phase R (done; P1-P20 accepted 2026-10-11). Draft 2.
1. Item 1 (eval batching), with a test beside
   `test_high_runs_one_case_per_spawn_unless_batched` (`tests/test_claude_eval.py:373`).
2. Item 2: claim records on `Work`, round and session counters, high-risk reasons; fake-clock
   tests for claim, read, answer, settle, expiry, re-claim, invalid, refused, session end, run end,
   a preparing round and a second session.
3. Items 3 and 6: `claims` in the result, merged at `_finish`; `classifier_ms` null; rescore
   carries `claims` and `summary.claude`; result-format tests (older results load; round trip
   through both rescore paths).
4. Items 4 and 5: spawn figures in `results.py`; compare lines and warnings; tests on a synthetic
   result, one with no `claude` block, and beside
   `test_compare_warns_when_the_runs_were_asked_differently` (`tests/test_eval_tooling.py:331`).
5. Item 7; full suite; macOS merge gate.
6. **Measurements** (paid, the operator's go-ahead each run; results into SPEC §21.2; an ADR only
   if a result changes a decision). Each run in its own `ecf claude` session, `--copy-to`, no other
   `ecf claude` session, Ollama idle.

   **6a. Batch 1 vs 5.** Two C runs on the frozen set, `--sensitivity high --batch 1` then
   `--batch 5`, same pins, back to back. They compare with each other only, not with `d880a988`
   (`standard`). Estimate: ~$9-10 and ~$6-8 API-equivalent, ~$15-20 in all, 2-3 h of the
   operator's time per run (P15; agent estimate from `d880a988` and SPEC §21.2, not re-checked).
   The batch-1 run also serves as the Sonnet arm of §16.3's unrun "Haiku vs Sonnet vs Opus on
   `high`".
   *Rule* (P2, P3): batch 5 is eligible as a default only if 0 unsafe, fraud-guard recall N/N,
   Newcombe lower bound of batch 5 − batch 1 above −5 (accuracy is a non-inferiority screen, not an
   estimate: 3 of 10 repeats differed in `d880a988`, so a 2-3 point difference can't be read), and
   the Sonnet classifier's tokens and $ per case fall. Time figures decide alongside cost: read ->
   answer per item and tokens per case first (they don't depend on how many spawns Claude Code runs
   at once); round and run wall time are reported but confounded (batch 1 runs up to 10 spawns per
   round in parallel, batch 5 two). The Haiku main session's cost is reported apart as dispatch
   overhead. SPEC §7.5's cross-item criterion can't be measured on the current set (0 of 268 cards
   are cross-item); the result says so and raising the default stays blocked on it unless the
   operator decides otherwise.

   **6b. Opus value (`actor_high`).** Two preset B runs on the frozen set, `standard`: the pinned
   models (Opus `actor_high`) and `--actor-model claude-sonnet-5-5` (moves `actor` and `actor_high`
   to Sonnet, `claude_eval.py:198-200`). Preset B reuses Gemma's classifications (OD-289), so the
   arms differ only in the actor and pair exactly. Estimate: a few dollars for both (B `standard`
   `2e26073e` cost ~$1.33 on the 189-card set; not measured for this set).
   *Compared:* per card, the three actor-sensitive safety checks (`must_escalate`, 102 cards;
   `must_not_hide`, 182; `injection_target`, 22), fraud-guard recall; the cards whose actions
   differ between the arms, listed with both actions for the operator to judge (no card labels
   expected actions beyond safety, so the actor's other value can't be scored automatically); cost
   and seconds per act claim; the share of act claims routed to `actor_high` and the count per
   high-risk reason.
   *Rule:* if the Sonnet arm has 0 unsafe, misses no `must_escalate`, follows no injection, and the
   operator judges its differing cases no worse, propose a separate decision: narrow what counts as
   high risk for the actor, or pin Sonnet for `actor_high`. Either changes production and the gate
   key (moves `live` addresses back to `assist`), so it needs its own OD (and an ADR if routing
   changes), not this release.

## 5. Limits

- Times are service-side: claim, first read and submission as the service sees them over the
  Unix socket. Claim -> first read includes the session's turn and the spawn's start-up; read ->
  answer is the subagent's own work on the item, valid inside a sequential batch.
- Wall time per round depends on Claude Code's Agent-tool concurrency, which is unverified; the
  claim -> read spread per round shows it.
- API load varies between runs (Sonnet agent 8.3 s median, 18.6 s p95 in `d880a988`): the two 6a
  arms run back to back and compare prints per-model seconds per call; if the Opus per-call median
  (identical work in both arms once item 1 is in) differs by more than ~20%, the time comparison is
  called confounded. Cache read/creation shares are printed per arm (batching creates once per
  spawn: a real but separate effect).
- Batch-5 spawn-mates are consecutive cards in `labels.jsonl` order, so similar cards share a
  context; production order is arrival order. Stated, not corrected (no shuffle).
- One run per arm: no noise floor exists for two identical C runs on this set; accuracy reads as a
  screen only (6a rule).
- Cost per spawn isn't attributable (`claude_calls` has no spawn); on `high` the Sonnet `agent`
  group is the classifier alone once item 1 is in. On `standard` the comparison doesn't hold.
- 6b: in the eval nearly every card is a first-time sender, so the eval's `actor_high` share
  overstates production's, where sender history builds up; 6b measures whether Sonnet is as good
  as Opus on high-risk items, not how often production calls Opus. Production's share can be read
  from `claude_calls` on a real install (not in scope).
- Plan usage stays null in C results (`plan` needs an ended session); not fixed here.
- Version: an added eval feature, so a minor release under OD-480: `v2.2.0` (operator decision
  2026-10-11; first chosen as `v2.1.1`). The IBAN resume fix follows as `v2.3.0`.
- Whether `standard` runs need a `--batch`-like split for `ecf-classifier` is out of scope.
