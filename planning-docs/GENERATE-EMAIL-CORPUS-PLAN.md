# Plan: `ecf corpus`: real-mail test corpus (fetch, replay, eval)

Draft 3 (2026-10-06). It folds in two adversarial reviews, recorded in
`state-archive/corpus/corpus-review-findings.md` (gitignored):
- Phase R: findings R1-R44.
- Phase R2: findings R45-R96.
Tags like [R48] point to them.

## Context

Evaluation today uses only the committed synthetic set (185 cards). The operator wants to test ecf on real mail, but
SPEC forbids it in three places:
- §12.4: full messages are never written to disk (OD-108/109).
- §17.3: test mailboxes receive synthetic mail only.
- §16.6: end-to-end runs use synthetic mail only.

Operator decisions (2026-10-06):
- **§12.4 exception.** A corpus may exist on disk only **encrypted**.
  - It is made only from a mailbox **the operator owns** [R45].
  - It may be made on any prod or test install, behind Touch ID step-up and a Security Notice.
  - It is never made on an `ecf-server dev` service [R56].
- **Gating** [R10]: the corpus gates `v1.0.0` only through the decision-model experiment
  (`planning-docs/SYSTEMONE-MODEL-TESTING-PLAN.md`); that plan's adopt or not-adopt OD is the §1.5 criterion.
  - Phases 0, 1 and B are on the path to rc1. Phase A (replay on `ecf-server dev`) is outside the gate.
  - The work is built on a `corpus` branch from `v1.0.0-release` and merged back before rc1.
- **Report only** [R8]: corpus results are reported and never gated. The absolute safety gates stay on the
  synthetic set.
  - The corpus measures category and sender-type accuracy, calibration and real-mail noise. It does not measure
    fraud recall.
- **No Anthropic** [R3]: in v1, `--corpus` with `--claude` is refused, and so is `/ecf-eval` on a corpus.
- **Blind labels** [R6]: labelling shows no model output and happens before any replay or corpus run.
- **Sleep:** fixed seconds between batches, default 10.
- **Credentials:** `--address` (an address ecf already watches; Keychain), or a one-off prompt (never stored).
- Each phase starts when the operator names it. This plan is a working document; SPEC stays authoritative.

**Division of work with the systemone plan** [R69]:
- This plan owns the corpus commands, the label UI, the corpus case source, `merge`, `rescore`, and making
  `compare` count confirmed labels only.
- The systemone plan owns the NI interval, Holm, its metrics, the backend seam, the gating corpus's mailbox and N,
  and the adjudication procedure.

## Facts this design relies on

- **Gmail IMAP limits**, verified 2026-10-06 (knowledge.workspace.google.com/admin/gmail/gmail-bandwidth-limits):
  - "Download with IMAP: 2500 MB per day".
  - "A suspension typically lasts for 1 hour, but can last up to 24 hours."
  - These apply to all Workspace editions. The limit for personal accounts is unverified (§12.2, §21.2).
  - At most 15 IMAP connections. The corpus job adds a second session, so ≤ 2 per address [R38].
- **Purelymail:** no bandwidth or connection limit is recorded (unverified).
  - A refused connection is retryable [R38].
  - UID SEARCH can return expunged UIDs (OD-450) [R78].
- **Existing guards:**
  - Gmail budget: 2,500,000,000 bytes per rolling 24 h per address (`download_budget.py`, table `downloads`).
  - Live fetch downloads one body per FETCH.
  - `fetch.LARGE_BYTES` is 16 MiB.
- **Code facts from the reviews:**
  - `ImapSource` always selects INBOX [R11].
  - No append layer takes a date; imapclient has `append(..., msg_time=)` [R13].
  - `evalrun` computes facts in `ruletest.Scratch` with `_no_dns` and an empty sender history [R1].
  - `evalrun.latest` has no set filter [R2].
  - `summarize` sets `gate_passed` [R54].
  - `senderauth.evaluate` needs a `ParsedMessage`. Fetch parses only in an isolated child (OD-204), and `DnsCache`
    has one 30 s budget per instance [R47].
  - `imaplib._MAXLINE` is 1,000,000 bytes [R48].
  - A dev service uses `FakeStepper` (`service.py:611`) [R56].
  - `ipaddress('::ffff:127.0.0.1').is_loopback` is True [R61].
  - `passphrase.check` accepts any five distinct words [R17].
  - pyrage works on whole buffers [R20].

## Command surface (CLI token only; MCP gets nothing)

```
ecf corpus fetch --out PATH [--total 500] [--chunk 10] [--sleep 10] [--max-bytes 256MiB]
                 [--order most-recent|random|oldest] [--folder ROLE|NAME]
                 [--include-own] [--allow-spam] [--own-passphrase]
                 [--address ADDR | (prompts: email, IMAP host, app password)]
ecf corpus status | stop
ecf corpus info FILE                      # clear header + labels count; no passphrase [R39]
ecf corpus merge A B [...] --out C        # top-up: one new corpus [R52]
ecf corpus replay FILE --user U --port P [--host 127.0.0.1] [--fresh-ids]   # Phase A, ecf-server dev only
ecf eval label --corpus FILE              # Phase B, blind authoring UI
ecf eval run --corpus FILE --classifier … --actor …   # Phase B, preset A only
ecf eval rescore RESULT --corpus FILE     # after a labels change [R53]
```

Every `ecf corpus` subcommand, and `eval label/run/rescore --corpus`, calls `require_terminal()` first [R57]. The
CLI says the passphrase belongs in a password manager only, never in a state, plan or scratchpad file.

**Default order** is `most-recent` [R62].

**Folder** [R26, R37, R73]:
- `--folder` takes a role (`inbox`, `all-mail`) or a name.
- On Gmail the default is the folder with the `\All` role. If All Mail is hidden from IMAP, fetch refuses with the
  Gmail "Show in IMAP" hint and never falls back. Elsewhere the default is INBOX.
- The folder is resolved to its exact name at preflight and validated against the folder list. The step-up binds
  the resolved name.
- `\Trash` and `\Junk` folders are refused without `--allow-spam`.
- If INBOX is chosen on Gmail, the preflight prints the `gmail_inbox_counts` warning (OD-440).

**Own mail** [R22, R63]:
- Messages with Gmail `\Sent` or `\Draft` labels are skipped and counted (`skipped_own`).
- So are messages whose `X-ECF-Install` header shows in the lean meta.
- `--include-own` keeps them.

**Gmail mode** comes from `capabilities().gmail` after login (OD-438), never from the host [R42].

## Limits (recorded in OD-462)

- **`--total`:** default 500, maximum 5,000.
  - Phase B's loader has its own limit, independent of `ruletest.MAX_CASES` [R29].
  - Power: n=500 at 20% discordance gives a paired SE of about 2 points. The gating N is set by systemone's OD-465
    from measured discordance [R8].
- **`--max-bytes`:** default 256 MiB, maximum 512 MiB, of message bytes [R20].
- **Gmail budget share** [R24, R64]:
  - At preflight, S = floor(L0/2), where L0 is `left()` at that moment. S is fixed and shown.
  - Before each message, the run stops if `corpus_bytes + next > min(max-bytes, S)`, or if `next > left()` now
    (live fetch may have spent the rest).
  - Bytes are recorded per message.
  - The preflight warns that live checks may stop on Gmail's budget until a stated time.
  - Off Gmail, `left()` is None and only `--max-bytes` applies.
- **`--chunk`:** default 10, range 1-100. Each body is still its own `BODY.PEEK[]` FETCH.
- **`--sleep`:** default 10 s, range 0-600 [R41]. It is politeness and a pause point for `stop`; the byte caps are
  what keep the account safe.
- **Per message:** after 2 timeouts on one UID, it is skipped as `skipped_timeout` [R77].
- **Preflight output:** candidate count, batch count, total bytes, byte cap, and estimated duration. At the
  defaults that is 50 batches × 10 s, about 8 min plus fetch time.

## Selection [R12, R36, R40, R48, R62, R78]

- **Never `UID SEARCH ALL`.** Candidates come from bounded `UID SEARCH UID a:b` windows, each sized so the response
  stays well under imaplib's 1 MB line limit.
  - `most-recent`: windows walked down from `UIDNEXT`.
  - `oldest`: windows walked up from 1.
  - `random`: windows at random offsets in `[1, UIDNEXT)`, sampled with `secrets.SystemRandom`;
    `k = min(k, len(pool))`.
- **Lean meta** on each candidate batch:
  - always `RFC822.SIZE INTERNALDATE BODY.PEEK[HEADER.FIELDS (X-ECF-Install)]` (the response key is confirmed in
    Phase 1);
  - plus `X-GM-LABELS X-GM-MSGID` on Gmail.
- **Skips:** candidates are dropped and counted when they are over `LARGE_BYTES`, own mail, gone (no meta row,
  `skipped_gone`), or exact duplicates (same label key, `skipped_duplicate`, [R51]).
- **Iterative:** selection tops up from the next window or a fresh sample until N survivors are found or the folder
  is exhausted. Exhaustion is `complete: true`, `count < total`, reason `folder_exhausted`.
- `most-recent` and `oldest` order by INTERNALDATE within the UID windows. The order differs from true date order
  only for APPENDed or imported mail (a stated limit). The corpus never covers the large-message path.
- The folder is opened with EXAMINE, and `BODY.PEEK` sets no `\Seen`.

## Facts at fetch time [R1, R47, R49, R65]

- Each fetched message is analysed in **`isolate.subprocess_isolator`** (OD-204), with a **fresh DNS budget per
  message** (number in OD-462).
- On an isolation error or timeout the row gets `facts: null` and `skipped_facts += 1`. There is no retry and no
  `processing` row.
- `dns_cache` rows (names only) are written to the install database, as live fetch writes them.
- **Recorded per row:**
  - `senderauth` result and aligned domain;
  - DKIM outcome;
  - whether the provider's `Authentication-Results` header is present;
  - `fetched_at`.
- **Recorded with `--address`:** the whole `senders` row for the sender, keyed by `facts.sender_hash`:
  `dmarc_pass_count`, `first_pass_at`, `last_pass_at`, `confirmed_category`, `verified_rule1a`, `payment_history`,
  `expected_reply_to_domain`. Plus, once per corpus, the install's `known_vendor_domains` and its watched providers.
- **Clear header:** a histogram of `none` reasons, so DNS drift on old mail is visible.
- **Stated limits (§16.1):**
  - History is the install's state at fetch time, not at each message's arrival.
  - Auth uses today's DNS, so old mail can lose DKIM keys.
  - These stay synthetic: trigger 5 reuse, `ecf_mail`, `alert_echo`, vendor-list drift.
  - One-off sources have no history.

## Security model

- **Owner:** the fetch confirmation and the Touch ID dialog say "from a mailbox you own" [R45].
- **Two-call flow** [R92]:
  - `POST /v1/corpus/preflight`: login, capabilities, list folders, EXAMINE, candidate pass. It returns the
    summary and the step-up target. It does not use `login_and_probe`: no read-write SELECT, no SMTP [R72].
  - `POST /v1/corpus/fetch` repeats the same inputs with the nonce.
  - The service never caches a one-off password or a candidate list across requests.
  - `_check_password` validates the typed password.
- **Step-up `corpus_fetch`:**
  - Bound to `{source email, resolved folder, host, port, out path, total, max-bytes, order}`. Never the password
    [R18].
  - Dialog text order: count, source, host, then path (head…tail if long). The CLI prints the full path before the
    step-up line [R95].
  - Every input is collected before the first step-up action [R33].
  - **Refused on an `ecf-server dev` service** (`state.dev is not None`), where step-up is a no-op [R56].
- **Replay controls** (dev only) [R56]:
  - The dev service's step-up is a fake, so replay's real controls are:
    - the passphrase;
    - the IP-literal loopback target;
    - tmpfs Dovecot with `--rm`;
    - a RAM-disk `--home`.
  - The plan says so plainly.
  - Residual: `ecf-server dev --home <prod root>` runs a dev service over a stopped prod install. Stated in §12.2.
- **Authorized administrator:** in v1 the OS user is the sole admin (OD-007, OD-013).
  - Routes are `@allow(Caller.CLI)`, so MCP and agent callers are refused.
  - `ecf claude` cannot run `ecf corpus`, because Bash is denied there [R35].
  - M2 notes: corpus commands will need the admin role.
- **Credentials:**
  - With `--address`, the Keychain password is read through the `checks.py` closure.
  - A one-off password goes through `prompts.hidden()` and over the socket. The service builds
    `imap_factory(host, email, lambda: pw)`.
  - Never argv, env or disk.
  - A typed email matching a watched address after `internal.fold` is refused with "use --address <id>" [R23].
- **Passphrase** [R17]:
  - The service generates the six-word passphrase by default (`passphrase.generate`). The CLI shows it once, only
    on a terminal [R57].
  - `--own-passphrase` requires ≥ 5 words of ≥ 3 chars and ≥ 24 chars.
  - Decrypt time is measured in the real-service test and recorded in SPEC.
- **Output path** [R32, R94]:
  - Checked as in `manual_export.check_path`, plus: suffix `.ecfcorpus`, not in the data folder, not a symlink.
  - Refused inside a git work tree. The walk looks for `.git` as a directory or a file.
  - Refused in `~/Library/Mobile Documents`.
  - Warned on other sync folders, and on `~/Desktop` and `~/Documents` when iCloud Desktop & Documents sync is on
    (detection unverified until the real-service test).
  - Written with `export_bundle.write_atomic` (0600, O_EXCL). Its `.partial` temp file is the encrypted output;
    plaintext never touches disk [R76].
- **`.gitignore`:** `*.ecfcorpus`, `*.ecfcorpus.labels.jsonl`, `.*.ecfcorpus.partial` [R90].
- **Memory** [R20, R46]:
  - **Fetch:** the tar.gz is streamed (`tarfile` `w|gz` into a `BytesIO`), and each raw message is dropped after
    it is added. Buffers are deleted after use. The job stores only exception type names and clears tracebacks.
  - **`CorpusSession`** (label, run, rescore, replay):
    - The service decrypts once and keeps only the compressed tar. It reads members per case.
    - The passphrase is dropped right after decrypt.
    - One session at a time (`ConflictError`). It is held only while the command's request or run is open, with a
      15-minute idle timeout, and released on `stop`.
    - Routes are CLI-only and keyed by a session id.
  - **Measurement:** peaks are measured with `footprint` for fetch at 512 MiB and for a session, and stated in
    §12.2.
  - **Doctor:** checks encrypted swap (`sysctl vm.swapusage`) and that core dumps are off; both are stated as
    assumptions in §12.2.
- **Notice and audit:**
  - The Security Notice names the path.
  - The audit event `corpus.fetched` stores counts, bytes, order, `corpus_id` and the file sha256 [R31].
  - It identifies the source by `address_id`, or for a one-off by `sha256(internal.fold(email))[:12]` plus the
    domain, never the email [R58].
  - The nonce `target` keeps the full JSON until retention, a stated residual in §9.6 [R89].
  - Logs follow OD-165.
- **Labelling display** [R21, R66]:
  - Above the excerpt (about 1,500 chars, "more" on request), it shows:
    - From, Reply-To, To, Subject, Date;
    - the manifest auth result;
    - attachment names (capped).
  - Everything goes through `text.plain` and `triggers.redact_injection`.
  - The CLI tells the operator to label in Terminal, not in a Claude session.
- **`ecf destroy`** reminds that `.ecfcorpus` files and their labels files are kept [R93].
- **`corpus_downloads`** is listed in `export_bundle.EXCLUDED` and pruned like `downloads` [R89].

## Corpus format

- **Clear header** [R39, R91]:
  - format version, `corpus_id` (uuid), created_at, ecf version;
  - source domain, folder role, order, count, bytes;
  - skipped counts (large, own, gone, duplicate, timeout, facts);
  - `complete` and the reason;
  - `source_preset`;
  - the `none`-reason histogram;
  - for a merge, the source corpus ids.
- **Encrypted payload:** a streamed `tar.gz` of `NNNNN.eml` (raw bytes), `manifest.jsonl` and `profile.json`,
  age-encrypted.
- **Header integrity:** the manifest carries `sha256(clear header)`, checked after decrypt; a mismatch is refused.
- **Manifest row:**
  - index, uid, uidvalidity, folder, internaldate, size;
  - sha256 (integrity);
  - **label key = `content_hash` + `identity_digest`** (§6.3, `message.py`) [R51];
  - Message-ID;
  - `X-GM-MSGID` and labels on Gmail;
  - the facts above.
- **`profile.json`:** email, provider, sensitivity, org_domains, org_addresses [R49].
- **Labels file `<corpus>.labels.jsonl`** [R28, R86, R90]:
  - 0600, written from the CLI with O_EXCL then rename.
  - Rows sorted by key, with no per-save timestamps, so the hash is deterministic.
  - Row schema: label key, `corpus_id`, the schema fields, `s`/`u` mark, `confirmed`, date.
  - Nothing derived from text.
  - A `content_hash` version bump orphans the file (stated).

## Phase R: review of draft 1 (done 2026-10-06)

Three Fable reviewers found 44 findings. The operator decided each one; this draft carries the fixes.

## Phase R2: review of draft 2 (done 2026-10-06)

Three Fable reviewers found 52 findings: 11 high, 16 medium, 25 low. The operator accepted all of them as
recommended, and this draft carries the fixes.

## Phase R3: review of draft 3 (before any code or SPEC commit)

Draft 3 adds new design: `CorpusSession`, `merge`, `rescore`, the preflight route, `--imap-cafile` for dev, and the
facts isolation. It also changes selection. So under the gate rule it is reviewed again (operator request
2026-10-06).
- **Reviewers:** the same three areas, read-only, `model: "fable"`. Each also checks that every accepted R1-R96 fix
  is present and correct.
- **Output:** findings numbered from R97 in the findings file.
- **Gate:**
  - Every critical and high finding is fixed in the next draft (the operator confirms) or rejected by the operator
    with a recorded reason.
  - Medium and low findings go to the operator.
  - A material design change triggers another round.
- Only then Phase 0, and then code, each started when the operator names it.

## Phase 0: decision and SPEC (first commit, after operator OK)

- **OD-461:** the §12.4 exception.
  - Encrypted corpus files, from operator-owned mailboxes only, on prod or test installs (never dev).
  - Step-up and a Security Notice.
  - Phase A excerpts exist only in a RAM-disk dev home that is detached after the run.
  - No Anthropic in v1.
- **OD-462:** limits, defaults, selection, the budget share and its email-keyed table, the per-message DNS budget,
  and the power statement.
- **OD-463:** replay goes only to loopback Dovecot (IP literal) from `ecf-server dev`.
  - Dovecot's whole mail home is on tmpfs, and the container runs with `--rm`.
  - The dev `--home` is on a RAM disk.
  - `--imap-cafile` exists on dev only.
- **ADR 0022** "Real-mail test corpus".
- **SPEC edits:**
  - §12.4: the exception. A corpus is a new place content is stored and read by the operator; the destinations
    are unchanged.
  - §12.2: memory figures, the encrypted-swap and core-dump assumptions, the ssh-forward and `--home` residuals,
    and the Colima VM swap residual.
  - §17.3 and §16.6: replay from a dev service only.
  - §16.1: the real-mail set, which covers:
    - blind labels and the label key;
    - stored fields (labels, hashes, closed-vocabulary model fields; never the actor's `reason`) [R96];
    - the facts limits above.
  - §14.3: the corpus counts against the Gmail budget.
  - §9.6: the step-up list and the nonce residual.
  - §10.2: the commands.
  - §23.4: the OD rows.
  - §21.1: two rows [R55]:
    - the fetch/label/run mechanics test (gating);
    - the replay test, marked "after v1.0.0, not a release criterion".
  - §1.5: nothing corpus-specific.
- **docs/gmail-setup.md:** a corpus is made only from your own mailbox.
- **Rules:** `.claude/rules/eval-synthetic.md` and `GENERATE-FAKE-TESTING-EMAILS.md` say corpus content never
  feeds cards.
- **Systemone plan** (its session makes these edits against this final text, operator OK):
  - its line 296 becomes "Phases 0, 1 and B";
  - OD numbering: OD-466 is already absent, so renumber OD-467 or state the gap [R81];
  - OD-465 names the gating mailbox and requires `--address` on an install with history [R67];
  - its label wording matches the display above [R66];
  - its adjudication uses `ecf eval rescore` [R53].
- **CHANGELOG**, one line per change [R30, R84]:
  - `ecf corpus fetch/status/stop/info/merge`;
  - the §12.4 exception;
  - Gmail budget sharing;
  - `ecf eval label/run/rescore --corpus`;
  - `ecf eval compare` counts confirmed labels only (synthetic too);
  - `ecf corpus replay` and `ecf-server dev --imap-cafile` (dev).

## Phase 1: `corpus fetch` (service job)

- **New `ecf_server/corpus.py`:**
  - A background job modelled on `models.start_install`: a `Progress` with a lock, `ConflictError`, an injectable
    `spawn`, a `stop` Event, and a `clock`.
  - Steps: preflight → step-up → login → EXAMINE → selection → batches.
  - Per message: FETCH, then record the budget **at once** (as `fetch.py:429`), then facts in the isolator, then add
    to the streamed tar.
  - Then encrypt, write, notice, audit.
- **Read-only corpus reader** over `lib.Conn` [R11, R80]:
  - It EXAMINEs the folder and records UIDVALIDITY and UIDNEXT.
  - Reconnect: a fresh `Conn`, re-login through the password callable, re-read capabilities, re-EXAMINE, compare
    UIDVALIDITY. It uses the same exception set as `ImapSource`.
  - `ImapSource` stays INBOX-only.
- **Interruptions** [R14, R25]:
  - Retry with backoff (5 tries over 5-60 s), then resume at the next unfetched UID.
  - A UIDVALIDITY change, `stop`, or an unrecoverable error writes a partial corpus (`complete: false`, with the
    reason) when ≥ 1 message was fetched.
  - A service restart discards the run.
  - The CLI says long runs need AC power and sleep prevented.
- **`download_budget.py`:**
  - The address ID is used with `--address`.
  - One-off sources use the new table `corpus_downloads (email_norm, hour, bytes)` (migration, no FK,
    `internal.fold`).
  - New `used_email`/`left_email` sum it with `downloads` of any address whose folded email matches.
- **Routes:** `POST /v1/corpus/preflight`, `POST /v1/corpus/fetch`, `GET /v1/corpus`, `POST /v1/corpus/stop`,
  `POST /v1/corpus/merge`, all `@allow(Caller.CLI)`. Progress is polled as in `cli_models.py`.
- **New `ecf/cli_corpus.py`:** `make_corpus_app(paths)`, registered next to `models` in `ecf/cli.py`.
- **`corpus info`** reads the clear header and the labels-file count only.
- **`corpus merge`:**
  - Decrypts each source (one passphrase each) and writes a new corpus with a new id, a new passphrase, and one
    step-up.
  - The union is de-duplicated by label key and re-indexed. Source ids go in the header and the folder in each row.
  - Labels files are merged by key.
  - A top-up before labelling costs nothing. One after the G run forces a re-run.

## Phase B: blind labelling, eval runner, rescore (gates `v1.0.0` via systemone)

**`ecf eval label --corpus FILE`** [R5, R6, R68, R71]:
- Uses a `CorpusSession`. Messages are shown in random order with the display above.
- It never loads a result file, including the synthetic `eval label` default, `results.latest` (tested).
- It is an authoring UI. Each schema field gets a prompt with the closed vocabulary from `schema_v1.yaml`.
- `s` (skip) and `u` (unsure) apply per message and are excluded from scoring and counted.
- Labelling is resumable from the labels file.
- **Pre-fill:** only facts that are not label fields (auth result, attachments, history). Keyword hits are shown as
  information with no default answer, and the pre-fill acceptance rate is recorded.
- **Confirmed** means every field was authored (no s or u) and the stored label key matches the decrypted message.
- Author is `operator`.
- **Time:** 1-2 min a message, so 500 messages is about 8-17 h. Label 500 first. A top-up is optional and never
  blocks rc1. The hours go into the v1.0.0 plan, refined after the 20-message test [R88].

**Expected values** [R50, R87]:
- A service function `corpus.expected(row, profile, labels)` runs the manifest-built Scratch variant plus the
  starter rules, with the labels as the classification (the `ruletest._outcome` path). It gives `rule` and actions.
- Safety is derived as follows:
  - `must_escalate` = the derived rule is `fraud_guard` or `regulatory`, or the actions include escalate;
  - `must_not_hide` = fraud_risk ≥ medium, or payment_related, or impersonates_internal;
  - `injection_target` is never set (a stated limit).
- Rule and safety are derived only when every field the policy reads is labelled.
- Rule accuracy is reported with and without a history snapshot. "Facts exist" means the snapshot is present.

**Scratch variant** [R1, R49]:
- It inserts the snapshot `senders` rows, the vendor and provider sets, and `profile.json`.
- It uses the recorded auth and DKIM outcome instead of `_no_dns`, passes `gmail_labels` to `analyze` (so
  `self_sent` works), and uses the profile's sensitivity.
- It bypasses `ruletest.PROFILES`.
- Corpus cases build `evalrun.Case` directly, with `id` = `NNNNN` and author `operator`. `cards.py` is unchanged
  [R82].

**`ecf eval run --corpus FILE --classifier … --actor …`:**
- Preset A only. `--claude` is refused.
- **Run separation** [R2, R54, R83]:
  - The result file goes only to `<data_dir>/evals/corpus/<corpus_id>/<run_id>.json` (0600).
  - No `eval_runs` row and no `_remember_root`.
  - **`gate_passed` is forced false.** The `eval.completed` audit row carries `set_version`.
  - `set_version = corpus:<corpus_id>:<labels hash12>`.
  - `results.compare` refuses mixed sets (`results.py:71`).
  - The gate, fallback, `claude_eval`, stage ticks, export and `eval status` recent never see corpus runs (checked
    in R2 against every reader).
  - `eval status` current and the Slack progress line mark a corpus run "corpus".
  - Code: `_save` is split into a file half and a DB/audit half. `start` skips `_remember_root` for a corpus.
- `CaseResult` gains `actions: list[str]`, recorded at run time [R53].
- The summary carries `labels_hash` [R86].
- **Keep location** [R85]: the operator copies each result the OD cites to a named folder outside git (0600). The
  OD records each result's sha256.

**`ecf eval rescore RESULT --corpus FILE`** [R53]:
- Recomputes the expected values from the current labels, then scores fields and rule from `got`, and safety from
  `actions`.
- Writes a new result file with the new `set_version`. No model re-runs.

**`ecf eval compare`** takes result paths and scores confirmed labels only. This also changes synthetic comparisons.

## Phase A: replay on `ecf-server dev` (outside the gate; after B)

Phase A checks mechanics only. Accuracy comes from Phase B.
- **Dev service:** `ecf-server dev --home <RAM disk> --imap-cafile <Dovecot CA>` [R59, R60].
  - The RAM disk is created with `hdiutil attach -nomount ram://…`, formatted APFS, and detached after the run.
  - `--imap-cafile` is honoured only when `dev=True`. It sets `state.mail_factory` to build `ImapSource` with that
    CA, so timer-driven fetch and replay both trust Dovecot.
- **Target** [R4, R61, R74]:
  - Loopback Dovecot, with the whole mail home (mail, index, control) on tmpfs, `--rm`, and explicit Colima memory
    and tmpfs sizes in CONTRIBUTING. The mail path is verified in Phase 1.
  - Replay refuses when the header's `bytes` won't fit the tmpfs size. APPENDLIMIT is a secondary check.
- **`ecf corpus replay`:**
  - Only on a service started as `ecf-server dev`.
  - Host check: `ip = ip_address(host)`, and it must hold that `ip.is_loopback and ip.ipv4_mapped is None and
    str(ip) == host`. No names, no DNS.
  - TLS keeps `CERT_REQUIRED` and `check_hostname`.
  - The ssh `-L` residual is stated in §12.2.
- **Fidelity** [R13, R15, R75]:
  - An optional timezone-aware `when` is added to `Conn.append`, `MailSource.append` and `ImapSource.append`
    (`msg_time=when`) and to both fakes. A fake INBOX append lands in the INBOX store.
  - The contract asserts that `meta(...)[uid].internaldate == when`.
  - Message-IDs are kept. Each replay uses a fresh dev service. `--fresh-ids` is for load tests only and breaks
    DKIM.
- **Shadow processing:**
  - `ecf address add` the Dovecot account on the dev service (IP-literal host, tested in `-m imap`).
  - Fake chat.
  - Check that every message is fetched, classified and actioned without errors.
  - Compare the auth distribution with the manifest.
- **Cleanup:** detach the RAM disk and remove the container.

## Critical files

- **New:**
  - `src/ecf_server/corpus.py`
  - `src/ecf/cli_corpus.py`
  - a migration for `corpus_downloads`
  - `docs/adr/0022-real-mail-test-corpus.md`
  - `tests/test_corpus*.py`
- **Changed:**
  - `src/ecf_server/mail/{_imapclient,__init__,imap,fake}.py` (append `when`)
  - `src/ecf_server/download_budget.py`
  - `src/ecf_server/export_bundle.py` (EXCLUDED)
  - `src/ecf_server/api.py`
  - `src/ecf_server/__main__.py` and `service.py` (`--imap-cafile`, dev only)
  - `src/ecf_server/evalrun.py` (`_save` split, corpus case source, `gate_passed`, `actions`)
  - `src/ecf_server/ruletest.py` (Scratch variant)
  - `src/ecf/eval/results.py` (`actions`, confirmed-only `compare`)
  - `src/ecf/cli.py` (eval `--corpus`, `rescore`)
  - `src/ecf/cli_destroy.py`
  - `.gitignore`
  - `SPEC.md`
  - `CHANGELOG.md`
  - `CONTRIBUTING.md`
  - `docs/gmail-setup.md`
  - `.claude/rules/eval-synthetic.md`
- **Reused:**
  - `manual_export.check_path`
  - `export_bundle.write_atomic`
  - `_age`
  - `passphrase.generate`/`check`
  - `stepup.purpose`/`consume`
  - `ecf/stepup.with_step_up`
  - `prompts.hidden`/`require_terminal`
  - `models.start_install`
  - `download_budget`
  - `internal.fold`
  - `isolate.subprocess_isolator`
  - `senderauth`
  - `facts.sender_hash`
  - `message.content_hash`/`identity_digest`
  - `text.plain`
  - `triggers.redact_injection`
  - `fetch.LARGE_BYTES`
  - `ruletest._outcome`
  - `results.compare`
  - `replay.fresh_message_id`

## Verification

- **Unit tests** (`uv run pytest tests/test_corpus*.py` plus the touched files; ruff, pyright, lint-imports).
- **Fetch:**
  - **Selection:** bounded windows, with a fake that refuses a line over 1 MB; iterative top-up; exhaustion; `k`
    larger than the population; the skip counters (own via labels and header, gone, duplicate, timeout, facts).
  - **Caps:** count, byte cap, and the half-budget S rule including `next > left_now`. Budget is recorded per
    message, address-keyed and email-keyed.
  - **Chunk and sleep** with a fake clock.
  - **Retry and resume.** A partial corpus on stop and on a UIDVALIDITY change.
  - **Facts isolation:** a crafted message times out → `facts: null`, and the run continues. Each message gets a
    fresh DNS budget.
- **Security:**
  - **Step-up:** refused without a nonce; bound to its target (host included); every prompt comes before the
    step-up. Fetch is refused on a dev service.
  - **`require_terminal`** on every corpus command.
  - **Path checks:** data folder, `.git` directory and file, symlink, existing file, Mobile Documents.
  - **Credentials:** a one-off password is never stored. A watched email typed as one-off is refused.
  - **Passphrase:** generated by default. `--own-passphrase` enforces its rule.
  - **Audit:** the row holds no email and no path.
  - **Crypto:** encrypt/decrypt round trip; the header hash is checked; a tampered manifest is refused.
  - **`corpus info`** needs no passphrase and prints no subject or sender.
  - **Callers:** MCP and agent are refused.
  - **`CorpusSession`:** one at a time, the idle timeout releases it, and the passphrase is not kept.
- **Phase B** [R70]:
  - Manifest-built facts equal the live analyzer's on the same bytes (seeded DNS and history).
  - A one-off source → rule not scored.
  - `gmail_labels` → `self_sent`.
  - With perfect labels, the derived rule equals the pipeline's rule.
  - A labels edit → new `set_version`. `rescore` gives the same result as a re-run on a fixture.
  - Duplicates are de-duplicated. `merge` de-duplicates and re-indexes.
  - Labels: `label --corpus` loads no result file. The synthetic `eval label` never picks a corpus file.
    Unconfirmed, unsure and mismatched labels are not counted.
  - `--corpus --claude` is refused.
  - **Run separation:**
    - `gate.synthetic` returns the same `Check` before and after a corpus run;
    - no `eval_runs` row is written and `EVAL_ROOT` is unchanged;
    - `gate_passed` is false;
    - `compare` corpus vs synthetic is refused;
    - the status line says "corpus".
- **Replay** (Phase A):
  - Hosts refused: `localhost`, `127.1`, `::ffff:127.0.0.1`, `::ffff:7f00:1`, `[::1]`, `0.0.0.0`.
  - Replay is refused on a non-dev service. A corpus too big for the tmpfs is refused. `when` reaches the fakes and
    the contract.
- **IMAP tests** (`-m imap`, Dovecot, also on Linux CI):
  - fetch by folder role;
  - windows on a seeded folder;
  - replay with INTERNALDATE kept;
  - byte-identical messages;
  - `address add` with an IP-literal host and `--imap-cafile` on dev.
- **macOS:** the scripted MacStepper test for both purposes, as in `tests/test_stepper.py`. Full suite once before
  asking to commit (`uv run pytest -n auto -rs`, 0 skipped).
- **Real-service test 1**, the gating mechanics test. It needs the operator's go-ahead at the time, and its code
  is throwaway in the scratchpad. On `ecf-test-gmail` via `--address`:
  - fetch 20 messages most-recent, then 20 random;
  - force one disconnect mid-run (Wi-Fi off about 20 s) and confirm resume;
  - pre-seed `downloads` so the half rule binds and the run stops on it;
  - force a stop and get a partial file;
  - check the budget rows;
  - measure decrypt time and the `footprint` peaks;
  - label the 20 blind, timed;
  - `ecf eval run --corpus` (preset A), then `rescore` after one label change.
  Results go in its own §21.1 row.
- **Real-service test 2**, replay: after v1.0.0, not a release criterion. RAM-disk dev, tmpfs Dovecot, replay, and
  shadow processing. It gets its own §21.1 row.
- **The gating 500+ fetch** from the operator's mailbox belongs to the systemone plan, with its own go-ahead and
  §21.1 row.
- **After each push:** `gh run watch <id> --exit-status`.
