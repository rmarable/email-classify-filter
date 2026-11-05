# M1 AWS mode, single user

**Status: draft 10, 2026-11-05. Not built.** M1 started 2026-11-04 (OD-592 put it next; OD-593).
Phase R: round 1 (eight Fable reviewers) on draft 1 (df4d331); rounds 2 and 3 (eight Opus
reviewers each, delta, OD-577) on drafts 2 (272df2a) and 3 (1c2e2f5). After round 3 kept finding
holes at the seams drafts 2 and 3 had added, the operator chose to simplify before the next round
(OD-615): four functions instead of six, no grant signatures, no Cancel marker. Round 4 (eight Opus
reviewers, full review of draft 4, 9642729) led to OD-623 to OD-625; round 5 (eight Opus reviewers,
delta, draft 5, 49cd6f1) to OD-640 to OD-642; round 6 (eight Opus reviewers, delta, draft 6, 7b4e5e4)
to OD-644 to OD-646; round 7 (eight Opus reviewers, delta, draft 7, b01ceee) to OD-649 to OD-653;
round 8 (eight Opus reviewers, delta, draft 8, 264ef2b) to OD-655 to OD-657; round 9 (eight Opus
reviewers, delta, draft 9, d6c48a4) to OD-658 to OD-660.
Every finding and decision (OD-593 to OD-660) is folded in and listed under "Plan review findings". Code references
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
- **Services (OD-615, OD-625):** Lambdas `ecf-api` (routes) and `ecf-worker` (the 1-minute tick, both
  Slack queues), one code package with two roles (OD-641),
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

### After Phase R round 4 (SPEC OD-623 to OD-625, operator 2026-11-05)

- **OD-623, a click-in-flight guard (K1), superseded by OD-640:** `ecf-slack-in` raises a per-item in-flight flag in its own
  table for any click naming an item, before it answers Slack; `ecf-worker` clears it when it has
  processed the click, whatever the outcome; the broker's consume transaction requires the flag down.
  The flag carries no authority (a non-approver's click delays a send only while it is processed); a
  flag older than 5 minutes is ignored by the broker and alarmed. Risk-lowering clicks (Cancel, Reject,
  Pause, Dismiss and the others in SPEC §10.1's table) use their own FIFO group per item, so they never
  wait behind other clicks. `executing → cancelled` (while no spend record exists) is a new transition,
  added to SPEC §6.2. Draft 4's "no earlier than 60 s after `due_at`" is dropped.
- **OD-624, grants in their own table (K2):** grants and spend records live in a grants table.
  `ecf-api` (and `ecf-worker`, same role until OD-641 split them; both may) may put and update; the broker may only update the status
  and consume attributes (`dynamodb:Attributes`), on keys it is given; `ecf-jobs` may only delete
  (restore voids grants anyway; deleting is the safe direction). The spend record is one item per
  attempt (`GRANTUSE#<id>#<n>`, n ≤ 3, each created with `attribute_not_exists`), so v1's 3-attempt
  retry still works; every runtime role is denied `UpdateItem`, `DeleteItem` and `BatchWriteItem` on
  spend records. The broker has no write on the `ecf-api` table.
- **OD-625, routes and work split by timeout (K3):** `ecf-api` serves routes only (30 s, the API
  Gateway limit); `ecf-worker` runs the tick, the sweeps and both Slack queue consumers (60 s). Same code
  package, same role: no new trust boundary, only separate timeouts and capacity, so a slow Slack post
  or tick can't take route capacity. (Separate roles since OD-641.)

### After Phase R round 5 (SPEC OD-640 to OD-642, operator 2026-11-05)

- **OD-640, Cancel and Reject decided at the request URL (L1):** round 5 found the in-flight flag
  (OD-623) cleared by other clicks, outlived by one queue redelivery, cleared on errors and stopped by
  maintenance. It is removed. Cancel and Reject (the risk-lowering actions that race a send) are
  decided synchronously by `ecf-slack-in` before it answers Slack: it reads the clicker's identity,
  person and role and the item's card record, checks the button's message is the item's card
  (`container.message_ts`), and makes one conditional transaction (Cancel: `delayed → cancelled`, or
  `executing → cancelled` while no attempt record exists or the newest attempt ended `retryable`;
  Reject: grant `approved → voided`, item to `rejected`), with its audit row, then queues the card
  edit. Whichever of the Cancel and the broker's consume reaches DynamoDB first wins; the person is
  told the true result ("cancelled", "sending now, can't cancel", "sent", "outcome unknown").
  Maintenance doesn't block a Cancel (it only lowers risk). Every other click stays queued. Stated
  limit: a compromised `ecf-slack-in` can cancel or reject anything and read people and roles.
  (Superseded: Cancel works only while `delayed` and there is no `executing → cancelled`, OD-649. Its
  write mechanism is replaced by OD-644: round 6 found these direct status writes let a
  compromise revive a voided grant, because IAM limits attribute names, not values.)
- **OD-641, separate roles for `ecf-api` and `ecf-worker` (L2):** the internet-facing route function
  gets no queue-consume rights and no Slack secrets; `ecf-worker` gets them. Amends OD-625's "same
  role".
- **OD-642, one signed migration attestation (L3):** the "local step-up" of draft 5 can't be verified
  by AWS and would hit v1's 5-per-10-minutes limit. Instead, the laptop, holding the typed backup key,
  signs one attestation per phase with the relink key (a domain tag, `install.id`, a nonce from
  `ecf-api`, an expiry, and every `(kind, target, SHA-256 of value)` it is about to write);
  `ecf-api` verifies it against the relink verify key, and requires one Cognito step-up (purpose
  `migrate_secret`, pending identity, migrating install only) bound to the same manifest hash. Two
  phases (pre-cutover: signing secret, configuration token; cutover: mailbox passwords, bot token):
  about four prompts in all, whatever the number of mailboxes.

### After Phase R round 6 (SPEC OD-644 to OD-646, operator 2026-11-05)

- **OD-644, Cancel and Reject by add-only stop records (N1):** `ecf-slack-in` writes nothing but
  create-once records under its own key prefixes, which IAM can enforce (`dynamodb:LeadingKeys`): a
  stop record `STOP#<item>` (`kind` cancel or reject, the grant, the Slack user, the person, the time,
  the dedupe key) and its own audit row (`AUDIT#SLACKIN#…`). It gets no `UpdateItem` or `DeleteItem`
  anywhere. A Cancel's transaction is: put `STOP#<item>` (`attribute_not_exists`), plus a
  `ConditionCheck` that `HANDOVER#<item>` doesn't exist. The broker, immediately before it hands an
  action to the mail server, puts `HANDOVER#<item>` in a transaction with a `ConditionCheck` that
  `STOP#<item>` doesn't exist. Exactly one of the two succeeds, so a Cancel wins whenever nothing has
  been handed to the mail server yet (including during v1's retry backoff), and the person is told the
  truth ("cancelled, nothing was sent" or "too late, sending started"). A Reject's transaction puts
  `STOP#<item>` with `ConditionCheck`s that the item is `awaiting_approval` or `awaiting_stepup`, is
  not a queued answer, and its grant is `issued` (v1's preconditions, `approvals.py:421-441`);
  `ecf-worker`'s approve and the tick's `delayed → executing` check that no stop record exists.
  `ecf-worker` then applies the change (item `cancelled` or `rejected`, grant voided, delay deleted,
  audit, the card re-rendered), from a message `ecf-slack-in` queues or on the next tick. A stop record
  is never cleared. The result reaches the person as an ephemeral through the click's `response_url`
  (never `replace_original`). Stated limit: a compromised `ecf-slack-in` can stop (cancel or reject)
  anything it can name, a denial of service, and can't approve, send or edit status. Delete draft
  stays a queued click handled by `ecf-worker`. Amends OD-640. (Its per-item keys and the handover
  record are replaced by OD-649: round 7 found a per-item stop blocks every later grant and a per-item
  handover refuses a Cancel after a retry that sent nothing.)
- **OD-645, held Slack posts stay at the head of their group (N2):** OD-557's holds use
  `ChangeMessageVisibility` and report the message as a batch-item failure, as v1 keeps a held job at
  the head of its channel; `slack-out.fifo`'s `maxReceiveCount` is 125 (more than a two-hour hold
  needs; 1,000 since OD-651); real crashes are counted per post in DynamoDB and the worker
  dead-letters a post after 5. Draft 6's delete-and-re-send is dropped (FIFO has no per-message delay).
- **OD-646, scheduled exports read the live tables during a short hold (N3):** strongly consistent
  reads of the live tables while the maintenance hold is up; a point-in-time restore only above a size
  Step 8 measures. About US$0.01 a month instead of US$0.45-4.50. (Amended by OD-650: the hold is a
  write barrier, the path is chosen by hold time too, and the cost is about US$1 per GB a month.)

### After Phase R round 7 (SPEC OD-649 to OD-653, operator 2026-11-05)

- **OD-649, Cancel and Reject as v1, stop records per grant (V1):** v1 cancels only a `delayed` send
  (`approvals.py:444-448`); during a retry wait the item stays `executing` (`execute.py:93-104`), so v1
  never offered Cancel there, and draft 7's promise that it would is dropped with the handover record.
  - **Keys:** a stop record is `STOP#<grant>` in the main table (its own partition key; grant IDs are
    32 random hex, `ecf/ids.py:23-29`), so a new grant (requeue, re-offer, reissue after a restore) is
    never blocked by an old stop. Fields: `kind`, item, Slack user, person, time, dedupe key and a
    `stopq` attribute that puts it in a sparse index of stops not yet applied.
  - **Cancel:** put `STOP#<grant>` (`attribute_not_exists`) with `ConditionCheck`s that the item is
    `delayed` and its current grant (a top-level `grant_id` attribute, set whenever a grant is issued)
    is the button's grant. The tick's `delayed → executing` is a transaction with a `ConditionCheck`
    that `STOP#<grant>` doesn't exist. DynamoDB transactions are serializable, so exactly one of the two
    commits: a Cancel either wins (nothing has started) or is told "too late: sending has started".
  - **Reject:** put `STOP#<grant>` with `ConditionCheck`s that the item is `awaiting_approval` or
    `awaiting_stepup`, its current grant is the button's, and it has no `answer_pending` attribute
    (moved out of the `proposal` JSON to a top-level attribute so a condition can test it; v1's
    `queued_answer`, `approvals.py:144-148`). v1 never checks the grant's status
    (`approvals.py:421-441`), so neither does this; no grants-table right is needed.
  - **Every move out of `awaiting_*` or `delayed`** (approve and `approve --pending` in either API role,
    the tick's `delayed → executing`, expiry and its re-offer) carries a `ConditionCheck` that no stop
    record exists for the item's current grant; when it fails, the writer applies the stop instead. A
    contract test enumerates SPEC §6.2's edges out of those states.
  - **Applying:** `ecf-worker` (from the message `ecf-slack-in` queues, or the tick, which Queries the
    sparse index) re-checks the stop's conditions against the item and applies v1's `cancel` or
    `reject` in one transaction that also removes `stopq` and sets `expires_at` (35 days, the PITR
    window). A stop whose conditions don't hold (only a forged one can: the transitions above refuse
    while it exists) is audited as an invalid stop record, alarmed, and leaves the item alone. The
    stop's own `kind`, `person` and time fields are never trusted beyond the audit.
  - **Identity in the transaction:** `ConditionCheck`s that the `IDENT#` item is `confirmed` for that
    person and the person is `active`; the role per address is read with a strongly consistent read
    before and re-checked by `ecf-worker` when it applies (a role removed in between makes the stop
    invalid; stated limit).
  - **Answers** come from `CancellationReasons` (`ReturnValues` stays `NONE`): stop exists → "already
    cancelled or rejected"; item condition → a consistent read of the status: `executing` "too late:
    sending has started", `executed` "already sent", another grant "this card is out of date",
    `approved`/`delayed` on a Reject "already approved: use Cancel"; identity → "not allowed";
    `TransactionConflict` → retried within the budget.
  - `executing → cancelled` is not added to SPEC §6.2. `ecf cancel` and `ecf reject` in `ecf-api` run
    v1's transitions with the same conditions. Amends OD-644. (The apply-time role re-check, the
    "only a forged one" claim and the index used for completeness are replaced by OD-656.)
- **OD-650, holds are a write barrier (V4, round-7 migration 3, performance 4 5):**
  - Every write transaction in the API roles and `ecf-jobs` (except the export or restore job that
    holds the barrier) carries a `ConditionCheck` on the `MAINT` item (no hold, or the hold's epoch is
    the one read at request start); a refused write returns "maintenance, retry". The broker is already
    drained by its lease.
  - A rollback first sets `MAINT.moving_back` (every stop transaction checks it, so `ecf-slack-in`
    answers "ecf is moving back to your computer; use it there"), then the hold; `ecf-jobs` applies
    every outstanding stop, then exports. A restore or import merge applies outstanding stops first,
    and an item whose stop was applied after the restore point keeps its live status (live-wins).
  - A scheduled export applies outstanding stops at the start of its hold, scans the stop and
    `AUDIT#SLACKIN#` prefixes last, records the scan's start and end, and lists rows written after the
    hold began. TTL deletes during the scan are a stated limit (expired items, already filtered).
  - The export takes the PITR path when the table size passes Step 8's line or the last hold exceeded
    60 s; Scans use parallel segments. Cost about US$1 per GB of table data a month (1 RRU per 4 KB,
    US$0.125 per million, fetched 2026-11-05 (rv)), US$4.50 per GB a month on the PITR path.
  - Clicks during a hold: `ecf-worker` sets the click's visibility to the hold's expected end (about
    30 s) and reports a batch-item failure; these receives don't count as crashes (counted in
    DynamoDB, as OD-645). Amends OD-646. (The per-write `MAINT` check and the order of `moving_back`
    are replaced by OD-655: round 8 found the check made `MAINT` one item every write conflicts on.)
- **OD-651, Slack queues and cards (round-7 performance 3 6, Slack 4 5):**
  - `slack-out.fifo` and `slack-in.fifo` allow 1,000 receives (SQS's maximum); `ecf-worker`
    dead-letters only after 5 real crashes counted in DynamoDB, so v1's network (60 s), token (300 s)
    and rate-limit holds never dead-letter early. Both queues and their DLQs keep messages 4 days;
    a post held past 1,000 receives (about 3.5 days of 300 s holds) is dead-lettered and alarmed
    (SPEC §12.2 stated limit).
  - Card posts and edits go by reference; ephemerals and DMs go by value. Deduplication ID = a ULID per
    render request. A first post uses the channel's group; edits of a posted card use `card:<item>`
    (they render from current state, so order doesn't matter); OD-557's per-channel pacing is a
    DynamoDB pacing record. A render is skipped when the card's rendered version is at least the item's.
    The item stores `card_note`, the event text v1's `_edit` calls pass (for example "Sending in N
    minutes unless you cancel", "Done: moved, flagged", "Offered again", "Approved: … (running)"),
    written in the same transaction as the change; one renderer with a parity test per v1 `_edit` call
    site (Step 2).
  - Stop-apply messages use their own group `stop:<grant>`.
  - The result ephemeral: `ecf-slack-in` posts it to `response_url` if its budget allows, else
    `ecf-worker` posts it within Slack's 30 minutes, before it applies the stop and before its hold
    check (the post writes nothing, so a hold doesn't delay it); the `slack-in.fifo` message carries the
    `response_url` and the person, and `ecf-slack-in`'s audit row says whether it posted.
    `response_url` is never stored in DynamoDB. Amends OD-645. (Form refusals, the renderer's
    inputs, Slack-wide holds and the hold length are completed by OD-657.)
- **OD-652, one identity store (round-7 identity 4):** every identity (Cognito, Slack, OS user) is an
  `IDENT#<issuer>#<subject>` item `{people_id, kind, status: pending | confirmed | revoked,
  confirmed_at, confirmed_by}` in the `ecf-api` table, with a GSI by `people_id`; the people item has
  no nested identities. Bundles carry `identities.jsonl`; local mode gets an `identities` table (Step 0).
  A pending Cognito identity on `init --migrating` has no `people_id` attribute until `migrate_link`
  (a GSI key can't be null; the index is sparse), and no authority check reads the GSI, which is
  eventually consistent: every check is a `GetItem` or `ConditionCheck` on the item.
- **OD-653, the cutover attestation is signed by the CLI (round-7 identity 1 2):** the held local
  service has no backup root (`backup_key.py:1-6`), so it can't sign. At cutover it builds the manifest
  (kind, target, SHA-256 of each Keychain value it will send); the CLI shows it, the operator confirms,
  the CLI signs it with the relink key derived from the typed backup key and does the Cognito
  step-up, then hands the attestation and the step-up proof to the held service, which sends exactly
  those values; `ecf-api` checks each value's SHA-256 against the manifest. The root never leaves the
  CLI process. The derivation (`derive`, the age recipient, the relink key) moves to an `ecf` module,
  which `ecf_server.backup_key` re-exports, with known-answer vectors. Amends OD-642.

### After Phase R round 8 (SPEC OD-655 to OD-657, operator 2026-11-05)

- **OD-655, a hold is the flag plus a grace period (W1, W2; operator chose this over patching the
  per-write barrier):** round 8 found OD-650's `ConditionCheck` on `MAINT` in every write made one item
  that every pair of concurrent writes conflicts on. Lambda timeouts are hard limits, so:
  - Every function reads `MAINT` (strongly consistent `GetItem`) when a request, message or tick
    starts and does nothing while a hold is up (as v1's maintenance flag). No write carries a `MAINT`
    check except a lease take.
  - The broker takes its address lease with a `ConditionCheck` on `MAINT`, and re-reads `MAINT` before
    each action and each fetch page, ending early with its progress saved, so a drain takes at most one
    step (120 s per action). Every `ecf-jobs` job holds a job lease taken the same way and checks
    `MAINT` between pages. The hold drains both kinds of lease.
  - After the hold is written and the leases have drained, the holder waits 90 s (`ecf-worker`'s 60 s
    and `ecf-api`'s 30 s timeouts, plus margin) before reading; no other writer can still be running.
  - Only `ecf-jobs` and the `ecf-api` role write `MAINT`; every other role's main-table write grant
    carries `ForAllValues:StringNotLike` `dynamodb:LeadingKeys` `["MAINT"]`.
  - A rollback sets `moving_back` and the hold in one write. A sweep that started just before it can
    still move a `delayed` item to `executing` during the grace, but no broker can take a lease, so
    nothing is sent in AWS; the rollback bundle carries it as `executing`, which v1's restore turns
    into `failed_unknown` (settled by `\Sent` reconciliation). `moving_back` is cleared only by `ecf
    migrate rollback --cancel` before the export; an alarm fires when it is set with no rollback job
    running for 30 minutes.
  - The path is chosen by the measured Scan time alone (drain is the same on both paths), with a
    margin so it doesn't flip between runs. A message that arrives during a hold is put back with its
    visibility set to the hold's expected end before anything is written, so nothing counts it as a
    crash. Amends OD-650. (The rollback's `moving_back` and grace case, `rollback --cancel`, and stops
    during rollback and merge holds are replaced by OD-658; the holder's own lease by OD-658 too.)
- **OD-656, stop records: authority at commit, never a wedge (W3, W4):**
  - Whether a Cancel or Reject is allowed is decided when its transaction commits (identity, person
    and the role read just before). When `ecf-worker` or `ecf-jobs` applies a stop it re-checks only
    the item's status and grant; a role change after the commit doesn't undo it (v1 checks at click
    time too).
  - A stop has a `state`: `pending`, `applied`, `moot` or `invalid`. Every move out of `awaiting_*` or
    `delayed` requires no stop for the current grant in state `pending`. The edges, from SPEC §6.2 and
    v1: approve (single, `--pending`, approve-scan, the draft check's reject inside approve,
    `approvals.py:262`), queue-for-computer (`:371`), expiry and its re-offer, the tick's `delayed →
    executing`, `fix` (`awaiting_approval → proposed`), and `resolved_manual` / `resolved_by_mailbox`
    from every open status (`state_machine.py:98-99`: `ecf item resolve`, address remove, the broker's
    `close_gone`). A contract test enumerates them.
  - Applying: the item still at the stop's status and grant → v1's `cancel` or `reject`, state
    `applied`; the item resolved by a resolve edge → `moot` (audited, not alarmed: nothing was done
    either way); anything else → `invalid` (audited and alarmed). Every outcome removes `stopq` and
    sets `expires_at` (35 days) in the same transaction, which is conditioned on `state = pending`, so
    two concurrent applies (message and tick) apply once and the second is skipped quietly. A stop is
    never left pending, so nothing is blocked for good. The broker needs no right on `STOP#`: it only
    resolves, and the worker marks the stop moot.
  - **Completeness:** the tick finds pending stops through the sparse index (eventually consistent,
    good enough for a retry loop). A rollback, export or merge, which must not miss one, finds them
    with a strongly consistent read of the base table: the export's own consistent Scan (stops applied,
    the affected items read again), or on the PITR path a separate consistent Scan of the live table.
  - **Answers:** one closed table from (reason, status) to text, every refusal prefixed "Not done:" as
    v1 (`approvals.py:1278`), in precedence order: a pending or applied stop → "already cancelled or
    rejected"; `answer_pending` → v1's `ANSWER_WAITS`; another grant → "this card is out of date";
    `executing` → "sending has started"; `executed` → "already sent"; a Reject on a `delayed` item →
    "already approved: use Cancel" (only then); `moving_back` → "ecf is moving back to your computer;
    use it there"; any other status → "this email is <status>". A test covers every row.
  - `grant_id` is written by every grant insert (request, re-offer, expiry, reissue after import or
    restore, requeue, `approvals.py:695-701`), with a test per writer; import and merge derive
    `answer_pending` from the `proposal` JSON. Cutover re-renders every open card with typed values; a
    legacy Cancel or Reject value (v1 cards carry only the item or grant, `approvals.py:383, 600`) is
    refused with "Not done: this card is from before the move; use the new card".
  - `ecf-jobs` gets grants-table `UpdateItem` on `status` only (`dynamodb:Attributes`), so it can void
    a grant as v1's `_void` does when it applies a stop. Amends OD-649. (Round 9: the resolve edges
    are exempt, not checked; completeness and the answers' first rows are replaced by OD-658;
    `ecf-jobs` voids by deleting the grant again, since an update right on `status` could set it back to
    `approved`; "cutover re-renders every open card" is replaced by cutover step 9's "moved" edit.)
- **OD-657, Slack forms and cards (W5; round-8 Slack 4 6, performance 3, cost 2):**
  - A form submission is answered by `ecf-slack-in` with `response_action: update` and a "Checking…"
    view; `ecf-worker` then sends `views.update` with the result: the next view, or the form again
    with an error section naming the refusal. `response_action: errors` (keyed by an input's
    `block_id`) is not used in M1.
  - The renderer takes the item's status, `card_note`, the buttons with the current grant, the draft
    text and `draft_versions.latest`, and the proposal's `done` list. Parity tests cover every v1
    function that draws a card (`_edit`, `_card`, `show_decided`, `done_card`, `edit_card`,
    `_check_draft`, `_redraw_one`, `draft_cards`). Every `card_note` write bumps the item's version. An
    edit that finds no `message_ts` yet holds until the first post lands; the first post re-reads the
    version after `chat.postMessage` and queues an edit if it changed.
  - Slack-wide holds as v1 (`slack_out.py:7-16`): a `hold_until` and reason on the channel's pacing
    record and on an install-wide Slack record, checked before every Slack call; a wait of 1 s or less
    is slept in the function. After 15 consecutive 60 s holds of one post its hold becomes 300 s (a new
    rule: v1 holds in memory and has no receive count), so 1,000 receives last about 3.4 days, inside
    the 4-day retention.
  - `Ack.Decided(result_text, card | None)`: Teams answers with a text message
    (`application/vnd.microsoft.activity.message`) and leaves the card to `ecf-worker`'s update, since
    whether an `Action.Execute` replacement card shows to everyone is unverified (M11).
  - M1 supports single-workspace Slack installs only; an org-wide (Enterprise Grid) install is refused.
    The payload paths for team and enterprise IDs are checked in Step 9 (unverified), and `doctor`
    compares `auth.test` with the stack parameters. Amends OD-651. (Round 9: the renderer's list,
    the edit-before-post rule and the 300 s counting are completed in "After round 9".)

### After Phase R round 9 (SPEC OD-658 to OD-660, operator 2026-11-05)

Round 9 again found most of its highs in draft 9's own fixes; applying the operator's rule, the
operator chose to simplify the rollback and the audit IDs and to finish key rotation.

- **OD-658, rollbacks and merges pause first; scheduled exports don't look for stops (X1, X2;
  round-9 safety 1-4):** v1 already holds a paused address's delayed sends at the tick
  (`approvals.py:574-579`) and its actions in the runner (`execute.py:49`), and a restore leaves every
  address paused until a mail check passes and the operator resumes it (`restore.py:18, 161`).
  - A rollback, and an AWS restore or import merge, first pauses every address (v1's pause), then sets
    the hold. Nothing can start sending, so no `delayed` item moves to `executing` during the grace.
    `moving_back` is gone: the hold records its kind (`scheduled_export`, `upgrade`, `rollback`,
    `merge`) and the holding job's ID.
  - During a rollback or merge hold, `ecf-slack-in` (which reads `MAINT` in its existing `BatchGetItem`,
    never in the stop transaction) refuses a Cancel or Reject with "Not done: ecf is moving back to
    your computer (or: a restore is running); nothing is sent until you approve it again there (or:
    resume the address)". That is true: the item stays held, and v1's restore turns `delayed` into
    `awaiting_approval` (`import_plan.py:14-16`). A Cancel that read `MAINT` just before the hold and
    commits after it is caught: the holder waits the grace (longer than `ecf-slack-in`'s timeout),
    then finds every pending stop with the consistent Scan it does anyway, applies them, reads the
    affected items again, and only then exports or merges.
  - Rollbacks and merges always take the Scan path (they are rare). There is no `ecf migrate
    rollback --cancel`: a rollback, once requested, runs to the end.
  - A scheduled export doesn't look for stops: stop records aren't exported, and the tick applies them
    after the hold. Stops are allowed during its hold, and `slack-out` posts that write only live-kept
    records run during it too.
  - **The holder's own lease:** `MAINT` names the holding job; the drain skips that job's lease;
    `ecf-jobs` runs that job's messages during the hold; the tick still re-queues it, still emits its
    heartbeat, and alarms on a hold with no live holder for 30 minutes. Every hold, `ecf upgrade`'s
    included, waits the 90 s grace after the drain. Fetch pages, backfill pages and job pages are each
    bounded to 60 s, so the worst-case hold is about 120 s (drain) + 90 s + the Scan, recorded on `MAINT`
    as its expected end. Step 8 records the hold's p99.
  - The resolve edges (`resolved_manual`, `resolved_by_mailbox`, address remove, `close_gone`) are
    exempt from the stop check: resolve wins and the stop becomes `moot`. `ecf cancel` and `ecf reject`
    in `ecf-api` leave the item at the stop's own outcome, which the apply treats as `applied`.
  - The answer table's first rows: a hold of kind `rollback` or `merge` → the text above; identity,
    person or role refused → "not allowed"; budget exhausted → "click again". "Already cancelled or
    rejected" only when the stop put failed and the item, read again, is `cancelled`, `rejected` or
    still at the stop's status; otherwise the status rows. Reject keeps v1's text ("this email is
    <status>: nothing to reject", `approvals.py:426`); Cancel's texts are new wording, recorded. A Reject
    on a re-offered card is told "out of date", a deliberate change from v1 (which rejects whatever the
    grant).
  - `ecf-jobs` voids a grant by deleting it (its only grants right, as before draft 9); the broker treats
    a missing grant as not approved. Amends OD-655 and OD-656.
- **OD-659, a random audit `uid` for every new row (X3; operator chose this over fixing the prefix):**
  every new audit row gets a random `uid` (`lower(hex(randomblob(16)))` in an `AFTER INSERT` trigger
  when the writer left it null; a ULID in DynamoDB), so no two installs or copies can collide and no
  prefix setting exists. Only rows from before M1 get a derived `uid`, `<install.id>:<id>`: at the
  SQLite migration for the install's own rows, and at import for a bundle's rows that carry none
  (computed from the bundle's `install_id` before anything else writes to the staged database; the
  migration's backfill in a staged database covers only ids up to the bundle's highest audit id, so rows
  the staged migrations insert, such as 0041's, get random ones). Re-importing the same old bundle
  still finds its duplicates. `importer.copy` inserts audit rows `WHERE NOT EXISTS` on `uid`. Replaces
  draft 9's `audit.uid_prefix` and `import:` prefix.
- **OD-660, backup-key rotation in AWS mode (operator chose to finish it in M1):**
  - It runs in the CLI only (after `migrate finalize` there is no local service): `new_root`,
    `key_text`, `fingerprint` and `same_fingerprint` (`backup_key.py:60-95`) move to the `ecf` module
    with `derive`.
  - Step-up purpose `export_key` (v1's, `export_keys.py:114-131`): admin only, bound to the new
    fingerprints, which the operator types, as v1.
  - Order: (1) the step-up gates an `ecf-api` route that records the rotation as pending, with the new
    export and relink fingerprints (audited, Security Notice); (2) the laptop writes the new seed, with
    the expected recipient and relink fingerprints in the same secret value, as a new version with a
    pending label; (3) one deploy switches: the age recipient and the relink verify key are replaced
    (single values; the leaked root can neither relink nor read new exports), the new export verify key
    is added and the previous ones kept in `ExportKeysPrevious` (only to verify older bundles; pruned
    when no kept backup needs them), and the functions read the seed version the deploy names;
    (4) `doctor` confirms the deployed fingerprints equal the recorded ones. A crash before (3) leaves
    the old keys in force; re-running is idempotent. Whether Secrets Manager's staging labels behave as
    needed is unverified: Step 8 checks it, else versions are addressed by ID.
  - The relink fingerprint is defined like OD-341's (80 bits of SHA-256, 4 groups of 4 Crockford
    characters) over `ecf/relink-fp/v1` and the relink verify key; it is recorded at `init`, shown beside
    the OD-341 fingerprint wherever that is shown, and compared by `migrate plan`, `doctor` and
    `export --pull`.
  - Refused until `migrate finalize`. Amends OD-616 and OD-653.
- **Also from round 9 (no new decision):**
  - **Identity:** relink uses one nonce, as cutover (the nonce record holds purpose, old identity,
    `people.id` and new subject; the signature and the step-up carry the same nonce; verify, then one
    `TransactWriteItems` consumes nonce and `jti`, revokes the old identity and writes the new); "bound to
    the same manifest hash" reads "carrying the same nonce" everywhere; `RATE#` counting is the only
    write before the consume, and verification covers `client_id`, `token_use`, scope, `auth_time`,
    `iat`, issuer, `ecf_nonce` and `sub`; the attestation body is never stored or logged; per-entry
    flags are keyed by (phase, kind, target). An OS identity's issuer is `os:<machine UUID>` and its
    subject the user name; a re-bind after a restore creates a new identity and never revives a revoked
    one. Local relink checks the typed root against `export.key`'s OD-341 fingerprint (v1's
    `same_fingerprint`), so upgraded v1 installs need no stored relink key. Identity merges: an identity
    with no `people_id` keeps the live record; one whose `people_id` differs between live and bundle
    keeps live, audited. `export_bundle.verify` moves with `_split`, `MAGIC`, `SIG_BYTES`, `MAX_HEADER`
    and `BundleError` to an `ecf` bundle-format module.
  - **Slack:** the renderer covers every `item:<sid>` card site: approvals (`approvals.py:820`),
    escalations (`escalations.py:88-108`, `close_card`) and answers (`answers.py:288-318`); its inputs add
    the facts (kind or title, `dismissible`, payment note), the clarification question and round, the
    first post's mention, and the escalation's carded or merged state; one parity test per site. An
    edit that finds no `message_ts` is dropped (the first post writes `message_ts` and then re-reads the
    version and queues an edit if it changed). A post is put back on 300 s once SQS's
    `ApproximateReceiveCount` passes 15, whatever the cause; a post waiting on a Slack-wide hold uses
    min(`hold_until` − now, 300 s); `views.open` is exempt from the holds. The "Checking…" view sets
    `external_id` = the submission's dedupe key and the queue message carries `view.id` as a fallback
    (Step 9 records which works); a re-shown form fills each input from the submitted
    `view.state.values`; a refusal whose view was closed (`not_found`) is sent as an ephemeral. Cutover
    edits old cards to "moved" and posts fresh cards (step 9); a legacy click in between is refused with
    "this card is from before the move". An org-wide install is detected by `auth.test`'s enterprise
    fields at `init` and by `doctor` (unverified field names, Step 9); the runbook gives the repair
    for a workspace later moved into Grid (a stack parameter update). For M11, still unverified: who
    sees a Teams invoke reply, its time limit, and `Action.Submit`'s fallback, which can't return a
    card.
  - **IAM:** every Allow statement granting main-table writes to a role other than `ecf-jobs` and
    `ecf-api` carries `ForAllValues:StringNotLike` `dynamodb:LeadingKeys` `["MAINT"]` and `Null`
    `dynamodb:LeadingKeys` `false` (`ForAllValues` is true when a request has no key,
    `reference_policies_condition-single-vs-multi-valued-context-keys.html`, fetched 2026-11-05 (rv));
    `ConditionCheckItem` sits in its own statements, so the broker's lease-take check on `MAINT` is
    allowed while its `PutItem` on `MAINT` is denied (a Step 8 test). The main table's key attributes are
    `pk` and `sk`; an item is a single record `pk = ITEM#<stable_id>`, `sk = ITEM`, and per-item records
    use their own prefixes (`CARD#`, `DRAFTV#`, `CHECK#`), through one key helper with a round-trip test;
    `ecf-slack-in`'s Attributes lists name `pk`, `sk` and `stopq`. The expected recipient and relink
    fingerprints live in the seed secret (runtime roles can't write it); `doctor` and `--pull` on the
    laptop are the real control. CloudFormation events match the stack or change-set name and add
    `CancelUpdateStack`, `RollbackStack`, `ContinueUpdateRollback` and `SetStackPolicy`; each notice is
    matched to a recorded `ecf upgrade` or rotation, and an unmatched one is flagged. Stated limit: a
    compromised `ecf-slack-in` can overwrite its own `AUDIT#SLACKIN#` rows before the next flush.
  - **Migration:** the bundle reader's table allow-list depends on the kind: `migrate` adds
    `slack_messages` and `draft_versions`, `rollback` adds `alert_outbox` (rows `queued` or `rolled_up`
    only, inserted with new ids; a row with `message_id` set and not settled is listed in the runbook
    as a possible duplicate). The shared transition helper writes `via: stop` and the stop's ID into the
    `item.transitioned` row, which is how "stopped after the restore point" is decided (a Step 0
    contract test). The highest generation AWS has seen is kept on the install's keys item.
  - **Measurement:** Step 1b runs on a second named local install (`ecf --install m1ref`, a real
    service), and its result file's path and sha256 go in SPEC §16. Step 12 uses a throwaway real local
    install (`ecf init` under its own name, a test Slack app and a test mailbox, as M8 Step 10), whose
    gate rows come from a `--fraud-only` C run on it with Step 12's code (go-ahead). The dedicated
    checkout is a full clone at a pinned commit at or after the eval/private merge. The clean-and-pushed
    check used by uploads and by for-gate runs is one function in `ecf.eval`; the membership counts are
    one function in `ecf.eval` too, called by the laptop and by `ecf-jobs`.

## Design

### Architecture

```
Laptop (operator)                    AWS (base stack + app stack)                       External
─────────────────                    ────────────────────────────                       ────────
ecf CLI ─────────────JWT───────────► API Gateway HTTP API ─► ecf-api (routes) ─┬─► DynamoDB / S3 / SSM
ecf claude (Claude Code, on demand)                                            ├─► jobs.fifo ─► ecf-jobs
  ├ ecf-mcp ─0600 socket─► relay ─JWT + session token─► same API               └─► invoke ─► ecf-broker ─► IMAP/SMTP
  └ OTLP ─loopback─► relay
  Claude: Anthropic (plan) or Bedrock us. profile
                                     Slack ─signed─► API GW ─► ecf-slack-in ─► slack-in.fifo ─► ecf-worker
                                     ecf-api / ecf-worker / broker / ecf-jobs ─► slack-out.fifo ─► ecf-worker ─► Slack
                                     Scheduler ─► ecf-worker (tick and sweep, every minute) ─► invoke ─► ecf-broker
                                     Scheduler ─► jobs.fifo (housekeeping) ─► ecf-jobs
                                     CloudWatch alarms, CloudTrail rules ─► SNS ─► admin email
Cognito (Essentials, TOTP): app clients CLI (PKCE loopback) · step-up (SRP only)
```

`ecf-api` and `ecf-worker` are one code package with two IAM roles (OD-625, OD-641). Below, "the API
roles" means both; "`ecf-api`'s role" or "`ecf-worker`'s role" is used where only one is meant.
Queue-consume rights and the Slack secrets are `ecf-worker`'s alone.

### Who may call, read and write what

A Lambda learns nothing about who invoked it, and inside one account an identity policy alone is
enough to use a queue or read a secret. So every boundary is stated on both sides:

- **Invocation:** `ecf-api` only by API Gateway (`SourceArn`); `ecf-worker` only by its two queue
  mappings and the tick schedule's role; `ecf-jobs` only by its `jobs.fifo` mapping (housekeeping
  schedules send to `jobs.fifo`, so every job goes through one queue and one lease); `ecf-slack-in`
  only by API Gateway; the broker only by the API roles. Mappings poll with the function's own role;
  each Scheduler role may invoke or send only to its one target, trusts `scheduler.amazonaws.com` with
  `aws:SourceAccount` and the schedule's `aws:SourceArn`, and its target accepts only the schedule's
  `Input` (which carries `<aws.scheduler.scheduled-time>`). Scheduler roles live under
  `/ecf-sched/<install>/` with their own small boundary (invoke `ecf-worker`; send to `jobs.fifo` and
  the one shared Scheduler DLQ) and their own CI test. The only `lambda:Principal` allowed is
  `apigateway.amazonaws.com`.
- **Queues:** each queue policy denies `SendMessage` unless `aws:PrincipalArn` is a named sender, and
  receive, delete and visibility changes unless it is the consumer (plus admin tooling on DLQs):

  | Queue | Senders | Consumer | `maxReceiveCount` |
  |---|---|---|---|
  | `slack-in.fifo` (+ DLQ) | `ecf-slack-in` | `ecf-worker` | 1,000 (holds; crashes counted, OD-650, OD-651) |
  | `slack-out.fifo` (+ DLQ) | the API roles, `ecf-broker`, `ecf-jobs` | `ecf-worker` | 1,000 (holds; crashes counted, OD-651) |
  | `jobs.fifo` (+ DLQ) | the API roles, housekeeping schedule roles | `ecf-jobs` | 5 |
  | on-failure queues (standard), one each for `ecf-broker` and `ecf-worker` (the async-invoked functions) | Lambda, with that function's role | admin | — |
  | the Scheduler DLQ (standard, one shared) | every schedule's role | admin | — |

  Alarms on Lambda's `DestinationDeliveryFailures` and Scheduler's
  `InvocationsFailedToBeSentToDeadLetterCount` and `InvocationDroppedCount`; Step 8 checks each
  failure path.
- **Secrets:** exact ARNs in role policies; deploy-time secrets (Slack signing secret and bot token,
  the export seed) carry a resource policy denying `GetSecretValue` unless `aws:PrincipalArn` is an
  owning role (for the seed, `PutSecretValue`, `UpdateSecret` and `DeleteSecret` are denied to every
  runtime role; the laptop writes it with the deployer credentials, "Write-once keys");
  mailbox secrets are denied to every role except the broker's read and `ecf-api`'s role's write-only
  `CreateSecret`/`PutSecretValue`. CI checks only the broker can read a mailbox ARN.
- **Bearer:** `ecf-api` validates the `Authorization` bearer itself and never reads
  `requestContext.authorizer`; a request carrying a profile or agent token is that profile, never the
  CLI.
- **Grants (OD-624):** grants and spend records live in the grants table. Two paths create a grant,
  both in the API roles and both audited: a person's approval (`people.id` set) and v1's policy apply
  for automatic actions (`decide.apply`, `src/ecf_server/decide.py:293-311`, `principal` `service`,
  `people.id` null, source `policy`), with the policy's corroboration and batch-risk rules ported
  unchanged and a contract test that no MCP route creates a grant except through `decide.apply`. The
  broker may update only status and consume attributes; `ecf-jobs` may only delete. The broker
  executes a grant only in `approved`, matching hashes, and with no attempt record for this attempt.
  Each attempt writes `GRANTUSE#<id>#<n>` (its own partition key) with `attribute_not_exists` in the
  consume transaction; its outcome is a second create-once item `GRANTRES#<id>#<n>` (`retryable`,
  `done`, `failed`); an attempt with no outcome item is outcome unknown (v1's `failed_unknown`), never
  retried; v1's retry is attempt n+1 only after attempt n's outcome is `retryable` (n ≤ 3). Holds are
  as v1 (a due time; the grant unchanged).
- **What IAM enforces and what code enforces (round 5, stated honestly):** IAM limits by key prefix
  (`dynamodb:LeadingKeys`, partition key only) and attribute names (`dynamodb:Attributes`), never
  values. Policies are Allow-style: `ecf-jobs` gets `DeleteItem` on `GRANT#*` keys only and no
  `BatchWriteItem` on the grants table (`ForAllValues:StringLike` on `dynamodb:LeadingKeys`);
  `ecf-slack-in` gets `PutItem` only on `STOP#*` and `AUDIT#SLACKIN#*` (OD-644, OD-649;
  `ForAllValues:StringLike` on `dynamodb:LeadingKeys`, a `dynamodb:Attributes` allow-list, and
  `StringEqualsIfExists` `dynamodb:ReturnValues` `NONE` on `PutItem` and `ConditionCheckItem`, so a
  deliberately failing check can't return an item; Cancel's and Reject's puts both use
  `attribute_not_exists`). Its `ConditionCheckItem` statement has its own `ForAllValues:StringEquals`
  `dynamodb:Attributes` list (`pk`, `sk`, `stopq`, `status`, `grant_id`, `answer_pending`,
  identity `status` and `people_id`, person `active`, stop `state`), because a pass or fail is itself
  an answer: without the list, checks like `begins_with(subject, :p)` would read item content a
  character at a time (`specifying-conditions.html`: Attributes covers attributes evaluated in a
  conditional write; whether it is evaluated on `ConditionCheckItem` is tested in Step 8). Items are
  keyed `ITEM#<id>`, so the item statement is scoped to `ITEM#*` and can't reach the `sent` ledger,
  DNS cache or other records. No role but
  the API and worker roles and the broker may put on the grants table. That spend and outcome records
  are never overwritten, that the broker never creates a grant, and that it sets only the transitions
  in its list are enforced in code (a CI test that every put on `GRANTUSE#` and `GRANTRES#` carries
  `attribute_not_exists`; an audit cross-check flags any grant without the API roles'
  `action.granted` row, and trusts `action.granted` only under the API and worker writers' prefixes;
  readers and `ecf audit` show each row's writer prefix). IAM has no condition key that can require a
  `ConditionExpression`, so create-once and the stop transaction's checks bind honest code only: a
  compromised `ecf-slack-in` can write a bare stop, which is applied whenever the item is still at that
  status and grant (stated limit) and otherwise marked `invalid` and alarmed (OD-656). The broker's `dynamodb:Attributes` list (keys, every attribute its
  conditions read, status and consume fields, `ReturnValues` limited) is a guard against bugs, not
  against a compromised broker, which has mailbox authority anyway.
- **The broker's writes** (a closed list through the conditional-update helper; the OD-181 rule is
  "only the helper writes `status`", with a test; mapped from every `checks.IN_LEASE` worker, with a
  test that fails on an unmapped one): the pre-check (`new → awaiting_claude`, decision, escalation,
  audit, the no-grant label and flag); `close_gone`; execution and send settle; Undo (against an
  `undo` grant); queued draft delete and flag (against `draft_delete`/`draft_flag` grants); the `sent`
  ledger, `probe`, `check_state`, `processing`, DNS cache rows, backfill progress, draft tracking
  (`draft_versions`) and the second-install auto-pause request, all in the main table as
  broker-owned records the API roles read and apply. The broker has no write on the `ecf-api`
  table (settings, people, roles, sender confirmations).
- **Mailbox work outside the check:** address add and re-probe are broker jobs whose result the API
  polls (v1 does them inside routes, `addresses.py:172-248`); the broker drains `alert_outbox` (alert
  email through a watched address's SMTP login, as v1), so Security Notices still reach email.
- **No-grant pre-check actions:** label and flag only, for `decide(facts)` recomputed in the same
  invocation (never a stored proposal), under v1's four gates (nothing in shadow, quarantined mail not
  re-read, content hash verified, OD-439's keyword-stored skip); contract tests per gate.
- **Stated limits (SPEC §12.2):** a compromised broker has full mailbox authority; a compromised
  `ecf-slack-in` holds the signing secret and the bot token (a Slack token can't be scoped per call),
  so it can forge clicks, which `ecf-worker` still role-checks, and can post, edit or delete as the bot
  in any channel the bot is in (misleading cards, never an approval); it can stop the current grant of
  any item whose ID it learns (item IDs are on cards), protective actions included (a mass Reject leaves fraud mail labelled and flagged but not
  moved), and reads identities, people, roles, item status and card references. Detected by alarms on
  the stop-record rate and on invalid stop records; `ecf-worker` drops (and alarms on) a stop or
  `AUDIT#SLACKIN#` row over 4 KB or with attributes outside the allow-list.

| Role | Invoked by | Reads | Writes | Secrets and keys |
|---|---|---|---|---|
| `ecf-api` role | API Gateway | all tables; S3 `eval/` | main (incl. `ConditionCheckItem` on `STOP#*` and `MAINT`); `ecf-api` table; grants table (put, update); excerpts `expires_at` only; S3 `jobs/` presigned URLs (incl. GETs on `exports/`, `backups/`); sends `slack-out.fifo`, `jobs.fifo`; invokes broker | mailbox secrets and Slack secrets write-only (`set-tokens`, migrate); relink and export verify keys (stack parameters); Cognito `AdminUserGlobalSignOut` |
| `ecf-worker` role | `slack-in.fifo`, `slack-out.fifo`; tick schedule role | all tables (incl. `Query` on the unapplied-stop index) | main (incl. applying stop records: update `STOP#*`); `ecf-api` table; grants table (put, update); S3 `logs/` (create-only); receives and deletes on both Slack queues (the only consumer); sends `slack-out.fifo`, `jobs.fifo`; invokes broker | Slack signing secret, bot token |
| `ecf-broker` | `ecf-api`, `ecf-worker` (async) | main; `ecf-api` table (settings, read); grants table | main (closed list); grants (status and consume attributes); spend records (create only); excerpts; sends `slack-out.fifo` | mailbox secrets (read, `lambda:SourceFunctionArn` condition) |
| `ecf-jobs` | `jobs.fifo` | all tables (incl. `Scan`); S3 | main and `ecf-api` table (import, restore, migrate, merges); excerpts (import, restore); grants (delete only, OD-658); `MAINT`; S3 `exports/`, `jobs/`, `backups/`, `eval/`; backup and scratch tables; sends `slack-out.fifo` | export seed (read); age recipient and verify keys (stack parameters) |
| `ecf-slack-in` | API Gateway | `ecf-api` table: `IDENT#` items, people and roles only (`dynamodb:LeadingKeys` on those prefixes, `dynamodb:Attributes` limited, `dynamodb:Select` specific attributes; no `Query` or `Scan`); main: `ITEM#*` status, `grant_id`, `answer_pending` and card record, and the `MAINT` item, the same limits | `PutItem` only, create-once, on `STOP#*` and `AUDIT#SLACKIN#*` (OD-644, OD-649); `ConditionCheckItem` on `ITEM#*`, `STOP#*`, `IDENT#*` and the person (`ReturnValues` `NONE`, its own Attributes list); Slack table (dedupe keys); sends `slack-in.fifo` | Slack signing secret; bot token (`views.open`) |

### The functions

- **`ecf-api` (routes, 30 s):** rules and policy on stored facts; transitions; grant creation; config
  (in the `ecf-api` table's settings: "S3 config" is dropped); stage, sensitivity, pause, outbound;
  people, identities, sender confirmations; role checks. A route that can't finish in 25 s is a job;
  `/v1/checks` becomes "enqueued; poll `ecf status`". Every route's writes are idempotent by request
  ID, so a route the CLI saw time out at 30 s and retries doesn't apply twice.
- **`ecf-worker` (tick and queues, 60 s):**
  - consumes `slack-in.fifo` (clicks other than Cancel and Reject: verifies the HMAC, checks
    `container.message_ts` against the item's card, role-checks, decides, audits; a processing error is
    audited and the person gets "click again" and the message is deleted, so only a crash redelivers;
    batch size 1) and `slack-out.fifo` (paced posts, `chat.update`, `views.update` by `external_id` =
    the dedupe key, ephemerals, the dead-man `chat.scheduleMessage`). OD-557's holds set the message's
    visibility to the hold time and report it as a batch-item failure, so it stays at the head of its
    group as v1's held job does; `maxReceiveCount` is 1,000; real crashes are counted per post in
    DynamoDB and a post is dead-lettered after 5 (OD-645, OD-651). Card posts and edits are queued by
    reference ("render item X's card", deduplication ID a ULID per request, edits in `card:<item>`) and
    rendered from the item's status and `card_note` when sent; ephemerals and DMs go by value (OD-651);
    a click arriving during a hold is put back with its visibility set to the hold's expected end, not
    counted as a crash (OD-650);
  - **the tick** (every minute): a lease keyed on the schedule's scheduled time (`TICK#<time>`), event
    age 60 s, each sweep time-boxed within 50 s; due checks (async invoke of the broker, with a
    `dispatched_until` the broker extends to its lease's expiry while it runs and clears on release, so
    a run that never started is retried after 5 minutes and a running one gets no second invoke; a busy
    lease writes no due time while its holder is live); approval expiry,
    `delayed → executing` (making that address's check due now; a transaction with a `ConditionCheck`
    that no stop for the item's grant is `pending`, OD-656), applying stop records left in the
    unapplied-stop index, stale marking; "Needs you",
    reminders and refresh warnings, each re-reading status inside the winning conditional write; the
    audit flush (its own lease); re-queueing jobs whose progress item shows they stopped (a job
    heartbeat older than its deadline), never a running one; the tick heartbeat is a custom metric.
- **`ecf-broker`:** invoked per address by the API roles. Each run:
  - takes the address lease (`LEASE#<address>`) and, in the same conditional write, sets
    `dispatched_until` to the lease's expiry (a broker that finds the lease busy changes neither); renews both before each
    blocking step to now + max(180 s, the step's bound + 30 s); every transaction carries a fencing
    `ConditionCheck`;
  - takes the lease only with a `ConditionCheck` that no hold is up, and re-reads `MAINT` before each
    action and each fetch page, ending early with progress saved (OD-655); checks the `migrating` hold
    at start;
  - when started by an approval, runs due actions before the fetch page;
  - fetches with a deadline of min(start + `max_per_check`, remaining time − a reserve);
  - computes facts, triggers and the pre-check decision in memory and writes them in one
    transaction;
  - executes due approved actions with the open mailbox (an item reaches `executing` only through the
    tick's or an approval's transition, checked for a pending stop, so no Cancel can win after that, OD-649); the
    attempt's outcome records whether the send was handed over and accepted, and which side effects
    ran. A socket timeout bounds each read, not a
    command (imaplib, smtplib and TLS `sendall` loop over reads and writes), so **a watchdog closes the
    socket at each blocking network step's deadline** (connect, login, select, each fetch page, backfill,
    probes, and 120 s per action; one watchdog per run holding the socket object, never its file
    number, cancelled and joined in `finally`; it shuts down the raw socket and the reader maps the
    resulting `ValueError` as well as `OSError`; after a cut the run saves progress and ends; DNS is
    resolved with its own timeout first); an SMTP DATA starts only if the time left covers 15 s plus
    the message size at an assumed minimum rate plus a final-reply allowance, otherwise the action
    waits for the next run (a cut DATA reply turns an accepted send into `failed_unknown`); the loop stops when less than one action
    deadline remains;
  - runs backfill pages and address probes; the alert-outbox drain runs as its own broker run (its own
    lease, a per-row conditional claim, dispatched by the tick whenever rows are due, its writes in the
    closed list);
  - treats a pending second-install auto-pause request as paused: `pause.is_paused` reads the setting or
    a pending request everywhere (broker, tick, approve), and `ecf-worker` applies the request on its
    next tick. As in v1 (`approvals.py:444-454`), a pause never blocks Cancel or Reject.

  The isolate child runs with a scrubbed environment and no AWS client (`isolate.py:96-99`; a test).
  Memory at least 2,048 MB (Step 8 picks the smallest meeting the line); timeout = `max_per_check` +
  reserve, at most 900 s. v1's scheduling rules ported (`next_due_at`, a due time asked during a run
  kept, busy-lease retry 60 s). Async: `MaximumRetryAttempts` 0, event age 300 s, on-failure queue.
  The broker joins the safety TCB in SPEC §3.2.
- **`ecf-jobs`:** import, export, restore and backup merges, `rules test`, `ecf eval run` (`null`) and
  `/ecf-eval` preparation, and housekeeping (retention and re-stamp, digests, daily summary, model
  watch, scheduled export), all through `jobs.fifo`. A job writes a heartbeat to its progress item,
  stops at its deadline minus a reserve, saves progress and returns success; the tick re-queues a
  stopped job. Bundles are read whole (OD-620). Presigned URLs are short-lived and size-capped.
  `jobs.fifo` group = job kind; import, restore and export exclusive by a lease. Part of the trust
  root (SPEC §3.2).
- **`ecf-slack-in`** (public, import-light, its own Slack client with a 1 s timeout, boto3 clients with
  0.5 s connect and 1 s read timeouts and 2 attempts, an overall budget of 2.2 s): signature over the
  raw body; timestamp within 5 minutes and not in the future; `api_app_id`, `team_id` and (on Enterprise
  Grid) `enterprise_id` equal to the stack parameters `SlackAppId`, `SlackTeamId` and
  `SlackEnterpriseId`, set at `init` from the operator's app; dedupe by conditional put. Button values are typed (`g:<grant>:i:<item>`, `i:<item>`, `a:<address>`,
  `b:<batch>`, `n:<nonce>`; legacy v1 values on surviving cards are parsed by a tested table).
  **Cancel and Reject (OD-649)**: two strongly consistent reads, budgeted: `GetItem` on the `IDENT#`
  item, then one `BatchGetItem` for the person and role, the item (status, `grant_id`,
  `answer_pending`) and its card record (the button's `container.message_ts` must be the item's card);
  then the stop transaction (OD-649, with the identity and person checks; `MAINT` is read in the
  `BatchGetItem`, never checked in the transaction, OD-658); then a
  message to `slack-in.fifo` (group `stop:<grant>`, carrying the `response_url` and the person) so
  `ecf-worker` applies it; then the result ephemeral through `response_url` if the budget allows, else
  `ecf-worker` posts it (OD-651). The answer is mapped from `CancellationReasons` (OD-649). The
  transaction uses `ClientRequestToken` = the dedupe key, so a retry after a client timeout returns the
  first result; once it has committed the reply always reports it; a failed enqueue is recovered by the
  tick through the unapplied-stop index. The budget runs from API Gateway's arrival time
  (`requestContext.timeEpoch`): a call or retry is skipped when the time left is under its worst case,
  and the person is told to click again only if nothing was written. The stop decision is in a shared
  import-light module that M11's adapter reuses.
  Secrets and clients are made in Init. A form or window button gets `views.open` with a fixed "Checking…" view (`external_id` = the
  dedupe key). Every other click goes raw to `slack-in.fifo` (group = Slack user ID; Pause and Resume
  in one group per address; deduplication ID = the dedupe key); 200. If a write or send fails it
  answers with an error, never a silent 200. A `view_submission` is answered with `response_action:
  update` and a "Checking…" view; `ecf-worker` sends the result by `views.update` (the next view, or the
  form with an error section), so a refusal is shown rather than the form closing (OD-657).
- **Maintenance and holds (OD-655):** every function reads `MAINT` when a request, message or tick
  starts and does nothing while a hold is up; broker and job leases are taken only with a `ConditionCheck`
  that no hold is up, and their holders re-check between steps. A holder sets the hold, waits for the
  leases to drain, then waits 90 s more (the longest unleased timeout plus margin) before it reads or
  merges. `MAINT` names the holding job, whose lease the drain skips and whose messages `ecf-jobs`
  runs during the hold (OD-658). Exceptions to "does nothing while a hold is up": the holding job;
  `ecf-slack-in` (which reads `MAINT` in its `BatchGetItem`, accepts stops during scheduled-export and
  upgrade holds, and refuses Cancel and Reject during rollback and merge holds with the OD-658 answer);
  `ecf-worker`'s result ephemerals (OD-651); and, during a scheduled export, `slack-out` posts that
  write only live-kept records. A rollback or merge first pauses every address (OD-658). `ecf upgrade` disables the `slack-in.fifo` mapping during maintenance and, on
  exit, re-enables it and drains it before the tick resumes.
- **Laptop:** the CLI and Claude Code; the relay (0600 Unix socket for `ecf-mcp` and the status line,
  loopback TCP for OTLP only, session token required, `Host` checked, `Origin` refused, fixed route
  allow-list, the CLI's JWT added; one refresher with the CLI under a Keychain lock). Deployer and
  admin credentials are short-lived (OD-605).

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
  check; config apply + stage demotion. Counters use `ADD` under the condition. The shared
  conditional-write helper retries a `TransactionConflict` (DynamoDB conflicts any two transactions
  touching one item, a `ConditionCheck` included, and the SDKs don't retry it,
  `transaction-apis.html`, fetched 2026-11-05 (rv)) up to 3 times with jitter, for every writer, in both
  forms: a transaction's `TransactionCanceledException` with a `TransactionConflict` reason (told apart
  from `ConditionalCheckFailed` per `CancellationReasons` entry) and a plain write's
  `TransactionConflictException`; a lease take maps a conflict to "retry now", never "busy lease";
  conflicts are alarmed; Step 8 runs a burst (Approve all on 50 items during a tick sweep and renders)
  and records the conflict count and p99 write latency.
- **Storage versions:** `storage_version` per item (`schema_version` already means the classification
  schema); tolerant readers; `UpdateItem` on named attributes only; one-release lag for new
  attributes (a contract test); AWS exports stamp the release's SQLite schema number and upgrade
  items on read. The resumable-import concept is a **data epoch**, never `install.generation`.
- **Settings across stores** by a fixed key list; OD-368's "this computer's state" keys listed for AWS.
- **Round trip:** a fixture at mixed storage versions exported by both adapters gives byte-identical
  `tables/*.jsonl`; local → AWS → local equal modulo `origin` and new IDs; the
  copied-or-handled-apart test runs on DynamoDB too.
- **Bundle kinds and merge rules (round-6 F5, round-7 migration 5):** v1's export allow-list
  (`export_bundle.py:59-67`) and its exclusions (grants, jobs, leases, nonces, rate, Slack dedupe and
  calls, delays, claims, processing, DNS cache and others) apply to every kind unless the table below
  says otherwise. Per v1 table the mapping table adds the merge rule; the AWS-only records are fixed
  here:

  | Records | `scheduled` / `manual` | `migrate` | `rollback` | Restore or import merge |
  |---|---|---|---|---|
  | v1 allow-list tables | yes | yes, plus `slack_messages`, `draft_versions` | as `migrate` | `downgrade._merge` rules; per-table rule in the mapping |
  | people, roles, `IDENT#` identities | yes | yes | yes | OD-596, OD-622; `revoked` is sticky both ways (live or bundle); an identity whose `people_id` isn't on the target is dropped |
  | audit (`uid`) | yes | yes | yes | insert when `uid` absent, never delete |
  | grants, `GRANTUSE#`, `GRANTRES#` | no | no | no | `GRANT#` deleted (voided); attempt and outcome records kept |
  | `STOP#` stop records | no | no | no | pending ones applied before the merge, found by the merge's consistent Scan (OD-658); live-wins; an item whose live last transition is a stop's `cancelled` or `rejected` after the restore point (by the transition history, not by whether the stop record still exists) keeps its live status |
  | `AUDIT#SLACKIN#` rows | as audit | as audit | as audit | as audit |
  | nonce, `RATE#`, `MAINT`, leases, `TICK#`, dedupe; Slack pacing and hold records, per-post crash counts, cards' rendered versions | no | no | no | live kept |
  | `alert_outbox` (unsent alert email) | no | no | yes | the local restore queues it again |

  Import refuses the `migrate` and `rollback` kinds (only `migrate run` and the local rollback restore
  read them); `ecf upgrade` in AWS is refused while a migration is unfinalized.

### People, identity and roles (OD-579)

- **Model:** `people {id (random UUID), roles, admin, status: active | inactive, source_install}`;
  identities are separate `IDENT#` items (OD-652). Invariant: the admin holds the approver role on every address (on address add and
  on import). Only an admin changes a person's status, with step-up and a Security Notice.
- **Identities (OD-652, one store):** `IDENT#<issuer>#<subject>` `{people_id, kind: cognito | slack |
  os_user, status: pending | confirmed | revoked, confirmed_at, confirmed_by}`, a GSI by `people_id`;
  every status change (Confirm, relink, revoke, `migrate_link`, restore) writes only this item; Cognito issuer `cognito:<pool id>`; Slack issuer with the team (and
  enterprise) ID. A Slack member is `pending` until its DM Confirm click. **Authority:** a confirmed
  identity of an active person holding the role; anything else is refused and audited.
- **First person:** `ecf init` (without `--migrating`, which creates no person; its pending Cognito
  identity has `people_id` null until `migrate_link`) creates the operator's person (UUID, admin, active) and records the
  Cognito user's `sub` (not the username) that `AdminCreateUser` returned; that identity is confirmed
  once, at the first `ecf login` after TOTP enrolment whose `sub` matches, and never again by that path.
  The temporary password is shown in the terminal, never emailed (`MessageAction: SUPPRESS`).
- **Re-link (`ecf people relink`):** for a changed Cognito identity (new pool, recreated user) or OS
  identity (new Mac). It is on an allow-list open to an unlinked or pending identity. Authority is the
  backup key itself, not the export seed AWS holds (round 4): the laptop derives a separate **relink
  key** from the typed root (HKDF with its own info string) and signs a relink challenge; `ecf-api`
  verifies against the relink verify key, a deploy-set parameter written at `init` from the laptop; no
  part of AWS ever holds the relink key's private half. The challenge's canonical fields: domain tag
  `ecf/relink/v1`, the new identity's kind, issuer and subject (for Cognito the issuer names the pool
  and the subject is the `sub`), the old identity (issuer, subject), `people.id`, `install.id`, nonce,
  expiry; a cross-replay test (a relink signature refused as an attestation and the reverse). A Cognito
  step-up by the new identity, carrying the relink's nonce (one nonce, verified before it is consumed,
  as cutover), proves it holds the new TOTP; an OS-user relink uses v1's local step-up instead, and
  locally the typed root is checked against `export.key`'s OD-341 fingerprint (v1's `same_fingerprint`),
  so no relink key is stored locally. The old identity is revoked in the same transaction
  (status `revoked`, its refresh tokens revoked); a later restore of an older bundle doesn't bring a
  revoked identity back (the restore keeps the live identity table's revocations). Audited, with a
  Security Notice. Locally, a same-install restore under the typed key re-binds the OS-user identity.
- **`people.id`** on every grant, approval, answer, audit row and security notice; backfill rules as
  draft 2 (operator for OS-user and `slack:*`, null for `service` and `migration`); rows written
  before a migrate merge carry `people.id` null plus the Cognito `sub`, and
  `migrate.identities_linked` records the mapping readers resolve through (nothing is rewritten).
- **Per address:** approval mode `single`, approver count 1, `stepup.totp` `off` (M2's mode).
- **Export format 5:** people, roles and identities (`identities.jsonl`, OD-652) are bundle files; format 3 bundles no longer
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
  purposes (round 6, one table):** `migrate_link` and `migrate_secret`: a `pending` Cognito identity,
  install `migrating` only, nonce bound to the bundle SHA-256 (link) or the phase manifest (secret) and
  that `sub`; `relink`: an unlinked or pending identity plus a valid relink signature; every other
  purpose: a confirmed identity of the active approving person. `MFA_SETUP`, `SOFTWARE_TOKEN_SETUP` and
  `NEW_PASSWORD_REQUIRED` are refusals. Verify limit via `RATE#`. SPEC §9.6 gains `migrate`,
  `migrate_link`, `migrate_secret`, `relink`.
- **Attestation and relink signatures (round 6):** versioned domain tags (`ecf/relink/v1`,
  `ecf/migrate-secret/v1`) over a canonical encoding (phase, pool ID, pending `sub`, `install.id`,
  target generation, nonce, expiry, a random per-manifest salt, sorted entries); `ecf-api` issues one
  nonce per phase, used by both the attestation and the `migrate_secret` step-up, and keeps it as an
  outstanding record (phase, pending `sub`, issued time, TTL at most 10 minutes); it accepts only an
  outstanding nonce it issued and caps expiry at issued time + TTL, so an attestation from before a
  `destroy` and re-init is refused. Each manifest entry is an HMAC-SHA256 of the value keyed by the
  salt, never a plain hash (a human-chosen IMAP password's plain SHA-256 would be a cracking oracle);
  `ecf-api` keeps only a written or failed flag per entry, never the MAC. One batch route per phase, in
  this order, as v1 (`stepup.py:173-207`): verify with no writes (the signature, the step-up token's
  `client_id`, scope, `ecf_nonce` and `sub`, and each value's MAC against its entry); then one
  `TransactWriteItems` consumes the nonce and the `jti` conditionally (a concurrent second batch is
  refused); then the Secrets Manager writes, each outcome recorded. Secrets Manager writes aren't
  transactional, so a partial batch is finished by a new batch (fresh nonce and step-up) that writes
  only the entries not marked written. Before cutover the CLI signs values typed at its own hidden
  prompts; at cutover the CLI signs the held service's manifest and the service sends exactly those
  values (OD-653). The modules the CLI needs move to `ecf` (OD-653), each re-exported by its
  `ecf_server` name: key-text parsing and `derive` (`backup_key.py`), the age facade (`_age.py`, one
  facade, SPEC §17.2), the relink derivation, and bundle verification (`export_bundle.verify`), with
  known-answer vectors. The test that the backup root never reaches the held service covers cutover
  only; rollback's local restore sends the typed key over the socket as v1 does (`cli_import.py:90-93`). Before the step-up the operator sees
  the phase, each kind and target by name, the count and the OD-341 fingerprint, and confirms.
  `migrate run` refuses unless the mailbox secrets written equal the bundle's addresses.
- **Slack identities:** `IDENT#` items as above (OD-652); one shared function builds the Slack issuer (team and enterprise) for
  `ecf-slack-in`, `ecf-worker` and the Confirm path, with contract tests.
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

- **DynamoDB, five tables:** main (items keyed `ITEM#<id>` and their records; `STOP#<grant>` stop records and
  `AUDIT#…` rows, `AUDIT#SLACKIN#…` for `ecf-slack-in`'s, each prefix a partition key so
  `dynamodb:LeadingKeys` can scope it; the `MAINT` item; a sparse GSI on `stopq` for stops not yet
  applied); `ecf-api` table (settings, people, `IDENT#` identities with a GSI by `people_id`, sender
  confirmations, nonce and `RATE#` records); grants (grants, `GRANTUSE#` attempt records and `GRANTRES#` outcomes, each its own
  partition key, OD-624); excerpts; the Slack table (dedupe keys, TTL 1 day, written by
  `ecf-slack-in`, no PITR). PITR and deletion protection on the first four.
- **Expiry:** no TTL at creation; the terminal transition sets `expires_at` = terminal time + item
  retention on the item and its excerpts when prunable (never fraud, weak-fraud, regulator,
  quarantined); item retention stays its own step-up setting, re-stamped by an `ecf-jobs` job on
  change; CloudWatch log retention separate; `AUDIT#` rows never get a TTL (OD-217; round 6); readers and
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
- **Before the change set:** `ecf upgrade` sets the maintenance flag, disables the `slack-in`
  mapping, waits for leases to drain (OD-379), then takes on-demand backups of all four PITR tables.
- **Consistent exports (round 4 S8, OD-646, OD-655):** a scheduled or rollback export sets the
  maintenance hold, waits for leases to drain and then the 90 s grace (OD-655), and reads the live
  tables with strongly consistent parallel Scans while the hold lasts (DynamoDB gives no snapshot across
  a Scan, only per item, API_Scan.html, fetched 2026-11-05 (rv); the hold and grace are what make the
  read consistent: no writer can still be running). A rollback export, which always takes this path,
  applies the pending stop records its Scan finds and reads their items again before writing (OD-658);
  rows written after the hold began are listed. A scheduled export doesn't look for stops (they aren't
  exported); when Step 8's measured Scan time passes its line (with a margin against flipping), it
  records a point in time T after the grace instead, lifts the hold, and reads scratch copies restored
  by PITR to T. The scheduled export re-checks the age recipient's fingerprint against the expected
  value in the seed secret (OD-660) and alarms on a change. The migrate export is local, not one of these.
- **`ecf upgrade --to <previous>`:** code only; data forward-compatible.
- **Restoring:** never by swapping tables. `ecf-jobs` restores all four tables to scratch tables
  (from the upgrade backups, or by PITR with one `RestoreDateTime` so they share a point), then merges
  into the live tables by `downgrade._merge`'s rules (`sent`, `threads`, `senders`, `gate` kept from
  live; grants deleted; executing items to `failed_unknown`; cursors rewound), per-table rules from the
  mapping, including rows written after the backup (kept when the mapping marks them live-wins,
  dropped otherwise, each listed in the job's report); then deletes the scratch tables. Restores are asynchronous; the job resumes by polling
  `DescribeTable`. Rights: `CreateBackup`, `DescribeBackup`, `ListBackups`, `DeleteBackup`,
  `RestoreTableFromBackup` (from `table/<live>/backup/*`), `RestoreTableToPointInTime`, and full item
  rights on `ecf-<install>-scratch-*` only.

### Queues, timers and delays

- **SQS FIFO:** `slack-in.fifo` (group = Slack user ID; Pause and Resume one group per address; stop
  messages `stop:<grant>`; batch 1), `slack-out.fifo` (first posts group = channel ID; edits of a
  posted card `card:<item>`; `views.update` and ephemerals in `view:<Slack user ID>`; the dead-man
  message its own group; OD-651), `jobs.fifo` (group = job kind; content-based deduplication on,
  which Scheduler requires for a FIFO target, and every API-role send sets an explicit deduplication
  ID = job ID + chunk; Scheduler sets `MessageGroupId` per schedule; SSE-SQS). `maxReceiveCount` 5 on
  `jobs.fifo`, 1,000 on the two Slack queues (their crashes are counted in DynamoDB, OD-651);
  visibility 6× the consumer's timeout; `MaximumConcurrency` 2 per mapping. `ecf-jobs` deletes its
  message once its progress item and heartbeat are written, so a crash doesn't hold its group for
  90 minutes; the tick is the only retry path, and an out-of-memory or timeout kill alarms. `slack-in.fifo`, `slack-out.fifo` and their DLQs keep messages 4 days (OD-651), SSE on;
  DLQ tools never print bodies.
- **Waits:** every scheduled wait is a `due_at` acted on by the tick. A `slack-out.fifo` hold sets the
  message's visibility and keeps it at the head of its group (OD-645); SQS counts every receive, hence
  1,000 (OD-651). Whether a visibility the handler set survives Lambda's batch-item failure report is
  unverified (the Lambda pages say only that failed messages return after the visibility timeout):
  Step 8 tests it.
- **Clicks:** `ecf-worker` verifies the HMAC and judges the 5-minute window against SQS
  `SentTimestamp`. SPEC §10.1 gets a closed table of every v1 click handler (21 today) marked
  risk-raising or risk-lowering, risk-raising by default, with a test that every registered handler
  (and every M11 action) is in it. A risk-raising click older than 10 minutes by `SentTimestamp` is
  refused, audited, and the person gets "click again"; risk-lowering clicks are processed whenever
  they arrive, auditing the original time. The dedupe key stops replays; each grant's conditional
  update stops double clicks.
- **Forms and windows:** `ecf-worker` checks identity and role, audits, then sends `views.update` with
  the real view, or "not allowed"; every refusal or failure path sends a final "expired, click
  again"; a click whose `views.open` failed gets an ephemeral; an update after the user closed the
  view counts as done.
- **Cancel and Reject (OD-649):** as v1: Cancel only while `delayed`, Reject only while awaiting
  approval or step-up, each against the item's current grant. The stop record `STOP#<grant>` and the
  tick's `delayed → executing` (checked for a pending stop) are serializable transactions, so exactly
  one wins; every other move out of those states checks for a pending stop too, except the resolve
  edges (OD-656, OD-658). `ecf cancel` and `ecf reject` run v1's
  transitions in `ecf-api` with the same conditions. No handover record; SPEC §6.2 is unchanged.
- **Holds:** a paused address or tripped send limit holds an action without counting an attempt.
- **EventBridge Scheduler:** the tick on `ecf-worker`; housekeeping schedules send to `jobs.fifo`;
  `MaximumRetryAttempts` 0 with the one shared standard SQS DLQ; the tick heartbeat (a custom metric) alarms when the
  tick runs fewer than once in 5 minutes.

### Slack over a request URL (OD-609)

- **Install** by hand; the signing secret pasted from Basic Information (hidden prompt) at `init`, at
  `migrate run` and by `set-tokens` (step-up, Security Notice); `doctor` checks it. No token rotation.
  `apps.manifest.update` uses a fresh configuration token: on a migrate it is written as a secret in
  the pre-cutover phase (OD-642) and deleted right after the switch (so 7 secrets, plus the one-time
  age identity, while a migration runs); elsewhere the operator pastes it at the switch.
- **Signature and replay:** HMAC-SHA256 over `v0:<timestamp>:<raw body>`; dedupe key = SHA-256 of
  timestamp + raw body, conditional put first, TTL 1 day.
- **Public route:** route throttle (burst 20, rate 10 a second); signature before any parse; body cap,
  413; 401 with no body; signing secret re-read at most once per container per 5 minutes and only for
  a fresh timestamp; access logs `$context.*` only; alarm on signature failures. No WAF on HTTP APIs
  (SPEC §12.2).
- **3-second budget:** `views.open` happens before the 200 and counts in it. Reserved concurrency on
  `ecf-slack-in` = the route burst, 20 (round-6 U4, written only in draft 8); raised if Step 9 measures
  more concurrent cold clicks, never lowered below the burst; above the latency line, SnapStart or provisioned
  concurrency 1.
- **Switch on migration:** AWS deployed and answering first; the local service held; mode-aware
  `manifest()`; the app-level token revoked. Clicks fail visibly in Slack from the local hold until the
  switch (stated in the runbook). Rollback to Socket Mode needs a new app-level token made by hand on
  Basic Information (no API found: unverified).
- **Chat seam for M11:** `ChatInbound` with `verify`, `parse` and `respond(Verified) → Ack`, `Ack` =
  `Empty | OpenPlaceholder | OpenForm(Form) | InterimCard(Card) | Decided(result_text, card | None)`, chosen by
  capability: Slack uses `OpenPlaceholder` and fills the form by update; an adapter with `form_in_ack`
  (Teams, whose dialog must be in the `task/fetch` invoke response) needs a synchronous, role-checked
  "build form" route from its public function to `ecf-api` (`POST /v1/chat/form`, IAM-authorized,
  specified now, built in M11). Cancel and Reject are decided synchronously (OD-649) and return
  `Decided`: Slack answers through `response_url`; Teams (no `response_url`) answers with a text
  message in the invoke response and leaves the card to `ecf-worker`'s update, since whether a
  replacement card shows to everyone is unverified (OD-657); the stop decision is the shared import-light module.
  Every other decision stays asynchronous; the final card comes by update. Neutral handler registry; opaque `RouteRef.channel`; `FakeChat`
  variants with every capability off and with `form_in_ack` (a test returning `Decided` in the Ack);
  routes `/chat/<surface>`. Typed button values and the legacy-value table are built in Step 2.

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
  install, compared with Step 10(c)'s plan-provider run on the same install and with 1b under the one
  rule (`--margin 5`): 0 unsafe, fraud guard N/N, `compare` showing no input difference except
  "different provider"; the 10-case determinism repeat on named case IDs, passing when it flips at most
  one more field than 10(c)'s repeat and than 1b's.
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

### Evals in AWS mode (OD-611, OD-617, OD-618, OD-643)

- **The set:** the release zip carries the built public part of the synthetic set; the private part
  (the adversarial subset, `eval/private/synthetic/` on the laptop, OD-618) is uploaded with `ecf eval
  upload-private` (laptop, step-up). The details are in "Moving from local mode to AWS", the gate's
  set: the upload is refused unless the laptop's public part equals the installed release's packaged
  version; `ecf-jobs` recomputes the merged version from the packaged and uploaded bytes with the
  shared merge (`ecf.eval`, OD-648), accepts it only when it equals the laptop's, and writes it to
  DynamoDB, where the gate reads it. The gate's fingerprint is (packaged set version, upload ID).
- **The gate guard (OD-643):** a gate run records the verified merged version and its membership
  counts (`public_cases`, `private_cases`, `private_injection_cases`, all above 0); a run on any other
  version doesn't count. A stale or missing upload means no synthetic pass, so OD-603 refuses a Bedrock
  session that names a `live` address.
- **Stated limit:** the laptop's public part must be at the installed release's version for any upload
  or re-verify, so the operator keeps a dedicated checkout at the release tag (Steps 1b, 10, 11 use the
  pinned one). A new public card or label counts on AWS only after the next release; a new private one
  after a re-upload.
- **Release build:** fails unless every `labels.jsonl` row's file (the `.build/` cases included) is
  present with a matching sha256, and unless the packaged set version equals the version the build was
  asked for (`--eval-set-from <checkout>`; for dev builds used in Steps 10(c) and 11, the pinned public
  commit recorded in SPEC §16).
- **Runs:** run state, claims and outcomes in DynamoDB; `ecf eval run --backend null` and `/ecf-eval`
  preparation are chunked `ecf-jobs` work; result files go to S3 `eval/` and `ecf eval pull <run>`
  brings one to the laptop for `compare`, rescore and `--source-run` (result files are in §12.4's and
  retention's lists).
- **Not in AWS mode (OD-617):** Jev, the Mistral judges, set generation; no TypeSafe or Mistral key in
  AWS.

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
  processing; DynamoDB stores what SQLite stores locally; S3 holds audit logs (no content),
  encrypted export bundles and eval result files and the uploaded private subset (synthetic text);
  SQS carries IDs except `slack-in.fifo` (the raw Slack click, with its `response_url`) and
  `slack-out.fifo` (outgoing views, ephemerals and DMs, which can include subjects, senders, excerpts
  and draft text; cards go by reference), each kept at most 4 days (OD-651);
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
- **Concurrency (OD-610):** quota at least 100 + reserved total (`ecf-slack-in` 20, the route burst;
  the trigger 1: 121); `init` requests the increase and waits; `doctor` checks.
- **Boundary:** an exact Allow list for runtime roles, with a CI check that every AWS call the code
  makes (collected from the moto tests) is allowed by both the role (per the table above) and the
  boundary, and a `dynamodb:Attributes` bug-guard test for the broker and `ecf-slack-in` (a call naming
  an attribute outside the list is denied) and a CI test that every `UpdateItem` carries
  `attribute_exists` on its key (IAM can't stop an update from creating an item):
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
  - Lambda `InvokeFunction` on the broker (for the API roles); Cognito `AdminUserGlobalSignOut`; Bedrock
    `GetInferenceProfile`, `GetFoundationModel` and the retention read (for the model-watch job);
  - explicit Deny on control-plane actions (Lambda code, configuration, permissions, mappings,
    function URLs, concurrency; `sqs:SetQueueAttributes`; secret resource policies;
    `dynamodb:UpdateContinuousBackups`, `DeleteTable` except scratch; `logs:DeleteLogGroup`;
    `cognito-idp:UpdateUserPool*`; `bedrock:PutAccountDataRetention`; `scheduler:*Schedule`;
    `iam:PassRole`). This boundary applies to runtime roles; Scheduler roles have their own (above);
    neither applies to cfn-exec. Both boundaries ship only in the admin-deployed base stack.
- **cfn-exec:** under `/ecf-deploy/<install>/`; `iam:PassRole` only to `lambda.amazonaws.com` and
  `scheduler.amazonaws.com` for `role/ecf/<install>/*`; trust with `aws:SourceAccount` and `aws:SourceArn`
  `ArnLike stack/ecf-<install>-app/*` (confirmed in Step 8); denied user, key, login-profile and policy
  creation, function URLs with auth `NONE`, and `lambda:AddPermission` unless `lambda:Principal` is
  `apigateway.amazonaws.com`. Restored from the plan's §10 (dropped by draft 4 without a note, round 4
  S4): cfn-exec may create or update a role under `/ecf/<install>/` only with
  `iam:PermissionsBoundary` equal to the install's boundary (and under `/ecf-sched/<install>/` only with
  the Scheduler boundary), on `CreateRole`, `PutRolePermissionsBoundary`, `AttachRolePolicy`,
  `DetachRolePolicy`, `PutRolePolicy` and `DeleteRolePolicy`, and may never delete or detach one; PassRole
  to `scheduler.amazonaws.com` only for the Scheduler path. The plan's self-protection Deny is
  restored: `iam:CreatePolicyVersion`, `DeletePolicy`, `DeletePolicyVersion` and
  `SetDefaultPolicyVersion` on both boundary policies, for cfn-exec, the deployer policy and every
  runtime role. CI fails on any Lambda permission without `SourceArn`, any resource policy with
  `Principal "*"` and no condition, and any runtime role without its boundary. Grants-table writes
  are Allow-style (Who may call, "What IAM enforces").
- **Write-once keys:** the export seed and age recipient are written by the laptop at `init` and
  migrate with the deployer credentials it already holds: the seed as a secret, whose resource policy
  denies `PutSecretValue`, `UpdateSecret` and `DeleteSecret` to every runtime role; the age recipient
  (public) and the relink and export verify keys as CloudFormation parameters passed to the functions'
  environment,
  kept by `UsePreviousValue` on upgrade; `doctor` and `export --pull` re-check the OD-341 fingerprint
  (unchanged: the age recipient and the Ed25519 key, `backup_key.py:12-14`) and a separate relink
  fingerprint every time. **Rotation in AWS mode (OD-660):** in the CLI only; v1's `export_key` step-up;
  the rotation recorded as pending; the new seed (with the expected fingerprints) written as a pending
  version; one deploy replaces the recipient and the relink verify key and adds the new export verify
  key to `ExportKeysPrevious` (kept only to verify older bundles); `doctor` confirms the fingerprints.
  Refused until `migrate finalize`. OD-616's "through `ecf-api` into Secrets Manager" is amended by
  these laptop writes.
- **CI:** GitHub OIDC trust on `aud`, `sub = repo:<owner>/<repo>:ref:refs/heads/main` and the
  validator workflow's `job_workflow_ref`; `cfn-policy-validator` on pushes to `main`; static policy
  tests on pull requests; dedicated empty account.
- **Detective controls:** EventBridge rules on CloudTrail management events, through an input
  transformer (metadata only), to the unencrypted `-direct` topic (CloudWatch and EventBridge can't
  publish to an `aws/sns`-encrypted topic; its policy admits `events.amazonaws.com` with the rules'
  `SourceArn`). Events: CloudFormation `UpdateStack`, `ExecuteChangeSet`, `CreateChangeSet` and
  `DeleteStack` on the install's stacks (a stack update can swap the verify keys or the recipient);
  Lambda code, configuration, permissions, function URLs, event source mappings
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

Alarms (34 metrics, 10 free): `ecf-api`, `ecf-worker`, `ecf-broker`, `ecf-jobs`, `ecf-slack-in` errors
(metric math, 2 each: 10) and throttles (5); 3 FIFO DLQs; 2 on-failure queues (`ecf-broker`,
`ecf-worker`; tick failures also reach the Scheduler DLQ); the one shared Scheduler DLQ;
`DestinationDeliveryFailures`, `InvocationsFailedToBeSentToDeadLetterCount` and
`InvocationDroppedCount` (3); `TransactionConflict` and a hold with no live holder for 30 minutes (2);
an age-recipient fingerprint change (1); 8 custom metrics, emitted through Powertools' embedded metric format
(no `PutMetricData` calls) and inside CloudWatch's 10 free custom metrics (aws.amazon.com/cloudwatch/pricing,
fetched 2026-11-05 (rv)): the tick heartbeat, a user without TOTP, Slack signature failures, the
stop-record rate and invalid stop records (OD-649, OD-656); `jobs.fifo` oldest age; the Cognito
trigger. Whether metric-math alarms count toward the 10 free alarm metrics is unverified.
Secrets: 3 mailboxes, Slack bot token, Slack signing secret, export seed: 6 (the age recipient is a
stack parameter; the models key is dropped, the model watch reads Bedrock only); while a migration runs,
also the Slack configuration token and the one-time age identity (8).
`ecf-api` and `ecf-worker` run at 512 MB; `ecf-jobs` at the measured memory with a 900 s timeout.
Broker runs: 3 addresses at v1's default intervals (every 10 minutes in business hours, 08:00-17:00
on weekdays, every 30 minutes otherwise; `schedule.py:38-50`) = about 6,700 a month, plus
approval-started and alert-drain runs: about 8,000.

| Item | Idle | 300-500 emails a day, 3 addresses |
|---|---|---|
| Secrets Manager, 6 at $0.40 | $2.40 | same |
| CloudWatch alarms, 34 metrics (10 free, $0.10 each); 8 custom metrics in the free tier | ~$2.40 | same |
| CloudTrail (first management copy free) and its S3 | $0.10-0.20 | same |
| DynamoDB on-demand, PITR, under 1 GB | ~$0.10 | $0.20-0.40 |
| SQS: idle long polling by 3 mappings with `MaximumConcurrency` (which disables Lambda's poller reduction), est. 1-2M requests a month, 1M free; FIFO rate not rendered | $0-0.50 (unverified; Step 8 measures) | same |
| Lambda: worker tick 43,200 invocations at 512 MB for about 1 s = 21,600 GB-s; broker ~8,000 a month at 2-3 GB for 10-20 s = 160,000-480,000 GB-s; `ecf-api`, `ecf-slack-in`, the Slack consumers and `ecf-jobs` under 10,000 GB-s (estimate; Step 8 measures) | $0 in the free tier | $0 to about $1.90 over the 400,000 GB-s free tier; $3.20-8.55 if billed (x86 rate; Arm not rendered) |
| Daily scheduled export (OD-650): strongly consistent reads, about $1 per GB of table data a month (1 RRU per 4 KB at $0.125 per million, aws.amazon.com/dynamodb/pricing/on-demand, fetched 2026-11-05 (rv); whether a Scan is charged by summed item size is unverified); the PITR path $0.15 per GB per restore, about $4.50 per GB a month | ~$0.05 | $0.10-0.40 at 0.1-0.4 GB |
| CloudWatch Logs, Scheduler, SNS, SSM, S3 audit writes, API Gateway, EventBridge rules | ~$0.10 | ~$0.15 |
| Cognito Essentials | $0 | $0 |
| **Infrastructure** | **about $5.15-5.75** | **about $5.35-6.45 in the free tier; up to about $8.35 over it; up to about $15.00 if all Lambda is billed** |
| If Step 9 needs it: provisioned concurrency 1 on `ecf-slack-in` at 512 MB | +~$5.40 (no free tier with it) | same |
| **Bedrock, when chosen** | $0 unused | **about $415-825**; a full gate run about $20 |

Step 8 runs the stack idle for 24 h with schedules on and records requests, GB-s and cost.

### Moving from local mode to AWS (`ecf migrate`, OD-602, OD-614, OD-616, OD-621)

- **Before:** `ecf init --mode aws --migrating` deploys the stacks; the operator types the backup key on
  the laptop, which derives the seed, the age recipient and the verify keys (OD-616); the laptop writes
  the seed as a secret with the deployer credentials and deploys the recipient and verify keys as
  parameters ("Write-once keys"); the operator confirms the OD-341 fingerprint. `migrate plan` and
  `migrate run` refuse unless the local `export.key` fingerprint equals the deployed one, and local
  `ecf export keys rotate` is refused while a migration is pending (a rotation changes the root and
  would break the attestations and the bundle signature, `export_keys.py:3-7`). The AWS install allows only `migrate`, `login`, `doctor` and the
  migrating-only `set-tokens` (signing secret, configuration token) until the migration finishes; its
  Cognito identity is `pending`. Refused when busy, as `ecf upgrade` (OD-379).
- **Secret writes while migrating (OD-642, OD-653):** each phase's secret writes (pre-cutover: signing
  secret, configuration token; cutover: mailbox passwords, bot token) need one attestation signed by
  the CLI with the relink key over the phase's manifest (at cutover, the manifest the held service
  built from the values it will send), verified by `ecf-api` against the relink verify key, and one
  Cognito step-up (purpose `migrate_secret`, pending identity, migrating install only) carrying the
  same nonce. On `init --migrating` the first-login confirmation is disabled and spent:
  the Cognito identity is confirmed only by `migrate_link` (step 7).
- **Audit across migrate and rollback (round 4 S7, round 5, round 7):** every audit row gets a source
  (`install.id`, `install.generation`) and a `uid`; a SQLite migration (Step 0) adds `source` and `uid`
  columns with a unique index (`id` stays the local order). `uid` is an opaque string (OD-659): rows
  that exist when the migration runs get `<install.id>:<id>` (the setting is JSON-quoted, so the
  migration uses `json_extract`; plain SQL); every row written later gets a random one,
  `lower(hex(randomblob(16)))`, from an `AFTER INSERT` trigger when the writer left it null (v1 has about
  90 audit inserts, some in SQL migrations). An import computes `<bundle install_id>:<id>` for bundle
  rows that carry no `uid` before anything else writes to its staged database, and the staged
  migration's backfill covers only ids up to the bundle's highest audit id, so rows the staged
  migrations insert get random ones. `importer.copy` inserts audit rows with
  `INSERT … WHERE NOT EXISTS` on `uid` (today an `IntegrityError` refuses the whole import,
  `importer.py:402-419`). Tests: no row has a null `uid`; an import or restore followed by 1,000 writes
  gives no UNIQUE failure and skips no source row; the same old bundle imported twice gives the same
  derived `uid`s. DynamoDB rows get a ULID; `uid` is compared for equality. The migration is added to `CHECKPOINT_AFTER` (`db.py:111`). Every readable
  bundle (formats 4 and 5, `bundle_reader.py:50`) carries `install_id` in its header, manifest and
  settings; import checks that the manifest's and the settings' values agree. Stated limit: rows imported before M1 carry the
  importing install's `uid`, so re-importing a later bundle from their original install duplicates
  them. Bundles carry `uid`; imports and restores
  insert a row only when its `uid` is absent, under a new local `id`, and never delete audit rows
  (restore's `DELETE FROM main.audit` for an empty target is removed); `flushed_at` is machine-local
  (not in bundles; the migration sets it for rows up to the old flushed id; a partial index on rows
  not yet flushed); `source` is the writer and never rewritten, `origin` stays the import marker;
  `audit.jsonl` is ordered by (time, `uid`); `AUDIT#` rows never expire (OD-217). Step 0 times the
  migration on a 2-million-row audit table.
- **On `init --migrating`:** no person is created; the pending Cognito identity is unattached until
  `migrate_link` attaches it to the bundle's person; a test checks exactly one admin after the link.
- **The gate's set in AWS (round 4 S9, round 5):** migrate never copies the laptop's `eval_root`. The
  AWS set is the packaged public part plus the private part uploaded by `ecf eval upload-private`
  (laptop, step-up): it refuses unless the laptop's public part equals the installed release's
  packaged set version and the private set passes the same clean-and-pushed check as a for-gate run
  (no uncommitted changes, HEAD contained in its remote branch; the upload records the private
  commit), and `ecf-jobs` recomputes the merged version from the uploaded bytes (every
  file's sha256 checked) and accepts it only when it equals the laptop's merged version; it also
  computes the membership counts with the shared merge and marker constant and refuses an upload with 0
  injection cases. The guard is the merged version; an upload is identified by its bytes' hash, so
  re-uploading the same bytes gives the same upload. The merged
  set is built in memory per run (the package is read-only and nothing is written to `/tmp`); the
  gate's fingerprint is (packaged set version, upload ID). Each run records both, and every container
  checks its rebuilt merged version equals the run's before serving a case; uploads are immutable by
  hash and kept while a run or gate row refers to them; `ecf-jobs` writes the verified merged version
  to DynamoDB, which the gate reads (never S3). `ecf upgrade` compares the old and new packaged set
  versions and, when they differ, warns, lists the addresses whose latest gate run is on the old
  version, and requires `upload-private` to be re-verified against the new public part. The overlay's stamp compares only the two source set versions
  (the private commit is informational), so migrated runs keep counting. `migrate plan` lists any
  address whose latest gate run used a set version other than the AWS set's. Cutover step 9 is
  preceded by the upload and its verification.
- **Grants after an import or restore (round 5):** in AWS the import marks waiting items "reissue
  pending"; `ecf-worker` issues the grants (with `people.id`, source `import`) and posts the cards once
  the hold lifts; a CLEAR of the grants table removes `GRANT#` items only, never attempt or outcome
  records.
- **The local `migrating` hold** (new, built in Step 6c): a persisted service state that survives a
  restart and is cleared only by `migrate abort`, `migrate finalize` or a restore; it refuses mail
  checks and Slack and allows export, step-up, the Keychain read and the transfer under the CLI's
  attestation (OD-653), the dead-man message's delete, and restore. Its
  Security Notices are queued and posted from AWS after the switch (or, on abort, locally).
- **Cutover order:**
  1. pause every address; drain `slack_out` and `alert_outbox`; post "fraud flagging off since T" to
     Slack;
  2. put the local service in the `migrating` hold;
  3. local step-up (purpose `migrate`) bound to the migrate plan's hash (`install.id`, the target
     stack, the one-time recipient's fingerprint);
  4. export the migrate kind (a new bundle kind; it also carries `slack_messages` and
     `draft_versions`), encrypted to the one-time recipient `ecf-jobs` publishes for this job; the
     laptop verifies the bundle under the typed key and records its SHA-256;
  5. the held local service transfers the mailbox passwords and the Slack bot token to `ecf-api`
     (write-only), under the phase's cutover attestation signed by the CLI and one `migrate_secret`
     step-up carrying the same nonce (OD-653);
  6. `migrate run`: `ecf-jobs` decrypts with the one-time identity (then deleted), checks the bundle
     against the stored verify key and imports it under restore's rules: same `install.id`,
     generation + 1, install name, routes, alerts, identity links, the 7-day restored-keyword guard;
     the AWS `migrating` hold keeps every address from fetching;
  7. `migrate_link` step-up by the pending Cognito identity, bound to the same SHA-256: the bundle's
     person and the Cognito identity become one person (local `people.id` kept, the Cognito identity
     confirmed, the Slack identity kept); `migrate.identities_linked` and a Security Notice;
  8. **only now** the local unit is uninstalled (plist removed, data kept), which also cancels the
     dead-man message (OD-621);
  9. upload and verify the private eval part; switch the Slack manifest; edit old cards to "moved";
     `ecf-worker` reissues open approvals with fresh grants and posts their cards;
     lift the AWS hold;
  10. resume each address only after its own mail check passes (OD-366); the first AWS check covers
      mail from the gap.
- **`ecf migrate abort`:** at any point before step 8, clears the AWS install (maintenance, every
  address held, mailbox secrets' access removed), clears the local hold and restarts local checking.
  Abort returns the Cognito identity to `pending` and unattached (after step 7 it was confirmed and
  linked), so a re-run's `migrate_link` works. A later `migrate run` deletes the aborted run's progress
  items and on-demand backups, clears every table except the install's keys, the pending identity and
  the `AUDIT#` rows (the aborted run's step-ups, transfers and Security Notices stay; the new import
  merges audit by `uid`), and imports the new bundle at generation max(the bundle's, the highest AWS
  has seen) + 1.
- **Rollback (requested within 7 days of step 6's `restore.at`; refused after; no cancel, OD-658):**
  every address is paused (v1's pause holds delayed sends and actions); a running scheduled export is
  stopped at its next page and its partial object deleted, so the rollback takes the lease; the hold
  (kind `rollback`, naming the rollback job) is set, schedules disabled and mailbox access removed; the
  leases drain, then the 90 s grace; the rollback job's consistent Scan finds and applies every pending
  stop and reads those items again; then the rollback-kind export (the holding job, so not held) is
  pulled to the laptop; the local unit is reinstalled and comes up in the `migrating`
  hold; `ecf restore` of that bundle under the backup key (same `install.id`, generation max + 1, the
  7-day guard); the hold is released; Slack goes back to Socket Mode with a new app-level token made by
  hand; cards re-edited. Only the rollback-kind bundle is accepted.
- **Re-migrating after a rollback:** `ecf destroy`, then `init --migrating` and the steps above;
  generations keep rising (N, N+1, N+2, N+3).
- **`migrate finalize`:** refuses while the local plist exists or the unit is loaded; a CLI-driven
  call to the kept local service (started held) removes its Keychain secrets; ends the rollback
  window. After day 7, `doctor` escalates an unfinalized migration to Operator Input Needed.
- **Lost, and said in the runbook:** on a rollback, `slack-out.fifo` posts not yet sent (unsent alert
  email is carried by the rollback kind); queued jobs other than the drained ones, pending step-ups,
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
  `global.` profiles (OD-604); the audit TTL and `logs/` expiry. (The plan's `response_url` replies,
  dropped in draft 1, came back for the Cancel and Reject result in OD-644 and OD-651.)
- From drafts 2 and 3: `ecf-slack-out` and `ecf-liveness` as functions, grant signatures (OD-612) and
  the Cancel marker (OD-615); from draft 4: "60 s after `due_at`", S3 config, signing relinks with the
  export seed; deriving keys in AWS (OD-616); Jev, Mistral and generation in AWS
  (OD-617); streaming bundles (OD-620); from draft 5: the in-flight flag and the `cancel:<item>` group
  (OD-640), the shared API role (OD-641), the local step-up during migrate (OD-642), per-command
  socket timeouts as a deadline (draft 5's dropped SQS-visibility holds came back in OD-645); from
  draft 6: `ecf-slack-in`'s direct status writes (OD-644), delete-and-re-send holds (OD-645), the
  default daily PITR export (OD-646); from draft 7: per-item stop records, the `HANDOVER#` record,
  Cancel during a retry wait and `executing → cancelled` (OD-649), the deploy-time rollback switch
  (OD-650), 125 receives and 1-day queue retention (OD-651), nested identities on the people item
  (OD-652), the held service signing at cutover (OD-653); from draft 8: the `MAINT` check on every
  write and `moving_back` set before the hold (OD-655), the apply-time role re-check and the index used
  for completeness (OD-656), `response_action: errors` for form refusals (OD-657), plain SHA-256
  manifest entries and two nonces per phase, the extended OD-341 fingerprint; from draft 9:
  `moving_back`, the grace-period send case and `rollback --cancel` (OD-658), stops searched by
  scheduled exports and on the PITR path (OD-658), `audit.uid_prefix` and the `import:` prefix
  (OD-659), the previous-keys list for the recipient and relink key (OD-660), `ecf-jobs`' grant
  `status` update (OD-658), a stored local relink key, "1b's dev service" for Step 12.

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
   `people relink` locally, the `identities` table, OD-652); the grants table and per-attempt spend
   records with v1's retry; the broker-owned records (probe, backfill progress, draft tracking,
   auto-pause requests); the audit `source` and `uid` migration, trigger and `audit.uid_prefix`; the
   bundle-kind table's rules; `answer_pending`, `grant_id` and `card_note` as item attributes (a SQLite
   migration); the shared transition helper's stop check (a no-op in local mode, which has no stop
   records) and the contract test over SPEC §6.2's edges out of `awaiting_*` and `delayed` (OD-656).
   Ships nothing. *Pass:* full suite green with SQLite behind the repositories; every repository method
   and every in-lease transition mapped; a format-4 bundle imports with a synthetic inactive person; an
   import into a fresh install can still approve; no audit row with a null `uid` after the migration and
   after every v1 write path; an import or restore followed by 1,000 writes gives no UNIQUE failure and
   skips no source row; the same bundle imported twice gives the same `uid`s; every grant writer sets
   `grant_id`; the migration's time on a 2-million-row audit table recorded.
   **0b. Bedrock pre-checks (go-ahead, read-only):** `allowed_modes`, model access, Haiku 5.5 ID form,
   Region. *Pass:* every pin allows `none`, and `GetAccountDataRetention` reads `none` in every
   destination Region of each pinned `us.` profile (Regions read with `GetInferenceProfile`), or the
   build stops. `init`, `doctor`, the model watch and Step 11 repeat the per-Region read.
1. **Mode-neutral Claude provider and eval work:** `claude.provider` with step-up; `provider` and
   `ended_at` on telemetry; `api_error` rows; items keyed by their session's provider; the gate key
   rule and gate rows per provider; `eval compare` warning; `ecf stats` split and cache-read share;
   eval-run state and result files behind repositories; the packaged set with the release-build
   sha256 check; the `sonnet` grep; a per-case `input_digest` (a hash of what `get_message` returned,
   never text) and a determinism sample with per-field flips in C results. *Pass:* a `--fraud-only` run
   is accepted by the gate; upgrading leaves live addresses live; a switch to
   `bedrock` and back leaves them live with Bedrock reviews only in the `bedrock` row; packaged +
   private = checkout set version, with equal confirmed and fraud-guard counts; a CI dry run of the
   release build with one set file changed fails.
   **1b. Reference run (go-ahead):** a plan-provider C `/ecf-eval` on M1 code in local mode, with the
   10-case determinism repeat on named case IDs. The set used by 1b, 10(c) and 11 is pinned: a public
   commit and a private commit, recorded with 1b's run ID in SPEC §16, from a dedicated checkout, never
   the live one; 1b, 10(c) and 11 have equal `set_version`. The dedicated checkout is a set-only clone
   of the public repository at the pinned public commit, with the private repository cloned into its
   `eval/private/` at the pinned private commit (detached; the for-gate check passes because that
   commit is contained in the private remote's branch), built with `ecf eval build` there. The M1 code
   runs from `../ecf-m1`, with its `eval_root` set to the dedicated checkout's `tests/eval/synthetic`
   (the resolver finds the private part from that path, OD-648). Dev builds for 10(c) and 11 package
   the public part from the dedicated checkout (`--eval-set-from`), and the private part is uploaded
   from it. A version change after 1b re-runs from 1b. If the operator adds cards before 1b, 1b is
   compared with `ce3be1df` on the shared subset (`--subset` = its IDs) and the new cards are reported
   separately; the safety bar applies to the full set. *Pass:* 0 unsafe, fraud guard N/N, non-inferior to `ce3be1df` (SPEC §16.2) at
   `--margin 5`; run ID recorded in SPEC §16; the run's merged version and private commit equal the
   pinned ones and `private_cases` > 0. Round-8 details: before 1b, the public-part and merged
   `set_version` computed at the pinned commits are recorded in SPEC §16 with the commits (what 1b,
   10(c) and 11 are checked against); `ecf eval build` in the dedicated checkout runs with the pinned
   public commit's own code (so M1's builder can't change the bytes), and afterwards `git status
   --porcelain` is empty in both repositories; the for-gate check's `git branch -r --contains` output
   is recorded (EVAL-PRIVATE Step 3 tests a detached HEAD); the dedicated checkout is a full clone at
   a pinned commit at or after the eval/private merge, built with `uv run` there, and `merge.json`
   exists after the build; 1b runs on a second named local install (`ecf --install m1ref`, a real
   service with its own database), never the operator's main install (whose `EVAL_ROOT` would
   otherwise move, and whose live install's `EVAL_ROOT` is checked unchanged afterwards), and its
   result file's path and sha256 are recorded in SPEC §16; `public_cases`, `private_cases` and
   `private_injection_cases` are each above 0 and equal `merge.json`'s counts. A shared ID whose label
   row differs from `ce3be1df`'s is excluded from that comparison and listed (whether `rescore` can
   re-score a v3 result on changed labels is unverified, so it isn't relied on). Before comparing with `ce3be1df`, any shared ID
   whose label row differs between the two sets is handled as above (`compare --subset` skips the
   version check, `results.py:252`).
2. **Chat seam inbound and cards:** `ChatInbound`, `respond() → Ack` with `Decided`, registry,
   `FakeChat` variants; typed button values and the legacy-value table; the card renderer from status
   and its inputs (OD-651, OD-657); the answer table (OD-656). *Pass:* suite green on Slack and on both
   fakes; a `form_in_ack` fake gets `Decided` in the Ack; one parity test per v1 function that draws a
   card (the rendered card equals v1's); every row of the answer table tested.
3. **AWS adapters:** DynamoDB repositories (transactions, the helper, fencing, spend records, `Scan`
   only in `ecf-jobs`), S3, SQS, Secrets Manager, the Cognito stepper; contract tests against moto.
   *Pass:* every contract test on both adapters; byte-identical round trip.
4. **Lambda handlers and build:** `ecf-api` (Mangum, routes, idempotent by request ID) and
   `ecf-worker` (the tick, both queue consumers), `ecf-slack-in`'s Cancel and Reject transactions, the broker
   (facts, the full in-lease worker list, per-step lease renewal, per-command socket deadlines,
   probes, the alert-outbox drain, scrubbed child), `ecf-jobs` (deadline-and-save, in-memory bundles, eval chunks, housekeeping),
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
   IAM-user credential refused; no key material in any job record, queue message or log (a test);
   an upgrade whose packaged set version differs warns, lists the addresses on the old version and
   requires `upload-private` to be re-verified.
   **6b. Laptop relay and the AWS provider path:** the relay (Unix socket for MCP and status, TCP for
   OTLP, allow-list, session token required), the shared refresher, `--provider bedrock` with Cognito
   step-up, the `GetCallerIdentity` check, settings generation. *Pass:* the scripted OTLP test; a
   profile token on a CLI route refused; a CLI JWT without a profile token refused on MCP routes; a
   request without the session token refused by the relay; a CLI refresh mid-session keeps the relay
   working.
   **6c. Migrate machinery:** the local `migrating` hold, the migrate and rollback bundle kinds, the
   one-time recipient (a job-scoped secret only `ecf-jobs` reads, deleted when the job ends), `migrate
   plan|run|abort|rollback|finalize`, `migrate_link`, `migrate_secret` and the signed phase attestation
   (OD-642), the migrating-only allow-list on AWS, `ecf eval upload-private`. *Pass:* the hold survives
   a restart, refuses checks and Slack, allows export, step-up and the dead-man's delete; abort restores
   local checking; no root key in any AWS request (a test); a secret write with a valid Cognito step-up
   but a forged attestation is refused, and one with an attestation but no step-up; a 4-address
   cutover hits no step-up rate limit; a partial or stale private upload is refused and a matching one
   accepted (the AWS merged `set_version` in DynamoDB equals the laptop's); `migrate plan` refuses a
   local `export.key` that differs from the deployed one, and local key rotation is refused while a
   migration is pending; the backup root never reaches the held service at cutover (a test on the
   socket messages); a partial secret batch finished by a second batch writes only the entries not
   marked written; no plain hash of a secret value is stored anywhere (a test); the batch verifies
   before it consumes the nonce, and a forged batch leaves the nonce unspent; an upload from a private
   set with uncommitted changes, or a HEAD not contained in its remote branch, is refused (the CLI runs
   the same check as a for-gate run and records the private commit with the upload); the AWS
   membership counts (`public_cases`, `private_cases`, `private_injection_cases`, computed by
   `ecf-jobs` with the shared merge and marker constant, `starter-injection` excluded by its sha256)
   equal the laptop's `merge.json` counts, and an upload with 0 injection cases is refused; both the
   clean-and-pushed check and the counts come from one function each in `ecf.eval`; AWS key rotation
   (OD-660) in the CLI with a crash after each step (the old keys stay in force until the deploy; a
   re-run finishes), the old relink key refused after the switch, rotation refused before `migrate
   finalize`; a relink with a forged signature leaves its nonce unspent; the attestation body appears in
   no log or table (a test); an import of a rollback bundle with an unsettled alert row lists it.
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
   the runtime; the four-table scratch merge by backup and by PITR; the broker on a 64 MB message
   (a fake IMAP server in the Lambda if the test mailbox refuses the append) at 1, 2 and 3 GB, a 64 MB
   message at the deadline plus 20 queued actions, and the lease never expiring while held; `ecf-jobs`
   import at the cap and a full `null` eval run across at least 2 invocations; Init Duration per
   function; the runtime's Python version; an SMTP server that trickles one byte every 10 s (the
   watchdog ends the action at its deadline); the chunked `null` run's result equal case by case to a
   single-invocation local `null` run on the same set (both from the dedicated checkout, the dev zip
   built with `--eval-set-from`); a PITR scratch restore checked for cross-table consistency; a
   held `slack-out` post (visibility set, batch-item failure reported) received again only after its
   hold, still at the head of its group; a `ConditionCheck` with `ALL_OLD` from `ecf-slack-in`'s role
   refused; `dynamodb:LeadingKeys` applied to `ConditionCheckItem` (unverified until this test); the
   `dynamodb:Attributes` bug guard denying an unlisted attribute; the tick's calls Query the
   unapplied-stop index and never Scan; a `ConditionCheck` from `ecf-slack-in`'s role naming `subject`
   denied; `ecf-worker`'s role denied a write to `MAINT`; a broker lease take refused while a hold is up,
   and a running broker ending at its next step; the export's drain time and Scan time recorded
   separately against table size (the PITR line set from the Scan time); a click during a 10-minute
   export hold answered within 30 s of the hold lifting and not dead-lettered; two concurrent applies
   of one stop (one applies, the other is skipped, no alarm); a retry with the same
   `ClientRequestToken` after a `TransactionConflict` (behaviour recorded; AWS documents only a retry
   after success); the transaction-conflict burst (Repositories, atomicity), retrying both
   `TransactionCanceledException` and a plain write's `TransactionConflictException` (botocore's own
   retry behaviour recorded); a scheduled export forced across two `ecf-jobs` invocations (the holder's
   own messages run during its hold and it resumes); the broker's `ConditionCheck` on `MAINT` allowed
   while its `PutItem` on `MAINT` is denied; Secrets Manager staging labels for the pending seed version
   (OD-660); the hold's p99 against its recorded expected end; a 24 h idle run (compared with the cost line scaled to the dev stack's own resource counts);
   teardown. *Pass:* chosen memory = the
   smallest meeting max Max Memory Used under 80 % over 3 runs, and the GB-s row recomputed with it;
   no run killed by timeout; no lease lapse; the idle run's extrapolated monthly cost within ±25 % of
   the infrastructure line (else the table is updated before Step 13); the deploy-to-teardown cost of
   Steps 7-8, by day from the dev account, read about 48 hours later and before the Step 9 go-ahead,
   under US$5.
9. **Slack request URL (go-ahead):** manual install with the signing secret; Socket Mode to request
   URL and back; `reauthorize` keeps the URL; real clicks; 5 forced-cold clicks and form buttons; the
   placeholder and `views.update`; a non-approver's Show excerpt; bad-signature and stale posts;
   replayed body; double click; a click processed after 5 minutes in the queue (accepted). Cancel
   (OD-649): N concurrent pairs of a Cancel and the tick's `delayed → executing` (harness, then the
   real stack), exactly one of each pair committing; a Cancel after the tick moved the item to
   `executing` (told "too late: sending has started"), including during v1's retry wait; a Cancel after
   a requeue, with the new card (wins) and with the old card (told "this card is out of date"); a Cancel
   on an `expired` or `failed` item (refused, no stop record); a double Cancel, Cancel then Reject, and
   another approver's Cancel (told "already cancelled or rejected"); an Approve while a stop record is
   pending (refused, naming the stop); a Cancel during maintenance (wins); a Cancel on a look-alike card
   (refused by the `message_ts` check); a non-approver's Cancel; a Cancel by a pending, revoked or
   inactive Slack member (refused by the transaction, audited); a bare stop record written outside the
   transaction after the item reached `executing` (`ecf-worker` refuses to apply it, alarms, the item
   keeps its state, the stop is marked `invalid` and leaves the index, and nothing is blocked); a role
   removed between a Cancel's commit and its apply (applied: authority is judged at commit, OD-656); an
   item resolved while its stop is pending (the stop marked `moot`, no alarm); a legacy v1 Cancel or
   Reject value (told "this card is from before the move"); a Reject at `awaiting_approval` and at
   `awaiting_stepup`, a Reject of a queued answer (told v1's `ANSWER_WAITS` text), a Reject on a draft
   card, a Reject on a `delayed` item (told "already approved: use Cancel") and on an `executing` one
   (told "sending has started"); a Reject then `ecf approve --pending` and a Reject then the item's
   expiry (both refused, the stop applied). A Cancel whose enqueue to `ecf-worker` fails after the commit (the tick applies
   it; the reply still says "cancelled"); a Cancel whose `response_url` post is skipped (`ecf-worker`
   posts the result); 20 forced-cold Cancels (Init, handler and `response_url` time recorded) and 5
   clicks within 1 s on a cold function (none throttled); Resume then Pause on one address (Pause
   wins); a post held more than 5 times (held, received again, still at the head of its group, not
   dead-lettered); two render requests for one card within 5 minutes (the card shows the latest state;
   one Slack call when the item didn't change); an edit queued before the first post lands (dropped,
   and the first post's re-read queues the edit); a 429 while 5 card edits are queued in one channel (one held call, not 5); an update while
   a post in the same channel is held; a form submission refused by `ecf-worker` (the form shows the
   error section, with the person's inputs kept) and which of `external_id` and `view.id` the
   update used; a refusal after the person closed the view (sent as an ephemeral); an org-wide Grid
   install refused, and the `auth.test` fields and the team and enterprise payload paths recorded; a
   Cancel during a rollback hold (told the rollback answer); the answer table's identity and budget
   rows; a legacy v1 click between the switch and the "moved" edit (refused); a Cancel during a hold whose `response_url` post was skipped (`ecf-worker` posts the result
   before its hold check, within 5 s); click to card edit; the dead-man message at cutover; teardown. *Pass:* max `$context.responseLatency` under 2.5 s
   cold and 0 `expired_trigger_id`; the real form within 5 s p95 cold; the non-approver sees nothing
   and can't cancel; replay and double click each one decision; every Cancel is answered with its true
   result as an ephemeral within 5 s; no Cancel answered "cancelled" for a sent email, checked against
   the test mailbox's Sent folder and the fake SMTP server's log (counts listed); after each applied
   Reject the item is `rejected`, the grant voided, the audit row written and the card re-rendered
   (v1's `reject` deletes no delay); the stop record is still present, state `applied`, after it is
   applied; a stale "Approve all" and a
   stale Undo are refused; reserved concurrency stays at least 20 (raised if more concurrent cold clicks are measured;
   cost recorded if provisioned concurrency is added).
10. **Parity (go-ahead):** (a) the `null` arm in both modes on the same set version: identical rule
    outcomes, triggers, facts and action plans; (b) a mailbox replay into fresh local and AWS installs
    in `shadow` with pre-written allowed differences (IDs, timestamps, `due_at` within one tick plus
    60 s, the generation header), DNS-fact differences investigated; (c) a plan-provider C
    `/ecf-eval` against the AWS install with the determinism repeat. *Pass:* (a) 0 differences; (b)
    delivered count = cases, 0 unexplained differences, and the replay fired every fraud trigger,
    every rule and the no-grant pre-check at least once (counts listed); (c) 0 unsafe, fraud guard
    N/N, non-inferior to 1b at `--margin 5`, per-case `input_digest` (a hash of what `get_message`
    returned, built in Step 1) equal across modes, determinism flips at most one more than 1b's
    repeat, the dev zip's packaged set version equal to the pinned public version; 10(c) becomes Step
    11's reference. (a) runs the local side against an in-memory scratch database and records the
    runtime and package versions on both sides.
11. **Bedrock (go-ahead):** one dev address set `live` first: a Bedrock `/ecf-review` naming it is
    refused, a Bedrock `/ecf-eval` runs, and after its pass the refusal lifts; the first session records
    the model string; a `/ecf-review` session with
    subagents and the spawn-guard hook; telemetry and status line; invocation-logging refusal; the full
    C `/ecf-eval` through Bedrock and the determinism repeat; cache figures under the fixed model; the
    bill. *Pass:* 0 unsafe, fraud guard N/N, non-inferior to 10(c) and to 1b at `--margin 5` (so the
    margin doesn't add up along the chain), `compare` shows only "different provider"; the run's
    `set_version` equals 1b's and the dev zip's packaged version equals the pinned public version; 0
    requests failing after their last attempt and 0 4xx other than 429 (retried 5xx reported);
    determinism flips at most one more than 10(c) and at most one more than 1b; per-item cost by profile tag
    over the run window, read about 48 hours later, within ±25 % of the projection recomputed from the
    run's own token counts (outside: a recorded miss, and §16.3 and `init`'s projection updated in
    Step 13).
12. **`ecf migrate` (go-ahead):** plan, run, abort at step 6, run again, rollback after one tick,
    re-migrate, finalize, and a second abort after step 7; N synthetic messages sent between cutover
    steps 2 and 10; one dev address seeded with a Bedrock run and one whose latest gate run used another
    set version; a Cancel and a Reject clicked between the rollback request and the rollback export; a
    queued alert email at the rollback. The set comes from the dedicated checkout (a `--eval-set-from`
    build). The local side is a throwaway real install (`ecf init` under its own name, a test Slack app
    and a test mailbox, as M8 Step 10), whose gate rows come from a `--fraud-only` C run on it with Step
    12's code (go-ahead); the count of addresses passing locally (more than 0) and the digest are
    listed. *Pass:* open-approval count equal, each with exactly one fresh grant with no
    attempt record; all N messages pre-checked on the first AWS check; a secret write with only one of
    attestation and step-up refused; audit rows after run, rollback and re-migrate equal the sum of the
    sources by `uid`, 0 collisions; a rollback started with one action `executing` sends it exactly once,
    and one started with a `delayed` send whose delay ends during the rollback sends nothing (paused,
    OD-658) and arrives locally as `awaiting_approval`;
    Bedrock gate rows and gate-eligible Bedrock runs 0 on AWS; the private part uploaded and verified
    before the AWS hold lifts; cursor and gate-row counts equal; at most one new card per open item, old cards
    edited (`conversations.history`); the local unit doesn't return after a reboot; `gate.synthetic`
    passes on AWS for every address that passed locally; abort restores local checking; rollback leaves
    AWS held and local restored with a bumped generation; the fraud-off notice posted and the gap's mail
    pre-checked on the first AWS check; `migrate plan` lists the mismatched address; the rollback's
    Cancel and Reject told the rollback answer if clicked after the hold (and arriving locally as
    `awaiting_approval`), else applied and arriving locally `cancelled` and `rejected`; a Cancel that
    commits just after the hold is set is found by the rollback's Scan and applied; the queued alert email sent locally after
    the rollback; abort after step 7 restores local checking, returns the Cognito identity to pending,
    keeps the aborted run's audit rows, and a re-run links and imports cleanly at a generation above any
    seen; the stamp rule holds (migrated runs count only on equal source set versions); the runbook
    records the hold, export and PITR timings beside the fraud-off gap.
13. **Docs and release:** SPEC (§1.2, §3.2 broker and `ecf-jobs` in the TCB, §4, §6.4 grants and spend
    records and `STOP#` stop records (OD-656), §8, §9, §9.6 new step-up purposes, §10.1 (the signing
    secret; the Slack queues: holds by visibility, 1,000 receives, crashes counted, 4-day retention,
    Slack-wide holds, OD-645, OD-651, OD-657; the closed click table; Cancel and Reject at the request
    URL as v1, OD-649, OD-656; form submissions, OD-657), §10.4 MCP
    tokens, §11.9 scheduled export, format 5 and bundle kinds, §11.10 AWS upgrades and format warnings,
    §12.2 AWS stated limits (deployer full data access, admin bypasses every rule, WAF unavailable,
    Cognito limits, a compromised broker has full mailbox authority, a compromised `ecf-slack-in` can
    forge one-click actions, stop the current grant of any item whose ID it learns (protective actions
    included), read identities, people, roles and item status, post as the bot and invite through `groups:write`
    (caught by the OD-215 member check), IAM's limits on DynamoDB (attribute names and key prefixes,
    not values; no condition key can require a `ConditionExpression`), a role removed between a Cancel's
    read and its commit (authority is judged at commit), Slack posts held past 1,000 receives
    (about 3.4 days) dead-lettered, a Cancel or Reject during a rollback or merge refused (nothing is
    sent until approved again), a rollback can't be cancelled once requested, a compromised
    `ecf-slack-in` overwriting its own `AUDIT#SLACKIN#` rows before a flush, org-wide Slack installs
    unsupported, TTL deletes during an
    export, rows imported before M1 carrying the importer's audit `uid`, fraud flagging off during
    migrate, a stale private eval clone accepted, the laptop's public eval set pinned to the installed
    release, lease stuck up to its bound after a kill), §12.4, §13, §14 `max_per_check` cap, §15, §16.3 Bedrock figures, §17), the
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
| R16 | tick and sweep | `ecf-worker` tick (OD-625) |
| R17 | no-grant pre-check gates | Who may call, no-grant actions |
| R18 | cost | Cost |
| R19 | eval keys | OD-617 |
| R20 | Bedrock gate on the real install | OD-619 |
| R21 | pass lines | Steps 1b, 8-11 |
| R22 | public repository | OD-618 |
| R23 | detective events | Infrastructure |

**Round 4** (draft 4, 9642729, eight Opus reviewers, full review; launched through the Workflow tool
without the operator's opt-in, Claude's error): about 75 findings, about 12 high (S1-S10 and mediums in
the findings file). The operator accepted every recommendation and decided K1-K3 (OD-623 to OD-625) on
2026-11-05. Where the highs landed:

| # | Round-4 finding | Landed |
|---|---|---|
| S1 | Cancel window | OD-623, replaced by OD-640 and OD-644; Queues, Cancel; Step 9 |
| S2 | grant authority, spend record vs retry | OD-624; Who may call, grants |
| S3 | `ecf-api` timeout | OD-625; The functions |
| S4 | cfn-exec boundary rule dropped | Infrastructure, cfn-exec |
| S5 | blocking command overruns | The broker (per-command socket deadlines) |
| S6 | secret writes without step-up while migrating | Moving to AWS |
| S7 | audit rows across migrate and rollback | Moving to AWS |
| S8 | inconsistent exports, double send | Data migrations (consistent exports); rollback |
| S9 | eval set join; laptop path copied | Moving to AWS; `planning-docs/EVAL-PRIVATE.md` (overlay) |
| S10 | stale-click table | Queues, clicks (SPEC §10.1 table) |

Mediums folded in: automatic grants (Who may call), probes and alert email (the broker), the
`ecf-slack-in` limit, relink key and revocation and first-login by `sub` (People), Scheduler roles and
`jobs.fifo` for housekeeping, `dispatched_until`, job heartbeats, slack-out holds, the `ecf-slack-in`
client, idempotent routes, "S3 config" dropped, alarm and cost rows. The rest (measurement pass-line
refinements, Bedrock retention per Region, Bedrock evidence on migrate, rollback click loss, M11 sync
route) are listed in the findings file for the draft-5 review to confirm or press.

**Round 5** (draft 5, 49cd6f1, eight Opus reviewers, delta, plain Agent calls): about 60 findings,
about 12 high, clustered as T1-T10 in the findings file; most landed on round 4's own fixes (the
in-flight flag above all). The operator accepted every recommendation and decided L1-L3 (OD-640 to
OD-642) on 2026-11-05.

| # | Round-5 cluster | Landed |
|---|---|---|
| T1 | in-flight flag holes | OD-640 (flag removed; Cancel and Reject at the request URL) |
| T2 | action values, click order | `ecf-slack-in` typed values; Pause/Resume one group per address |
| T3 | spend outcomes, IAM claims | Who may call (outcome items, "What IAM enforces") |
| T4 | socket timeouts | The broker (watchdog); Step 8 |
| T5 | shared role | OD-641; role table |
| T6 | Scheduler roles, boundary self-protection | Who may call; Infrastructure |
| T7 | migrate step-ups | OD-642; Moving to AWS; Step 6c |
| T8 | AWS eval set, stamp | Moving to AWS (`upload-private`); Steps 6c, 12 |
| T9 | grant reissue after restore | Moving to AWS |
| T10 | mediums | throughout (slack-out holds, `dispatched_until`, alert drain, auto-pause, jobs deletes, Scheduler FIFO, write-once keys, audit ULIDs, cost, pass lines) |

Items round 5 left open, and where they landed in draft 8: key rotation in AWS (Infrastructure,
write-once keys), identities live-wins on restore and the scratch-merge live-wins list and
rollback-kind tables (Repositories, bundle kinds), relink challenge contents (People, re-link), the
recreated-user path (People, re-link: a recreated user is a changed Cognito identity), the admin
invariant (People, model) and MCP tokens (Cognito), the M11 synchronous form route (Slack, chat
seam).

**Round 6** (draft 6, 7b4e5e4, eight Opus reviewers, delta, plain Agent calls): about 55 findings,
eight high (U1-U8 in the findings file). Two came from Claude's own round-5 fixes: OD-640's direct
status writes (IAM limits attribute names, not values, so the public function could revive a voided
grant) and re-sending held Slack posts (FIFO has no per-message delay, a fact already in this
document). The operator accepted every recommendation and decided N1-N3 (OD-644 to OD-646) on
2026-11-05.

| # | Round-6 finding | Landed |
|---|---|---|
| U1 | public function could approve | OD-644 (add-only stop records) |
| U2 | "cancelled" after a send | OD-644 (handover record); the broker |
| U3 | Reject spec | OD-644 (v1 preconditions); Step 9 |
| U4 | Cancel result, budget, concurrency | `ecf-slack-in`; Step 9; the concurrency change was marked landed here but not written until draft 8 (Claude's error) |
| U5 | held Slack posts | OD-645 |
| U6 | role wording | throughout ("the API roles") |
| U7 | audit TTL | Data and state (no TTL on `AUDIT#`) |
| U8 | eval set pinning and upgrades | Step 1b; Moving to AWS (gate's set) |

Mediums folded in: cards queued by reference, the rollback switch, stop records kept by merges, the
step-up purpose table, attestation nonces and domain tags, `IDENT#` items, `init --migrating` creating
no person, audit `uid` derivation and machine-local `flushed_at`, scheduled export reads (OD-646).
Also folded in: one watchdog per network step (socket object, `ValueError` mapping, DATA size rule),
`dispatched_until` set with the lease, auto-pause everywhere, Scheduler DLQ metric names. Three
items draft 7 left "for the next review" (the bundle-kind table, the M11 `Decided` Ack, the Lambda
row) were deferrals against the operator's rule; draft 8 writes all three.

**Round 7** (draft 7, b01ceee, eight Opus reviewers, delta, plain Agent calls): about 70 findings,
eight high (V1-V8 in the findings file). All eight lenses found V1 independently: per-item stop and
handover records that are never cleared. Claude's errors behind it: draft 7 promised a Cancel during
v1's retry wait, which v1 never offered. The operator accepted every recommendation, V1 as v1's
behaviour (OD-649), on 2026-11-05.

| # | Round-7 finding | Landed |
|---|---|---|
| V1 | per-item stop and handover records | OD-649; Queues, Cancel; Step 9 |
| V2 | stops applied without a re-check | OD-649 (applying); Step 9 bare-stop case |
| V3 | paths that skip the stop check | OD-649 (every move out of `awaiting_*` or `delayed`) |
| V4 | rollback loses unapplied stops | OD-650; Moving to AWS, rollback; Step 12 |
| V5 | cutover signing without a root | OD-653; Cognito; Moving to AWS; Step 6c |
| V6 | evals section, pinned set, dedicated checkout | Evals in AWS mode; Step 1b; Step 10 |
| V7 | reserved concurrency still 2 | Slack, budget; Infrastructure, concurrency |
| V8 | no way to find unapplied stops | OD-649 (sparse index); the tick; Step 8 |

Mediums folded in: `ReturnValues` `NONE` and the attribute allow-list on `ecf-slack-in`'s writes and
checks; mass-stop alarms and size cap; audit writer prefixes; write-once wording and the attribute
bug-guard test; the export write barrier and hold time (OD-650); clicks during holds; Slack queues,
render deduplication, `card_note` and the result ephemeral (OD-651); the audit `uid` migration
details; the bundle-kind table; rollback and abort leftovers; one identity store (OD-652); identity
checks in the stop transaction; nonce consume order; key rotation refused while migrating; pass lines
(Reject, Approve after a stop, 6c accept case, observation source, 10(c) bound); alarm count, secrets
count and the Lambda row in the cost table; stale lines; step owners; SPEC §6.4, §10.1 and §12.2
entries.

**Round 8** (draft 8, 264ef2b, eight Opus reviewers, delta, plain Agent calls): about 70 findings,
six high (W1-W6 in the findings file), most from draft 8's own fixes. No reviewer found a path where a
Cancel committed while `delayed` is followed by a send. Applying the operator's rule that a loop that
keeps finding holes means questioning the approach, the operator chose to simplify the hold (OD-655,
hold plus grace) rather than patch the per-write barrier; every other recommendation was accepted,
2026-11-05.

| # | Round-8 finding | Landed |
|---|---|---|
| W1 | `MAINT` as a hot item; barrier gaps | OD-655; The functions, holds; Data migrations; Step 8 |
| W2 | `moving_back` before the hold | OD-655 (one write); Moving to AWS, rollback; Step 12 |
| W3 | an invalid stop wedges its item | OD-656 (stop states, authority at commit, every edge); Step 9 |
| W4 | completeness through an eventually consistent index | OD-656 (consistent reads); exports; Step 12 |
| W5 | form refusals can't be shown | OD-657; `ecf-slack-in`; Step 9 |
| W6 | audit `uid` trigger namespace | Moving to AWS, audit (`audit.uid_prefix`, import prefix, `WHERE NOT EXISTS`); Step 0 |

Mediums folded in: every stop edge and the answer table; `grant_id` on every grant writer, legacy
card values; `ecf-jobs` voiding grants; the renderer's inputs, version bumps and first-post order;
Slack-wide holds; the 300 s hold after 15; `Decided` as text for Teams; single-workspace installs;
`ConditionCheckItem`'s Attributes list and `ITEM#` keys; stack-change detection and rotation order;
one nonce per phase, verify then consume, salted MACs in manifests; the sparse identity GSI; AWS key
rotation; the unchanged OD-341 fingerprint; relink fields for OS identities; the module list; identity
merges; rollback alert email; abort after step 7; generations; stop-time by transition history; the
`install_id` cross-check; 1b on a dev service with recorded versions and a clean build; clean uploads
and AWS counts; non-inferiority to 1b at Step 11; the set source for Steps 8 and 12; the custom-metric
cost; step owners; stale lines.

**Round 9** (draft 9, d6c48a4, eight Opus reviewers, delta, plain Agent calls): about 85 findings, six
high (X1-X6 in the findings file), most again from draft 9's own fixes; no reviewer found a send after a
Cancel committed while `delayed`. Applying the operator's rule, the operator chose to simplify the
rollback (OD-658, pause first, no cancel) and the audit IDs (OD-659, random), and to finish AWS key
rotation (OD-660); every other recommendation was accepted, 2026-11-05.

| # | Round-9 finding | Landed |
|---|---|---|
| X1 | the holder's own lease deadlocks the hold | OD-658 (`MAINT` names the holder); Step 8 |
| X2 | stop search is a full Scan; PITR rollback and merges miss stops | OD-658 (pause first; Scan path for rollbacks and merges; scheduled exports don't search) |
| X3 | audit `uid` prefixes | OD-659 (random) |
| X4 | Step 1b and Step 12 on `ecf-server dev` | Steps 1b (`--install m1ref`) and 12 (a throwaway real install) |
| X5 | renderer misses escalation and answer cards | After round 9, Slack; Step 2 |
| X6 | `ecf-jobs` could revive voided grants | OD-658 (delete only) |

Mediums folded in ("After round 9"): resolve edges exempt; `ecf cancel` and `ecf reject` as applied;
answer rows and v1 wording; holder lease and bounded pages; upgrade grace; both conflict error forms;
`ecf-slack-in` reads `MAINT` without a transaction check; edits dropped before a first post; 300 s by
receive count; `views.update` target and kept inputs; refusals after close; legacy cards at cutover;
Grid detection; relink's one nonce and the verify order; attestation body never stored; OS issuer;
identity merges; the module list; the `MAINT` IAM pattern; key attribute names and `ITEM#` records;
CloudFormation events; kind-dependent reader allow-list; the transition marker; the generation's home;
one function each for the clean check and the counts; 1b's checks; the alarm count; stale lines.

Draft 10 gets a delta review on Opus (OD-577).

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
  least 5, valid range 1 to 1,000 (`sqs-configure-dead-letter-queue.html`, fetched 2026-11-05);
  `VisibilityTimeout` 0 to 43,200 s, retention 60 s to 14 days (`API_SetQueueAttributes.html`); a FIFO
  message's enqueue time resets when it moves to the DLQ; a failed batch returns after the visibility timeout; Lambda's SQS trigger needs
  `GetQueueAttributes` (`services-sqs-configure.html`).
- (rv) EventBridge: Scheduler 60 s precision, 14 million a month free, retries up to 185 within a
  day; AWS management events ingested free; CloudTrail rules need an active trail
  (`eb-service-event-cloudtrail.html`).
- (rv) DynamoDB: PITR $0.20 per GB-month; on-demand $0.125 and $0.625 per million; TTL within days;
  transactions authorized per item action plus `ConditionCheckItem` (`transaction-apis-iam.html`); a
  backup restores only to a new table, with TTL, PITR, deletion protection, tags, IAM and alarms
  set again (`CreateBackup.html`); `AWS::DynamoDB::Table` supports CloudFormation resource import.
  The 100-item transaction limit and 400 KB item limit: unverified, Step 0. A Scan with
  `ConsistentRead` is consistent per item only: "DynamoDB does not provide snapshot isolation for a scan
  operation when the ConsistentRead parameter is set to true" (`API_Scan.html`, fetched 2026-11-05);
  a strongly consistent read is 1 RRU per 4 KB, $0.125 per million RRU (pricing page, fetched
  2026-11-05). `ConditionCheckItem` is its own action; a policy can limit `dynamodb:ReturnValues` to
  `NONE` (`transaction-apis-iam.html` Example 4); no condition key can require a
  `ConditionExpression` (`specifying-conditions.html`, fetched 2026-11-05). (rv, round 8, fetched
  2026-11-05) GSIs are updated asynchronously and read eventually consistently only; a NULL-typed
  GSI key attribute is a `ValidationException`, and an item without the attribute isn't indexed
  (`GSI.html`). Two transactions touching one item, a `ConditionCheck` included, conflict, and the SDKs
  don't retry it (`transaction-apis.html`). `dynamodb:Attributes` covers attributes evaluated in a
  conditional write (`specifying-conditions.html`); whether it and `LeadingKeys` apply to
  `ConditionCheckItem` is unverified (the authorization reference page didn't render; Step 8 tests it).
  CloudWatch includes 10 free custom metrics besides 10 free alarm metrics (aws.amazon.com/cloudwatch/pricing).
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
  `block_actions` payload; `response_url` 5 uses in 30 minutes, ephemeral by default
  (`interactivity/handling-user-interaction`, fetched 2026-11-05; whether another sender may post to it
  after the 200 is unverified, Step 9 shows it); `views.update` needs no `trigger_id`,
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
