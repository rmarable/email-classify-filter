# M1 AWS mode, single user

**Status: draft 4, 2026-11-05. Not built.** M1 started 2026-11-04 (OD-592 put it next; OD-593).
Phase R: round 1 (eight Fable reviewers) on draft 1 (df4d331); rounds 2 and 3 (eight Opus
reviewers each, delta, OD-577) on drafts 2 (272df2a) and 3 (1c2e2f5). After round 3 kept finding
holes at the seams drafts 2 and 3 had added, the operator chose to simplify before the next round
(OD-615): four functions instead of six, no grant signatures, no Cancel marker. Every finding and
decision (OD-593 to OD-622) is folded in and listed under "Plan review findings". Code references
are as of `93e0fea`. Ships as one release at the end (OD-600).

This document replaces, for M1, the design plan's M1 parts
(`docs/history/design-plan-2026-09-27.md`, "the plan": Scope line 31, §2, §3a, §4, §7, §8, §9,
§10, §10a, §11, §13, §14, §15, §16, §17 M1). Where this document and the plan differ, this document
wins, and SPEC wins over both. Stale plan text is replaced with the decision that changed it cited;
nothing is dropped silently (see "Removed from the plan's M1"). The plan's §10 role table is
replaced by this document's.

Real-service tests and every AWS dev-stack run need the operator's go-ahead each time; their code
stays in the session scratchpad.

## Scope

Run ecf in the operator's own AWS account, so mail is checked, fraud is flagged and approvals
execute with every computer closed. One person (the operator) uses it, but the data, identity and
export format are built for M2's teams (OD-579).

In scope:

- **Storage behind repositories, local mode first** (OD-601). M1's largest step.
- **Infrastructure:** CDK `infra/` (Python), two stacks per install, IAM under a permissions
  boundary, CI policy checks (OD-124).
- **Services (OD-615):** Lambdas `ecf-api` (routes, decisions, the 1-minute tick, Slack posting),
  `ecf-broker` (the only holder of mailbox credentials), `ecf-jobs` (long and scheduled work),
  `ecf-slack-in` (the public Slack request URL), the Cognito trigger and the auto-delete helper;
  DynamoDB (PITR); S3; SQS FIFO; Secrets Manager; SSM; EventBridge Scheduler; CloudWatch, SNS and
  Budgets.
- **Identity:** Cognito Essentials with TOTP, CLI and step-up app clients.
- **Server-side pre-check** on the tick, with facts, triggers and approved actions in the broker.
- **Slack over a public request URL**, installed by hand (OD-609).
- **Operations:** `ecf init --mode aws`, `upgrade`, `destroy`, `doctor`, `budget set`,
  `retention set`, `billing activate-tags`, `iam deployer-policy`, `people relink`, dev stack, and
  `ecf migrate` local to AWS under restore's rules (OD-602, OD-614, OD-616, OD-621).
- **Evals in AWS mode:** `/ecf-eval` and the `null` run, the synthetic set shipped with the release
  without its adversarial subset (OD-611, OD-617, OD-618).
- **Bedrock:** `/ecf-review` and `/ecf-eval` through Amazon Bedrock instead of the operator's Claude
  plan, on demand, from `ecf claude` on the laptop (OD-578, OD-595). No Lambda calls Bedrock in M1;
  M4 adds a server-side path and a boundary change.
- **M2 foundations, run with one person** (OD-579): people, `people.id` everywhere, identities with a
  confirmation state, role checks per address, approval mode and count fields, approvers as a list,
  people and roles in export and import (data format 5, OD-608, OD-622).
- **The chat seam for M11, both directions.**

Not in scope: multi-user features (M2), remote MCP for claude.ai (M3), scheduled Claude review and
the always-on host (M4), Linux as a local-mode platform (M5), Microsoft Teams (M11), Lambda code
signing (OD-606), Slack OAuth install and token rotation (OD-609), customer-managed KMS keys,
grant signatures (OD-615), Jev, the Mistral judges and set generation in AWS mode (OD-617).

## Decisions

### Carried from the plan (SPEC OD rows)

- OD-016: `aws` mode is DynamoDB with PITR plus S3, one base and one app stack per install, a
  dedicated AWS account recommended, default encryption.
- OD-021: fetches are asynchronous. OD-022: the scheduler tick is every minute.
- OD-080, OD-081: answer proposals off per connection (M3); refresh-expiry warnings on day 25, then
  daily.
- OD-090: CloudTrail log file validation. OD-091: infrastructure budget default $20 (Bedrock has its
  own, OD-604). OD-092: secrets under default AWS-managed encryption; its grant HMAC key is not used
  in AWS mode (OD-615).
- OD-124: `infra/` is a Python CDK app. Terraform is not considered.
- OD-150: service-side step-up only.

### Since the plan

- **OD-578, OD-579:** Bedrock in M1; M2 foundations in M1.
- **OD-587 to OD-592:** M2 multi-user teams; M3 remote MCP; Microsoft Teams is M11 and needs M1's
  public HTTPS endpoint; M1 next, then M11.
- **OD-506, ADR 0031; OD-516:** no local models; preset C only.
- **OD-458:** GitHub Releases only.
- **OD-551, OD-583, OD-584:** drafts approved only when shown in full; checker and card ported.
- **OD-557:** `slack_out` retry waits 30 s, 1, 2 and 5 minutes (at least 60 s in AWS mode).
- **OD-217, OD-507:** the audit log, `item.classified` rows and fraud, weak-fraud, regulator and
  quarantined items are never pruned.
- **OD-318, OD-362 to OD-372:** restore rules; `ecf migrate` follows them.
- **OD-358:** import drops Slack routes and identity (reconciled by OD-596).

### Operator decisions 2026-11-04, planning (SPEC OD-593 to OD-600)

- **OD-593** M1 started; **OD-594** your AWS account and Bedrock as destinations, whole messages in
  Lambda memory only; **OD-595** `claude.provider` (`plan` | `bedrock`), changed with step-up;
  **OD-596** identity links carry over on migrate only; **OD-597** moto, SAM and DynamoDB Local on
  Colima, a real dev stack only where needed, no LocalStack, no tunnels; **OD-598** managed
  `python3.12` arm64 zip; **OD-599** Bedrock retention `none`, Fable unavailable, checked first;
  **OD-600** one minor release, checked path.

### After Phase R round 1 (SPEC OD-601 to OD-609)

- **OD-601** repositories first, mapping reviewed in that step; **OD-602** `ecf migrate`
  restore-shaped; **OD-603** a `live` address refuses a session whose provider has no synthetic pass,
  first Bedrock pass a full C `/ecf-eval`, `--provider bedrock` with step-up, a parity step;
  **OD-604** Bedrock cost shown, own Budget, tagged application profiles, `us.` profiles only,
  refuse while invocation logging is configured; **OD-605** short-lived AWS credentials only;
  **OD-606** no Lambda code signing; **OD-607** approved actions inside the mailbox check;
  **OD-608** data format 5 (amended by OD-622); **OD-609** Slack manual install, signing secret
  stored, no token rotation.

### After Phase R round 2 (SPEC OD-610 to OD-614)

- **OD-610** Lambda concurrency quota at least 100 plus the reserved total before the app stack
  deploys; **OD-611** the synthetic set ships in the release (amended by OD-618), eval-run state in
  DynamoDB, the gate key gains the provider only for `bedrock`, gate rows per provider; **OD-612**
  Ed25519 grant signing (superseded by OD-615); **OD-613** a schedule invokes `ecf-api`'s sweep
  (the tick joins it, OD-615); **OD-614** migrate under restore's rules (amended by OD-616 and
  OD-621).

### After Phase R round 3 (SPEC OD-615 to OD-622, operator 2026-11-04 and 2026-11-05)

- **OD-615, simplify (option B):** round 3 found 11 high findings, mostly at seams drafts 2 and 3 had
  added. Kept: the broker as the only holder of mailbox credentials; a small public Slack handler
  that reads no data and decides nothing; the migrate holds and restore rules. Changed:
  - `ecf-slack-out` and `ecf-liveness` merge into `ecf-api`: one internal function posts to Slack and
    runs the 1-minute tick and the due-work sweep from one schedule.
  - Grants are not signed. Supersedes OD-612. A key pair couldn't stop a compromised broker, which
    holds the mailbox credentials and can send without any grant. Single use is enforced by an
    append-only spend record written in the same transaction as the consume. The stated limit:
    only `ecf-api` creates approvals (code-enforced and audited); a compromised broker has full
    mailbox authority.
  - No Cancel marker. A Cancel is a queued click like any other, never refused as stale, and wins
    whenever it is processed before the broker consumes the grant; the residual window is a stated
    limit (Queues, Cancel).
- **OD-616, the backup root key never leaves the Mac (G1):** `ecf migrate` and `ecf init --mode aws`
  derive everything from the typed (or newly generated) backup key on the laptop and verify bundles
  there; AWS receives only the export signing seed and the public age recipient, written once,
  write-only, through `ecf-api` into Secrets Manager, and the public verify key as a deploy-set
  parameter. The running AWS install can sign bundles but never read them, as v1. Amends OD-614.
- **OD-617, eval tools in AWS mode (G2):** AWS mode supports `/ecf-eval` and the `null` run only. Jev,
  the Mistral judges and set generation stay local-mode tools; no TypeSafe or Mistral key goes to
  AWS. Revisit trigger: Mistral Large 4 or Jev on Bedrock with retention `none` (Open questions).
- **OD-618, the adversarial subset stays private (G2):** the repository is public, and
  `.claude/rules/eval-synthetic.md` puts the adversarial subset in a gitignored `eval/private/`, which
  doesn't exist yet. The subset moves there (its own task, before Step 1), is left out of the release
  zip, and is uploaded by the operator to an AWS install by its hash, so the gate still sees the
  whole set. Earlier versions remain in git history. Amends OD-611.
- **OD-619, Bedrock gate on the operator's install (G3):** the dev stack's Bedrock pass is destroyed
  with it, so after the release the operator's first Bedrock use needs one full C `/ecf-eval` through
  Bedrock on their own install (about US$20, again after a pin change). Gate rows for provider
  `bedrock` don't migrate. The Bedrock projection is now about US$415-825 a month (the `us.`
  premium), replacing OD-604's US$380-750.
- **OD-620, bundles read whole (G4):** a bundle carries one Ed25519 signature over the whole file and
  pyrage decrypts whole buffers, so bundles are read in memory; `ecf-jobs`' memory and an AWS-mode
  bundle cap are set from Step 8's measurement. Format 5 keeps format 4's signature scheme.
- **OD-621, migrate keeps fraud checking until AWS has the data (G5):** the local service is
  uninstalled only after AWS has verified the import; `ecf migrate abort` restores the local service
  from its kept data at any point before that; Slack is told "fraud flagging off since T" for the
  gap between the local hold and the first AWS check, and the gap is a stated limit in SPEC §12.2.
  The first AWS check covers mail from the gap (the cursor carries over).
- **OD-622, format-4 bundles get a synthetic person:** on import into a different install a format-4
  bundle gets one synthetic inactive person carrying `source_install`; the operator is filled in only
  on a restore or migrate of the same install. Amends OD-608's "the one person filled in".

## Design

### Architecture

```
Laptop (operator)                    AWS (base stack + app stack)                       External
─────────────────                    ────────────────────────────                       ────────
ecf CLI ─────────────JWT───────────► API Gateway HTTP API ─► ecf-api ─┬─► DynamoDB / S3 / SSM
ecf claude (Claude Code, on demand)                                   ├─► jobs.fifo ─► ecf-jobs
  ├ ecf-mcp ─0600 socket─► relay ─JWT + session token─► same API      ├─► invoke ─► ecf-broker ─► IMAP/SMTP
  └ OTLP ─loopback─► relay                                            └─► slack-out.fifo ─► ecf-api (sender) ─► Slack
  Claude: Anthropic (plan) or Bedrock us. profile
                                     Slack ─signed─► API GW ─► ecf-slack-in ─► slack-in.fifo ─► ecf-api
                                     Scheduler ─► ecf-api (tick and sweep, every minute)
                                     Scheduler ─► ecf-jobs (retention, digests, summary, model watch, export)
                                     CloudWatch alarms, CloudTrail rules ─► SNS ─► admin email
Cognito (Essentials, TOTP): app clients CLI (PKCE loopback) · step-up (SRP only)
```

### Who may call, read and write what

A Lambda learns nothing about who invoked it, and inside one account an identity policy alone is
enough to use a queue or read a secret. So every boundary is stated on both sides:

- **Invocation:** `ecf-api` only by API Gateway (`SourceArn`), its own event source mappings
  (`slack-in.fifo`, `slack-out.fifo`) and its tick schedule's role; `ecf-jobs` only by its
  `jobs.fifo` mapping and its schedules' roles; `ecf-slack-in` only by API Gateway; the broker only
  by `ecf-api`. Event source mappings poll with the function's own role (no resource permission);
  each Scheduler role may invoke only its one target, trusts `scheduler.amazonaws.com` with
  `aws:SourceAccount` and the schedule's `aws:SourceArn`, and the tick handler accepts only the fixed
  `Input`. The only `lambda:Principal` ever allowed is `apigateway.amazonaws.com`. CI tests all of it.
- **Queues:** each queue policy denies `SendMessage` unless `aws:PrincipalArn` is a named sender, and
  `ReceiveMessage`, `DeleteMessage` and `ChangeMessageVisibility` unless it is the consumer (plus
  admin tooling on DLQs):

  | Queue | Senders | Consumer |
  |---|---|---|
  | `slack-in.fifo` (+ DLQ) | `ecf-slack-in` | `ecf-api` |
  | `slack-out.fifo` (+ DLQ) | `ecf-api`, `ecf-broker`, `ecf-jobs` | `ecf-api` |
  | `jobs.fifo` (+ DLQ) | `ecf-api` | `ecf-jobs` |
  | broker on-failure (standard) | `ecf-broker` (Lambda writes it with the function role) | admin |
  | Scheduler DLQs (standard) | each schedule's role | admin |

  Redrive is an admin action. Alarms on `DestinationDeliveryFailures` and `DeadLetterErrors`; Step 8
  checks each failure path lands a message.
- **Secrets:** role policies name exact ARNs. Deploy-time secrets (Slack signing secret and bot
  token, the export seed) carry a resource policy denying `GetSecretValue` unless
  `aws:PrincipalArn` is an owning role. Mailbox secrets, created at runtime, are protected by an
  explicit Deny on `ecf/<install>/mailbox/*` in every role except the broker's read and `ecf-api`'s
  write-only `CreateSecret`/`PutSecretValue`. CI checks only the broker can read a mailbox ARN.
- **Bearer:** `ecf-api` validates the `Authorization` bearer itself and never reads
  `requestContext.authorizer` (a forged-context test). A request carrying a profile or agent token is
  treated as that profile, never as the CLI, even if it also carries the CLI JWT.
- **Writers:** `ecf-api` writes everything except the broker's closed list, made through the same
  conditional-update helper (the OD-181 rule becomes "only the helper writes `status`", with a
  test). The broker's list, mapped worker by worker from v1's in-lease work (`checks.IN_LEASE`;
  a test fails when a worker isn't mapped):
  - pre-check: `new → awaiting_claude` and the decision, escalation and audit rows; the no-grant
    label and flag (below);
  - `close_gone`: open → `resolved_by_mailbox`, voiding non-approved grants;
  - execution: grant consume (with its spend record), `executing → executed | failed |
    failed_unknown`; v1's retry (`consumed → approved` below 3 attempts) becomes a new attempt on the
    spend record;
  - send settle: `failed_unknown → executed | failed`;
  - Undo: `executed → undoing → undone | undo_failed`, against an `undo` grant `ecf-api` created;
  - queued draft delete and flag, against a `draft_delete` or `draft_flag` grant `ecf-api` created.
- **Approvals (OD-615):** only `ecf-api` creates grants (code-enforced; the broker's helper can't
  insert one; every grant creation is audited with the approving person). The broker executes a grant
  only in `approved`, matching the action and content hashes, with no spend record (`GRANTUSE#<id>`,
  created with `attribute_not_exists` in the consume transaction, carrying attempts and outcome; no
  runtime role may delete or overwrite it). Holds are as v1: a due time on the item, the grant
  unchanged.
- **No-grant pre-check actions:** the broker may label and flag without a grant only for a decision
  `decide(facts)` recomputed in the same invocation (never from a stored proposal), and only under
  v1's four gates: nothing in shadow, quarantined mail not re-read, the content hash verified before
  writing, OD-439's keyword-stored skip. Contract tests per gate.
- **Stated limits (SPEC §12.2):** a compromised broker has full mailbox authority; a compromised
  `ecf-slack-in` holds the signing secret and can forge clicks, which `ecf-api` still role-checks,
  so it can trigger what one click by the operator could (one-click actions, never step-up ones).

| Role | Invoked by | Reads | Writes | Secrets and keys |
|---|---|---|---|---|
| `ecf-api` | API Gateway; `slack-in.fifo`, `slack-out.fifo`; tick schedule | all tables; S3 config | main; `ecf-api` table; excerpts `expires_at` only (`dynamodb:Attributes`); Slack table (none); S3 `logs/` (create-only), `jobs/` presigned URLs; sends `slack-out.fifo`, `jobs.fifo`; invokes broker | Slack signing secret, bot token; mailbox secrets write-only; export seed write-only (once); the export verify key (parameter); Cognito `AdminUserGlobalSignOut` |
| `ecf-broker` | `ecf-api` (async) | main; `ecf-api` table (grants, settings) | main (closed list, `LEASE#`, `CURSOR#`, `GRANTUSE#` create-only); grants (closed list); excerpts; sends `slack-out.fifo` | mailbox secrets (read, `lambda:SourceFunctionArn` condition) |
| `ecf-jobs` | `jobs.fifo`; housekeeping schedules | all tables (incl. `Scan`); S3 | main and `ecf-api` table (import, restore, migrate, merges); S3 `exports/`, `jobs/`, `backups/`, `eval/`; backup and scratch tables; sends `slack-out.fifo` | export seed and age recipient |
| `ecf-slack-in` | API Gateway | none | Slack table (dedupe keys); sends `slack-in.fifo` | Slack signing secret; bot token (`views.open` only) |

### The functions

- **`ecf-api`:**
  - routes; rules and policy on stored facts; transitions; grant creation; config, stage,
    sensitivity, pause, outbound; people, identities, sender confirmations; role checks;
  - consumes `slack-in.fifo` (clicks) and `slack-out.fifo` (paced posts, `chat.update`,
    `views.update`, ephemerals, the dead-man `chat.scheduleMessage`);
  - **the tick** (one schedule, every minute): a lease keyed on the scheduled time (`TICK#<time>`;
    the Scheduler attribute confirmed in Step 8), event age 60 s, each sweep time-boxed; due checks
    (async invoke of the broker, with a conditional `dispatched_until` so a pending invoke isn't
    repeated); approval expiry, `delayed → executing` (which makes that address's check due now),
    stale marking; "Needs you", reminders and refresh warnings, each re-reading status inside the
    winning conditional write before sending; the audit flush (its own lease); re-queueing jobs with
    an unfinished progress item;
  - routes that can't finish inside 30 s and 10 MB are jobs; `/v1/checks` becomes "enqueued; poll
    `ecf status`".
- **`ecf-broker`:** invoked per address by `ecf-api`. Each run:
  - takes the address lease (`LEASE#<address>`) and renews it **before each blocking step** to now +
    max(180 s, that step's bound + 30 s), where a message's bound is fetch time at the measured rate
    plus the isolate limit (`isolate.py:50-57`, 30 s + 3 s/MB) and an action's bound is its own
    deadline; every transaction carries a fencing `ConditionCheck` on the lease;
  - checks the maintenance flag and the `migrating` hold at start;
  - when started by an approval, runs due actions before the fetch page;
  - fetches with a deadline of min(start + `max_per_check`, remaining time − a reserve for the worst
    parsing child, the write-back and one action);
  - computes facts, triggers and the pre-check decision in memory (the whole message never leaves the
    broker) and writes them with escalation and audit rows in one transaction;
  - executes due approved actions with the open mailbox, each under its own wall-clock deadline
    checked between IMAP and SMTP commands, stopping when less than one action deadline remains;
  - backfill pages run here (they need IMAP).

  The isolate child runs with a scrubbed environment (no `AWS_*`, minimal `PATH` and `PYTHONPATH`)
  and no AWS client, returning new DNS answers to the parent (`isolate.py:96-99` passes no `env`
  today; a test asserts no `AWS_` key reaches it). Memory at least 2,048 MB, the smallest of 1, 2, 3
  GB meeting Step 8's line; timeout = `max_per_check` + reserve, at most 900 s, with `max_per_check`
  capped in AWS mode so it fits. v1's scheduling rules are ported (`next_due_at`, a due time asked
  during a running check kept, `schedule.py:197-199`; busy-lease retry 60 s). Async:
  `MaximumRetryAttempts` 0, event age 300 s, on-failure queue alarmed. The broker joins the safety
  TCB in SPEC §3.2.
- **`ecf-jobs`:** import, export, restore and backup merges, `rules test`, `ecf eval run` (the `null`
  arm, OD-617) and `/ecf-eval` preparation, and housekeeping on schedules (retention and its
  re-stamp, digests, daily summary, weekly model watch, scheduled export). Every job stops at its
  deadline minus a reserve, saves its progress item and returns success; the tick re-queues
  unfinished jobs, so no job relies on SQS redelivery. Bundles are read whole in memory (OD-620),
  older bundles migrated in an in-memory SQLite database with every migration in the zip; presigned
  URLs are short-lived and size-capped (presigned POST `content-length-range`). `jobs.fifo` group =
  job kind; import, restore and export are mutually exclusive by a lease; an alarm on the oldest
  message's age. Part of the trust root with `ecf-api` (SPEC §3.2).
- **`ecf-slack-in`** (public, import-light, an import-linter contract): signature over the raw
  body; timestamp within 5 minutes and not in the future; `api_app_id` and `team_id`; dedupe by
  conditional put; for a form or window button, `views.open` with a fixed "Checking…" view (no inputs,
  no submit, no data); then the raw click (with the `view_id`, or a note that `views.open` failed)
  to `slack-in.fifo` (group = Slack user ID; deduplication ID = the dedupe key); 200. It reads no
  table and never says the action was taken.
- **Maintenance and holds:** every writer honours the maintenance flag. `ecf upgrade` disables the
  `slack-in.fifo` event source mapping for the maintenance window (deployer credentials) and
  re-enables it after, so clicks wait in the queue without spending receives.
- **Laptop:** the CLI and Claude Code. `ecf claude` runs a relay: `ecf-mcp` and the status line
  reach it over a 0600 Unix socket (as v1's socket); only OTLP uses loopback TCP. The relay requires
  the session's profile or agent token on every request, checks `Host` and refuses any request with
  an `Origin` header, forwards a fixed allow-list (MCP, telemetry and status routes), and adds the
  CLI's JWT. The relay and the CLI share one refresher: the refresh token is read from the Keychain
  under a lock and re-read before refreshing (rotation invalidates the loser's copy). Deployer and
  admin credentials are short-lived (OD-605); the Bedrock profile is checked to be the Bedrock
  permission set.

### Repositories and the DynamoDB mapping (OD-601)

- **Step 0** extracts repositories per aggregate (items and transitions, grants and spend records,
  settings, people and identities, senders and threads, audit, alerts, Claude telemetry, eval runs and
  results, Slack routes and messages, DNS cache, jobs and leases), SQLite first, contract tests on
  each adapter.
- **Mapping first:** the mapping table and access-pattern list are written and reviewed before the
  extraction merges; every repository method maps to a `GetItem`, a `Query` on a key or GSI, or a
  `TransactWriteItems` row (a Step 0 pass item); `Scan` only in `ecf-jobs`. A DynamoDB Local spike of
  items/transitions and audit. The mapping also lists every v1 in-lease worker and its transitions.
- **Mapping table:** one row per table (migrations 0001 to 0043): store, keys and GSIs, writer role,
  TTL rule, exported, atomic group, backup-merge rule. Tables draft 1 left homeless are listed
  explicitly (`addresses`, `alerts`, `alert_outbox`, `check_state`, `claims`, `claim_batches`,
  `claude_calls`, `claude_sessions`, `slack_calls`, `model_calls`, `escalations`, `delays`,
  `draft_versions`, `eval_runs`, `eval_results`, `slack_messages`, `slack_dedupe`, `processing`,
  `dns_cache`, `downloads`, `schema_migrations`, install settings keys).
- **Atomicity:** each v1 write transaction is one `TransactWriteItems`: a transition with `status =
  :expected AND version = :v` plus audit; approval = transition + grant + audit; pre-check = decision
  + escalation + audit; consume = grant + spend record + item; a terminal transition voids grants,
  deletes delays and sets children's expiry; every broker transaction carries the lease fencing
  check; config apply + stage demotion. Counters use `ADD` under the condition.
- **Storage versions:** `storage_version` per item (`schema_version` already means the classification
  schema); tolerant readers; `UpdateItem` on named attributes only; one-release lag for new
  attributes (a contract test); AWS exports stamp the release's SQLite schema number and upgrade
  items on read. The resumable-import concept is a **data epoch**, never `install.generation`.
- **Settings across stores** by a fixed key list; OD-368's "this computer's state" keys listed for AWS.
- **Round trip:** a fixture at mixed storage versions exported by both adapters gives byte-identical
  `tables/*.jsonl`; local → AWS → local equal modulo `origin` and new IDs; the
  copied-or-handled-apart test runs on DynamoDB too.

### People, identity and roles (OD-579)

- **Model:** `people {id (random UUID), identities, roles, admin, status: active | inactive,
  source_install}`. Invariant: the admin holds the approver role on every address (on address add and
  on import). Only an admin changes a person's status, with step-up and a Security Notice.
- **Identities:** `{issuer, subject, kind: cognito | slack | os_user, status: pending | confirmed,
  confirmed_at, confirmed_by}`; Cognito issuer `cognito:<pool id>`; Slack issuer with the team (and
  enterprise) ID. A Slack member is `pending` until its DM Confirm click. **Authority:** a confirmed
  identity of an active person holding the role; anything else is refused and audited.
- **First person:** `ecf init` creates the operator's person (UUID, admin, active); the Cognito
  identity is confirmed at the first `ecf login` after TOTP enrolment, for the one username init
  created.
- **Re-link (`ecf people relink`):** for a changed Cognito identity (new pool, recreated user) or OS
  identity (new Mac). It is on an allow-list open to an unlinked or pending identity. Authority is the
  backup key: the laptop derives it from the typed key and signs a relink challenge with the export
  seed; `ecf-api` verifies against the install's export verify key (a deploy-set parameter no runtime
  role can change); a Cognito step-up by the new identity, bound to the relink hash, proves it holds
  the new TOTP. Audited, with a Security Notice. Locally, a same-install restore under the typed key
  re-binds the OS-user identity to the account the service runs as.
- **`people.id`** on every grant, approval, answer, audit row and security notice; backfill rules as
  draft 2 (operator for OS-user and `slack:*`, null for `service` and `migration`); rows written
  before a migrate merge carry `people.id` null plus the Cognito `sub`, and
  `migrate.identities_linked` records the mapping readers resolve through (nothing is rewritten).
- **Per address:** approval mode `single`, approver count 1, `stepup.totp` `off` (M2's mode).
- **Export format 5:** people, roles and identities are bundle files; format 3 bundles no longer
  readable, and a 3.2.x release can't read format 5 (CHANGELOG, §11.10).
- **Import (OD-596, OD-622):** the bundle's people arrive `inactive` with `source_install` on a
  different install; a format-4 bundle gets a synthetic inactive person; the operator is filled in only
  on a same-install restore or migrate; `--replace` never removes the target's active admin.

### Cognito

- **Pool:** Essentials; `AllowAdminCreateUserOnly`; TOTP required; SMS MFA off; device remembering off;
  deletion protection; prevent user-existence errors; case-insensitive usernames; group `admins`;
  resource server `ecf` (`observe`, `work`, `stepup`); managed login version 2 (snapshot test); issuer
  type set explicitly; pool and clients under the change-set guard.
- **CLI client:** PKCE (S256) on fixed registered loopback ports for callback and sign-out;
  `AllowedOAuthScopes` `ecf/observe ecf/work` (no `openid`, so no ID token; no admin scope); no
  `ExplicitAuthFlows` for SRP or password; attribute read/write empty; access token 15 minutes; refresh
  30 days with rotation through the token endpoint; tokens in the OS secret store. A snapshot test
  asserts no client allows `ecf/stepup`.
- **Login always fresh:** `prompt=login`; the callback answers with a 302 to the Logout endpoint; the
  CLI warns if the sign-out page isn't reached. `logout` and `revoke-device` revoke and sign out.
- **Step-up client:** `ALLOW_USER_SRP_AUTH` only; the nonce in `ClientMetadata` on both
  `RespondToAuthChallenge` calls; the V2 trigger (step-up client, `TokenGeneration_Authentication`
  only) copies it into `ecf_nonce` and adds `ecf/stepup`, and suppresses the admin scope everywhere.
  Proof in the request body. `ecf-api` requires `client_id`, `token_use=access`, `ecf/stepup` (refused
  elsewhere), `ecf_nonce` = the nonce, `auth_time` and `iat` within 5 minutes, `sub` = a confirmed
  identity of the active approving person, and consumes nonce and `jti` by conditional put. **Migrate
  purpose `migrate_link`:** while the install is `migrating`, a `pending` Cognito identity may step up
  for this purpose only, the nonce bound to the bundle SHA-256 and that `sub`. `MFA_SETUP`,
  `SOFTWARE_TOKEN_SETUP` and `NEW_PASSWORD_REQUIRED` are refusals. Verify limit via `RATE#`. New
  step-up purposes for SPEC §9.6: `migrate`, `migrate_link`, `relink`.
- **Password change:** managed login's forgot-password (email verified at init) or break-glass.
- **Trigger:** no DynamoDB or network access; reserved concurrency 1 and an alarm.
- **Runtime rights:** only `ecf-api`'s `AdminUserGlobalSignOut`; `AdminCreateUser` once from `init`.
- **Token validation:** PyJWT, JWKS refetch at most once a minute; issuer exactly
  `https://cognito-idp.<region>.amazonaws.com/<pool>` or `https://issuer-cognito-idp...`; `doctor`
  compares the pool's issuer with the authorizer's (two-issuer support checked in Step 7).
- **Break-glass:** `AdminDeleteSoftwareToken`, `AdminSetUserPassword` (temporary),
  `AdminUserGlobalSignOut`; logged and emailed; `doctor` and a custom metric flag a user without
  TOTP.
- **Stated limits:** password and TOTP typed into the `ecf` process; the CLI's text is all the person
  sees; a stolen refresh token is 30 days of non-step-up access until revoked. Runbook "stolen
  laptop".
- **MCP tokens:** Claude Code holds no Cognito token; profile and agent tokens are bound to the
  session with a TTL; no unauthenticated OBSERVE; SPEC §10.4 updated in Step 13.

### Data and state

- **DynamoDB, four tables:** main; `ecf-api` table (grants, settings, people, identities, sender
  confirmations); excerpts; the Slack table (dedupe keys, TTL 1 day, written only by `ecf-slack-in`,
  no PITR). PITR and deletion protection on the first three.
- **Expiry:** no TTL at creation; the terminal transition sets `expires_at` = terminal time + item
  retention on the item and its excerpts when prunable (never fraud, weak-fraud, regulator,
  quarantined); item retention stays its own step-up setting, re-stamped by an `ecf-jobs` job on
  change; CloudWatch log retention separate; `AUDIT#` TTL only after a confirmed S3 flush; readers and
  the exporter filter expired items.
- **S3:** one bucket; Block Public Access; `BucketOwnerEnforced`; versioning; SSE-S3;
  `ExpectedBucketOwner`; TLS-only. `logs/` create-only, no expiry, `s3:DeleteObject*` denied to every
  runtime role: the tamper-evident audit copy. `exports/`, `jobs/` expire in 1 day; every versioned
  prefix has a noncurrent-version expiration rule; `backups/` pruned to `export_keep` in code, never
  pruning an object younger than the longest presigned URL; `eval/` holds result files and the
  uploaded adversarial subset (OD-618), by hash.
- **Crash safety:** `processing <stable_id>` counted across invocations; quarantine after 2 crashes; a
  lapsed lease with a second holder doesn't count an attempt (a test).
- **No temporary files** on the Lambda code path (a test).

### Data migrations, upgrades and backups

- **Expand and contract:** additive attributes, lazy upgrade by `storage_version`, idempotent backfill
  with a progress item; a full rewrite needs a planned outage.
- **Before the change set:** `ecf upgrade` takes on-demand backups of all three PITR tables under one
  maintenance hold, waits for leases to drain (OD-379), disables the `slack-in` mapping.
- **`ecf upgrade --to <previous>`:** code only; data forward-compatible.
- **Restoring:** never by swapping tables. `ecf-jobs` restores all three tables to scratch tables
  (from the upgrade backups, or by PITR with one `RestoreDateTime` so they share a point), then merges
  into the live tables by `downgrade._merge`'s rules (`sent`, `threads`, `senders`, `gate` kept from
  live; grants voided; executing items to `failed_unknown`; cursors rewound), per-table rules from the
  mapping; then deletes the scratch tables. Restores are asynchronous; the job resumes by polling
  `DescribeTable`. Rights: `CreateBackup`, `DescribeBackup`, `ListBackups`, `DeleteBackup`,
  `RestoreTableFromBackup` (from `table/<live>/backup/*`), `RestoreTableToPointInTime`, and full item
  rights on `ecf-<install>-scratch-*` only.

### Queues, timers and delays

- **SQS FIFO:** `slack-in.fifo` (group = Slack user ID; `maxReceiveCount` 3), `slack-out.fifo`
  (group = channel ID; `views.update` and ephemerals in group `view:<Slack user ID>`; the dead-man
  message its own group), `jobs.fifo` (group = job kind). DLQs alarmed; visibility at least 6× the
  consumer timeout; batch 1-2; `MaximumConcurrency` 2 per mapping. `slack-in.fifo`, `slack-out.fifo`
  and their DLQs keep messages 1 day, SSE on; DLQ tools never print bodies.
- **Waits:** every scheduled wait is a `due_at` acted on by the tick. `slack-out.fifo` retries by
  `ChangeMessageVisibility` only.
- **Clicks:** `ecf-api` verifies the HMAC and judges the 5-minute window against SQS
  `SentTimestamp`. A **risk-raising** click (Approve, Resume, a send form) older than 10 minutes by
  `SentTimestamp` when processed is refused, audited, and the person gets "click again"; risk-lowering
  clicks (Cancel, Reject, Pause, Dismiss) are processed whenever they arrive, auditing the original
  time. The dedupe key stops replays; each grant's conditional update stops double clicks.
- **Forms and windows:** `ecf-api` checks identity and role, audits, then sends `views.update` with the
  real view, or "not allowed"; every refusal or failure path (stale, error, DLQ handler) sends a final
  "expired, click again"; a click whose `views.open` failed gets an ephemeral instead; an update after
  the user closed the view counts as done.
- **Cancel (OD-615):** a Cancel moves `delayed → cancelled`, or `executing → cancelled` while the
  grant has no spend record, conditionally; the broker's consume requires the item still `executing`
  with no spend record. So a Cancel processed before the broker consumes always wins. **Stated
  limit:** a delayed send is executed no earlier than 60 s after its `due_at`, but a Cancel clicked
  near the end of the delay can still lose if click processing is held up (a backlog behind a failing
  click in the same person's queue, at most 3 receives); the person is then told "already sent".
- **Holds:** a paused address or tripped send limit holds an action without counting an attempt.
- **EventBridge Scheduler:** the tick on `ecf-api`; housekeeping schedules on `ecf-jobs`;
  `MaximumRetryAttempts` 0 with standard SQS DLQs; a heartbeat alarm when the tick runs fewer than once
  in 5 minutes.

### Slack over a request URL (OD-609)

- **Install** by hand; the signing secret pasted from Basic Information (hidden prompt) at `init`, at
  `migrate run` and by `set-tokens` (step-up, Security Notice); `doctor` checks it. No token rotation.
  `apps.manifest.update` uses a fresh configuration token the operator pastes at the switch.
- **Signature and replay:** HMAC-SHA256 over `v0:<timestamp>:<raw body>`; dedupe key = SHA-256 of
  timestamp + raw body, conditional put first, TTL 1 day.
- **Public route:** route throttle (burst 20, rate 10 a second); signature before any parse; body cap,
  413; 401 with no body; signing secret re-read at most once per container per 5 minutes and only for
  a fresh timestamp; access logs `$context.*` only; alarm on signature failures. No WAF on HTTP APIs
  (SPEC §12.2).
- **3-second budget:** `views.open` happens before the 200 and counts in it. Reserved concurrency on
  `ecf-slack-in` = route rate × p99 duration measured in Step 9 (2 until then); above the latency line,
  SnapStart or provisioned concurrency 1.
- **Switch on migration:** AWS deployed and answering first; the local service held; mode-aware
  `manifest()`; the app-level token revoked. Clicks fail visibly in Slack from the local hold until the
  switch (stated in the runbook). Rollback to Socket Mode needs a new app-level token made by hand on
  Basic Information (no API found: unverified).
- **Chat seam for M11:** `ChatInbound` with `verify`, `parse` and `respond(Verified) → Ack`, `Ack` =
  `Empty | OpenPlaceholder | OpenForm(Form) | InterimCard(Card)`, chosen by capability: Slack uses
  `OpenPlaceholder` and fills the form by update; an adapter with `form_in_ack` (Teams, whose dialog
  must be in the `task/fetch` invoke response) needs a synchronous, role-checked "build form" route
  from its public function to `ecf-api`, a new route recorded for M11. Decisions stay asynchronous; the
  final card comes by update. Neutral handler registry; opaque `RouteRef.channel`; `FakeChat`
  variants with every capability off and with `form_in_ack`; routes `/chat/<surface>`.

### Bedrock and the Claude provider

- **Reaching Bedrock:** `ecf claude` writes `CLAUDE_CODE_USE_BEDROCK=1`, `AWS_PROFILE`, `AWS_REGION`
  and the application profile ARNs into its dedicated config's `settings.json` `env` block; the
  `sonnet` alias handled in Step 1; `--provider bedrock` refuses unless `GetCallerIdentity` is the
  generated Bedrock permission set; `doctor` fails on any non-Bedrock action in it.
- **Model IDs:** one tagged application profile per pin from the `us.` system profile; never
  `global.`; permissions generated from the pins (profiles, the foundation model per Region,
  `bedrock:InferenceProfileArn`, `GetInferenceProfile`, the retention read), optional Deny on
  `aws:RequestedRegion = "unspecified"`. The model string for a profile ARN is recorded in Step 11's
  first session before the model check relies on it.
- **Checked first (Step 0b):** `allowed_modes`, model access, the Haiku 5.5 ID form, Region.
- **Retention `none`** set by admin credentials; `doctor` reads it back; whether it holds across the
  `us.` profile's Regions and with the 1-hour cache: Steps 0b and 11.
- **Invocation logging:** refused while configured; a CloudTrail rule alerts on
  `PutModelInvocationLoggingConfiguration`.
- **Gate (OD-603, OD-611, OD-619):** each item records the key of the session that classified it,
  provider included; `gate.compute`, `gate.stored` and the tick's stage check take the provider
  (`gate.pair_key` gains it, only for `bedrock`); the tick never demotes on a provider switch, the
  session-start refusal replaces it; `/ecf-eval` keys a run by its own provider. Tests: upgrading keeps
  live addresses live; switching to `bedrock` and back keeps them live and Bedrock-session reviews count
  only in the `bedrock` row. The operator's install gets its own Bedrock pass after the release
  (OD-619).
- **First Bedrock pass (dev stack, Step 11):** a full C `/ecf-eval` through Bedrock against the AWS
  install, compared with Step 10(c)'s plan-provider run on the same install under the one rule
  (`--margin 5`): 0 unsafe, fraud guard N/N, `compare` showing no input difference except "different
  provider"; the 10-case determinism repeat on named case IDs, passing when it flips at most one more
  field than 10(c)'s repeat.
- **Cost (OD-604, OD-619):** about US$0.042-0.050 an item at global list prices, about 10 % more
  through `us.` (unverified on Bedrock), cache hits possibly lower: about US$415-825 a month at
  300-500 emails a day; a full gate run about US$20. `init` and `--provider bedrock` print it; the
  Bedrock Budget (US$100 default, `ecf budget set --bedrock`) alarms by tag.
- **Telemetry:** the relay forwards OTLP and status-line batches with the CLI's refreshable token,
  buffering up to 10 minutes; model-check windows in DynamoDB; `api_error` rows kept; provider from
  the session; Bedrock `cost_usd` shown as "API-equivalent".
- **What differs on Bedrock:** no WebSearch, no `/logout`, no plan rate limits; subagents and the
  spawn-guard hook tested in Step 11 (if either fails, Bedrock is blocked).
- **Weekly model watch** (an `ecf-jobs` schedule): each pin's profile offered, retirement date,
  `allowed_modes` still has `none`.

### Evals in AWS mode (OD-611, OD-617, OD-618)

- **The set:** the release zip carries the built synthetic set without its adversarial subset; the
  operator uploads that subset to the install's S3 `eval/` by hash. The set version is
  `labels.set_version` (`labels.jsonl` plus `fixed_labels.jsonl`, the shared function,
  `ecf/eval/labels.py:164-175`); a test asserts packaged + uploaded = checkout. The release build fails
  unless every `labels.jsonl` row's file (the `.build/` cases included) is present with a matching
  sha256; the gate's synthetic check refuses when the confirmed or fraud-guard case count is below the
  set's.
- **Runs:** run state, claims and outcomes in DynamoDB; `ecf eval run --backend null` and `/ecf-eval`
  preparation are chunked `ecf-jobs` work; result files go to S3 `eval/` and `ecf eval pull <run>`
  brings one to the laptop for `compare`, rescore and `--source-run` (result files are in §12.4's and
  retention's lists).
- **Not in AWS mode (OD-617):** Jev, the Mistral judges, set generation; no TypeSafe or Mistral key in
  AWS. A newly confirmed label counts for the AWS gate after the next release (or a re-upload of the
  private subset).

### Prompt caching (operator request 2026-11-04)

- Claude Code builds requests and places breakpoints on both providers; ecf controls what it returns
  and the order of its agent instructions (stable first, the message last).
- OD-594's "no email content in any cache" covers caches ecf operates; the provider's prompt cache is
  part of the Anthropic or Bedrock destination; in a batched agent earlier items are in the prefix by
  design (SPEC §5.6).
- Measured: cache-read share per spawn by batch size and provider; `claude_calls.ended_at`.
- TTL 5 minutes; the cost model is fixed before Step 11 and applied to Step 10(c)'s request gaps by
  spawn; `ENABLE_PROMPT_CACHING_1H` only if cheaper.

### Caches in the AWS design (operator request 2026-11-04)

Rule: no email content in any cache ecf operates (OD-594).

| Cache | Lives in | Lifetime | If stale |
|---|---|---|---|
| Cognito JWKS | Lambda memory | container life; refetch on unknown `kid`, at most once a minute | fails closed until refetch |
| DNS for DKIM/DMARC | DynamoDB with `expires_at`; `BatchGetItem` per page; an in-invocation memo for the isolate child, whose answers the parent writes | 10-minute cap | seen up to 10 min late, as v1 |
| Slack DM channel IDs | DynamoDB | until Slack refuses | re-opened |
| Effective schema, rules, policy | Lambda memory by config version | until the version changes | version checked per invocation |
| Gate fingerprint | DynamoDB per address and provider | until inputs change | recomputed once |
| Synthetic set version | code package + uploaded subset hash | the release / the upload | none |
| Secrets | Lambda memory | container life; re-read on `AUTHENTICATIONFAILED`, signing secret at most once per 5 minutes | fails once, then reloads |
| Bedrock and Anthropic prompt caches | the provider | 5 min or 1 h | none |

### Privacy (OD-594, OD-599, OD-604)

SPEC §12.4 AWS mode:

- **Your AWS account** (the install's Region): full messages only in the broker's memory while
  processing; DynamoDB stores what SQLite stores locally; S3 holds audit logs (no content), config,
  encrypted export bundles and eval result files and the uploaded private subset (synthetic text);
  SQS carries IDs except `slack-in.fifo` (the raw Slack click) and `slack-out.fifo` (outgoing cards and
  views, which can include subjects, senders, excerpts and draft text), each kept at most 1 day;
  Secrets Manager holds the credentials and the export signing seed. PITR keeps deleted items up to
  35 days; S3 noncurrent versions expire by rule.
- **Amazon Bedrock** (a session whose provider is `bedrock`): what Anthropic receives during
  `/ecf-review` and `/ecf-eval`, through a `us.` profile (possibly another US Region); retention
  `none`; refused while invocation logging is configured.
- **Logs:** a top-level catch in every handler through the no-content processor; Powertools on the same
  processor, `log_event` forbidden by a test; the processor also drops `response_url`, `trigger_id`,
  `authorization`, `x-slack-signature`, `code`, `refresh_token`, `id_token`, `access_token`,
  `X-Amz-Signature`, `X-Amz-Credential`; a captured-log test.
- Slack, Anthropic and the mail provider are unchanged; no other destination without an OD.

### Infrastructure, IAM and deploy

- **Synthesizer:** `DefaultStackSynthesizer(generate_bootstrap_version_rule=False)`, `versionReporting`
  off, `aws-cdk-lib` pinned, template snapshot, no-asset test; asset-creating constructs avoided;
  explicit log groups; inline auto-delete helper.
- **cdk-nag 3** (pinned), every suppression with a reason. **No VPC** (CDK assertion).
- **Two-phase deploy:** base stack; zip verified against the SHA-256 in the client wheel; app stack.
  No code signing (OD-606).
- **Credentials (OD-605):** `init`, `upgrade`, `destroy`, `budget set`, `retention set`, `billing
  activate-tags`, `migrate`, `doctor`'s AWS checks and break-glass refuse `:user/` and `:root`.
- **Concurrency (OD-610):** quota at least 100 + reserved total (`ecf-slack-in` 2 until measured, the
  trigger 1); `init` requests the increase and waits; `doctor` checks.
- **Boundary:** an exact Allow list for runtime roles, with a CI check that every AWS call the code
  makes (collected from the moto tests) is allowed by both the role (per the table above) and the
  boundary:
  - DynamoDB `GetItem`, `PutItem`, `UpdateItem`, `DeleteItem`, `ConditionCheckItem`, `Query` (tables
    and `index/*`), `BatchGetItem`, `BatchWriteItem`; `Scan` and the backup and restore actions for
    `ecf-jobs` only (scratch tables scoped by name);
  - S3 `GetObject`, `PutObject`, `ListBucket` (prefix-conditioned); the helper's version actions;
  - SQS `SendMessage`, `ReceiveMessage`, `DeleteMessage`, `ChangeMessageVisibility`,
    `GetQueueAttributes`;
  - Secrets Manager `GetSecretValue`, `PutSecretValue`, `CreateSecret`, `TagResource` on exact
    prefixes; SSM `GetParameter`, `PutParameter` on `/ecf/<install>/runtime/*` only (deploy-set
    parameters, such as the export verify key, are under another path);
  - KMS `GenerateDataKey*`, `Decrypt` on `aws/sns`; SNS `Publish`; CloudWatch Logs writes;
  - Lambda `InvokeFunction` on the broker (for `ecf-api`); Cognito `AdminUserGlobalSignOut`; Bedrock
    `GetInferenceProfile`, `GetFoundationModel` and the retention read (for the model-watch job);
  - explicit Deny on control-plane actions (Lambda code, configuration, permissions, mappings,
    function URLs, concurrency; `sqs:SetQueueAttributes`; secret resource policies;
    `dynamodb:UpdateContinuousBackups`, `DeleteTable` except scratch; `logs:DeleteLogGroup`;
    `cognito-idp:UpdateUserPool*`; `bedrock:PutAccountDataRetention`; `scheduler:*Schedule`;
    `iam:PassRole`). The boundary applies to runtime and Scheduler roles, not to cfn-exec.
- **cfn-exec:** under `/ecf-deploy/<install>/`; `iam:PassRole` only to `lambda.amazonaws.com` and
  `scheduler.amazonaws.com` for `role/ecf/<install>/*`; trust with `aws:SourceAccount` and `aws:SourceArn`
  `ArnLike stack/ecf-<install>-app/*` (confirmed in Step 8); denied user, key, login-profile and policy
  creation, function URLs with auth `NONE`, and `lambda:AddPermission` unless `lambda:Principal` is
  `apigateway.amazonaws.com`. CI fails on any Lambda permission without `SourceArn` and any resource
  policy with `Principal "*"` and no condition.
- **CI:** GitHub OIDC trust on `aud`, `sub = repo:<owner>/<repo>:ref:refs/heads/main` and the
  validator workflow's `job_workflow_ref`; `cfn-policy-validator` on pushes to `main`; static policy
  tests on pull requests; dedicated empty account.
- **Detective controls:** EventBridge rules on CloudTrail management events, through an input
  transformer (metadata only), to the unencrypted `-direct` topic (CloudWatch and EventBridge can't
  publish to an `aws/sns`-encrypted topic; its policy admits `events.amazonaws.com` with the rules'
  `SourceArn`). Events: Lambda code, configuration, permissions, function URLs, event source mappings
  (create, update, delete) and concurrency (put, delete); IAM changes under `/ecf/`, `CreateUser`,
  `CreateAccessKey`, `UpdateAssumeRolePolicy`; Secrets Manager under `ecf/<install>/` including
  resource policies; `cognito-idp:Admin*`; DynamoDB `UpdateContinuousBackups`, `DeleteTable`,
  `UpdateTable`; CloudTrail `StopLogging`, `DeleteTrail`, `UpdateTrail`; S3 bucket policy, lifecycle,
  public-access block and versioning changes; SQS `SetQueueAttributes`, `AddPermission`,
  `DeleteQueue`; SNS `Subscribe`, `Unsubscribe`, `SetTopicAttributes`, `DeleteTopic`; Scheduler
  `Create/Update/DeleteSchedule`; EventBridge `DisableRule`, `DeleteRule`, `PutTargets`,
  `RemoveTargets`; Bedrock `PutModelInvocationLoggingConfiguration`,
  `Put/DeleteAccountDataRetention`. IAM events in us-east-1 only (unverified). `doctor` checks the
  trail is active.
- **SNS, alarms, teardown, tags, CloudTrail validation:** as the plan's §10; the email topic on
  `aws/sns` with its key ARN resolved at `init`.
- **Budgets:** infrastructure $20 (OD-091), Bedrock US$100 (OD-604).

### Cost (one user, us-east-1; prices fetched 2026-11-04 (rv); restores the plan's §15)

Alarms (about 22 metrics, 10 free): `ecf-api`, `ecf-broker`, `ecf-jobs`, `ecf-slack-in` errors
(metric math, 2 each) and throttles (1 each); 3 FIFO DLQs; the broker on-failure queue; Scheduler
DLQs; the tick heartbeat; `jobs.fifo` oldest age; the Cognito trigger; Slack signature failures; a
user without TOTP. Secrets: 3 mailboxes, Slack bot token, Slack signing secret, export seed, and the
models key if the model watch uses one (6-7).

| Item | Idle | 300-500 emails a day, 3 addresses |
|---|---|---|
| Secrets Manager, 6-7 at $0.40 | $2.40-2.80 | same |
| CloudWatch alarms, ~22 metrics (10 free, $0.10 each) | ~$1.20 | same |
| CloudTrail (first management copy free) and its S3 | $0.10-0.20 | same |
| DynamoDB on-demand, PITR, under 1 GB | ~$0.10 | $0.20-0.40 |
| SQS: idle long polling by 3 mappings with `MaximumConcurrency` (which disables Lambda's poller reduction), est. 1-2M requests a month, 1M free; FIFO rate not rendered | $0-0.50 (unverified; Step 8 measures) | same |
| Lambda: tick 43,200 invocations; broker ~8,000 a month at 2-3 GB for 10-20 s = 160,000-480,000 GB-s | $0 in the free tier | $0 to about $0.25 over the free tier at the top; $2.70-8.00 if billed (x86 rate; Arm not rendered) |
| CloudWatch Logs, Scheduler, SNS, SSM, S3 audit writes, API Gateway, EventBridge rules | ~$0.10 | ~$0.15 |
| Cognito Essentials | $0 | $0 |
| **Infrastructure** | **about $3.90-4.90** | **about $4.05-5.50 in the free tier; up to about $13.25 if Lambda is billed** |
| If Step 9 needs it: provisioned concurrency 1 on `ecf-slack-in` at 512 MB | +~$5.40 (no free tier with it) | same |
| **Bedrock, when chosen** | $0 unused | **about $415-825**; a full gate run about $20 |

Step 8 runs the stack idle for 24 h with schedules on and records requests, GB-s and cost.

### Moving from local mode to AWS (`ecf migrate`, OD-602, OD-614, OD-616, OD-621)

- **Before:** `ecf init --mode aws --migrating` deploys the stacks; the operator types the backup key on
  the laptop, which derives the seed, the age recipient and the verify key (OD-616); `ecf-api` stores
  the seed and recipient write-only and the verify key is deployed as a parameter; the operator
  confirms the OD-341 fingerprint. The AWS install allows only `migrate`, `login`, `doctor` and the
  migrating-only `set-tokens` (signing secret, configuration token) until the migration finishes; its
  Cognito identity is `pending`. Refused when busy, as `ecf upgrade` (OD-379).
- **The local `migrating` hold** (new, built in Step 6c): a persisted service state that survives a
  restart and is cleared only by `migrate abort`, `migrate finalize` or a restore; it refuses mail
  checks and Slack and allows export, step-up, the Keychain read for the transfer, and restore. Its
  Security Notices are queued and posted from AWS after the switch (or, on abort, locally).
- **Cutover order:**
  1. pause every address; drain `slack_out` and `alert_outbox`; post "fraud flagging off since T" to
     Slack;
  2. put the local service in the `migrating` hold;
  3. export the migrate kind (a new bundle kind; it also carries `slack_messages` and
     `draft_versions`), encrypted to a one-time recipient `ecf-jobs` publishes for this job;
  4. local step-up bound to the bundle's SHA-256; the laptop verifies the bundle under the typed key;
  5. the held local service transfers the mailbox passwords and the Slack bot token to `ecf-api`
     (write-only), using the CLI's Cognito token;
  6. `migrate run`: `ecf-jobs` decrypts with the one-time identity (then deleted), checks the bundle
     against the stored verify key and imports it under restore's rules: same `install.id`,
     generation + 1, install name, routes, alerts, identity links, the 7-day restored-keyword guard;
     the AWS `migrating` hold keeps every address from fetching;
  7. `migrate_link` step-up by the pending Cognito identity, bound to the same SHA-256: the bundle's
     person and the Cognito identity become one person (local `people.id` kept, the Cognito identity
     confirmed, the Slack identity kept); `migrate.identities_linked` and a Security Notice;
  8. **only now** the local unit is uninstalled (plist removed, data kept), which also cancels the
     dead-man message (OD-621);
  9. switch the Slack manifest; edit old cards to "moved"; re-post open approvals with fresh grants;
     lift the AWS hold;
  10. resume each address only after its own mail check passes (OD-366); the first AWS check covers
      mail from the gap.
- **`ecf migrate abort`:** at any point before step 8, clears the AWS install (maintenance, every
  address held, mailbox secrets' access removed), clears the local hold and restarts local checking.
- **Rollback (7 days from step 6's `restore.at`; refused after):** the AWS install is held first
  (maintenance, every address held, schedules disabled, mailbox access removed), and an `ecf-jobs`
  export of the rollback kind (exempt from the maintenance flag: it reads data and bumps only
  `export.seq`) is pulled to the laptop; the local unit is reinstalled and comes up in the `migrating`
  hold; `ecf restore` of that bundle under the backup key (same `install.id`, generation max + 1, the
  7-day guard); the hold is released; Slack goes back to Socket Mode with a new app-level token made by
  hand; cards re-edited. Only the rollback-kind bundle is accepted.
- **Re-migrating after a rollback:** `ecf destroy`, then `init --migrating` and the steps above;
  generations keep rising (N, N+1, N+2, N+3).
- **`migrate finalize`:** refuses while the local plist exists or the unit is loaded; a CLI-driven
  call to the kept local service (started held) removes its Keychain secrets; ends the rollback
  window. After day 7, `doctor` escalates an unfinalized migration to Operator Input Needed.
- **Lost, and said in the runbook:** queued jobs other than the drained ones, pending step-ups,
  `delayed` sends (back to `awaiting_approval`), login cooldowns; `sent` at `pending` with `executing`
  becomes `failed_unknown`, settled by `\Sent` reconciliation. Fraud flagging is off from step 2 to
  the first AWS check (stated limit, OD-621). Clicks fail visibly from step 2 to step 9.
- **Scheduled export** writes to `backups/`; `ecf export --pull` copies the newest (or a named)
  `backups/` object to the laptop's `export_dir` without bumping `export.seq`, where the local prune
  applies; SPEC §11.9 and `doctor` say so.

### Operations

`ecf init --mode aws [--migrating]` (generates or takes the backup key on the laptop, OD-616),
`login`, `logout`, `revoke-device`, `upgrade`, `destroy`, `doctor`, `budget set [--bedrock]`,
`retention set`, `billing activate-tags`, `iam deployer-policy`, `people relink`, `export --pull`,
`eval pull`, `pause|resume --restore-stage`, `migrate plan|run|abort|rollback|finalize`. CLI request
timeout 30 s in AWS mode. `doctor` adds: Cognito session and refresh expiry, a user without TOTP, the
pool issuer against the authorizer, the concurrency quota, DLQ depth and oldest age, SNS, CloudTrail
active and validating, drift, the Lambda Python version, Bedrock retention, invocation logging, model
access and permission-set scope, the signing secret, no long-lived keys, a loadable local unit after
migrate, an unfinalized migration, `backups/` being the only copy.

### Testing (OD-597)

| What | Local (free) | Real AWS dev stack |
|---|---|---|
| Repositories, handlers, grants and spend records, transitions, export format | contract tests on SQLite and moto (CI) | — |
| Packaging, memory on 64 MB messages, Mangum, the isolate child's environment | SAM `local invoke` on Colima (cold start indicative) | memory, duration, cold start, lease bounds |
| HTTP routes | SAM `local start-api` | authorizer, issuer |
| DynamoDB (conditions, transactions, indexes, fencing, spend records) | DynamoDB Local | PITR, TTL, scratch-table merge |
| S3, Secrets Manager, SSM, SNS | moto | bucket and secret policies, `logs/` delete denied, lifecycle |
| SQS order, dedupe, sender and receiver policies | moto; policy tests | redelivery, group blocking, retry timing, denies, failure paths |
| Tick | handler called directly; lease; no threads | firing, overlap, late tick |
| CDK, IAM | synth, cdk-nag, snapshots, no-asset, no-VPC, policy tests, boto-call coverage (CI, PRs too); `cfn-policy-validator` (main) | denies, boundary teardown |
| Cognito | token-shape checks only | everything else |
| Slack | signed fake payloads; replay, bad signature, delayed consumption | install, switch both ways, real clicks, timing |
| Bedrock | stubbed client | real calls |
| Alarms, Budgets, CloudTrail rules | — | all |
| Logging | captured-log search (CI) | — |
| Relay | scripted OTLP sender: N events over 3 forced refreshes and a 60 s outage, N rows; CLI refresh during a relay session (CI) | one real session |

The dev stack runs in a separate account, a test Slack workspace and a test mailbox with synthetic
mail only, destroyed after each run with the Budget alarm on.

### Removed from the plan's M1

- Ollama, Gemma, presets A and B, local-model checks, the Ollama digest (ADR 0031).
- The remote-MCP client, `/mcp` route and login domain (M3). `people add`, multi-approver rules,
  offboarding (M2). PyPI publishing (OD-458). `BootstraplessSynthesizer`.
- The plan's §10 role table and grant HMAC key; Lambda code signing (OD-606); `actions.fifo`
  (OD-607); `transitions.fifo` (OD-613); the `https://localhost` OAuth install (OD-609);
  `response_url`; `global.` profiles (OD-604); the audit TTL and `logs/` expiry.
- From drafts 2 and 3: `ecf-slack-out` and `ecf-liveness` as functions, grant signatures (OD-612) and
  the Cancel marker (OD-615); deriving keys in AWS (OD-616); Jev, Mistral and generation in AWS
  (OD-617); streaming bundles (OD-620).

## Steps

Each step is built on branch `m1-aws-mode` (or a sub-branch), with targeted tests, ruff and pyright,
and reviewed per `docs/agents/reviews-and-evals.md`. Steps marked **(go-ahead)** use real services.
Each step ends with its pass line.

Before Step 1, separately: **the adversarial subset moves to `eval/private/`** (OD-618), its own
small task with its own review.

0. **Repositories, local mode (OD-601):** the mapping table, access-pattern list and in-lease worker
   map first (reviewed on Fable before the extraction merges); a DynamoDB Local spike; repositories
   with contract tests; the grant spend record; the people model (UUIDs, status, identities, the admin
   invariant, `people.id` and backfill, roles, approvers, data format 5 with the synthetic person,
   `people relink` locally). Ships nothing. *Pass:* full suite green with SQLite behind the
   repositories; every repository method and every in-lease transition mapped; a format-4 bundle
   imports with a synthetic inactive person; an import into a fresh install can still approve.
   **0b. Bedrock pre-checks (go-ahead, read-only):** `allowed_modes`, model access, Haiku 5.5 ID form,
   Region. *Pass:* every pin allows `none`, or the build stops.
1. **Mode-neutral Claude provider and eval work:** `claude.provider` with step-up; `provider` and
   `ended_at` on telemetry; `api_error` rows; items keyed by their session's provider; the gate key
   rule and gate rows per provider; `eval compare` warning; `ecf stats` split and cache-read share;
   eval-run state and result files behind repositories; the packaged set with the release-build
   sha256 check; the `sonnet` grep. *Pass:* upgrading leaves live addresses live; a switch to
   `bedrock` and back leaves them live with Bedrock reviews only in the `bedrock` row; packaged +
   private = checkout set version, with equal confirmed and fraud-guard counts.
   **1b. Reference run (go-ahead):** a plan-provider C `/ecf-eval` on M1 code in local mode, with the
   10-case determinism repeat on named case IDs; the set version frozen from here to Step 11 (a
   change re-runs 1b). *Pass:* run ID recorded in SPEC §16.
2. **Chat seam inbound:** `ChatInbound`, `respond() → Ack`, registry, `FakeChat` variants. *Pass:*
   suite green on Slack and on both fakes.
3. **AWS adapters:** DynamoDB repositories (transactions, the helper, fencing, spend records, `Scan`
   only in `ecf-jobs`), S3, SQS, Secrets Manager, the Cognito stepper; contract tests against moto.
   *Pass:* every contract test on both adapters; byte-identical round trip.
4. **Lambda handlers and build:** `ecf-api` (Mangum, the tick, both queue consumers), the broker
   (facts, the full in-lease worker list, per-step lease renewal, per-action deadlines, scrubbed
   child), `ecf-jobs` (deadline-and-save, in-memory bundles, eval chunks, housekeeping),
   `ecf-slack-in`; Powertools through the processor; the zip; the Python check; the container
   harness. *Pass:* SAM invokes every handler; no `tempfile`, no thread left, no `log_event`; no `AWS_`
   key in the child; log search clean; a lapsed lease with a second holder doesn't count an attempt.
5. **`infra/`:** stacks, the role table, boundary, queue and secret policies, Scheduler roles,
   cfn-exec, synthesizer, cdk-nag, snapshots, policy and boto-call coverage tests, application
   profiles, schedules, CloudTrail rules, Budgets, `cfn-policy-validator` through GitHub OIDC (sets up
   the OIDC role: go-ahead). *Pass:* CI green with every policy test.
6. **CLI for AWS mode:** `init --mode aws` (backup key on the laptop, cost projection, quota,
   credentials), `login`, `logout`, `revoke-device`, `upgrade` (backups, maintenance, lease drain,
   mapping disable, guard), `destroy`, `doctor`, `budget`, `retention`, `billing`, `iam
   deployer-policy`, `export --pull`, `eval pull`, `people relink` in AWS. *Pass:* targeted tests;
   IAM-user credential refused; no key material in any job record, queue message or log (a test).
   **6b. Laptop relay and the AWS provider path:** the relay (Unix socket for MCP and status, TCP for
   OTLP, allow-list, session token required), the shared refresher, `--provider bedrock` with Cognito
   step-up, the `GetCallerIdentity` check, settings generation. *Pass:* the scripted OTLP test; a
   profile token on a CLI route refused; a CLI JWT without a profile token refused on MCP routes; a
   request without the session token refused by the relay; a CLI refresh mid-session keeps the relay
   working.
   **6c. Migrate machinery:** the local `migrating` hold, the migrate and rollback bundle kinds, the
   one-time recipient, `migrate plan|run|abort|rollback|finalize`, `migrate_link`, the
   migrating-only allow-list on AWS. *Pass:* the hold survives a restart, refuses checks and Slack,
   allows export and step-up; abort restores local checking; no root key in any AWS request (a test).
7. **Dev stack, Cognito (go-ahead):** deploy; fresh login and logout redirect (a second login within
   the hour prompts; a no-prompt authorize fails); refresh; step-up with `ClientMetadata` on both
   calls, `ecf_nonce`, `ecf/stepup`, `jti` consumed, replay refused; `migrate_link` only while
   migrating; `auth_time` on refresh; reused TOTP; lockout; `MFA_SETUP` and `NEW_PASSWORD_REQUIRED`
   refused; no `AssociateSoftwareToken` from the CLI token; the authorizer and both issuers; more than
   one user; relink; teardown. *Pass:* every listed refusal observed.
8. **Dev stack, infrastructure (go-ahead):** the quota rule; IAM, boundary, queue and secret denies; no
   direct invoke of `ecf-api`; `logs/` delete denied; cfn-exec trust; Scheduler invocation type and
   attribute; each failure path landing a message; redelivery and group blocking; the tick firing,
   overlap and a late tick; a real alarm and a CloudTrail rule through SNS; Budgets; drift; DNS from
   the runtime; the three-table scratch merge by backup and by PITR; the broker on a 64 MB message
   (a fake IMAP server in the Lambda if the test mailbox refuses the append) at 1, 2 and 3 GB, a 64 MB
   message at the deadline plus 20 queued actions, and the lease never expiring while held; `ecf-jobs`
   import at the cap and a full `null` eval run across at least 2 invocations; Init Duration per
   function; the runtime's Python version; a 24 h idle run; teardown. *Pass:* chosen memory = the
   smallest meeting max Max Memory Used under 80 % over 3 runs, and the GB-s row recomputed with it;
   no run killed by timeout; no lease lapse; the idle run's extrapolated monthly cost within ±25 % of
   the infrastructure line (else the table is updated before Step 13); the deploy-to-teardown cost of
   Steps 7-8, by day from the dev account, read about 48 hours later and before the Step 9 go-ahead,
   under US$5.
9. **Slack request URL (go-ahead):** manual install with the signing secret; Socket Mode to request
   URL and back; `reauthorize` keeps the URL; real clicks; 5 forced-cold clicks and form buttons; the
   placeholder and `views.update`; a non-approver's Show excerpt; bad-signature and stale posts;
   replayed body; double click; a click processed after 5 minutes in the queue (accepted); a Cancel
   at `due_at` − 2 s with a cold consumer, and a Cancel after the tick moved the item to `executing`;
   a non-approver's Cancel; an update while a post in the same channel is retrying; click to card
   edit; the dead-man message at cutover; teardown. *Pass:* max `$context.responseLatency` under 2.5 s
   cold and 0 `expired_trigger_id`; the real form within 5 s p95 cold; the non-approver sees nothing
   and can't cancel; replay and double click each one decision; both Cancels win; reserved concurrency
   sized from the measured p99 (cost recorded if provisioned concurrency is added).
10. **Parity (go-ahead):** (a) the `null` arm in both modes on the same set version: identical rule
    outcomes, triggers, facts and action plans; (b) a mailbox replay into fresh local and AWS installs
    in `shadow` with pre-written allowed differences (IDs, timestamps, `due_at` within one tick plus
    60 s, the generation header), DNS-fact differences investigated; (c) a plan-provider C
    `/ecf-eval` against the AWS install with the determinism repeat. *Pass:* (a) 0 differences; (b)
    delivered count = cases, 0 unexplained differences; (c) non-inferior to 1b at `--margin 5` with
    per-case Claude-input digests equal across modes; 10(c) becomes Step 11's reference.
11. **Bedrock (go-ahead):** the first session records the model string; a `/ecf-review` session with
    subagents and the spawn-guard hook; telemetry and status line; invocation-logging refusal; the full
    C `/ecf-eval` through Bedrock and the determinism repeat; cache figures under the fixed model; the
    bill. *Pass:* 0 unsafe, fraud guard N/N, non-inferior to 10(c) at `--margin 5`, `compare` shows
    only "different provider"; 0 requests failing after their last attempt and 0 4xx other than 429
    (retried 5xx reported); determinism flips at most one more than 10(c); per-item cost by profile tag
    over the run window, read about 48 hours later, within ±25 % of the projection recomputed from the
    run's own token counts (outside: a recorded miss, and §16.3 and `init`'s projection updated in
    Step 13).
12. **`ecf migrate` (go-ahead):** plan, run, abort at step 6, run again, rollback after one tick,
    re-migrate, finalize. *Pass:* open-approval count equal, each with exactly one fresh grant with no
    spend record; cursor and gate-row counts equal; at most one new card per open item, old cards
    edited (`conversations.history`); the local unit doesn't return after a reboot; `gate.synthetic`
    passes on AWS for every address that passed locally; abort restores local checking; rollback leaves
    AWS held and local restored with a bumped generation; the fraud-off notice posted and the gap's mail
    pre-checked on the first AWS check.
13. **Docs and release:** SPEC (§1.2, §3.2 broker and `ecf-jobs` in the TCB, §4, §6.4 grants and spend
    records, §8, §9, §9.6 new step-up purposes, §10.1 signing secret and retry granularity, §10.4 MCP
    tokens, §11.9 scheduled export, format 5 and bundle kinds, §11.10 AWS upgrades and format warnings,
    §12.2 AWS stated limits (deployer full data access, admin bypasses every rule, WAF unavailable,
    Cognito limits, a compromised broker has full mailbox authority, a compromised `ecf-slack-in` can
    forge one-click actions, the Cancel window, fraud flagging off during migrate, lease stuck up to
    its bound after a kill), §12.4, §13, §14 `max_per_check` cap, §15, §16.3 Bedrock figures, §17), the
    admin guide (AWS install, migration runbook with OD-362 to OD-372, abort and rollback, break-glass,
    stolen laptop, cost), README, CHANGELOG; the macOS merge gate; the release on the checked path
    (OD-600, `ecf-release` skill). After the release, the operator's own Bedrock gate run (OD-619).

## Plan review findings

**Round 1** (draft 1, df4d331, eight Fable reviewers): 32 findings P1-P32, all recommendations
accepted. **Round 2** (draft 2, 272df2a, eight Opus reviewers, delta): 27 findings Q1-Q27; decisions
OD-610 to OD-614. **Round 3** (draft 3, 1c2e2f5, eight Opus reviewers, delta): 23 findings R1-R23 (11
high), mostly at seams drafts 2 and 3 had added (grant signatures, the Cancel marker, the relay, the
migrate holds), and some from Claude misreading v1 (the broker's in-lease writes). Applying the
operator's rule that a review loop that keeps finding holes means questioning the approach, the
operator chose to simplify (OD-615) and decided G1-G5 (OD-616 to OD-621); OD-622 records a SPEC
contradiction the round found. Full text: `state-archive/m1/review-findings.md`.

| # | Round-3 finding | Landed |
|---|---|---|
| R1 | backup root key into AWS | OD-616; Moving to AWS; Step 6c |
| R2 | signature replay | OD-615 (no signatures; spend record) |
| R3 | broker writer list | Who may call, writers; Step 0 |
| R4 | Cancel marker | OD-615; Queues, Cancel |
| R5 | relay | The functions, laptop; Step 6b |
| R6 | identity bootstrap, relink, signing secret | People; Cognito; Slack, install |
| R7 | lease and action bounds | The broker; Step 8 |
| R8 | long jobs, streaming | `ecf-jobs`; OD-620 |
| R9 | secret and queue policies | Who may call, read and write what; Infrastructure |
| R10 | gate key under a switch, set version, result files | Bedrock, gate; Evals in AWS mode |
| R11 | migrate holds, fraud gap | OD-621; Moving to AWS; Step 6c |
| R12 | three-table restore, PITR | Data migrations |
| R13 | re-migrate, data epoch, window, `--pull`, notices | Moving to AWS; Repositories |
| R14 | SPEC rows | OD-622, OD-619; SPEC §1.2 row |
| R15 | Slack queue and view details | Queues; Slack (notices disappear with `ecf-slack-out`) |
| R16 | tick and sweep | `ecf-api` tick (one schedule) |
| R17 | no-grant pre-check gates | Who may call, no-grant actions |
| R18 | cost | Cost |
| R19 | eval keys | OD-617 |
| R20 | Bedrock gate on the real install | OD-619 |
| R21 | pass lines | Steps 1b, 8-11 |
| R22 | public repository | OD-618 |
| R23 | detective events | Infrastructure |

Rounds 1 and 2's tables are in drafts 2 and 3 and the findings file. Draft 4 gets a full Opus review
of the whole document (not a delta, since its shape changed).

## Open questions

1. The model watch's page parsing (only the Models API locally; the CI canary parses pages).
2. Region: `us-east-1` by default; decided in Step 0b.
3. How release tags drive stack upgrades when client and stack versions differ: Step 6 detail.
4. The Python upper bound (`.python-version` `<3.13`, OD-526).
5. SPEC inconsistencies (§1.1 platform wording, §1.6 ADR list): Step 13.

Revisit triggers (operator, 2026-11-04):

- If Mistral Large 4 (ecf's pinned draft judge, OD-548) or TypeSafe Jev becomes available on Amazon
  Bedrock with data retention `none`, consider moving that eval tool to Bedrock (an OD, since OD-594
  covers Bedrock only for what Anthropic receives; and a judge re-measurement if the Mistral model
  changes). Checked 2026-11-04 (docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html):
  Bedrock lists Mistral Large 3 but not Large 4 or Medium 3.5, and no TypeSafe model (Bedrock
  Marketplace not checked). Until then Jev, the Mistral judges and set generation stay local-mode
  tools (OD-617).

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
- (rv, round 3, fetched 2026-11-04) Bedrock model cards list Mistral Large, Large 3, Small,
  Magistral Small, Ministral, Pixtral Large, Devstral 2 and others, not Mistral Large 4 or Medium 3.5,
  and no TypeSafe model (docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html, re-read by
  Claude). Lambda writes async on-failure records with the function's role, to standard queues only,
  else drops them (`invocation-async-retain-records.html`); Scheduler DLQs are standard queues written
  by the schedule's role (`configuring-schedule-dlq.html`); event source mappings poll with the
  function's role and records carry `SenderId` (`with-sqs.html`); `MaximumConcurrency` disables
  Lambda's poller reduction (`services-sqs-scaling.html`); `RestoreTableFromBackup` needs read and
  write on the target including `Scan` and `BatchWriteItem` (`backuprestore_IAM.html`);
  `RestoreTableToPointInTime` restores to a new table at any second in the window
  (`API_RestoreTableToPointInTime.html`); CloudWatch and EventBridge can't publish to an
  `aws/sns`-encrypted topic (`sns-key-management.html`); Teams dialogs are returned in the
  `task/fetch` invoke response (learn.microsoft.com, task-modules-bots, updated 2026-10-13).
- *ecf's backup key: one 32-byte root derives both the age identity that decrypts bundles and the
  Ed25519 signing seed; "the running install keeps only the seed ... so it can sign bundles but never
  read them" (`src/ecf_server/backup_key.py:1-6`, `:62-72`, at 93e0fea).
