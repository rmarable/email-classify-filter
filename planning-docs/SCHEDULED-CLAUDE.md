# Scheduled Claude review without AWS (plan, draft 1)

- **Status:** draft 1, 2026-10-20. **Deferred** (operator decision 2026-10-20): this work starts
  only after the Bedrock integration (roadmap M4, which needs M1) is complete. Scheduled runs
  would then likely use Claude on Bedrock instead of an Anthropic API key; Design §3 and the
  decisions below get revisited at that start. Nothing else decided; Phase R not run.
  Proposed: ADR 0035 (amends ADR 0017), ODs numbered when the operator approves (next free
  number at that time; OD-539 was the last on 2026-10-20).
- **Base:** `main` at `6d49748`. Code references below were checked against that commit.
- **Asked for** (operator, 2026-10-20): "dynamic execution of actions", so mail is handled
  without typing `/ecf-review`; first step toward "one day I want the actions to get done so I can
  stop sweating email".
- **Out of scope:** any change to what needs approval (policy, step-up, the outbound switch and
  its guardrails stay as they are); `/loop`; M4's Bedrock and always-on host; model-written sends.
  Autonomy (proposals that skip approval) is a separate later decision, informed by what this
  plan measures.

## Context

Today a C address's mail waits at `awaiting_claude` until the operator opens `ecf claude` and
types `/ecf-review` (SPEC §4.1, `SPEC.md:303-305`; ADR 0017). The pre-check still labels, flags
and escalates fraud and regulator mail at fetch, but nothing is classified, and no automatic
action (label, flag, corroborated hides) runs, until a review. Waiting mail gets a reminder at
24 h and a loud alert at 48 h (`src/ecf_server/claude_review.py:111-114`, `:1058-1111`).

What already exists and stays:

- **The session.** `ecf claude` (`src/ecf/claude_wrapper.py:434-477`) opens a service session
  (`POST /v1/sessions`, CLI caller only, `src/ecf_server/api.py:286-299`; version and plugin
  digest checked, `:427-445`), writes its own settings (`dontAsk`, allow and deny lists, the
  spawn-guard hook, `:205-226`), and runs `claude --setting-sources user --strict-mcp-config
  --mcp-config … --plugin-dir … --agents …` in `claude-work` (`:460-470`).
- **The review loop.** The `ecf-review` skill (`src/ecf/data/plugin/skills/ecf-review/SKILL.md`)
  calls `review_queue`, spawns exactly the agents the service names, and stops on `stopped` or
  `next: end`. The main session is Haiku 5.5; classifier, actor and their high-risk pairs are Opus
  5.5 (`src/ecf_server/data/models.lock:3-7`).
- **The checks.** Each submission is judged against telemetry: every subagent request in its
  window must be on the pinned model (`src/ecf_server/telemetry.py:16-26`, `_judge` `:280-294`);
  any refusal or built-in agent request stops the review (`:256-273`). The spawn guard refuses any
  Agent parameter beyond type, description and prompt (`src/ecf/spawn_guard.py:19-48`).
- **Policy after the actor** is unchanged by anything here: label, flag, escalate and leave run on
  their own; hides only with corroboration; drafts and sends need approval (SPEC §8.3).

### Why ADR 0017 said no, and what changed

ADR 0017 (`docs/adr/0017-no-loop.md:12-18`) gave three reasons: a long-running Claude session on
the operator's plan, unclear Consumer Terms for such sessions, and `/loop` jitter. This plan uses
no long-running session (one short `claude -p` run per batch of mail) and no `/loop`. The terms
question is now answered, and it decides the design:

- **Consumer Terms** (anthropic.com/legal/consumer-terms, effective 2025-10-08, fetched
  2026-10-20), §3 item 7: you may not "access the Services through automated or non-human means,
  whether through a bot, script, or otherwise", "Except when you are accessing our Services via an
  Anthropic API Key". A scheduled run on the operator's Pro or Max sign-in is automated access.
- **Claude Code legal page** (code.claude.com/docs/en/legal-and-compliance, fetched 2026-10-20):
  Free, Pro and Max users are under the Consumer Terms; Team, Enterprise and API users under the
  Commercial Terms. OAuth (plan sign-in) "is designed to support ordinary use of Claude Code";
  "Advertised usage limits for Pro and Max plans assume ordinary, individual usage".

So **scheduled runs use an Anthropic API key**, billed per token under the Commercial Terms, and
never the plan sign-in. Interactive `/ecf-review` keeps using the plan as today.

Side finding, not this plan's scope: the eval plan's DEV adapter runs `claude -p` from a script
on the accept install's plan sign-in (`planning-docs/EVAL-GENERATE-GRADE.md:483-489`; ADR 0034:20).
The same §3 item 7 reading applies there; raised to the operator separately.

## Design

A scheduled review is the same `/ecf-review` loop, run headless by the same wrapper, with an API
key, when the service sees mail waiting. Nothing about claims, checks, policy or approvals
changes.

1. **Trigger (service).** A new `claude_runs` job queue beside actions, Slack and fetch
   (`src/ecf_server/jobs.py:24-28`). After a fetch leaves items waiting for Claude
   (`claude_review.py:124-130`), the service queues one run if scheduling is on, no run is in
   progress, no interactive `ecf claude` session is open, the last run started at least
   `claude_schedule_min_gap` ago (default: the address's fetch interval, 10 min in business hours
   and 30 otherwise, `src/ecf_server/schedule.py:38-47`), and today's spend is under the cap. An
   empty queue never starts a run, so idle cost is zero.
2. **Launch (client process, spawned by the service).** The service runs `ecf claude --scheduled`
   as a child process (the service already starts children: `isolate.py:93`, `notify.py:85`).
   The wrapper opens a session as the CLI does today, then runs `claude -p "/ecf-review"
   --output-format json --max-budget-usd <per-run cap> --no-session-persistence` with the same
   settings, MCP config, plugin, agents, spawn guard and telemetry. Not `--bare`: it skips hooks
   (`claude --help`, 2.1.304), and the spawn guard is a hook.
3. **Auth, failing closed.** Scheduled runs use their own config dir
   (`<data_dir>/claude-config-scheduled`) that never holds a plan sign-in, and an `apiKeyHelper`
   in the scheduled settings: a small `ecf` command that asks the service for the key over the
   socket and prints it. The key never sits in an environment variable or a file (AGENTS.md
   Credentials). Before the run, `claude auth status --json` in that dir must report API-key auth;
   anything else stops the run and raises an alert. A separate secret from the model watch's
   `models-api-key` (`src/ecf_server/model_watch.py:60`), so the operator can give it its own
   Console workspace and spending limit.
4. **Cost guards.** Per run: `--max-budget-usd` (default $1). Per day: a cap (default $5) checked
   before each run from the recorded `cost_usd` (`src/ecf_server/claude_usage.py:39-70`, today
   shown as "API-equivalent"; under an API key it is the real cost, unverified until step 0).
   Reaching the daily cap pauses scheduling until the next day and posts once in Slack. The
   operator also sets a monthly limit on the key's Console workspace (outside ecf).
5. **Stops.** A `stopped` review (a refusal, a model-check failure, a built-in agent request),
   an auth failure or three failed runs in a row turns scheduling off (not just this run), raises
   `[ecf-alert] Operator Input Needed: scheduled review stopped (<reason>)`, and needs `ecf claude
   schedule on` to resume. An unattended run must not keep going after something unexpected.
6. **Switch.** `ecf claude schedule on|off|status`, off by default. `on` needs step-up (it spends
   money without asking each time), audits `claude.schedule_on`, and sends a Security Notice.
   `status`, `ecf status` and `doctor` show: on or off, last run (when, items, cost, outcome),
   next eligible run, today's spend against the cap.
7. **Slack and approvals** work as today: proposals that need approval post cards; escalations
   post. What changes for the operator is timing: mail is classified and its automatic actions run
   within about one fetch interval while the Mac is awake, instead of at the next review.
8. **Sleep.** Nothing runs while the Mac sleeps (SPEC §4.1); `resident: true` covers a Mac left on.

### Cost, estimated (unmeasured)

Per item: one classifier and one actor run on Opus 5.5 ($4 input / $20 output per 1M tokens,
cache reads $0.20; Claude API skill price table, cached 2026-10-06) plus a Haiku 5.5 main session
($0.10 / $0.50). SPEC §4.1 measured about 1.7-2.3k input tokens per item on Sonnet agents before
the move to Opus; Claude Code's own system prompt per subagent spawn comes on top and is mostly
cached. Rough range: **$0.03 to $0.10 per item**, so 50 items a day is roughly $1.50 to $5.
Step 0 measures it on synthetic mail before any default is fixed.

## Decisions for the operator

1. **Do it at all,** amending ADR 0017 (scheduled runs allowed, API key only, no `/loop`), as ADR
   0035. Recommended.
2. **Auth: API key only for scheduled runs.** Recommended (Consumer Terms §3 item 7). The
   alternative, a Team or Enterprise plan sign-in, is under the Commercial Terms, but the legal
   page ties OAuth to "ordinary use", so it is not recommended without asking Anthropic.
3. **Key handling: `apiKeyHelper` from the service,** separate secret
   (`ecf models api-key set --provider anthropic-review`, name to confirm). Recommended. The
   alternative, an environment variable in the child process only, breaks the credentials rule.
4. **Who triggers: the service spawns the wrapper.** Recommended: one scheduler that already
   knows the queue, business hours and sleep. Alternative: a launchd `StartInterval` agent running
   `ecf claude --scheduled`; simpler, but blind to the queue, so it starts runs on an empty one.
5. **Defaults:** off; minimum gap = fetch interval; $1 per run; $5 per day; stop after any refusal
   or 3 failures. Recommended as listed.
6. **Interactive sessions win:** no scheduled run while `ecf claude` is open. Recommended.
7. **Phase R size.** The plan changes when the classifier and actor run, not their logic, but it
   makes them run with nobody watching. Recommended: four Fable agents (safety, cost and design,
   correctness, performance).

## Steps

0. **Facts and checks** (each real-service check needs the operator's go-ahead; throwaway code in
   the scratchpad):
   - a. `claude -p "/ecf-review"` on a dev service with synthetic mail: does a skill with
     `disable-model-invocation: true` run from `-p`; do the PreToolUse hook, `--agents`, the MCP
     servers and OTEL telemetry behave as in an interactive session; does the status line run (if
     not, plan-usage figures are absent, which is fine under an API key)?
   - b. `apiKeyHelper` without `--bare`: used, and `claude auth status --json` reports it, in a
     config dir with no sign-in; confirm it can never fall back to a plan.
   - c. `--max-budget-usd` stops a run cleanly, and what the review leaves behind (claims expire
     after 300 s, `claude_review.py:102`).
   - d. Measured cost per item on the synthetic set under an API key; reconcile ecf's `cost_usd`
     with the Console's usage page.
   - e. Anthropic pages, cited with fetch date: Commercial Terms on automated use; API data
     retention and training defaults; Console workspace spending limits.
1. **Secret and helper:** the review key in the Keychain through the service; the `apiKeyHelper`
   command; tests.
2. **Wrapper `--scheduled` mode:** headless launch, its own config dir, the auth check, the
   JSON result read back; the skill's "the person types" wording (it is in the plugin digest
   checked at session open, `api.py:437-444`, not in the gate key). Tests with a fake `claude`
   executable.
3. **Service scheduler:** the `claude_runs` queue, the trigger rules, one run at a time, the
   interactive-session lock, the spawn of the wrapper. Tests.
4. **Cost guards and stops:** per-run and daily caps, the stop rules, the alert, the Slack line.
   Tests.
5. **Switch and status:** `ecf claude schedule`, step-up, audit, Security Notice, `ecf status`,
   `doctor`. Tests.
6. **Docs:** SPEC §4.1 and §10, ADR 0035 (ADR 0017 marked amended), README and operator guide
   (plain language: what runs, what it costs, how to turn it off), CHANGELOG.
7. **Real-service test** (operator go-ahead): one address on a test install, schedule on, one
   working day; record runs, items, cost per item, time from fetch to classification, and any
   stop. Results in SPEC.
8. **Release:** a minor release, numbered when it ships (OD-533); the release path is chosen then.

## Plan review findings

None yet (Phase R not run).

## Facts

- Consumer Terms §3 item 7, quoted above (anthropic.com/legal/consumer-terms, effective
  2025-10-08, fetched 2026-10-20).
- Claude Code legal and compliance page, quoted above (code.claude.com/docs/en/legal-and-compliance,
  fetched 2026-10-20).
- Claude Code 2.1.304 `claude --help` (2026-10-20): `--max-budget-usd <amount>` "only works with
  --print"; `--bare` skips hooks, and its auth is "strictly ANTHROPIC_API_KEY or apiKeyHelper via
  --settings (OAuth and keychain are never read)".
- `claude -p` with structured output and no tools works on 2.1.303 (2026-10-20,
  `planning-docs/EVAL-GENERATE-GRADE.md:483-489`); a full `/ecf-review` under `-p` is unverified
  (step 0a).
- API prices: Claude API skill price table, cached 2026-10-06 (unverified against the live
  pricing page; step 0e).
