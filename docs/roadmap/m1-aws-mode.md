# M1 AWS mode, single user

**Status: draft 1, 2026-11-04. Not built.** M1 started 2026-11-04 (OD-592 put it next; OD-593).
Phase R has not run yet; its findings will be recorded below and the draft number raised.
Operator decisions made while planning: OD-593 to OD-600 (planning record
`planning-docs/M1-PLANNING.md`). Code references are as of `93e0fea`. Ships as one release at the
end (OD-600).

This document replaces, for M1, the design plan's M1 parts
(`docs/history/design-plan-2026-09-27.md`, "the plan": Scope line 31, §2, §3a, §4, §7, §8, §9,
§10, §10a "Moving to AWS", §11, §13, §14, §15, §16, §17 M1). Where this document and the plan
differ, this document wins, and SPEC wins over both. Stale plan text is replaced here with the
decision that changed it cited; nothing is dropped silently (the dropped items are listed under
"Removed from the plan's M1").

Real-service tests and every AWS dev-stack run need the operator's go-ahead each time; their code
stays in the session scratchpad.

## Scope

Run ecf in the operator's own AWS account, so mail is checked, fraud is flagged and approvals
execute with every computer closed. One person (the operator) uses it, but the data, identity and
export format are built for M2's teams (OD-579), so M2 adds features without changing them.

In scope:

- **Infrastructure:** CDK `infra/` (Python), two stacks per install, IAM under a permissions
  boundary, CI policy checks (plan §10, OD-124).
- **Services:** Lambdas `ecf-api`, `ecf-broker`, `ecf-slack`, `ecf-liveness`, the Cognito trigger
  and the auto-delete helper; DynamoDB (PITR); S3; SQS FIFO; Secrets Manager; SSM; EventBridge
  Scheduler; CloudWatch, SNS and a Budget (plan §2, §10).
- **Identity:** Cognito Essentials with TOTP, CLI and step-up app clients (plan §8).
- **Server-side pre-check** on the 1-minute tick (plan §3a).
- **Slack over a public request URL** (plan §9).
- **Operations:** `ecf init --mode aws`, `upgrade`, `destroy`, `doctor`, `budget set`,
  `retention set`, `billing activate-tags`, `iam deployer-policy`, dev stack, and `ecf migrate`
  local to AWS (plan §10a, §13).
- **Bedrock:** `/ecf-review` can run Claude through Amazon Bedrock instead of the operator's Claude
  plan, still on demand (OD-578, OD-595).
- **M2 foundations, run with one person** (OD-579): the people model, `people.id` on every grant,
  approval, answer, audit row and security notice, identities, role checks per address, approval
  mode and approver count fields, approvers as a list, people and roles in export and import.

Not in scope: multi-user features (M2), remote MCP for claude.ai (M3), scheduled Claude review
and the always-on host (M4), Linux as a local-mode platform (M5), Microsoft Teams (M11; M1 only
keeps the chat-surface seam ready for it, OD-589, OD-590).

## Decisions

### Carried from the plan (SPEC OD rows)

- OD-016: `aws` mode is DynamoDB with PITR plus S3, one base and one app stack per install, a
  dedicated AWS account recommended, default encryption.
- OD-021: fetches are asynchronous. OD-022: the scheduler tick is every minute.
- OD-080, OD-081: answer proposals stay off per connection (M3); refresh-expiry warnings on day
  25, then daily.
- OD-090: `ecf init` turns on CloudTrail log file validation, or creates a trail. OD-091: budget
  default $20. OD-092: grants HMAC-signed with a key under default AWS-managed encryption.
- OD-124: `infra/` is a Python CDK app. Terraform is not considered (operator, 2026-11-04).
- OD-150: service-side step-up only; the six-reviewer pass's M1 fixes.

### Since the plan

- **OD-578:** Bedrock moved from M4 to M1. Scheduled Claude review stays in M4.
- **OD-579:** M1 builds M2's foundations, run with one person.
- **OD-587 to OD-592:** M2 is "Multi-user teams"; M3 is remote MCP alone; Microsoft Teams is M11
  and needs only M1's public HTTPS endpoint; M1 next, then M11.
- **OD-506, ADR 0031:** presets A and B, Gemma and Ollama are removed. M1 has no local-model
  checks and no Ollama digest in `GATE`. **OD-516:** preset D is held back, so C only.
- **OD-458:** releases are GitHub Releases only (the plan's "PyPI trusted publishing" is stale).
- **OD-551, OD-583, OD-584:** model-written drafts (v3.2.0) are approved only when shown in full;
  the draft checker and the draft card are ported unchanged.
- **OD-557:** `slack_out` retry waits 30 s, 1, 2 and 5 minutes (see Queues: FIFO has no
  per-message delay).
- **OD-358:** import drops Slack routes and identity (reconciled by OD-596).

### New, operator decisions 2026-11-04 (SPEC OD-593 to OD-600)

- **OD-593, M1 started:** this document is written; the AGENTS.md rule "don't load AWS skills
  until M1 starts" is lifted, so the global AWS guidance applies (AWS skills, documentation over
  memory, infrastructure as code).
- **OD-594, privacy destinations:** the operator's own AWS account and Amazon Bedrock become
  destinations for email content, under the v1 rules: whole messages are held in Lambda memory
  only while processed, never written to S3, SQS, DynamoDB, Lambda `/tmp`, logs or any cache;
  only excerpts (up to the existing limits) are stored. SPEC §12.4 is rewritten (Design,
  Privacy).
- **OD-595, Bedrock and choosing the provider:** in AWS mode, `/ecf-review` can run through
  Bedrock, billed to AWS, on demand only. Install setting `claude.provider` = `plan` | `bedrock`,
  default `plan`; changing it needs step-up and a Security Notice. `ecf claude --provider bedrock`
  overrides it for one session. The provider shows in the status line, on every `claude_calls`
  row and in `ecf stats`.
- **OD-596, identities on import:** people and roles are always exported. Identity links
  (Cognito user, Slack member) carry over on `ecf migrate` (same install); they are dropped on
  import into a different install, as OD-358 does today.
- **OD-597, testing without cost:** three layers: moto unit tests; a local container layer on
  Colima (the AWS Lambda Python arm64 image through SAM, its runtime interface emulator, DynamoDB
  Local); and a real AWS dev stack only for what can't be faked, torn down after each run.
  LocalStack is not used (open-source repository archived 2026-03-23; the free plan is
  non-commercial). No tunnel services (they would be a new destination for Slack payloads).
- **OD-598, platform and runtime:** M1's supported platform is the AWS Lambda runtime, not
  "Linux": M1 is not Linux support, and ADR 0020 and M5 don't block it. The Lambdas run on the
  managed `python3.12` runtime (arm64), deployed as a zip, matching the Mac and CI (`.python-version`
  `>=3.12.6,<3.13`). A deploy-time check asserts Python 3.12.6 or later.
- **OD-599, Bedrock data retention `none`:** ecf sets the account's Bedrock data retention mode to
  `none` in the install's Region and `ecf doctor` checks it. Fable stays unavailable on Bedrock
  (it requires `aws_review`); ecf's pins are Haiku and Opus and the override can't name Fable
  (`src/ecf_server/claude_pins.py:54`). If a pinned model refuses `none`, the build stops and
  asks the operator.
- **OD-600, one release:** M1 ships as one release at the end: minor, numbered when it ships,
  unless `people.id` breaks the export format (then major). Checked path (OD-492).

## Design

### Architecture

```
Laptop (operator)                    AWS (base stack + app stack)                        External
─────────────────                    ────────────────────────────                        ────────
ecf CLI ─────────────JWT───────────► API Gateway HTTP API ─► ecf-api ─┬─► DynamoDB / S3 / SSM
ecf claude (Claude Code, on demand)                         (facts,    ├─► fetch jobs (async) ─► ecf-broker ──► IMAP/SMTP
  + ecf-mcp (stdio) ──JWT──► same API                        rules,     └─► actions.fifo ─► ecf-broker (execute)
  Claude: Anthropic (plan) or Bedrock (OD-595)               policy)
                                     Slack ──signed──► ecf-slack ─► ecf-api (DecisionService) ─► actions.fifo
                                     slack-out.fifo ─► ecf-slack (paced posts, DMs) ──► Slack
                                     EventBridge Scheduler ─► ecf-liveness ─► ecf-broker (pre-check) ─► ecf-api ─► slack-out.fifo / alerts
                                     CloudWatch alarms ─► SNS ─► admin email (independent of Slack)
Cognito (Essentials, TOTP): app clients CLI (PKCE loopback) · step-up (SRP only)
```

The functions' duties are the plan's §2 (unchanged unless noted):

- **`ecf-api`:** claim, lease and cursor; computed facts, fraud triggers, rules and policy;
  transitions; grants (the only writer); config writes; stage, sensitivity, pause, outbound;
  admin operations; export and import staging; role checks per address (OD-579). The JWT
  authorizer accepts only the CLI client's tokens with the route's scopes; handlers re-authorize
  the caller against `people` (the operator in M1).
- **`ecf-broker`:** the only holder of mailbox credentials. Fetches are asynchronous: `ecf-api`
  enqueues, `ecf-liveness` invokes the broker per address. Timeout at least 6 minutes; 1 GB memory
  (to measure, Steps). Executes only when invoked from `actions.fifo` (event source ARN checked).
  Asynchronous invocations get an on-failure destination, a standard SQS queue alarmed on depth
  (FIFO isn't allowed as a destination).
- **`ecf-slack`:** request-URL interactivity: signature, 5-minute timestamp window, `api_app_id`
  and `team_id` re-checked, payload dedupe, acknowledge within 3 seconds, then submit to
  `ecf-api`. Consumes `slack-out.fifo`. No Events API. Card updates use `response_url` only within
  its limit (5 uses in 30 minutes) and `chat.update` after that.
- **`ecf-liveness`:** the 1-minute tick: due pre-check fetches, audit flush to S3, approval expiry,
  grant voiding and `delayed → executing` through `ecf-api`, stale marking, "Needs you" refresh,
  digests, daily summary, reminders, weekly model watch, refresh-expiry warnings.
- **Laptop:** the CLI, and Claude Code during `/ecf-review` (the plan's "laptops run the models"
  meant Ollama, removed by ADR 0031). No direct AWS or mailbox access except the operator's
  deployer or admin credentials for `init`, `upgrade`, `budget set`, `retention set`, `destroy`,
  `billing activate-tags` and `doctor`'s read-only AWS checks; and, for the Bedrock provider, the
  AWS profile Claude Code uses (Bedrock and the provider).
- **The same code:** the Starlette app (`src/ecf_server/api.py:371`) is wrapped with Mangum
  (active, 0.22.0); the local Unix-socket service (`service.py:166`, `:633`) stays for local
  mode. Clients accept a server at N and N-1.

### People, identity and roles (OD-579)

- **Model:** `people {id, identities: [{issuer, subject}], roles: {address_id: role}, admin}`. In
  M1 there is one person, the operator, admin, with every address's approver role. Approval
  authority comes from roles, never Slack channel membership.
- **`people.id`** on every grant, approval, answer, audit row and security notice. Today there is
  no people module (`stepup.person()` returns the OS user, `src/ecf_server/stepup.py:93`; the
  grants table has no person column, `migrations/0001_initial.sql:88`). Local mode gets the
  model too: its one person is linked to the OS user, so local and AWS share one data shape.
- **Identities:** `{issuer, subject}` pairs link the Cognito user (`iss`, `sub`) and the Slack
  member ID (Slack team as issuer) to the person. `chat.Identity` (`chat.py:23-70`) stays the
  per-address display identity; it is not a person.
- **Per address:** approval mode (`single` only in M1), approver count (1), `stepup.totp` (`off`
  only in M1). Grants record approvers as a list.
- **Export format:** people and roles go in the bundle. Whether this needs a `data_format` bump
  (today 4, reading 3 and 4, `export_bundle.py:54`, `bundle_reader.py:50`) is an open question;
  a bump that still reads 4 keeps the release minor (OD-600).
- **Import (OD-596):** people and roles always import; identity links survive only a migration
  import into the same install.

### Cognito

As the plan's §8, with these changes from the 2026-11-04 fact checks:

- **Pool:** Essentials (default for new pools; 10,000 MAU free); admin-created users; TOTP
  required (SHA-1 codes; ±30 s window); SMS MFA off; device remembering off; deletion protection
  on; prevent user-existence errors; case-insensitive usernames; group `admins` (mirrors
  `people.admin`); resource server `ecf` with scopes `observe` and `work`. `AuthSessionValidity`
  is set explicitly (3 to 15 minutes; the step-up exchange must finish inside it).
- **CLI client:** authorization code with PKCE (S256) on an `http://localhost` loopback redirect
  (allowed over HTTP, any port); access and ID tokens 15 minutes; refresh 30 days with rotation
  (rotation doesn't extend the 30 days; 60 s grace period). **Changed:** with rotation on,
  `REFRESH_TOKEN_AUTH` isn't allowed, so the CLI refreshes through the token endpoint
  (`grant_type=refresh_token`) and the refresh flow is not enabled on the client. Tokens in the
  OS secret store. AWS advises against access and ID tokens under 1 hour with managed login (it
  doesn't shorten the 1-hour managed-login cookie): see Open questions.
- **Logout:** `ecf logout` and `revoke-device` revoke the refresh token and send the browser to
  Cognito's Logout endpoint, because `GlobalSignOut` doesn't clear the managed-login cookie.
  Proposed: `ecf login --fresh` uses `prompt=login` to force a fresh sign-in.
- **Step-up client:** `ALLOW_USER_SRP_AUTH` only; SRP `InitiateAuth`, then the TOTP challenge;
  the server issues a single-use nonce bound to (grant or change hash, person), carried in
  `ClientMetadata` on `RespondToAuthChallenge` (the trigger never sees `InitiateAuth`'s metadata);
  the pre-token-generation V2 trigger suppresses `aws.cognito.signin.user.admin`, adds the
  step-up claim, and ignores `TokenGeneration_RefreshTokens` events. The server requires
  `client_id` = step-up client, `token_use=access`, `auth_time` after the nonce and within 5
  minutes, `sub` = an identity of the approving person; consumes the nonce with a conditional
  write; revokes the session's refresh token. Step-up tokens in request bodies are redacted from
  audit and Powertools logs. Whether a refresh token is issued when the refresh flow is omitted,
  and whether a reused TOTP code is refused, are not documented: Step 7 tests both.
- **App clients in CDK only:** runtime never calls `UpdateUserPoolClient`.
- **Token validation:** PyJWT with a cached JWKS; `iss`, `token_use`, `exp`, `client_id` (access
  tokens carry no `aud`), scopes. API Gateway checks `aud`, else `client_id`, and any one of the
  route's scopes; it caches keys up to 2 hours and doesn't check revocation, so handlers check
  the person and, for step-up, the nonce.
- **Lost authenticator:** the break-glass runbook uses `AdminDeleteSoftwareToken` with admin AWS
  credentials; the operator re-enrolls at next sign-in (`MFA_SETUP`). Logged and emailed.
- **Cognito tested with more than one user** (OD-579), in the dev stack.

### Data and state

- **DynamoDB, three tables** (on-demand, PITR on, deletion protection on): main; grants and
  settings (`STAGE`, `OUTBOUND`, `PAUSE`, `SENSITIVITY`; written only by `ecf-api`); excerpts
  (written by the broker, read only by `ecf-api`, TTL). Keys as the plan's §4, plus `PEOPLE#<id>`
  and identity lookup items. The SQLite tables (migrations 0001 to 0043) map to these; the
  mapping table is written in Step 3 and reviewed.
- **TTL** deletes within days, not at the second: readers filter expired items.
- **Grants:** HMAC-SHA256 with a key in Secrets Manager, re-signed at every state change; the
  signature covers `{grant_id, action_hash, content_hash, status, approvers, stepup_nonce_id,
  expiry}` (approvers as a list, OD-579). The broker executes only a signed `approved` grant.
- **S3:** one bucket; Block Public Access; `BucketOwnerEnforced`; versioning; SSE-S3;
  `ExpectedBucketOwner` on every call; layout and lifecycle as the plan's §10. `logs/` is
  create-only, enforced by a bucket policy requiring `If-None-Match` on that prefix only (it would
  also block copies there; multipart uploads are exempted).
- **Retention:** `log_retention_days` in SSM; S3 lifecycle and log-group retention are stack
  parameters set by `ecf retention set` through a stack update.

### Queues, timers and delays

- **SQS FIFO** `actions.fifo` and `slack-out.fifo` (`MessageGroupId = address_id`), each with a
  DLQ; `maxReceiveCount` 5; visibility timeout at least 6 times the consumer's timeout
  (`actions.fifo` at least 36 minutes); batch at most 10; `ReportBatchItemFailures` and Powertools
  `SqsFifoPartialProcessor(skip_group_on_error=True)`; the action is marked `failed` after 3
  attempts. `actions.fifo` carries only a grant ID.
- **No per-message delay on FIFO** ("FIFO queues don't support timers on individual messages").
  So every wait (OD-557's `slack_out` waits, approval TTLs, the 1-hour AWS delays for
  sensitivity downgrades and security config, `delayed → executing`) is a `due_at` timestamp in
  DynamoDB, acted on by the next `ecf-liveness` tick. A failed message's visibility is set to its
  backoff with `ChangeMessageVisibility` before the partial-batch report.
- **EventBridge Scheduler:** `rate(1 minute)` (60 s precision) on `ecf-liveness`; standard SQS
  DLQ; a heartbeat alarm when `ecf-liveness` runs fewer than once in 5 minutes. About 43,200
  invocations a month, inside the free tier.
- **Lambda async:** 2 retries by default, events kept up to 6 hours; confirmed on the dev stack
  (Step 8).

### Slack over a request URL

- **Install:** a request-URL app with `https://localhost` as the OAuth redirect (temporary HTTPS
  listener with a self-signed certificate; `oauth.v2.access`; token to Secrets Manager). Slack
  requires HTTPS redirects but documents nothing about `https://localhost`; PKCE desktop
  redirects can't request bot scopes. The real-service test (Step 9) gates it; the fallback is the
  manual install.
- **Switch on migration:** the same app moves from Socket Mode to the request URL by
  `apps.manifest.update`, which replaces the whole manifest (send every field). An app receives
  clicks over one or the other at any moment, never both. Not documented, so tested in Step 9:
  whether a reinstall is needed, what happens to the app-level token (the plan revokes it), and
  whether the interactivity URL needs a manual verification.
- **Chat-surface seam (M11):** the request-URL handler sits behind `ChatSurface` (`chat.py:99`),
  so M11's Teams adapter can reuse `ecf-slack`'s pattern (public HTTPS endpoint, signed request,
  3-second acknowledgement). M1 adds nothing Teams-specific.

### Bedrock and the Claude provider (OD-595, OD-599)

- **How Claude Code reaches Bedrock:** `ecf claude` writes, into its own dedicated config's
  `settings.json` `env` block, `CLAUDE_CODE_USE_BEDROCK=1`, `AWS_PROFILE=<the profile chosen at
  setup>`, `AWS_REGION`, and the pinned model IDs (`ANTHROPIC_MODEL`,
  `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`, and
  `ANTHROPIC_DEFAULT_SONNET_MODEL` so the `sonnet` alias doesn't fall back to Sonnet 4.5). No
  keys in environment variables or files: the profile is an SSO or `aws login` session, refreshed
  through the `awsAuthRefresh` setting. Static keys and `AWS_BEARER_TOKEN_BEDROCK` are refused by
  `ecf doctor`.
- **Model IDs:** the pins hold both forms, e.g. `claude-opus-5-5` and the inference-profile ID
  `us.anthropic.claude-opus-5-5` (or `global.`, which has no regional premium; regional endpoints
  cost 10% more). Opus, Sonnet and Haiku 5.5 are not stated as open to every Bedrock account
  ("See Access"): `ecf init --mode aws` checks access per pinned model.
- **Retention `none` (OD-599):** set with the Bedrock control-plane `PutAccountDataRetention` in
  the install's Region (no console UI); `doctor` reads it back. If a pinned model's
  `allowed_modes` excludes `none`, init stops and asks.
- **What differs on Bedrock:** no WebSearch (ecf's review doesn't use it); no `/logout`; the
  status line has prompt-cache figures but no plan rate limits. Not documented: subagents and the
  spawn-guard hook on Bedrock (Step 10 tests both; if either fails, Bedrock is blocked, not
  shipped half-working).
- **Telemetry and cost:** each `claude_calls` row records the provider; `ecf stats` splits by it;
  Bedrock spend counts against the AWS Budget. Bedrock per-token prices weren't retrievable
  (2026-11-04): Step 10 records them from the AWS pricing page or the bill.
- **Weekly model watch:** also checks each pinned model is still offered on Bedrock (Bedrock sets
  its own retirement dates).
- **Go-live gate:** see Open questions (whether switching provider, same model, needs the gate
  re-run).

### Prompt caching (operator request 2026-11-04)

One prompt layout serves both providers.

- **Layout:** stable content first, the email last: the agent instructions, the effective schema
  (`get_message` returns it, SPEC §10.3), the rules and signals block, then the message. A change
  in the stable part (schema extension, rules) is a new prefix; per-email text never comes before
  a cache point.
- **Anthropic (plan):** Claude Code caches automatically. ecf measures it from the telemetry it
  already receives (`claude_code.api_request` events carry `cache_read_tokens` and
  `cache_creation_tokens`; the `claude_calls` rows, SPEC §13.4, gain those columns if missing).
- **Bedrock:** Claude Code on Bedrock caches by default too (`DISABLE_PROMPT_CACHING` off). Each
  request may hold up to 4 cache points, at least 512 tokens each on the 5.5 models; TTL 5 minutes,
  or 1 hour if requested. A request served in another Region writes a new entry, so the global
  inference profile may write more often than a regional one: Step 10 measures both and records
  the cheaper.
- **TTL:** 5 minutes is the default for API-key and cloud-provider sessions; `ecf claude` sets
  `ENABLE_PROMPT_CACHING_1H` only if Step 10 shows a saving for review batches spaced more than
  5 minutes apart (a 1-hour write costs 2× input, a 5-minute write 1.25×).
- **Isolation:** caching reuses an identical prefix only; ecf asserts no cached prefix contains
  one email's text when another email is reviewed (a test over the built prompts).
- **Cost figure:** cached versus uncached input per reviewed email, per provider, in `ecf stats`.

### Caches in the AWS design (operator request 2026-11-04)

Rule: no email content in any cache (OD-594).

| Cache | Lives in | Lifetime | If stale |
|---|---|---|---|
| Cognito JWKS (handlers) | Lambda memory | container life; refetched on unknown `kid` | a rotated key fails closed until refetch (API Gateway itself caches up to 2 h) |
| DNS for DKIM/DMARC (SPEC §7.3, 10-minute cap) | DynamoDB items with `expires_at` (SQLite today) | TTL respected, capped at 10 min | a changed key or policy is seen up to 10 min late, as in v1 |
| Slack DM channel IDs (SPEC §10.1) | DynamoDB | until Slack refuses the ID | a refused ID is re-opened with `conversations.open` |
| Effective schema, rules, policy | Lambda memory, keyed by config version | until the config version changes | every handler checks the version number in DynamoDB per invocation; a mismatch reloads |
| Secrets (mailbox, Slack, HMAC key) | Lambda memory | container life; re-read once on `AUTHENTICATIONFAILED` or a signature failure | a rotated secret fails once, then reloads |
| Bedrock and Anthropic prompt caches | the provider | 5 min or 1 h | none: identical prefixes only |

### Privacy (OD-594, OD-599)

SPEC §12.4 becomes mode-aware. AWS mode adds:

- **Your AWS account** (the install's Region): the broker fetches and holds full messages in Lambda
  memory only while processing; DynamoDB stores metadata, computed facts, classifications and
  excerpts (the same fields SQLite stores in local mode); S3 holds audit logs (no content),
  config, eval labels and transient encrypted export bundles; SQS carries IDs only; Secrets Manager
  holds the credentials. Nothing is written to Lambda `/tmp`; logs carry no content (the
  no-payload processor, SPEC §11).
- **Amazon Bedrock** (only when `claude.provider` is `bedrock`): what Anthropic receives during
  `/ecf-review` today. Retention mode `none`; invocation logging off (AWS's default); AWS states
  model providers can't access Bedrock prompts.
- Slack, Anthropic and the mail provider are unchanged. The closed-list rule stays: any other
  destination is a design change with an OD.

### Infrastructure, IAM and deploy

As the plan's §10, with these changes:

- **Synthesizer:** `DefaultStackSynthesizer(generate_bootstrap_version_rule=False)` with
  code-location parameters, plus a CI test that the template has no `cdk-hnb659fds` or asset
  references. (`BootstraplessSynthesizer` "does not support assets" and still needs bootstrapping;
  the plan's option is dropped.)
- **cdk-nag 3** (Apache-2.0, 3.0.2): suppressions are `Validations.of(...).acknowledge({id,
  reason})`, every one with a reason; the version is pinned.
- **Two-phase deploy, unchanged:** base stack with the install bucket; upload the CI-built arm64
  zip verified against the SHA-256 embedded in the client wheel; app stack. The zip must stay
  under 250 MB unzipped (Lambda limit). The template and zip ship as GitHub Release assets
  (OD-458).
- **Lambda code signing:** `AWS::Lambda::CodeSigningConfig` with `Enforce` (supported in
  CloudFormation; no charge). Who signs (the release workflow or the operator's account, since
  `AllowedPublishers` names a signing profile) is an open question.
- **IAM:** the plan's role table, boundary, boundary self-protection, deployer policy and
  cfn-exec conditions stand, plus: `ecf-api` gets the people writes; the operator's Bedrock
  profile is outside the stacks (a user permission set, documented in the admin guide, allowing
  only `bedrock:InvokeModel*` on the pinned models and the retention read). CI reaches AWS only
  through GitHub OIDC to a role allowed the validator's calls (`iam:GetPolicy`,
  `GetPolicyVersion`, the Access Analyzer validate and check calls, `s3:ListAllMyBuckets`,
  `cloudformation:ListExports`, `ssm:GetParameter`).
- **SNS:** the internal and `-direct` topics stay unencrypted and metadata-only (CloudWatch and
  Budgets need a customer-managed key to publish to an encrypted topic); the email topic uses
  `aws/sns`. Alert subjects are cut to 99 characters (the limit is "less than 100").
  `AuthenticateOnUnsubscribe` applies only to signed confirmations, so it doesn't protect the
  email-link path; `doctor` lists subscribers instead.
- **Alarms:** names ASCII-only (`aws:SourceArn` conditions need it).
- **Upgrade:** change-set guard (stops on `Replacement` `True` or `Conditional`, `Remove` or
  `Dynamic` on stateful resources); server first, then clients (N and N-1).
- **Teardown, tags, Budget ($20 default, free), CloudTrail validation:** as the plan's §10.

### Moving from local mode to AWS (`ecf migrate`)

As the plan's §10a steps 1-4, 6 and 7, with:

- **Step 3 (cut over)** also moves people, roles and identity links (OD-596), and switches Slack
  (above). Mailbox passwords move from the Keychain to Secrets Manager through `ecf-api`
  (write-only); nothing to retype.
- **Step 5 (grow to multi-user)** is M2's, not M1's.
- **Runbook** "Moving from local mode to AWS" (checklist, each prompt, a sequence diagram) is
  written in the admin guide in Step 12; the plan put it in this file.
- AWS to local stays single-person only, by export and import.

### Operations

`ecf init --mode aws [--migrating]`, `login`, `logout`, `revoke-device`, `upgrade` (and
`--stack dev --build <ci-run>`), `destroy`, `doctor`, `budget set`, `retention set`,
`billing activate-tags`, `iam deployer-policy`, `pause|resume --restore-stage`, `migrate
plan|run|rollback|finalize`, as the plan's §9 and §13. `doctor` adds, in AWS mode: Cognito
session and refresh expiry, Lambda concurrency, DLQ depth, SNS confirmation and subscribers,
CloudTrail validation, stack drift, the Lambda Python version (OD-598), the Bedrock retention mode
and model access (OD-599), and the Bedrock profile having no static keys (OD-595). Alert routing
defaults to both Slack and email.

### Testing (OD-597)

| What | Local (free) | Real AWS dev stack |
|---|---|---|
| Handler logic, grants, transitions, export format | moto + pytest (CI) | — |
| Packaging, imports, cold start, memory on 64 MB messages, Mangum | SAM `local invoke` in the Lambda arm64 image on Colima | once, to confirm |
| HTTP routes | SAM `local start-api` (no JWT authorizer emulation) | authorizer |
| DynamoDB (conditional writes, indexes) | DynamoDB Local (no PITR; TTL not documented) | PITR, TTL |
| S3, Secrets Manager, SSM, KMS, SNS | moto | bucket policies, lifecycle |
| SQS FIFO order, dedupe, partial batch | moto | retry timing |
| Scheduler tick | handler called directly | firing |
| CDK | synth, cdk-nag, snapshot tests (CI) | — |
| IAM | policy tests (CI); `cfn-policy-validator` (OIDC) | denies actually denying; boundary teardown |
| Cognito | moto for token shape and TOTP | everything else |
| Slack | signed fake payloads to local routes | install, switch, real clicks |
| Bedrock | stubbed client | real calls |
| Alarms, Budget, CloudTrail | — | all |

Colima's Docker socket is set as `DOCKER_HOST` for SAM (SAM supports non-Desktop Docker
runtimes from 1.47.0). The dev stack runs in a separate account or install ID, a test Slack
workspace and a test mailbox with synthetic mail only, and is destroyed after each run with the
Budget alarm on.

### Removed from the plan's M1

- Ollama, Gemma, presets A and B, local-model checks, the Ollama digest (ADR 0031).
- The remote-MCP client, `/mcp` route and login domain (M3).
- `people add`, multi-approver rules, offboarding (M2).
- PyPI publishing (OD-458).
- `BootstraplessSynthesizer` (fact check 2026-11-04).

## Steps

Each step is built on branch `m1-aws-mode` (or a sub-branch), with targeted tests, ruff and pyright,
and reviewed per `docs/agents/reviews-and-evals.md`. Steps marked **(go-ahead)** use real services.

1. **People and identity, local mode first** (OD-579, OD-596): people module, `people.id` columns
   and backfill migration, identities, roles per address, approvers list on grants, role checks in
   handlers, export and import (decide the `data_format` question first). Ships nothing yet.
2. **Claude provider and prompt layout** (OD-595): `claude.provider` setting with step-up,
   `ecf claude --provider`, settings-file generation, cache columns on `claude_calls`, `ecf stats`
   split, prompt order check and isolation test.
3. **AWS adapters** behind the existing ports: state (DynamoDB, with the SQLite-to-DynamoDB
   mapping table reviewed), blob (S3), queues (SQS), secrets (Secrets Manager), stepper (Cognito),
   chat surface over a request URL; contract tests against moto.
4. **Lambda handlers and build:** Mangum, Powertools, the five handlers, the deterministic arm64
   zip, the deploy-time Python check; the local container harness (SAM on Colima, DynamoDB
   Local).
5. **`infra/`:** the two stacks, IAM and boundary, synthesizer, cdk-nag 3, snapshot and policy
   tests, `cfn-policy-validator` through GitHub OIDC (sets up the OIDC role: go-ahead), code
   signing.
6. **CLI for AWS mode:** `init --mode aws`, `login`/`logout`/`revoke-device`, `upgrade` with the
   change-set guard, `destroy`, `doctor` additions, `budget`, `retention`, `billing`,
   `iam deployer-policy`.
7. **Dev stack, Cognito (go-ahead):** deploy; login, refresh through the token endpoint, step-up
   with SRP and TOTP, fresh `auth_time`, refresh token when the flow is omitted, reused TOTP code,
   logout clearing the cookie, more than one user (OD-579); teardown.
8. **Dev stack, infrastructure (go-ahead):** IAM denies, boundary teardown, Lambda async retries
   and the on-failure queue, FIFO retry timing, scheduler firing and heartbeat, a real alarm
   through SNS, budget, CloudTrail validation, drift, cold start and broker memory on a 64 MB
   message, the managed runtime's Python version; cost of the run; teardown.
9. **Slack request URL (go-ahead):** `https://localhost` install; Socket Mode to request URL on
   the same app; reinstall need; app-level token; interactivity URL verification; real clicks;
   `response_url` and `chat.update`; teardown of test resources.
10. **Bedrock (go-ahead):** retention `none` and each pinned model's `allowed_modes`; model access;
    a `/ecf-review` session through Bedrock with subagents and the spawn-guard hook; status line
    and telemetry; prompt-cache figures (global vs regional); prices.
11. **`ecf migrate` (go-ahead):** plan, run, rollback and finalize on the test install into the dev
    stack; cursors carried; open approvals re-posted.
12. **Docs and release:** SPEC (§1.2, §4, §8, §9, §11, §12.4, §13, §14, §15, §17 as needed), the
    admin guide (AWS install, migration runbook, break-glass), README, CHANGELOG; the macOS merge
    gate; the release on the checked path (OD-600, `ecf-release` skill).

## Plan review findings

None yet. Phase R (eight read-only agents: safety; measurement; cost and design; performance; AWS
IAM and infrastructure security; identity (Cognito and the M2 foundations); migration and data
format; Slack request URL and M11 readiness) runs on this draft before Step 1.

## Open questions

1. Does adding `people.id` need a `data_format` bump (OD-579)? If the reader can fill it for format
   4 bundles (the one person), no.
2. Does switching `claude.provider`, same pinned models, need the go-live gate re-run, or a
   smaller check (a sample of the synthetic set through Bedrock, compared with the one rule)?
3. The plan's "revisit at M1 start": the model watch's page parsing (only the Models API locally;
   the CI canary parses pages), and DMARC/DNS from Lambda (resolver, timeouts, the 10-minute
   cache above).
4. Region: the plan's default `us-east-1` (Fable's regional endpoint is there only, irrelevant
   while Fable is blocked).
5. How `ecf upgrade` and release tags drive stack upgrades when the client and stack versions
   differ (N-1 window).
6. The Python upper bound: `.python-version` says `<3.13`, `pyproject.toml` none, OD-526 warns
   past 3.13. The Lambdas follow `.python-version` (OD-598).
7. Token lifetime: the plan's 15-minute access and ID tokens versus AWS's advice not to go
   under 1 hour with managed login. Keep 15 minutes (the cookie is a separate 1 hour) or move to
   1 hour?
8. Lambda code signing: which signing profile `AllowedPublishers` names, and where it lives,
   given the zip is uploaded into the operator's account from a GitHub Release.
9. SPEC inconsistencies found in the inventory, fixed with draft 2 or Step 12: §1.1's platform
   wording, the §1.6 ADR list, §1.6's roadmap filename (`M1-aws.md`, now this file).

## Facts (fetched 2026-11-04; full table `state-archive/m1/facts.md`)

Starred facts were re-read by Claude on the source page; the rest are from research agents and
marked (rv).

- *Cognito refresh-token rotation isn't compatible with `REFRESH_TOKEN_AUTH`; use
  `GetTokensFromRefreshToken` or the token endpoint; grace period up to 60 s; `GlobalSignOut`
  doesn't clear the managed-login cookie
  (docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-the-refresh-token.html).
- (rv) Cognito Essentials: 10,000 MAU free, $0.015 per MAU after (aws.amazon.com/cognito/pricing);
  default for new pools; pre-token V2 on Essentials; TOTP ±30 s, SHA-1 only; email 50 a day;
  access and ID tokens 5 minutes to 1 day; refresh 60 minutes to 3,650 days; `ClientMetadata`
  reaches the trigger from `RespondToAuthChallenge` and `GetTokensFromRefreshToken`, not
  `InitiateAuth`; `prompt=login` on managed login; `AuthSessionValidity` 3-15 minutes
  (Cognito developer guide pages, API reference `CreateUserPoolClient`).
- (rv) API Gateway HTTP API: 30 s integration timeout (not increasable), 10 MB payload; JWT
  authorizer checks `aud` else `client_id`, any one route scope, caches keys up to 2 h
  (apigateway developer guide `http-api-quotas.html`, `http-api-jwt-authorizer.html`).
- *Bedrock data retention: modes `none` < `default` < `aws_review`; Fable 5 and 5.1 require
  `aws_review` (kept up to 30 days, may be reviewed by AWS, not shared with Anthropic); set per
  Region by API, no console; Opus 4.8 allows `none`; check a model's `allowed_modes` with
  `GET /v1/models/{model}` (docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html).
- (rv) Bedrock invocation logging is off by default; providers can't access prompts
  (bedrock user guide `model-invocation-logging.html`, `data-protection.html`).
- (rv) Bedrock prompt caching: up to 4 checkpoints, 512-token minimum on the 5.5 models, TTL 5
  minutes or 1 hour, cross-Region requests write new entries (bedrock user guide
  `prompt-caching.html`). Regional endpoints cost 10% more than global
  (platform.claude.com, Claude in Amazon Bedrock). Per-token Bedrock prices: not documented on
  2026-11-04.
- (rv) Claude Code on Bedrock: `CLAUDE_CODE_USE_BEDROCK` and `AWS_PROFILE` can live in the
  settings `env` block; `awsAuthRefresh`; WebSearch unavailable; prompt caching on by default;
  without `ANTHROPIC_DEFAULT_SONNET_MODEL` the `sonnet` alias is Sonnet 4.5
  (code.claude.com/docs/en/amazon-bedrock; `prompt-caching`; `monitoring-usage`; `statusline`).
- (rv) Anthropic prompt caching: up to 4 breakpoints, 512-token minimum on the 5.5 models, write
  1.25× (5 min) or 2× (1 h), read 0.1× (0.05× Opus and Sonnet 5.5)
  (platform.claude.com/docs/en/build-with-claude/prompt-caching).
- *Anthropic API: `temperature`, `top_p`, `top_k` removed in API version 2026-10-21 (ecf sets
  none); Haiku 4.5 active, not sooner than 2026-10-15; Haiku 5.5 to 2027-10-07
  (platform.claude.com/docs/en/about-claude/model-deprecations).
- (rv) Lambda: `python3.12` deprecated 2028-10-31, `python3.13` and `python3.14` 2029-06-30, all
  on arm64; patch versions not published; AWS's base images measured 3.12.16, 3.13.18, 3.14.10;
  async 2 retries, up to 6 hours; FIFO not allowed as an on-failure destination; reserved
  concurrency leaves 100 unreserved; zip 250 MB unzipped (lambda developer guide
  `lambda-runtimes.html`, `invocation-async-*.html`, `gettingstarted-limits.html`).
- (rv) SQS: FIFO queues don't support per-message timers; visibility at least 6× the function
  timeout; `maxReceiveCount` at least 5; FIFO batch 10 (sqs developer guide `sqs-message-timers.html`;
  lambda `services-sqs-configure.html`).
- (rv) EventBridge Scheduler: 60 s precision, no FIFO DLQ, 14 million invocations a month free
  (scheduler user guide; aws.amazon.com/eventbridge/pricing).
- (rv) S3 us-east-1: $0.023 per GB-month, PUT $0.005 and GET $0.0004 per 1,000; bucket policies
  can enforce conditional writes (s3 pricing JSON; `conditional-writes-enforce.html`).
- (rv) DynamoDB PITR $0.20 per GB-month; TTL deletes within days; DynamoDB Local has no PITR.
- (rv) SNS Subject "less than 100 characters"; `AuthenticateOnUnsubscribe` is on
  `ConfirmSubscription`; CloudWatch and Budgets need a customer-managed key for encrypted topics.
- (rv) CloudTrail: extra management-event copies $2.00 per 100,000; data events $0.10 per 100,000.
  CloudWatch alarm $0.10 per metric a month. Budgets without actions free.
- (rv) `BootstraplessSynthesizer` "does not support assets" (CDK API reference). cdk-nag 3.0.2
  (Apache-2.0) replaced `NagSuppressions`. `cfn-policy-validator` MIT-0, custom checks $0.002 a
  call. Mangum 0.22.0 active (MIT). Powertools MIT. Colima MIT. SAM supports non-Desktop Docker
  runtimes from 1.47.0.
- *LocalStack's open-source repository was archived 2026-03-23; the free plan is for
  non-commercial use (github.com/localstack/localstack).
- (rv) Slack: redirect URLs must use HTTPS; PKCE desktop redirects can't request bot scopes;
  signing secret with a 5-minute window; 3 s acknowledgement; `response_url` 5 uses in 30
  minutes; 1 post per second per channel; Socket Mode and request URL exclusive at any moment;
  `apps.manifest.update` replaces the whole manifest; internal apps unaffected by the 2025
  rate-limit changes (docs.slack.dev).
