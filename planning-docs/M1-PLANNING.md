# M1 planning: AWS mode, single user

Status: plan accepted by the operator 2026-11-04; M1 started 2026-11-04 (OD-592 puts M1 next).
Not yet written: the roadmap document `docs/roadmap/m1-aws-mode.md` (step 3). This file holds the plan for writing it and the
operator decisions made while planning; the decisions get OD rows in SPEC (from OD-593) with
draft 1 of the roadmap document. Code references are as of `93e0fea`.

## Context

No M1 document existed. M1's design is spread over the immutable design plan
(`docs/history/design-plan-2026-09-27.md`: Scope line 31, decision summary, §2, §3a, §4, §7, §8,
§9, §10, §10a migrate, §11, §13, §14, §15, §16, §17 M1), SPEC §1.2, and ODs made since. The
roadmap rule (`.claude/rules/roadmap.md`) says a milestone's document is written at its start.
The result is one current M1 document that folds in the decisions since 2026-09-27 and marks what
is stale; Phase R runs on it before anything is built.

Starting M1 lifts the AGENTS.md line "don't load AWS skills until M1 starts": the global AWS
guidance applies (AWS skills, docs over memory, CDK).

## Operator decisions, 2026-11-04

1. **Privacy:** the operator's own AWS account and Amazon Bedrock become destinations, under
   strict rules: whole messages stay in memory only, never in S3, SQS, Lambda `/tmp` or any
   cache; only excerpts are stored. SPEC §12.4 is rewritten to match, and the AGENTS.md privacy
   constraint updated.
2. **Bedrock in M1:** in AWS mode, `/ecf-review` can run Claude through Bedrock, billed to AWS,
   still on demand only. Scheduled review stays in M4 (OD-578).
3. **Choosing the provider:** install setting `claude.provider` = `plan` | `bedrock`, default
   `plan`; changing it needs step-up. `ecf claude --provider bedrock` overrides it for one
   session. The status line and every `claude_calls` row show the provider; `ecf stats` splits
   by it; Bedrock spend counts against the AWS Budget; model pins hold both IDs; the model watch
   checks Bedrock availability. **Blocker if it fails:** Claude Code's Bedrock mode must work
   without keys in environment variables (an AWS profile) and keep the status line, telemetry,
   subagents and the spawn-guard hook (unverified; step 2b).
4. **Import/export identities:** people and roles are always exported. Identity links (Cognito
   user, Slack member) carry over on `ecf migrate` (same install); they are dropped on import into
   a different install, as OD-358 does today. Reconciles OD-358 with OD-579.
5. **Testing Lambda without cost:** the roadmap document gets a "Testing" section with three
   layers.
   - (1) moto unit tests in pytest and CI (plan line 734).
   - (2) A local container on **Colima**: the AWS Lambda Python arm64 runtime image through
     `sam local invoke` with the real build, plus DynamoDB Local and LocalStack's free tier (SQS,
     S3). Covers packaging, cold start, memory on 64 MB messages, Mangum, routes, Slack signature
     checks with signed fake payloads, and the tick logic.
   - (3) The real AWS dev stack, only for what can't be faked: IAM enforcement (plus the policy
     simulator and `cfn-policy-validator` without a deploy), Cognito, the API Gateway JWT
     authorizer, Lambda async retries and destinations, SQS FIFO retry timing, EventBridge
     Scheduler firing, alarms, Budget and CloudTrail, real Slack, Bedrock. Each run is torn down
     afterwards, with the Budget alarm on.
   - A per-service table (local full or partial, or real AWS only), every row checked in step 2b.
   - No tunnel services (ngrok and the like): they would be a new destination for Slack payloads.
6. **M5 is decoupled:** M1's supported platform is the AWS Lambda container image, not "Linux".
   M1 is not Linux support; ADR 0020 and M5 don't block it. Build and tests run in the Lambda
   image (Colima locally, and CI). Deployment: a zip on the managed runtime if step 2b finds
   AWS's Python 3.12 runtime at 3.12.6 or later; otherwise the container image, with an ECR
   repository per install, an image push at `ecf init`, and a change to the plan's no-bootstrap
   deploy. The roadmap document records which and why.
7. **Infrastructure as code:** CDK in Python, unchanged (OD-124). Terraform is not considered.
8. **Releases:** this planning work is docs only, no release. M1 ships as **one release at the
   end**, like M8: minor, numbered when it ships, unless `people.id` breaks the export format
   (major). Checked path (OD-492); the `ecf-release` skill is loaded at release time.
9. **Fact checks:** every dated fact and every "confirm at M1 start" item is re-verified before
   draft 1 (step 2b).
10. **Prompt caching and the AWS caches** are designed in the roadmap document (Design, below).

## Steps (each write, commit and push only after the operator's OK, AGENTS.md rule 3)

1. **Branch and worktree:** `m1-aws-mode` in `../ecf-m1`, from `origin/main` (`93e0fea`).

2. **Inventory: done 2026-11-04** (two read-only agents during planning). About 150 M1 items in
   the plan and about 25 "confirm at M1 start" notes; written to `state-archive/m1/inventory.md`.
   Spot-checked: SPEC §12.4 line 1822, OD-358, `.python-version` (`>=3.12.6,<3.13`).
   Contradictions found: §1.1 platform wording; the §1.6 ADR list; the Python upper bound
   (`.python-version` vs `pyproject.toml`); OD-358 vs OD-579 (decision 4); SQS has no per-message
   delay for OD-557's waits; §12.4 lists no AWS destination (decision 1).

3. **Fact re-verification (step 2b; not started).** Four parallel read-only research agents,
   using the AWS skills, the AWS docs, and the `claude-api` skill:
   - (a) Cognito and API Gateway;
   - (b) Lambda (Python runtime vs 3.12.6, async retries, concurrency), SQS (per-message delay),
     EventBridge Scheduler, S3, DynamoDB, KMS/SNS, CloudTrail, CDK tooling (cdk-nag,
     `cfn-policy-validator` OIDC, `BootstraplessSynthesizer`, code signing), SAM on Colima, the
     arm64 Lambda image, LocalStack free-tier coverage, dev-stack cost;
   - (c) Bedrock (Claude models and IDs, prompt caching, pricing, region), Claude Code's Bedrock
     mode, Anthropic prompt caching;
   - (d) Slack (request URL, the `https://localhost` install, the Socket Mode switch) and the
     plan's dated model and pricing facts.

   Each fact comes back confirmed, changed (with the new value) or not documented, with page URL
   and fetch date; Claude re-reads every "changed" one. Output: `state-archive/m1/facts.md`.

4. **Draft 1** of `docs/roadmap/m1-aws-mode.md`, in the roadmap rule's shape:
   - **Status:** draft 1, date, OD-592; code references at the commit checked.
   - **Scope:** plan §17 M1, plus Bedrock (OD-578) and the M2 foundations (OD-579).
   - **Decisions carried:** the plan's M1 ODs (OD-016, 021, 022, 080, 081, 090-092, 124, 150 …),
     then the later ones: OD-578; OD-579; OD-589/590 (M11 needs M1's public HTTPS endpoint, so
     the chat-surface seam is designed for it); ADR 0031 / OD-506 (presets A/B, Gemma and Ollama
     removed: drop the "local-model checks" and the Ollama digest in `GATE`); OD-516 (preset D
     held back, so C only); M8 drafts (OD-551, OD-583, OD-584); M6 `addresses.access` (parked;
     leave room in the broker); the decisions above.
   - **Design:** copied from the plan's M1 parts and updated; stale text is replaced with the OD
     that changed it cited, never silently dropped. Includes:
     - **Prompt caching, both Claude paths.** Claude Code `/ecf-review` (Anthropic): Claude Code
       applies caching itself; ecf controls the prompt layout (the schema `get_message` returns,
       SPEC line 1498; the agent files): stable parts first, the email last. Hits are measured
       from the per-request telemetry ecf records (`claude_calls`, SPEC line 1923; check whether
       it carries cache figures). Bedrock: ecf sets the cache points itself, on the same layout,
       so one design serves both. Both: no cached prefix holds one email's text when another is
       reviewed; a cost figure, cached vs uncached per email.
     - **AWS caches.** Every cache in the M1 design: JWKS (plan §8 line 511), DNS for DKIM/DMARC
       (SPEC line 866, SQLite today), the Slack DM channel ID (SPEC line 1408), the schema and
       rules loaded per Lambda container. For each: where it lives (Lambda memory, which lasts
       only while a container is warm, or DynamoDB), its TTL, and what a stale entry can break.
       No email content in any cache.
     - **Testing** (decision 5) and the deploy form (decision 6).
   - **Steps:** numbered build steps ending in the one release. Each real-service test is its own
     step needing the operator's go-ahead: Slack request-URL install, Socket Mode to request-URL
     switch, Cognito (more than one user, OD-579), Bedrock, dev-stack checks.
   - **Facts:** from step 2b, each with page and fetch date; a fact the docs no longer support
     reads "unverified"; the plan's bare "verified" claims are re-checked or downgraded.
   - **Open questions:** whether `people.id` needs a `data_format` bump (OD-579); the plan's
     "revisit at M1 start" items (the model watch's page parsing, DMARC/DNS in Lambda); region
     (plan: us-east-1); how `ecf upgrade` and release tags drive stack upgrades; the Python upper
     bound.
   - **Same commit:** SPEC OD rows for the decisions above (from OD-593); SPEC §1.2 M1 row points
     at the document; the §1.6 table (line 209) gets the real filename; §12.4 rewrite; AGENTS.md
     privacy constraint and AWS-skills line; one CHANGELOG line for the recorded decisions.

5. **Operator reads draft 1** and answers what they want to answer now; then commit on the branch.

6. **Phase R** (M1 moves facts, rules and policy into Lambdas): major size, eight read-only Fable
   agents (Opus if Fable is out of credits, OD-562): safety, measurement, cost and design,
   performance, plus AWS IAM and infrastructure security, identity (Cognito and the M2
   foundations), migration and data format, Slack request URL and M11 readiness. Findings in
   `state-archive/m1/review-findings.md`, merged as P1…, key claims spot-checked; the operator
   decides each; draft 2. Later rounds on Opus (OD-577).

7. **Merge:** docs only, so no macOS gate; merge into `main` with the operator's OK, push, and
   `gh run watch --exit-status`. Remove the worktree and branch (OK needed). Update
   `CLAUDE-STATE.md`. Commit messages end with the single trailer
   `Co-Authored-By: Claude <noreply@anthropic.com>` (OD-532).

## Verification

- Every [M1] line in the inventory has a home in the roadmap document (moved, updated with an
  OD, or listed as dropped with its reason), checked with a script listing plan lines against
  document anchors.
- Every OD cited exists in SPEC with the same meaning (grep). External facts cite page and date.
- CI green after the push.
