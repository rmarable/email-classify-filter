# M1 AWS mode, single user

**Status: draft 21, 2026-11-06. Not built.** M1 started 2026-11-04 (OD-592 put it next; OD-593).
Phase R: round 1 (eight Fable reviewers) on draft 1 (df4d331); rounds 2 and 3 (eight Opus
reviewers each, delta, OD-577) on drafts 2 (272df2a) and 3 (1c2e2f5). After round 3 kept finding
holes at the seams drafts 2 and 3 had added, the operator chose to simplify before the next round
(OD-615): four functions instead of six, no grant signatures, no Cancel marker. Round 4 (eight Opus
reviewers, full review of draft 4, 9642729) led to OD-623 to OD-625; round 5 (eight Opus reviewers,
delta, draft 5, 49cd6f1) to OD-640 to OD-642; round 6 (eight Opus reviewers, delta, draft 6, 7b4e5e4)
to OD-644 to OD-646; round 7 (eight Opus reviewers, delta, draft 7, b01ceee) to OD-649 to OD-653;
round 8 (eight Opus reviewers, delta, draft 8, 264ef2b) to OD-655 to OD-657; round 9 (eight Opus
reviewers, delta, draft 9, d6c48a4) to OD-658 to OD-660; round 10 (eight Opus reviewers, delta, draft 10,
f86ad4d) to OD-661; round 11 (eight Opus reviewers, delta, draft 11, b6a031e) to OD-662 to OD-665; round 12 (eight
Opus reviewers, delta, draft 12, 9302be1) to OD-666 to OD-668 (the freeze rewritten from invariants);
round 13 (eight Opus reviewers, draft 13, 7b3c114) to OD-669 to OD-671 (the protocol reshaped and
model-checked); round 14 (eight Opus reviewers, draft 14, e03effa) and a four-agent model attack to
OD-672 to OD-675 (draft 15 written model-first); round 15 (eight Opus reviewers, draft 15, 4ff3587)
to OD-680 to OD-683 (the models extended, the text tied to them); round 16 (eight Opus reviewers,
draft 16, 75417c7) to OD-690 to OD-694 (every model step one real write); round 17 (eight Opus
reviewers, draft 17, 5b8f147) to OD-698 to OD-702 (no model reads what a real read can't give; the
retention bound replaced by a skip rule); round 18 (eight Opus reviewers, draft 18, c2013f7) to OD-720
to OD-724 (the models generate the migrate re-run, the retention re-stamp, stale environments and card
reissue; one card-reissue mechanism); round 19 (eight Opus reviewers, draft 19, 2025af9) to OD-725 to
OD-731 (mechanisms removed rather than patched: no migrate clear, no expiry stamp at any write, the
tick reads the stack); round 20 (eight Opus reviewers, draft 20, 175a3b9) to OD-732 to OD-740 (a
rotation only on its key text's machine, children expiring with their item, the re-ask by key, the
post step and move-back cards modelled, no runtime batch writes); round 21 (eight Opus reviewers, draft
21, 38d018a) to OD-741 to OD-748 (the bundle kept before the commit, a fixed wait before freeze (b),
IAM claims cut to what IAM enforces, a fixed set of item records, the abort's audit copy ordered).
Every finding and decision (OD-593 to OD-748) is folded in and listed under "Plan review findings". Code references
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
  - (The per-write `MAINT` check, `moving_back` and the export's own stop pass are superseded: the
    export hold is OD-655 and OD-658, every other quiescing is a freeze job, Design, Maintenance.)
    TTL deletes during an export's scan are a stated limit (expired items, already filtered).
  - The export takes the PITR path when Step 8's measured Scan time passes its line (Design, Data
    migrations); Scans use parallel segments. Cost about US$1 per GB of table data a month (1 RRU per 4 KB,
    US$0.125 per million, fetched 2026-11-05 (rv)), US$4.50 per GB a month on the PITR path.
  - Clicks during a hold: `ecf-worker` sets the click's visibility to the hold's expected end (about
    30 s) and reports a batch-item failure; these receives don't count as crashes (counted in
    DynamoDB, as OD-645). Amends OD-646.
- **OD-651, Slack queues and cards (round-7 performance 3 6, Slack 4 5):**
  - `slack-out.fifo` and `slack-in.fifo` allow 1,000 receives (SQS's maximum); `ecf-worker`
    dead-letters only after 5 real crashes counted in DynamoDB, so v1's network (60 s), token (300 s)
    and rate-limit holds never dead-letter early. Both queues and their DLQs keep messages 14 days (OD-671; was 4);
    a post held past 1,000 receives (about 3.5 days of 300 s holds) is dead-lettered and alarmed
    (SPEC §12.2 stated limit).
  - Card posts and edits go by reference; ephemerals and DMs go by value. Deduplication ID = a ULID per
    render request, except an item card's post, whose ID is its `CARD#` key and `card_queued_at` (F10). A first post uses the channel's group; edits of a posted card use `card:<item>`
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
  - The path is chosen by the measured Scan time alone (drain is the same on both paths), with a
    margin so it doesn't flip between runs. A message that arrives during a hold is put back with its
    visibility set to the hold's expected end before anything is written, so nothing counts it as a
    crash. Amends OD-650. (Since draft 13 the hold serves scheduled exports only; rollbacks, restores,
    imports and upgrades are freeze jobs: Design, Maintenance, OD-666.)
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
    "already approved: use Cancel" (only then); any other status → "this email is <status>". A test
    covers every row.
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
    edit that finds no `message_ts` is dropped (the round-9 rule; this hold was withdrawn by OD-674, F10); the first post re-reads the
    version after `chat.postMessage` and queues an edit if it changed.
  - Slack-wide holds as v1 (`slack_out.py:7-16`): a `hold_until` and reason on the channel's pacing
    record and on an install-wide Slack record, checked before every Slack call; a wait of 1 s or less
    is slept in the function. After 15 consecutive 60 s holds of one post its hold becomes 300 s (a new
    rule: v1 holds in memory and has no receive count), so 1,000 receives last about 3.4 days, inside
    the 14-day retention.
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

- **OD-658, the holder's own lease; scheduled exports don't look for stops (X1, X2; round-9 safety
  1-4):**
  - (Rollback and merge parts superseded: Design, Maintenance, OD-666.) The hold records its kind and
    the holding job's ID; since draft 13 its only kind is `scheduled_export`.
  - A scheduled export doesn't look for stops: stop records aren't exported, and the tick applies them
    after the hold. Stops are allowed during its hold, and `slack-out` posts that write only live-kept
    records run during it too.
  - **The holder's own lease:** `MAINT` names the holding job; the drain skips that job's lease;
    `ecf-jobs` runs that job's messages during the hold; the tick still re-queues it, still emits its
    heartbeat, and alarms on a hold with no live holder for 30 minutes. The hold waits the 90 s grace
    after the drain. Fetch pages, backfill pages and job pages are each
    bounded to 60 s, so the worst-case hold is about 120 s (drain) + 90 s + the Scan, recorded on `MAINT`
    as its expected end. Step 8 records the hold's p99.
  - The resolve edges (`resolved_manual`, `resolved_by_mailbox`, address remove, `close_gone`) are
    exempt from the stop check: resolve wins and the stop becomes `moot`. `ecf cancel` and `ecf reject`
    in `ecf-api` leave the item at the stop's own outcome, which the apply treats as `applied`.
  - The answer table's first rows: identity, person or role refused → "not allowed"; budget
    exhausted → "click again". "Already cancelled or rejected" only when the stop put failed and the item, read again, is `cancelled`, `rejected` or
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
  - Order (as amended by OD-666 and OD-669; Maintenance, F11 is the current text): (1) the step-up
    creates the rotation's job record bound to the new export and relink fingerprints (audited,
    Security Notice); (2) the laptop writes the new seed, with
    the expected recipient and relink fingerprints in the same secret value, as a new version with a
    pending label; (3) one deploy switches: the age recipient and the relink verify key are replaced
    (single values; the leaked root can neither relink nor read new exports), the new export verify key
    is added and the previous ones kept in `ExportKeysPrevious` (only to verify older bundles; pruned
    when no kept backup needs them), and the functions read the seed version the deploy names;
    (4) `rotation_check` confirms the deployed fingerprints equal the recorded ones. A crash before (3) leaves
    the old keys in force; re-running is idempotent. Versions are addressed by ID (OD-700); a version
    written with a custom label leaves `AWSCURRENT` where it was (PutSecretValue API reference, fetched
    2026-11-06; Step 8 still checks it).
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
    `view.state.values`; a refusal whose view was closed (`not_found`) is sent by DM, as v1 ("After round 10"). Cutover
    edits old cards to "moved" and posts fresh cards (step 9); a legacy click in between is refused with
    "this card is from before the move". An org-wide install is detected by `auth.test`'s enterprise
    fields at `init` and by `doctor` (unverified field names, Step 9); the runbook gives the repair
    for a workspace later moved into Grid (a stack parameter update). For M11, still unverified: who
    sees a Teams invoke reply, its time limit, `Action.Submit`'s fallback, which can't return a
    card, and how Teams treats a request URL whose function is throttled during a freeze. A rollback
    leaves Teams with no endpoint (stated limit for M11).
  - **IAM:** every Allow statement granting main-table writes to a role other than `ecf-jobs` and
    `ecf-api` carries `ForAllValues:StringNotLike` `dynamodb:LeadingKeys` `["MAINT"]` and `Null`
    `dynamodb:LeadingKeys` `false` (`ForAllValues` is true when a request has no key,
    `reference_policies_condition-single-vs-multi-valued-context-keys.html`, fetched 2026-11-05 (rv));
    `ConditionCheckItem` sits in its own statements, so the broker's lease-take check on `MAINT` is
    allowed while its `PutItem` on `MAINT` is denied (a Step 8 test). The main table's key attributes are
    `pk` and `sk`; an item is a single record `pk = ITEM#<stable_id>`, `sk = ITEM`, and per-item records
    use their own prefixes (`CARD#`, `DRAFTV#`, `ESC#` for its escalation row; `CHECK#`, which named no
    record any writer makes, is removed, OD-746), through one key helper with a round-trip test;
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
    `item.transitioned` row for the audit (since OD-673 no merge decides by it: a live terminal status
    wins). The highest generation AWS has seen is kept on the install's keys item.
  - **Measurement:** Step 1b runs on a second named local install (`ecf --install m1ref`, a real
    service), and its result file's path and sha256 go in SPEC §16. Step 12 uses a throwaway real local
    install (`ecf init` under its own name, a test Slack app and a test mailbox, as M8 Step 10), whose
    gate rows come from a `--fraud-only` C run on it with Step 12's code (go-ahead). The dedicated
    checkout is a full clone at a pinned commit at or after the eval/private merge. The clean-and-pushed
    check used by uploads and by for-gate runs is one function in `ecf.eval`; the membership counts are
    one function in `ecf.eval` too, called by the laptop and by `ecf-jobs`.

### After Phase R round 10 (SPEC OD-661, operator 2026-11-05)

Round 10 found highs in the rollback machinery for the sixth round running, all in coordinating running
AWS functions from inside AWS; the operator chose to let AWS stop them instead.

- **OD-661, rollbacks and AWS restores and imports run on a frozen stack, driven from the laptop
  (Y1-Y3):** the laptop freezes the stack by deploy ("To intentionally throttle an Event Function, set
  its reserved concurrency to 0. This stops your Event Function from processing any events until you
  remove the limit", `configuration-concurrency.html`, fetched 2026-11-05) and drives `ecf-jobs`
  itself. Current text: Design, Maintenance (OD-666).
- **Also from round 10 (no new decision):**
  - **Step 1b against `ce3be1df`:** always with `--subset` = the IDs shared with `ce3be1df`'s set
    (`a6642d21`, 396 cards; main is now 398 cards, `9370cd54d96f28bc`, after OD-632's cards), minus IDs
    whose label row differs between `labels.jsonl` at `e557c56` (the commit `ce3be1df` ran on, SPEC
    §16.2) and the pinned merged `labels.jsonl`; the excluded IDs and count go in SPEC §16 with
    `ce3be1df`'s result file path (`accept/evals/ce3be1dfc61ac0366f50082452d1add9.json`) and sha256.
  - **m1ref:** `ecf --install m1ref init` from a wheel built at a recorded clean M1 commit and
    installed in m1ref's own environment (an editable `uv` install would follow `../ecf-m1`'s checkout;
    the launchd unit runs the `ecf-server` beside the Python used at `init`, `service_unit.py:34-38`), its
    own `ecf --install m1ref claude --login`; pass: the wheel's SHA-256 and commit are recorded (v1 reports
    no commit, so the record is the check), `git rev-parse HEAD` and an empty `git status --porcelain`
    at that commit before and after 1b, its pins key equals
    `ce3be1df`'s, and no other install gains an `eval_runs` row. m1ref is kept until Step 11 passes, and
    the 1b result file is also copied to a kept folder.
  - **Step 12 seeds:** "passing locally" means `gate.synthetic` passes (the fraud-only run's three
    membership counts recorded, each above 0); the gate's latest run is per pins digest, not per address
    (`evalrun.py:1486-1490`), so the "address on another set version" case moves to a unit test of
    `migrate plan`; the Bedrock-seeded address needs its own go-ahead and run ID.
  - **Rotation (OD-660):** previous export verify keys accept only bundles whose SHA-256 is on the hash
    list `rotation_list` makes inside the freeze (Maintenance, F11; the laptop writes it into the seed
    secret), so a bundle forged with the leaked root is refused; after `rotation_check` passes, the laptop moves `AWSCURRENT` to the new version
    (`UpdateSecretVersionStage`), every reader uses the deployed version ID, and the seed secret's
    resource policy denies `PutSecretValue`, `UpdateSecret`, `DeleteSecret`, `UpdateSecretVersionStage`, `PutResourcePolicy`, `DeleteResourcePolicy` and `RestoreSecret` to runtime roles (Design, Secrets); the rotation deploy runs as a freeze job (Maintenance, F11); a CLI re-run
    after a crash asks for the saved key text and checks it against the pending fingerprints (from
    `rotation_list` on, only a forward re-run finishes it, Maintenance F11); the laptop keeps both expected
    fingerprints in a local file (`<data dir>/aws-keys.json`), updated only after step (4) matches;
    local mode stores the relink fingerprint (public) next to `export.key`, written at rotate and
    restore; the `export_key` step-up binds the old and new OD-341 fingerprints (v1's
    `digest("export_key", new, old)`, `export_keys.py:120-123`), admin only, and the operator types the
    new OD-341 fingerprint.
  - **Identity:** the relink transaction's conditions: the old `IDENT#` has the record's `people_id`
    and isn't revoked; the new one doesn't exist, or is pending with no `people_id`; the person is
    active; the nonce record holds the new kind, issuer, `install.id` and a TTL of at most 10 minutes;
    `AdminUserGlobalSignOut` on the old user runs after and alarms (not fails) on `NotFound`. An OS
    identity's subject is a random binding ID created at `init` or relink, beside the user name, and
    its issuer is `os:` + HMAC(`install.id`, hardware UUID), so the hardware UUID leaves no machine; a
    re-bind creates a new binding. Local relink compares `derive(root)`'s public keys with
    `export.key`'s recipient and verify key. The purpose table names `export_key` (admin). The bundle
    module also moves `FORMAT`.
  - **IAM:** every `LeadingKeys`-scoped statement carries `Null` `dynamodb:LeadingKeys` `false`; each
    role other than `ecf-jobs` and `ecf-api` also gets an explicit Deny on writes with
    `ForAnyValue:StringEquals` `LeadingKeys` `["MAINT"]`; `ecf-slack-in` reads `CARD#*` (`pk`, `sk`,
    `message_ts`, channel) and is denied `DRAFTV#` and `ESC#` (a Step 8 test); the `ecf-api` table's
    key attributes and the `people_id` index key are named in its Attributes lists; CloudFormation's
    native "Stack Status Change" events for the install's stack IDs are watched besides CloudTrail,
    `ExecuteChangeSet` is matched on any name, and the record a notice is matched against lives on the
    laptop.
  - **Holds and the broker:** the large-message step (v1 defers mail over 16 MB until after the page,
    `fetch.py:53-62, 436-456`) is its own step, bounded by size over measured rate plus a margin and
    capped at 120 s, gated by a `MAINT` read; the drain bound is the larger of that and the action cap.
    The holder updates `MAINT`'s expected end after the drain and after the grace; put-backs use
    min(expected end − now, 60 s) (`jobs.fifo`: to the expected end, "After round 11"). After its
    retries a lease take sets the address due on the next tick.
  - **Slack:** every `enqueue_post` key prefix is listed in Step 2 as by reference (item cards, with
    parity tests) or by value in its own `card:<key>` group ("Needs you", scan summaries, bursts and
    roll-ups, digests, review, daily, alert and stage cards, the answer mirror); `slack-out.fifo`'s
    privacy line says by-value cards can carry subjects and senders. A card record holds `queued`,
    `posted`, `dropped`, `given_up_refused`, `given_up_outage` or `given_up_other`, replacing v1's
    `has_card`: an outage or unknown-code give-up is re-queued once automatically, a refused one waits
    for `ecf slack repost` (F10; a test).
    The 300 s escalation counts post-specific failures in DynamoDB, not receives, and the recovery lag
    of up to 5 minutes after a long outage is a stated limit. A modal refusal whose view was closed goes
    by DM, as v1 (`answers.py:278`). The re-shown form uses fresh `block_id`s and maps each element's
    initial value (unverified, Step 9 per input type). Org-wide installs are detected only by recording
    `enterprise_id` and `team_id` at `init`; a workspace moved into Grid needs the runbook's repair,
    which also remaps Slack `IDENT#` subjects (`migration.exchange`). The legacy-value table marks each
    v1 value accepted or refused, after the `message_ts` check. `UnprocessedKeys` from `ecf-slack-in`'s
    read is retried once if the budget allows, else "click again". Stops committed during a scheduled
    export's hold are listed in its report.
  - **Migration:** the staged backfill bound is a staged-only setting `import.audit_uid_max` written by
    `importer.load` first and deleted by the migration; the `uid` trigger is created from Python after
    the SQL (v1's `_split` cuts on every `;`, `db.py:227-229`); every person-initiated `cancelled` or
    `rejected` transition carries `via: stop` or `via: cli` (audit only; OD-673's live-wins rule
    doesn't read it); staged rows'
    `source` is the importing install, not the bundle's; two pre-M1 copies that ran side by side can
    derive the same `uid` for different events (stated limit, as v1's merge by id).
  - **Ownership and wording:** Step 6 builds AWS `export keys rotate`, the relink fingerprint and the
    rotation's freeze job; Step 4 builds the hold record, the drain that skips the holder, page bounds
    and the expected end; a Step 0 contract test treats a missing grant and a `voided` one alike for
    every grant reader; Operations lists `export keys rotate`; the custom metrics are listed by name.

### After Phase R round 11 (SPEC OD-662 to OD-665, operator 2026-11-05)

Round 11 found six highs, all in draft 11's freeze (OD-661), the fourth round running with highs in the
rollback and hold design. The operator chose to copy v1's mappings (OD-662), take the step-up and
bundle upload before the freeze (OD-663), make each freeze one resumable job (OD-664) and put upgrades
and the rotation deploy on the freeze (OD-665). Round 12 found highs in those fixes again; their
current text is Design, Maintenance (OD-666 to OD-668), and only the parts still true are kept here:

- **OD-662:** an AWS import is v1's import (empty target or `--replace`, `importer.py:96-101`); an AWS
  restore is v1's `downgrade` (`items.py:145-154`, `downgrade.py:72-97`); every address paused; a send's
  delay doesn't count while frozen. Items in flight: OD-667.
- **OD-663:** the admin's step-up (purposes `restore`, `import`, `migrate_rollback`; SPEC §9.6 gains
  `migrate_rollback`) and the import bundle's upload come before the freeze; the job record (F1).
- **OD-664:** a freeze is one resumable job (F1-F12).
- **OD-665:** upgrades and the rotation deploy are freeze jobs (F11); the hold is left for scheduled
  exports only. Stated limit: mail checks and fraud flagging stop during each upgrade's freeze (five
  stack updates and one 960 s wait, Maintenance F11; timed in Step 8).
- **Also from round 11 (no new decision):**
  - **Restores keep live identities:** `IDENT#` and people (OD-667) are live-wins on a restore: a
    restore never removes a live confirmed identity, and `revoked` and `inactive` are sticky.
  - **Rollback restore and cards:** the local restore reads the bundle's `item:` card rows into an edit
    list, queues the "moved back" edits, deletes those rows (as `restore.py:238`), and only then
    reissues and posts, so `has_card` doesn't skip the new card (`approvals.py:885-887`); it loads
    `draft_versions` and re-queues `alert_outbox`. Lost on a rollback: `slack-in.fifo` clicks not yet
    processed, and by-value posts still queued (I6; runbook).
  - **IAM:** cfn-exec's PassRole for `scheduler.amazonaws.com` is `role/ecf-sched/<install>/*`; the
    `MAINT` Deny names `PutItem`, `UpdateItem`, `DeleteItem` and `BatchWriteItem` only; `ecf-jobs`
    gets `s3:DeleteObject` on `exports/` and `backups/` (partial objects and the prune); the role table
    says `ecf-jobs` is invoked by `jobs.fifo` or, frozen, by the deployer.
  - **`jobs.fifo`:** a job arriving during a scheduled export's hold is put back to the hold's expected
    end without the 60 s cap, and `jobs.fifo` crashes are counted in DynamoDB with `maxReceiveCount`
    1,000, as the Slack queues (OD-651).
  - **Slack:** Step 2 holds the full `enqueue_post` prefix table (by reference or by value, privacy
    class), and a test fails on any prefix missing from it; Step 9 records API Gateway's status and what
    Slack shows (button, form button, modal submit; desktop and phone) for a throttled request URL; the
    Teams equivalents join M11's unverified list.
  - **Measurement:** m1ref runs a wheel built at the recorded commit in its own environment (an
    editable install follows the checkout); the 1b label exclusion also covers `fixed_labels.jsonl`
    rows; Step 11 is also compared with `ce3be1df` on the same `--subset` at `--margin 5`.

### After Phase R round 12 (SPEC OD-666 to OD-668, operator 2026-11-05)

Round 12 found ten highs, again mostly in the previous draft's own fixes (the job record, the delay
shift, dropping pre-freeze messages, the rotation as a freeze job). The operator accepted every
recommendation and asked for the patching to stop, so draft 13 rewrites the freeze once, from stated
invariants, as one section (Design, Maintenance), deletes the superseded text above, and checks every
operation against every exit path and invariant before review (`state-archive/m1/draft13-check.md`).

- **OD-666, one freeze lifecycle from invariants:** invariants I1-I8 and rules F0-F15 (Design,
  Maintenance, as rewritten by OD-669): one section, one exit table, `aws unfreeze` defined for every
  kind, the rotation's own freeze exempt and its steps ordered, nothing dropped after a freeze.
- **OD-667, one start rule and one mapping for items in flight:** as rewritten by OD-670 (Design,
  Maintenance).
- **OD-668, stated limits and wording:** OD-665's "one quiescing mechanism" corrected to two (the
  freeze, and the export hold for scheduled exports); a broken `ecf-api` blocks upgrade-back, restore
  and rollback through `ecf` (runbook: the deployer path); `destroy` on a frozen stack uses the
  deployer credentials only, with no step-up.
- **Also for draft 13 (eval/private draft 9, OD-633, merged on main bc98a9b):** Step 1b and the upload
  take the membership counts from `ecf.eval.membership(sources)` (as amended by eval/private drafts 12
  to 16, OD-636 to OD-638: `public_cases` and `private_cases` from each source's ID list, every private
  card marked by construction; `is_injection_card` is a front-matter check, and content is scanned by
  `scripts/check_card_injection.py` in CI's `card-scan.yml`) and the clean-and-pushed check from `ecf.eval.clean_and_pushed(top, set)`, which records
  the upstream ref (`rev-parse --symbolic-full-name @{u}`, under `refs/remotes/`) and the result of
  `merge-base --is-ancestor HEAD @{u}`.

### After Phase R round 13 (SPEC OD-669 to OD-671, operator 2026-11-05)

Round 13 (8 Opus) found 10 highs, again mostly in draft 13's own rules. The operator accepted the
recommendations and asked for the underlying issue to be fixed. The underlying issue was the shape,
not the individual rules: several writers of a job's state, a driver resumed by position in a
sequence, and freeze steps that waited on the parts of the system the freeze stops; and a review
process that checked prose with prose. Draft 14 changes the shape and checks it by machine.

- **OD-669, the freeze protocol:** after `ecf-api` creates a job, only `ecf-jobs` writes its record,
  lock (and, until OD-691, a rotation-pending record); the laptop is a reconciler (F0) that takes the next action from
  what it reads, so any kill, resume or second machine is a re-run; every stack update is a
  compare-and-swap change set; nothing in the freeze waits on Slack, `ecf-api` or the tick; a rollback
  `commit` state refuses `abort` once the local restore may start. As amended by OD-674 and OD-675
  (Design, Maintenance, is the current text).
- **OD-670, one start rule and one mapping:** for rollback, restore and import; `sent`, `threads`,
  `senders` and `gate` a union with the live row winning; the admin-approver invariant re-applied. As
  amended by OD-673 (Design, Maintenance, is the current text).
- **OD-671, smaller choices** (as amended by OD-675): no on-demand backups (an upgrade's restore point
  is PITR at `quiet_at`, F5); the import binds `manifest.json`'s hash and `replace`, with no laptop
  signature; queues a freeze holds keep messages 14 days; a queued stop's answer is its card when
  its `response_url` is over 25 minutes old (stated limit); mailbox secrets have no resource policy;
  `Frozen` goes into every function's environment (F4).

### After Phase R round 14 (SPEC OD-672 to OD-675, operator 2026-11-05)

Round 14 (8 Opus, full) found no fault in draft 14's shape; all ten highs (CC1-CC10) were in what its
models abstracted away (one job, one name for two stack states, an oracle on laptop-local state, no
time, no import, no pruned or cross-install items). The operator accepted the recommendations and asked
again for the underlying issue to be fixed. Draft 15 is written model-first: the models were rebuilt
so that each actor decides only from what it can read, every round-14 counterexample was seeded back
in as a fault, then four Opus agents attacked the models and a design note before any plan text was
written (their highs, also fixed first: the restore commits in the local service, not the CLI; the
wait could be stamped mid-update; lock takeover of a started job; `FreezeSeq` missing failed updates,
mid-freeze items and late ticks; three item-mapping holes). The plan text below is written from that
note (`state-archive/m1/model/draft15-design.md`).

- **OD-672, how the freeze is checked:** three models (`freeze_model2.py`, `delay_model2.py`,
  `map_model3.py`) and `run_all.sh` with every seeded fault, each caught by the invariant it targets;
  Step 6 moves them into `tests/m1/` with pure shared functions and a refinement test from real reads
  to model observations; what the models don't cover is stated (Maintenance, "Not modelled"); stated
  limit: losing the bound machine strands a rotation after `rotation_list` and a rollback from
  `commit` on (`destroy`).
- **OD-673, item rules (amends OD-670):** a restore or import point older than the retention period is
  refused; the live item wins when terminal, approved or later, or with any attempt or `sent` row;
  undo outcomes follow the grant's purpose; I3 is applied last (anything that went or may have gone ends
  `failed_unknown` at worst); `executing` maps to `failed_unknown` in every kind; the mapping is
  idempotent; one transaction per item, deletes and audit rows last.
- **OD-674, protocol rules (amends OD-669):** `Frozen` = `<job>/<config>` with three configurations and
  an exact-reverse release; every gate reads the truth (ecf-jobs its own DescribeStacks and the lock;
  the wait stamped only from a completed frozen stack); lock takeover only before `begin`; the rollback's
  local restore runs in the local service, which checks a per-job "aborted" marker row in its own
  transaction; `UPDATE_ROLLBACK_FAILED` handled by `ecf aws continue-rollback`; the bound machine is
  the hardware UUID.
- **OD-675, delays and smaller choices:** release (b) bumps `FreezeSeq`, the tick counts by its
  scheduled time, so no time across any freeze is counted; change sets with `DisableRollback=false`;
  the Slack app-level token taken before `commit`; `slack-out.fifo` purged after a rollback; the bot
  token a runtime secret; one stuck-freeze alarm kept enabled; the gate guard counts `public_cases` and
  `private_cases` (eval/private OD-637).

### After Phase R round 15 (SPEC OD-680 to OD-683, operator 2026-11-05)

Round 15 (8 Opus) found 7 highs, down from 10; three lenses found none. Three highs were rules the plan
text had that the models didn't (a re-stamp of the wait, a key check on every change set, the
countdown's `counted_at`); the rest were outside the models' scope (job IDs a lost laptop takes with
it, the freeze counter across a code deploy, the stuck alarm, a re-run after the local restore, a
raised retention setting). The operator accepted every recommendation. Draft 16 extends the models to
those scopes first, adds a check that each job can actually reach `done`, and ties every rule in the
text to a model check (Maintenance, "Rule → model"), so no rule exists only in prose.

- **OD-680, text tied to the models:** every Maintenance rule names the model function or check that
  encodes it, or is a stated limit or "Not modelled"; a Step 6 test checks the table; the models check
  that each job can reach `done` by the laptop alone.
- **OD-681, protocol (amends OD-674):** `status` and a refused create return the lock holder;
  `aws unfreeze` aborts a `freezing` lock holder when `Frozen` is empty; every gate and the takeover are
  conditional transactions; `prepare` extends the expiry (cap 24 hours); the wait is stamped once per
  job; stops are applied before `running`, and only for kinds that read items; I8 compares with the
  seed's `AWSCURRENT` version; non-stop clicks are refused in QUIET; the deployer can't `UpdateStack`.
- **OD-682, delays and alarms (amends OD-675):** release (b) sets `FreezeSeq` to the releasing job's
  ID; `counted_at` advances on both branches; the stuck-freeze alarm is the tick heartbeat, always on;
  no `slack-out` purge.
- **OD-683, items and the move back (amends OD-673):** the retention watermark, re-checked at the
  merge; a settled `failed` row wins; `merged_by` marks; parent rows live-wins; merges never write
  `IDENT#`; the local restore writes `rollback_restored:<job>`, enqueues the "moved back" edits in its own
  transaction, and v1's own reissue and `set-tokens` finish the move; `--abort` goes through a service
  route; secret names carry `install.id` (amended by OD-691, OD-692 and OD-694).

### After Phase R round 16 (SPEC OD-690 to OD-694, operator 2026-11-05)

Round 16 (8 Opus) found 7 highs, as many as round 15. Four were places where a model made one step of
what is two or more real writes, or assumed a value the system must compute: the rotation's key move
(a file, then a secret label), the local restore (a commit, then steps after it), a release (b) that
never failed, and a retention bound taken as given. The other three were outside the models: the
stuck-freeze alarm delivered through a frozen function, secret names that repeat on a re-migrate, and
`--abort` text that ran on after the restore committed. The operator accepted every recommendation.
Draft 17 audits every model step against the real write it stands for
(`state-archive/m1/model/draft17-atomicity.md`), splits the four, adds the missing failures, and only
then changes the text.

- **OD-690, how the models are built:** each model action is one real write (a file write, an API
  call, a DynamoDB transaction, a SQLite transaction) and a kill can fall between any two; nothing a
  real system computes is assumed in a model; the runner fails unless each seeded fault is caught by
  the check it targets; a Step 6 test fails on any Maintenance rule missing from "Rule → model" or
  "Not modelled".
- **OD-691, keys and secrets (amends OD-660, OD-681):** the rotation moves `AWSCURRENT` first, decided
  from `status`, and `finish` needs `AWSCURRENT` = the deployed keys; `aws-keys.json` follows
  `AWSCURRENT` on every run; every change set's own key parameters are checked before execute; the
  rotation-pending record is removed; the rollback and `destroy` delete the runtime secrets without a
  recovery window; the deployer policy is written as one statement per resource.
- **OD-692, the move back (amends OD-683):** the local restore's row lives in its own table and
  records progress; the service finishes the restore's later steps itself after a crash, and `finish`
  waits for them; `--abort` stops unless it wrote the marker; the "moved back" edits use their own
  message keys; `ecf slack reauthorize` replaces the manual `set-tokens` step (amended by OD-701: both
  run, `reauthorize` then `set-tokens`).
- **OD-693, delays and alarms (amends OD-682):** a tick whose own environment says frozen counts
  nothing, so I1 no longer depends on CloudFormation's update order; the tick's write is a
  compare-and-swap on `counted_at`; the stuck-freeze alarm goes to the `-direct` email topic; the
  release reads the functions' concurrency before release (b).
- **OD-694, items (amends OD-673, OD-683):** the retention bound is a formula over the retention
  history plus a 24-hour merge cap, checked at `first` and by every merge chunk; `merged_by` is the
  source key; a restore expires approved items that never started; `ecf migrate run` uses the import
  mapping; merges don't touch cards and `--replace` deletes nothing.

### After Phase R round 17 (SPEC OD-698 to OD-702, operator 2026-11-06)

Round 17 (8 Opus) found 7 highs, as many as rounds 15 and 16; the models were clean again, and every
high was in something they didn't generate or a value they read that no real read gives: whether a
restore request is with the local service, whether this job's seed version exists, a reopened item,
a deploy outside a job, a merge chunk running past the cap (hidden by the model's 1-hour grid), and a
Slack token step dropped by the last round's fix. Two lenses separately proposed deleting the
retention bound. The operator accepted every recommendation: the bound is replaced, the models lose
their last oracles and gain the missing events, and the text follows them.

- **OD-698, items (amends OD-670, OD-694):** a source item whose live item is absent but whose
  `item.created` audit row is in live was pruned and is skipped; the retention bound, the 24-hour
  merge cap and their refusals are removed; leaving a terminal status removes `expires_at`; every
  terminal item a merge or `migrate` writes gets one; the card sweep touches only items with no live
  grant and records its queued card before the SQS send; the tick re-cards any approval left without
  a grant.
- **OD-699, freezes (amends OD-693):** a deploy outside a job keeps `Frozen` and refuses a non-empty
  one; a tick on a new `seq` dates it from its invocation; the stack sets each function's event age;
  the `-direct` topic admits CloudWatch; the heartbeat comes only from a tick that wasn't skipped.
- **OD-700, rotation (amends OD-691):** every machine decides by this job's version ID (its
  `ClientRequestToken`), never by a label; the seed payload is canonical; the `AWSCURRENT` move is a
  compare-and-swap on the version read; gates read `AWSCURRENT` uncached.
- **OD-701, the move back and secrets (amends OD-691, OD-692):** the local restore commits only if
  the job has no row, so a second request rolls back; `reauthorize` then `set-tokens`; the tail's
  sub-steps retry on their own and a failing one is named; "moved back" text by status; the CLI checks
  the bundle's SHA-256; `migrate abort` force-deletes the transferred secrets; the one-time identity
  and the Slack configuration token are force-deleted by named roles.
- **OD-702, models and tests (amends OD-690):** no model actor reads a value no real read gives; the
  change-set precheck is made when the step is decided; the "Rule → model" table covers I1 to I8 and
  names only real functions or fields; Step 8 gives numbers for what I1 and I8 rest on.

### After Phase R round 18 (SPEC OD-720 to OD-724, operator 2026-11-06)

Round 18 (8 Opus) found 4 highs, down from 7 in rounds 15 to 17; a measurement reviewer re-ran
`run_all5.py` and matched all 68 lines. Two highs were events the models never generated (the clear
before a `migrate run` after `migrate abort`; the retention re-stamp job), one was text contradicting a
correct model (the restore tail on an `aborted` row), and one was a synchronous Slack call draft 18 had
added to that tail. Card reissue was described three ways, and F10's way couldn't be built under the
role table. The operator accepted every recommendation: the models gain the missing events first,
card reissue becomes one marker-based mechanism, and the text follows.

- **OD-720, items (amends OD-670, OD-698):** `migrate run` never applies the pruned-item skip (its
  target is empty by construction), and the clear before a re-run after `migrate abort` is named as the
  one `ITEM#` delete besides TTL; every `ITEM#` write is conditioned on the item not having expired;
  the retention re-stamp is a conditional write (still terminal, `expires_at` as read); excerpts are
  written in their item's transaction with its expiry; every `item.created` row, carried or new,
  carries the index attribute `created_sid`, and a merge reuses the bundle's own row; an import from
  another install lists and skips a `stable_id` that live holds under another address.
- **OD-721, the move back (amends OD-701):** an `aborted` row runs nothing, and the tail runs only from
  `committed`; the tail makes no Slack call (the commit leaves a "renew now" mark for the Slack
  thread); the Keychain seed is written only by a request carrying the typed key, so the start-up tail
  skips it; `ecf slack set-tokens` and `reauthorize` work during a rollback once its row is
  `committed`; the commit sets queued escalation and question cards that never posted back to pending.
- **OD-722, cards after a merge (amends OD-694, OD-698):** the merge marks an open approval
  `needs_card` in its item's transaction; `ecf-worker`'s tick writes its grant and card placeholder and
  removes the mark in one transaction conditioned on the mark, the grant ID read, the status and no
  grant row yet; the merge's sweep and the tick's sweep over every waiting item are removed.
- **OD-723, freezes and access (amends OD-693, OD-699, OD-701):** a deploy outside a job checks after
  its change set is created that the set's own `Frozen` is empty; I1 rests on crediting by the
  scheduled time, and `ecf-slack-in`'s Cancel and Reject don't read `Frozen`, since Lambda may serve
  the old environment briefly after an update; a run adopts a lock holder only if its kind and bound
  values match; the CLI confirms the alarm email subscription itself with `AuthenticateOnUnsubscribe`;
  the static test against `ITEM#` deletes covers every delete path; the one-time identity and the
  Slack configuration token are force-deleted when their job ends or expires and by `migrate abort`.
- **OD-724, models (amends OD-702):** `freeze_model7.py`, `map_model7.py`, `card_model.py`,
  `delay_model6.py`, run by `run_all6.py`; case counts reported by part; every fault that only
  restates its rule marked as such; the "Rule → model" table test checks names against the models'
  source.

### After Phase R round 19 (SPEC OD-725 to OD-731, operator 2026-11-06)

Round 19 (8 Opus) found 1 high (4 in round 18); the measurement reviewer re-ran 76 of `run_all6.py`'s
78 lines identically. The high was again a fix of the previous draft written as prose and called
"not modelled": adoption by bound values, which stranded a rotation after its seed write. The mediums
clustered where draft 19 added mechanism: the migrate clear, expiry stamped at every write, the card
tick, and environments read by the tick. The operator accepted every recommendation and a working rule:
no rule without a model event and a fault that fails without it, nothing that decides whether a job
can finish left "not modelled", and a mechanism removed rather than patched where possible.

- **OD-725, adoption and the move back (amends OD-721, OD-723):** a rotation is adopted by a machine
  without its key text once its seed version exists (before that only with matching fingerprints);
  `status` returns the holder's bound values and person, so the comparison is computable; the
  `committed` re-ask carries a fresh step-up, and the tail writes the Keychain seed only from a request
  whose bundle SHA-256 equals the one the row recorded at the commit; the start-up tail runs only for a
  `committed` row.
- **OD-726, `migrate abort` (amends OD-701, OD-720):** abort destroys the AWS install (tables, secrets,
  the pending Cognito identity), and a later attempt starts again from `init --migrating`; the migrate
  clear is removed, so no runtime role can delete an `ITEM#`; the one-time identity is deleted when its
  job ends.
- **OD-727, expiry (amends OD-698, OD-720):** v1's shape: no write stamps `expires_at`; one daily
  `ecf-jobs` sweep hides terminal items past their retention, conditioned on the item and on the
  retention setting as read; only items and their excerpts expire; every update requires the item to
  exist and not be expired; a migrate or import writes the bundle's settings before its items.
- **OD-728, cards (amends OD-722):** the tick waits for the AWS migrating hold to lift; one `CARD#` row
  per item; a resend at most every 15 minutes, none during a Slack hold; refused and outage give-ups
  told apart; question and "Needs you" cards come back on a move back; escalations reset by their own
  card key.
- **OD-729, the tick reads the stack (amends OD-693, OD-723):** the delay countdown reads `Frozen` and
  `FreezeSeq` from DescribeStacks (stable status only), never from the function's environment, so a
  stale or torn Lambda environment can't credit frozen time; the scheduled-time rule is again defence in
  depth.
- **OD-730, access (amends OD-723):** `ecf-worker` writes excerpts' expiry; deletes are allow-listed per
  role and no runtime role holds `BatchWriteItem` on the main table except ecf-jobs' put-only audit
  writes; the static test covers every write action in the Service Reference; the CLI owns the alarm
  email subscription and `doctor --fix` re-creates it; grant writes are scoped to `GRANT#`; install
  names have no dash.
- **OD-731, models (amends OD-724):** `freeze_model7.py`, `map_model8.py`, `card_model2.py`,
  `delay_model7.py`, run by `run_all7.py`; counts are what each check covers; every restatement
  marked; the models run in their own CI workflow.

### After Phase R round 20 (SPEC OD-732 to OD-740, operator 2026-11-06)

Round 20 (8 Opus) found 2 highs (1 in round 19); the measurement reviewer re-ran 81 of `run_all7.py`'s
83 lines identically. Both highs sat in draft-20 text with no model event: a rotation adopted after its
seed write could not build its deploy (the seed holds fingerprints, not the public keys), and children
kept after their item made an AWS export unrestorable by v1 (foreign keys). The mediums clustered in
the same place: the re-ask needing a bundle that expires, the local hold counting delays, the tick
gated on the stack's status, batch writes IAM can't make put-only, the card post step, and a
cross-reference check that matched substrings. The operator accepted every recommendation and moved
finishing a job from another computer to M2 as its own task (OD-732, on `main`).

- **OD-733, rotation on its key text's machine (amends OD-725):** only the machine holding a
  rotation's key text runs it; no other machine adopts a rotation; a lost machine mid-rotation is a
  stated limit from `rotation_list` to `finish` (restore, then destroy).
- **OD-734, children expire with their item (amends OD-727):** the item lists its records and a
  `children_n` counter; the sweep hides the item and every record it lists in one transaction,
  conditioned on the counter; creates replace a hidden record; `DRAFTV#` rows expire by their own
  `created`, as v1.
- **OD-735, the move back and the local hold (amends OD-725, OD-726):** the `committed` re-ask carries
  only the typed key, checked against the key fingerprint the row recorded; the local `migrating` hold
  counts no delay.
- **OD-736, the tick's stack read (amends OD-729):** the read gates only the countdown and `delayed →
  executing`; the stable-status rule is removed; a failed read counts nothing; Step 8's pass line is
  the read's lag under 30 s; two tick metrics.
- **OD-737, access and destroy (amends OD-726, OD-730):** no runtime role holds `BatchWriteItem` on
  the main table (audit rows by create-only `PutItem`); the delay fields live on the item; no excerpt
  expiry rights outside ecf-jobs; the boundary is generated from the role table; `migrate abort`'s
  steps (step-up, lock, audit copy, deletion protection and PITR off, bucket emptied) are written out;
  the alarm email on a scanning mailbox is a stated limit.
- **OD-738, the sweep (amends OD-727):** transactions of up to 100 actions with per-item conditions,
  a lease, and a duration pass line.
- **OD-739, cards (amends OD-728):** the post's mark is conditioned on the rendered grant; every way a
  post dies ends its row; escalations reset by the key of the card that carried them, whatever the
  item's status; every question card re-posted on a move back; no per-item "Needs you" card.
- **OD-740, models and checks (amends OD-731):** `freeze_model8.py`, `map_model9.py`,
  `card_model3.py`, `delay_model8.py`, run by `run_all8.py` (file hashes, stated-limit counts);
  `check_refs.py` matches whole identifiers and checks itself; the biggest scopes run on the macOS
  gate, not CI.

### After Phase R round 21 (SPEC OD-741 to OD-748, operator 2026-11-06)

Round 21 (8 Opus) found 2 highs (2 in round 20); the measurement reviewer re-ran 105 of `run_all8.py`'s
lines identically. Both highs came from events the models didn't generate: the rollback bundle
expiring before the local commit (no pull was required before it), and the stack read's lag counted
from an update's start while Step 8's pass line counts it from its completion. The round-20 fixes
measured clean, but several new checks restated their rule, and several IAM claims were wider than
IAM enforces. The operator accepted every recommendation, keeping OD-733 (no machine finishes another
machine's rotation; OD-732).

- **OD-741, the bundle before the commit and the re-ask's route (amends OD-725, OD-735):** the laptop
  pulls the rollback bundle, verifies its SHA-256 and keeps it before `commit`; an object that expired
  first is exported again. The `committed` re-ask has its own route: the service reads the
  `rollback_restore` row before opening any bundle, its step-up binds the job ID and the row's SHA-256,
  and the row records what the tail needs.
- **OD-742, a fixed wait before freeze (b) (amends OD-736):** the laptop starts freeze (b) only 45 s
  after freeze (a) completes, so a stack read up to 30 s behind, from either end of an update, sees
  `Frozen` before Cancel can stop; Step 8 measures the lag from both ends.
- **OD-743, what IAM enforces (amends OD-726, OD-730, OD-737):** one permissions boundary per runtime
  role (each under the 6,144-character limit); explicit Denies on `UpdateItem` of `AUDIT#` rows and on
  any `PutItem` of one naming `expires_at`; the deployer has no S3 delete rights (the stack's
  auto-delete helper empties the bucket); `logs/` is create-only by bucket policy. AWS permissions
  stop deletes; an item's contents and expiry marker are protected by ecf's code.
- **OD-744, `migrate abort`'s order (amends OD-726, OD-737):** stop AWS's writers, copy the audit
  rows, compare them with `logs/` (a mismatch is a Security Notice), record the copy, then delete; its
  own step-up purpose; scratch tables deleted.
- **OD-745, rotations and key files (amends OD-733):** a rotation's first chunk applies pending stops
  and writes a rollback-kind export, which the lost-machine limit restores; a machine that didn't run
  a rotation moves its `aws-keys.json` only after the operator types the new fingerprint.
- **OD-746, item records and the sweep (amends OD-734, OD-738):** an item's records are a fixed set
  (its excerpt, `CARD#` row and escalation row); the exporter drops a record whose item it didn't
  export; a sweep page that meets a raised retention ends and is read again; the lease is removed.
- **OD-747, cards (amends OD-739):** an unknown-code give-up is re-posted once, as in v1; a card left
  by a newer grant is marked "replaced" at once; a scan run's fraud lines still queued at a move back
  are reported again on the Mac; the move back edits only open items' cards; two reissue conditions
  that no fault needed are removed.
- **OD-748, models and checks (amends OD-740):** `freeze_model9.py`, `map_model10.py`,
  `card_model4.py`, `delay_model9.py`, run by `run_all9.py` (expected stated-limit counts for masked
  faults, `--ci` for the subset CI runs); `check_refs.py` v3; a restatement list the check compares;
  the local hold resets the tick clock when it lifts.

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
  mappings and the tick schedule's role; `ecf-jobs` only by its `jobs.fifo` mapping, or by the deployer's direct invokes for a freeze job's kinds (Maintenance, F6) (housekeeping
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
  | `jobs.fifo` (+ DLQ) | the API roles, housekeeping schedule roles | `ecf-jobs` | 1,000 (crashes counted, "After round 11") |
  | on-failure queues (standard), one each for `ecf-broker` and `ecf-worker` (the async-invoked functions) | Lambda, with that function's role | admin | — |
  | the Scheduler DLQ (standard, one shared) | every schedule's role | admin | — |

  Alarms on Lambda's `DestinationDeliveryFailures` and Scheduler's
  `InvocationsFailedToBeSentToDeadLetterCount` and `InvocationDroppedCount`; Step 8 checks each
  failure path.
- **Secrets:** exact ARNs in role policies for the deploy-time secrets (the Slack signing secret and
  the export seed; the bot token and mailbox secrets are created at runtime under
  `ecf/<install>/<install.id>/...` and guarded by name-pattern identity grants plus the boundary,
  Maintenance F14), which carry a resource policy denying `GetSecretValue` unless `aws:PrincipalArn` is an
  owning role (for the seed, `PutSecretValue`, `UpdateSecret`, `DeleteSecret`, `UpdateSecretVersionStage`, `PutResourcePolicy`, `DeleteResourcePolicy` and `RestoreSecret` are denied to every
  runtime role, one list used everywhere; the laptop writes it with the deployer credentials, "Write-once keys");
  for mailbox secrets only the broker's role may read and `ecf-api`'s role may only `CreateSecret`/
  `PutSecretValue` (identity grants on the name pattern; no resource policy). CI checks only the broker
  can read a mailbox secret.
- **Bearer:** `ecf-api` validates the `Authorization` bearer itself and never reads
  `requestContext.authorizer`; a request carrying a profile or agent token is that profile, never the
  CLI.
- **Grants (OD-624):** grants and spend records live in the grants table. Three paths create a grant,
  all audited: in the API roles, a person's approval (`people.id` set) and v1's policy apply
  for automatic actions (`decide.apply`, `src/ecf_server/decide.py:293-311`, `principal` `service`,
  `people.id` null, source `policy`); and `ecf-worker`'s card tick after a restore, import or migrate
  (F10; source `restore` or `import`, `people.id` of the original approval's person, its own
  `action.granted` row, so the cross-check counts it), with the policy's corroboration and batch-risk rules ported
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
| `ecf-api` role | API Gateway | all tables; S3 `eval/` | main (incl. `ConditionCheckItem` on `STOP#*` and `MAINT`); `ecf-api` table; grants table (put, update, `GRANT#*` only, OD-730); S3 `jobs/` presigned URLs (incl. GETs on `exports/`, `backups/`); sends `slack-out.fifo`, `jobs.fifo`; invokes broker | mailbox secrets and Slack secrets write-only (`set-tokens`, migrate); relink and export verify keys (stack parameters); Cognito `AdminUserGlobalSignOut` |
| `ecf-worker` role | `slack-in.fifo`, `slack-out.fifo`; tick schedule role | all tables (incl. `Query` on the unapplied-stop index) | main (incl. applying stop records: update `STOP#*`); `ecf-api` table; grants table (put, update, `GRANT#*` only, OD-730); `cloudformation:DescribeStacks` on its own stack (the tick, F8); `Query` on the `needs_card` and `card_queued_at` indexes; S3 `logs/` (create-only); receives and deletes on both Slack queues (the only consumer); sends `slack-out.fifo`, `jobs.fifo`; invokes broker | Slack signing secret, bot token |
| `ecf-broker` | `ecf-api`, `ecf-worker` (async) | main; `ecf-api` table (settings, read); grants table | main (closed list); grants (status and consume attributes); spend records (create only); excerpts; sends `slack-out.fifo` | mailbox secrets (read, `lambda:SourceFunctionArn` condition) |
| `ecf-jobs` | `jobs.fifo`; the deployer's direct invokes for a freeze job (F6) | all tables (incl. `Scan`); S3 | main and `ecf-api` table (import, restore, migrate, merges, the expiry sweep and its `ConditionCheckItem` on the settings item); excerpts (import, restore, migrate, merges, the sweep); grants (delete only, OD-658); `MAINT`; S3 `exports/`, `jobs/`, `backups/`, `eval/`; backup and scratch tables; sends `slack-out.fifo` | export seed (read); age recipient and verify keys (stack parameters); `job/<id>` one-time identity (create, read, forced delete, OD-730) |
| `ecf-slack-in` | API Gateway | `ecf-api` table: `IDENT#` items, people and roles only (`dynamodb:LeadingKeys` on those prefixes, `dynamodb:Attributes` limited, `dynamodb:Select` specific attributes; no `Query` or `Scan`); main: `ITEM#*` status, `grant_id` and `answer_pending`, `CARD#*` (`pk`, `sk`, `message_ts`, channel), and the `MAINT` item, the same limits | `PutItem` only, create-once, on `STOP#*` and `AUDIT#SLACKIN#*` (OD-644, OD-649); `ConditionCheckItem` on `ITEM#*`, `STOP#*`, `IDENT#*` and the person (`ReturnValues` `NONE`, its own Attributes list); Slack table (dedupe keys); sends `slack-in.fifo` | Slack signing secret; bot token (`views.open`) |

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
    reference ("render item X's card", deduplication ID a ULID per request, except a reissued card's,
which is F10's; edits in `card:<item>`) and
    rendered from the item's status and `card_note` when sent; ephemerals and DMs go by value (OD-651);
    a click arriving during a hold is put back with its visibility set to the hold's expected end, not
    counted as a crash (OD-650);
  - **the tick** (every minute): a lease keyed on the schedule's scheduled time (`TICK#<time>`), event
    age 60 s, each sweep time-boxed within 50 s; due checks (async invoke of the broker, with a
    `dispatched_until` the broker extends to its lease's expiry while it runs and clears on release, so
    a run that never started is retried after 5 minutes and a running one gets no second invoke; a busy
    lease writes no due time while its holder is live); approval expiry,
    the delay countdown (Maintenance, F8) and `delayed → executing` (making that address's check due now; a transaction with a `ConditionCheck`
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
  `/ecf-eval` preparation, and housekeeping (the daily expiry sweep, digests, daily summary, model
  watch, scheduled export), all through `jobs.fifo`; the deployer invokes it directly for a freeze job's
  kinds (Maintenance, F6), which never run from `jobs.fifo`. A job writes a heartbeat to its progress item,
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
- **The export hold (OD-655, OD-658; scheduled exports only):** every function reads `MAINT` when a
  request, message or tick starts and does nothing while the hold is up; broker and job leases are
  taken only with a `ConditionCheck` that no hold is up, and their holders re-check between steps. The
  holder sets the hold, waits for the leases to drain, then waits 90 s more (the longest unleased
  timeout plus margin) before it reads. `MAINT` names the holding job, whose lease the drain skips and
  whose messages `ecf-jobs` runs during the hold. Exceptions to "does nothing while a hold is up": the
  holding job; `ecf-slack-in` (which reads `MAINT` in its `BatchGetItem` and accepts stops during the
  hold); `ecf-worker`'s result ephemerals (OD-651); the tick's re-queue of the holder, its heartbeat and
  the no-holder alarm; and `slack-out` posts that write only live-kept records. The holder's
  continuations use their own `jobs.fifo` group; a job arriving during the hold is put back to its
  expected end. Everything else that quiesces AWS is a freeze job (Maintenance).
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
  REMOVEs the delay fields and sets `terminal_at` (OD-727: nothing sets expiry but the sweep; v1's
  `delays` row is the `remaining`, `seq` and `counted_at` attributes of the `ITEM#` row, so no runtime
  role deletes on the main table for a delay, OD-737); every broker transaction carries the lease fencing
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
  | v1 allow-list tables | yes | yes, plus `slack_messages`, `draft_versions` | as `migrate` | restore: v1's `downgrade`; import: v1's import (empty target or `--replace`); items by OD-670 as amended by OD-673 (Maintenance); per-table rule in the mapping |
  | people, roles, `IDENT#` identities | yes | yes | yes | OD-596, OD-622; one rule (OD-670, People): no AWS restore or import writes `IDENT#`, people are live-wins where present; the rollback's local restore applies the bundle's revocations |
  | audit (`uid`) | yes | yes | yes | insert when `uid` absent, never delete |
  | grants, `GRANTUSE#`, `GRANTRES#` | no | no | no | `GRANT#` deleted (voided); attempt and outcome records kept |
  | `STOP#` stop records | no | no | no | pending ones applied by the freeze's first chunk (F7); live-wins (a live terminal status always wins, OD-670) |
  | `AUDIT#SLACKIN#` rows | as audit | as audit | as audit | as audit |
  | nonce, `RATE#`, `MAINT`, `JOB#` job records, leases, `TICK#`, dedupe; Slack pacing and hold records, per-post crash counts, cards' rendered versions | no | no | no | live kept |
  | `alert_outbox` (unsent alert email) | no | no | yes | the local restore queues it again |
  | `CARD#` card records | no | no (`slack_messages` carries the cards) | as `migrate` | the one `CARD#` row kept; the tick re-queues it for the new grant with `replaces_ts`, and `slack-out` edits the old message to "replaced" (F10) |

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
  locally `derive(root)`'s public keys are compared with `export.key`'s recipient and verify key
  ("After round 10"), so no relink key is stored locally. The old identity is revoked in the same transaction
  (status `revoked`, its refresh tokens revoked); a later restore of an older bundle doesn't bring a
  revoked identity back (the restore keeps the live identity table's revocations). Audited, with a
  Security Notice. Locally, a same-install restore under the typed key creates a new OS-user binding (stored machine-local, beside `export.key`, never in bundles) and revokes the earlier one.
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
- **Expiry (OD-727, OD-734, OD-738, OD-746; v1's shape, `retention.py:5`):** no write stamps
  `expires_at` but the sweep. A terminal transition sets `terminal_at` (the time the item entered the
  terminal status); every transition out of a terminal status (executed → undoing or reprocessed,
  undone → proposed, observed → reprocessed: v1 allows them, `state_machine.py:89-96`) REMOVEs it. An
  item's records are a fixed set, at most one of each: its excerpt, its `CARD#` row and its escalation
  row (`ESC#`), the per-item rows v1's export carries with a foreign key to `items` or a card reference
  (`export_bundle.py:59-62`; `0001_initial.sql:83`, `0015_escalations.sql:14`); the item lists the keys
  of those it has (`children`), and every write that creates one, a merge's included, is one
  transaction that also ADDs its key to the item's `children`, with the item's conditions below (faults
  `childuncond`, a child created without them, and `mergenochild`, a merged child not listed: either is
  left visible and a recreated item inherits it). Once a day an `ecf-jobs` sweep (one `jobs.fifo` kind,
  so its runs are serial; no lease: two overlapping runs are safe under the per-item conditions, OD-746)
  reads the item retention setting (with its version) per page, Scans for terminal, prunable items
  (never fraud, weak-fraud, regulator, quarantined) with no `expires_at` whose `terminal_at` + retention
  has passed, and writes `expires_at` = now on each item and on every record it lists, in
  transactions of at most 24 items (each item at most 4 actions: itself and its 3 records) plus one
  `ConditionCheck` on the settings item (the retention version as read), each item's Update conditioned
  on the status still terminal, `terminal_at` and `children` as read and no `expires_at`. On a cancel
  (`CancellationReasons`): an item whose entry is `ConditionalCheckFailed` is dropped and the rest
  retried; a failed settings check ends the page, and the sweep re-reads the setting and Scans that page
  again; an item whose entry is `TransactionConflict` (a live write) is dropped and the rest retried,
  with backoff, and the next sweep takes it (map_model10 `lifecycle_timelines`, `children_timelines`,
  `sweep_batch`: faults `sweepuncond`, an open item deleted by TTL; `sweepnoset`, an item hidden before
  a raised retention; `batchnocond`, an open item hidden by a batch; `dropsetcheck`, a retry without
  the settings check after it failed, hides items under the old retention; `retryall`, retrying the same
  batch on a failed settings check, never ends; `nochildver`, a child created between the sweep's read
  and write left visible; `childkeep`, draft 20's kept children, inherited by a recreated item;
  `childstamp_early`, draft 19's children stamped at the terminal transition and deleted under a reopened
  item). So an item and everything it lists hide at the same instant, and v1's cascade holds. The
  exporter's per-table Scans are not atomic with the sweep, so it writes a record only if the item Scan
  returned its item, and every reader hides a record from its own `expires_at`; TTL deletes each some
  time after (v1's restore has foreign keys on, `db.py:76`, so an exported record without its item
  can't be restored; map_model10 reads the record a step before or after its item, fault
  `exportnofilter`). `DRAFTV#` rows are not an item's records (v1 prunes them by `created`,
  `0043_draft_versions.sql:2`): the same sweep hides those whose `created` + retention has passed, under
  the same settings check (Not modelled: they decide no job's finish, and a restore tolerates a draft
  with no item). A changed retention applies at the next sweep, as v1; nothing re-dates items, and a
  merge, import or `migrate` writes no expiry (the next sweep applies the current setting). Every update
  of an `ITEM#` or of a record it lists carries `attribute_exists(pk) AND (attribute_not_exists(expires_at)
  OR expires_at > :now)`, and every create `attribute_not_exists(pk) OR expires_at <= :now`, so a new
  message or escalation under the `stable_id` of a hidden item replaces the hidden record rather than
  being refused (a contract test over every writer, which also treats a refusal for expiry as "gone",
  not as an error; faults `noexpcond`, a write bringing a hidden item back; `noexists`, an update after
  the TTL delete upserting a bare item; `createstrict`, a new message or escalation dropped over a
  hidden record). Step 8 records the sweep's Scan seconds per GB and items per second; its pass line
  is a seeded table of 2 million items with their records and audit rows, 100,000 of them due,
  on-demand capacity, swept in under 1 hour (at 500 messages a day and the 3,650-day maximum
  retention, lowering retention to the minimum makes about 1.8 million items due: under a day at that
  rate). `ecf retention set` retries a
  TransactionConflict with the sweep for up to 30 s (a Step 8 test runs it during a sweep). CloudWatch
  log retention separate; `AUDIT#` rows never get a TTL (OD-217; round 6; an explicit IAM Deny on
  `UpdateItem` of `AUDIT#` rows and on a `PutItem` of one naming `expires_at`, OD-743), and the
  `item.created` rows carry `created_sid` (= `stable_id`, with the address ID projected), set on every
  such row whoever writes it, with a sparse index on it (OD-670's pruned-item rule; Step 0 sizes it).
  No runtime role holds a delete right on an `ITEM#`, a record it lists or an `AUDIT#` row (a static
  test, Infrastructure); their expiry marker, which TTL acts on, is set only by the sweep, which ecf's
  code enforces (a contract test) and IAM enforces for `AUDIT#` rows alone (OD-743).
- **S3:** one bucket; Block Public Access; `BucketOwnerEnforced`; versioning; SSE-S3;
  `ExpectedBucketOwner`; TLS-only. `logs/` create-only (a bucket-policy Deny on `s3:PutObject` to
  `logs/*` without the `s3:if-none-match` condition, conditional-writes-enforce.html, fetched 2026-11-06
  (rv); OD-743), no expiry and no noncurrent-version expiration, `s3:DeleteObject*` denied to every
  runtime role and the deployer: the tamper-evident audit copy. `exports/`, `jobs/` expire in 1 day;
  every other versioned prefix has a noncurrent-version expiration rule; `backups/` pruned to `export_keep` in code, never
  pruning an object younger than the longest presigned URL; `eval/` holds result files and the
  uploaded adversarial subset (OD-618), by hash.
- **Crash safety:** `processing <stable_id>` counted across invocations; quarantine after 2 crashes; a
  lapsed lease with a second holder doesn't count an attempt (a test).
- **No temporary files** on the Lambda code path (a test).

### Data migrations, upgrades and backups

- **Expand and contract:** additive attributes, lazy upgrade by `storage_version`, idempotent backfill
  with a progress item; a full rewrite needs a planned outage.
- **Before the change set:** `ecf upgrade` is a freeze job (Maintenance, F11): the freeze, the
  change set with `Frozen` set, `upgrade_last`, the release; its restore point is PITR at the freeze's
  `quiet_at` (Maintenance F5; no on-demand backups, OD-671). Refused while busy (OD-379).
- **Consistent exports (round 4 S8, OD-646, OD-655):** a scheduled export sets the maintenance hold, waits for leases to drain and then the 90 s grace (OD-655), and reads the live
  tables with strongly consistent parallel Scans while the hold lasts (DynamoDB gives no snapshot across
  a Scan, only per item, API_Scan.html, fetched 2026-11-05 (rv); the hold and grace are what make the
  read consistent: no writer can still be running); rows written after the hold began, stops
  included, are listed in its report. A rollback export is a freeze job instead (Maintenance). A scheduled export doesn't look for stops (they aren't
  exported); when Step 8's measured Scan time passes its line (with a margin against flipping), it
  records a point in time T after the grace instead, lifts the hold, waits until
  `DescribeContinuousBackups` shows `LatestRestorableDateTime` ≥ T (about 5 minutes), and reads scratch
  copies restored by PITR to T without secondary indexes (named `ecf-<install>-scratch-<its job
  id>-<table>`). The scheduled export re-checks the age recipient's fingerprint against the expected
  value in the seed secret (OD-660) and alarms on a change. The migrate export is local, not one of these.
- **`ecf upgrade --to <previous>`:** code only; data forward-compatible.
- **Restoring:** never by swapping tables. An AWS restore or import is a freeze job (Maintenance, F2
  and F11): scratch tables restored before the freeze, the merge on the frozen stack by v1's rules and
  OD-670 (as amended by OD-673, Maintenance), per-table rules from the mapping (parent rows live-wins where the live row exists; Maintenance,
  OD-670), cursors rewound, the scratch tables deleted by housekeeping after `finish`. Restores are PITR only (recovery period set to 35 days, the maximum; the latest
  restorable time is typically 5 minutes before now: PointInTimeRecovery_Howitworks.html, fetched
  2026-11-05) and asynchronous; each `prepare` call polls `DescribeTable`. Rights, `ecf-jobs` only:
  `RestoreTableToPointInTime` on the four source table ARNs and `table/ecf-<install>-scratch-*`, the
  data-plane writes a restore needs and full item rights on `ecf-<install>-scratch-*` only (install
  names are `[a-z0-9]+`, no dash, so one install's scratch pattern can't match another's tables;
  OD-730),
  `DescribeTable`, `DeleteTable` on the scratch tables; which resource each action is authorised
  against is unverified (Step 8 runs both the export's and a restore's PITR path under the boundary,
  since moto doesn't check it).

### Maintenance: freeze jobs and the export hold (OD-666 to OD-724)

This section is the only current text on how AWS is quiesced; other sections cite its rules (F0 to
F15) and OD-670's mapping. OD-661 to OD-665 are history; OD-655 and OD-658 now describe only the
export hold. Where any other line differs, this section wins.

**Two mechanisms (OD-668).** A **freeze job** for every operation a person runs: the rollback, an AWS
restore, an AWS import, `ecf upgrade` and the rotation deploy. The **export hold** (`MAINT`, kind
`scheduled_export` only) for the unattended scheduled export (OD-655, OD-658's holder rules).
`prepare`, `begin`, `abort` and `status` are exempt from the hold; the first chunk cancels an export
in flight.

**How this section is checked (OD-669, OD-672, OD-680, OD-690, OD-724, OD-731, OD-740).** The rules are written from executable models
in `state-archive/m1/model/` (Step 6 moves them into `tests/m1/`), and the table at the end of this
section maps every rule to the model function or check that encodes it; a rule with no entry there is
a stated limit or listed under "Not modelled". Since round 19 (OD-731) a new or changed rule enters
the text only with a model event and a seeded fault that fails without it, or is marked defence in
depth (its fault masked, with the rule that masks it named) or a restatement, and nothing that decides
whether a job can finish may be "not modelled" (a superset model proves safety, not that a job
finishes: draft 19's adoption rule, and draft 20's, whose adopter could not build the deploy). Each
model action is one real write (a file write, an API call, a DynamoDB transaction, a SQLite
transaction), a kill can fall between any two, nothing the real system computes is assumed, and no
actor reads what no real read gives it (`draft18-atomicity.md` to `draft22-atomicity.md` list each
action with its write and its reads).
`freeze_model9.py` explores every interleaving of two
jobs (the second a one-step job or, in the `+rot2` run, a second rotation started on the other
machine, which alone holds its key text), two machines and a second local process (in the `+rot2` run
also a second terminal on machine 1 re-running the first rotation while the first process still runs:
with each rotation on its key text's machine, that and the other machine's rotation are where the
`AWSCURRENT` compare-and-swap is needed, fault `nocasmove`), `aws unfreeze` from any machine, a deploy
outside any job (`+foreign`), up to two restore requests with the local service at once, a service
crash between the restore's commit and its tail, the rollback bundle's S3 object expiring before the
pull and the kept copy lost after the commit, a replaced Slack bot token, kills, a lost laptop, and
stack updates in progress, failed, rolled back or stuck in `UPDATE_ROLLBACK_FAILED`; each actor decides
only from what it can read, and a machine knows a job only from its own command, its `aws-freeze.json`,
`Frozen` or `status`'s lock holder. It checks safety (I2 for both jobs), that some named command always
reaches a clean end with every live machine's `aws-keys.json` following `AWSCURRENT`, that each job
can reach `done` by the laptop alone, and that each base kind's stated-limit count is the one the
runner records (a rise is a strand the text doesn't state). `+strict` checks the stricter adoption
rule (neither machine adopts another's upgrade, restore or probe). `delay_model9.py` checks F8 over
27,778,576 timelines (failed updates, a failed release (b) under both rollback orders with
DescribeStacks showing the new or the old parameters mid-update, an abort from QUIET, items created
mid-freeze, ticks delivered late into the freeze or after the release, a second freeze carrying a
previous job's value, overlapping ticks, the item's creator on the oldest configuration of its stale
window, the stack read up to 30 s behind a change, counted from the update's start or from its
completion, or failing, the shortest cycles on a 1 s grid, and v1's tick under the local `migrating`
hold and its lift); `map_model10.py` checks OD-670 and Expiry over 109,725 merge cases (the
combinatorial mapping), 339,307 item lifecycles (reopens, a retention change between sweeps, the
sweep racing a reopen, TTL at any time: they check the sweep and TTL), the skip rule's never-here
reads and its faults, 34 distinct `migrate` bundles killed and resumed, swept between, or aborted,
18,360 child-record timelines (the exporter's per-table reads apart by a step, a child written by a
merge), every order of a bundle's settings and items, 516 sweep pages (a reopen, a live write's
conflict or a raised retention between the read and the write), 17,850 `migrate abort` audit
timelines and the lost-machine restore of a rotation; `card_model4.py` checks card reissue for two
items under a two-item tick with kills between items (part A, 72,486 states), one card row's post,
mark, resends, holds, give-ups and "replaced" edits (part B, 10,322 states), and the move back's
escalation, scan-reply and question cards against what v1 then does on the Mac (part C).
`run_all9.py` prints each model file's SHA-256, runs the models in parallel by default (cores minus 2
at once, under a memory budget of 75% of RAM; base kinds copied from an earlier run only when the
recorded SHA-256 matches), then every counterexample from rounds 14 to 21 seeded back in as a fault,
and fails unless each is caught by the check it targets; a fault caught only by restating its rule
is marked, and a masked fault's stated-limit count must equal its expected value. The full
`rollback`, `upgrade+foreign` and `rotation+rot2` base kinds (17,569,986, 8,140,020 and 4,408,263
states in `run_all9.out`'s `freeze_model9` lines; about 18, 7.5 and 4.5 GB) and the faults
that explore them run in the macOS merge gate; CI's models workflow runs `run_all9.py --ci` (every
other line); both trigger on the same paths: the models and the pure functions they import (Step 6).
**Restatements.** These faults are caught only because the check restates the rule they break:
`clockwait`, `envearly`, `fifo`, `runningfirst`, `startseed`, `anycommit`, `foreignnorefuse`,
`oldrule`, `skipnoaudit`, `nomark`, `noexists`, `requeueall`, `holdresend`, `qts`, `foreignfrozen`;
each rule is also carried by the Step 6 test named with it.
**Principle:** every gate reads the truth directly (ecf-jobs its own stack,
record and lock; the laptop `status`, the stack and its own files; the local service its own database
inside its own transaction); nothing is inferred from timing, environments, another machine or
another process.

**Invariants.** I1 A `delayed` send goes out only after Cancel has worked for its whole delay. I2 Every
Cancel or Reject that committed is applied before any export or merge reads, and its card shows the
true result. I3 Nothing that went or may have gone is offered again or left requeueable. I4 One active
job at a time, authorised by one active admin and holding the lock (every gate and the lock takeover
are conditional writes, so this needs no timing argument); nothing runs on a frozen stack
except that job's chunks. I5 From every state any exit leaves, a named command reaches a running
install (AWS or local) with no job lock held, except the stated limits below; after a restore or
import every address is paused, as in v1, and is resumed after its check. I6 No Slack post, card or
notice is dropped by maintenance, except Slack messages still queued on a move back (by value or by
reference; each item is covered by its "moved back" edit, the local reissue, an escalation set back to
pending, a question card enqueued again, or the pinned "Needs you" list, and the report counts them,
F11), and cards
render current state when sent. I7 While a job reads or merges, nothing else writes. I8 The deployed key
parameters change only in a rotation's own deploy, and every change set (the rotation's own included)
and every release runs only when they equal the fingerprints of the seed secret's `AWSCURRENT` version,
and the change set's own key parameters equal them too (the rotation deploy: the pending version's).

**F0 The driver is a reconciler.** Each run of the command, first or resumed, on any machine with the
deployer credentials, reads: `status` (the record, its expiry and progress, the bound machine, the
fingerprints ecf-jobs computes from its environment and those of the seed secret's `AWSCURRENT` version,
read uncached);
DescribeStacks (`Frozen`, the stack status, the deployed code version and key parameters); with no job
ID (another machine, or a CLI that died before writing its file), `status` returns the lock holder's
ID, kind, machine, person and bound values;
DescribeSecret on the seed secret (`VersionIdsToStages`, read uncached: whether this job's version,
whose ID is its `ClientRequestToken`, exists, and whether `AWSCURRENT` is on it) and on each runtime
secret name (`DeletedDate`: a name that has one is force-deleted again, F11); this machine's files;
and, for a rollback, its local database (read through the local service, started in the hold if it
is down, as `--abort` does) and `ecf status`'s Slack connection. A run adopts a lock holder found
through `status` only if it is of the run's own kind and its bound values (F1, which `status` returns
with the holder's person) equal this run's: an upgrade, the CLI's own version equals the bound target;
an import, the manifest's SHA-256 and `replace`; a restore, the `RestoreDateTime` (a relative `--at`
is resolved first, and a mismatch prints the bound point to type); a rollback, this machine bound; a
rotation, never on another machine: only the machine holding the key text (the one that started the
rotation) runs it, since its deploy needs the new public keys the key text gives and the seed secret
holds only their fingerprints (OD-733; a lost machine mid-rotation is F12's stated limit, and finishing
a job from another computer is an M2 task, OD-732; freeze_model9: each rotation's seed, deploy and file
steps run only on its creator's machine, `kh`, and no other machine adopts it; `adoptseed`, adoption
once the seed version exists, is safe and lowers the stated limit, but is not adopted, by operator
choice, OD-745). Any other is reported (kind, person, machine, bound values) with the command that started
it; a run that adopts prints "continuing <person>'s <kind> job". It takes the one next action from this table (a pure function,
`next_action(Obs)`, shared with the model). On every run, a pending entry in this machine's
`aws-keys.json` whose fingerprints equal `AWSCURRENT`'s is moved to current first (the file follows
`AWSCURRENT`, never the reverse; OD-691). A machine whose file's current entry differs from
`AWSCURRENT`'s and holds no matching pending entry (another machine ran the rotation, or it has no file
yet) is told the last done rotation (person, machine, date, the new OD-341 and relink fingerprints,
from `status`); `doctor` and `export --pull` refuse until the operator types the new OD-341
fingerprint, which must equal `AWSCURRENT`'s, and only then is the file moved (OD-745; freeze_model9
`keys_confirm`, fault `noconfirm`: the other machine's file never follows).

| Record | Stack | Next action |
|---|---|---|
| any | `UPDATE_ROLLBACK_FAILED` (or a partial `UPDATE_FAILED`) | `ecf aws continue-rollback`, read again; after a second `UPDATE_ROLLBACK_FAILED`, stop and point to the runbook (`ResourcesToSkip`, an administrator) |
| any | update in progress | read again later |
| expired before `freezing` | live | remove `aws-freeze.json`; "run the command again" |
| `authorised`, `preparing` | live | `prepare` (repeated until `ready`) |
| `ready` | live, no owner | write `aws-freeze.json` (job ID, kind), then `begin` |
| `ready` | held by another job | wait |
| `freezing`, `running` | LIVE | freeze update (a) |
| `freezing`, `running` | QUIET, this job | freeze update (b), only 45 s after freeze (a)'s `UPDATE_COMPLETE` (OD-742) |
| `freezing` | FROZEN, this job | `first` (refused until the wait is over, F5) |
| `running` | FROZEN, this job | the kind's next step (F11), from the record's progress, the deployed code version and key parameters, and `AWSCURRENT`'s fingerprints; then `finish` (rollback: on the bound machine only, pull the bundle, verify it against the recorded SHA-256 and keep it, or `reexport` if its object has expired, then `commit`; OD-741) |
| `committing` | FROZEN, this job | bound machine only, by the job's `rollback_restore` row: `aborted`: uninstall the unit, then `abort_local`; none: ask the service to restore the kept bundle (a fresh `restore` step-up: v1 consumes each request's nonce) and wait; it can't know whether an earlier request is still with the service, and the commit rule makes asking again safe (F11); `committed`: ask on the key route (job ID and typed key, no bundle: the service reads the row before opening anything, checks the key's fingerprint against the row's and runs the tail with it before answering, or names the sub-step that fails; F11, OD-741) and wait; `complete`: `finish` |
| `done`, rollback | FROZEN | bound machine: F11's closing steps; any other machine: the secrets' forced delete (deployer credentials) if not yet done, then remove its own file |
| `done`, `aborted` | FROZEN, this job | release update (a), when the deployed keys equal `AWSCURRENT`'s |
| `done`, `aborted` | QUIET, this job | release update (b) |
| `done`, `aborted` | LIVE or another job's | remove `aws-freeze.json`; done |

**F1 Job record and lock.** `JOB#<id>` in the `ecf-api` table: kind, bound values, the step-up's
`IDENT#` key (upgrade: the JWT's identity), `people.id`, the creating machine's hardware UUID (as the
HMAC(`install.id`, UUID) value the OS issuer uses, read on every call, never stored in the data dir), state, `expires_at`, progress. States: `authorised` →
`preparing` (restore and import) → `ready` → `freezing` → `running` → `committing` (rollback) → `done`,
or `aborted`. `ecf-api` creates the record and `JOBLOCK` in one transaction, for an admin only, with
`expires_at` = creation + 2 hours, and writes nothing after (a code rule: IAM can't make a put
create-only); a create refused because the lock is held returns the holder's ID, kind, machine, person and bound values.
Taking over the lock is one transaction with a `ConditionCheck` on the old record: terminal, or in
`authorised`, `preparing` or `ready` with `expires_at` < now (10 minutes of slack added). Every ecf-jobs
gate is likewise a condition in the same transaction as its write (`JOBLOCK` = this job, the record's
state, `expires_at` > now where it applies), so DynamoDB serialises a gate and a takeover on the same
items. Each `prepare` extends `expires_at` to now + 2 hours (conditioned on not expired, capped at
creation + 24 hours), so a slow PITR restore can finish; `begin` removes it, so a started job never
expires. Only TTL on terminal records. From then on **only ecf-jobs writes** the
record and the lock (every role but `ecf-api`, which only creates and takes over, and ecf-jobs is
denied writes to `JOB#` and `JOBLOCK`, each its own partition key; a static test). A terminal write
(the record and, where it names this job, the `JOBLOCK` delete) is one transaction. Bound values,
shown in the step-up prompt: restore, `install.id` and the `RestoreDateTime`; import, `install.id`,
`manifest.json`'s SHA-256 and `replace`; `migrate_rollback`, `install.id` and the generation; rotation
(v1's `export_key` step-up, OD-660, bound to the new OD-341 fingerprint as v1; the relink fingerprint is
checked by `rotation_check`), the new fingerprints; upgrade (admin role, no step-up), the target version,
which `upgrade_last` checks against its own.

**F2 Preparing (live stack).** `prepare` (gate: not expired, `JOBLOCK` = this job, no owner in
`Frozen`): restore starts, or polls, PITR restores of the four tables at the bound point to
`ecf-<install>-scratch-<job id>-<table>` and writes `ready` when all are ACTIVE, after polling
`DescribeContinuousBackups` until `LatestRestorableDateTime` ≥ the point (about 5 minutes behind now;
`RestoreTableToPointInTime` refuses a later point, API reference, fetched 2026-11-05); scratch tables
are restored without secondary indexes except the grants table's `stable_id` index, which the merge's
attempt lookup needs (index reads are eventually consistent; the merge reads a restored table nothing
writes). No point is refused on retention grounds (OD-670, OD-698); import checks that the upload exists and its `manifest.json`
hash equals the record's; other kinds go straight to `ready`. The laptop decrypts and verifies an import
bundle under the typed key (old-key bundles against the rotation's hash list, OD-660), re-encrypts it
to the job's one-time recipient and uploads it by a presigned PUT signing `x-amz-checksum-sha256` and
`If-None-Match: *` (create-once; both unverified, Step 8).

**F3 Begin.** `begin` (gate: `ready`, not expired, `JOBLOCK` = this job, `Frozen` empty) moves the record
to `freezing`, removes `expires_at` and queues the freeze notice (Slack, and email through
`alert_outbox`): kind, expected end (the sum of Step 8's measured phase times, the Scan and merge
phases scaled by the install's table size), "Slack clicks fail and
mail checks and fraud flagging stop until it ends; a Cancel or Reject answered 'cancelled' or
'rejected' stands; the 'hasn't checked in' message may post meanwhile, and is true". The laptop waits up
to 60 s for the Slack notice and goes on either way (the notice is best effort; the dead-man message is
the backstop, F15).

**F4 Stack configurations and updates.** Three configurations: LIVE; QUIET (every schedule and event
source mapping off, functions on: Cancel works, since `ecf-slack-in` writes `STOP#` synchronously, and
nothing counts delays, F8); FROZEN (reserved concurrency 0 on every function except `ecf-jobs` (1), the
Cognito trigger (1; no data access) and the auto-delete helper (deploy-time only); provisioned
concurrency removed). `Frozen` = `<job id>/<config>` (empty: LIVE), carried in every function's
environment; a function other than the exempt three exits at once when it says FROZEN (never in QUIET),
except `ecf-slack-in`'s Cancel and Reject path, which doesn't read `Frozen` (reserved concurrency 0 is
what stops it in FROZEN), so an environment served stale after release (a) can't make Cancel exit
(OD-723; delay_model6, fault `slackfrozen`).
Under `<job>/QUIET`, the chat seam (`respond()`, so M11's function gets it too) answers every click
other than Cancel and Reject "ecf is under maintenance; not done, click again after it ends" and
queues nothing; under FROZEN `ecf-slack-in` can't run, and Slack shows its own error for every click,
Cancel and Reject included. Freeze is LIVE → QUIET
(update (a)) → FROZEN (update (b)); release is the exact reverse, FROZEN → QUIET (release (a): functions
back) → LIVE (release (b): mappings and schedules together; both `DependsOn` the functions so they
update last). Every update by ecf is a change set (named deterministically per job and step; an existing one of
that name is deleted first, after waiting for it to leave `CREATE_IN_PROGRESS`, since
`DeleteChangeSet` refuses one still being created; then executed and described by the ARN that
`CreateChangeSet` returned, so two machines re-running one step never execute each other's) executed
with `DisableRollback=false` (explicit: the API reference lists `True` as the default, fetched
2026-11-05); the deployer policy has no
`cloudformation:UpdateStack` or `CancelUpdateStack`, so a direct update needs an administrator. Executing one
deletes the stack's other change sets (ExecuteChangeSet, docs.aws.amazon.com, fetched 2026-11-05), so a
decision made before someone else's update can't apply: after create, the laptop reads the stack and
`status` again and executes only if freeze (a) finds LIVE, no owner and the record not terminal, and
every other step finds `Frozen`'s owner = this job and, except a release, the record not terminal; the
deployed fingerprints, and the change set's own key and seed-version parameters as `DescribeChangeSet`
returns them, are checked against `AWSCURRENT`'s (I8; for the rotation deploy, the change set's
against the pending version's; `AWSCURRENT` moves only after `rotation_check`), and `DescribeChangeSet`
must list exactly the step's changes (the code deploy, the one new-template change set, is checked against the
target version's synth snapshot). The laptop re-reads only after its own change set reaches `CREATE_COMPLETE`, so whether a set still
being created survives another's execution doesn't matter; Step 8 makes the deletion rule a pass
criterion. `UPDATE_COMPLETE` and `UPDATE_ROLLBACK_COMPLETE` are both stable statuses; `Frozen` is read
from the deployed parameters. One exemption
list, each entry with its reason, is used by F4 and by the synth test, which fails on any function,
mapping or schedule (CDK-generated singletons included) not covered. Every deploy path keeps `FreezeSeq`,
every key parameter and the seed-version parameter with `UsePreviousValue` and uses change sets (CI
tests). A job's steps pass `Frozen` explicitly. Every other command that creates a change set on the
app stack (the list is taken from the CLI's code by a CI test) keeps `Frozen` with `UsePreviousValue`
and, once its change set reaches `CREATE_COMPLETE` (as a job's steps wait for), executes it only if
DescribeStacks shows `Frozen` empty and a stable status and `DescribeChangeSet` shows the set's own
`Frozen`, `FreezeSeq` and key parameters equal to the deployed ones (defence in depth: a set created
while LIVE always carries `Frozen` empty, and a freeze executed meanwhile deletes it, ExecuteChangeSet
docs; the check matters only for a set created in QUIET that survives a release while still being
created, which Step 8's deletion pass rules out; round-19 measurement: creation-time resolution in the
model changes nothing); otherwise it deletes the change set and names
the job (OD-699; freeze_model9 `+foreign`, faults `foreignnorefuse`, the refusal removed (it restates
the no-update-during-a-job check), and `foreignfrozen`, which also passes `Frozen` empty: a deploy from a second machine, or after a
rollback, would otherwise bring the stack back mid-job; with the refusal kept, keeping `Frozen` is
defence in depth). A hand-made `UpdateStack` skips every rule here (runbook).

**F5 Confirm and wait.** After (b) the laptop reads the stack's resources, `GetFunctionConcurrency`,
provisioned concurrency and the environment for each function, each mapping's `State` (polling a
bounded time while `Disabling`) and each schedule's state; a mismatch stops the run, and the next run
reads again. ecf-jobs stamps `frozen_seen_at` once per job, at its first call whose own DescribeStacks
shows a stable status with `Frozen` = own/FROZEN (any kind but `status` stamps); `first` and every work
step refuse until 960 s after the stamp (the longest function timeout, `ecf-jobs`' 900 s, plus 60 s).
The later FROZEN → FROZEN deploys (code, rotation) keep every function throttled, so they don't
re-stamp. `first` records `quiet_at` = stamp + 960 s, the upgrade's PITR restore point. Async events queued before the freeze (event age, each function's
`EventInvokeConfig` `MaximumEventAgeInSeconds`: broker 300 s, tick 60 s; the default is 6 hours, so the
stack sets both) have expired by then. A broker running at (b) may finish inside the wait; its attempt records
say so and OD-670 uses them.

**F6 Chunks.** The laptop invokes `ecf-jobs` synchronously (read timeout 960 s, TCP keepalive on,
retries off); a throttle or a busy job lease means wait 30 s and call `status`. Kinds: `prepare`,
`begin`, `first`, `rollback_export`, `commit`, `restore_merge`, `import_merge`, `upgrade_last`,
`rotation_list`, `rotation_check`, `finish`, `abort`, `abort_local` and `status` (any state, read-only;
with no job ID it returns the lock holder). `abort`'s gate: the record not terminal, not `committing`,
not a rotation from `rotation_list` on, and `Frozen` empty or owned by this job.
They run only on a direct invoke naming the job ID, never from `jobs.fifo` whatever the event's shape (a
test sends a forged SQS-shaped event). `first`, the work steps, `commit` and `finish` also need
`JOBLOCK` = this job and ecf-jobs' DescribeStacks showing a stable status (F4) with `Frozen` = own/FROZEN.
ecf-jobs' role, and the runtime boundary, allow
`cloudformation:DescribeStacks` on its own stack. ecf-jobs runs at reserved concurrency 1 while frozen, so `abort` can't land mid-chunk; each chunk takes
the job lease (fenced), stops at 300 s and is idempotent by its progress item; long reads page by a
cursor in the progress item. ecf-jobs batch-fails every `jobs.fifo` message while `Frozen` has an owner
(those batches wait out the visibility timeout: housekeeping and eval jobs can run up to about 90
minutes late after a freeze; Step 8 measures it).

**F7 First chunk** (repeated by F0 while the record is `freezing`). Re-reads the record's person and
the bound `IDENT#` (active, admin, that identity confirmed); if that fails the record goes `aborted` (a terminal write as F1) and F0 releases; cancels a scheduled export in flight (its progress item `cancelled`,
`MAINT` deleted, its partial object deleted, its scratch tables marked for housekeeping to delete once
ACTIVE after the release; a continuation that finds its job cancelled exits); for the kinds that read
items (rollback, restore, import), applies every pending stop found by a consistent Scan of the main
table, paged by cursor across chunks (upgrade and rotation leave pending stops to the tick after the
release); only then moves the record to `running`. A stop's audit row keeps its own person; every other row the job
writes carries `requested_by` = the job's person (SPEC §6, added at Step 13). Later chunks run under the deployer
credentials without re-reading the person (stated limit, OD-668: role changes wait for the release).

**F8 Delays (I1).** Release (b), the change set every exit ends with, sets the `FreezeSeq`
parameter to the releasing job's ID in every function's environment (IDs never repeat; every other
change set keeps it with `UsePreviousValue`, a CI test as for `Frozen`; the schedules update after the
functions, F4). Whoever
creates a `delayed` item stamps its `seq` and `counted_at` from its own environment, with `remaining`.
Each tick reads `Frozen` and `FreezeSeq` from DescribeStacks once per run (a client with 2 attempts
and a 2 s read timeout), never from its own environment (OD-729): a tick whose read fails or is
throttled, or that finds `Frozen` non-empty, counts nothing and moves nothing to `executing` (OD-693,
OD-736; a restored stack after a failed release (b) still says `<job>/QUIET`). The stack status is
not consulted: draft 20's stable-status rule is removed, since no fault failed without it (round 20)
and it stopped every countdown during any update. The read gates only the countdown and `delayed →
executing`; the tick's other duties (due checks, stops, expiry, reminders, cards, re-queues) gate on
their own checks as before (`Frozen` in the environment, the holds), so a CloudFormation throttle or a
non-freeze update stops no mail check. So a Lambda environment served stale or torn after an update
can't reach the countdown (delay_model9: `failopen`, a failed read using the tick's last good read,
breaks I1; `envtick`, the tick reading its environment under an adversary that may serve the oldest
configuration of a 60 s stale window, broke it in draft 21 and is clean under OD-742's wait, so the
stack read is defence in depth there: it stays, since Lambda documents no bound on that window and
Step 8 only measures it). The laptop starts freeze (b) only 45 s after freeze
(a)'s `UPDATE_COMPLETE` (OD-742), so a read lagging up to 30 s, counted from either the update's start
or its completion, sees `Frozen` non-empty before Cancel can stop: draft 21 started (b) at once, and
with the lag counted from completion a whole freeze-to-release cycle fit inside 30 s (delay_model9
generates the lag from both ends and the shortest cycles on a 1 s grid; fault `nowait`, draft 21's
order, breaks I1; with the wait the shortest cycle is 65 s, so a 60 s lag is still clean and `dslate`,
a 90 s lag, breaks it). Otherwise each
tick uses its scheduled time (carried in the Scheduler input) as `now`: an item whose `seq` differs
from the stack's counts 0, takes the new `seq` and re-announces its remainder on the card; otherwise it
counts max(0, min(now − `counted_at`, 2 tick periods)) (2 periods covers an item a time-boxed sweep
skipped once). `counted_at` becomes max(`counted_at`, `now`) when counting, and max(`counted_at`, the
tick's invocation time) on a `seq` change: the scheduled time is used only for crediting, since a tick
scheduled before a freeze and delivered after it would otherwise date the new `seq` before the freeze
(OD-699; delay_model9, fault `schedseq`). The write is a SET of
`remaining`, `seq` and `counted_at` conditioned on `counted_at` equal to the value read; on a failed
condition the tick reads again and recomputes (ticks can overlap: event age 60 s plus a 50 s sweep).
It moves `delayed → executing` (with the `STOP#` ConditionCheck) only when `remaining` ≤ 0. So no time
across any freeze, failed or aborted, is counted (delay_model9's `failrelb` reads the stack through a failing release (b) and its re-run, with
DescribeStacks showing either the new or the old parameters mid-update, both generated), whatever order CloudFormation updates environments,
mappings and schedules in, however stale an environment is, and even if a disabled schedule fires
(delay_model9: `badorder` and `nowsched` break nothing; the `DependsOn` order and the scheduled time
are defence in depth). The item's creator still stamps `seq` from its own environment; a stale one
only makes the item reset at its next tick (the safe direction; delay_model9 serves the creator the
oldest configuration of the window, and Cancel's path reads no environment). Step 8 records those
behaviours and Lambda's stale window; none gates M1. What I1 does rest on, and Step 8 makes a pass
criterion: a DescribeStacks read made 30 s after a parameter update starts, and one made 30 s after
it completes, show the new `Frozen` and `FreezeSeq` together, and reserved concurrency 0 stops every
function but the exempt three (the Cancel path's only barrier in FROZEN; an administrator's direct
concurrency change mid-merge is a stated limit, as a hand-made `UpdateStack` is). A tick handles as many items
as its 50 s sweep allows (Step 8 records items per sweep); a larger backlog only slows countdowns
(the safe direction) and `doctor` reports it.

**F9 Release** (every kind but a done rollback, and `aws unfreeze`): only for a `done` or `aborted`
record, so no chunk can run after it starts; the two updates of F4 in reverse, release (a) only when the
deployed keys equal `AWSCURRENT`'s, release (b) only after the laptop reads, as F5 does, every
function's concurrency back (`ecf-slack-in` at 20, no other 0 reservation); then `aws-freeze.json` is
removed. The end notice was queued by `finish` or `abort` and posts when `slack-out` resumes ("ended at
<time>. A Cancel or Reject stood where you were answered cancelled or rejected, as its card will show;
other clicks from <the time freeze (a) completed> were not done: click again"); upgrade and rotation
apply their pending stops at the first tick after the release, so a card may show "cancelled" a minute
after the notice. Stated limits: a freeze over the
scheduled export's time cancels that day's export (the next runs as scheduled); after release (b)
every address is due at once and queued posts drain at Slack's 1 post a second per channel, so the
first minutes after a release are slower.

**F10 After a freeze (I2, I6).** Queued `slack-out` posts are delivered: by-reference cards render
current state; a by-value post queued before the first chunk and delivered after a restore or import
says "from before the restore (or import) at <time>". Queued clicks go through the normal checks (the
card's `message_ts`, the current grant, the 10-minute rule for risk-raising clicks), so a click on a
replaced card is told "this card is out of date". A queued stop message is answered from its `STOP#`
state through its `response_url` when that is under 25 minutes old; otherwise the card edit is the answer
(stated limit, OD-671). A restore or import merge posts no cards (OD-694). Card reissue is one mechanism (OD-722, OD-728, OD-739;
`card_model4.py`, part A): the merge's item transaction, when it leaves an item `awaiting_approval` with its
old grants deleted, gives the item a new `grant_id` (no `GRANT#` row yet) and sets the sparse attribute
`needs_card`, whose index sorts by merge time (`migrate` does so for every open approval); nothing else
sets it, and a v1 path that writes a grant doesn't touch it (draft 21's REMOVE by a request is removed,
OD-747: no fault failed without it; the `grant_id` condition already refuses a stale mark). Each tick whose stack read
shows `Frozen` empty and the AWS migrating hold lifted (step 9 lifts it before cards are due),
`ecf-worker` Queries the index oldest first, reads at most 20 items carrying a card (v1's per-tick cap,
`approvals.py:866`; old-mail items, which get a grant and no card, `approvals.py:878`, don't count), and
then, one at a time (a tick can die between two), writes for each one transaction conditioned on `needs_card` present, `grant_id` equal to the value
read and status `awaiting_approval` (draft 21's fourth condition, no `GRANT#` row for that grant, is
removed: no fault failed without it at any scope, round 21; OD-747): the `GRANT#` (with
`people.id`, source `restore` or `import`) and its `action.granted` audit row, the item's one `CARD#` row
set to `queued` for that grant with `replaces_ts` = the old card's `message_ts` (if any), and REMOVE
`needs_card`, all in the same transaction (fault `twotx`: the mark removed in a second transaction
strands it on an item that has its card). A marked item that is no longer awaiting has the mark
removed in a write conditioned on the status still not awaiting (fault `nocleanup`). Overlapping
ticks, a tick dying before its first write or between two items' writes, a second restore or import
merge (which runs only after F5's drain, so never beside a tick in flight), v1's expire (which needs
an issued grant), request, approve and `ecf item resolve`, and `migrate abort` while the hold is up
give at most one card per grant, none for a dead grant or a destroyed install, and every awaiting
item its card by the tick alone (card_model4 part A: faults `nohold`, `nostatuscond`, `nocleanup`,
`twotx`; `nograntcond` is masked by F5's drain, since a re-merge racing a tick in flight breaks it,
`REMERGE=any`). An SQS send can't join the transaction (card_model4 part B): `slack-out` takes a message (deduplication ID =
the `CARD#` key and its `card_queued_at`), reads the row, marks it `dropped` (REMOVE `card_queued_at`)
if its item is absent or terminal, else renders the row's grant, posts, and marks it `posted` with
`message_ts` in an UpdateItem conditioned on status `queued` and `grant_id` equal to the grant it
rendered, never on the stamp (REMOVE `card_queued_at`; fault `stampmark`, the mark conditioned on the
stamp, posts twice when a held message and its resend both go out; `nogrant`, the mark on status
alone, records the wrong grant as posted). The row keeps a list of earlier cards to mark "replaced"
(`replaces`, each with its ts and channel): a request that re-queues the row for a newer grant adds its
posted card; when the mark succeeds, slack-out edits every card in the list to "replaced" (fixed text
and v1's short ID, no subject; the old buttons already answer "this card is out of date") and clears
it; when the mark fails because the row moved to a newer grant, the card just posted is edited
"replaced" at once, in its own channel, and the list is kept (OD-747; fault `overwrite`, draft 21's
single `replaces_ts` overwritten on a mismatch, leaves the pre-restore card showing a dead grant). A
"replaced" edit that finds the message gone is dropped, never re-posted. The tick re-sends a row still `queued` 15 minutes after `card_queued_at`
and re-stamps it in the same conditional write, at most 20 a tick, so a row goes out at most once per
15 minutes, and not at all while a Slack-wide hold record or the row's channel hold is live (v1's
`AUTH_HOLD_S`, `slack_out.py:53-57`; fault `holdresend` restates it). Every way a post can die ends the row:
`given_up_refused` for v1's refused keys (refused codes, an outage give-up while other posts got
through, and a second death, `slack_out.py:621-626`; `doctor` lists it, `ecf slack repost` posts it
again); `given_up_outage` for an outage give-up after QUIET and `given_up_other` for v1's unknown-code
give-up, each re-queued once automatically in a write conditioned on `requeued` absent, which it sets
(v1's one automatic re-post after any other give-up, `slack_out.py:22-25, 631-634`; a re-queue for a
newer grant clears `requeued`; fault `requeueall` restates it; `otherterminal`, draft 21's terminal
unknown-code give-up, leaves the approval without a card; OD-747); a dead letter after 5 crashes ends the
row `given_up_other` with `requeued` set (a message redriven after 1,000 receives is a death the
function never sees: the row stays `queued` and the 15-minute resend covers it) (`doctor` lists it and `ecf slack
repost` covers it; fault `noother`: such a row left `queued` is re-sent forever and leads the
oldest-first index). Every end state REMOVEs `card_queued_at`, so its index holds only queued rows (Step 12 counts
it 0 after the drain). v1's crash window between a post and its mark stays (a stated limit, as in v1). ecf-jobs writes no grant
or card (it stays delete-only on grants, OD-658). At 20 cards a minute, 1,000 open approvals take
about 50 minutes after the release (Step 9 records the drain; `doctor` shows the count left); a
waiting item has no grant meanwhile, so `ecf approve` refuses it with "card on its way". So an aborted
merge or a mid-merge `aws unfreeze` leaves no approval without a card for longer than the drain after
the release. An edit that finds no `message_ts` is dropped (the round-9 rule; OD-657's "holds
until the first post lands" is withdrawn). Each step is idempotent, so a kill anywhere re-runs it. Every queue a freeze holds (`slack-in`, `slack-out`, `jobs` and their DLQs) keeps
messages 14 days, the SQS maximum; a freeze longer than that loses them (stated limit).

**F11 Per kind.** Steps while `running`, in order (F0 runs them):

- **Upgrade:** the code deploy (`Frozen` = the job ID, key parameters unchanged and checked); F5's reads
  again; `upgrade_last` on the new code (checks its version against the bound target; only starts the
  release's backfill, which the next tick starts if it wasn't); `finish`. Restore point: PITR at `quiet_at` (F5). An `abort` racing the code deploy's execute leaves the new code deployed; the release
  reports the deployed version (expand and contract keeps it safe; stated). Refused while a migration
  is unfinalized or busy (OD-379). Five stack updates in all, plus one 960 s wait (Step 8 times each).
- **Rotation (OD-660, OD-691, OD-700, OD-745):** its first chunk, after applying pending stops as
  every kind's does, writes a rollback-kind export under the current key (the `rollback_export` code)
  to `exports/`, kept until the job ends: the bundle F12's lost-machine way out restores (a daily
  backup can be a day older: sends since would be offered again and Cancels answered in AWS lost;
  map_model10 `rotation_limit`, faults `nofreezeexport`, `nostopapply`); then `rotation_list`
  takes `export_dir`'s hashes from the laptop, adds the SHA-256 recorded for every `backups/` object when
  it was written, and refuses, before recording any progress, if the list exceeds what fits in the seed
  secret (SecretString holds at most 65,536 bytes, PutSecretValue API reference, fetched 2026-11-05;
  base64 hashes: about 1,300 entries); the machine holding the new key text writes the new
  fingerprints to `aws-keys.json` as a pending entry (the file holds fingerprints, never key text), then writes the new seed version: PutSecretValue with
  `VersionStages` [`AWSPENDING`] (never `AWSCURRENT`) and `ClientRequestToken` = the hex SHA-256 of the
  job ID (64 characters; the API takes 32 to 64), which becomes the version ID, so every machine knows
  it in advance; the SecretString is a canonical serialisation of the expected fingerprints and the
  list, so a payload rebuilt after a crash is byte-equal and a retry adds no version (the same token
  with a different payload is refused, PutSecretValue API reference, fetched 2026-11-06; the code then
  reads that version and compares fingerprints). Every machine decides from DescribeSecret's
  `VersionIdsToStages` by that version ID, never by whether some version carries `AWSPENDING`: the label
  stays where AWS put it after `AWSCURRENT` moves, so a later rotation would find the earlier one's
  (freeze_model9 `+rot2`, faults `labelpresence`, `nocasmove`). Then the rotation deploy (the machine
  holding the key text: its change set carries the new age recipient and the relink and export verify
  keys, derived from the key text, and names this job's version); `rotation_check` (the fingerprints ecf-jobs computes from its environment
  equal that version's, and the new OD-341 fingerprint equals the step-up's bound value); `AWSCURRENT`
  moved to this job's version by `UpdateSecretVersionStage` with `MoveToVersionId` = that version and
  `RemoveFromVersionId` = the `AWSCURRENT` version read with the decision (never from the
  file; the API fails the call if `AWSCURRENT` has moved since, API reference, fetched 2026-11-06, and
  F0 reads again: a compare-and-swap); `finish`, whose gate reads `AWSCURRENT` uncached (never ecf-jobs'
  container cache) and needs its fingerprints to equal its environment's. The key text's machine moves
  its pending entry to current on its next run; every other machine's file moves only when the
  operator confirms the new fingerprint (F0). From `rotation_list` on,
  `abort` is refused and only a forward re-run on the machine holding the key text finishes it (I8); that
  machine lost at any point from `rotation_list` to `finish` strands it, and only F12's way out remains
  (stated limit, OD-733; freeze_model9 `limit`, fault `limitseed`: draft 20's limit, which ended at the
  seed write, leaves real stuck states; finishing a job from another computer is an M2 task, OD-732). An earlier rotation's version loses its labels and Secrets
  Manager may delete it; nothing reads it (the verify keys are stack parameters).
- **Restore:** `restore_merge` (its first write pauses every address and moves stage `live` to
  `assist`, as v1: `restore.py:161`, `downgrade.py:90-91`), then OD-670; `finish` marks the scratch
  tables for housekeeping.
- **Import:** `import_merge` decrypts with the job's one-time key, checks `manifest.json`'s SHA-256
  against the record and each file against the manifest (once, recorded in the progress item), then
  OD-670 (empty target or `--replace`), its first write pausing every address; `finish`. Any terminal
  write of a restore or import (`finish`, `abort`, the identity re-check) marks its
  scratch tables for housekeeping to delete. The AWS
  step-up text says "merge this bundle into this install", not v1's "replacing".
- **Rollback:** `rollback_export` (OD-670; the bundle carries `slack_messages`, `draft_versions`,
  unsent `alert_outbox` rows and the AWS dead-man message's channel, ID and time) and the presigned GET;
  before `commit`, the laptop pulls the bundle, verifies its SHA-256 against the one `rollback_export`
  recorded (read through `status`) and keeps it in `export_dir` until the row is `complete`; if the
  object expired (1 day) before the pull, an ecf-jobs `reexport` chunk writes it again (the stack is
  frozen, so the data is the same) and records the new SHA-256 (OD-741; freeze_model9: the object may
  expire any time after `export2`; fault `pullaftercommit`, draft 21's order, commit then pull, leaves
  the job unable to finish); `commit` (bound machine only) moves the record to `committing`, after which
  `abort` is refused from any machine; the laptop reinstalls the local unit in the `migrating` hold and
  asks the local service to restore the kept bundle (v1: `POST /v1/restore`, with v1's `restore` step-up; same
  `install.id`, generation max + 1; the CLI checks the pulled bundle's SHA-256 against the one
  `rollback_export` recorded, read through `status`, and v1's step-up binds that hash,
  `restore.py:133-138`). The job's marker and its progress live in their own table, `rollback_restore` (job
  ID, `aborted` or `committed` or `complete`), which `_apply` neither clears nor copies (v1's `_settings`
  deletes every non-machine-local setting inside the same transaction, `restore.py:272`, so a
  `settings` row would not survive). Inside the restore's own SQLite write transaction (v1's `_apply`,
  `restore.py:232`), after `_settings`, the service writes the row `committed`, with the bundle's
  SHA-256, the job ID taken from the bundle's manifest (never from the CLI), the OD-341 fingerprint
  of the key that opened the bundle, the manifest's `kind`, `created_at` and `seq`, and `_apply`'s counts
  (what the tail's audit row and notice read, `restore.py:170-187`), only if the job has no row; if it has one, the transaction raises, its CLEAR and copy roll back, and the request reads
  the row: `aborted`, it answers "aborted" and runs nothing (OD-721; freeze_model9, fault
  `abortedtail`); `committed` (by another request: v1 has no restore lock, so a request from a CLI that
  died and one from its re-run can both pass the entry check), it runs the tail below with its own typed
  key, but only if that key's OD-341 fingerprint equals the row's (so the Keychain seed comes from the
  key that opened the committed bundle, and the re-ask needs no bundle: it still works once the pulled
  copy and its S3 object have expired; OD-735; freeze_model9 event `bgone`), then answers "already restored"
  (a key whose fingerprint differs is answered "this is not the key that opened the bundle"); `complete`, "already restored" (OD-701; freeze_model9 allows two
  pending requests, fault `entryonly`). The re-ask is its own route (`POST /v1/restore` with `job_id`
  and no `path`): the service reads the `rollback_restore` row first, in its own transaction, before
  opening any bundle, refuses unless it is `committed`, and checks a `restore` step-up bound to (job
  ID, the row's bundle SHA-256), both read from the row; v1's handler as it is opens the bundle first
  (`api.py:1914`) and binds (path, SHA-256) (`restore.py:121-126, 150`), so it could not re-ask once the
  bundle is gone (OD-741; fault `bundleask`: the re-ask through the bundle parse can't finish). The commit transaction also
  clears the hold, inserts `slack_messages` rows under the prefix `moved:<stable_id>` from the bundle's AWS
  card references, takes the bundle's `needs-you` row over the local one and deletes `slack_needs_you_hash` (so the
  next tick's "Needs you" edit lands on the pinned message at once), and enqueues by those keys, each
  with the AWS card's recorded channel as its route and under the address key `moved:<channel>` (its
  own FIFO group, so the edits don't hold up new posts in that channel; OD-747), the by-value "moved
  back" edits for every open item's card and every item whose status changed from the freeze on (a
  terminal item's card already shows its result and its buttons answer "out of date") (fixed text and v1's short ID, by status: a terminal
  item its result, so an applied Cancel shows "cancelled"; `awaiting_approval` "a new card follows in
  <channel>", or "in the scan summary" for an old-mail item, which gets no card (`scan_summary.py:41-46`), `approvals.py:878`; any other
  open status its v1 status line and the command, e.g. `ecf item resolve <short>` for
  `failed_unknown`) and a "moved back to <computer> at <time>" post, which also says that clicks made
  in the minutes before the move were not done; it sets back to pending each bundle escalation in
  state `posted` or `merged` whose `post_key` (the key of the card that carried it, which AWS records
  on the escalation row when it posts or merges it: `item:<sid>`, a burst card `burst:<aid>:<first sid>`,
  a roll-up card with its timestamp, the cap thread or a scan summary, `escalations.py:94-112, 151-165`)
  has no `message_ts` among the bundle's card references, whatever its item's status (v1 marks an
  escalation posted when it enqueues it, `escalations.py:88-92`, so one still queued in
  `slack-out.fifo` would never post, and an escalated item is usually terminal at once,
  `decide.py:152-167`; v1's sweep posts pending rows whatever the status, `escalations.py:55-65`;
  OD-739; card_model4 part C: faults `headkey`, a reset by `thread_key`, the head card's key, which
  misses a follow-up burst, and `openonly`); an escalation from a scan or reprocess run (backfill or
  reprocess facts, `marked.py:24-25`) carries its run's reply key `scan:<run>:r<n>` as its `post_key`,
  and for each such reply with no `message_ts` the commit removes its items from the run document's
  `reported` list, so the local scan tick reports them again in a new reply (v1 re-merges a pending
  marked row into `scan:<run>` with no post, `escalations.py:72-75`, and skips items in `reported`,
  `scan_summary.py:87-93`; OD-747; part C models what v1 then does; fault `scanreset`: the reset alone
  leaves them unposted); it enqueues v1's question card (`answers.py:288-303`) for
  every `needs_clarification` item, whatever its AWS card shows, since a lost question edit leaves a
  `message_ts` behind (fault `qts`), and the item's "moved back" edit then says "a new card follows";
  `needs_human` items get no per-item card (v1's `_to_person`, `answers.py:92-97`, posts nothing
  without a local `item:` row): the pinned "Needs you" message the commit takes over lists them
  (`needs_you.py:45`); and it writes a "renew
  now" mark for the dead-man message (a settings key written after `_settings`; v1's `keep_armed`,
  `deadman.py:94-95`, gains the check, Step 6c). v1 does more after its commit (the Keychain seed,
  `restore.py:168`; the audit row, 170-180, and notice, 181-187; the reissue, 251-254;
  `announce_converted`, 191, a no-op for an AWS bundle); the service runs these as an idempotent tail
  (the reissue as a sweep over "awaiting with no issued grant" first, each item's grant and card one
  transaction conditioned on it still having none; the audit row and notice once, each a conditional
  insert keyed by the job, so two tail runners can't double them; the seed last), each sub-step
  retried on its own, then writes the row `complete`, conditioned on `committed`. The seed needs the
  typed key text, which only a restore request carries: the request that commits runs the tail with
  it, and so does any later request that finds the row `committed`; at start-up the service runs, for
  a `committed` row only, every sub-step but the seed and never writes `complete`, so a crash after the commit only delays the move
  back until F0 asks again (freeze_model9 `tailreq`, fault `startseed`). The tail makes no Slack call:
  the "renew now" mark makes v1's Slack thread renew the dead-man message (`deadman.py:107`, a
  synchronous `chat.scheduleMessage`) once Slack answers, and the row goes `complete` without it
  (OD-721; draft 18's forced renewal in the tail blocked the rollback on a replaced bot token while
  F13 refused `set-tokens`: fault `deadmantail`). A failing sub-step never stops start-up, and the
  restore answer names it (F0 prints it, `doctor` shows it, F12). v1's sender posts all
  of it through the Web API before Socket Mode. The CLI may die at any point; the service decides
  alone. The bundle's dead-man fields go
  into the local `slack_deadman_*` settings, so v1's `keep_armed` renews the AWS message, or posts "back"
  if it fired (`deadman.py:96-103`). Cutover leaves the local Keychain's Slack entries in place until
  `migrate finalize`, so v1's sender runs (`slack_runtime.py:111-131`); its refused-token alert is
  expected until `set-tokens` below. Then `finish` (`done`; the stack stays frozen); the deployer, from
  any machine, deletes the AWS mailbox and bot-token secrets with `ForceDeleteWithoutRecovery` (no
  recovery window, so an admin's `RestoreSecret` can't revive a mailbox password on the frozen stack;
  the deletion finishes in the background, so a later `CreateSecret` of the same name retries with
  back-off, DeleteSecret API reference, fetched 2026-11-06; it deletes by the deterministic names, then
  sweeps `ListSecrets` with `IncludePlannedDeletion`, since the list omits names marked for deletion;
  a name with a `DeletedDate` is force-deleted again; the local Keychain keeps every original until
  `migrate finalize`), and the report counts the messages still queued in `slack-out.fifo` and its
  DLQ ("about N queued Slack messages, plus M dead-lettered"; they are lost, I6) and lists each address
  whose AWS mailbox secret changed after cutover step 5 (`LastChangedDate`: "re-enter this app password
  locally"; the forced delete removed it and the Keychain copy is older); `aws-rolled-back.json` is
  written, then `aws-freeze.json` removed (`doctor` warns; no command refuses); the operator runs v1's
  `ecf slack reauthorize` (`slack_admin.py:341-350`), which switches the app manifest to Socket Mode
  with a pasted configuration token and reposts cards, then v1's `ecf slack set-tokens` with the bot
  token and a new app-level token made by hand (step-up; its `check_tokens` opens a connection, so it
  runs after the switch): `reauthorize` stores no token, and the Keychain still holds the app-level
  token the migration revoked (`slack_runtime.py:111`; OD-701). Both are allowed during the rollback
  once its row is `committed` (F13, OD-721), so a bot token replaced mid-rollback can be fixed. Until
  both have run, local cards' clicks fail visibly (stated limit). If the local restore can't commit, `ecf migrate rollback --abort`
  reads `status` and stops unless the record is `committing` and this is the bound machine; it then
  calls a local service route (starting the service in the hold if it is down) that, in one write transaction, writes
  the `rollback_restore` row `aborted` only if the job has no row. Only if the row is (now) `aborted`
  does the CLI uninstall the unit and invoke `abort_local` (ecf-jobs: `committing`, the bound machine;
  a guard against mistakes, not a boundary; it records the caller's identity as actor and queues a
  Security Notice, as `aws unfreeze`'s abort does); if the restore already committed it stops with "already
  restored; run `ecf migrate rollback` to finish". SQLite's write lock serialises it with the restore;
  there is no lock file.

**F12 Exits (I5).** F0 is the way out of every exit; this table says what is left.

| Exit | State left | Way out |
|---|---|---|
| Record expires before `freezing` | nothing frozen; lock can be taken over 10 min later | run the command again (new step-up); housekeeping deletes its scratch tables and force-deletes its one-time identity (`job/<id>`, OD-723) |
| An update fails and rolls back | the stack as before it | re-run (F0 decides again) |
| An update ends `UPDATE_ROLLBACK_FAILED` | stack needs `ContinueUpdateRollback` | F0 runs `ecf aws continue-rollback`, then reads again; a second failure stops at the runbook |
| Laptop killed, asleep, offline, credentials expired | as left | re-run on that laptop or another (F0; another machine finds the job through `status`'s lock holder), or `aws unfreeze` where allowed; `doctor` and the stuck-freeze alarm (F15) report it |
| The bound machine lost or replaced (rotation from `rotation_list` to `finish`; rollback from `commit` on) | frozen | stated limit (OD-672, OD-733): copy the rollback bundle (a rollback) or the rotation's export with the deployer credentials, restore it on the new Mac under the typed key, then `destroy` (a rotation: its freeze-time export, written by its first chunk after the stops, OD-745; no other machine finishes it, OD-733); deployer S3 read and v1's restore of a rollback-kind bundle unverified (Step 8) |
| A chunk keeps failing | frozen | `aws unfreeze` where allowed (runbook first), else `destroy` |
| `aws unfreeze` | `abort`, then F9 | for the job named in `Frozen`, or, with `Frozen` empty, the lock holder in `freezing`; in every state before `done` except a rotation from `rotation_list` and a rollback in `committing` |
| A restore or import aborted midway | every address paused; items mapped so far are final (each item's merge is one transaction); approvals already merged get their cards from the `needs_card` tick after the release (F10) | run the same command again: the new job skips items already marked `merged_by` this source key |
| The local restore's tail keeps failing (rollback) | committed locally; AWS frozen; the open items' cards wait | fix the cause the restore answer and `doctor` name (a Keychain refusal, a bad item; `set-tokens` and `reauthorize` are allowed once the row is `committed`), then re-run `ecf migrate rollback` |
| `ecf-api` broken (a bad release, its authorizer or the Cognito trigger) | no new job record | stated limit (OD-668): the runbook's deployer path; `aws unfreeze` needs only the deployer credentials |

`ecf aws unfreeze`: deployer credentials, the install name typed; `abort` queues a Security Notice
(email through `alert_outbox`). `ecf destroy` on a frozen stack uses the deployer credentials only, with
no step-up (stated limit, OD-668), and also deletes the scratch tables and the runtime secrets (without
a recovery window). Anyone with `lambda:InvokeFunction` on `ecf-jobs` can run a job's chunks (the job
ID is no secret: `status` returns the lock holder's; stated limit, OD-668; the deployer can change the
stack anyway). An `abort` run by `aws unfreeze` records the deployer's caller ARN as actor and
`requested_by` empty.

**F13 Local refusals.** While `aws-freeze.json` exists, only `doctor`, `destroy`, `aws unfreeze`, `aws
continue-rollback`, `revoke-device`, break-glass (the runbook says `aws unfreeze` if the admin session is
suspect), `cancel` and `reject` (they work in QUIET), during a rollback whose `rollback_restore` row
is `committed` or `complete` `ecf slack reauthorize` and `ecf slack set-tokens` (OD-721; a convenience,
not a liveness rule: since the tail makes no Slack call, nothing before `finish` needs Slack, and
freeze_model9's `f13refuse` and `tailslack` are each clean alone), and the
command that started the job run (re-running it continues, F0); every other `ecf` command refuses with "ecf in AWS is frozen for <kind> (job <id>) since
<time>; re-run `<command>` to finish it, or see `ecf aws unfreeze`". `doctor` flags the file, and the
runbook says lid open and on power (`caffeinate -i -w <pid>` doesn't stop sleep when the lid closes).

**F14 Deployer policy** (listed in full by `ecf iam deployer-policy`, one statement per resource,
snapshot-tested with each action's resource; OD-691) adds to the base deploy actions: `lambda:InvokeFunction` on `ecf-jobs`;
`lambda:GetFunctionConcurrency`, `ListProvisionedConcurrencyConfigs`, `GetFunctionConfiguration`,
`ListEventSourceMappings`; `scheduler:GetSchedule`; `cloudformation:DescribeStacks`,
`DescribeStackResources`, `DescribeStackEvents`, `CreateChangeSet`, `DescribeChangeSet`,
`ExecuteChangeSet`, `DeleteChangeSet`, `ContinueUpdateRollback` on its stacks; on the seed secret's ARN,
`secretsmanager:PutSecretValue`, `UpdateSecretVersionStage` and `DescribeSecret`; on the name prefixes
`ecf/<install>/<install.id>/mailbox/*` and `ecf/<install>/<install.id>/slack/*` (created at runtime by
`set-tokens` and the mailbox routes, so no CloudFormation-owned secret is deleted out of band; the
Slack configuration token a migrate writes is `slack/config`, which the deployer force-deletes right
after cutover step 9's manifest switch), `DescribeSecret` and `DeleteSecret` (with
`ForceDeleteWithoutRecovery`, so a re-migrate with the same `install.id` never meets a name scheduled
for deletion; `CreateSecret` refuses one, API reference, fetched 2026-11-05; re-create retries with
back-off; the statement is an Allow conditioned `Bool` `secretsmanager:ForceDeleteWithoutRecovery` =
"true", so a call without the parameter fails it; the key exists for `DeleteSecret`, Service Reference
JSON for Secrets Manager, fetched 2026-11-06 (rv), and Step 8 tests it); on `*`, `secretsmanager:ListSecrets` and
`dynamodb:ListTables` (list actions take no resource; unverified, Step 8); `sns:Subscribe` (conditioned
`sns:Protocol` = `email` and `sns:Endpoint` = the admin address), `Unsubscribe`, `ConfirmSubscription`,
`ListSubscriptionsByTopic` and `GetSubscriptionAttributes` on the `-direct` topic (F15; the topic is
their only resource, Service Reference JSON for SNS, fetched 2026-11-06 (rv));
`DescribeSecret` and `DeleteSecret` (forced only, as above) on `ecf/<install>/<install.id>/job/*`
(OD-723); `sqs:GetQueueAttributes` on
`slack-out.fifo` and its DLQ; `dynamodb:DeleteTable` on `ecf-<install>-scratch-*`; for `destroy` and
`migrate abort` (OD-737), `cloudformation:DeleteStack` on its stacks, and `s3:GetObject` and
`s3:ListBucket` on the bucket's `logs/` prefix (the abort's audit compare); no S3 delete right: a
standing `DeleteObjectVersion` would let stolen deployer credentials erase `logs/`, the tamper-evident
audit copy, with no management event to alert on, so the stack's inline auto-delete custom resource
empties the bucket inside `DeleteStack`, after the functions that write it are gone (OD-743; Step 0
confirms it removes every object version and delete marker); the deletion-protection, PITR and
concurrency changes go through a change set, under the stack's role, whose cfn-exec policy holds
`dynamodb:UpdateTable`, `dynamodb:UpdateContinuousBackups`, `cognito-idp:UpdateUserPool` and
`lambda:PutFunctionConcurrency` (a Step 8 test runs the abort on a protected stack). It has no `iam:PassRole`:
`CreateChangeSet` without a role ARN uses the stack's role (API reference, fetched 2026-11-05). It has
no `cloudformation:UpdateStack` or `CancelUpdateStack` (F4). It has no DynamoDB data rights (progress through
`status`; Step 12 seeds use a test-only role), though with `InvokeFunction` on `ecf-jobs` and stack
updates it can reach any data in effect (§12.2 says so). ecf-jobs' role and the runtime boundary gain
`cloudformation:DescribeStacks` on its own stack (F5, F6), and `secretsmanager:DeleteSecret` on
`ecf/<install>/<install.id>/job/*` conditioned on `ForceDeleteWithoutRecovery` = true (the one-time
age identity, deleted when its job ends; OD-701). Secret names use the `install.id` stack parameter,
set by the CLI at `init` (`--migrating`: the local one) and kept with `UsePreviousValue`, never the
data's `install.id`, which an empty-target restore can adopt from another install's bundle
(`restore.py:83-84`). Mailbox and Slack secrets have no resource
policy: name-pattern identity grants plus the boundary, with a CI check of which roles can read each.

**F15 Alarms and notices.** Under a non-empty `Frozen` the alarms a freeze trips (throttles, queue
ages) have their actions disabled. The stuck-freeze alarm is the tick heartbeat `TickCounted` (emitted only
by a tick whose DescribeStacks read succeeded and showed `Frozen` empty; a failed or throttled read
emits none) absent for 6 hours, missing data treated as breaching, always enabled whatever `Frozen` says (chat-agnostic, so M11 keeps
it). It publishes to the unencrypted, metadata-only `-direct` topic (its policy admits
`cloudwatch.amazonaws.com` with `aws:SourceArn` = the install's alarms and `aws:SourceAccount`, beside
EventBridge's rules; OD-699) with the admin's email subscribed directly. The CLI owns the subscription
(OD-730): `init` calls `Subscribe` for the admin's address, asks the admin to paste the confirmation
link from the email without opening it, and calls `ConfirmSubscription` with its token and
`AuthenticateOnUnsubscribe` = true, which only a signed call can set (SNS API reference (rv); OD-723),
so a footer link can't silently cut it (Step 8). A mail scanner that opens links on arrival can
confirm it first, unauthenticated (re:Post knowledge-center article on SNS unsubscribes, fetched
2026-11-06 (rv)); `doctor` checks `ConfirmationWasAuthenticated` true and `PendingConfirmation` false,
and `doctor --fix` (refused while `Frozen` is non-empty, so the alarm keeps a recipient through a
freeze) unsubscribes and subscribes again, the admin pasting the new link. A scanner that opens every
link on arrival confirms the new subscription too, so on such a mailbox the subscription stays
unauthenticated (a stated limit, OD-737: a footer link can then cut it, and `doctor`'s existence check
and the unmatched `Unsubscribe` notice are the detection; whether CloudTrail records a link-based
unsubscribe is unverified, Step 8); `doctor --fix` then offers another address, and Step 8
re-subscribes through a scanning mailbox and records the result; the CloudTrail
notices on `Subscribe` and `Unsubscribe` are matched to the recorded `init` or `doctor` run;
the AlarmDescription is fixed text) (the
plan's §10 heartbeat path), never through a function a freeze throttles (`ecf-api`'s
formatter is at 0 when FROZEN); its OK transition says resolved; Step 8 checks delivery with the stack
FROZEN. A healthy freeze longer than 6 hours (a long merge) trips it too; the freeze notice says so
when the expected end is past 6 hours. After a rollback it fires once, 6 hours later, and the runbook says `destroy` (the rollback
report says to expect it). The dead-man
message posts on most workday freezes ("hasn't checked in", with AWS wording); it is not the stuck
alarm. A scheduled dead-man message whose delete fails (Slack error) stays scheduled and may post
later (stated limit, as v1: `deadman.py:131-136` logs and goes on). The laptop records each freeze and release so their CloudFormation and Lambda notices are
matched; an unmatched one is flagged.

**OD-670, which items a rollback, restore or import keeps, and how they map (amended by OD-673, OD-683,
OD-694, OD-698, OD-720).**
Per `stable_id`:

- **Pruned items (OD-698; replaces draft 17's retention bound and 24-hour merge cap):** AWS hides an
  item the moment now ≥ its `expires_at` and TTL may delete it any time after (TTL docs, fetched
  2026-11-05). The only delete of an `ITEM#` is TTL, and only the sweep sets `expires_at`, on a terminal
  item (Expiry, under Data and state). So a source item whose live item is absent (deleted, or hidden)
  while live holds its `item.created` audit row (never pruned, and written in the creating
  transaction, a merge's own creates included) was here, went terminal and was pruned: the merge skips
  it, whatever the retention history, the merge's length or TTL's timing. A source item with no such
  row was never here and is mapped as below. No restore point or merge length is refused on
  retention grounds. The row is found by a Query on the sparse `created_sid` index (every
  `item.created` row carries it, a carried one included, so an item pruned in an earlier leg is still
  known; Expiry). The index is eventually consistent; a row lags only for an item created moments
  before, which can't yet be absent (only the sweep hides an item, at least a day after it went
  terminal, Expiry). After `migrate abort` the AWS install is destroyed
  with its audit rows (OD-726), so a later `migrate run` meets an empty install and the rule finds
  nothing (map_model10 `migrate_rerun`, fault `keepaudit`: draft 19's clear, which kept the audit rows).
  Checked by map_model10 `lifecycle_timelines` (reopened items, retention changed between sweeps, the
  sweep racing a reopen, TTL at any time, the merge at any time after the point; faults
  `noauditcreate`, `sweepuncond`, `sweepnoset`, `noexpcond`; `oldrule`, `skipnoaudit` and `noexists`
  restate the rule).
- **Start:** an item already marked `merged_by` this source key (restore: the `RestoreDateTime`;
  import: `manifest.json`'s SHA-256) is skipped; restoring the same point again after a finished
  restore changes no item, and the report says so. Else the
  live item if it exists and is terminal (`ecf/status.py:36-47`), approved or later (approved, delayed,
  executing, failed, failed_unknown, undoing, undo_failed), or has any attempt record or `sent` row;
  else, with the live item absent, nothing if live holds its `item.created` row (pruned, above); else
  the source item if the bundle has it; else the live item. Rollback starts from the live item. An
  import from another install (the manifest's `install.id` differs from live's) lists and skips every
  source address whose ID live holds with another email, with all of that address's items (v1's
  `stable_id` hashes the address ID, a local-part slug by default, `message.py:217-225`,
  `addresses.py:120-122`, so one message to `info@` at two installs collides, and an item under a
  clashing ID would land in another mailbox), and refuses any source address whose email live holds
  under another ID (two addresses fetching one mailbox; v1's `email` is `UNIQUE`,
  `migrations/0001_initial.sql:7`, and DynamoDB has no equivalent) until the operator renames one
  (OD-720, amended in draft 20 after round 19; a Step 6 test).
- **`map_in_flight(item)`:** a terminal status stays. The current grant's settled `sent` row first
  (v1's settle writes outcomes there, `send.py:171-189`): `sent` → `executed`; `failed` → `failed`
  outside the undo flow (it didn't go), whatever a no-outcome attempt says. Then the current grant's highest-n attempt, by the grant's
  purpose: original `done` → `executed`, `failed` → `failed`, no outcome → `failed_unknown`; undo `done`
  → `undone`, `failed` → `undo_failed`, no outcome → `failed_unknown`. `undoing` with no undo attempt →
  `executed` (the undo grant voided); `undo_failed` with no newer undo attempt stays. A no-outcome attempt
  on an older grant not followed by `done` → `failed_unknown`. Otherwise the kind's v1 mapping, with
  `executing` → `failed_unknown` in every kind: rollback re-offers waiting items as `awaiting_approval`
  (`import_plan.py:28`), restore uses v1's `downgrade` (`items.py:145-154`) except that `approved` and
  `delayed` with no attempt and no `sent` row become `expired`, like awaiting items (consume writes the
  attempt and `executing` in one transaction, so such an item didn't go), import `map_imported`
  (`items.py:129-142`); `ecf migrate run` is an import into an empty install (live empty, the bundle's
  `sent` rows carried; v1 keeps no attempt records), so its open approvals stay open and step 9 reissues their cards.
  Attempt records carry `stable_id`, the grant's purpose and its order, and are read by a query on a
  `stable_id` index (Step 0 sizes it).
- **I3, applied last:** an item with any evidence it went or may have gone (a `done` attempt; a
  no-outcome attempt whose grant's row isn't settled `failed`; any `sent` row `pending`, `unknown` or
  `sent`; a live status `executing`, `failed_unknown`, `undoing` or `undo_failed` without a settled
  `failed` row) never ends re-offerable, and never `failed` unless its current grant settled `failed`:
  it becomes `failed_unknown`, and an existing `pending` `sent` row becomes `unknown` (no row is ever
  invented); this includes a no-outcome attempt on an older grant. Every `undoing` that comes out `failed_unknown` gets the runbook warning that `requeue` re-runs the
  original actions (`approvals.py:664-706`).
- **The merge:** the addresses first, each in its own idempotent write (live-wins when the live row
  exists; an address arrives paused); then one transaction per item, conditioned on the live item as
  read (absent, or its version) and not expired (the item with `merged_by` = this source key, and its
  excerpt and escalation row as records it lists, Expiry, so a skipped item brings none back and a
  skipped cross-install item's records never land on the live item with its `stable_id` (OD-720,
  OD-734), its `GRANT#` deletes, its `sent` row, `needs_card` for an open approval (F10); its `draft_versions` rows follow in their
  own idempotent writes, live-wins, only for an item the merge wrote or kept; past 90 actions, the item's old grant rows are deleted first in their own idempotent writes,
  since a transaction holds at most 100 actions and 4 MB, API reference, fetched 2026-11-05); an
  item the merge creates gets its `item.created` row in the same transaction (the bundle's own row,
  same `uid`, included only when a consistent `GetItem` finds that `uid` absent, since audit rows are
  immutable; else a new row naming the job), and it writes `terminal_at`, never `expires_at` (Expiry;
  for an item from a v1 bundle, which has none, `terminal_at` is its `updated_at`, v1's own prune
  basis, `retention.py:5`);
  an import writes the bundle's other audit rows in a final step after every item, paged by cursor,
  one `PutItem` per row conditioned `attribute_not_exists(pk)` (a refusal means the row is already
  there; no runtime role holds `BatchWriteItem` on the main table, OD-737) (a
  same-install restore skips that step: live already holds every audit row up to the point, since none
  is pruned). An
  import with `--replace` deletes nothing: `replace` only lifts the refusal of a non-empty target, and
  this mapping decides every row (v1's `CLEAR`, `importer.py:65-68`, would delete the `sent` rows the
  stated limit below relies on). The mapping is a fixpoint (checked); skipping marked items is a
  restated rule (`nomark`), so a killed merge, or a new job over the same source, re-runs safely. `sent`, `threads`, `senders`
  and `gate` are the union with the live row winning (so a restore can't undo a sender confirmation or
  `verified_rule1a` set after T: stated limit, and the report shows the sender diff, OD-373).
- **People:** after the items. Restore, and `ecf migrate run` (the same install into an empty target,
  so the operator's person arrives active, as People says): live-wins on existence, admin, roles and
  status (`inactive` stays sticky). Import from another install: people absent from the target are
  inserted inactive; people present are
  live-wins, as in a restore, so the target's active admin is never removed or deactivated. Then the
  admin-approver invariant is re-applied and roles for absent addresses are dropped. No AWS restore or
  import writes `IDENT#`: live identities are the truth (OD-596's drop for another install; for the
  same install live is already a superset, since identities are revoked, never deleted). The
  rollback's local restore is the exception: the local database is older than the bundle, so it applies
  the bundle's identity revocations (revoked is sticky both ways).
- Stated limit: an import from another install that is still running can't see that install's later
  sends (import from a stopped install).

**Not modelled (OD-672, OD-690, OD-724, OD-731, OD-740, OD-748).** Data-plane details (indexes and their
eventual consistency, argued under Pruned items; DynamoDB limits, PITR latency, the first chunk's
Scan, which grows with `AUDIT#`: Step 8 times it per GB; F3's expected end scales it by the install's
table size; the expiry sweep's Scan and its duration, Step 8; `DRAFTV#` expiry by `created`, which
decides no job's finish); the export hold beside a
freeze beyond the exemptions above; Slack rendering, notices, the QUIET click refusal (F4; Step 9
tests it), queue retention, the move back's edits keyed apart from new posts (timing, not safety; Step 12
records the time to the first new post after a move back) and v1's crash window between a post and
its mark (card_model4 checks which grants get a card, the post's mark, "replaced" edits and give-ups,
and the move back's escalation, scan-reply and question cards against what v1 then does); the "renew now" mark (F11; Step 6c and Step 12 test it); F6's
`jobs.fifo` refusal (the `fifo` fault only restates it; Step 6 tests the refusal); whether F7's
identity re-check is needed (the model shows its abort exit is safe and live; Step 6 tests the gate);
the local restore's tail apart from its seed is assumed to succeed in the end (F12's row covers one
that keeps failing), and `socket_mode` stands for `reauthorize` then `set-tokens`; the seed payload's
canonical form (Step 6 and Step 8 test it); a stale cached `AWSCURRENT` (every gate reads it uncached)
and Secrets Manager's eventual consistency (a stale read makes a gate refuse or the compare-and-swap
fail: safe); old and new Lambda environments mixed within one invocation (the tick no longer reads its
environment; the creator is served the oldest configuration of the window, the worst case, and
Cancel's path reads none); the IAM policies themselves (static tests and Step 8; the one IAM rule a
model consequence rests on, no runtime delete or expiry of an `AUDIT#` row, is map_model10's
`auditdel`); the abort's change sets, `DeleteStack` and scratch-table deletes (Step 12 runs them); people,
`IDENT#` and step-ups; bound values other than a rotation's key text (both machines are assumed to
hold the same command; `+strict`, in which neither machine adopts another's running upgrade, restore
or probe, is also clean, so a mismatch leaves `aws unfreeze` as the way out: a limit check, not a rule
check; Step 6 tests the comparison per kind); F2's import upload and PITR
polling; F4's exact-changes check and exemption list; F4's own-parameter check on a deploy outside a
job (defence in depth; creation-time resolution changes nothing in the model, round 19); F5's confirm
reads and F9's concurrency read before release (b) (the model's updates complete exactly);
CloudFormation beyond the change-set rule; more than two jobs or two machines (the second job is a
one-step job or, in the `+rot2` run, a rotation); chunks are atomic (reserved concurrency 1); a killed
merge is checked only in `map_model2.py` (draft-14 rules, `clearfirst`), and v1's restore tail as one
event per request (the seed with it). The I8 change-set check is decisive only for a deploy path that
drops `UsePreviousValue` (`keyreset`); `complete()`'s I8 release check fires under `nocasmove`.
`complete()` clearing `seen` and `waited` on a stack change stands for F5's stamp being the frozen
stack's own update (`draft19-atomicity.md`). The faults caught only by restating a rule are the
**Restatements.** list above, marked so in `run_all9.out` (delay_model9's `foreignfrozen`:
freeze_model9 catches it by its consequence); `limitseed` and `+strict` check limits, not rules;
`filefirst` reverses the file order and also drops the `finish` gate, so its catch
credits the gate; `deadmantail` removes both of OD-721's Slack rules, each of which alone is masked by
the other (`tailslack`, `f13refuse`: defence in depth; each run's stated-limit count must equal the
base's); `adoptseed` is safe and lowers `rotation+nojob2`'s stated-limit count from 27 to 18, but is
not adopted (OD-733, OD-745); the always-on "stop committed after the first chunk" checks are
unreachable under the other gates (kept as defence in depth); the import kind is the same model as
the restore kind, so only `restore` runs.

**Rule → model (OD-680, OD-690, OD-702, OD-724, OD-731, OD-740, OD-748).** Invariants: I1 `delay_model9.py`; I2 the
`viol` set by `apply_J` and `succ`, for both jobs (`stopped`, `stopped2`; `runningfirst`, `i2skip2`); I4, I7 and the three local-restore checks `safety()`; I3
`map_model10.py` and its lifecycle, child-record and migrate re-run checks; I5 `check`'s stuck and can't-finish passes
`limit` and `STATED`; I6 card reissue, the post step and the move back's cards (`card_model4.py`;
Step 12 tests the rest); I8 `precheck_deploy` and `complete()`. F0 rows: `laptop_next`, `unfreezer_next`
(freeze_model9.py); F0's adoption per kind: the `own` and `known` test in `succ`, a rotation only on the
key text's machine, `kh` (`adoptseed` lowers the limit, not adopted; `STRICT` for the other kinds); F0's
reads by version ID: `observe_laptop`'s `pend` (`labelpresence`); F0's file-follows-AWSCURRENT rule:
the `keys_file` and `keys_confirm` actions and `good()`, every live machine's file (`filefirst`,
`noconfirm`); F1 lock, takeover, expiry and gates as conditions:
`apply_J` and job-2 creation in `succ` (faults `stalerevive`, `takeover_any`, `toctou`,
`nolockholder`); F2 `prepare` and `ready`: `apply_J`, `ready_flag`; F3: `apply_J`; F4 configurations,
exact-reverse release, compare-and-swap, owner, I8 and change-set parameter prechecks (made when the
step is decided, as the post-create read is): `TARGET`, `precheck_deploy`, `apply_D`, `invalidate`,
the `_reset` deploys (faults `oldrelease`, `noowner`, `nocas`, `i8list`, `keyreset`); F4's deploys
outside a job: `pf`, `foreign` and the `foreign_deploy` action (`foreignnorefuse`, `foreignfrozen`);
F4's Cancel path not reading Frozen: delay_model9 `cancel_ok` (`slackfrozen`); F4 and F6 stable
statuses, rollback-failed: `apply_J`'s `frozen_own`, `rbfail` (`norbf`); F5 one stamp and the wait: the
`seen`, `waited` and `inflight` fields (`clockwait`, `envearly`, `restamp`); F7's identity re-check:
`revoked`; F7 stops before `running`: `stopped` (`runningfirst`); F8: `run`, `run_failrelb`,
`run_overlap`, `tick_view`, `dsview`, `last_good` and `view`, and the `localhold` scenario (`schedseq`, `nofrozenskip`, `addwrite`, `incr`, `seqa`, `seqb`, `cap`, `foreignfrozen`, `dslate`,
`failopen`, `nowait`, `holdcounts`, `holdskip`, `nolift`; `badorder`, `nowsched` and `envtick` clean); F9: `laptop_next`'s and
`unfreezer_next`'s release branches; F10 card reissue: `card_model4.py` `a_succ` and `a_good`
(`nohold`, `nostatuscond`, `nocleanup`, `twotx`; `nograntcond` masked);
F10's post step: `b_succ` and `b_good` (`stampmark`, `nogrant`, `noother`, `otherterminal`, `overwrite`,
`requeueall`, `holdresend`); F11's move-back cards: `part_c` (`headkey`, `openonly`, `scanreset`, `qts`); F11
rotation: `keys_pend`, `vers`, `plabel`, `apply_D`'s `aws_move` (the CAS on the version read),
`apply_J`'s `finish` gate and `abort` gate, the key text's machine in `laptop_next` (`filefirst`,
`i8list`, `unfreezekeys`, `labelpresence`, `nocasmove`, `limitseed`); F11 rollback commit, bound machine, service-side restore (committed only with no row)
and its tail, the rollback_restore row, marker, --abort, closing steps: `apply_L`, `local`,
`restored`, `tailreq`, `seed`, `bgone`, `bobj`, `pulled`, `reexport`, `slack_ok`, `marker`, `svc_n`,
`secrets`, `rolled`, `socket` (`pullaftercommit`, `bundleask`, `anycommit`, `nolocallock`, `svc`, `entryonly`, `norestoredrow`, `tail`, `abortalways`,
`abortedtail`, `startseed`, `deadmantail`; `tailslack`, `f13refuse` masked); F12 stated limits:
`limit` and `STATED`; OD-670 and Expiry: `merge`, `map_in_flight`, `lifecycle_timelines`, `play`, `migrate_rerun`,
`children_timelines`, `settings_order`, `sweep_batch`, `abort_audit`, `rotation_limit` (`v2`, `oldrule`, `noauditcreate`, `skipnoaudit`,
`nomark`, `nosettled`, `migraterestore`, `restoredown`, `keepaudit`, `rerunnoskip`, `sweepuncond`,
`sweepnoset`, `noexpcond`, `noexists`, `childkeep`, `childuncond`, `nochildver`, `mergenochild`,
`exportnofilter`, `createstrict`, `childstamp_early`, `itemsfirst`, `batchnocond`, `dropsetcheck`,
`retryall`, `abortcopyfirst`, `copydbonly`, `nofreezeexport`, `nostopapply`, `auditdel`; map_model2's `bb4`, `bb6`, `draft14`,
`clearfirst`). Not modelled: F3's notice, F13, F14, F15, the people rules and the
items listed above. A Step 6 test fails if any invariant, F heading or OD-670 bullet appears in
neither this table nor "Not modelled", or if a name here doesn't occur in the models' source as a
function, field, action name, fault name or model file (the same test the table states; Step 6 uses
this wording). **Cross-references (OD-731; `.claude/rules/roadmap.md`).** Each rule here (F0 to F15,
I1 to I8, each OD-670 bullet, Expiry) has one owning paragraph, and other sections cite it by name
rather than restating it. A script, `state-archive/m1/model/check_refs.py` until Step 6 moves it to
`tests/m1/`, must pass before each Phase R round: every name in this table occurs in the models'
source as a whole identifier (from the syntax tree, comments and docstrings excluded); every fault the
text names is in `run_all9.py`'s tables, every fault in those tables is named in the text, and every
MUT name the models test is in those tables; every count the text quotes equals a whole number in
`run_all9.out`; `check_refs.py --selftest` seeds seven breaks (a renamed name, a comment-only name, a
fake fault, a truncated count, a fault dropped from the text or the runner) and each must be found (a
rename to another name the code has can't be caught: the check verifies existence, not intent); no superseded model generation is
named outside "Decisions" and "Plan review findings"; every F-rule, invariant and OD the text cites
exists (an OD in SPEC's table, on this branch or `main`).

### Queues, timers and delays

- **SQS FIFO:** `slack-in.fifo` (group = Slack user ID; Pause and Resume one group per address; stop
  messages `stop:<grant>`; batch 1), `slack-out.fifo` (first posts group = channel ID; edits of a
  posted card `card:<item>`; `views.update` and ephemerals in `view:<Slack user ID>`; the dead-man
  message its own group; OD-651), `jobs.fifo` (group = job kind; content-based deduplication on,
  which Scheduler requires for a FIFO target, and every API-role send sets an explicit deduplication
  ID = job ID + chunk; Scheduler sets `MessageGroupId` per schedule; SSE-SQS). `maxReceiveCount` 1,000 on
  all three queues, their crashes counted in DynamoDB (OD-651; `jobs.fifo` since round 11);
  visibility 6× the consumer's timeout; `MaximumConcurrency` 2 per mapping. `ecf-jobs` deletes its
  message once its progress item and heartbeat are written, so a crash doesn't hold its group for
  90 minutes; the tick is the only retry path, and an out-of-memory or timeout kill alarms. `slack-in.fifo`, `slack-out.fifo`, `jobs.fifo` and their DLQs keep messages 14 days (OD-651, OD-671), SSE on;
  DLQ tools never print bodies.
- **Waits:** every scheduled wait is a `due_at` acted on by the tick, except a `delayed` send's
  countdown (Maintenance F8). A `slack-out.fifo` hold sets the
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
  `MaximumRetryAttempts` 0 with the one shared standard SQS DLQ; a second custom metric, `TickRan` (emitted
  by every tick run, before its stack read), alarms when the tick runs fewer than once in 5 minutes,
  its actions disabled under a non-empty `Frozen` like the other alarms a freeze trips (F15); the
  stuck-freeze alarm is `TickCounted` (F15).

### Slack over a request URL (OD-609)

- **Install** by hand; the signing secret pasted from Basic Information (hidden prompt) at `init`, at
  `migrate run` and by `set-tokens` (step-up, Security Notice); `doctor` checks it. No token rotation.
  `apps.manifest.update` uses a fresh configuration token: on a migrate it is written as a secret in
  the pre-cutover phase (OD-642) and force-deleted by the deployer right after the switch (F14) (so 7 secrets, plus the one-time
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
  counts (`public_cases` and `private_cases`, both above 0, OD-637), read from what `ecf-jobs` stored
  at upload (each source's ID list and the fraud-guard references); a run's counts cover the cases it
  loaded (a `--fraud-only` run counts its own). `ecf.eval.membership` and
  `load` run only in `ecf-jobs` at upload, never per request or tick (a call-site test); a run whose
  upload is no longer current, or whose stored counts are 0 or missing, doesn't count. A stale or missing upload means no synthetic pass, so OD-603 refuses a Bedrock
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
  and draft text; cards go by reference), each kept at most 14 days (OD-651, OD-671);
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
- **Boundary:** one permissions boundary per runtime role, an exact Allow list generated from that
  role's row of the role table (OD-743: a role has one boundary and a managed policy holds at most
  6,144 characters, IAM quotas page, fetched 2026-11-06 (rv); one shared boundary with per-role
  `aws:PrincipalArn` statements came to about 16,000 characters in a round-21 sketch; a CI test
  measures each generated policy against the limit, and cfn-exec's `iam:PermissionsBoundary` condition
  names, per role path, that role's boundary; the self-protection Deny lists every boundary policy; a
  CI test that every role's statements are a subset of its boundary's), with a CI check that every AWS call the code
  makes (collected from the moto tests) is allowed by both the role (per the table above) and the
  boundary, and a `dynamodb:Attributes` bug-guard test for the broker and `ecf-slack-in` (a call naming
  an attribute outside the list is denied) and a CI test that every `UpdateItem` carries
  `attribute_exists` on its key (IAM can't stop an update from creating an item):
  - DynamoDB `GetItem`, `PutItem`, `UpdateItem`, `ConditionCheckItem`, `Query` (tables and
    `index/*`), `BatchGetItem`; `DeleteItem` only in each role's own statement with a
    `ForAllValues:StringLike` `dynamodb:LeadingKeys` allow-list (the role table names each prefix;
    none matches `ITEM#*`, a record an item lists, or `AUDIT#*`) and `Null` false; Step 0's mapping
    has a "deletes" column: every v1 `DELETE FROM` (the delay row on each terminal transition,
    `items.py:115-116`; processing rows, `fetch.py:1003, 1016`; leases, `leases.py:82`;
    `slack_messages` rows, `slack_out.py:484, 542`, `approvals.py:930`, `retention.py:147`) maps to a
    named prefix and role, a REMOVE, or TTL, and the static test compares the role table with it;
    `Scan` and the PITR restore actions for `ecf-jobs` only; `BatchWriteItem` on the main table for no
    runtime role (it takes no condition key that tells a put from a delete: Service Authorization
    Reference, DynamoDB, fetched 2026-11-06 (rv), so it would let ecf-jobs delete `AUDIT#` rows; audit
    rows are written one `PutItem` at a time, conditioned `attribute_not_exists(pk)`; map_model10 fault
    `auditdel`: a deleted `item.created` row lets the skip rule bring a pruned item back), and on
    scratch tables only, scoped by name (Data migrations). No PartiQL, `BulkPutItems`, `StartBulkOperation` or `ImportTable` in any
    role or the boundary (`StartBulkPutItems` takes no condition key, so `LeadingKeys` can't scope it:
    AWS Service Reference JSON for DynamoDB, fetched 2026-11-06 (rv)). The static ITEM# test (OD-730) is
    driven by the Service Reference's write actions on the main table, not a hand list, and matches
    patterns, not strings: it maps each boto call to its `AuthorizedActions` (TransactWriteItems to
    ConditionCheckItem, DeleteItem, PutItem and UpdateItem; the PartiQL calls to `PartiQL*`) and fails
    closed on any `IsWrite` action allowed to a runtime role that isn't in its hand classification,
    and if any allowed delete pattern in any role could match `ITEM#x`, a listed record or `AUDIT#x`
    (Step 8 sends a `BatchWriteItem` from each runtime role, which must be denied); a `PutItem`
    overwrite or an `UpdateItem` REMOVE is a content change no delete pattern catches: those are
    code-enforced (the contract tests above), not IAM-enforced; so is `expires_at` set on an `ITEM#` or
    a record it lists, which TTL then deletes (TTL.html, fetched 2026-11-06 (rv)): the static test
    counts an Allow that can name `expires_at` on a prefix as a delete of that prefix and allows it only
    to `ecf-jobs` (the sweep) and the broker (whose create condition names it), and the contract test
    checks that no write but the sweep sets it; for `AUDIT#` rows IAM enforces it too (the Denies
    below; OD-743);
  - S3 `GetObject`, `PutObject`, `ListBucket` (prefix-conditioned); `DeleteObject` on `exports/` and
    `backups/` for `ecf-jobs` only (partial objects, the prune); the helper's version actions;
  - SQS `SendMessage`, `ReceiveMessage`, `DeleteMessage`, `ChangeMessageVisibility`,
    `GetQueueAttributes`;
  - Secrets Manager `GetSecretValue`, `PutSecretValue`, `CreateSecret`, `TagResource` on exact
    prefixes, and `DescribeSecret` and `DeleteSecret` on `job/*` for `ecf-jobs` only (F14); SSM `GetParameter`, `PutParameter` on `/ecf/<install>/runtime/*` only (deploy-set
    parameters, such as the export verify key, are under another path);
  - KMS `GenerateDataKey*`, `Decrypt` on `aws/sns`; SNS `Publish`; CloudWatch Logs writes;
  - CloudFormation `DescribeStacks` on its own stack (`stack/ecf-<install>-app/*`; the call always
    names the stack, a test), for `ecf-jobs` (F6) and `ecf-worker` (the tick, F8);
  - Lambda `InvokeFunction` on the broker (for the API roles); Cognito `AdminUserGlobalSignOut`; Bedrock
    `GetInferenceProfile`, `GetFoundationModel` and the retention read (for the model-watch job);
  - explicit Deny on `dynamodb:UpdateItem` with `ForAnyValue:StringLike` `dynamodb:LeadingKeys`
    `["AUDIT#*"]` (no runtime path updates an audit row; Step 0's mapping confirms it), and on
    `dynamodb:PutItem` with that condition and `ForAnyValue:StringEquals` `dynamodb:Attributes`
    `["expires_at"]` (audit puts name only `pk` in their condition; an overwrite stays code-enforced:
    IAM has no condition key for a ConditionExpression; OD-743; map_model10 fault `auditdel`: a lost
    `item.created` row lets the skip rule bring a pruned item back); `dynamodb:UpdateTimeToLive`;
  - explicit Deny on control-plane actions (Lambda code, configuration, permissions, mappings,
    function URLs, concurrency; `sqs:SetQueueAttributes`; secret resource policies;
    `dynamodb:UpdateContinuousBackups`, `DeleteTable` except scratch; `logs:DeleteLogGroup`;
    `cognito-idp:UpdateUserPool*`; `bedrock:PutAccountDataRetention`; `scheduler:*Schedule`;
    `iam:PassRole`). This boundary applies to runtime roles; Scheduler roles have their own (above);
    neither applies to cfn-exec. Both boundaries ship only in the admin-deployed base stack.
- **cfn-exec:** under `/ecf-deploy/<install>/`; `iam:PassRole` only to `lambda.amazonaws.com` for
  `role/ecf/<install>/*` and to `scheduler.amazonaws.com` for `role/ecf-sched/<install>/*`; trust with `aws:SourceAccount` and `aws:SourceArn`
  `ArnLike stack/ecf-<install>-app/*` (confirmed in Step 8); denied user, key, login-profile and policy
  creation, function URLs with auth `NONE`, and `lambda:AddPermission` unless `lambda:Principal` is
  `apigateway.amazonaws.com`. Restored from the plan's §10 (dropped by draft 4 without a note, round 4
  S4): cfn-exec may create or update a role under `/ecf/<install>/` only with
  `iam:PermissionsBoundary` equal to the install's boundary (and under `/ecf-sched/<install>/` only with
  the Scheduler boundary), on `CreateRole`, `PutRolePermissionsBoundary`, `AttachRolePolicy`,
  `DetachRolePolicy`, `PutRolePolicy` and `DeleteRolePolicy`, and may never call `iam:DeleteRolePermissionsBoundary` (removing a boundary); PassRole
  to `scheduler.amazonaws.com` only for the Scheduler path. The plan's self-protection Deny is
  restored: `iam:CreatePolicyVersion`, `DeletePolicy`, `DeletePolicyVersion` and
  `SetDefaultPolicyVersion` on both boundary policies, for cfn-exec, the deployer policy and every
  runtime role. CI fails on any Lambda permission without `SourceArn`, any resource policy with
  `Principal "*"` and no condition, and any runtime role without its boundary. Grants-table writes
  are Allow-style (Who may call, "What IAM enforces").
- **Write-once keys:** the export seed and age recipient are written by the laptop at `init` and
  migrate with the deployer credentials it already holds: the seed as a secret, whose resource policy
  denies the Secrets list's actions (above) to every runtime role; the age recipient
  (public) and the relink and export verify keys as CloudFormation parameters passed to the functions'
  environment,
  kept by `UsePreviousValue` on upgrade; `doctor` and `export --pull` re-check the OD-341 fingerprint
  (unchanged: the age recipient and the Ed25519 key, `backup_key.py:12-14`) and a separate relink
  fingerprint every time. **Rotation in AWS mode (OD-660):** in the CLI only; v1's `export_key` step-up;
  a freeze job whose order is Maintenance, F11 (the current text): the hash list and the new seed version
  (with the expected fingerprints) written inside the freeze; one deploy replaces the recipient and the
  relink verify key, makes the new export verify key current and moves the old ones to
  `ExportKeysPrevious` (kept only to verify bundles on the hash list; copies kept elsewhere and not on
  it can't be imported, a stated limit); `rotation_check`; the `AWSCURRENT` move; `aws-keys.json`
  follows it; the release. The job lock keeps any other job out while a rotation runs.
  Refused until `migrate finalize`. OD-616's "through `ecf-api` into Secrets Manager" is amended by
  these laptop writes.
- **CI:** GitHub OIDC trust on `aud`, `sub = repo:<owner>/<repo>:ref:refs/heads/main` and the
  validator workflow's `job_workflow_ref`; `cfn-policy-validator` on pushes to `main`; static policy
  tests on pull requests; dedicated empty account.
- **Detective controls:** EventBridge rules on CloudTrail management events, through an input
  transformer (metadata only), to the unencrypted `-direct` topic (CloudWatch and EventBridge can't
  publish to an `aws/sns`-encrypted topic; its policy admits `events.amazonaws.com` with the rules'
  `SourceArn`, and `cloudwatch.amazonaws.com` with the install's alarms' `SourceArn` and
  `aws:SourceAccount`, F15; a static test checks both statements). Events: CloudFormation `UpdateStack`, `ExecuteChangeSet`, `CreateChangeSet` and
  `DeleteStack` on the install's stacks (a stack update can swap the verify keys or the recipient);
  Lambda code, configuration, permissions, function URLs, event source mappings
  (create, update, delete) and concurrency (put, delete); IAM changes under `/ecf/`, `CreateUser`,
  `CreateAccessKey`, `UpdateAssumeRolePolicy`; Secrets Manager under `ecf/<install>/` including
  resource policies; `cognito-idp:Admin*`; DynamoDB `UpdateContinuousBackups`, `DeleteTable`,
  `UpdateTable`, `UpdateTimeToLive` (pointing TTL at an attribute every item carries would delete them;
  OD-743); CloudTrail `StopLogging`, `DeleteTrail`, `UpdateTrail`; S3 bucket policy, lifecycle,
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

Alarms (35 metrics, 10 free; the 35th is the stuck-freeze alarm, F15, a second alarm on the tick
heartbeat): `ecf-api`, `ecf-worker`, `ecf-broker`, `ecf-jobs`, `ecf-slack-in` errors
(metric math, 2 each: 10) and throttles (5); 3 FIFO DLQs; 2 on-failure queues (`ecf-broker`,
`ecf-worker`; tick failures also reach the Scheduler DLQ); the one shared Scheduler DLQ;
`DestinationDeliveryFailures`, `InvocationsFailedToBeSentToDeadLetterCount` and
`InvocationDroppedCount` (3); `TransactionConflict` and a hold with no live holder for 30 minutes (2);
an age-recipient fingerprint change (1); 8 custom metrics, emitted through Powertools' embedded metric format
(no `PutMetricData` calls) and inside CloudWatch's 10 free custom metrics (aws.amazon.com/cloudwatch/pricing,
fetched 2026-11-05 (rv)): the tick heartbeat, a user without TOTP, Slack signature failures, the
stop-record rate, invalid stop records (OD-649, OD-656), transaction conflicts, a hold with no live
holder, and an age-recipient fingerprint change; plus `jobs.fifo` oldest age and the Cognito trigger
(AWS metrics). Whether metric-math alarms count toward the 10 free alarm metrics is unverified.
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
| CloudWatch alarms, 35 metrics (10 free, $0.10 each); 8 custom metrics in the free tier | ~$2.50 | same |
| CloudTrail (first management copy free) and its S3 | $0.10-0.20 | same |
| DynamoDB on-demand, PITR, under 1 GB | ~$0.10 | $0.20-0.40 |
| SQS: idle long polling by 3 mappings with `MaximumConcurrency` (which disables Lambda's poller reduction), est. 1-2M requests a month, 1M free; FIFO rate not rendered | $0-0.50 (unverified; Step 8 measures) | same |
| Lambda: worker tick 43,200 invocations at 512 MB for about 1 s = 21,600 GB-s; broker ~8,000 a month at 2-3 GB for 10-20 s = 160,000-480,000 GB-s; `ecf-api`, `ecf-slack-in`, the Slack consumers and `ecf-jobs` under 10,000 GB-s (estimate; Step 8 measures) | $0 in the free tier | $0 to about $1.90 over the 400,000 GB-s free tier; $3.20-8.55 if billed (x86 rate; Arm not rendered) |
| Daily scheduled export (OD-650): strongly consistent reads, about $1 per GB of table data a month (1 RRU per 4 KB at $0.125 per million, aws.amazon.com/dynamodb/pricing/on-demand, fetched 2026-11-05 (rv); whether a Scan is charged by summed item size is unverified); the PITR path $0.15 per GB per restore, about $4.50 per GB a month | ~$0.05 | $0.10-0.40 at 0.1-0.4 GB |
| CloudWatch Logs, Scheduler, SNS, SSM, S3 audit writes, API Gateway, EventBridge rules | ~$0.10 | ~$0.15 |
| Cognito Essentials | $0 | $0 |
| **Infrastructure** | **about $5.25-5.85** | **about $5.45-6.55 in the free tier; up to about $8.45 over it; up to about $15.10 if all Lambda is billed** |
| If Step 9 needs it: provisioned concurrency 1 on `ecf-slack-in` at 512 MB | +~$5.40 (no free tier with it) | same |
| **Bedrock, when chosen** | $0 unused | **about $415-825**; a full gate run about $20 |

Step 8 runs the stack idle for 24 h with schedules on and records requests, GB-s and cost. After a
rollback the frozen stack keeps billing until `ecf destroy`: about $3.60 a month by this table (35
alarm metrics $2.50, the export seed and signing secret $0.80, CloudTrail and DynamoDB about $0.25;
estimate, unverified).

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
  (`ecf.eval.clean_and_pushed`: no uncommitted changes, HEAD an ancestor of its upstream under
  `refs/remotes/`; the upload records the private commit, the upstream ref and the result), and `ecf-jobs` recomputes the merged version from the uploaded bytes (every
  file's sha256 checked) and accepts it only when it equals the laptop's merged version; it also
  computes the membership counts with `ecf.eval.membership` (OD-633) and refuses an upload with 0
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
- **Grants after an import or restore (round 5; OD-722):** the merge marks each open approval
  `needs_card`, and `ecf-worker`'s tick issues its grant (with `people.id`, source `import`) and card
  once the stack is unfrozen (Maintenance, F10, the only reissue path); a CLEAR of the grants table
  removes `GRANT#` items only, never attempt or outcome records.
- **The local `migrating` hold** (new, built in Step 6c): a persisted service state that survives a
  restart and is cleared only by `migrate abort`, `migrate finalize` or a restore; it refuses mail
  checks and Slack and allows export, step-up, the Keychain read and the transfer under the CLI's
  attestation (OD-653), and restore (a rollback's restore clears it, F11). Because it refuses
  Cancel, it counts no delay: v1's ticks keep running under the hold, set `_last_tick_mono` and call
  `advance_delays` with 0, and lifting the hold sets `_last_tick_mono` to the lift time
  (`service.py:245`, `approvals.py:566-587`), so a `delayed` send resumed after `migrate abort` still
  gets its whole delay with Cancel working (OD-735, OD-748; delay_model9 scenario `localhold`, a tick
  simulation of v1's countdown: faults `holdcounts`, held ticks counting; `holdskip`, held ticks
  returning before updating `_last_tick_mono`; `nolift`, no reset at the lift, the round-20 rule as
  written, which still counted the time from the last held tick to the lift). Its
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
  6. `migrate run`: `ecf-jobs` decrypts with the one-time identity (deleted when the run ends), checks the bundle
     against the stored verify key and imports it under restore's rules (items: OD-670's import mapping
     into an empty install, so open approvals stay open; OD-694): same `install.id`,
     generation + 1, install name, routes, alerts, identity links, the 7-day restored-keyword guard;
     the AWS `migrating` hold keeps every address from fetching;
  7. `migrate_link` step-up by the pending Cognito identity, bound to the same SHA-256: the bundle's
     person and the Cognito identity become one person (local `people.id` kept, the Cognito identity
     confirmed, the Slack identity kept); `migrate.identities_linked` and a Security Notice;
  8. **only now** the local unit is uninstalled (plist removed, data kept), which also cancels the
     dead-man message (OD-621);
  9. upload and verify the private eval part; switch the Slack manifest; edit to "moved" the old cards
     of items not marked `needs_card`; lift the AWS hold, after which `ecf-worker`'s tick issues grants
     and cards for the items step 6 marked (F10; their old cards get the "replaced" edit instead);
  10. resume each address only after its own mail check passes (OD-366); the first AWS check covers
      mail from the gap.
- **`ecf migrate abort` (OD-726, OD-737, OD-744):** at any point before step 8, destroys the AWS
  install. It takes its own step-up purpose, `migrate_abort`, bound to (`install.id`, the AWS stack
  name), whose prompt reads "destroy the AWS install <stack>; keep this computer's data" (v1's
  `destroy` purpose would show the wrong action and share its bound values with a local destroy,
  `destroy.py:151-157`), records its actor in a local Security Notice, and holds the local service's
  lock for its gate, as step 8's transition does, so the two can't race; once started, every other
  `migrate` subcommand is refused (as v1's `destroy.started`). It waits for any `migrate run` chunk
  still holding the migrate lease (at most 300 s), then, in this order (map_model10 `abort_audit`):
  (0) a change set sets reserved concurrency 0 on every function except `ecf-jobs`, so nothing else
  writes an audit row (fault `abortcopyfirst`: copying first loses the rows written after it);
  (1) copies the AWS `AUDIT#` rows into the local audit by `uid` (idempotent), paged through a direct
  `ecf-jobs` invoke of kind `abort_audit`, which F6's closed list allows only while AWS's `migrating`
  hold is up and which records the caller's ARN (the deployer has no data rights, F14; OD-217);
  (2) downloads `logs/`, the tamper-evident copy, with the deployer's S3 read, and compares it with the
  copied rows by `uid`: a row whose flushed version differs is a Security Notice (fault `copydbonly`:
  an overwrite after its flush reaches the local audit unflagged; one before its flush is v1-equal);
  (3) records the copy done locally, so a re-run skips (1) and (2); (4) force-deletes the step-5 mailbox
  and bot-token secrets, so AWS can no longer fetch, and clears the local hold, resumes exactly the
  addresses step 1 paused and posts "fraud flagging back on"; (5) runs `destroy`'s AWS deletes: a
  change set that turns off the tables' and the user pool's deletion protection and the tables' PITR
  (with PITR on, `DeleteTable` keeps a system backup for 35 days, PointInTimeRecovery_Howitworks.html,
  fetched 2026-11-06 (rv); Step 12 checks `ListBackups` with `BackupType` SYSTEM shows none for the
  install, unverified), `DeleteStack` (the stack's inline auto-delete helper empties the bucket, every
  version and delete marker included, once the functions are gone; the deployer has no S3 delete right,
  OD-743), any `ecf-<install>-scratch-*` table (PITR restores create them outside the stack; one still
  `CREATING` is waited for), then the one-time identity `job/*` and the Slack configuration token
  `slack/config`, force-deleted with the deployer credentials, since no resource policy can remove
  access. v1 destroy's Slack steps (archiving channels, the manifest delete, the token revoke) are not
  run: the laptop keeps the Slack app. A killed abort is finished by running it again (each delete is
  idempotent, as `destroy`'s are; a stack left `DELETE_FAILED` is deleted again). Step 12 records
  abort-to-local-checking and abort-to-`init` times; `init --migrating` retries a same-name create
  while a deleted queue, secret or table is still going (SQS waits 60 s, API_DeleteQueue, fetched
  2026-11-06 (rv)). A later attempt starts again from
  `ecf init --migrating` (a new stack, a new login), as re-migrating after a rollback does; generations
  keep rising (the new bundle's generation is above any the destroyed install saw, recorded locally at
  step 4). No clear exists, so no runtime role can delete an `ITEM#`, and a `migrate run` always meets
  an empty install (map_model10 `migrate_rerun`, fault `keepaudit`: draft 19's clear, which kept the
  `AUDIT#` rows and lost every re-imported item). A `migrate run` resumed after a kill skips items
  marked `merged_by` this bundle and applies the skip rule as every merge does: an item its first chunk
  wrote and the sweep has hidden since was pruned in AWS (OD-726 replaces OD-720's exemption; a sweep
  and TTL can fall between the kill and the resume; fault `rerunnoskip`). `migrate run` is chunked by
  cursor under the migrate lease, so a second `migrate run` while one holds it is refused; the one-time
  identity is deleted when the run ends, not after the first decrypt; the bundle's settings (retention
  included) are written before its items (OD-727; map_model10 `settings_order`, fault `itemsfirst`: a
  sweep between items written first and the settings hides them under AWS's default retention), and
  an import from another install does the same; a retention lower than the target's posts v1's
  lowering notice (`retention.py:16-18`).
- **Rollback (requested within 7 days of step 6's `restore.at`, checked at the first request only;
  no cancel once its record is `committing`):** a freeze job whose order is Maintenance, F11 (the
  `migrate_rollback` step-up and the job record while `ecf-api` is live; the freeze; the rollback-kind
  export, items in flight by OD-670; `commit`; the local restore by the local service under the backup
  key, same `install.id`, generation max + 1, the 7-day guard, which clears the hold, takes over the AWS
  dead-man message and enqueues the "moved back" edits, v1's restore reissuing the open approvals;
  `finish`; the AWS secrets deleted without a recovery window; Socket Mode through v1's `ecf slack
  reauthorize`, then `ecf slack set-tokens`). Only the
  rollback-kind bundle is accepted; the AWS stack stays frozen until `ecf destroy`.
- **Re-migrating after a rollback:** `ecf destroy`, then `init --migrating` and the steps above;
  generations keep rising (N, N+1, N+2, N+3). The runtime secrets' names carry the same `install.id` on
  every leg (step 4 records the bundle's, which names the secrets step 5 creates); the rollback and
  `destroy` delete them without a recovery window, so step 5 can create them again (Step 12 checks it).
- **`migrate finalize`:** refuses while the local plist exists or the unit is loaded; a CLI-driven
  call to the kept local service (started held) removes its Keychain secrets; refuses while
  `slack/config` or a `job/*` identity still exists in AWS (`doctor` flags either, and the deployer
  force-deletes it); ends the rollback window. After day 7, `doctor` escalates an unfinalized migration to Operator Input Needed.
- **Lost, and said in the runbook:** on a rollback, by-value `slack-out.fifo` posts not yet sent (I6:
  item cards are posted again locally, unsent alert email is carried), `slack-in.fifo` clicks not yet
  processed, queued jobs,
  pending step-ups, `delayed` sends (back to `awaiting_approval`), login cooldowns; an `executing`
  item becomes `failed_unknown` (OD-673), an existing `pending` `sent` row set to `unknown` so `\Sent`
  reconciliation settles it where the provider keeps sent mail; with no `sent` row it needs `ecf item
  resolve` and a manual redo. Fraud flagging is off from step 2 to
  the first AWS check (stated limit, OD-621). Clicks fail visibly from step 2 to step 9.
- **Scheduled export** writes to `backups/`; `ecf export --pull` copies the newest (or a named)
  `backups/` object to the laptop's `export_dir` without bumping `export.seq`, where the local prune
  applies; SPEC §11.9 and `doctor` say so.

### Operations

`ecf init --mode aws [--migrating]` (generates or takes the backup key on the laptop, OD-616),
`login`, `logout`, `revoke-device`, `upgrade`, `destroy`, `doctor`, `budget set [--bedrock]`,
`retention set`, `billing activate-tags`, `iam deployer-policy`, `people relink`, `export --pull`,
`eval pull`, `pause|resume --restore-stage`, `migrate plan|run|abort|rollback|finalize`, `export keys
rotate` (AWS, OD-660), `restore` and `import` in AWS (freeze jobs, Maintenance), `aws unfreeze` (F12). CLI request timeout 30 s in AWS mode, except a freeze job's chunk invokes (the function's
timeout plus 60 s, retries off). `doctor` adds: Cognito session and refresh expiry, a user without TOTP, the
pool issuer against the authorizer, the concurrency quota, DLQ depth and oldest age, SNS, CloudTrail
active and validating, drift, the Lambda Python version, Bedrock retention, invocation logging, model
access and permission-set scope, the signing secret, no long-lived keys, a loadable local unit after
migrate, an unfinalized migration, `backups/` being the only copy, a freeze older than its expected
end, an `aws-freeze.json` left on the laptop (F13), and (locally) a frozen AWS stack left after a
rollback (`aws-rolled-back.json`).

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
  `status` update (OD-658), a stored local relink key, "1b's dev service" for Step 12; from draft 10:
  pause-first rollbacks and merges, their hold kinds, refusal answers and holder (OD-661), a re-bind
  reusing a deterministic OS identity key; from draft 11:
  the attempt mapping for restores and imports and "other addresses untouched" (OD-662), the CloudTrail
  rule on direct `ecf-jobs` invokes (OD-663), the `ConcurrentExecutions` wait and `GetMetricData`
  (OD-664), the `upgrade` hold, its grace and its `slack-in` drain (OD-665); from draft 12 (OD-666):
  the one-use job record and its 2-hour TTL from creation, the freeze epoch and dropping pre-freeze
  messages, the delay shift in the last chunk, the boolean `Frozen`, mailbox access removed before the
  rollback drive, `ecf-jobs` deleting the AWS dead-man message and bot-token secret, the hash list "at
  step (1)", the restore's "only a live terminal status kept", newest attempt over all grants (OD-667),
  "one quiescing mechanism" (OD-668).

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
   auto-pause requests); the audit `source` and `uid` migration, the random-`uid` trigger (created from
   Python) and the derived `uid` for pre-M1 rows (OD-659); the
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
   the live one; 1b, 10(c) and 11 have equal `set_version`. The dedicated checkout is a full clone of
   the public repository at the pinned public commit, with the private repository cloned into its
   `eval/private/` on a local branch at the pinned private commit tracking the private remote's branch
   (so the for-gate check's `@{u}` exists and contains it), built with `ecf eval build` there. The M1 code
   runs from m1ref's wheel ("After round 10"), with its `eval_root` set to the dedicated checkout's `tests/eval/synthetic`
   (the resolver finds the private part from that path, OD-648). Dev builds for 10(c) and 11 package
   the public part from the dedicated checkout (`--eval-set-from`), and the private part is uploaded
   from it. A version change after 1b re-runs from 1b. 1b is always compared with `ce3be1df` on the
   shared subset as "After round 10" says (main already has 398 cards), the new cards reported
   separately; the safety bar applies to the full set. *Pass:* 0 unsafe, fraud guard N/N, non-inferior to `ce3be1df` (SPEC §16.2) at
   `--margin 5`; run ID recorded in SPEC §16; the run's merged version and private commit equal the
   pinned ones and `private_cases` > 0. Round-8 details: before 1b, the public-part and merged
   `set_version` computed at the pinned commits are recorded in SPEC §16 with the commits (what 1b,
   10(c) and 11 are checked against); `ecf eval build` in the dedicated checkout runs with the pinned
   public commit's own code (so M1's builder can't change the bytes), and afterwards `git status
   --porcelain` is empty in both repositories; the for-gate check (`ecf.eval.clean_and_pushed`) records
   the upstream ref and its `merge-base --is-ancestor HEAD @{u}` result, both kept with 1b's record; the dedicated checkout is a full clone at
   a pinned commit at or after the eval/private merge, built with `uv run` there, and `merge.json`
   exists after the build; 1b runs on a second named local install (`ecf --install m1ref`, a real
   service with its own database, set up as "After round 10" says), never the operator's main install
   (no other install gains an `eval_runs` row), and its result file's path and sha256 are recorded in
   SPEC §16 and the file is copied to a kept folder; `public_cases` and `private_cases` are each above
   0 and equal `ecf.eval.membership(sources)`'s counts (OD-637). A shared ID whose label
   row differs from `ce3be1df`'s is excluded from that comparison and listed (whether `rescore` can
   re-score a v3 result on changed labels is unverified, so it isn't relied on; `compare --subset` skips
   the version check, `results.py:252`).
2. **Chat seam inbound and cards:** `ChatInbound`, `respond() → Ack` with `Decided`, registry,
   `FakeChat` variants; typed button values and the legacy-value table; the card renderer from status
   and its inputs (OD-651, OD-657); the answer table (OD-656). *Pass:* suite green on Slack and on both
   fakes; a `form_in_ack` fake gets `Decided` in the Ack; one parity test per v1 function that draws a
   card (the rendered card equals v1's); every row of the answer table tested; the `enqueue_post` prefix
   table complete (a test fails on any v1 or new prefix missing from it, each marked by reference or by
   value with its privacy class).
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
   the OIDC role: go-ahead), the `Frozen` parameter (F4) and the deployer policy listed in full (F14).
   *Pass:* CI green with every policy test; a snapshot of `ecf iam deployer-policy`; a job's steps pass
   `Frozen` explicitly, every other command that creates a change set on the app stack keeps it with
   `UsePreviousValue` and refuses a non-empty `Frozen` after creating its change set (the command list
   taken from the CLI's code), and every deploy path updates by change set; under a non-empty `Frozen` every function,
   mapping and schedule in the synthesized template (CDK-generated singletons included) is covered or
   on F4's one exemption list; every deploy path keeps `FreezeSeq`, the key parameters and the
   seed-version parameter with `UsePreviousValue` (a test per parameter); runtime roles denied writes to
   `JOB#` and `JOBLOCK` except as F1 allows, and `PutFunctionConcurrency`,
   `DeleteFunctionConcurrency`, `UpdateEventSourceMapping` and `scheduler:UpdateSchedule` (static).
6. **CLI for AWS mode:** `init --mode aws` (backup key on the laptop, cost projection, quota,
   credentials), `login`, `logout`, `revoke-device`, `export keys rotate` with the relink fingerprint
   (OD-660), freeze jobs (Maintenance, F0-F15, OD-670) for rollback,
   restore, import, `upgrade` (guard) and rotation, `destroy`, `doctor`, `budget`, `retention`, `billing`, `iam
   deployer-policy`, `export --pull`, `eval pull`, `people relink` in AWS. *Pass:* targeted tests;
   IAM-user credential refused; no key material in any job record, queue message, log, `status` or other chunk response (a test);
   an upgrade whose packaged set version differs warns, lists the addresses on the old version and
   requires `upload-private` to be re-verified; **the models (OD-672, OD-680, OD-690, OD-702, OD-724,
   OD-731, OD-740, OD-748):** `freeze_model9.py`, `delay_model9.py`, `map_model10.py`,
   `card_model4.py` and `check_refs.py` (now in `state-archive/m1/model/`) move to `tests/m1/` with
   `run_all9.py`'s seeded faults, its table of the check each must be caught by and its stated-limit
   counts, masked faults' included (a traceback, another check or a changed count fails the run;
   restatements marked), in their own CI workflow (`run_all9.py --ci`, on changes to the models or to
   the pure functions they import, and nightly; a public repository's
   schedule stops after 60 days without activity, unverified, so `doctor` for the repo's maintainer
   names the last nightly run): one job per base kind or fault, each with its own timeout, faults
   stopping at their first catch, every job under 45 minutes and 8 GB on the runner (a public
   repository's standard runner has 16 GB, GitHub-hosted runners reference, fetched 2026-11-06 (rv));
   the full `rollback` (about 18 GB, at about 1,015 bytes a state), `upgrade+foreign` (7.5 GB)
   and `rotation+rot2` (4.5 GB) base kinds, and the faults that explore them (`labelpresence`,
   `nocasmove`, `nolocallock`, `svc`, `abortalways`), run in the macOS merge gate on the same trigger as
   CI (the models and the pure functions they import; a CI test compares the two path lists; Step 6's
   gate line records them), never on the runner; a growth budget: a draft whose model adds over 50% to a kind's states says why; each model
   action stays one real write and reads only what a real read gives (`draft18-atomicity.md` to
   `draft22-atomicity.md`); `next_action(Obs)`, the ecf-jobs
   gates `gate(record, lock, stack_read)`, `map_in_flight` and `count_down(item, seq, sched_time)` are
   pure functions in `src/` that the models import (faults are test parameters per module, never hooks
   in production code); Obs carries the job ID the machine learned (its command, its file, `Frozen` or
   `status`'s lock holder), and the unfreezer has its own observe; a refinement test builds one moto and
   temp-directory world per distinct model observation and asserts `abstract(observe(world))` equals
   the model's, and for a torn read (`status` and DescribeStacks at different moments) that
   `next_action` equals its value at one of the two consistent moments or is a step its own
   post-create read or gate refuses; a gate test asserts that
   changing anything a gate can't observe leaves its decision unchanged; the Maintenance "Rule → model"
   table is checked by a test that every name in it occurs in the models' source as a function, field,
   action name, fault name or model file, and that every invariant, F heading
   and OD-670 bullet appears in that table or under "Not modelled"; per invariant
   (Maintenance): I1, `count_down` over the delay model's scenarios, two freezes in a row after a code
   deploy, a failed release (b), two overlapping ticks (the conditional write) and a tick scheduled
   before a freeze and delivered after the release (its new `seq` dated at its invocation) and the
   item's creator and `ecf-slack-in` on an environment that is the oldest configuration of the last 60 s
   (Cancel works from release (a) on), the tick reading `Frozen` and `FreezeSeq` from a stubbed
   DescribeStacks (counting nothing when `Frozen` is non-empty or the read fails, and counting during a
   non-freeze update), freeze (b) refused until 45 s after freeze (a)'s `UPDATE_COMPLETE`, and the
   local hold held longer than a delay, then aborted, leaving `remaining_s` unchanged, included; I2, a stop committed in QUIET applied by the first chunk before `running`, its card
   edited to "cancelled" after the release; I3, the 109,725 merge cases of `map_model10.py` through the
   real merge on moto, and 1,000 lifecycles per shape (absent with its row, never here, reopened,
   swept; fixed seed 20261106) through the real merge and sweep, a merge killed after each item and
   re-run, and a new job over the same bundle skipping marked items, a kill between an item's
   transaction and the next tick leaving exactly one card, a kill between a `queued` row and its SQS
   send posting exactly one card, two overlapping ticks issuing one grant, a grant expired and
   re-requested during a tick (one card per grant), an old-mail item given a grant and no card and not
   counted against the cap, a refused post marked `given_up_refused` and an outage give-up re-queued
   once, a row re-sent at most once per 15 minutes and not at all during a Slack hold, no card posted
   while the AWS migrating hold is up, a second job over the same source key leaving every card in
   place, the `needs_card` index empty within the drain time after the release, a pruned item
   (absent, with its `item.created` row) skipped and a never-here one imported, a carried
   `item.created` row indexed (a bundle whose item was pruned at its source, then an older bundle:
   skipped), a bundle's `item.created` row reused (audit rows equal the sum of sources by `uid`), a
   cross-install address-ID clash listed and skipped with its items and an email clash refused, excerpts written only in their
   item's transaction, a `migrate run` after `migrate abort` meeting an empty install and importing
   every item, the sweep racing an Undo (its write refused, the item kept), the sweep racing a raised
   retention (its write refused), a write on an expired, undeleted item and an update of a deleted one
   refused (a contract test over every `ITEM#` writer, each treating the refusal as "gone"), an item
   undone or reprocessed after it executed losing its `terminal_at` (a contract test over every
   terminal exit in the state machine), no write but the sweep setting `expires_at`, a sweep page
   whose settings check fails re-read and a conflicting item left for the next sweep, an export taken
   during a sweep restorable by v1, no role able to delete an `ITEM#`, a record it lists or an `AUDIT#`
   row by any write action in the Service Reference (static, pattern-matched; naming `expires_at`
   counts as a delete),
   `--replace` deleting nothing, `ecf migrate run` keeping open approvals open; I4, a forged SQS-shaped event refused, a chunk naming another job ID, a record in another
   state or without the lock refused, a delayed `begin` write after a takeover refused (conditional
   writes), the first chunk refusing an inactive person or another identity than the step-up's,
   `ecf-worker` refused a `JOB#` write; I5, every F0 row, each job killed after each step and resumed on
   the same and on a second machine (finding it through `status`'s lock holder), a laptop lost between
   `begin` and freeze (a) cleared by `aws unfreeze`, a stale change set refused after another update,
   `aws unfreeze` per kind at each allowed and refused point, `ecf aws continue-rollback` on a stubbed
   `UPDATE_ROLLBACK_FAILED`, a stack left in `UPDATE_ROLLBACK_COMPLETE` by a foreign update, a second job
   after each kind finishes, after `aws unfreeze` and after an expiry, a slow PITR `prepare` extending
   its expiry, a deploy outside a job refused while `Frozen` is non-empty (and after a done rollback;
   and one whose change set, created before a freeze, carries `Frozen` empty), a lock holder of another
   kind, or of this kind with other bound values, reported, not adopted, two concurrent `POST /v1/restore` calls for one
   job committing once (the second rolls back and answers "already restored"), a restore tail
   sub-step forced to fail (start-up goes on, the answer names it, the other sub-steps finish), the rotation killed between `AWSCURRENT`'s move and `aws-keys.json`'s and finished on its own
   machine, then the other machine's `doctor` refusing until the new fingerprint is typed, the
   rollback killed after `rollback_export` and resumed after the object expired (re-exported, then
   pulled, then committed), the `committed` re-ask with the pulled bundle deleted completing, the local service killed after the restore's commit and before its tail (the
   start-up tail runs every sub-step but the seed; F0's next request runs the seed and writes
   `complete`; `finish` waits for it), a restore request on an `aborted` row answered "aborted" with
   nothing run, a bot token replaced mid-rollback (the tail completes; `set-tokens` allowed once
   `committed`), an escalation queued in `slack-out.fifo` at the move back posted locally once,
   `--abort` after the commit stopping with "already restored"; I6, a post queued before the freeze delivered after the release, a by-value post after a
   restore marked "from before", a non-stop click in QUIET answered "not done"; I7, a throttle on the
   first invoke treated as wait, a second concurrent chunk refused, a client timeout starting no second
   chunk, `first` refused before the stamp plus 960 s, no re-stamp after the code deploy, a `jobs.fifo`
   batch refused while `Frozen` has an owner; I8, a change set and a release refused when the deployed
   keys differ from `AWSCURRENT`'s, a change set whose own key or seed-version parameter differs refused,
   the rotation deploy accepted against this job's version, the rotation's `finish` refused until
   `AWSCURRENT` moves, `abort` refused after `rotation_list`, two rotations in a row each deciding by
   its own version ID (the first's `AWSPENDING` label left in place), the seed payload rebuilt from
   scratch after a crash byte-equal, an `UpdateSecretVersionStage` with a stale `RemoveFromVersionId`
   failing and F0 reading again,
   a hash list over the cap refused before progress; the rollback's `--abort` through the service route
   racing the restore, the `rollback_restore` row read back after a real v1 `_apply`, and a re-run after
   the restore committed answered "already restored" once the tail is done; a non-empty target without
   `--replace` refused; a restore after retention was lowered and raised again skipping exactly the
   pruned items, with no point refused and no merge length capped.
   **6b. Laptop relay and the AWS provider path:** the relay (Unix socket for MCP and status, TCP for
   OTLP, allow-list, session token required), the shared refresher, `--provider bedrock` with Cognito
   step-up, the `GetCallerIdentity` check, settings generation. *Pass:* the scripted OTLP test; a
   profile token on a CLI route refused; a CLI JWT without a profile token refused on MCP routes; a
   request without the session token refused by the relay; a CLI refresh mid-session keeps the relay
   working.
   **6c. Migrate machinery:** the local `migrating` hold, the migrate and rollback bundle kinds, the
   one-time recipient (a job-scoped secret only `ecf-jobs` reads, force-deleted by `ecf-jobs` when
   the job ends, with no recovery window), `migrate
   plan|run|abort|rollback|finalize`, `migrate_link`, `migrate_secret` and the signed phase attestation
   (OD-642), the migrating-only allow-list on AWS, `ecf eval upload-private`. *Pass:* the hold survives
   a restart, refuses checks and Slack, allows export and step-up, and a restore clears it; abort restores
   local checking and force-deletes the step-5 secrets, the one-time identity and `slack/config`; the
   one-time identity gone with no `DeletedDate` after its job ends, after its record expires before
   `freezing` and after a `migrate abort` before step 6; no root key in any AWS request (a test); a secret write with a valid Cognito step-up
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
   membership counts (`public_cases` and `private_cases`, computed by `ecf-jobs` with
   `ecf.eval.membership`, OD-637) equal the laptop's, and an upload with either at 0 is refused; the
   gate guard's call-site test (no `membership` or `load` outside `ecf-jobs`' upload), a run whose
   upload is no longer current not counting, and stored counts of 0 or missing failing the gate; both the
   clean-and-pushed check and the counts come from one function each in `ecf.eval`; AWS key rotation
   (OD-660) in the CLI with a crash after each step (the old keys stay in force until the deploy; a
   re-run finishes), the old relink key refused after the switch, rotation refused before `migrate
   finalize`; a relink with a forged signature leaves its nonce unspent; the attestation body appears in
   no log or table (a test); an import of a rollback bundle with an unsettled alert row lists it; the
   rollback restore posts one new card per open item and edits the old one, and loads `draft_versions`
   (counts equal); an AWS import re-encrypts to the job's one-time recipient and no root key appears
   in any request.
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
   retry behaviour recorded); a scheduled export forced across two `ecf-jobs` invocations with a
   same-kind job queued before the hold (the holder's continuation, in its own group, runs during its
   hold and it resumes); a freeze job (Maintenance) through cfn-exec with its exact policy: F4's two
   change sets (schedules and mappings off with Cancel still working, then every frozen function
   throttled: synchronous invokes refused, a queued async event expired, not run, after the release),
   a change set created before another update refused on execute (a pass criterion: I4 and I7 rest on it), F5's reads matching,
   whether a configuration change replaces warm environments (recorded; a warm function with
   `Frozen` = FROZEN exits, and none exits in QUIET), F9's release restoring `ecf-slack-in` to 20 before the schedules,
   whether the schedules update after the functions' environments (`DependsOn`), CloudFormation's
   rollback order after a failed release (b), and whether a disabled schedule can still fire (each
   recorded; OD-693 makes none of them gate I1), whether a function invocation sees one whole
   environment with `Frozen` and `FreezeSeq` changed together (recorded, not a pass since OD-736: the
   tick reads the stack, the creator is safe stale, and Cancel's path reads no environment; during
   freeze (a), release (a) and release (b) each function is invoked at least once a second and logs
   its pair over at least 200 invocations per update),
   the tick's and the broker's `MaximumEventAgeInSeconds` as set (a pass), items per tick sweep
   (recorded), a
   tick that runs on an environment saying frozen writing nothing (a pass), a change set created while another executes and a direct
   update made beside a pending change set (each recorded: refused or not), the effective
   `DisableRollback` default (recorded), the `jobs.fifo` delay after a freeze (recorded), the
   stuck-freeze alarm (tick heartbeat absent, the real 6-hour threshold) firing and delivered by email
   through the `-direct` topic within 15 minutes of the state change with the stack FROZEN, and one OK
   email after the release (a pass), the email subscription confirmed by the CLI's `ConfirmSubscription`
   with `AuthenticateOnUnsubscribe` (`ConfirmationWasAuthenticated` true) and the footer's unsubscribe
   link refused (a pass) and `doctor --fix` re-creating a subscription a scanner confirmed, through a mailbox whose
   scanner opens every link (recorded: the stated limit says it may stay unauthenticated),
   each function's stale-environment window after a configuration update, measured from the function's
   `LastUpdateStatus` `Successful` (the longest time an invocation still logs the old (`Frozen`,
   `FreezeSeq`); recorded; the tick doesn't use its environment), DescribeStacks' lag: for every update
   of a freeze and release, a read every second from the update's start logs (`Frozen`, `FreezeSeq`,
   status); pass = from 30 s after the update starts, and from 30 s after it completes, every read
   shows the new pair (delay_model9 generates both anchors and, with OD-742's 45 s wait, is clean up to
   a 60 s lag; `dslate`, 90 s, breaks it; `nowait` breaks it at 30 s); the shortest freeze (a) to
   release (b) completion span, failed (b) included (recorded, against the model's 65 s); what
   DescribeStacks shows mid-update (recorded: the model generates both), a `BatchWriteItem` from each runtime role on the main table
   denied (a pass), the expiry sweep's Scan seconds per GB and items hidden per second on a seeded
   table (recorded), a seeded 100,000-due-item sweep finishing in under 1 hour (a pass) and `ecf
   retention set` during it succeeding within 30 s (a pass), a seed version written at the 65,536-byte cap, the deployer reading a rollback bundle from S3 and v1 restoring a rollback-kind bundle
   outside a rollback (recorded), the first chunk's Scan seconds per GB on a seeded table, the
   positive control (an Approve sent during (b)'s `UPDATE_IN_PROGRESS` whose DATA reply the fake SMTP
   server holds 90 s: the SMTP log time after (b)'s `UPDATE_COMPLETE`, `GRANTRES#` `done`; (b) is held in
   `UPDATE_IN_PROGRESS` by a test pause hook, so the interleaving is always reached), a drive killed between chunks
   and resumed, a re-run within 10 s of a kill (throttled, waits), freeze (a) forced to fail once (a bad
   parameter) and the re-run, the runbook's deployer path from a broken authorizer once, a freeze while
   a scheduled export chunk runs (Scan and PITR paths; the export cancelled, `MAINT` gone, its own
   scratch tables deleted once ACTIVE and partial object deleted, no export after the release), a
   direct invoke on an unfrozen stack refused, runtime roles denied the concurrency, mapping and
   schedule changes (live), provisioned concurrency removed if Step 9 adds it, a presigned PUT with a
   checksum and `If-None-Match: *` refused on a second write (recorded), a PITR restore of each table
   and the export's PITR path under the boundary (which resource each action is authorised against,
   recorded), a `delayed` dev item's remainder across a freeze (a pass: the stored `remaining` unchanged from
   freeze (a) to release (b)), the rollback's hand-over (the AWS dead-man
   message replaced by the local `keep_armed`, or "back" posted if it fired; secrets by the deployer
   with `ForceDeleteWithoutRecovery`, and `CreateSecret` of the same name issued at most 1 s later
   succeeding within 120 s with back-off (a pass; time to success and retries recorded), a force delete
   of a secret already scheduled for deletion (recorded), `ListSecrets` with `IncludePlannedDeletion`
   listing it (recorded), the condition key `secretsmanager:ForceDeleteWithoutRecovery` (recorded)),
   `ListSecrets` and `ListTables` allowed only with resource `*` (recorded), a restore's PITR start
   waiting for `LatestRestorableDateTime` and a scratch restore with only the grants table's
   `stable_id` index (restore seconds per GB recorded), merge items per second and an import's audit
   rows per second on a seeded table (recorded; F3's expected end uses them), an item whose
   `expires_at` is removed after it passed but before TTL deleted it staying (recorded),
   `DescribeChangeSet` showing resolved values for `UsePreviousValue` key and seed-version parameters
   (a pass: I8 rests on it), a rolled-back dev stack
   then unfrozen by hand at once unable to send (no mailbox or Slack secret), and each part of an
   upgrade's wall time (begin, two freeze updates, wait, code deploy, reads, two release updates;
   the fraud-off gap, recorded); a 64 MB message on a throttled link processed by the large-message step
   within the stated drain bound; a forced `UnprocessedKeys` in `ecf-slack-in`'s read; the broker's `ConditionCheck` on `MAINT` allowed
   while its `PutItem` on `MAINT` is denied; a seed version written with a custom staging label leaving
   `AWSCURRENT` where it was (a pass: the rotation's I8 rests on it, OD-660), `AWSCURRENT` moved after
   `rotation_check`, a retried `PutSecretValue` with the same `ClientRequestToken` adding no second
   version, the same token with a different payload refused (recorded) and the code reading the
   version and comparing fingerprints, an `UpdateSecretVersionStage` with a stale
   `RemoveFromVersionId` refused (a pass), and a bundle signed by the old key but not on the step-(1) list refused; the hold's p99 at
   most its recorded expected end (the botocore items are recorded, not gating); a job queued during a scheduled export's hold put back
   to its expected end and not dead-lettered; a 24 h idle run (compared with the cost line scaled to the dev stack's own resource counts);
   teardown. *Pass:* chosen memory = the
   smallest meeting max Max Memory Used under 80 % over 3 runs, and the GB-s row recomputed with it;
   no run killed by timeout; no lease lapse; the idle run's extrapolated monthly cost within ±25 % of
   the infrastructure line (else the table is updated before Step 13); the deploy-to-teardown cost of
   Steps 7-8, by day from the dev account, read about 48 hours later and before the Step 9 go-ahead,
   under US$5.
9. **Slack request URL (go-ahead):** manual install with the signing secret; Socket Mode to request
   URL and back (`reauthorize`, then `set-tokens` with a new app-level token); `reauthorize` keeps the URL; real clicks; 5 forced-cold clicks and form buttons; the
   placeholder and `views.update`; a non-approver's Show excerpt; bad-signature and stale posts;
   replayed body; double click; a click processed after 5 minutes in the queue (accepted). Cancel
   (OD-649): concurrent pairs of a Cancel and the tick's `delayed → executing` (at least 100 in the
   harness, then 20 on the real stack), exactly one of each pair committing; a Cancel after the tick moved the item to
   `executing` (told "too late: sending has started"), including during v1's retry wait; a Cancel after
   a requeue, with the new card (wins) and with the old card (told "this card is out of date"); a Cancel
   on an `expired` or `failed` item (refused, no stop record); a double Cancel, Cancel then Reject, and
   another approver's Cancel (told "already cancelled or rejected"); an Approve while a stop record is
   pending (refused, naming the stop); a Cancel in QUIET or during an export hold (wins); a modal submit and a form button in QUIET (answered
   "not done" through `response_action`; the typed text is lost, as stated); a Cancel on a look-alike card
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
   update used; a refusal after the person closed the view (sent by DM); an org-wide Grid
   install refused, and the `auth.test` fields and the team and enterprise payload paths recorded; a
   button, form button and modal submit on a frozen stack (API Gateway's status and what Slack shows on
   desktop and phone, recorded); a Cancel committed between an upgrade's freeze updates (a) and
   (b) (its card edited to "cancelled" after the release; its `response_url` answer only if under 25
   minutes old, F10); a click on a card replaced
   by a restore (told "this card is out of date"); the freeze notice and end notice; the answer table's identity and budget rows; a legacy v1 click between the switch and the "moved" edit (refused); a Cancel during a scheduled export's hold whose `response_url` post was skipped (`ecf-worker` posts
   the result before its hold check, within 5 s); click to card edit; the dead-man message at cutover; teardown. *Pass:* max `$context.responseLatency` under 2.5 s
   cold and 0 `expired_trigger_id`; the real form within 5 s p95 cold (over at least 20 cold opens); the non-approver sees nothing
   and can't cancel; replay and double click each one decision; every Cancel on a live stack is
   answered with its true result as an ephemeral within 5 s (a Cancel committed during a freeze's
   updates is shown by its card after the release; one on a frozen stack fails visibly); no Cancel answered "cancelled" for a sent email, checked against
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
    bill. *Pass:* 0 unsafe, fraud guard N/N, non-inferior to 10(c) and to 1b at `--margin 5`, and to
    `ce3be1df` on 1b's `--subset` file at `--margin 5` (so the margin doesn't add up along the chain), `compare` shows only "different provider"; the run's
    `set_version` equals 1b's and the dev zip's packaged version equals the pinned public version; 0
    requests failing after their last attempt and 0 4xx other than 429 (retried 5xx reported);
    determinism flips at most one more than 10(c) and at most one more than 1b; per-item cost by profile tag
    over the run window, read about 48 hours later, within ±25 % of the projection recomputed from the
    run's own token counts (outside: a recorded miss, and §16.3 and `init`'s projection updated in
    Step 13).
12. **`ecf migrate` (go-ahead):** plan, run, abort at step 6 (the AWS install destroyed; the abort's
    audit copy equals the AWS `AUDIT#` rows by `uid`, a row overwritten after its flush gives a Security
    Notice, no `ecf-<install>-scratch-*` table and no SYSTEM backup is left, and the deployer holds no
    S3 delete right; OD-744), `init --migrating` and run again, rollback after one tick (the bundle
    pulled before `commit`; the queued "moved back" edits counted and the time to the first new post
    in each channel recorded),
    re-migrate, finalize, and a second abort after step 7; 50 synthetic messages sent between cutover
    steps 2 and 10; one dev address seeded with a Bedrock run (its own go-ahead and run ID; the "other
    set version" case is a unit test of `migrate plan`, since the latest gate run is per pins digest).
    The set comes from the dedicated checkout (a `--eval-set-from` build). The local side is a
    throwaway real install (`ecf init` under its own name, a test Slack app and a test mailbox, as M8
    Step 10), whose gate rows come from a `--fraud-only` C run on it with Step 12's code (go-ahead); the
    count of addresses whose `gate.synthetic` passes locally (more than 0), the run's three membership
    counts (each above 0) and the digest are listed.
    **Seeds, per freeze job** (Maintenance), each written by a test-only seeding role in the throwaway
    account (not the deployer, whose policy has no data rights, F14) after F5's reads (during the wait,
    so nothing live touches them) and its write time recorded, unless it says otherwise; each seed has
    an ID and an expected end status, listed before the run:
    - **Rollback:** a Cancel and a Reject applied before the rollback; a Cancel committed live just
      before the freeze with the `slack-in.fifo` mapping and the tick schedule disabled by hand first (so
      only the first chunk can apply it); a Cancel clicked after freeze (b)'s `UPDATE_COMPLETE`; the
      positive control as Step 8; one `executing` item each with no attempt, a
      newest `retryable`, a `done`, a `failed`, and a no-outcome attempt with its `sent` row at
      `pending`; a requeued item whose older grant has the higher n and a no-outcome attempt; one
      `undoing` with no undo attempt and one with a no-outcome undo attempt; one `delayed` item; a queued alert email; the laptop
      killed mid-drive and the rollback re-run.
    - **AWS restore** to a T: items `awaiting_approval` at T and, after T, one sent (`executed`), one
      `failed_unknown` (no-outcome attempt), one requeued with a no-outcome attempt, one created and
      sent, one created and rejected; killed mid-drive and resumed.
      Also one item `undoing` live whose original ran after T, and one present at T and sent after T on
      a newer grant.
    - **AWS import** with `--replace` of a scheduled-export bundle from the same install: one item with a
      live no-outcome attempt, one sent live on a newer grant after the bundle, one cancelled live after
      the bundle, and one rejected live after the bundle.
    - **Upgrade:** live, before `begin`: a `delayed` item whose remaining delay would end inside the
      wait; between freeze (a) and (b): a Cancel clicked and a fraud alert card written straight to
      `slack-out.fifo`.
    - **Every kind:** a second job of another kind started right after the first finishes, and one
      after an `aws unfreeze`; two upgrades in a row with a `delayed` item across each.
    - **Rollback re-run:** the CLI killed after the local restore commits and the rollback re-run; the
      local service killed between the commit and the end of its tail, then started again; `--abort`
      run after the commit.
    - **Retention:** an Undo between the sweep's read and its write (a pause hook in the sweep; the item
      stays); an escalation created for an item between the sweep's read and its write (the item and
      the escalation both stay, then hide together at the next sweep); an escalated prunable item
      hidden with its escalation and card rows, then an AWS export of the table restored by v1 (foreign
      keys on: it restores); a new message under a hidden item's `stable_id` (a fresh item, no old
      escalation or card); retention lowered, a sweep, retention raised, an item executed and
      then undone, and a restore to a point before all of it: the pruned items skipped, the undone one
      still present, nothing refused.
    - **Rotation:** the laptop killed after `AWSCURRENT` moves and before `aws-keys.json` changes, the
      rotation finished by a re-run on the same machine, its file brought current on that run; a second
      machine with the deployer credentials run during the rotation reports it ("continuing it needs
      the key text: run it on <machine>", OD-733) and adopts nothing; after the rotation, that machine's
      `doctor` and `export --pull` refuse until the new OD-341 fingerprint is typed, then pass (OD-745);
      the rotation's freeze-time export exists in `exports/` and restores on a throwaway Mac.
    - **Re-migrate:** after the rollback, `destroy` and a new migrate run create the mailbox and
      bot-token secrets and the Slack configuration token under the same names (no recovery window
      left).
    - **Move back, Slack:** a `failed_unknown` item and an old-mail awaiting item on AWS before the
      rollback; after it, `reauthorize` then `set-tokens`.
    *Pass:* every seed ends in its listed status, compared on the exported bundle's item rows (not a
    count), and each re-offer has exactly one fresh grant with no attempt record; also seeded: an item
    `failed_unknown` with no attempt record (from a v1 import), an `undo_failed` item, a restore point
    older than the retention period (accepted; its pruned items skipped), and an import from another install's bundle; all N messages
    pre-checked on the first AWS check; a secret write with only one of attestation and step-up refused;
    audit rows after run, rollback and re-migrate equal the sum of the sources by `uid`, 0 collisions;
    nothing starts sending in AWS after the wait ends on the rollback (the test mailbox's Sent folder
    and the fake SMTP log), and the stalled send is delivered after (b)'s `UPDATE_COMPLETE` and arrives
    as `executed`; the pre-freeze Cancel's `applied` record names the rollback's job ID, and it arrives
    locally as `cancelled`; read from the local database as the restore commits (before any reissue): the `executing` items
    arrive as `failed_unknown` (no attempt, `retryable`: OD-673), `executed` (`done`), `failed` (`failed`) and
    `failed_unknown` with its `sent` row `unknown` (no outcome), the requeued one as `failed_unknown`,
    the `undoing` ones as `executed` and `failed_unknown`, the `delayed` one as `awaiting_approval`;
    each killed drive's progress item shows every chunk applied once and its audit counts equal the
    sum by `uid`; the restore re-offers none of its seeds (the `failed_unknown` and requeued ones come
    back `failed_unknown` with `sent` rows `unknown`, the sent ones `executed` with their live `sent` rows,
    the late `undoing` one `executed`, the created-and-rejected one `rejected`) and pauses every
    address; the import maps its attempt item to `failed_unknown`, keeps the sent one `executed`, the
    cancelled and rejected ones as they are, and pauses every address; a second reply to the sent
    items' threads is refused by the reply limit; each upgrade's `delayed` item has, when its send starts,
    at least its whole delay of Cancel-working time (from the recorded times of the last tick before
    freeze (a), freeze (b)'s start, release (a)'s `UPDATE_COMPLETE` and the send), its Cancel's card shows "cancelled" after the
    release and its fraud alert card is posted after the release; each second job runs to its end; Bedrock gate rows and gate-eligible Bedrock runs 0 on AWS; the private part uploaded
    and verified before the AWS `migrating` hold lifts; cursor and gate-row counts equal; after an AWS
    restore, import or upgrade the stack is released, no `MAINT` record is left, a mail check completes
    on a resumed address, the end notice is posted, and the DLQ messages from the freeze are listed;
    at most one new card per open item (a by-reference first post queued before the freeze checks
    `CARD#` first; old-mail items, which get no card, excluded), old cards edited (`conversations.history`), and after the rollback each open item has
    exactly one actionable local card while its AWS card says "moved back" (the `failed_unknown` one's
    edit names `ecf item resolve`, the old-mail one's "in the scan summary"), local Socket Mode connected after
    `set-tokens`; the local unit
    doesn't return after a reboot; `gate.synthetic` passes on AWS for every address that passed
    locally; abort restores local checking; the rollback leaves AWS frozen and local restored with a
    bumped generation, local `ecf` commands working (`aws-rolled-back.json` only warns); AWS's cards
    edited to "moved back"; the fraud-off notice posted and the gap's mail pre-checked on the first AWS
    check; the Cancel clicked after freeze (b) completed fails visibly in Slack, writes no `STOP#` record and
    leaves the item's state unchanged; the queued alert email sent locally after the rollback; abort
    after step 7 restores local checking, returns the Cognito identity to pending, keeps the aborted
    run's audit rows, and a re-run links and imports cleanly at a generation above any seen; the stamp
    rule's unit test passes (migrated runs count only on equal source set versions); after the
    rollback completes the AWS dead-man message is kept or renewed by the local `keep_armed` (`deadman.py:96-97`), or
    followed by "back" if it had fired, the re-run after the commit is answered "already restored" once
    the tail is done and posts no second card, the killed service runs its tail on start-up and the
    re-run's request finishes it (every open item has a grant and a card, the seed is current, one
    notice), the run after the abort at step 6 meets an empty install and imports every item (counts equal the
    bundle's), a fraud
    escalation enqueued between freeze (a) and (b) and then rolled back gets one local card, `--abort` after the commit
    stops without aborting, the AWS mailbox and bot-token secrets are gone (no recovery window) and the
    re-migrate creates them and the configuration token again, the stuck-freeze alarm fires once in the
    12 hours after the rollback, and the rollback's
    `--abort` raced against the local service's restore 50 times never leaves both committed and aborted;
    `ecf destroy` then completes on the frozen stack with the deployer credentials; the runbook records
    the freeze, export and PITR timings beside the fraud-off gap.
13. **Docs and release:** SPEC (`requested_by` in §6 and OD-579's row, F7; §1.2, §3.2 broker and `ecf-jobs` in the TCB, §4, §6.4 grants and spend
    records and `STOP#` stop records (OD-656), §8, §9, §9.6 new step-up purposes (`migrate_rollback`, OD-663), §10.1 (the signing
    secret; the Slack queues: holds by visibility, 1,000 receives, crashes counted, 14-day retention,
    Slack-wide holds, OD-645, OD-651, OD-657; the closed click table; Cancel and Reject at the request
    URL as v1, OD-649, OD-656; form submissions, OD-657), §10.4 MCP
    tokens, §11.9 scheduled export, format 5 and bundle kinds, §11.10 AWS upgrades and format warnings,
    §12.2 AWS stated limits (deployer full data access in effect, through `ecf-jobs` and stack updates, admin bypasses every rule, WAF unavailable,
    Cognito limits, a compromised broker has full mailbox authority, a compromised `ecf-slack-in` can
    forge one-click actions, stop the current grant of any item whose ID it learns (protective actions
    included), read identities, people, roles and item status, post as the bot and invite through `groups:write`
    (caught by the OD-215 member check), IAM's limits on DynamoDB (attribute names and key prefixes,
    not values; no condition key can require a `ConditionExpression`), a role removed between a Cancel's
    read and its commit (authority is judged at commit), Slack posts held past 1,000 receives
    (about 3.4 days) dead-lettered, every Slack click and most `ecf` commands failing and mail checks and
    fraud flagging stopped during a freeze (rollback, restore, import, upgrade, rotation; OD-666), a
    restored send that was waiting at T coming back `failed_unknown` (OD-662, OD-673), every stated limit in Design,
    Maintenance (copied from that section at Step 13, not kept as a second list here), a card
    up to 5 minutes behind after a long Slack outage, a compromised
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

**Round 10** (draft 10, f86ad4d, eight Opus reviewers, delta, plain Agent calls): about 75 findings,
four high (Y1-Y4); no send after an accepted Cancel or Reject; the cost reviewer judged complexity down.
Three highs were in the rollback's in-AWS coordination for the sixth round running; the operator chose
to freeze the stack by deploy and drive rollbacks, restores and imports from the laptop (OD-661), and
accepted every other recommendation, 2026-11-05.

| # | Round-10 finding | Landed |
|---|---|---|
| Y1 | rollback disables the schedules the holder needs | OD-661 (no holder for rollbacks); scheduled exports keep the tick |
| Y2 | holder stuck behind a held message | OD-661 (`hold:<job id>` group for scheduled exports and upgrades) |
| Y3 | `executing` items stranded as `failed_unknown` | OD-661 (mapping by attempt record); Step 12 |
| Y4 | 1b vs `ce3be1df` needs `--subset` | After round 10; Step 1b |

Mediums folded in ("After round 10"): m1ref setup and file lifetime; Step 12 seeds; rotation (bundle
hash list, `AWSCURRENT`, label Deny, re-run, laptop fingerprint file, local relink fingerprint, binding,
hold); relink conditions; OS identity binding and privacy; `Null` on `LeadingKeys`, the `MAINT` Deny,
`CARD#` scope, key names, CloudFormation native events; the large-message step, expected-end updates,
upgrade grace, lease retry; by-value cards and privacy, card-record states, 300 s counting, DM
refusals, form inputs, Grid recording, legacy table, `UnprocessedKeys`; staged backfill setting,
trigger from Python, `via: cli`, staged `source`, derived-uid limit; ownership of rotation and holds;
grant-reader contract test; metric names.

**Round 11** (draft 11, b6a031e, eight Opus reviewers, delta, plain Agent calls): about 90 findings,
six high (Z1-Z6), all in draft 11's freeze; no send after an accepted Cancel or Reject, no approval
without a person. Most highs came from the AWS restore and import doing something other than v1; the
operator chose to copy v1 there (OD-662), take the step-up and bundle upload before the freeze (OD-663),
make each freeze one resumable job (OD-664) and put upgrades and the rotation deploy on the freeze
(OD-665), and accepted every other recommendation, 2026-11-05.

| # | Round-11 finding | Landed |
|---|---|---|
| Z1 | a scheduled export caught by the freeze blocks the drive or resumes across the merge | OD-664 (first chunk cancels it, deletes `MAINT`); Step 8 |
| Z2 | attempt mapping on restores and imports sends twice; two contradictory mappings | OD-662 (rollback only; import and restore as v1) |
| Z3 | a delayed send on an "untouched" address goes out after a failed Cancel | OD-662 (no partial import; every address paused; delays stop while frozen) |
| Z4 | no resume, abort or unfreeze; frozen refusal blocks restore, import, destroy | OD-664 (job state, resume, `aws unfreeze`, power assertion, credential refresh) |
| Z5 | no step-up or admin check; no bundle path; CloudTrail rule blind | OD-663 (job record after step-up; upload before the freeze; rule dropped) |
| Z6 | client retry runs two chunks at once | OD-664 (retries off, job lease, `ecf-jobs` concurrency 1 when frozen) |

Mediums folded in ("After round 11" and the Design sections): newest-attempt keying, `retryable`,
`undoing`, `pending` sent rows; rollback restore card order and tables; pre-epoch messages dropped;
freeze deploy pinned and fingerprint-checked; freeze confirmed by reads, not a metric; fraud-off gap
stated; scratch restore before the freeze; `jobs.fifo` receives; provisioned concurrency; freeze IAM
and deploy tests; cfn-exec PassRole path; Slack throttle display recorded, freeze notice before the
freeze; prefix table; rotation and freeze exclusion, hash list in the freeze; AWS import key path;
restore keeps live identities; post-rollback alarms, dead-man and secrets; Step 12 seeds; m1ref wheel;
`fixed_labels.jsonl`; 11 against `ce3be1df`; stale hold-era lines.

**Round 12** (draft 12, 9302be1, eight Opus reviewers, delta, plain Agent calls): about 85 findings,
ten high (AA1-AA10), again mostly in the previous draft's own fixes; no send after an accepted Cancel
or Reject on the rollback path, no approval without a person. The operator accepted every
recommendation and asked for the patching to stop (2026-11-05): draft 13 rewrites the freeze once from
invariants as the single section Design, Maintenance (OD-666), with one in-flight mapping (OD-667) and
stated limits (OD-668), deletes the superseded decision text, and was checked operation by exit by
invariant before review (`state-archive/m1/draft13-check.md`, six gaps found and fixed first).

| # | Round-12 finding | Landed |
|---|---|---|
| AA1 | "first chunk consumes" refuses every later chunk; 2-hour TTL deadlocks a killed drive | F1 states, expiry by attribute, cleared at `freezing`; F6 gating |
| AA2 | delay shift from an unrecorded time, skipped by `aws unfreeze` and aborts | F3 T0 before the deploy; F8 shift by the first tick after any unfreeze; F9 schedules last |
| AA3 | dropping pre-freeze messages loses alert cards and posts and answers an applied Cancel "Not done" | F10: nothing dropped; stops answered from `STOP#` |
| AA4 | the rotation's own freeze refused; its unfreeze fails the fingerprint check | F11 rotation order; exemption by job ID; I8 against the new entry |
| AA5 | an AWS restore re-offers items in flight, requeued or created after T | OD-667 restore rule |
| AA6 | newest attempt over all grants picks an older grant | OD-667 current grant first |
| AA7 | the rollback's AWS deletes have no path or IAM | F11: local service deletes the dead-man message, the deployer the secrets (F14) |
| AA8 | the import binding can't be checked after re-encryption | F2 manifest hash, re-signed upload, checksum PUT; F11 import check |
| AA9 | a broken `ecf-api` blocks recovery | OD-668 stated limit, runbook deployer path |
| AA10 | the freeze marker blocks local after a rollback; no way out of a stuck rollback; `destroy` step-up | F11, F12, F13; OD-668 |

Mediums and lows folded in (Maintenance and Steps): `ecf-jobs` in the wait and throttles as waits;
scratch tables by job ID; gating by kind against forged events; the code deploy re-read and the synth
coverage test; the job's person re-read and `requested_by`; people live-wins on a restore; the dead-man
message as the stuck-freeze alarm; card re-render and `CARD#` on restore and import; OD-665's wording;
Step 12 seeds (tick disabled, positive control, `pending` sent row, write times, awaiting-at-T,
same-install import bundle, settle timing, expected count, `undoing` no outcome); Step 9 pass scope;
`jobs.fifo` 1,000; stale hold-era text; end notice; lid-open runbook; frozen-stack cost; the
eval/private draft 9 functions in 1b and the upload.

Draft 13 got its review in round 13.

### Phase R round 13 (8 Opus, draft 13 7b3c114, 2026-11-05)

Ten high (BB1-BB10), mostly in draft 13's own rules; every lens judged draft 13 simpler than draft 12.
No send after an accepted Cancel or Reject was found except through BB4 and BB7, and no approval
without a person. Draft 14 changes the protocol's shape (OD-669 to OD-671) and model-checks it rather
than patching each finding; the models live in `state-archive/m1/model/` until Step 6 moves them into
the tests.

| # | Round-13 finding | Landed |
|---|---|---|
| BB1 | a tick before the freeze used up the delay shift | F8: v1's countdown, no shift, no T0 |
| BB2 | job states and `JOBLOCK` had no writer on a frozen stack | F1: only `ecf-jobs` writes after creation; `finish` and `abort` kinds; the lock released with the terminal write |
| BB3 | a resumed job repeated the begin step and aborted | F0: the reconciler; `begin` only from `ready` |
| BB4 | an import with `--replace` re-offered items sent, cancelled or rejected after the bundle | OD-670: one start rule; `map_model.py` |
| BB5 | the restore dropped `sent`, `threads`, `senders`, `gate` live-wins | OD-670 union, live row winning |
| BB6 | `undoing` with no undo attempt re-offered | OD-670: `executed` |
| BB7 | a rollback could be unfrozen after the local restore committed | F11 `commit` state; `abort` refused; same-machine `--abort` only |
| BB8 | the import binding was circular; old-key bundles unsignable | F1/F2: `manifest.json` hash and `replace` bound; no laptop signature |
| BB9 | backup and restore IAM narrower than DynamoDB checks | OD-671: no on-demand backups; PITR rights listed, Step 8 live check |
| BB10 | queued stop answers through an expired `response_url` | F10: the card is the answer past 25 minutes (stated limit) |

Found by the models while writing draft 14 (not by review): an `aws unfreeze` landing just before a
rotation deploy left new keys deployed on an aborted job (F11: `abort` refused after `rotation_list`);
a stale deploy decided before another update could unthrottle a merge (F4: compare-and-swap change
sets); a second machine could run the rollback's local steps (F11: `commit` records the machine); an
item whose original actions ran after T and whose undo had not started was judged by its undo grant
alone (OD-670: any attempt after T on any grant).

Mediums and lows folded in (Maintenance and Steps): bound values per purpose; the step-up's identity
re-checked; people's admin-approver invariant re-applied; `ecf-worker` denied job writes; F5 reads
provisioned concurrency and `FROZEN_JOB`; `ContinueUpdateRollback`, `DescribeStackEvents` and
change-set rights; `destroy`'s scratch and secret deletes; one exemption list for F4 and the synth
test; mailbox secrets without a resource policy and `DeletedDate`; warm environments; 14-day queue
retention; prepare expiry from the last call; scratch deletes once ACTIVE; the first chunk's Scan by
cursor; rotation hashes recorded at write; the rollback order in one place; notice wording; the
"from before" marker against the first chunk; the gate guard storing references; Step 12 seeds by a
test-only role, per-seed expected statuses, the `slack-in` mapping only, the positive control's
method; F12's code-deploy and rollback-failed rows; `starter-injection` wording.

Draft 14 got its review in round 14.

### Phase R round 14 (8 Opus, draft 14 e03effa, 2026-11-05) and the draft-15 model attack

Ten high (CC1-CC10), all in what the models abstracted away; no fault in draft 14's shape. Draft 15
rebuilt the models first, seeded every counterexample back in, had four Opus agents attack the models
before writing (raw: `state-archive/m1/phase-r-raw/m15-*.md`), then wrote this text from the result.

| # | Round-14 finding | Landed |
|---|---|---|
| CC1 | "(a)" named two stack states; an abort after freeze (a) left mappings off | F4: three configurations, exact-reverse release; fault `oldrelease` |
| CC2 | a stale job released another job's freeze; stale `prepare` revived an expired job | F1/F4/F6: `Frozen` = job/config, owner and lock checked everywhere; faults `noowner`, `stalerevive` |
| CC3 | the countdown credited 2 periods across a freeze | F8: `FreezeSeq` at release (b), scheduled time; `delay_model2.py` |
| CC4 | `--abort` raced the local restore | F11: marker row in the service's transaction; faults `nolocallock`, `svc` |
| CC5 | the rollback order couldn't run | F11: restore clears the hold, dead-man handed to `keep_armed`, token before `commit` |
| CC6 | pruned and cross-install items re-offered | OD-673: retention-horizon refusal, start rule, I3 last; `map_model3.py` |
| CC7 | a killed `--replace` import lost live stops | OD-670 merge: one transaction per item, deletes last, idempotent |
| CC8 | ecf-jobs took `jobs.fifo` work while frozen | F6: batch-fail while `Frozen` has an owner |
| CC9 | Step 6's model link couldn't work | Step 6: pure shared functions, refinement test |
| CC10 | the release's key check needed the laptop's file | F0/F9: AWS's expected values via `status`; fault `unfreezekeys` |

Mediums and lows folded in: lost bound machine as a stated limit; the server-side wait and its stamp;
F7's abort as a terminal write; in-progress and rollback-failed rows; deploy steps inferred from
deployed parameters; the code deploy as the one new-template change set; begin/prepare exempt from the
export hold; F7 no longer waits for the export's scratch tables; the Scan's growth measured; Step 12's
rollback `executing` row; parent rows following their item; attempt attributes; the stuck-freeze alarm;
the round-9 drop rule for edits without `message_ts`; bound values per purpose; the hardware UUID;
`requested_by`; eval/private drafts 10 to 16 (OD-634 to OD-638).

Draft 15 got its review in round 15.

### Phase R round 15 (8 Opus, draft 15 4ff3587, 2026-11-05)

Seven high (DD1-DD7); identity, cost-design and performance found none. Raw:
`state-archive/m1/phase-r-raw/r15-*.md`.

| # | Round-15 finding | Landed |
|---|---|---|
| DD1 | a laptop lost before freeze (a) left a lock nobody could find or abort | F0/F1/F6: `status` returns the lock holder; `aws unfreeze` aborts it (fault `nolockholder`) |
| DD2 | the text's re-stamp of the wait (not in the model) stopped upgrades and rotations | F5: one stamp per job; the can-finish check (fault `restamp`) |
| DD3 | the text's I8 check on every change set refused the rotation's own deploy | I8/F4/F11: compare with `AWSCURRENT`, moved after `rotation_check` (fault `i8list`) |
| DD4 | `FreezeSeq` could repeat after a code deploy reset it | F8: release (b) sets the releasing job's ID (`delay_model3` scenario `aba`, fault `incr`) |
| DD5 | the stuck alarm watched an empty queue | F15: tick heartbeat absent 6 hours, always on |
| DD6 | a re-run after the local restore committed restored again | F11: `rollback_restored:<job>` (fault `norestoredrow`) |
| DD7 | the retention refusal used today's setting, checked only at `prepare` | OD-670: the watermark, re-checked at the merge (fault `curret`) |

Mediums and lows folded in: conditional transactions instead of a timing margin (fault `toctou`);
stops before `running` (fault `runningfirst`); QUIET refuses non-stop clicks; `UPDATE_ROLLBACK_COMPLETE`
stable; the settled `failed` row (fault `nosettled`); `merged_by` (fault `nomark`); parent rows and
people per kind; merges never write `IDENT#`; the move back through v1's own restore, reissue and
`set-tokens`; `--abort` through a service route; the lost or replaced machine keeps its data; ecf-jobs'
DescribeStacks in the boundary; runtime secret names with `install.id`, `ListSecrets` and `ListTables`
for `destroy`; the compare-and-swap fact a Step 8 pass; no `UpdateStack` for the deployer; the
hash-list cap before progress; `prepare` extends its expiry; the Scan only for kinds that read items;
mappings `DependsOn` the functions; SPEC's 14-day retention; stale text (OD-650 PITR trigger, `via:
stop`, the rotation-pending route, the voided pending record, the outage figure, `quiescent_at`, the
secret resource policies); Step 13 cites Maintenance's limits; F13 allows `cancel` and `reject`.

Draft 16 gets a full review on Opus (OD-577): text, models and `run_all3.sh` output together.

### Phase R round 16 (8 Opus, draft 16 75417c7, 2026-11-05)

Seven high (EE1-EE7), every lens with one or confirming one; four were places a model made one step of
several real writes. Raw: `state-archive/m1/phase-r-raw/r16-*.md`. Draft 17's models were rebuilt
first (`freeze_model4.py`, `delay_model4.py`, `map_model5.py`, `run_all4.py`; atomicity audit in
`draft17-atomicity.md`) and run one at a time.

| # | Round-16 finding | Landed |
|---|---|---|
| EE1 | the rotation's key move was two writes decided from the file; a kill between stranded a frozen stack | F0/F11: `AWSCURRENT` first, decided from `status`; `finish` needs it; the file follows (fault `filefirst`) |
| EE2 | v1's restore steps after its commit were lost on a crash, and the row would not survive `_settings` | F11: the `rollback_restore` table; the service's idempotent tail; `finish` waits for `complete` (fault `tail`) |
| EE3 | `--abort` aborted after the restore committed | F11: the CLI goes on only if the row is `aborted` (fault `abortalways`) |
| EE4 | the retention watermark had no writer in AWS, and TTL deletes during the merge | OD-670: the bound formula with the 24-hour cap, at `first` and each chunk (`retention_timelines`; faults `wmrun`, `firstonly`, `curret`) |
| EE5 | a failed release (b) restored the old `FreezeSeq` and a tick counted frozen time | F8: a tick on a frozen environment counts nothing (`run_failrelb`, fault `nofrozenskip`) |
| EE6 | the stuck-freeze alarm went through a frozen function | F15: the `-direct` email topic; Step 8 checks delivery while FROZEN |
| EE7 | runtime secret names repeat on a re-migrate while the old ones wait out a recovery window | F11/F12/F14: `ForceDeleteWithoutRecovery`; Step 12 re-migrate |

Mediums and lows folded in: F6 accepts any stable status; I8 checks the change set's own key and
seed-version parameters (fault `keyreset`); Step 8 pass criteria for what I1 and I8 rest on (one whole
environment per invocation; the staging label); the tick's conditional write (`run_overlap`, fault
`addwrite`); OD-680's table made complete, with a test for omissions; `ecf migrate run` uses the import
mapping (fault `migraterestore`); restore expires approved items that never started (fault
`restoredown`); merges don't touch cards; `--replace` deletes nothing; `merged_by` is the source key;
the FROZEN click text and the end notice; the "moved back" edits under `moved:` keys; the deployer
policy one statement per resource (seed `PutSecretValue`, list actions on `*`, no `PassRole`); the
release reads concurrency before release (b); the F0 marker row; PITR's restorable time and no
indexes on scratch restores; DynamoDB's transaction limits; the rotation-pending record removed; the
job ID not a capability; the identity rule per kind; import people present on the target live-wins;
`ecf slack reauthorize`; the dead-man orphan limit; M11's `RouteRef` and the QUIET refusal in the chat
seam; the stale `STOP#` clause and identity rows; the Cost table's 35th alarm metric.

Draft 17 gets a full review on Opus (OD-577): text, models and `run_all4.py` output together.

### Phase R round 17 (8 Opus, draft 17 5b8f147, 2026-11-06)

Seven high (FF1-FF7); the models were clean and `run_all4.py` re-ran identically. Raw:
`state-archive/m1/phase-r-raw/r17-*.md`. Draft 18's models were changed first (`freeze_model5.py`,
`delay_model5.py`, `map_model6.py`, `run_all5.py`; audit in `draft18-atomicity.md`) and run one at a
time.

| # | Round-17 finding | Landed |
|---|---|---|
| FF1 | a merge chunk could read past the 24-hour cap; the model's 1-hour grid hid it | OD-670: the cap removed with the bound (OD-698) |
| FF2 | leaving a terminal status kept `expires_at`, so TTL could delete an open item | Expiry: REMOVE on every terminal exit (fault `reopenkeep`) |
| FF3 | a deploy outside a job had no `Frozen` rule and could unfreeze mid-job | F4: keep `Frozen`, refuse when non-empty (`+foreign`, fault `foreignfrozen`) |
| FF4 | two restore requests could both commit; the model's laptop knew a request was pending | F11: commit only with no row (fault `entryonly`) |
| FF5 | no read showed whether this job's seed version existed; a label-based build strands a later rotation | F0/F11: decide by version ID; CAS move (`+rot2`, fault `labelpresence`) |
| FF6 | `set-tokens` was dropped, so local Socket Mode never reconnected | F11: `reauthorize`, then `set-tokens` |
| FF7 | the retention bound refused valid restores and imports and left items unstamped | OD-670: the skip rule (faults `oldrule`, `noauditcreate`, `skipnoaudit`, `nostamp`) |

Mediums and lows folded in: a late tick dates a new `seq` from its invocation (`schedseq`); the card
sweep's placeholder; the tick's re-card sweep; IAM for the age identity, the configuration token and
`migrate abort`'s secrets; the `-direct` policy's CloudWatch statement; the restore tail's failure
exit and F12 row; per-status "moved back" text; the queued-message count with the DLQ; the
`needs-you` row; the heartbeat after the skip; the 6-hour alarm in long freezes; modal submits in
QUIET; the dead-man renewal; Step 8 numbers for one whole environment, the alarm, the forced delete's
back-off and resolved change-set parameters; restatement faults listed honestly; the stale rotation
text; the migrate people rule; the attempts wording; the fresh step-up on a restore re-run; the
SHA-256 check; lock-holder adoption by kind; `--abort` on a non-bound machine; `continue-rollback`'s
stop; the boundary wording; the `install.id` parameter; the Cost totals.

Draft 18 gets a full review on Opus (OD-577): text, models and `run_all5.py` output together.

### Phase R round 18 (8 Opus, draft 18 c2013f7, 2026-11-06)

Four high (GG1-GG4); a measurement reviewer re-ran `run_all5.py` and matched all 68 lines. Raw:
`state-archive/m1/phase-r-raw/r18-*.md`. Draft 19's models were changed first (`freeze_model7.py`,
`delay_model6.py`, `map_model7.py`, the new `card_model.py`, `run_all6.py`; audit in
`draft19-atomicity.md`) and run one at a time.

| # | Round-18 finding | Landed |
|---|---|---|
| GG1 | a `migrate run` after `migrate abort` skipped every item: the clear kept `item.created`, and contradicted "only TTL deletes" | OD-670 and Moving from local mode: no skip for `migrate`; the clear named in Expiry's static test (`migrate_rerun`, fault `migrateskip`) |
| GG2 | the text ran the restore tail on an `aborted` row, so a rollback could end `done` with no local restore | F11: `aborted` runs nothing; `complete` only over `committed` (fault `abortedtail`) |
| GG3 | the retention re-stamp wrote unconditionally and could stamp an item an Undo had just reopened | Expiry: a conditional write (fault `restampuncond`); the model generates the job |
| GG4 | the tail's forced dead-man renewal blocked on a replaced bot token while F13 refused `set-tokens` | F11: no Slack call in the tail; F13 allows `set-tokens` and `reauthorize` once `committed` (fault `deadmantail`) |

Mediums folded in: one card-reissue mechanism, `needs_card` (F10, `card_model.py`); excerpts in their
item's transaction; every `ITEM#` write conditioned on not expired (`noexpcond`); the tail's seed
written only with a request's key text (`startseed`); adoption by kind and bound values; Lambda's
stale environment (I1's scheduled-time crediting a rule; Cancel not reading `Frozen`; `slackfrozen`;
Step 8 measures the window); a deploy outside a job checking its own change set's `Frozen`
(`foreignnorefuse`); queued escalation cards on a move back; carried `item.created` rows indexed
(`created_sid`); cross-install `stable_id` collisions; the merge reusing the bundle's `item.created`
row; the signed `ConfirmSubscription`; the static test over every delete path; the one-time identity
and configuration token on expiry and abort; `nocasmove`; counts reported by part; `norestoredrow`
measured by its own consequence; the table test against the models' source; restatements marked in
the runner. Lows folded in: `reauthorize` then `set-tokens` in the rollback summary; the local row
read through the service in the hold; "the time it entered the status"; the GSI consistency argument;
Secrets Manager's eventual consistency; the dead hooks; the `nonjob` wording; concurrent tail runners;
the merge's per-item condition; uncached `AWSCURRENT` in F0; F0's done-rollback row on another
machine; the failed-release-(b) exception in F8; the torn-read criterion; Step 9 and Step 12 counts;
the SPEC wording of OD-693 and OD-700. Lows not taken (for round 19's view): a composite stuck alarm,
TCP keepalive tuning, Step 8's merge throughput mix, v1's 600 s restore-request timeout, two macOS
accounts sharing a bound machine, the generation as `migrate_rollback`'s bound value, a symmetric I2
check for job 2.

Draft 19 gets a full review on Opus (OD-577): text, models and `run_all6.py` output together.

### Phase R round 19 (8 Opus, draft 19 2025af9, 2026-11-06)

One high (HH1); the measurement reviewer re-ran 76 of `run_all6.py`'s 78 lines identically. Raw:
`state-archive/m1/phase-r-raw/r19-*.md`. Draft 20 removed mechanisms instead of patching them, and its
models were changed first (`freeze_model7.py`, `delay_model7.py`, `map_model8.py`, `card_model2.py`,
`run_all7.py`; audit in `draft20-atomicity.md`).

| # | Round-19 finding | Landed |
|---|---|---|
| HH1 | adoption by key-text fingerprints stranded a rotation whose laptop was lost after the seed write (the rule was prose, "not modelled") | F0: per-kind adoption; a rotation adopted by kind and version ID once its seed exists (fault `adoptbound`); OD-731's working rule |

Mediums folded in: the migrate clear removed (`migrate abort` destroys the install; its authority, its
resumability, the audit-row conflict and the one-time identity's lifetime go with it; `keepaudit`);
expiry as v1's daily sweep (no re-stamp, no stamp at any write, the setting version checked;
`sweepuncond`, `sweepnoset`), `attribute_exists` on every update (`noexpcond`, `noexists`), children
never expire; the tick reads the stack (`envtick`; the oldest-configuration adversary for the creator and
Cancel); cards: the migrating hold (`nohold`), the mark removed in the card's own transaction (`twotx`),
v1's real expiry in the model (the leaked mark needed the old model's grantless expiry: `requestkeep`
masked), one `CARD#` row with `replaces_ts`, bounded resends, the two give-ups, question and "Needs you"
cards on a move back, escalations reset by their own card key; `status` returns bound values; the
re-ask's step-up and the tail's bundle-hash check; `ecf-worker`'s excerpt and DescribeStacks rights;
allow-listed deletes, put-only `BatchWriteItem`, the Service-Reference-driven static test (StartBulkPutItems
has no condition keys); the CLI-owned alarm subscription and `doctor --fix`; grants scoped to `GRANT#`;
`job/*` rights and cleanup; install names without a dash; cross-install collisions as cost and
migration proposed; the measurement reviewer's honest counts and the four restatements marked;
`deadmantail` split; `abortedtail` credited by its consequence; the own-`Frozen` check as defence in
depth; the models' own CI workflow. Lows folded in: stale lines (two grant paths, the old card's
record, the tick's sweep, "only TTL deletes", "in the digest"); the start-up tail for a `committed` row;
`abort_local`'s actor and notice; the ForceDelete condition key verified; `slack/config` checked at
`finalize`; Step 6's sample size and seed, Step 12's pause hook; the stale window measured from a named
event. Lows not taken (for round 20's view): a composite stuck alarm, TCP keepalive, Step 8's merge mix,
v1's 600 s restore timeout, two macOS accounts on one bound machine, a symmetric I2 check for job 2
(Step 6 tests job 2's stops), born-expired writes (moot: no write stamps).

Draft 20 gets a full review on Opus (OD-577): text, models and `run_all7.py` output together.

### Phase R round 20 (8 Opus, draft 20 175a3b9, 2026-11-06)

Two highs (II1, II2); the measurement reviewer re-ran 81 of `run_all7.py`'s 83 lines identically (the
`rollback` and `upgrade+foreign` base kinds copied). Raw: `state-archive/m1/phase-r-raw/r20-*.md`. The
brief's process check counted macOS system processes, so the cost and IAM reviewers ran few models;
the others' findings carry model counts. Draft 21's models were changed first (`freeze_model8.py`,
`delay_model8.py`, `map_model9.py`, `card_model3.py`, `run_all8.py`, `check_refs.py`; audit in
`draft21-atomicity.md`).

| # | Round-20 finding | Landed |
|---|---|---|
| II1 | a rotation adopted after its seed write can't build the deploy (the change set needs the new public keys; the seed holds fingerprints; `status` returns only `AWSCURRENT`'s): a lost laptop strands it outside the stated limit | F0, F11, F12: a rotation only on its key text's machine; the stated limit covers the whole rotation (fault `limitseed`; `adoptseed` masked); finishing from another computer is an M2 task (OD-732, OD-733) |
| II2 | children kept after their item (OD-727) are exported without it; v1's restore has foreign keys on, so the move back, the lost-laptop restore and `export --pull` fail | Expiry: the item lists its records and `children_n`; the sweep hides them together (faults `childkeep`, `childuncond`, `nochildver`, `createstrict`, `childstamp_early`; OD-734) |

Mediums folded in: the re-ask by key fingerprint, no bundle (`bundleask`); the local hold counts no
delay (`holdcounts`); the tick's read gates only the countdown, the stable-status rule removed, the
read's lag a Step 8 pass line (`dslate`, `failopen`), two tick metrics, the boundary generated from
the role table (it had DescribeStacks for ecf-jobs only); no runtime `BatchWriteItem` on the main
table (`auditdel`), the delay fields on the item, no excerpt expiry rights outside ecf-jobs, Step 0's
"deletes" column, `migrate abort`'s steps (deletion protection, PITR's system backup, the versioned
bucket, the audit copy, the step-up and lock, early local resume, the chunk lease); the sweep batched
with a lease and a pass line (`batchnocond`), `retention set` retries; cards: the post's mark
conditioned on the rendered grant (`stampmark`, `nogrant`), every death ends the row (`noother`), the
re-queue once by `requeued` (`requeueall`), escalations reset by their posting card's key whatever the
item's status (`headkey`, `openonly`), every question card re-posted (`qts`), no per-item "Needs you"
card, the card index sparse; settings before items modelled (`itemsfirst`) and the lowering notice; the
migrate resume's skip rule (`rerunnoskip`); symmetric I2 (`i2skip2`); `check_refs.py` by syntax tree,
with a self-test; stated-limit counts and file hashes in the runner; the biggest scopes on the macOS
gate; honest counts (the skip rule's reads); the alarm email on a scanning mailbox a stated limit.
Lows folded in: stale lines (a merge writing expiry, the excerpt's expiry, the `aws-keys.json`
contents, the dedup ID, the Step 8 environment pass, the 60 s caveat, "not reached" with a pause hook);
the "replaced" edit's channel and a gone message dropped; DescribeStacks scoped to the stack; job/*
rights in the boundary; `ConditionCheckItem` on settings for ecf-jobs; the key-text mismatch answer.
Lows not taken (for round 21's view): M11 field names on `CARD#` (`message_ref`); v1's Claude backlog
gate on the card tick; the per-tick cap for old-mail grants; the import base kind kept; CloudTrail on
link-based SNS unsubscribes (Step 8 records it).

Draft 21 gets a full review on Opus (OD-577): text, models and `run_all8.py` output together.

### Phase R round 21 (8 Opus, draft 21 38d018a, 2026-11-06)

Two highs (JJ1, JJ2); the measurement reviewer re-ran 105 of `run_all8.py`'s lines identically (the
`rollback`, `upgrade+foreign` and `rotation+rot2` base kinds and two faults on the last copied). Raw:
`state-archive/m1/phase-r-raw/r21-*.md`. The brief's corrected process check still matched another
session's tests, so the cost reviewer ran no models; corrected mid-round. Draft 22's models were
changed first (`freeze_model9.py`, `delay_model9.py`, `map_model10.py`, `card_model4.py`,
`run_all9.py`, `check_refs.py` v3; audit in `draft22-atomicity.md`).

| # | Round-21 finding | Landed |
|---|---|---|
| JJ1 | the rollback bundle (1-day `exports/` object) can expire before the local restore commits: no rule pulled it before `commit`, so a killed or next-day run can never finish (fm8 with the event: 351 can't finish) | F0, F11: pull, verify and keep before `commit`; an expired object exported again (`reexport`; fault `pullaftercommit`; OD-741) |
| JJ2 | Step 8's DescribeStacks pass line counts the lag from an update's completion, the model from its start: counted from completion, a 30 s lag breaks I1 when a whole freeze-to-release cycle is shorter (90 of 12.4M timelines); the model's coarse grid hid it | F8, Step 8: freeze (b) only 45 s after freeze (a) completes; both anchors and a 1 s grid generated (faults `nowait`, `dslate` at 90 s; OD-742) |

Mediums folded in: the key-only re-ask's own route (the row read before any bundle, a step-up bound to
the job and the row's hash; `bundleask`); IAM claims cut to what IAM enforces (a TTL delete through
`expires_at`; Denies on audit rows; one boundary per role under 6,144 characters); the deployer's S3
delete right removed (`logs/` erasable unseen); `migrate abort` stops the writers, copies, compares with
`logs/` and records before deleting (`abortcopyfirst`, `copydbonly`), its own step-up purpose, scratch
tables; the rotation's freeze-time export for the lost-machine limit (`nofreezeexport`,
`nostopapply`); other machines' key files confirmed by the typed fingerprint (`noconfirm`; per-job key
holder in the model, which also corrects `+rot2`'s stated-limit count); the F12 row's other-machine
re-run removed; the exporter's filter (`exportnofilter`); a fixed set of item records (`CHECK#` and
`children_n` removed; merges list theirs, `mergenochild`); the sweep's retry rules (`dropsetcheck`,
`retryall`), its lease removed, its batch size and pass line stated; cards: scan replies queued at a
move back reported again (`scanreset`), the unknown-code give-up re-queued once (`otherterminal`), a
list of cards to mark "replaced" (`overwrite`), the move back's edits for open items only and in their
own FIFO group, two reissue conditions removed (no fault failed without them); checks: `check_refs.py`
v3 (counts by label, tuple MUT names, output hashes and result, file names, the restatement list),
part C, the sweep and the local hold modelled instead of restated, the masked faults' stated-limit
counts, `--ci`, the gate's trigger equal to CI's; the local hold's lift resets the tick clock (`nolift`,
found while modelling: the round-20 rule as written still counted part of the hold).
Lows folded in: stale Step 6 lines (the stable-status rule, child rows), the dedup ID exception, the
card record's states, the "Needs you" hash, resends under a channel hold, the redrive death,
`requeued` on a new grant, `UpdateTimeToLive` watched, `logs/` create-only by bucket policy, cfn-exec's
abort rights, `import` dropped from the runner, the rollback scope's memory, `adoptseed` relabelled.
Lows not taken (for round 22's view): M11 field names on `CARD#`; v1's Claude backlog gate on the card
tick; a sparse due index for the sweep (the full Scan stays, timed in Step 8); a separate TTL attribute
(`ttl_at`) only ecf-jobs may name; re-announcing sends whose delay ran out while paused (I1 holds).

Draft 22 gets a full review on Opus (OD-577): text, models and `run_all9.py` output together.

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
