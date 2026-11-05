# M1 AWS mode, single user

**Status: draft 2, 2026-11-04. Not built.** M1 started 2026-11-04 (OD-592 put it next; OD-593).
Draft 1 (df4d331) went through Phase R (eight Fable reviewers); its 32 findings and the operator's
decisions (OD-601 to OD-609) are folded in here and listed under "Plan review findings". Operator
decisions made while planning: OD-593 to OD-600 (planning record `planning-docs/M1-PLANNING.md`).
Code references are as of `93e0fea`. Ships as one release at the end (OD-600).

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

- **Storage behind repositories, local mode first** (OD-601): the service's SQL moves behind
  per-aggregate repositories with SQLite as the first adapter, before any AWS code. This is M1's
  largest step.
- **Infrastructure:** CDK `infra/` (Python), two stacks per install, IAM under a permissions
  boundary, CI policy checks (plan §10, OD-124).
- **Services:** Lambdas `ecf-api`, `ecf-jobs`, `ecf-broker`, `ecf-slack`, `ecf-liveness`, the
  Cognito trigger and the auto-delete helper; DynamoDB (PITR); S3; SQS FIFO; Secrets Manager; SSM;
  EventBridge Scheduler; CloudWatch, SNS and Budgets (plan §2, §10).
- **Identity:** Cognito Essentials with TOTP, CLI and step-up app clients (plan §8).
- **Server-side pre-check** on the 1-minute tick (plan §3a), with facts and triggers computed in
  the broker (P1).
- **Slack over a public request URL** (plan §9), manually installed (OD-609).
- **Operations:** `ecf init --mode aws`, `upgrade`, `destroy`, `doctor`, `budget set`,
  `retention set`, `billing activate-tags`, `iam deployer-policy`, dev stack, and `ecf migrate`
  local to AWS, restore-shaped (OD-602).
- **Bedrock:** `/ecf-review` can run Claude through Amazon Bedrock instead of the operator's Claude
  plan, still on demand (OD-578, OD-595), from `ecf claude` on the laptop. No Lambda calls Bedrock
  in M1; M4's scheduled review adds a server-side path and a boundary change (OD-578's dependency
  is the provider plumbing, the prompt layout and the Bedrock checks built here, not a server
  client).
- **M2 foundations, run with one person** (OD-579): the people model, `people.id` on every grant,
  approval, answer, audit row and security notice, identities with a confirmation state, role
  checks per address, approval mode and approver count fields, approvers as a list, people and
  roles in export and import (data format 5, OD-608).
- **The chat seam for M11, both directions** (P18): an inbound port beside `ChatSurface`, Slack its
  only adapter.

Not in scope: multi-user features (M2), remote MCP for claude.ai (M3), scheduled Claude review
and the always-on host (M4), Linux as a local-mode platform (M5), Microsoft Teams (M11), Lambda
code signing (OD-606), Slack OAuth install and token rotation (OD-609).

## Decisions

### Carried from the plan (SPEC OD rows)

- OD-016: `aws` mode is DynamoDB with PITR plus S3, one base and one app stack per install, a
  dedicated AWS account recommended, default encryption.
- OD-021: fetches are asynchronous. OD-022: the scheduler tick is every minute.
- OD-080, OD-081: answer proposals stay off per connection (M3); refresh-expiry warnings on day
  25, then daily.
- OD-090: `ecf init` turns on CloudTrail log file validation, or creates a trail. OD-091: budget
  default $20 (infrastructure; Bedrock has its own Budget, OD-604). OD-092: grants HMAC-signed
  with a key under default AWS-managed encryption.
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
- **OD-557:** `slack_out` retry waits 30 s, 1, 2 and 5 minutes (in AWS mode at least 60 s, see
  Queues).
- **OD-217, OD-507:** the audit log, `item.classified` rows and items that fired a fraud, weak
  fraud or regulator trigger or were quarantined are never pruned. Draft 1's audit TTL and `logs/`
  expiry broke this; fixed (P8).
- **OD-318:** a restored install bumps its generation and keeps restored keywords 7 days; `ecf
  migrate` follows it (OD-602).
- **OD-358:** import drops Slack routes and identity (reconciled by OD-596).

### Operator decisions 2026-11-04, planning (SPEC OD-593 to OD-600)

- **OD-593, M1 started:** this document is written; the AGENTS.md rule "don't load AWS skills
  until M1 starts" is lifted, so the global AWS guidance applies.
- **OD-594, privacy destinations:** the operator's own AWS account and Amazon Bedrock become
  destinations for email content, under the v1 rules: whole messages are held in Lambda memory
  only while processed, never written to S3, SQS, DynamoDB, Lambda `/tmp`, logs or any cache ecf
  operates; only excerpts (up to the existing limits) are stored.
- **OD-595, Bedrock and choosing the provider:** in AWS mode, `/ecf-review` can run through
  Bedrock, billed to AWS, on demand only. Install setting `claude.provider` = `plan` | `bedrock`,
  default `plan`; changing it needs step-up and a Security Notice. `ecf claude --provider bedrock`
  overrides it for one session (with step-up, OD-603). The provider shows in the status line, on
  every `claude_calls` and `claude_sessions` row and in `ecf stats`.
- **OD-596, identities on import:** people and roles are always exported. Identity links
  (Cognito user, Slack member) carry over on `ecf migrate` (same install, OD-602); they are
  dropped on import into a different install, as OD-358 does today.
- **OD-597, testing without cost:** moto unit tests; a local container layer on Colima (the AWS
  Lambda Python arm64 image through SAM, DynamoDB Local); a real AWS dev stack only for what can't
  be faked, torn down after each run. No LocalStack; no tunnel services.
- **OD-598, platform and runtime:** M1's platform is the AWS Lambda runtime, not Linux. Managed
  `python3.12` (arm64), zip; a deploy-time check asserts Python 3.12.6 or later.
- **OD-599, Bedrock data retention `none`:** set in the install's Region and checked by `ecf
  doctor`; Fable stays unavailable on Bedrock; if a pinned model refuses `none`, the build stops
  and asks (checked first, Step 0b).
- **OD-600, one release:** M1 ships as one release at the end, minor (OD-608 settles the data
  format), checked path (OD-492).

### Operator decisions 2026-11-04, after Phase R (SPEC OD-601 to OD-609)

- **OD-601, repositories first (P9):** before any AWS code, the service's SQL (843 `execute`
  calls in 104 of 149 `ecf_server` modules; 42 tables) moves behind per-aggregate repositories
  with SQLite as the first adapter and contract tests that every adapter must pass. The full
  table-to-DynamoDB mapping is written and reviewed in that step. Ships nothing.
- **OD-602, `ecf migrate` is restore-shaped (P11):** the AWS install takes the local install's
  `install.id`, bumps `install.generation`, keeps the install name (keywords match by name) and
  OD-318's 7-day restored-keyword rule, and carries routes and identity links. The local person
  and the Cognito user become one person, proven by a local step-up and a Cognito step-up both
  bound to the bundle's SHA-256. A fixed cutover order; a defined rollback to local.
- **OD-603, provider and the gate (P12):** the go-live gate key and `summary.claude` include the
  provider; a `live` address refuses a session whose provider has no synthetic pass under its key.
  The first Bedrock pass is a full C `/ecf-eval` through Bedrock (about US$18), compared with the
  latest plan-provider run under the one rule, 0 unsafe and fraud guard N/N. `ecf claude
  --provider bedrock` needs step-up and is audited. A parity step proves AWS mode decides as local
  mode does.
- **OD-604, Bedrock cost and region (P14, P24):** the doc, `ecf init --mode aws` and `ecf claude
  --provider bedrock` show the projected monthly cost (about US$380-750 at 300-500 emails a day on
  the current Opus 5.5 pins); a separate Bedrock Budget; one tagged application inference profile
  per pinned model, used as the model ID; geographic `us.` profiles only (no `global.`, which can
  route outside US Regions); ecf refuses to run Bedrock while model invocation logging is
  configured in the Region.
- **OD-605, short-lived AWS credentials (P16):** `init`, `upgrade`, `destroy`, `budget set`,
  `retention set`, `migrate` and the Bedrock provider refuse an IAM user or root caller and
  require an assumed role (IAM Identity Center, `aws login`, or an assumed role). `doctor` checks
  every profile ecf uses.
- **OD-606, no Lambda code signing in M1 (P17):** the zip's trust root is the GitHub Release, as
  for the wheel; the zip is checked against the SHA-256 embedded in the client wheel. Code
  signing is later hardening.
- **OD-607, actions run inside the mailbox check (P19):** as in v1, approved actions execute
  inside the broker's check under the address lease, with the open mailbox; there is no
  `actions.fifo`. An approval schedules an immediate check for its address.
- **OD-608, data format 5 (P27):** people, roles and identities are new bundle files and
  `people.id` a new field: `DATA_FORMAT = 5`, the reader accepts 4 and 5 (a format-4 bundle gets
  the one person filled in); the release stays minor (OD-600, OD-480).
- **OD-609, Slack in AWS mode (P5, P29):** the request-URL app is installed by hand ("Install to
  Workspace", token typed into a hidden prompt, as v1); no `https://localhost` OAuth install and no
  token rotation in M1. ecf stores the app's signing secret (pasted from Basic Information), which
  changes SPEC §10.1's "keeps only the app ID".

## Design

### Architecture

```
Laptop (operator)                    AWS (base stack + app stack)                        External
─────────────────                    ────────────────────────────                        ────────
ecf CLI ─────────────JWT───────────► API Gateway HTTP API ─► ecf-api ─┬─► DynamoDB / S3 / SSM
ecf claude (Claude Code, on demand)                         (rules,    ├─► jobs.fifo ─► ecf-jobs (import, export,
  ├ ecf-mcp ─service token─► same API                       policy,    │                restore, backfill, rules test)
  ├ telemetry relay ─JWT──► same API                        grants)    └─► check request ─► ecf-broker ──► IMAP/SMTP
  Claude: Anthropic (plan) or Bedrock us. profile (OD-595, OD-604)        (fetch, facts, triggers, pre-check, due actions)
                                     Slack ──signed──► ecf-slack ─► slack-in.fifo ─► ecf-api (decisions)
                                     slack-out.fifo ─► ecf-slack sender (paced posts, chat.update) ──► Slack
                                     EventBridge Scheduler ─► ecf-liveness ─► transitions.fifo ─► ecf-api
                                                                         └─► ecf-broker (due checks)
                                     CloudWatch alarms, CloudTrail rules ─► SNS ─► admin email
Cognito (Essentials, TOTP): app clients CLI (PKCE loopback) · step-up (SRP only)
```

**Trust between functions (P3).** A Lambda that is invoked learns nothing about who invoked it,
so no function trusts another's payload as identity:

- `ecf-api` validates the `Authorization` bearer itself on every route and never reads
  `requestContext.authorizer` (a test sends a forged-context event).
- Internal hand-offs go through SQS queues whose IAM send permission is the authentication:
  `slack-in.fifo` (sender `ecf-slack` only), `transitions.fifo` (sender `ecf-liveness` only),
  `jobs.fifo` (sender `ecf-api` only). The consumer checks `eventSourceARN`.
- `ecf-api` re-verifies the Slack signature on every forwarded click (the raw body and the two
  Slack headers travel in the message); it trusts no Slack identity from anywhere else. No public
  route takes a member ID.
- `ecf-api` re-derives `due_at` from DynamoDB for every transition `ecf-liveness` asks for.
- The broker executes only a signed `approved` grant; the HMAC signature is the control, the
  event source is not.

The functions' duties (the plan's §2, changed where noted):

- **`ecf-api`:** routes; rules and policy on stored facts; transitions (the only status writer);
  grants (the only writer); config writes; stage, sensitivity, pause, outbound; people, identities
  and sender confirmations; role checks per address (OD-579); consumes `slack-in.fifo` and
  `transitions.fifo`. Routes that can't finish inside 30 s and 10 MB are jobs (below).
- **`ecf-broker`:** the only holder of mailbox credentials. Invoked per address by `ecf-liveness`
  (due checks) or by `ecf-api` (an approval, a manual check). Each invocation takes the address
  lease, fetches, computes **facts, fraud and regulator triggers and the pre-check decision in
  memory** (P1: the whole message never leaves the broker), writes them with the decision,
  escalation and audit rows in one transaction, then executes due approved actions with the same
  open mailbox (OD-607). It joins the safety TCB in SPEC §3.2. Memory at least 2,048 MB (measured
  in Step 8 at 1, 2 and 3 GB against v1's ~950 MB for a 64 MB message, SPEC §5.1); timeout =
  `max_per_check` plus a retry allowance, capped at 900 s, with `max_per_check` capped at 10
  minutes in AWS mode; the check watches the remaining time and defers the rest through
  `deferred_uids` instead of being killed. Asynchronous invocations: `MaximumRetryAttempts` 0
  (the next tick retries), on-failure destination a standard SQS queue alarmed on depth.
- **`ecf-jobs`:** import, export, restore, backfill and `rules test`, which run up to 600-900 s
  in v1. Bundles move by presigned S3 URL; the CLI polls a job record. 15-minute timeout,
  backfill chunked across invocations. Holds no mailbox credentials.
- **`ecf-slack`:** two handlers. The **request-URL handler** is import-light (no Starlette app,
  an import-linter contract): signature, timestamp within 5 minutes and not in the future,
  `api_app_id` and `team_id`, dedupe (one conditional put), `views.open` for form buttons, enqueue
  the raw click to `slack-in.fifo` (`MessageDeduplicationId` = the dedupe key), return 200. It
  never claims the action was taken. The **sender** consumes `slack-out.fifo`. No Events API, no
  `response_url` (every card edit is a `chat.update` through `slack-out.fifo`, keeping v1's single
  writer). No DynamoDB write on the main table.
- **`ecf-liveness`:** the 1-minute tick. Takes a per-minute tick lease (`TICK#<minute>`,
  conditional put) so an overlapping tick exits; reserved concurrency 1; timeout 50 s; each sweep
  time-boxed. Asks for transitions through `transitions.fifo`, invokes the broker for due checks,
  and dispatches heavy jobs (retention, digests, daily summary, model watch, audit flush, alert
  e-mail) as their own asynchronous invocations. No threads outlive a handler (a test asserts it).
- **Laptop:** the CLI, and Claude Code during `/ecf-review`. No direct AWS or mailbox access except
  the operator's short-lived deployer or admin credentials (OD-605) for `init`, `upgrade`, `budget
  set`, `retention set`, `destroy`, `billing activate-tags`, `migrate` and `doctor`'s read-only
  checks; and, for the Bedrock provider, the AWS profile Claude Code uses.
- **The same code:** the Starlette app (`src/ecf_server/api.py:371`) is wrapped with Mangum
  (0.22.0) for `ecf-api`; the local Unix-socket service stays for local mode. Clients accept a
  server at N and N-1. The checks stream (`/v1/checks`, NDJSON) becomes "enqueued; poll `ecf
  status`" in AWS mode.

### Repositories and the DynamoDB mapping (OD-601)

- **Step 0** (local mode, ships nothing) extracts repositories per aggregate: items and
  transitions, grants and settings, people and identities, senders and threads, audit, alerts,
  Claude telemetry, Slack routes and messages, DNS cache, jobs and leases. SQLite is the first
  adapter; contract tests run every repository against each adapter.
- **Mapping table** (written in Step 0, reviewed before Step 3): one row per table (migrations
  0001 to 0043), with store (DynamoDB table, S3 prefix, SSM, Secrets Manager or dropped), keys and
  GSIs, writer role, TTL rule, exported or not, and the atomic group it belongs to. Tables draft 1
  left without a home are listed explicitly: `addresses` (config in S3, runtime state in
  DynamoDB), `alerts`, `alert_outbox`, `check_state`, `claims`, `claim_batches`, `claude_calls`,
  `claude_sessions`, `slack_calls`, `model_calls`, `escalations`, `delays`, `draft_versions`,
  `eval_runs`, `eval_results`, `slack_messages`, `slack_dedupe`, `processing`, `dns_cache`,
  `downloads`, `schema_migrations`, and the install-level settings keys.
- **Access patterns:** every tick sweep and CLI query is listed with its key or GSI (a sparse
  `due_at` GSI, an open-items GSI, audit by time); a test forbids `Scan` in handler paths.
  Unbounded lists on one item (`deferred_uids`, merged escalation threads) are capped below the
  400 KB item limit (unverified, Step 0).
- **Atomicity (P9):** each v1 write transaction becomes one `TransactWriteItems` (both tables in
  one account and Region allowed): every status transition is the item with `ConditionExpression
  status = :expected AND version = :v` plus its audit row; approval = transition + grant + audit;
  the pre-check = decision + escalation + audit; a terminal transition voids grants, deletes
  delays and sets children's expiry; config apply + stage demotion. Counters use `ADD` under the
  same condition. The SQLite authorizer that makes `transition()` the only status writer (OD-181)
  becomes one conditional-update helper and a test that nothing else writes `status`.
- **Settings across stores:** export, import and restore assemble `settings.jsonl` from the
  DynamoDB settings items, SSM and S3 config by a fixed key list, and apply it back the same way;
  the "this computer's state" keys of OD-368 are listed for AWS mode.
- **Round trip:** a fixture database exported by both adapters gives byte-identical
  `tables/*.jsonl` (booleans, decimals and JSON fields canonicalised); local → AWS → local is
  equal for every exported table modulo `origin` and new IDs; the v1 invariant
  `test_every_exported_table_is_copied_or_handled_apart` runs over the DynamoDB adapter too.

### People, identity and roles (OD-579)

- **Model:** `people {id, identities: [...], roles: {address_id: role}, admin, status}`. In M1
  there is one person, the operator, admin, with every address's approver role. Approval
  authority comes from roles, never Slack channel membership.
- **Identities:** `{issuer, subject, kind, status: pending | confirmed, confirmed_at,
  confirmed_by}`. Only `confirmed` identities authorise. The Cognito issuer is stored as
  `cognito:<pool id>` (the `iss` string can change form under a live pool; validation accepts both
  forms). A Slack member is `pending` until its DM Confirm click, which now writes the identity
  record instead of a setting. `chat.Identity` (`chat.py:23-70`) stays the per-address display
  identity.
- **A click is accepted** when its (team, member) is a confirmed identity of a person with the
  approver role on the item's address; the Confirm click arrives at the request URL; a refused
  click is audited with the member ID only.
- **`people.id`** on every grant, approval, answer, audit row and security notice. Local mode gets
  the model too (its person linked to the OS user). Backfill: `person_id` = the operator for audit
  actors that are the OS user or `slack:*`, null for `service` and `migration`; new columns are
  nullable, added without a table rebuild; history before M1 can't be re-attributed later.
  `grants.principal` (`migrations/0001_initial.sql:95`, always `'os_user'`) is the executing
  principal and is replaced by `approvers`.
- **Per address:** approval mode (`single` only in M1), approver count (1), `stepup.totp` (`off`
  in M1: M2's multi-approver TOTP mode; `off` means the single approver's own Cognito step-up).
- **Export format 5 (OD-608):** people, roles and identities are bundle files. Grants and nonces
  are not exported (`export_bundle.py:62-68`), so the change touches audit, items, approvals and
  answers.
- **Import (OD-596):** people and roles always import; identity links survive only `ecf migrate`.
  On import into a different install, the bundle's people arrive `inactive` (roles and admin
  inert, `source_install` kept for audit); re-activation needs admin step-up and a Security Notice;
  the import preview lists them.

### Cognito

As the plan's §8, with the 2026-11-04 fact checks and Phase R:

- **Pool:** Essentials; `AllowAdminCreateUserOnly`; TOTP required; SMS MFA off; device remembering
  off; deletion protection on; prevent user-existence errors; case-insensitive usernames; group
  `admins`; resource server `ecf` with scopes `observe` and `work`; `AuthSessionValidity` set
  explicitly. The pool and its clients are stateful resources under the change-set guard.
- **CLI client:** authorization code with PKCE (S256) on an `http://localhost` loopback redirect.
  `AllowedOAuthScopes` = `ecf/observe ecf/work` only: no `openid`, no
  `aws.cognito.signin.user.admin` (P7: that scope lets a token holder re-enrol TOTP); attribute
  read and write permissions empty. Access and ID tokens 15 minutes (the API Gateway authorizer
  never checks revocation, so lifetime is the revocation bound; answers draft 1's open question
  7); refresh 30 days with rotation, refreshed through the token endpoint. Tokens in the OS secret
  store.
- **Login always fresh (P25):** `ecf login` always sends `prompt=login` and always ends by sending
  the browser to Cognito's Logout endpoint, so the managed-login cookie can't silently authorise
  another process's PKCE flow. `ecf logout` and `revoke-device` revoke the refresh token
  (`RevokeToken`) and clear the cookie the same way.
- **Step-up client (P6):** `ALLOW_USER_SRP_AUTH` only. The server issues a single-use nonce bound
  to (grant or change hash, person); the CLI sends it in `ClientMetadata` on
  `RespondToAuthChallenge`; the pre-token-generation V2 trigger copies it into claim `ecf_nonce`,
  acting only when the client is the step-up client and the source is
  `TokenGeneration_Authentication`, and suppresses `aws.cognito.signin.user.admin` on every client
  and source. The server requires `client_id` = step-up client, `token_use=access`, `ecf_nonce` =
  the nonce being consumed, `auth_time` and `iat` within 5 minutes, `sub` = a confirmed identity of
  the approving person; it consumes the nonce and the token's `jti` with conditional puts (TTL =
  `exp`), so one token serves one nonce. Step-up access token 5 minutes; refresh revoked at once.
  `MFA_SETUP` or `SOFTWARE_TOKEN_SETUP` during step-up is a refusal; the step-up code never calls
  `AssociateSoftwareToken`. The 5-per-10-minutes verify limit becomes a `RATE#` item.
- **Trigger:** no DynamoDB or network access; reserved concurrency and an alarm (its failure
  blocks every sign-in, which is the right way to fail).
- **Runtime rights:** no Lambda role has `cognito-idp` rights beyond `AdminUserGlobalSignOut` and
  `RevokeToken` (`ecf-api`). `AdminCreateUser` runs once from `init` with deployer credentials;
  user creation and groups arrive with M2.
- **Token validation:** PyJWT with a cached JWKS (refetch on an unknown `kid` at most once a
  minute per container); `iss` (both forms), `token_use`, `exp`, `client_id`, scopes.
- **Lost authenticator (break-glass, admin credentials):** `AdminDeleteSoftwareToken`,
  `AdminSetUserPassword` (temporary, forcing a new password), `AdminUserGlobalSignOut`; logged
  and emailed; enrolment only through managed login; `doctor` and an alarm flag a user with no
  TOTP factor.
- **Stated limits (SPEC §12.2):** the password and TOTP are typed into the `ecf` process (SRP keeps
  the password off the wire, but a trojaned CLI sees it; a TOTP code is valid 30 s); v1's
  service-drawn dialog has no AWS equivalent, so the CLI's text is all the person sees; a stolen
  refresh token gives 30 days of non-step-up access until revoked. Runbook "stolen laptop".
- **Cognito tested with more than one user** (OD-579), in the dev stack.
- **Claude Code never holds a Cognito token (P13):** `ecf claude` exchanges the CLI's JWT at
  `ecf-api` for the session's profile and agent tokens, as today; MCP routes validate those
  service-issued tokens; decision and step-up routes accept only the CLI's token (SPEC §9.6,
  §10.4).

### Data and state

- **DynamoDB, three tables** (on-demand, PITR on, deletion protection on): main (written by the
  broker and `ecf-api`); `ecf-api`-only (grants, settings, people, identities, sender
  confirmations, dedupe of forwarded clicks); excerpts (written by the broker, read only by
  `ecf-api`). `ecf-slack`'s dedupe is its own one-attribute table (P3), so the internet-facing
  function writes nothing else.
- **Expiry (P8):** no TTL is written when an item is created. The terminal transition sets
  `expires_at` on an item and its excerpts in the same transaction, and only when the item is
  prunable: never for items that fired a fraud, weak-fraud or regulator trigger or were
  quarantined (`retention.py:44-50`, OD-217). `AUDIT#` rows get a TTL only after a confirmed S3
  flush. Readers and the exporter filter expired items (TTL deletes within days).
- **Grants:** HMAC-SHA256 with a key in Secrets Manager (readable by `ecf-api` and the broker
  only), re-signed at every state change; the signature covers `{grant_id, action_hash,
  content_hash, status, approvers, stepup_nonce_id, expiry}`.
- **S3:** one bucket; Block Public Access; `BucketOwnerEnforced`; versioning; SSE-S3;
  `ExpectedBucketOwner`; TLS-only. `logs/` is create-only (`If-None-Match` required) and has **no
  expiry rule**: it is the tamper-evident audit copy, flushed every tick; the DynamoDB copy is not
  tamper-evident (any role with main-table writes could change it before the flush). The exporter
  rebuilds `audit.jsonl` from DynamoDB and `logs/` with a monotonic ULID. `exports/` and `jobs/`
  have a 1-day lifecycle; `backups/` (scheduled exports) keeps `export_keep` bundles.
- **Retention:** `log_retention_days` (CloudWatch log groups only) in SSM; set by `ecf retention
  set` through a stack update. PITR keeps deleted items up to 35 days and S3 versioning keeps
  deleted bundles (encrypted): SPEC §12.4 says so.
- **Crash safety:** a `processing <stable_id>` marker is written before each message and counted
  across invocations; after 2 crashes the message is quarantined and escalated, as v1 (SPEC §12.1).
- **No temporary files:** v1's export, import and restore stage `.db` files (`export_bundle.py:83`,
  `importer.py:106`, `restore.py:155`); the AWS adapters build and read bundles in memory, and a
  test forbids `tempfile` on the Lambda code path.

### Data migrations and stack upgrades (P10)

v1's upgrade guarantees (a backup before `migrate`, one transaction per migration, automatic
rollback, `upgrade --to <previous>`, SPEC §11.10) are restated for DynamoDB:

- **Expand and contract only:** new attributes are additive; each item carries `schema_version`
  and is upgraded lazily on read; an idempotent backfill job (`ecf-jobs`, with a progress item)
  upgrades the rest. A migration that would rewrite every item (like 0038) needs a planned outage.
- **Before the change set runs:** `ecf upgrade` takes an on-demand DynamoDB backup, kept until the
  upgrade settles, and sets a maintenance flag that `ecf-api`, `ecf-slack` and `ecf-liveness`
  honour (the equivalent of stopping the service).
- **`ecf upgrade --to <previous>`** rolls back code only; data stays forward-compatible. Restoring
  from the backup creates a new table, swapped in by a stack update, with cursors rewound as
  `downgrade._merge` does.
- Server first, then clients (N and N-1). The change-set guard stops on `Replacement` `True` or
  `Conditional`, `Remove` or `Dynamic` on stateful resources.

### Queues, timers and delays

- **SQS FIFO:** `slack-in.fifo` (group = item), `slack-out.fifo` (group = Slack channel ID, since
  pacing is 1 post a second per channel and every address posts to the summary channel),
  `transitions.fifo` (group = item), `jobs.fifo` (group = install). Each has a DLQ alarmed on
  depth; `maxReceiveCount` 5; visibility at least 6 times the consumer's timeout; batch size 1-2
  with `ReportBatchItemFailures`, unprocessed records returned as failures when time runs low;
  `MaximumConcurrency` 2 on each event source mapping so queues can't starve the click handler.
  No `actions.fifo` (OD-607).
- **One wait mechanism per queue:** FIFO has no per-message delay, so every scheduled wait
  (approval TTLs, the 1-hour AWS delays for sensitivity downgrades and security config,
  `delayed → executing`, held actions) is a `due_at` timestamp acted on by the next tick.
  `slack-out.fifo` retries by `ChangeMessageVisibility` only, never by re-enqueueing (which would
  let a `chat.update` overtake its post). OD-557's 30-second first wait becomes at least 60 s in
  AWS mode (SPEC §10.1 notes it).
- **Holds (P19):** a paused address or a tripped send limit holds an approved action without
  counting an attempt: the grant is marked held with a `due_at`; resume or the end of the breaker
  makes it due again. A busy lease is not an attempt.
- **EventBridge Scheduler:** `rate(1 minute)` on `ecf-liveness`; `MaximumRetryAttempts` 0 with an
  SQS DLQ; a heartbeat alarm when `ecf-liveness` runs fewer than once in 5 minutes. The local
  dead-man message (`chat.scheduleMessage` every tick) is kept for the Slack surface and counted in
  the tick budget.
- **Duplicates:** Slack documents no retry for interactivity (the `X-Slack-Retry-Num` headers are
  Events API only), so a slow acknowledgement loses the click and the card stays actionable;
  duplicates come from double clicks and ecf's own retries, and the dedupe key plus each grant's
  conditional update absorb them.

### Slack over a request URL (OD-609)

- **Install:** the request-URL app is installed by hand, as v1: `apps.manifest.create` (or update),
  "Install to Workspace", the bot token typed into a hidden prompt. The signing secret, pasted from
  the app's Basic Information page into a hidden prompt, goes to its own Secrets Manager entry
  (readable by `ecf-slack` and `ecf-api` only). `ecf slack set-tokens` accepts it under step-up
  with a Security Notice; `doctor` checks it is set; the runbook covers regenerating it. No token
  rotation in M1 (it would need the client secret and a refresh job, and can't be turned off once
  on).
- **Signature and replay (P4):** HMAC-SHA256 over `v0:<timestamp>:<raw body>`; timestamp within 5
  minutes and not in the future; the dedupe key is the SHA-256 of `timestamp + raw body`, written
  by conditional put before anything else, TTL 1 day. The dedupe is the only replay defence inside
  the window.
- **Public route (P21):** a route throttle on `POST /slack` (burst 20, rate 10 a second); signature
  checked before any parse or DynamoDB access; a body cap with 413 above it; 401 with no body on
  failure; the signing secret re-read at most once per container per 5 minutes and only for a
  fresh timestamp; access logs with `$context.*` fields only; an alarm on signature failures per
  minute. AWS WAF can't attach to HTTP APIs: SPEC §12.2 says so, and a REST API is the upgrade
  path if it is ever needed.
- **3-second budget (P2):** Step 9 times a cold click and a cold form button; if either nears 3 s,
  `ecf-slack` gets SnapStart or provisioned concurrency 1 (cost recorded).
- **Switch on migration:** the AWS app stack is deployed and answering first; the local Socket Mode
  client is held; `apps.manifest.update` (which replaces the whole manifest and refuses
  interactivity without a request URL) switches the same app. `manifest()` becomes mode-aware (v1
  hardcodes `socket_mode_enabled: True`, `slack_admin.py:85`, and `reauthorize` resends it). The
  app-level token is revoked (on the app page if `auth.revoke` refuses it).
- **Chat seam for M11, both directions (P18):** v1's `ChatSurface` is outbound only; inbound
  handling is Slack-shaped in six modules (`slack_runtime.py:55`). Step 3 adds `ChatInbound`:
  `verify(headers, raw_body) → Verified(dedupe_key, surface_user, payload) | Refused(reason)`,
  `parse(Verified) → Click | SurfaceEvent`, `ack()`, `open_form(Click, Form)` with `Form` a neutral
  description the adapter renders; the handler registry moves to a neutral module; `RouteRef.channel`
  stays opaque; a `FakeChat` with every capability flag off proves cards, approvals and "Needs you"
  still work; routes are `/chat/<surface>` (Slack at `/chat/slack`). What M11's Teams adapter will
  need (recorded, not built): Bot Framework JWT validation and its JWKS, a 10-15 s limit,
  `Action.Execute` returning the replacement card in the response, and stored conversation
  references for proactive posts.

### Bedrock and the Claude provider (OD-595, OD-599, OD-603, OD-604)

- **How Claude Code reaches Bedrock:** `ecf claude` writes, into its dedicated config's
  `settings.json` `env` block, `CLAUDE_CODE_USE_BEDROCK=1`, `AWS_PROFILE`, `AWS_REGION`, and the
  model variables (`ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`,
  `ANTHROPIC_DEFAULT_HAIKU_MODEL`) set to the install's application inference profile ARNs. The
  `sonnet` alias: Step 2 greps the plugin's agents; if none names `sonnet`,
  `ANTHROPIC_DEFAULT_SONNET_MODEL` is set to the Haiku pin's profile so nothing falls back to
  Sonnet 4.5. No keys in environment variables or files: the profile is a short-lived session
  (OD-605), refreshed through `awsAuthRefresh`.
- **Model IDs (OD-604):** the app stack creates one application inference profile per pinned
  model, copied from the geographic `us.` system profile and tagged with the install; Claude Code
  is pointed at those ARNs, so Cost Explorer and the Bedrock Budget see the spend by tag. `global.`
  profiles are not used. `claude_pins` records the plan ID, the `us.` profile and the application
  profile; the model check compares the telemetry's model against the pin for the session's
  provider. The operator's Bedrock permission set is generated from the pins: `bedrock:InvokeModel*`
  on the application profiles and the foundation-model ARN in each Region of the `us.` profile,
  with a `bedrock:InferenceProfileArn` condition, plus `GetInferenceProfile` and the retention
  read; `doctor` fails on `bedrock:*` or `foundation-model/*`.
- **Checked first (Step 0b), read-only:** each pin's `allowed_modes` includes `none`, model access
  in the Region, the Haiku 5.5 Bedrock ID form, and the Region itself. If a pin refuses `none`,
  the build stops and asks (OD-599).
- **Retention `none`:** set with `PutAccountDataRetention` (admin credentials) in the install's
  Region; `doctor` reads it back. Whether `none` holds for requests the `us.` profile serves in
  another US Region, and whether it is compatible with the 1-hour prompt cache, is checked in
  Step 0b and Step 10.
- **Invocation logging (OD-604):** account-wide per Region, and when on it stores full requests.
  `init --mode aws`, `doctor` and `ecf claude --provider bedrock` call
  `GetModelInvocationLoggingConfiguration` and refuse while one exists.
- **Provider in the gate (OD-603):** the gate key and `summary.claude` gain `provider`; `ecf eval
  compare` warns "different provider". A `live` address refuses a Bedrock session until the first
  full C `/ecf-eval` through Bedrock passes (0 unsafe, fraud guard N/N, non-inferior to the latest
  plan run under the one rule, `--margin 5`), recorded in SPEC §21.2, plus the 10-case determinism
  check on the Bedrock arm. `ecf claude --provider bedrock` needs step-up and writes an audit row;
  SPEC §12.4's "only when `claude.provider` is `bedrock`" becomes "only in a session whose provider
  is `bedrock`".
- **Cost (OD-604):** at the current pins (every gate role Opus 5.5) a whole review costs about
  US$0.042-0.050 an item at list prices with Claude Code's real cache hits (SPEC §16.3 figures);
  at 300-500 emails a day that is about US$380-750 a month, at 50 a day US$63-75; a full gate run
  about US$18. Bedrock is chosen for where data is handled and billed (retention `none`, the AWS
  invoice), not to save money against a plan. `ecf init --mode aws` and `ecf claude --provider
  bedrock` print the projection (per-item figure × the last 30 days' volume). A separate Bedrock
  Budget, filtered by the install tag, alarms at a figure set at init.
- **Telemetry (P13):** `ecf claude` runs a loopback OTLP receiver and status-line relay on the
  laptop that forwards batches to `ecf-api` with the CLI's refreshable token (a fixed OTLP header
  would expire mid-session); the model check's per-session windows live in DynamoDB, not memory.
  `api_error` events are kept as `claude_calls` rows (status code, attempt, no text), so failures
  and throttles per provider show in `ecf stats`. The provider comes from the session's setting,
  never the event. Bedrock `cost_usd` is shown as "API-equivalent" only. Step 2's test: a
  20-minute session loses no `api_request` row.
- **What differs on Bedrock:** no WebSearch (unused); no `/logout`; the status line has no plan
  rate limits. Subagents and the spawn-guard hook on Bedrock are tested in Step 10; if either
  fails, Bedrock is blocked, not shipped half-working.
- **Weekly model watch:** also checks each pin's profile is still offered (`GetInferenceProfile`,
  `GetFoundationModel`; the exact fields are named in Step 2), its retirement date, and that
  `allowed_modes` still contains `none`; a change raises the model-watch alert.

### Prompt caching (operator request 2026-11-04)

- **Who controls what:** Claude Code builds the requests and places the cache breakpoints, on both
  providers (on Bedrock explicit breakpoints only, which Claude Code uses). ecf controls only what
  it returns and the order of its agent instructions: stable content first (agent instructions,
  the effective schema `get_message` returns, the rules and signals block), the message last.
- **What the rule means (P26):** OD-594's "no email content in any cache" covers the caches ecf
  operates. The provider's prompt cache is part of the Anthropic or Bedrock destination, scoped to
  the operator's account, and reuses identical prefixes only; in a batched agent (`--batch 5`,
  SPEC §16.3) earlier items are in the prefix by design (SPEC §5.6). Draft 1's "isolation test
  over the built prompts" is dropped: ecf can't observe the breakpoints.
- **Measured instead:** cache-read share per spawn (`cache_read_tokens` over prompt tokens, from the
  existing `claude_calls` columns, `migrations/0022_claude_usage.sql`), with batch size as a
  factor, per provider. `claude_calls` gains `ended_at` (the request's end, not the batch's arrival)
  so request gaps are measurable.
- **TTL:** 5 minutes by default. Before Step 10 the cost model is fixed: uncached × 1, 5-minute
  write × 1.25 or 1-hour write × 2, read × 0.1 (0.05 on Opus 5.5), over the request gaps of one
  full `/ecf-eval`. `ENABLE_PROMPT_CACHING_1H` is set only if that model says it is cheaper.

### Caches in the AWS design (operator request 2026-11-04)

Rule: no email content in any cache ecf operates (OD-594).

| Cache | Lives in | Lifetime | If stale |
|---|---|---|---|
| Cognito JWKS (handlers) | Lambda memory | container life; refetched on unknown `kid`, at most once a minute | a rotated key fails closed until refetch (API Gateway caches up to 2 h) |
| DNS for DKIM/DMARC (SPEC §7.3, 10-minute cap) | DynamoDB items with `expires_at`; one `BatchGetItem` per page plus an in-invocation memo passed to the isolate child | TTL respected, capped at 10 min | a changed key or policy is seen up to 10 min late, as in v1 |
| Slack DM channel IDs (SPEC §10.1) | DynamoDB | until Slack refuses the ID | a refused ID is re-opened with `conversations.open` |
| Effective schema, rules, policy | Lambda memory, keyed by config version | until the config version changes | every handler checks the version in DynamoDB per invocation; a mismatch reloads |
| Gate fingerprint (`stages.SEEN`) | DynamoDB per address | until the inputs change | recomputed once; `passed_at` already guards "Ready for live" |
| Secrets (mailbox, Slack bot token, signing secret, HMAC key) | Lambda memory | container life; re-read on `AUTHENTICATIONFAILED`, or for the signing secret at most once per 5 minutes on a fresh-timestamp failure | a rotated secret fails once, then reloads |
| Bedrock and Anthropic prompt caches | the provider (part of that destination) | 5 min or 1 h | none: identical prefixes only |

### Privacy (OD-594, OD-599, OD-604)

SPEC §12.4 becomes mode-aware. AWS mode adds:

- **Your AWS account** (the install's Region): the broker fetches and holds full messages in Lambda
  memory only while processing, and computes facts and triggers there; DynamoDB stores metadata,
  computed facts, classifications and excerpts (the same fields SQLite stores in local mode); S3
  holds audit logs (no content), config, and encrypted export bundles; SQS carries IDs, and
  `slack-in.fifo` carries the raw Slack click (what Slack already sends: action IDs, the person,
  form answers); Secrets Manager holds the credentials. Nothing is written to Lambda `/tmp`. PITR
  keeps deleted items up to 35 days and S3 versioning keeps deleted bundles.
- **Amazon Bedrock** (only in a session whose provider is `bedrock`): what Anthropic receives during
  `/ecf-review` today, sent through a `us.` profile, so it may be processed in another US Region.
  Retention mode `none`; ecf refuses while invocation logging is configured; AWS states model
  providers can't access Bedrock prompts.
- **Logs (P22):** every handler has a top-level catch that logs through the no-content processor
  and returns a sanitised error; Powertools' logger goes through the same processor and
  `log_event` is forbidden by a test; the processor also drops `response_url`, `trigger_id`,
  `authorization`, `x-slack-signature`, `code`, `refresh_token`, `id_token` and `access_token`;
  exception messages never embed body or header text; a test feeds a fake Slack click and a fake
  app-password body through each handler and searches the captured logs.
- Slack, Anthropic and the mail provider are unchanged. The closed-list rule stays: any other
  destination is a design change with an OD.

### Infrastructure, IAM and deploy

As the plan's §10, with these changes:

- **Synthesizer:** `DefaultStackSynthesizer(generate_bootstrap_version_rule=False)` with
  code-location parameters, `versionReporting` off, `aws-cdk-lib` pinned, a template snapshot,
  and a CI test that the template has no `cdk-hnb659fds` or asset references. Constructs that
  create assets silently are avoided: `Bucket(auto_delete_objects=True)`,
  `Function(log_retention=...)`, `BucketDeployment`, `custom_resources.Provider`,
  `PythonFunction`; log groups are explicit and the auto-delete helper is inline (`ZipFile`, under
  4 KB).
- **cdk-nag 3** (3.0.2, pinned): suppressions are `Validations.of(...).acknowledge({id,
  reason})`, every one with a reason.
- **No VPC:** IMAP, SMTP, Slack and DNS need only Lambda's default egress; a CDK assertion that no
  function has `VpcConfig`.
- **Two-phase deploy:** base stack with the install bucket; upload the CI-built arm64 zip verified
  against the SHA-256 embedded in the client wheel; app stack. Zip under 250 MB unzipped. The
  template and zip ship as GitHub Release assets (OD-458). No code signing (OD-606).
- **Credentials (OD-605):** the commands that use deployer or admin credentials refuse a caller
  whose `GetCallerIdentity` ARN is `:user/` or `:root`; `ecf iam deployer-policy` prints an IAM
  Identity Center permission set (an IAM-user form only behind a flag, with a warning).
- **Boundary (P20):** the permissions boundary carries an explicit data-plane Allow list (DynamoDB
  item operations, S3 object operations, SQS send, receive, delete and visibility, Secrets Manager
  get and put value, SSM get and put parameter, `lambda:InvokeFunction`, `sns:Publish`, the two
  Cognito calls, CloudWatch Logs writes) and an explicit Deny on control-plane actions
  (`lambda:UpdateFunctionCode`, `UpdateFunctionConfiguration`, `AddPermission`,
  `CreateEventSourceMapping`, `CreateFunctionUrlConfig`; `sqs:SetQueueAttributes`;
  `secretsmanager:PutResourcePolicy`; `dynamodb:UpdateContinuousBackups`, `DeleteTable`;
  `logs:DeleteLogGroup`; `cognito-idp:UpdateUserPool*`; `bedrock:PutAccountDataRetention`). A CI
  test asserts every Allow action in a runtime role is in the list. The plan's boundary
  self-protection denies stand.
- **cfn-exec (P20):** lives under `/ecf-deploy/<install>/`, outside the runtime path; denied
  `iam:PassRole` unless `iam:PassedToService` is CloudFormation or the runtime roles' services for
  `role/ecf/<install>/*`; trust policy with `aws:SourceAccount` and `aws:SourceArn` on the app
  stack (condition form confirmed in Step 8); denied `iam:CreateUser`, `CreateAccessKey`,
  `CreateLoginProfile`, `CreatePolicy`, `AttachUserPolicy`, and function URLs with auth `NONE`.
- **Broker secrets:** the broker's `GetSecretValue` on mailbox secrets carries a
  `lambda:SourceFunctionArn` condition, so a leaked broker credential can't read them from outside
  the function. `ecf-api`'s write-only `PutSecretValue` on mailbox secrets can change the IMAP host
  inside the secret; that is accepted because `ecf-api` is already the trust root, and SPEC says
  so.
- **CI (P20):** GitHub OIDC trust requires `aud = sts.amazonaws.com` and `sub =
  repo:<owner>/<repo>:ref:refs/heads/main`; the validator job runs only on pushes to `main`;
  `ssm:GetParameter` is scoped to `/ecf/*`; the account is a dedicated empty one, never an
  install's.
- **Detective controls (P23):** EventBridge rules on CloudTrail management events
  (`UpdateFunctionCode*`, `UpdateFunctionConfiguration*`, IAM changes under `/ecf/`, Secrets Manager
  changes under `ecf/<install>/`, `cognito-idp:Admin*`, `UpdateContinuousBackups`, `DeleteTable`,
  `UpdateTable`, `StopLogging`, `DeleteTrail`, `UpdateTrail`, `PutBucketPolicy`,
  `PutBucketLifecycle`) publish to the `-direct` alert topic (cost checked in Step 8).
- **Concurrency:** `ecf init` reads the account's Lambda concurrency quota and requests at least 50
  on a new account; `doctor` warns below that; `ecf-slack`'s request handler gets 2 reserved when
  the quota allows; `ecf-liveness` 1.
- **SNS, alarms, teardown, tags, CloudTrail validation:** as draft 1 and the plan's §10 (topics
  unencrypted and metadata-only except the email topic on `aws/sns`; subjects cut to 99
  characters; ASCII alarm names; `doctor` lists subscribers).
- **Budgets:** the infrastructure Budget ($20 default, OD-091) and the Bedrock Budget (OD-604).

### Cost (one user, us-east-1, fetched 2026-11-04 (rv); restores the plan's §15)

| Item | Idle | 300-500 emails a day, 3 addresses |
|---|---|---|
| Secrets Manager, 4-5 secrets at $0.40 | $1.60-2.00 | same |
| CloudWatch alarms, ~16-20 metrics, 10 free, $0.10 each | $0.60-1.00 | same |
| CloudTrail (first management copy free) and its S3 | $0.10-0.20 | same |
| DynamoDB on-demand, PITR, storage under 1 GB | ~$0.10 | $0.20-0.40 |
| Lambda (free tier duration not stated by AWS) | $0-0.50 | $0.50-1.00 if billed |
| CloudWatch Logs, Scheduler, SNS, SSM, S3 audit writes, API Gateway | ~$0.10 | ~$0.15 |
| Cognito Essentials (10,000 MAU free) | $0 | $0 |
| **Infrastructure** | **about $2.5-4** | **about $3.5-6.5** |
| **Bedrock, when chosen** | $0 unused | **about $380-750**; a full gate run about $18 |

Secrets Manager is the largest fixed line (OD-092 chose it). A NAT gateway (avoided: no VPC) would
be the biggest line if one were ever added. Step 8 records the dev-stack run's actual cost.

### Moving from local mode to AWS (`ecf migrate`, OD-602)

As the plan's §10a steps 1, 2, 4, 6 and 7, with step 3 restore-shaped:

- **Same install:** `ecf migrate plan` prints the install ID and the export public key; `ecf init
  --mode aws --migrating` stores both in SSM; `migrate run` requires the bundle to be this
  install's own (signed by its key, claiming its ID). The AWS install takes the local
  `install.id`, `install.generation` + 1 (the `X-ECF-Install` header changes), the install name,
  routes, alerts and identity links, and applies OD-318's 7-day restored-keyword rule. Which of
  OD-363 to OD-372 apply is listed in Step 11's runbook.
- **Same person:** a local step-up and a Cognito step-up, both bound to the bundle's SHA-256; the
  bundle's person is merged into the Cognito person (the local `people.id` kept, the Cognito
  identity added, the Slack identity kept); audited as `migrate.identities_linked` with a Security
  Notice.
- **Moved with it:** mailbox passwords (Keychain → Secrets Manager through `ecf-api`, write-only),
  the Slack bot token and the signing secret (OD-609), the export signing seed and `export.key`
  generations (so AWS exports stay signed and restorable, OD-340, OD-350).
- **Refused when busy:** as `ecf upgrade` (OD-379): nothing executing or undoing, no lease, no
  `ecf claude` session, no `ecf watch`, upgrade settled.
- **Cutover order:** pause → drain `slack_out` and `alert_outbox` → stop the local service (which
  cancels the dead-man's scheduled message) → export (the migrate kind also carries
  `slack_messages` and `draft_versions`) → import into AWS → switch the Slack manifest → edit old
  cards to "moved; see the new card" → re-post open approvals with fresh grants → resume.
- **What is lost, and said in the runbook:** queued jobs other than the drained ones, pending
  step-ups, `delayed` sends (they return to `awaiting_approval`), an open `ecf claude` session,
  login cooldowns. A `sent` row at `pending` with its item `executing` becomes `failed_unknown`
  and the AWS broker's `\Sent` reconciliation settles it.
- **Rollback to local (14-day window):** allowed only "before AWS processes any mail", defined as no
  cursor advanced, no grant consumed and no `sent` row written in AWS; it switches Slack back to
  Socket Mode with a new app-level token and re-enables the unit. After AWS has processed mail,
  going back is `ecf import --replace` into the kept local data, under OD-358's rules (paused,
  capped at assist, routes and identity dropped). The local install must already run the M1
  release (data format 5).
- **Scheduled export:** Lambda can't write to the laptop's `export_dir`, so AWS mode writes
  scheduled exports to `backups/` with `export_keep` lifecycle and the same pruning rule; SPEC
  §11.9 and `doctor` say so.
- **Step 5 (grow to multi-user)** is M2's. The runbook "Moving from local mode to AWS" goes in the
  admin guide (Step 12).

### Operations

`ecf init --mode aws [--migrating]`, `login`, `logout`, `revoke-device`, `upgrade` (and `--stack
dev --build <ci-run>`), `destroy`, `doctor`, `budget set`, `retention set`, `billing
activate-tags`, `iam deployer-policy`, `pause|resume --restore-stage`, `migrate
plan|run|rollback|finalize`, as the plan's §9 and §13. In AWS mode the CLI's request timeout is
30 s (cold starts; local mode keeps 10 s). `doctor` adds: Cognito session and refresh expiry, a
user without TOTP, Lambda concurrency quota, DLQ depth, SNS confirmation and subscribers,
CloudTrail validation, stack drift, the Lambda Python version (OD-598), Bedrock retention mode,
invocation logging, model access and permission-set scope (OD-599, OD-604), the signing secret
(OD-609), and no long-lived keys on any profile ecf uses (OD-605). Alert routing defaults to both
Slack and email.

### Testing (OD-597)

| What | Local (free) | Real AWS dev stack |
|---|---|---|
| Repositories, handler logic, grants, transitions, export format | contract tests on SQLite and moto; pytest (CI) | — |
| Packaging, imports, memory on 64 MB messages, Mangum, the isolate child | SAM `local invoke` in the Lambda arm64 image on Colima (cold-start figures indicative only) | memory at 1, 2, 3 GB; cold start |
| HTTP routes | SAM `local start-api` (no JWT authorizer emulation) | authorizer |
| DynamoDB (conditional writes, transactions, indexes) | DynamoDB Local (no PITR; TTL not documented, so readers' expiry filter has its own unit test) | PITR, TTL |
| S3, Secrets Manager, SSM, SNS | moto | bucket policies, lifecycle |
| SQS FIFO order and dedupe | moto | partial-batch redelivery and group blocking (event source mapping behaviour), retry timing |
| Scheduler tick | handler called directly; tick lease; no threads left | firing, overlap |
| CDK | synth, cdk-nag, snapshot, no-asset and no-VPC tests (CI) | — |
| IAM | policy tests (CI) incl. the boundary Allow list; `cfn-policy-validator` (OIDC, main only) | denies actually denying; boundary teardown |
| Cognito | token-shape checks only (moto's TOTP validation unverified) | everything else |
| Slack | signed fake payloads to local routes; replay and bad-signature cases | install, switch, real clicks, cold timing |
| Bedrock | stubbed client | real calls |
| Alarms, Budgets, CloudTrail rules | — | all |
| Logging | captured-log search for secrets and content (CI) | — |

Colima's Docker socket is set as `DOCKER_HOST` for SAM. The dev stack runs in a separate account or
install ID, a test Slack workspace and a test mailbox with synthetic mail only, and is destroyed
after each run with the Budget alarm on.

### Removed from the plan's M1

- Ollama, Gemma, presets A and B, local-model checks, the Ollama digest (ADR 0031).
- The remote-MCP client, `/mcp` route and login domain (M3).
- `people add`, multi-approver rules, offboarding (M2).
- PyPI publishing (OD-458).
- `BootstraplessSynthesizer` (fact check 2026-11-04).
- Lambda code signing (OD-606); `actions.fifo` (OD-607); the `https://localhost` OAuth install
  (OD-609); `response_url` card updates (P2); `global.` inference profiles (OD-604); the audit TTL
  and `logs/` expiry (P8).

## Steps

Each step is built on branch `m1-aws-mode` (or a sub-branch), with targeted tests, ruff and
pyright, and reviewed per `docs/agents/reviews-and-evals.md`. Steps marked **(go-ahead)** use real
services. Each step ends with its pass line.

0. **Repositories, local mode (OD-601):** extract the repositories; contract tests; the mapping
   table (every table, keys, writer, TTL, exported, atomic group) and the access-pattern list.
   Also the people model (OD-579, OD-596, OD-608): people module, identities with status,
   `people.id` columns and backfill, roles per address, approvers list, data format 5. Ships
   nothing. *Pass:* full suite green with SQLite behind the repositories; mapping table reviewed
   (diff review on Fable); a format-4 bundle imports as format 5.
   **0b. Bedrock pre-checks (go-ahead, read-only):** `allowed_modes` for each pin, model access,
   the Haiku 5.5 ID form, Region. *Pass:* every pin allows `none`, or the build stops (OD-599).
1. **Claude provider, telemetry relay and prompt layout** (OD-595, OD-603): `claude.provider` with
   step-up; `--provider` with step-up and audit; settings-file generation; `provider` on
   `claude_calls` and `claude_sessions`; `ended_at`; `api_error` rows; the laptop OTLP and
   status-line relay; provider in the gate key and `eval compare`; `ecf stats` split and cache-read
   share. *Pass:* a 20-minute local session loses no `api_request` row; a gate key differs by
   provider.
2. **Chat seam inbound** (P18): `ChatInbound`, neutral registry, `FakeChat` with all flags off.
   *Pass:* the suite green on Slack and on the all-off fake.
3. **AWS adapters:** DynamoDB repositories (transactions, conditional helper, no `Scan`), S3, SQS,
   Secrets Manager, Cognito stepper; contract tests against moto. *Pass:* every contract test
   passes on both adapters; the round-trip test gives identical bundles.
4. **Lambda handlers and build:** Mangum for `ecf-api`; the import-light Slack handler; the broker
   with facts and actions in the check; `ecf-jobs`; `ecf-liveness` with the tick lease; Powertools
   through the no-content processor; the deterministic arm64 zip; the deploy-time Python check;
   the local container harness. *Pass:* SAM invokes every handler; no `tempfile`, no thread left,
   no `log_event`; log search clean.
5. **`infra/`:** the two stacks, IAM, boundary Allow list and Denies, cfn-exec path and trust,
   synthesizer, cdk-nag 3, snapshot, policy, no-asset and no-VPC tests, application inference
   profiles, CloudTrail rules, Budgets, `cfn-policy-validator` through GitHub OIDC (sets up the
   OIDC role: go-ahead). *Pass:* CI green with every policy test.
6. **CLI for AWS mode:** `init --mode aws` (cost projection, concurrency quota, credential check),
   `login` always fresh, `logout`, `revoke-device`, `upgrade` with backup, maintenance flag and
   change-set guard, `destroy`, `doctor` additions, `budget`, `retention`, `billing`, `iam
   deployer-policy`. *Pass:* targeted tests; refuses an IAM-user credential.
7. **Dev stack, Cognito (go-ahead):** deploy; login with `prompt=login` and logout redirect; refresh
   through the token endpoint with the refresh flow off; step-up with SRP and TOTP; `ecf_nonce`
   bound, `jti` consumed, a replayed token refused; `auth_time` on refresh; reused TOTP code;
   lockout; `MFA_SETUP` refused in step-up; CLI token can't call `AssociateSoftwareToken`; more than
   one user (OD-579); teardown. *Pass:* every listed refusal observed.
8. **Dev stack, infrastructure (go-ahead):** IAM denies and the boundary's control-plane Denies,
   cfn-exec trust conditions, async retries off and the on-failure queue, partial-batch redelivery
   and FIFO group blocking, scheduler firing, tick overlap, heartbeat, a real alarm and a
   CloudTrail rule through SNS, Budgets, drift, DNS from the Lambda runtime (fallback resolver if
   needed), broker memory and duration on a 64 MB message at 1, 2 and 3 GB, cold-start Init
   Duration per function, the runtime's Python version; teardown. *Pass:* broker peak memory under
   80 % of its setting; no check killed by timeout; run cost recorded and under US$5.
9. **Slack request URL (go-ahead):** manual install with the signing secret; Socket Mode to request
   URL on the same app; `reauthorize` keeps the request URL; real clicks; a cold click and a cold
   form button timed against 3 s; bad-signature and stale-timestamp posts (401, audited, no
   secret-read storm); a replayed captured body; a double click; the dead-man message at cutover;
   teardown (`apps.manifest.delete`, secrets deleted without a recovery window). *Pass:* every
   click acknowledged under 3 s cold, or SnapStart/provisioned concurrency decided; replay and
   double click each produce one decision.
10. **Bedrock (go-ahead):** a `/ecf-review` session through the application profiles with
    subagents and the spawn-guard hook; telemetry and status line; invocation-logging refusal; the
    first full C `/ecf-eval` through Bedrock and the determinism check (OD-603); prompt-cache
    figures under the fixed cost model; prices from the bill. *Pass:* 0 unsafe, fraud guard N/N,
    non-inferior at `--margin 5`; 0 non-throttle `api_error` rows over the run.
11. **Parity and `ecf migrate` (go-ahead):** replay the frozen synthetic set into the dev stack's
    test mailbox and run the `null` arm in both modes; then migrate plan, run, rollback and
    finalize on the test install. *Pass:* 0 differences per case in rule outcomes, triggers,
    computed facts and action plans; item, cursor, grant and gate-row counts equal before and
    after migrate; 0 duplicate Slack posts; old cards edited.
12. **Docs and release:** SPEC (§1.2, §3.2 broker in the TCB, §4, §8, §9, §10.1 signing secret and
    retry granularity, §11.9 scheduled export, §11.10 AWS upgrades, §12.2 AWS-mode stated limits
    (deployer full data access, admin bypasses every rule, WAF unavailable, Cognito limits),
    §12.4, §13, §14 `max_per_check` cap, §15, §17), the admin guide (AWS install, migration
    runbook, break-glass, stolen laptop, cost), README, CHANGELOG; the macOS merge gate; the
    release on the checked path (OD-600, `ecf-release` skill).

## Plan review findings

Phase R on draft 1 (df4d331), 2026-11-04: eight read-only Fable reviewers (safety, measurement,
cost and design, performance, AWS IAM and infrastructure, identity, migration and data format,
Slack and M11). 32 findings (18 high), merged and spot-checked by Claude against `93e0fea`; full
text in `state-archive/m1/review-findings.md`. The operator accepted every recommendation
(2026-11-04). Where each landed:

| # | Finding | Landed |
|---|---|---|
| P1 | facts and triggers computed where the message can't go | broker computes them (Architecture) |
| P2 | Slack "ack then submit" impossible in Lambda | `slack-in.fifo`, import-light handler, no `response_url` |
| P3 | internal calls carry no identity; `ecf-slack` table writes | Trust between functions; dedupe table |
| P4 | no replay dedupe key | Slack, signature and replay |
| P5 | signing secret never stored | OD-609 |
| P6 | step-up token not bound to nonce | Cognito, step-up client |
| P7 | CLI client scope can re-enrol TOTP | Cognito, CLI client |
| P8 | audit and kept items pruned | Data and state, expiry |
| P9 | no state port; mapping and transactions | OD-601, Repositories |
| P10 | no data-migration design | Data migrations and stack upgrades |
| P11 | migrate as import | OD-602, Moving to AWS |
| P12 | eval and gate in AWS mode | OD-603; Steps 10, 11 |
| P13 | telemetry and MCP tokens | Bedrock telemetry; Cognito |
| P14 | Bedrock cost | OD-604, Cost |
| P15 | Lambda limits | broker, `ecf-jobs`, liveness; Data and state |
| P16 | long-lived deploy keys | OD-605 |
| P17 | code signing | OD-606 |
| P18 | chat seam inbound | Slack, chat seam; Step 2 |
| P19 | queues and leases | OD-607; Queues |
| P20 | IAM hardening | Infrastructure |
| P21 | public Slack route | Slack, public route |
| P22 | log redaction | Privacy, logs |
| P23 | detective controls | Infrastructure |
| P24 | Bedrock Region and logging | OD-604 |
| P25 | identity details | Cognito; People |
| P26 | prompt-cache assertion | Prompt caching |
| P27 | data format | OD-608 |
| P28 | settings and round trip | Repositories |
| P29 | Slack install and switch | OD-609; Slack |
| P30 | pass lines and testing table | Steps; Testing |
| P31 | cost section, Bedrock checks first, M4, VPC | Cost; Step 0b; Scope; Infrastructure |
| P32 | DNS, cold start, concurrency | Caches; Operations; Infrastructure |

The next review (draft 2, delta only) runs on Opus (OD-577).

## Open questions

1. The plan's "revisit at M1 start": the model watch's page parsing (only the Models API locally;
   the CI canary parses pages).
2. Region: the plan's default `us-east-1`; decided in Step 0b with model access.
3. How release tags drive stack upgrades when client and stack versions differ (N-1 window):
   detail for Step 6.
4. The Python upper bound: `.python-version` says `<3.13`, `pyproject.toml` none, OD-526 warns
   past 3.13. The Lambdas follow `.python-version` (OD-598).
5. SPEC inconsistencies found in the inventory, fixed in Step 12: §1.1's platform wording, the
   §1.6 ADR list.

Draft 1's questions 1 (data format), 2 (gate on provider switch), 7 (token lifetime) and 8 (code
signing) are answered by OD-608, OD-603, the Cognito section and OD-606; question 3's DNS half
moved to Step 8.

## Facts (fetched 2026-11-04; full table `state-archive/m1/facts.md`)

Starred facts were re-read by Claude on the source page; the rest are from research agents or
Phase R reviewers and marked (rv).

- *Cognito refresh-token rotation isn't compatible with `REFRESH_TOKEN_AUTH`; use
  `GetTokensFromRefreshToken` or the token endpoint; grace period up to 60 s; `GlobalSignOut`
  doesn't clear the managed-login cookie
  (docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-the-refresh-token.html).
- (rv) Cognito: managed-login cookies let a user re-authenticate with no prompt; shortening tokens
  doesn't affect the cookie (same page; `amazon-cognito-user-pools-using-the-access-token.html`).
  `AssociateSoftwareToken` needs `aws.cognito.signin.user.admin` and verifying a new token
  disassociates the old (`API_AssociateSoftwareToken.html`). With no scopes requested, managed
  login grants all the client's scopes (`authorization-endpoint.html`). `auth_time`, `iat`, `jti`,
  `sub`, `client_id` can't be changed by the pre-token trigger
  (`user-pool-lambda-pre-token-generation.html`). After `AdminDeleteSoftwareToken` the next sign-in
  gets `MFA_SETUP` (`user-pool-settings-mfa-totp.html`). A pool can use an original or updated
  issuer (access-token page).
- (rv) Cognito Essentials: 10,000 MAU free; pre-token V2 on Essentials; TOTP ±30 s, SHA-1;
  access and ID tokens 5 minutes to 1 day; refresh 60 minutes to 3,650 days; `ClientMetadata`
  reaches the trigger from `RespondToAuthChallenge`, not `InitiateAuth`; `AuthSessionValidity`
  3-15 minutes.
- (rv) API Gateway HTTP API: 30 s integration timeout, 10 MB payload; JWT authorizer checks `aud`
  else `client_id`, any one route scope, caches keys up to 2 h; throttles are best-effort targets,
  per stage or route; AWS WAF protects REST APIs, not HTTP APIs.
- *Bedrock data retention: modes `none` < `default` < `aws_review`; Fable 5 and 5.1 require
  `aws_review`; set per Region by API; Opus 4.8 allows `none`; check `allowed_modes` with
  `GET /v1/models/{model}` (docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html).
- (rv) Bedrock invocation logging is off by default, account-wide per Region, and when on stores
  full request and response data (`model-invocation-logging.html`). `global.` profiles route to
  any supported commercial Region (`cross-region-inference.html`). An inference profile in an IAM
  `Resource` needs the foundation model in each of its Regions (`inference-profiles-prereq.html`).
  Application inference profiles take tags "to track usage and costs"
  (`inference-profiles-create.html`).
- (rv) Bedrock on-demand quotas far above a review batch (Opus 5.5: 15,000,000 tokens a minute;
  general/latest/gr/bedrock.html; global rows not seen).
- (rv) Prices (platform.claude.com/docs/en/about-claude/pricing): Opus 5.5 $4 in, $5 5-minute
  write, $8 1-hour write, $0.20 read, $20 out per million tokens; Haiku 5.5 $0.10, $0.125, $0.20,
  $0.01, $0.50; regional and multi-region endpoints 10 % over global. AWS's Bedrock price table
  didn't render: Step 10 records the bill.
- (rv) Bedrock prompt caching: up to 4 checkpoints, 512-token minimum on the 5.5 models, TTL 5
  minutes or 1 hour, explicit breakpoints only; cross-Region requests write new entries.
- (rv) Claude Code on Bedrock: settings `env` block; `awsAuthRefresh`; WebSearch unavailable;
  caching on by default; `ENABLE_PROMPT_CACHING_1H`, `DISABLE_PROMPT_CACHING`; an inference-profile
  ARN as model needs `bedrock:GetInferenceProfile` and "doesn't act as a pin" for the startup check
  (code.claude.com/docs/en/amazon-bedrock). OTLP `api_request` is a successful request, `api_error`
  a failed one; `cost_usd` is an estimate; `client_request_id` absent on third-party backends
  (code.claude.com/docs/en/monitoring-usage).
- *Anthropic API: `temperature`, `top_p`, `top_k` removed in API version 2026-10-21 (ecf sets
  none); Haiku 5.5 to 2027-10-07 (platform.claude.com/docs/en/about-claude/model-deprecations).
- (rv) Lambda: `python3.12` deprecated 2028-10-31; AWS's base images measured 3.12.16; async 2
  retries, up to 6 hours; FIFO not allowed as an on-failure destination; synchronous payload 6 MB,
  asynchronous 1 MB; maximum timeout 900 s; 1,769 MB = one vCPU; the environment freezes when the
  handler returns; new accounts have reduced concurrency quotas; SnapStart supports Python 3.12+,
  not with provisioned concurrency; `lambda:SourceFunctionArn` usable in identity policies only;
  zip 250 MB unzipped (lambda developer guide: `gettingstarted-limits.html`,
  `lambda-runtime-environment.html`, `configuration-memory.html`, `snapstart.html`).
- (rv) SQS: FIFO queues don't support per-message timers; visibility at least 6× the function
  timeout; `maxReceiveCount` at least 5; on a function error without a partial-batch response the
  whole batch returns after the visibility timeout (`services-sqs-errorhandling.html`).
- (rv) EventBridge Scheduler: 60 s precision, no FIFO DLQ, 14 million invocations a month free;
  retry policy up to 185 attempts within 86,400 s (`API_RetryPolicy.html`; default unverified).
- (rv) S3 us-east-1: $0.023 per GB-month, PUT $0.005 per 1,000; bucket policies can enforce
  conditional writes.
- (rv) DynamoDB PITR $0.20 per GB-month; on-demand $0.125 per million reads, $0.625 per million
  writes; TTL deletes within days; DynamoDB Local has no PITR. `TransactWriteItems` 100 items
  across tables and the 400 KB item limit: unverified, Step 0.
- (rv) Secrets Manager $0.40 per secret a month. CloudWatch alarm $0.10 per metric, each metric in
  a metric-math alarm billed, 10 free. CloudTrail extra management copies $2.00 per 100,000.
  Budgets without actions free.
- (rv) `DefaultStackSynthesizer`: `generateBootstrapVersionRule` "generally should be left set to
  true, unless you explicitly want to be able to deploy to an unbootstrapped environment".
  `BootstraplessSynthesizer` "does not support assets". cdk-nag 3.0.2 (Apache-2.0).
  `cfn-policy-validator` MIT-0. Mangum 0.22.0 (MIT). Powertools MIT. Colima MIT. SAM supports
  non-Desktop Docker runtimes from 1.47.0.
- *LocalStack's open-source repository was archived 2026-03-23; the free plan is for
  non-commercial use (github.com/localstack/localstack).
- (rv) GitHub OIDC `sub` forms: `repo:ORG/REPO:ref:refs/heads/BRANCH`, `:environment:NAME`,
  `:pull_request` (GitHub OIDC reference).
- (rv) Slack: signature `v0:` + timestamp + raw body, HMAC-SHA256, the 5-minute check is Slack's
  recommendation (docs.slack.dev/authentication/verifying-requests-from-slack); acknowledge within
  3 s, `trigger_id` expires in 3 s and is single-use, `response_url` 5 uses in 30 minutes
  (docs.slack.dev/interactivity/handling-user-interaction); retry headers are Events API only
  (docs.slack.dev/apis/events-api); `apps.manifest.update` replaces the whole manifest and refuses
  interactivity without a request URL (docs.slack.dev/reference/methods/apps.manifest.update);
  redirect URLs must use HTTPS, nothing documented about localhost
  (docs.slack.dev/authentication/installing-with-oauth); token rotation can't be turned off once on
  (docs.slack.dev/authentication/using-token-rotation). Unverified: Socket Mode and request URL
  exclusive at any moment (the Socket Mode page returned 404); 1 post a second per channel.
- (rv) Microsoft Teams (for M11, recorded only): Bot Framework bearer JWT from
  `api.botframework.com`, keys refreshed daily, 5-minute skew; 10-15 s timeout; `Action.Execute`
  returns the replacement card; proactive posts need stored conversation references
  (learn.microsoft.com Bot Framework and Teams platform pages).
