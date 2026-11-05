# M1 AWS mode, single user

**Status: draft 3, 2026-11-04. Not built.** M1 started 2026-11-04 (OD-592 put it next; OD-593).
Phase R round 1 (eight Fable reviewers) ran on draft 1 (df4d331); round 2 (eight Opus reviewers,
delta, OD-577) ran on draft 2 (272df2a). Both rounds' findings and the operator's decisions
(OD-601 to OD-614) are folded in here and listed under "Plan review findings". Planning decisions:
OD-593 to OD-600 (`planning-docs/M1-PLANNING.md`). Code references are as of `93e0fea`. Ships as
one release at the end (OD-600).

This document replaces, for M1, the design plan's M1 parts
(`docs/history/design-plan-2026-09-27.md`, "the plan": Scope line 31, §2, §3a, §4, §7, §8, §9,
§10, §10a "Moving to AWS", §11, §13, §14, §15, §16, §17 M1). Where this document and the plan
differ, this document wins, and SPEC wins over both. Stale plan text is replaced here with the
decision that changed it cited; nothing is dropped silently (the dropped items are listed under
"Removed from the plan's M1"). The plan's §10 role table is replaced by this document's.

Real-service tests and every AWS dev-stack run need the operator's go-ahead each time; their code
stays in the session scratchpad.

## Scope

Run ecf in the operator's own AWS account, so mail is checked, fraud is flagged and approvals
execute with every computer closed. One person (the operator) uses it, but the data, identity and
export format are built for M2's teams (OD-579), so M2 adds features without changing them.

In scope:

- **Storage behind repositories, local mode first** (OD-601): the service's SQL moves behind
  per-aggregate repositories with SQLite as the first adapter, before any AWS code. M1's largest
  step.
- **Infrastructure:** CDK `infra/` (Python), two stacks per install, IAM under a permissions
  boundary, CI policy checks (plan §10, OD-124).
- **Services:** Lambdas `ecf-api`, `ecf-broker`, `ecf-jobs`, `ecf-slack-in`, `ecf-slack-out`,
  `ecf-liveness`, the Cognito trigger and the auto-delete helper; DynamoDB (PITR); S3; SQS FIFO;
  Secrets Manager; SSM; EventBridge Scheduler; CloudWatch, SNS and Budgets.
- **Identity:** Cognito Essentials with TOTP, CLI and step-up app clients (plan §8).
- **Server-side pre-check** on the 1-minute tick (plan §3a), with facts, triggers and approved
  actions in the broker.
- **Slack over a public request URL**, installed by hand (OD-609).
- **Operations:** `ecf init --mode aws`, `upgrade`, `destroy`, `doctor`, `budget set`,
  `retention set`, `billing activate-tags`, `iam deployer-policy`, dev stack, and `ecf migrate`
  local to AWS, restore-shaped (OD-602, OD-614).
- **Evals in AWS mode** (OD-611): the synthetic set ships with the release; eval runs keep their
  state in DynamoDB.
- **Bedrock:** `/ecf-review` and `/ecf-eval` can run Claude through Amazon Bedrock instead of the
  operator's Claude plan, on demand (OD-578, OD-595), from `ecf claude` on the laptop. No Lambda
  calls Bedrock in M1; M4's scheduled review adds a server-side path and a boundary change.
- **M2 foundations, run with one person** (OD-579): the people model, `people.id` on every grant,
  approval, answer, audit row and security notice, identities with a confirmation state, role
  checks per address, approval mode and approver count fields, approvers as a list, people and
  roles in export and import (data format 5, OD-608).
- **The chat seam for M11, both directions:** an inbound port beside `ChatSurface`, Slack its only
  adapter.

Not in scope: multi-user features (M2), remote MCP for claude.ai (M3), scheduled Claude review
and the always-on host (M4), Linux as a local-mode platform (M5), Microsoft Teams (M11), Lambda
code signing (OD-606), Slack OAuth install and token rotation (OD-609), customer-managed KMS keys
(OD-612).

## Decisions

### Carried from the plan (SPEC OD rows)

- OD-016: `aws` mode is DynamoDB with PITR plus S3, one base and one app stack per install, a
  dedicated AWS account recommended, default encryption.
- OD-021: fetches are asynchronous. OD-022: the scheduler tick is every minute.
- OD-080, OD-081: answer proposals stay off per connection (M3); refresh-expiry warnings on day
  25, then daily.
- OD-090: `ecf init` turns on CloudTrail log file validation, or creates a trail. OD-091: budget
  default $20 (infrastructure; Bedrock has its own Budget, OD-604). OD-092: secrets under default
  AWS-managed encryption (the grant HMAC key is replaced by a signing key pair, OD-612).
- OD-124: `infra/` is a Python CDK app. Terraform is not considered (operator, 2026-11-04).
- OD-150: service-side step-up only; the six-reviewer pass's M1 fixes.

### Since the plan

- **OD-578:** Bedrock moved from M4 to M1. Scheduled Claude review stays in M4.
- **OD-579:** M1 builds M2's foundations, run with one person.
- **OD-587 to OD-592:** M2 is "Multi-user teams"; M3 is remote MCP alone; Microsoft Teams is M11
  and needs only M1's public HTTPS endpoint; M1 next, then M11.
- **OD-506, ADR 0031:** presets A and B, Gemma and Ollama are removed. **OD-516:** preset D is held
  back, so C only.
- **OD-458:** releases are GitHub Releases only.
- **OD-551, OD-583, OD-584:** model-written drafts are approved only when shown in full; the draft
  checker and the draft card are ported unchanged.
- **OD-557:** `slack_out` retry waits 30 s, 1, 2 and 5 minutes (in AWS mode at least 60 s).
- **OD-217, OD-507:** the audit log, `item.classified` rows and items that fired a fraud, weak
  fraud or regulator trigger or were quarantined are never pruned.
- **OD-318, OD-362 to OD-372:** restore rules (generation bump, 7-day restored-keyword guard, the
  typed backup key as the only trust root, per-address resume after a mail check); `ecf migrate`
  follows them (OD-614).
- **OD-358:** import drops Slack routes and identity (reconciled by OD-596).

### Operator decisions 2026-11-04, planning (SPEC OD-593 to OD-600)

- **OD-593, M1 started;** the AGENTS.md AWS-skills rule is lifted.
- **OD-594, privacy destinations:** the operator's own AWS account and Amazon Bedrock may receive
  email content; whole messages only in Lambda memory while processed, never in S3, SQS, DynamoDB,
  Lambda `/tmp`, logs or any cache ecf operates; only excerpts stored.
- **OD-595, Bedrock and the provider:** `claude.provider` = `plan` | `bedrock`, default `plan`,
  changed with step-up and a Security Notice; `ecf claude --provider bedrock` for one session
  (step-up, OD-603); the provider shows in the status line, `claude_calls`, `claude_sessions` and
  `ecf stats`.
- **OD-596, identities on import:** people and roles always exported; identity links carry over on
  `ecf migrate` only.
- **OD-597, testing without cost:** moto; SAM and DynamoDB Local on Colima; a real dev stack only
  for what can't be faked. No LocalStack, no tunnels.
- **OD-598, platform:** the AWS Lambda runtime, managed `python3.12` arm64, zip; a deploy-time check
  asserts 3.12.6 or later.
- **OD-599, Bedrock retention `none`;** Fable unavailable on Bedrock; a pin refusing `none` stops
  the build (checked first, Step 0b).
- **OD-600, one release,** minor (OD-608), checked path.

### Operator decisions 2026-11-04, after Phase R round 1 (SPEC OD-601 to OD-609)

- **OD-601, repositories first:** the SQL (843 `execute` calls in 104 of 149 `ecf_server` modules;
  42 tables) moves behind per-aggregate repositories with SQLite first and contract tests; the
  mapping is written and reviewed in that step.
- **OD-602, `ecf migrate` is restore-shaped** (detailed by OD-614): same `install.id`, generation +
  1, install name, routes and identity links; one person proven by a local and a Cognito step-up
  bound to the bundle; fixed cutover; defined rollback.
- **OD-603, provider and the gate** (amended by OD-611): a `live` address refuses a session whose
  provider has no synthetic pass; the first Bedrock pass is a full C `/ecf-eval` through Bedrock
  (0 unsafe, fraud guard N/N, the one rule); `--provider bedrock` needs step-up; a parity step.
- **OD-604, Bedrock cost and Region:** projected cost shown at init and at `--provider bedrock`;
  a separate Bedrock Budget; tagged application inference profiles; geographic `us.` profiles only;
  refuse while invocation logging is configured.
- **OD-605, short-lived AWS credentials only:** IAM users and root refused; assumed roles only.
- **OD-606, no Lambda code signing in M1;** the zip's trust root is the GitHub Release.
- **OD-607, approved actions run inside the mailbox check;** no `actions.fifo`.
- **OD-608, export data format 5,** reading 4 and 5; release minor.
- **OD-609, Slack in AWS mode:** manual install, stored signing secret, no token rotation.

### Operator decisions 2026-11-04, after Phase R round 2 (SPEC OD-610 to OD-614)

- **OD-610, Lambda concurrency quota (E1):** `ecf init --mode aws` requires the account's Lambda
  concurrency quota to be at least 100 plus the install's reserved total (AWS keeps 100 unreserved);
  below that it requests an increase and does not deploy the app stack until AWS grants it (on a
  new account this can take days). `doctor` checks it.
- **OD-611, evals in AWS mode (E2):** the built synthetic set ships in the release zip, identified
  by its `labels.jsonl` hash (synthetic text, so no privacy change); the gate's synthetic check
  reads it from the code package, not a disk path. Eval-run state (`ecf eval run` progress and
  `/ecf-eval`'s run registry, claims and outcomes) lives in DynamoDB; `ecf eval run` is an `ecf-jobs`
  job. The Bedrock gate run runs against the AWS install. Amends OD-603: the gate key gains the
  provider only when it is `bedrock`, so the M1 upgrade and plan-provider installs keep their keys
  and no live address is demoted; gate rows are stored per provider.
- **OD-612, approvals signed with a key pair, no customer-managed KMS key (E3):** grants are signed
  with Ed25519. The private key is in Secrets Manager under default encryption, readable by
  `ecf-api` only; the broker gets only the public key, set at deploy as a stack parameter that no
  runtime role can change. A compromised broker can check approvals but not create one. AWS-managed
  KMS keys can't be used for ecf's own signing ("you cannot use AWS managed keys in cryptographic
  operations directly"), and the operator declined a customer-managed key. The broker's own
  pre-check actions run without a grant under a fixed code rule limited to label and flag.
- **OD-613, no `transitions.fifo` (E4):** a second EventBridge schedule invokes `ecf-api`'s
  due-work sweep every minute; `ecf-api` finds and moves due items itself.
- **OD-614, migrate under restore's rules** (round-2 findings, accepted 2026-11-04; details OD-602):
  the operator types the backup key at `migrate run`; `ecf-jobs` derives the public key and the
  signing seed from it and verifies the bundle (no public key stored in SSM, no seed transferred,
  OD-362, OD-364); the local service is held, exported, stepped up and then uninstalled (plist
  removed, data kept), so it can't return at login; the AWS install holds every address from
  fetching until the Slack switch, then resumes each address only after its mail check passes
  (OD-366); rollback is `ecf restore` of an AWS-signed bundle into local after AWS is held; the
  rollback window is 7 days, matching the restored-keyword guard; `migrate finalize` is defined.

## Design

### Architecture

```
Laptop (operator)                    AWS (base stack + app stack)                       External
─────────────────                    ────────────────────────────                       ────────
ecf CLI ─────────────JWT───────────► API Gateway HTTP API ─► ecf-api ─┬─► DynamoDB / S3 / SSM
ecf claude (Claude Code, on demand)                         (rules,    ├─► jobs.fifo ─► ecf-jobs
  └ relay (OTLP, status line, MCP) ─JWT + session token─►   policy,    │   (import, export, restore,
      ecf-mcp ─► relay                                      signing)   │    eval runs, housekeeping)
  Claude: Anthropic (plan) or Bedrock us. profile                      └─► invoke ─► ecf-broker ─► IMAP/SMTP
                                                                        (fetch, facts, triggers, pre-check,
                                                                         due approved actions)
                                     Slack ─signed─► API GW ─► ecf-slack-in ─► slack-in.fifo ─► ecf-api
                                     ecf-api / broker / liveness ─► slack-out.fifo ─► ecf-slack-out ─► Slack
                                     Scheduler ─► ecf-liveness (tick) ─► ecf-broker (due checks)
                                     Scheduler ─► ecf-api (due-work sweep, OD-613)
                                     Scheduler ─► ecf-jobs (retention, digests, summary, model watch, export)
                                     CloudWatch alarms, CloudTrail rules ─► SNS ─► admin email
Cognito (Essentials, TOTP): app clients CLI (PKCE loopback) · step-up (SRP only)
```

### Who may call and write what

A Lambda that is invoked learns nothing about who invoked it, and within one account an identity
policy alone is enough to send to a queue. So:

- **Invocation:** `ecf-api` is invoked only by API Gateway (`SourceArn`), its `slack-in.fifo`
  event source mapping and its Scheduler schedule; `ecf-jobs` only by its `jobs.fifo` mapping and
  its schedules; `ecf-slack-in` only by API Gateway. No runtime role holds `lambda:InvokeFunction`
  on `ecf-api` or `ecf-jobs` (a CI test). The `eventSourceARN` check in handlers holds only because
  of this rule. `ecf-api` and `ecf-liveness` may invoke the broker.
- **Queues:** every queue policy denies `sqs:SendMessage` unless `aws:PrincipalArn` is a named
  sender (a condition, never `NotPrincipal`): `slack-in.fifo` ← `ecf-slack-in`, `ecf-slack-out`;
  `slack-out.fifo` ← `ecf-api`, `ecf-broker`, `ecf-liveness`; `jobs.fifo` ← `ecf-api`. `jobs.fifo`
  messages carry only a job ID; parameters come from the job record `ecf-api` wrote.
- **Bearer:** `ecf-api` validates the `Authorization` bearer itself on every route and never reads
  `requestContext.authorizer` (a test sends a forged-context event).
- **Approvals (OD-612):** `ecf-api` alone signs grants (Ed25519 private key); the broker verifies with
  the public key and executes only a validly signed `approved` grant.
- **Writers:** `ecf-api` is the status writer for everything except a closed list the broker writes
  through the same conditional-update helper: `new → awaiting_claude` and the pre-check decision;
  `executing → executed | failed | failed_unknown`; grants `approved → consumed | voided | held`.
  The OD-181 rule becomes "only the helper writes `status`", with a test.
- **Stated limit (SPEC §12.2):** `ecf-slack-in` holds the signing secret, so a compromise of it can
  sign clicks as the operator's member ID: one-click actions (archive, junk, label, Dismiss) are
  within its reach; sends and anything else needing step-up are not.

Per-role table (replaces the plan's §10 table). Where DynamoDB can't scope writes by key, the limit
is enforced in code and stated; `dynamodb:LeadingKeys` is used where the key design allows.

| Role | Invoked by | Writes | Secrets and keys |
|---|---|---|---|
| `ecf-api` | API Gateway; `slack-in.fifo`; sweep schedule | main (transitions, people via its table), `ecf-api` table (grants, settings, people, identities, sender confirmations), excerpts (`expires_at` only, `dynamodb:Attributes` condition); sends to `slack-out.fifo`, `jobs.fifo`; invokes broker; S3 `jobs/` presigned URLs | grant private key; Slack signing secret; mailbox secrets write-only (`CreateSecret`, `PutSecretValue`); Cognito `AdminUserGlobalSignOut` |
| `ecf-broker` | `ecf-api`, `ecf-liveness` (async) | main (pre-check, closed transitions, `LEASE#`, `CURSOR#`), grants (closed list), excerpts; sends to `slack-out.fifo` | mailbox secrets (`lambda:SourceFunctionArn` condition); grant public key (parameter) |
| `ecf-jobs` | `jobs.fifo`; housekeeping schedules | main and `ecf-api` table (import, restore, migrate); S3 `exports/`, `jobs/`, `backups/`; DynamoDB backup and scratch restore | export signing seed (derived in AWS at migrate or init, never transferred) |
| `ecf-slack-in` | API Gateway only | the Slack table (dedupe keys, cancel markers); sends to `slack-in.fifo` | Slack signing secret; bot token (for `views.open` only) |
| `ecf-slack-out` | `slack-out.fifo` | main: Slack message records, `slack_calls`, audit and escalation-state keys only (code-enforced); sends to `slack-in.fifo` (route and channel notices for `ecf-api`) | bot token |
| `ecf-liveness` | tick schedule | main: `TICK#`, audit-flush state; S3 `logs/` (create-only); sends to `slack-out.fifo`; invokes broker | none |

### The functions

- **`ecf-api`:** routes; rules and policy on stored facts; transitions; grant signing; config,
  stage, sensitivity, pause, outbound; people, identities, sender confirmations; role checks per
  address; consumes `slack-in.fifo`; the due-work sweep (approval expiry, grant voiding, `delayed →
  executing`, stale marking) on its schedule. Routes that can't finish inside 30 s and 10 MB are
  jobs. The checks stream (`/v1/checks`, NDJSON) becomes "enqueued; poll `ecf status`".
- **`ecf-broker`:** the only holder of mailbox credentials. Invoked per address by `ecf-liveness`
  (due checks) or by `ecf-api` (an approval, a manual check). Each run:
  - takes the address lease (`LEASE#<address>`, 180 s, renewed inside the handler between pages
    and actions, no thread) and puts a fencing `ConditionCheck` on it in every transaction, so an
    overrun can never write a cursor backwards;
  - checks the maintenance flag and the `migrating` hold at start;
  - when started by an approval, runs due actions before the fetch page;
  - fetches with a deadline of min(start + `max_per_check`, remaining time − a reserve covering the
    worst parsing child, the write-back and one action);
  - computes facts, fraud and regulator triggers and the pre-check decision in memory, writing
    them with the decision, escalation and audit rows in one transaction (the whole message never
    leaves the broker);
  - executes due approved actions (OD-607) with the same open mailbox, stopping when less than one
    action budget (120 s) remains and leaving the rest due;
  - backfill pages run here too (they need IMAP).

  Parsing runs in v1's isolate child, launched with a scrubbed environment (no `AWS_*`, minimal
  `PATH` and `PYTHONPATH`) and no AWS client; it returns new DNS answers in its JSON and the parent
  writes them (`isolate.py:96-99` passes no `env` today; a test asserts no `AWS_` key reaches the
  child). Memory at least 2,048 MB (measured in Step 8); timeout = `max_per_check` + reserve,
  capped at 900 s, with `max_per_check` capped in AWS mode so the sum fits (value set in Step 8).
  v1's scheduling rules are ported: `next_due_at`, a due time asked for during a running check is
  kept (`schedule.py:197-199`), a busy lease retries after 60 s. Async config:
  `MaximumRetryAttempts` 0, `MaximumEventAgeInSeconds` 300, on-failure queue alarmed. The broker
  joins the safety TCB in SPEC §3.2.
- **`ecf-jobs`:** import, export, restore (including the DynamoDB backup merge), `rules test`,
  `ecf eval run` (OD-611), and housekeeping on schedules (retention, digests, daily summary,
  weekly model watch, scheduled export). Bundles move by presigned S3 URL (short expiry, size
  capped by a presigned POST `content-length-range`); import and export stream (S3 → decrypt →
  `tarfile` stream mode), import and restore are resumable with a progress item and write a new
  generation that is flipped under the maintenance flag; older bundles are migrated in an
  in-memory SQLite database with every migration shipped in the zip. Memory set from Step 8's
  measurement, and an AWS-mode bundle cap derived from it. `jobs.fifo` group = job kind, with
  import, restore and export mutually exclusive by a lease; an alarm on the oldest message's age.
  Part of the trust root with `ecf-api` (SPEC §3.2).
- **`ecf-slack-in`** (public, import-light, an import-linter contract): signature over the raw
  body; timestamp within 5 minutes and not in the future; `api_app_id` and `team_id`; dedupe by
  conditional put; for a Cancel button, a per-item cancel marker in the same table; for a form or
  window button, `views.open` with a fixed "Checking…" view holding no data; then the raw click
  (with the returned `view_id`) to `slack-in.fifo` (group = Slack user ID, as v1; deduplication ID
  = the dedupe key); 200. It never says the action was taken and reads no other table.
- **`ecf-slack-out`** (internal): consumes `slack-out.fifo` (group = channel ID); posts,
  `chat.update`, `views.update`, `chat.postEphemeral`, the dead-man `chat.scheduleMessage`; records
  each post's channel and ts; sends `channel_gone` and route notices to `ecf-api` through
  `slack-in.fifo`. No `response_url` anywhere.
- **`ecf-liveness`:** the 1-minute tick. A tick lease keyed on the scheduled time (`TICK#<time>`;
  the Scheduler attribute is confirmed in Step 8), `MaximumEventAgeInSeconds` 60, timeout 50 s,
  reserved concurrency 1; time-boxed sweeps: due checks (invokes the broker), the audit flush (its
  own lease), "Needs you" refresh, reminders, refresh-expiry warnings, the dead-man message (via
  `slack-out.fifo`, its own group). Heavy jobs are `ecf-jobs` schedules, not tick work. No thread
  outlives a handler (a test covers every function).
- **Maintenance flag and holds:** every writer (`ecf-api`, broker, `ecf-jobs`, both Slack functions,
  liveness) honours the maintenance flag; `slack-in.fifo` is held by visibility during maintenance,
  never sent to its DLQ.
- **Laptop:** the CLI, and Claude Code during `/ecf-review` and `/ecf-eval`. `ecf claude` runs a
  relay on loopback that carries OTLP telemetry, the status line and every `ecf-mcp` call to
  `ecf-api`, adding the CLI's refreshable token. Deployer and admin credentials are short-lived
  (OD-605); the Bedrock profile is checked to be the Bedrock permission set.

### Repositories and the DynamoDB mapping (OD-601)

- **Step 0** extracts repositories per aggregate: items and transitions, grants and settings,
  people and identities, senders and threads, audit, alerts, Claude telemetry, eval runs, Slack
  routes and messages, DNS cache, jobs and leases. SQLite is the first adapter; contract tests run
  every repository against each adapter.
- **Mapping first:** the mapping table and the access-pattern list are written and reviewed before
  the extraction merges, and every repository method maps to a `GetItem`, a `Query` on a key or GSI,
  or a `TransactWriteItems` row (a Step 0 pass item), so interfaces don't come out SQL-shaped. The
  two hardest aggregates (items and transitions, audit) get a DynamoDB Local spike in Step 0.
- **Mapping table:** one row per table (migrations 0001 to 0043): store (DynamoDB table, S3
  prefix, SSM, Secrets Manager or dropped), keys and GSIs, writer role, TTL rule, exported, atomic
  group. Tables draft 1 left without a home are listed explicitly: `addresses`, `alerts`,
  `alert_outbox`, `check_state`, `claims`, `claim_batches`, `claude_calls`, `claude_sessions`,
  `slack_calls`, `model_calls`, `escalations`, `delays`, `draft_versions`, `eval_runs`,
  `eval_results`, `slack_messages`, `slack_dedupe`, `processing`, `dns_cache`, `downloads`,
  `schema_migrations`, and the install-level settings keys.
- **Access patterns:** every sweep and CLI query with its key or GSI (sparse `due_at`, open items,
  audit by time); no `Scan` in handler paths (a test). Unbounded lists are capped below the item
  size limit.
- **Atomicity:** each v1 write transaction becomes one `TransactWriteItems`: a status transition
  with `ConditionExpression status = :expected AND version = :v` plus its audit row; approval =
  transition + grant + audit; pre-check = decision + escalation + audit; a terminal transition
  voids grants, deletes delays and sets children's expiry; grant consume includes a
  `ConditionCheck` that no cancel marker exists; every broker transaction includes the lease
  fencing check; config apply + stage demotion. Counters use `ADD` under the same condition.
- **Storage versions:** each item carries `storage_version` (`schema_version` already means the
  classification schema version on items). Readers ignore unknown attributes and accept higher
  storage versions; writes to existing items are `UpdateItem` on named attributes, never a whole
  `PutItem`; a new attribute ships one release after a reader that tolerates it (a contract test).
  AWS exports stamp the release's SQLite schema number; the exporter upgrades each item on read.
- **Settings across stores:** export, import and restore assemble `settings.jsonl` from DynamoDB
  settings items, SSM and S3 config by a fixed key list; OD-368's "this computer's state" keys are
  listed for AWS mode.
- **Round trip:** a fixture database (items at mixed storage versions) exported by both adapters
  gives byte-identical `tables/*.jsonl`; local → AWS → local is equal for every exported table
  modulo `origin` and new IDs; `test_every_exported_table_is_copied_or_handled_apart` runs over
  the DynamoDB adapter too.

### People, identity and roles (OD-579)

- **Model:** `people {id (random UUID), identities, roles: {address_id: role}, admin, status:
  active | inactive, source_install}`. In M1 one person, the operator, admin. **Invariant:** the
  admin holds the approver role on every address (applied on address add and on import), so an
  import into a fresh or different install always leaves someone able to approve. Only an admin
  changes a person's status, with step-up and a Security Notice.
- **Identities:** `{issuer, subject, kind: cognito | slack | os_user, status: pending | confirmed,
  confirmed_at, confirmed_by}`. Cognito issuer = `cognito:<pool id>`; Slack issuer includes the team
  (and enterprise) ID. A Slack member is `pending` until its DM Confirm click, which writes the
  identity record. **Authority rule:** a confirmed identity of an active person holding the role;
  anything else is refused and audited (member ID only).
- **Re-link:** a changed Cognito identity (new pool after destroy and re-init, recreated user) or a
  changed OS identity (new Mac) is re-linked by `ecf people relink`, needing admin step-up (local
  step-up, or deployer credentials plus Cognito step-up in AWS) and a Security Notice.
- **`people.id`** on every grant, approval, answer, audit row and security notice. Backfill:
  the operator for OS-user and `slack:*` actors, null for `service` and `migration`; nullable
  columns, no table rebuild. `grants.principal` (always `'os_user'`) is replaced by `approvers`.
- **Per address:** approval mode `single`, approver count 1, `stepup.totp` `off` (M2's
  multi-approver TOTP mode).
- **Export format 5 (OD-608):** people, roles and identities are bundle files. Format 3 bundles are
  no longer readable, and after `ecf upgrade --to` a 3.2.x release, format-5 bundles aren't either:
  CHANGELOG and the §11.10 warning say so.
- **Import (OD-596):** on import into a different install the bundle's people arrive `inactive`
  with `source_install`; a format-4 bundle gets one synthetic inactive person carrying
  `source_install`; the operator is filled in only on restore or migrate of the same install.
  `--replace` never removes the target's active admin. "Same install" = same `install.id` and a
  bundle verified under the typed backup key.

### Cognito

As the plan's §8, with the fact checks and both review rounds:

- **Pool:** Essentials; `AllowAdminCreateUserOnly`; TOTP required; SMS MFA off; device remembering
  off; deletion protection on; prevent user-existence errors; case-insensitive usernames; group
  `admins`; resource server `ecf` with scopes `observe`, `work` and `stepup`; managed login
  version 2 (`prompt` needs it), asserted by a snapshot test; the issuer type set explicitly. The
  pool and its clients are stateful resources under the change-set guard.
- **CLI client:** authorization code with PKCE (S256) on fixed registered loopback ports for the
  callback and the sign-out URL; `AllowedOAuthScopes` = `ecf/observe ecf/work` only (no `openid`,
  so no ID token; no `aws.cognito.signin.user.admin`); attribute read and write empty. Access token
  15 minutes; refresh 30 days with rotation, refreshed through the token endpoint. Tokens in the
  OS secret store.
- **Login always fresh:** `ecf login` sends `prompt=login`; the loopback callback answers with a
  302 to the Logout endpoint, so the managed-login cookie doesn't outlive the login; if the
  sign-out page isn't reached, the CLI warns the cookie may live up to an hour. `ecf logout` and
  `revoke-device` revoke the refresh token and clear the cookie the same way.
- **Step-up client:** `ALLOW_USER_SRP_AUTH` only. The nonce (bound to grant or change hash and
  person) is sent in `ClientMetadata` on both `RespondToAuthChallenge` calls (password verifier
  and TOTP). The V2 trigger, only for the step-up client and `TokenGeneration_Authentication`,
  copies it into claim `ecf_nonce` and adds scope `ecf/stepup`; on every client and source it
  suppresses `aws.cognito.signin.user.admin`. The proof travels in the request body (the API
  Gateway authorizer's audience is the CLI client only). `ecf-api` requires `client_id` = step-up
  client, `token_use=access`, scope `ecf/stepup` (refused on every other route), `ecf_nonce` = the
  nonce, `auth_time` and `iat` within 5 minutes, `sub` = a confirmed identity of the active
  approving person; it consumes the nonce and `jti` with conditional puts. Step-up token 5
  minutes, refresh revoked. `MFA_SETUP`, `SOFTWARE_TOKEN_SETUP` and `NEW_PASSWORD_REQUIRED` during
  step-up are refusals. The 5-per-10-minutes verify limit is a `RATE#` item.
- **Password change:** managed login's forgot-password flow (the user's email is verified at
  init) or the break-glass runbook; the CLI has no password-change command.
- **Trigger:** no DynamoDB or network access; reserved concurrency and an alarm.
- **Runtime rights:** only `ecf-api` has Cognito rights (`AdminUserGlobalSignOut`); `RevokeToken`
  is called with the client, not IAM. `AdminCreateUser` runs once from `init`.
- **Token validation:** PyJWT, JWKS cached (refetch on unknown `kid` at most once a minute);
  issuer accepted as exactly `https://cognito-idp.<region>.amazonaws.com/<pool>` or
  `https://issuer-cognito-idp.<region>.amazonaws.com/<pool>`; `doctor` compares the pool's
  `.well-known/openid-configuration` issuer with the authorizer's (whether the HTTP API authorizer
  takes two issuers is checked in Step 7; if not, the pool's issuer type is pinned).
- **Lost authenticator (break-glass):** `AdminDeleteSoftwareToken`, `AdminSetUserPassword`
  (temporary), `AdminUserGlobalSignOut`; logged and emailed; enrolment only in managed login;
  `doctor` and a custom metric flag a user without TOTP.
- **Stated limits (SPEC §12.2):** password and TOTP typed into the `ecf` process; the CLI's text is
  all the person sees; a stolen refresh token is 30 days of non-step-up access until revoked.
  Runbook "stolen laptop".
- **Claude Code holds no Cognito token, and service tokens are not internet credentials:** every
  `ecf-mcp` call goes through the relay in `ecf claude`, which adds the CLI's JWT; `ecf-api`
  requires the CLI JWT and the session's profile token on MCP routes; the authorizer is on every
  route; there is no unauthenticated OBSERVE in AWS mode; profile tokens are bound to the session
  and expire. SPEC §10.4 is updated.
- **Cognito tested with more than one user** (OD-579), in the dev stack.

### Data and state

- **DynamoDB, four tables** (on-demand; PITR and deletion protection on the first three): main;
  `ecf-api` table (grants, settings, people, identities, sender confirmations); excerpts; the Slack
  table (dedupe keys and cancel markers, each with a TTL attribute, 1 day; written only by
  `ecf-slack-in`, read by `ecf-api` and the broker).
- **Expiry:** no TTL at creation. The terminal transition sets `expires_at` = terminal time +
  item retention on the item and its excerpts, only when prunable (never for fraud, weak-fraud,
  regulator or quarantined items, OD-217). Item retention stays its own step-up setting
  (`log_retention_days`, as v1); `retention set` re-stamps existing items through an `ecf-jobs`
  backfill. CloudWatch log-group retention is a separate stack parameter. `AUDIT#` rows get a TTL
  only after a confirmed S3 flush. Readers and the exporter filter expired items.
- **Grants (OD-612):** Ed25519 signatures over `{grant_id, action_hash, content_hash, status,
  approvers, stepup_nonce_id, expiry}`, re-signed by `ecf-api` at every state change it makes; a
  broker-made change (consume, void, hold) is recorded with the broker's own transition audit and
  is valid only as a move out of `approved` (a signed `approved` grant is required to execute).
- **S3:** one bucket; Block Public Access; `BucketOwnerEnforced`; versioning; SSE-S3;
  `ExpectedBucketOwner`; TLS-only. `logs/` is create-only (`If-None-Match` required), has no expiry,
  and a bucket policy denies `s3:DeleteObject*` on it to every runtime role: it is the
  tamper-evident audit copy (a Step 8 deny test). `exports/` and `jobs/` expire in 1 day; every
  versioned prefix has a noncurrent-version expiration rule. `backups/` holds scheduled exports,
  pruned to `export_keep` in code; `doctor` warns that it is the only copy and offers `ecf export
  --pull` to the laptop.
- **Crash safety:** a `processing <stable_id>` marker before each message, counted across
  invocations; quarantine after 2 crashes (SPEC §12.1).
- **No temporary files** on the Lambda code path (a test).

### Data migrations, upgrades and backups

- **Expand and contract:** additive attributes, lazy upgrade on read by `storage_version`, an
  idempotent backfill job with a progress item. A rewrite of every item needs a planned outage.
- **Before the change set:** `ecf upgrade` takes an on-demand DynamoDB backup (kept until the upgrade
  settles), sets the maintenance flag, and waits for leases to drain (as OD-379) before executing.
- **`ecf upgrade --to <previous>`** rolls back code only; data stays forward-compatible by the
  storage-version rules.
- **Restoring a backup:** never by swapping tables (a backup restores only to a new table, and the
  change-set guard and deletion protection rightly block replacing a stateful table). `ecf-jobs`
  restores to a scratch table and merges it into the live table by `downgrade._merge`'s rules:
  `sent`, `threads`, `senders` and `gate` kept from live, grants voided, executing items to
  `failed_unknown`, cursors rewound; then deletes the scratch table. `CreateBackup`,
  `RestoreTableFromBackup` and the scratch-table rights are in `ecf-jobs`' role and the boundary.
  A table swap stays a disaster-only runbook.
- Server first, then clients (N and N-1); change-set guard on stateful resources.

### Queues, timers and delays

- **SQS FIFO:** `slack-in.fifo` (group = Slack user ID), `slack-out.fifo` (group = channel ID; the
  dead-man message has its own group), `jobs.fifo` (group = job kind). Each has a DLQ alarmed on
  depth; `maxReceiveCount` 5; visibility at least 6 times the consumer's timeout; batch 1-2 with
  `ReportBatchItemFailures`; `MaximumConcurrency` 2 per mapping. `slack-in.fifo` and its DLQ keep
  messages 1 day, SSE on; DLQ tools never print bodies. No `actions.fifo` (OD-607), no
  `transitions.fifo` (OD-613).
- **Waits:** every scheduled wait (approval TTLs, 1-hour AWS delays, `delayed → executing`, held
  actions) is a `due_at` acted on by the next sweep. `slack-out.fifo` retries by
  `ChangeMessageVisibility` only. OD-557's 30-second first wait becomes at least 60 s in AWS mode.
- **Holds:** a paused address or a tripped send limit holds an approved action without counting an
  attempt (grant `held` with a `due_at`). A busy lease is not an attempt.
- **Clicks and freshness:** `ecf-api` verifies the HMAC of every queued click and judges the 5-minute
  window against the message's SQS `SentTimestamp`, not the time it is processed; a decision click
  older than 10 minutes when processed is refused, audited, and the person gets an ephemeral "click
  again". Slack documents no retry for interactivity; the dedupe key stops replays and ecf's own
  resends; each grant's conditional update stops a double click (the second is audited as
  `slack.click_failed`).
- **Cancel:** a Cancel click writes its marker in `ecf-slack-in` before the 200, and the broker's
  grant-consume transaction fails if the marker exists, so a Cancel acknowledged by Slack always
  wins over a `delayed → executing` that hasn't consumed the grant yet.
- **EventBridge Scheduler:** tick (`rate(1 minute)`) on `ecf-liveness`, sweep (`rate(1 minute)`) on
  `ecf-api`, housekeeping schedules on `ecf-jobs`; `MaximumRetryAttempts` 0 with SQS DLQs; heartbeat
  alarms when the tick or the sweep runs fewer than once in 5 minutes.

### Slack over a request URL (OD-609)

- **Install:** by hand, as v1; the signing secret pasted from Basic Information into a hidden prompt
  goes to its own secret (read by `ecf-slack-in` and `ecf-api`); `set-tokens` accepts it under
  step-up with a Security Notice; `doctor` checks it. No token rotation.
- **Signature and replay:** HMAC-SHA256 over `v0:<timestamp>:<raw body>`; dedupe key = SHA-256 of
  timestamp + raw body (unique per genuine click, since each carries its own `trigger_id`),
  conditional put first, TTL 1 day.
- **Forms and windows:** `ecf-slack-in` opens a fixed "Checking…" view; `ecf-api` checks identity and
  role, writes the audit, then `ecf-slack-out` sends `views.update` (by `view_id`; it needs no
  `trigger_id`) with the real form or window, or "not allowed". Step 9 times it.
- **Public route:** route throttle (burst 20, rate 10 a second); signature checked before any parse;
  body cap with 413; 401 with no body; signing secret re-read at most once per container per 5
  minutes and only for a fresh timestamp; access logs with `$context.*` fields only; an alarm on
  signature failures. AWS WAF can't attach to HTTP APIs (SPEC §12.2).
- **3-second budget:** Step 9 measures `$context.responseLatency` on forced-cold clicks; above the
  pass line, `ecf-slack-in` gets SnapStart or provisioned concurrency 1 (cost in the Cost table).
- **Switch on migration:** the AWS app stack is deployed and answering first; the local service is
  held; `apps.manifest.update` (mode-aware `manifest()`) switches the same app; the app-level token
  is revoked. Clicks fail visibly in Slack from the local hold until the switch (minutes; the
  runbook says so). Rollback to Socket Mode needs a new app-level token, created by hand on Basic
  Information (no API found: unverified); Step 9 runs one rollback switch.
- **Chat seam for M11:** `ChatInbound` with `verify(headers, raw_body) → Verified | Refused`,
  `parse(Verified) → Click | SurfaceEvent`, and `respond(Verified) → Ack`, where `Ack` is `Empty`,
  `OpenForm(Form, placeholder)` or `InterimCard(Card)` and each adapter renders it (Slack: 200 plus
  `views.open`; Teams: the invoke response body). Decisions stay asynchronous; the final card comes
  by update (Slack `chat.update`; Teams proactive update with a stored conversation reference).
  Neutral handler registry; opaque `RouteRef.channel`; `FakeChat` variants with every capability
  off and with "form must be in the ack"; routes `/chat/<surface>`.

### Bedrock and the Claude provider (OD-595, OD-599, OD-603, OD-604, OD-611)

- **How Claude Code reaches Bedrock:** `ecf claude` writes `CLAUDE_CODE_USE_BEDROCK=1`,
  `AWS_PROFILE`, `AWS_REGION` and the model variables (the install's application inference profile
  ARNs) into its dedicated config's `settings.json` `env` block. The `sonnet` alias: Step 1 greps
  the plugin's agents; if none names `sonnet`, `ANTHROPIC_DEFAULT_SONNET_MODEL` is the Haiku pin's
  profile. No keys in environment variables or files (OD-605). `ecf claude --provider bedrock`
  refuses unless `GetCallerIdentity` shows the generated Bedrock permission set, so an admin or
  deployer role never sits in a session that reads hostile mail; `doctor` fails on any non-Bedrock
  action in that set.
- **Model IDs:** one application inference profile per pin, copied from the `us.` system profile and
  tagged; `global.` never. Permissions generated from the pins (the application profiles, the
  foundation model in each Region of the `us.` profile, `bedrock:InferenceProfileArn` condition,
  `GetInferenceProfile`, the retention read), with an optional Deny on `aws:RequestedRegion =
  "unspecified"`. The model string Claude Code reports for a profile ARN is recorded in Step 11's
  first session before the model check relies on it (`claude_pins.family` matches `claude-*` only).
- **Checked first (Step 0b):** each pin's `allowed_modes`, model access, the Haiku 5.5 ID form,
  Region.
- **Retention `none`** set with `PutAccountDataRetention` (admin credentials); `doctor` reads it
  back. Whether it holds across the `us.` profile's Regions and with the 1-hour cache: Step 0b and
  Step 11.
- **Invocation logging:** `init`, `doctor` and `--provider bedrock` refuse while a configuration
  exists; a CloudTrail rule alerts on `PutModelInvocationLoggingConfiguration` mid-session.
- **Gate (OD-611):** the gate key gains `provider` only for `bedrock`; gate rows per provider; a
  `live` address refuses, at session start, a session whose provider has no pass; `ecf eval
  compare` warns "different provider". A test: upgrading leaves live addresses live.
- **First Bedrock pass:** a full C `/ecf-eval` through Bedrock against the AWS install (OD-611),
  compared with a named plan-provider reference run on M1 code (Step 1b) under the one rule
  (`--margin 5`): 0 unsafe, fraud guard N/N; plus the 10-case determinism check, which passes when
  it flips no more fields than the plan arm on the same 10 cases. Recorded in SPEC §21.2.
- **Cost (OD-604):** about US$0.042-0.050 an item at Anthropic's global list prices (SPEC §16.3);
  `us.` profiles cost about 10 % more (unverified on Bedrock) and cross-Region routing may lower
  the cache-hit share, so about US$415-825 a month at 300-500 emails a day, US$70-85 at 50; a full
  gate run about US$20. Chosen for where data is handled and billed, not to save money. `init` and
  `--provider bedrock` print the projection; the Bedrock Budget (default US$100, set by `ecf budget
  set --bedrock`) alarms by install tag. Step 11 compares the bill with the projection: within
  ±25 %, or the projection is updated.
- **Telemetry:** the laptop relay forwards OTLP and status-line batches to `ecf-api` with the CLI's
  refreshable token, buffering up to 10 minutes while `ecf-api` is unreachable; the model check's
  windows live in DynamoDB. `api_error` rows are kept (status code, attempt, no text). Provider from
  the session's setting. Bedrock `cost_usd` shown as "API-equivalent".
- **What differs on Bedrock:** no WebSearch; no `/logout`; no plan rate limits in the status line.
  Subagents and the spawn-guard hook tested in Step 11; if either fails, Bedrock is blocked.
- **Weekly model watch** (an `ecf-jobs` schedule): each pin's profile still offered
  (`GetInferenceProfile`, `GetFoundationModel`), its retirement date, `allowed_modes` still has
  `none`.

### Prompt caching (operator request 2026-11-04)

- **Who controls what:** Claude Code builds the requests and places the breakpoints on both
  providers. ecf controls what it returns and the order of its agent instructions: stable content
  first, the message last.
- **What the rule means:** OD-594's "no email content in any cache" covers the caches ecf operates;
  the provider's prompt cache is part of the Anthropic or Bedrock destination, scoped to the
  operator's account, identical prefixes only. In a batched agent earlier items are in the prefix
  by design (SPEC §5.6).
- **Measured:** cache-read share per spawn from the existing `claude_calls` columns, by batch size
  and provider; `claude_calls.ended_at` added.
- **TTL:** 5 minutes by default. The cost model is fixed before Step 11 (uncached × 1, 5-minute
  write × 1.25 or 1-hour write × 2, read × 0.1, 0.05 on Opus 5.5), applied to the request gaps of
  the Step 1b reference run grouped by spawn; `ENABLE_PROMPT_CACHING_1H` only if it is cheaper.

### Caches in the AWS design (operator request 2026-11-04)

Rule: no email content in any cache ecf operates (OD-594).

| Cache | Lives in | Lifetime | If stale |
|---|---|---|---|
| Cognito JWKS (handlers) | Lambda memory | container life; refetched on unknown `kid`, at most once a minute | a rotated key fails closed until refetch |
| DNS for DKIM/DMARC (10-minute cap) | DynamoDB with `expires_at`; one `BatchGetItem` per page; an in-invocation memo passed to the isolate child, whose new answers the parent writes | TTL respected, capped at 10 min | a changed key or policy is seen up to 10 min late, as in v1 |
| Slack DM channel IDs | DynamoDB | until Slack refuses the ID | re-opened with `conversations.open` |
| Effective schema, rules, policy | Lambda memory, keyed by config version | until the version changes | each handler checks the version per invocation |
| Gate fingerprint | DynamoDB per address | until the inputs change | recomputed once; `passed_at` guards "Ready for live" |
| Synthetic set version | the code package | the release | none: shipped with the code |
| Secrets and the grant public key | Lambda memory / stack parameter | container life; re-read on `AUTHENTICATIONFAILED`, signing secret at most once per 5 minutes | a rotated secret fails once, then reloads |
| Bedrock and Anthropic prompt caches | the provider | 5 min or 1 h | none: identical prefixes only |

### Privacy (OD-594, OD-599, OD-604)

SPEC §12.4 AWS mode:

- **Your AWS account** (the install's Region): the broker holds full messages in Lambda memory only
  while processing and computes facts and triggers there; DynamoDB stores what SQLite stores
  locally; S3 holds audit logs (no content), config and encrypted export bundles; SQS carries IDs,
  except `slack-in.fifo`, which carries the raw Slack click for up to 1 day (action IDs, the person,
  form answers, and the card's own text, which can include subjects, senders and draft text);
  Secrets Manager holds the credentials and the grant signing key. PITR keeps deleted items up to
  35 days and S3 noncurrent versions are expired by rule.
- **Amazon Bedrock** (a session whose provider is `bedrock`): what Anthropic receives during
  `/ecf-review` and `/ecf-eval`, through a `us.` profile, so it may be processed in another US
  Region; retention `none`; refused while invocation logging is configured.
- **Logs:** a top-level catch in every handler logs through the no-content processor; Powertools'
  logger uses the same processor and `log_event` is forbidden by a test; the processor also drops
  `response_url`, `trigger_id`, `authorization`, `x-slack-signature`, `code`, `refresh_token`,
  `id_token`, `access_token`, `X-Amz-Signature` and `X-Amz-Credential`; a test searches captured
  logs after a fake Slack click and a fake app-password body.
- Slack, Anthropic and the mail provider are unchanged. Any other destination needs an OD.

### Infrastructure, IAM and deploy

- **Synthesizer:** `DefaultStackSynthesizer(generate_bootstrap_version_rule=False)`,
  `versionReporting` off, `aws-cdk-lib` pinned, template snapshot, CI test for no `cdk-hnb659fds`
  or asset references; asset-creating constructs avoided (`Bucket(auto_delete_objects=True)`,
  `Function(log_retention=...)`, `BucketDeployment`, `custom_resources.Provider`,
  `PythonFunction`); explicit log groups; inline auto-delete helper.
- **cdk-nag 3** (3.0.2, pinned), every suppression with a reason. **No VPC** (CDK assertion).
- **Two-phase deploy:** base stack; zip verified against the SHA-256 in the client wheel; app stack.
  No code signing (OD-606).
- **Credentials (OD-605):** `init`, `upgrade`, `destroy`, `budget set`, `retention set`, `billing
  activate-tags`, `migrate`, `doctor`'s AWS checks and the break-glass runbook refuse `:user/` and
  `:root` callers; `ecf iam deployer-policy` prints an IAM Identity Center permission set.
- **Concurrency (OD-610):** quota at least 100 + reserved total (`ecf-liveness` 1, `ecf-slack-in` 2,
  the Cognito trigger 1); `init` requests the increase and waits; `doctor` checks.
- **Boundary:** an exact Allow list for runtime roles, with a CI check that every AWS call the code
  makes (collected from the moto tests) is allowed by both the role and the boundary:
  - DynamoDB: `GetItem`, `PutItem`, `UpdateItem`, `DeleteItem`, `ConditionCheckItem`, `Query` (tables
    and `index/*`), `BatchGetItem`, `BatchWriteItem` (transactions are authorized per item
    operation); for `ecf-jobs` also `CreateBackup`, `RestoreTableFromBackup` and scratch-table
    `DescribeTable`, `DeleteTable` on `ecf-<install>-scratch-*` only.
  - S3: `GetObject`, `PutObject`, `ListBucket` (prefix-conditioned); the auto-delete helper's
    `ListBucketVersions`, `DeleteObjectVersion`.
  - SQS: `SendMessage`, `ReceiveMessage`, `DeleteMessage`, `ChangeMessageVisibility`,
    `GetQueueAttributes`.
  - Secrets Manager: `GetSecretValue`, `PutSecretValue`, `CreateSecret`, `TagResource` (on
    `ecf/<install>/*`). SSM: `GetParameter`, `PutParameter` (on `/ecf/<install>/*`, excluding
    deploy-set parameters).
  - KMS: `GenerateDataKey*`, `Decrypt` on `aws/sns` only. SNS: `Publish`. CloudWatch Logs writes.
  - Lambda: `InvokeFunction` on the broker only. Cognito: `AdminUserGlobalSignOut`. Bedrock (for
    the model-watch job): `GetInferenceProfile`, `GetFoundationModel`, the retention read.
  - Explicit Deny on control-plane actions (`lambda:UpdateFunctionCode`,
    `UpdateFunctionConfiguration`, `AddPermission`, `CreateEventSourceMapping`,
    `CreateFunctionUrlConfig`; `sqs:SetQueueAttributes`; `secretsmanager:PutResourcePolicy`;
    `dynamodb:UpdateContinuousBackups`, `DeleteTable` except scratch tables;
    `logs:DeleteLogGroup`; `cognito-idp:UpdateUserPool*`; `bedrock:PutAccountDataRetention`). The
    plan's boundary self-protection denies stand. The boundary applies to runtime roles only, not
    to cfn-exec.
- **cfn-exec:** under `/ecf-deploy/<install>/`; `iam:PassRole` only to `lambda.amazonaws.com` and
  `scheduler.amazonaws.com` for `role/ecf/<install>/*`; trust with `aws:SourceAccount` and
  `aws:SourceArn` `ArnLike stack/ecf-<install>-app/*` (confirmed in Step 8); denied
  `iam:CreateUser`, `CreateAccessKey`, `CreateLoginProfile`, `CreatePolicy`, `AttachUserPolicy`,
  function URLs with auth `NONE`, and `lambda:AddPermission` unless `lambda:Principal` is
  `apigateway`, `sqs`-via-mapping or `scheduler`. CI fails on any Lambda permission without
  `SourceArn` and any resource policy with `Principal "*"` and no condition.
- **Broker secrets:** `GetSecretValue` on mailbox secrets with `lambda:SourceFunctionArn`.
  `ecf-api`'s write-only mailbox access can change the IMAP host inside a secret; accepted
  because `ecf-api` is the trust root.
- **CI:** GitHub OIDC trust requires `aud = sts.amazonaws.com`, `sub =
  repo:<owner>/<repo>:ref:refs/heads/main` and the validator workflow's `job_workflow_ref`;
  `cfn-policy-validator` runs on pushes to `main` only; the static policy tests (no AWS) run on
  pull requests too; dedicated empty account.
- **Detective controls:** EventBridge rules on CloudTrail management events publish through an input
  transformer (metadata only) to the `-direct` topic, whose policy admits `events.amazonaws.com`
  with the rules' `SourceArn`. Events: Lambda `UpdateFunctionCode*`, `UpdateFunctionConfiguration*`,
  `AddPermission*`, `CreateFunctionUrlConfig*`, `Create/UpdateEventSourceMapping`; IAM changes
  under `/ecf/`, `CreateUser`, `CreateAccessKey`, `UpdateAssumeRolePolicy`; Secrets Manager under
  `ecf/<install>/`; `cognito-idp:Admin*`; DynamoDB `UpdateContinuousBackups`, `DeleteTable`,
  `UpdateTable`; CloudTrail `StopLogging`, `DeleteTrail`, `UpdateTrail`; S3 `PutBucketPolicy`,
  `DeleteBucketPolicy`, `PutBucketLifecycle`, `Put/DeletePublicAccessBlock`,
  `PutBucketVersioning`; SQS `SetQueueAttributes`, `AddPermission`; SNS `Subscribe`,
  `SetTopicAttributes`; Bedrock `PutModelInvocationLoggingConfiguration`,
  `Put/DeleteAccountDataRetention`. IAM events reach EventBridge in us-east-1 only (unverified):
  installs elsewhere get the IAM rules in us-east-1. `doctor` checks the trail is active.
- **SNS, alarms, teardown, tags, CloudTrail validation:** as the plan's §10.
- **Budgets:** infrastructure ($20, OD-091) and Bedrock (US$100 default, OD-604).

### Cost (one user, us-east-1; prices fetched 2026-11-04 (rv); restores the plan's §15)

| Item | Idle | 300-500 emails a day, 3 addresses |
|---|---|---|
| Secrets Manager: 3 mailboxes, bot token, signing secret, grant key, export seed, models key (7-8 at $0.40) | $2.80-3.20 | same |
| CloudWatch alarms, ~25 metrics (10 free, $0.10 each) | $1.50-1.70 | same |
| CloudTrail (first management copy free) and its S3 | $0.10-0.20 | same |
| DynamoDB on-demand, PITR, storage under 1 GB | ~$0.10 | $0.20-0.40 |
| Lambda: tick and sweep 86,400 invocations; broker ~8,000 a month at 2-3 GB for 10-20 s = 160,000-480,000 GB-s | $0 in the free tier | $0 in the free tier (1M requests, 400,000 GB-s); $2.70-8.00 if billed (x86 rate; Arm not rendered) |
| CloudWatch Logs, Scheduler, SNS, SSM, S3 audit writes, API Gateway, EventBridge rules | ~$0.10 | ~$0.15 |
| Cognito Essentials | $0 | $0 |
| **Infrastructure** | **about $4.60-5.30** | **about $4.85-5.65 in the free tier; up to $13.65 if Lambda is billed** |
| If Step 9 needs it: provisioned concurrency 1 on `ecf-slack-in` at 512 MB | +~$5.40 (no free tier with it) | same |
| **Bedrock, when chosen** | $0 unused | **about $415-825**; a full gate run about $20 |

Step 8 records the dev stack's real GB-s and cost.

### Moving from local mode to AWS (`ecf migrate`, OD-602, OD-614)

How each restore rule applies (OD-362 to OD-372 listed in the runbook, Step 13):

- **Before:** `ecf init --mode aws --migrating` deploys the stacks; the AWS install refuses
  everything except `migrate`, `login` and `doctor` until the migration finishes; its Cognito
  identity is `pending`.
- **Refused when busy:** as `ecf upgrade` (OD-379).
- **Cutover order:**
  1. pause every address; drain `slack_out` and `alert_outbox`;
  2. hold the local service in a new `migrating` state (it refuses checks and Slack, stays up);
  3. export the migrate kind (also carries `slack_messages` and `draft_versions`);
  4. local step-up bound to the bundle's SHA-256;
  5. uninstall the local unit (plist removed, data kept; this also cancels the dead-man message),
     so it can't return at login;
  6. `migrate run`: the operator types the backup key; `ecf-jobs` derives the public key and the
     signing seed from it, verifies the bundle, and imports it under restore's rules: same
     `install.id`, generation + 1, install name, routes, alerts, identity links, the 7-day
     restored-keyword guard; the AWS install holds every address from fetching (`migrating` hold);
  7. Cognito step-up bound to the same SHA-256; the bundle's person is merged into one person (the
     local `people.id` kept, the Cognito identity confirmed, the Slack identity kept); AWS-written
     rows are rewritten to that id in the same transaction; audit `migrate.identities_linked` with
     both ids and a Security Notice;
  8. switch the Slack manifest; edit old cards to "moved; see the new card"; re-post open approvals
     with fresh grants;
  9. resume each address only after its own mail check passes (OD-366).
- **Moved with it:** mailbox passwords (Keychain → Secrets Manager through `ecf-api`, write-only),
  the Slack bot token and signing secret. Nothing else secret crosses the network.
- **Lost, and said in the runbook:** queued jobs other than the drained ones, pending step-ups,
  `delayed` sends (back to `awaiting_approval`), login cooldowns; `sent` at `pending` with its item
  `executing` becomes `failed_unknown`, settled by the broker's `\Sent` reconciliation. Clicks fail
  visibly from step 2 to step 8.
- **Rollback (7 days, the keyword guard's length):** first the AWS install is held (maintenance
  flag, every address held, schedules disabled, mailbox secrets' access removed), and `doctor`
  checks it; then `ecf restore` into local of an AWS-signed bundle under the same backup key (same
  `install.id`, generation bumped, 7-day guard); the unit is reinstalled; Slack goes back to Socket
  Mode with a new app-level token made by hand; cards re-edited. A rollback before AWS processed
  any mail (no cursor advanced, which the `migrating` hold guarantees until step 9) follows the same
  path.
- **`migrate finalize`:** refuses while the local plist exists or the unit is loaded; removes the
  local Keychain secrets; ends the rollback window. `doctor` warns while a local unit is loadable.
- **Scheduled export** in AWS mode writes to `backups/` (above); SPEC §11.9 and `doctor` say so.

### Operations

`ecf init --mode aws [--migrating]`, `login`, `logout`, `revoke-device`, `upgrade`, `destroy`,
`doctor`, `budget set [--bedrock]`, `retention set`, `billing activate-tags`, `iam deployer-policy`,
`people relink`, `export --pull`, `pause|resume --restore-stage`, `migrate
plan|run|rollback|finalize`. CLI request timeout 30 s in AWS mode. `doctor` adds: Cognito session
and refresh expiry, a user without TOTP, the pool issuer against the authorizer, Lambda concurrency
quota (OD-610), DLQ depth and oldest-message age, SNS confirmation and subscribers, CloudTrail
active and validating, stack drift, the Lambda Python version, Bedrock retention, invocation
logging, model access and permission-set scope, the signing secret, no long-lived keys on any
profile ecf uses, a loadable local unit after migrate, `backups/` being the only copy.

### Testing (OD-597)

| What | Local (free) | Real AWS dev stack |
|---|---|---|
| Repositories, handlers, grants, transitions, export format | contract tests on SQLite and moto (CI) | — |
| Packaging, memory on 64 MB messages, Mangum, the isolate child's environment | SAM `local invoke` on Colima (cold start indicative) | memory, duration, cold start |
| HTTP routes | SAM `local start-api` (no JWT authorizer) | authorizer, issuer |
| DynamoDB (conditional writes, transactions, indexes, fencing) | DynamoDB Local (readers' expiry filter has its own unit test) | PITR, TTL, backup scratch merge |
| S3, Secrets Manager, SSM, SNS | moto | bucket policies, `logs/` delete denied, lifecycle |
| SQS FIFO order and dedupe, queue sender policies | moto (policies by policy tests) | partial-batch redelivery, group blocking, retry timing, sender denies |
| Scheduler tick and sweep | handlers called directly; tick lease; no threads | firing, overlap, late tick |
| CDK | synth, cdk-nag, snapshot, no-asset, no-VPC (CI) | — |
| IAM | policy tests and the boto-call coverage check (CI, PRs too); `cfn-policy-validator` (main) | denies actually denying; boundary teardown |
| Cognito | token-shape checks only | everything else |
| Slack | signed fake payloads; replay, bad signature, delayed consumption | install, switch both ways, real clicks, timing |
| Bedrock | stubbed client | real calls |
| Alarms, Budgets, CloudTrail rules | — | all |
| Logging | captured-log search (CI) | — |
| Telemetry relay | scripted OTLP sender, N events over 3 forced token refreshes and a 60 s `ecf-api` outage, N rows (CI) | one real session |

The dev stack runs in a separate account, a test Slack workspace and a test mailbox with synthetic
mail only, destroyed after each run with the Budget alarm on.

### Removed from the plan's M1

- Ollama, Gemma, presets A and B, local-model checks, the Ollama digest (ADR 0031).
- The remote-MCP client, `/mcp` route and login domain (M3).
- `people add`, multi-approver rules, offboarding (M2).
- PyPI publishing (OD-458). `BootstraplessSynthesizer`.
- The plan's §10 role table (replaced above) and grant HMAC key (OD-612).
- Lambda code signing (OD-606); `actions.fifo` (OD-607); `transitions.fifo` (OD-613); the
  `https://localhost` OAuth install (OD-609); `response_url` card updates; `global.` inference
  profiles (OD-604); the audit TTL and `logs/` expiry; storing the export public key in SSM and
  transferring the signing seed (OD-614).

## Steps

Each step is built on branch `m1-aws-mode` (or a sub-branch), with targeted tests, ruff and
pyright, and reviewed per `docs/agents/reviews-and-evals.md`. Steps marked **(go-ahead)** use real
services. Each step ends with its pass line.

0. **Repositories, local mode (OD-601):** the mapping table and access-pattern list first
   (reviewed on Fable before the extraction merges); a DynamoDB Local spike of items/transitions
   and audit; the repositories with contract tests. The people model (OD-579, OD-596, OD-608):
   people with UUIDs and status, identities with status and kind, the admin invariant, `people.id`
   columns and backfill, roles, approvers list, data format 5, `people relink`. Ships nothing.
   *Pass:* full suite green with SQLite behind the repositories; every repository method mapped to
   a key, GSI or transaction; a format-4 bundle imports with a synthetic inactive person; an import
   into a fresh install can still approve.
   **0b. Bedrock pre-checks (go-ahead, read-only):** `allowed_modes`, model access, Haiku 5.5 ID
   form, Region. *Pass:* every pin allows `none`, or the build stops.
1. **Mode-neutral Claude provider work** (OD-595, OD-603, OD-611): `claude.provider` with step-up;
   `provider` on `claude_calls` and `claude_sessions`; `ended_at`; `api_error` rows; the gate key
   rule (provider only for `bedrock`) and gate rows per provider; `eval compare` warning; `ecf stats`
   split and cache-read share; eval-run state behind a repository; the synthetic set packaged with
   the release; the `sonnet` grep. *Pass:* upgrading a fixture with live addresses leaves them live;
   the gate's synthetic check passes from the packaged set.
   **1b. Reference run (go-ahead):** a plan-provider C `/ecf-eval` on M1 code (local mode, same set
   version, pins, batch size and sensitivity as the latest gate run), named as Step 11's reference.
   *Pass:* run ID recorded in SPEC §16.
2. **Chat seam inbound:** `ChatInbound` with `respond() → Ack`, neutral registry, `FakeChat`
   variants. *Pass:* the suite green on Slack and on both fakes.
3. **AWS adapters:** DynamoDB repositories (transactions, the conditional helper, lease fencing, no
   `Scan`), S3, SQS, Secrets Manager, the Cognito stepper, Ed25519 grant signing; contract tests
   against moto. *Pass:* every contract test passes on both adapters; byte-identical round trip.
4. **Lambda handlers and build:** `ecf-api` (Mangum, sweep, `slack-in` consumer), the broker (facts,
   actions, deadline reserve, in-handler lease renewal, scrubbed child), `ecf-jobs` (streaming,
   resumable import, eval runs, housekeeping), `ecf-slack-in` (import-light), `ecf-slack-out`,
   `ecf-liveness`; Powertools through the no-content processor; the zip; the Python check; the
   container harness. *Pass:* SAM invokes every handler; no `tempfile`, no thread left, no
   `log_event`; no `AWS_` key in the child; log search clean.
5. **`infra/`:** stacks, the per-role table, boundary Allow list and Denies, queue sender policies,
   cfn-exec, synthesizer, cdk-nag, snapshot, policy, boto-call coverage, no-asset and no-VPC tests,
   application inference profiles, schedules, CloudTrail rules, Budgets, `cfn-policy-validator`
   through GitHub OIDC (sets up the OIDC role: go-ahead). *Pass:* CI green with every policy test.
6. **CLI for AWS mode:** `init --mode aws` (cost projection, quota check and request, credential
   check), `login` (fresh, logout redirect), `logout`, `revoke-device`, `upgrade` (backup,
   maintenance flag, lease drain, change-set guard), `destroy`, `doctor`, `budget`, `retention`,
   `billing`, `iam deployer-policy`, `export --pull`. *Pass:* targeted tests; IAM-user credential
   refused.
   **6b. Laptop relay and the AWS provider path:** the relay (OTLP, status line, MCP) with the CLI's
   refreshable token and buffering; `--provider bedrock` with Cognito step-up, the
   `GetCallerIdentity` check and settings generation with profile ARNs. *Pass:* the scripted OTLP
   test (N events across 3 refreshes and an outage, N rows); MCP calls refused without the CLI JWT.
7. **Dev stack, Cognito (go-ahead):** deploy; login with `prompt=login` and the logout redirect (a
   second login within the hour prompts; a no-prompt authorize fails); refresh through the token
   endpoint; step-up with SRP and TOTP, `ClientMetadata` on both calls, `ecf_nonce` and `ecf/stepup`
   present, `jti` consumed, a replayed token refused; `auth_time` on refresh; reused TOTP code;
   lockout; `MFA_SETUP` and `NEW_PASSWORD_REQUIRED` refused; the CLI token can't call
   `AssociateSoftwareToken`; the authorizer and both issuer forms; more than one user; teardown.
   *Pass:* every listed refusal observed.
8. **Dev stack, infrastructure (go-ahead):** the quota rule; IAM and boundary denies; queue sender
   denies; no direct invoke of `ecf-api`; `logs/` delete denied; cfn-exec trust; async settings and
   the on-failure queue; partial-batch redelivery and group blocking; schedules firing, overlap and
   a late tick; heartbeats; a real alarm and a CloudTrail rule through SNS; Budgets; drift; DNS from
   the runtime; the backup scratch merge; broker on a 64 MB message (via a fake IMAP server in the
   Lambda if the test mailbox refuses a 64 MB append) at 1, 2 and 3 GB, and a 64 MB message at the
   deadline plus 20 queued actions; `ecf-jobs` export and import at the bundle cap; Init Duration
   per function; the runtime's Python version; teardown. *Pass:* at the chosen memory, the maximum
   Max Memory Used over 3 runs under 80 %; no run killed by timeout; the dev account's total cost,
   read about 48 hours after teardown and before the Step 9 go-ahead, under US$5.
9. **Slack request URL (go-ahead):** manual install with the signing secret; Socket Mode to request
   URL and one rollback switch back; `reauthorize` keeps the request URL; real clicks; 5 forced-cold
   clicks and form buttons; the placeholder view and `views.update` timing; a non-approver clicking
   Show excerpt sees nothing; bad-signature and stale posts; replayed body; double click; delayed
   consumption (a click processed after 5 minutes in the queue); Cancel at `due_at` − 2 s with a cold
   consumer; click to card edit, cold and warm; the dead-man message at cutover; teardown.
   *Pass:* maximum `$context.responseLatency` under 2.5 s cold (else SnapStart or provisioned
   concurrency added, cost recorded, re-measured to the same line); the non-approver sees nothing;
   replay and double click each produce one decision; the late Cancel wins.
10. **Parity (go-ahead):** (a) the `null` arm in both modes on the same set version: identical
    per-case rule outcomes, triggers, facts and action plans (only run IDs and timings may differ);
    (b) a mailbox replay into fresh local and AWS installs, both in `shadow`, same mailbox and UID
    order, separate Slack apps, compared on a canonical projection with a list of allowed
    differences written before the run (IDs, timestamps, `due_at` within one tick plus 60 s, the
    generation header); a difference in a DNS-derived fact is investigated, not waived. *Pass:* (a)
    0 differences; (b) delivered count = cases, 0 unexplained differences.
11. **Bedrock (go-ahead):** the first session records the model string; a `/ecf-review` session with
    subagents and the spawn-guard hook; telemetry and status line; invocation-logging refusal; the
    full C `/ecf-eval` through Bedrock against the AWS install and the determinism check; cache
    figures under the fixed cost model; the bill. *Pass:* 0 unsafe, fraud guard N/N, non-inferior to
    the Step 1b reference at `--margin 5`; 0 requests failing after their last attempt and 0 4xx
    other than 429 (retried 5xx rate reported); determinism flips no more than the plan arm; cost per
    item within ±25 % of the projection, or the projection updated.
12. **`ecf migrate` (go-ahead):** plan, run, rollback after one tick, and finalize on the test
    install into the dev stack. *Pass:* open-approval count equal, each with exactly one fresh
    unconsumed grant and none consumed or void; cursor and gate-row counts equal; at most one new
    card per open item and every old card edited (`conversations.history` per channel); the local
    unit doesn't return after a reboot; `gate.synthetic` passes on AWS for every address that passed
    locally; rollback leaves AWS held and local restored with a bumped generation.
13. **Docs and release:** SPEC (§1.2 with the six functions, §3.2 broker and `ecf-jobs` in the TCB,
    §4, §8, §9, §10.1 signing secret and retry granularity, §10.4 MCP tokens in AWS mode, §11.9
    scheduled export and format 5, §11.10 AWS upgrades and format warnings, §12.2 AWS stated limits
    (deployer full data access, admin bypasses every rule, WAF unavailable, Cognito limits,
    `ecf-slack-in` click authority, lease stuck up to 180 s after a kill), §12.4, §13, §14
    `max_per_check` cap, §15, §17), the admin guide (AWS install, migration runbook with OD-362 to
    OD-372, break-glass, stolen laptop, cost), README, CHANGELOG; the macOS merge gate; the release
    on the checked path (OD-600, `ecf-release` skill).

## Plan review findings

**Round 1** on draft 1 (df4d331), eight Fable reviewers: 32 findings P1-P32; all recommendations
accepted; landed in draft 2. **Round 2** on draft 2 (272df2a), eight Opus reviewers (delta): 27
findings Q1-Q27 (16 high); round-1 fixes P2, P3, P11, P12, P18, P19, P20 and P23 were found
incomplete and are finished here; draft 2 had listed P12 as landed without saying where the eval
set lives (Claude's omission, fixed by OD-611). Recommendations accepted and decisions E1-E4 made
(OD-610 to OD-613; E3 as a key pair, not a KMS key) on 2026-11-04. Full text:
`state-archive/m1/review-findings.md`.

| # | Round-2 finding | Landed |
|---|---|---|
| Q1 | broker writes status and grants | Who may call and write what |
| Q2 | split `ecf-slack` | `ecf-slack-in`, `ecf-slack-out` |
| Q3 | forms before identity check | Slack, forms and windows |
| Q4 | direct invoke, queue senders, public permissions | Who may call; cfn-exec |
| Q5 | Cancel race, freshness, groups | Queues, clicks and Cancel |
| Q6 | child inherits credentials | The broker |
| Q7 | broker time budget, lease | The broker |
| Q8 | concurrency quota | OD-610 |
| Q9 | boundary Allow list | Infrastructure, boundary |
| Q10 | eval set and runs | OD-611 |
| Q11 | gate key demotes live addresses | OD-611; Step 1 |
| Q12 | migrate under restore's rules | OD-614; Moving to AWS |
| Q13 | backup swap | Data migrations, restoring a backup |
| Q14 | MCP tokens | Cognito, last item |
| Q15 | import leaves nobody to approve | People, invariant |
| Q16 | broker can mint approvals | OD-612 |
| Q17 | `ecf-jobs` | The functions |
| Q18 | approval latency | The broker |
| Q19 | identity details | People; Cognito |
| Q20 | data details | Repositories; Data and state |
| Q21 | Bedrock profile role; `ecf-slack-in` limit | Bedrock; Who may call |
| Q22 | CloudTrail rules | Infrastructure, detective controls |
| Q23 | raw clicks in SQS; presigned URLs | Queues; Privacy; `ecf-jobs` |
| Q24 | cost table | Cost |
| Q25 | step order and pass lines | Steps 0, 1, 1b, 6b, 8-12 |
| Q26 | `ChatInbound` shape | Slack, chat seam |
| Q27 | lows | throughout |

Round 1's table (P1-P32) is in draft 2 (272df2a) and the findings file. The next check (draft 3,
delta only) runs on Opus (OD-577).

## Open questions

1. The model watch's page parsing (only the Models API locally; the CI canary parses pages).
2. Region: `us-east-1` by default; decided in Step 0b.
3. How release tags drive stack upgrades when client and stack versions differ: Step 6 detail.
4. The Python upper bound (`.python-version` `<3.13`, OD-526).
5. SPEC inconsistencies (§1.1 platform wording, §1.6 ADR list): Step 13.

## Facts (fetched 2026-11-04; full table `state-archive/m1/facts.md`)

Starred facts were re-read by Claude on the source page; the rest are from research agents or
Phase R reviewers and marked (rv).

- *AWS-managed KMS keys: "you cannot use AWS managed keys in cryptographic operations directly; the
  service that creates them uses them on your behalf"; HMAC KMS keys support only `GenerateMac` and
  `VerifyMac`; customer-managed keys carry a monthly fee
  (docs.aws.amazon.com/kms/latest/developerguide/concepts.html, `hmac.html`).
- *Cognito refresh-token rotation isn't compatible with `REFRESH_TOKEN_AUTH`; use the token endpoint;
  grace period up to 60 s; `GlobalSignOut` doesn't clear the managed-login cookie
  (`amazon-cognito-user-pools-using-the-refresh-token.html`).
- (rv) Cognito (developer guide, fetched 2026-11-04): V2 pre-token trigger customizes access tokens
  on Essentials, `claimsToAddOrOverride`, `scopesToAdd`, `scopesToSuppress`; `ClientMetadata` reaches
  it from `RespondToAuthChallenge`, not `InitiateAuth`; `auth_time`, `iat`, `jti`, `sub`, `client_id`
  can't be changed (`user-pool-lambda-pre-token-generation.html`). An ID token only with `openid`;
  the token endpoint returns a refresh token for `authorization_code` without `offline_access`;
  `prompt` only in managed login, not the classic hosted UI; `prompt=login` issues a new cookie
  without invalidating the old (`authorization-endpoint.html`, `token-endpoint.html`). API sign-in
  tokens carry only `aws.cognito.signin.user.admin`; managed-login cookies let a user
  re-authenticate with no prompt (`...using-the-access-token.html`). Issuers
  `https://cognito-idp.<region>.amazonaws.com/<pool>` and
  `https://issuer-cognito-idp.<region>.amazonaws.com/<pool>` (`federation-endpoints.html`).
  `AssociateSoftwareToken` needs the admin scope and replaces the old token. After
  `AdminDeleteSoftwareToken` the next sign-in gets `MFA_SETUP`. Essentials: 10,000 MAU free;
  TOTP ±30 s; refresh 60 minutes to 3,650 days; `AuthSessionValidity` 3-15 minutes.
- (rv) API Gateway HTTP API: 30 s, 10 MB; JWT authorizer checks `aud` else `client_id`, any one
  scope, caches keys up to 2 h; throttles best-effort; AWS WAF protects REST APIs, not HTTP APIs.
- *Bedrock data retention: `none` < `default` < `aws_review`; Fable 5 and 5.1 require
  `aws_review`; set per Region by API; check `allowed_modes` with `GET /v1/models/{model}`
  (`data-retention.html`).
- (rv) Bedrock: invocation logging off by default, account-wide per Region, stores full requests
  when on; `global.` routes to any commercial Region; an inference profile in `Resource` needs the
  foundation model in each Region and supports a Deny on `aws:RequestedRegion = "unspecified"`;
  application profiles take cost tags; on-demand quotas far above a review batch.
- (rv) Prices (platform.claude.com pricing): Opus 5.5 $4 in, $5 5-minute write, $8 1-hour write,
  $0.20 read, $20 out per million; Haiku 5.5 $0.10, $0.125, $0.20, $0.01, $0.50; regional and
  multi-region endpoints 10 % over global. AWS's Bedrock table didn't render.
- (rv) Bedrock prompt caching: 4 checkpoints, 512-token minimum on 5.5, TTL 5 minutes or 1 hour,
  explicit breakpoints only; cross-Region requests write new entries.
- (rv) Claude Code on Bedrock (code.claude.com/docs/en/amazon-bedrock, `monitoring-usage`): `env`
  block, `awsAuthRefresh`, no WebSearch, caching on, `ENABLE_PROMPT_CACHING_1H`; a profile ARN needs
  `bedrock:GetInferenceProfile` and isn't a pin for the startup check; `api_request` success,
  `api_error` failure per attempt; `cost_usd` estimated; no `client_request_id` on third parties.
- *Anthropic API: `temperature`, `top_p`, `top_k` removed in 2026-10-21; Haiku 5.5 to 2027-10-07.
- (rv) Lambda: `python3.12` to 2028-10-31, images at 3.12.16; async 2 retries, up to 6 hours,
  throttled events retried up to 6 hours by default; FIFO not an on-failure destination; payload
  6 MB sync, 1 MB async; 900 s maximum; 10,240 MB maximum memory; 1,769 MB = one vCPU; freeze on
  return; reserved concurrency up to unreserved minus 100 (`configuration-concurrency.html`); new
  accounts reduced quotas; `MaximumConcurrency` 2 to 1,000 (`API_ScalingConfig.html`); SnapStart
  Python 3.12+, not with provisioned concurrency; `lambda:SourceFunctionArn` in identity policies
  only; zip 250 MB unzipped. Prices: x86 $0.0000166667 per GB-s, provisioned concurrency
  $0.0000041667 per GB-s, free tier 1M requests and 400,000 GB-s, none with provisioned
  concurrency (aws.amazon.com/lambda/pricing).
- (rv) SQS: no per-message timers on FIFO; visibility at least 6× the timeout; `maxReceiveCount` at
  least 5; a failed batch returns after the visibility timeout; Lambda's SQS trigger needs
  `GetQueueAttributes` (`services-sqs-configure.html`).
- (rv) EventBridge: Scheduler 60 s precision, 14 million a month free, retries up to 185 within a
  day; AWS management events ingested free; CloudTrail rules need an active trail
  (`eb-service-event-cloudtrail.html`).
- (rv) DynamoDB: PITR $0.20 per GB-month; on-demand $0.125 and $0.625 per million; TTL within days;
  transactions authorized per item action plus `ConditionCheckItem` (`transaction-apis-iam.html`); a
  backup restores only to a new table, with TTL, PITR, deletion protection, tags, IAM and alarms
  set again (`CreateBackup.html`); `AWS::DynamoDB::Table` supports CloudFormation resource import.
  The 100-item transaction limit and 400 KB item limit: unverified, Step 0.
- (rv) S3 $0.023 per GB-month, PUT $0.005 per 1,000; conditional writes enforceable by policy.
  Secrets Manager $0.40 per secret. CloudWatch alarm $0.10 per metric, 10 free, metric-math alarms
  billed per metric. CloudTrail extra copies $2.00 per 100,000. Budgets without actions free.
- (rv) CDK and tools: `generateBootstrapVersionRule` "generally should be left set to true, unless you
  explicitly want to be able to deploy to an unbootstrapped environment"; `BootstraplessSynthesizer`
  "does not support assets"; cdk-nag 3.0.2; `cfn-policy-validator` MIT-0; Mangum 0.22.0; Powertools
  MIT; Colima MIT; SAM non-Desktop Docker from 1.47.0.
- *LocalStack's open-source repository archived 2026-03-23; free plan non-commercial.
- (rv) GitHub OIDC `sub` forms for branch, environment and `pull_request`; `job_workflow_ref` claim.
- (rv) Slack (docs.slack.dev): signature `v0:` + timestamp + raw body, HMAC-SHA256, the 5-minute
  check a recommendation; 3 s acknowledgement; `trigger_id` 3 s single-use and required on every
  `block_actions` payload; `response_url` 5 uses in 30 minutes; `views.update` needs no `trigger_id`,
  takes `view_id` or `external_id`, `hash` optional, no time limit stated
  (`reference/methods/views.update`); retry headers Events API only; `apps.manifest.update` replaces
  the whole manifest and refuses interactivity without a request URL; HTTPS redirects, nothing on
  localhost; token rotation irreversible. Unverified: Socket Mode and request URL exclusive at any
  moment; 1 post a second per channel; an API to create app-level tokens.
- (rv) Microsoft Teams (M11, recorded only): Bot Framework JWT from `api.botframework.com`, keys
  refreshed daily, 5-minute skew; 10-15 s timeout; `Action.Execute` returns the replacement card;
  dialogs returned in the invoke response; proactive posts need stored conversation references.
