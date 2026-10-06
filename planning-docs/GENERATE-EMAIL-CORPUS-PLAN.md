# Plan: `ecf corpus`: real-mail test corpus (fetch, replay, eval)

Draft 2 (2026-10-06): folds in the Phase R review, findings R1-R44 in
`state-archive/corpus/corpus-review-findings.md` (gitignored). Tags like [R12] point to them.

## Context

Evaluation today uses only the committed synthetic set (185 cards). The operator wants to test ecf on real mail,
which SPEC forbids. §12.4 says full messages are never written to disk (OD-108/109). §17.3 and §16.6 say test
mailboxes and end-to-end tests use synthetic mail only.

Operator decisions (2026-10-06):

- **§12.4 exception.** A corpus may exist on disk only **encrypted**. **Any install** (prod included) may create
  one, behind step-up and a Security Notice.
- **Gating** [R10]. The corpus gates `v1.0.0` only through the decision-model experiment
  (`planning-docs/SYSTEMONE-MODEL-TESTING-PLAN.md`). That plan's adopt/not-adopt OD is the §1.5 criterion.
  - Phases 0, 1 and B are on the path to rc1.
  - Phase A (replay on `ecf-server dev`) is outside the gate.
  - The work is built on a `corpus` branch from `v1.0.0-release` and merged back before rc1.
- **Report only** [R8]. Corpus results are reported and never gated. The absolute safety gates stay on the
  synthetic set. The corpus measures category and sender-type accuracy, calibration and real-mail noise. It does
  not measure fraud recall.
- **No Anthropic** [R3]. `--corpus` with `--claude` is refused in v1, and so is `/ecf-eval` on a corpus.
- **Blind labels** [R6]. Labelling never shows any model output and happens before any replay or corpus run.
- **Sleep:** fixed seconds between batches, default 10.
- **Credentials:** either `--address` (an address ecf already watches; Keychain) or a one-off prompt (never
  stored).
- Each phase starts when the operator names it. The plan is a working document; SPEC stays authoritative.

## Facts this design relies on

- **Gmail IMAP limits**, verified 2026-10-06 (knowledge.workspace.google.com/admin/gmail/gmail-bandwidth-limits):
  - "Download with IMAP: 2500 MB per day".
  - "A suspension typically lasts for 1 hour, but can last up to 24 hours."
  - They apply to "all Google Workspace editions". The limit for personal accounts is unverified (§12.2, §21.2).
  - At most 15 simultaneous IMAP connections (§18). The corpus job adds a second session, so at most 2 per
    address [R38].
- **Purelymail:** no bandwidth or connection limit is recorded (unverified). A refused connection is retryable
  [R38].
- **Existing guards:**
  - Gmail budget: 2,500,000,000 bytes per rolling 24 h per address (`download_budget.py`, table `downloads`).
  - Live fetch downloads one body per FETCH, never batched (SPEC line 265).
  - Messages over `fetch.LARGE_BYTES` (16 MiB) are deferred.
- **Code facts found by the review:**
  - `ImapSource` always selects INBOX (`mail/imap.py:95-99`, `_reads` :115-118) [R11].
  - `Conn.append`/`MailSource.append` take no date, and imapclient's `append(..., msg_time=)` does [R13].
  - `evalrun` computes facts in `ruletest.Scratch` with `_no_dns` and an empty sender history [R1].
  - `evalrun.latest` has no set filter and `gate.synthetic` compares `set_version` [R2].
  - `passphrase.check` accepts any five distinct words [R17].
  - pyrage works on whole buffers, and the age scrypt work factor isn't exposed [R17, R20].

## Command surface (CLI token only; MCP gets nothing)

```
ecf corpus fetch --out PATH [--total 500] [--chunk 10] [--sleep 10] [--max-bytes 256MiB]
                 [--order most-recent|random|oldest] [--folder ROLE|NAME]
                 [--include-own] [--allow-spam] [--own-passphrase]
                 [--address ADDR | (prompts: email, IMAP host, app password)]
ecf corpus status | stop
ecf corpus info FILE                    # clear header only; no passphrase, no decrypt [R39]
ecf corpus replay FILE --user U --port P --cafile CA [--host 127.0.0.1] [--fresh-ids]   # Phase A, dev only
ecf eval label --corpus FILE            # Phase B, blind authoring UI
ecf eval run --corpus FILE --classifier … --actor …    # Phase B, preset A only
```

**Folder** [R26, R37]:
- `--folder` takes a role (`inbox`, `all-mail`) or a name.
- On Gmail the default is the folder with the `\All` role (found by role, so a localised name works). Elsewhere
  the default is INBOX.
- The folder is resolved to its exact decoded name at preflight and validated against `folders()`. The step-up
  binds the resolved name.
- `\Trash` and `\Junk` roles are refused unless `--allow-spam`. A Spam enrichment fetch is allowed this way [R8].
- When INBOX is chosen on Gmail, the preflight prints the `gmail_inbox_counts` warning (OD-440).

**Own mail** [R22]: messages with Gmail `\Sent` or `\Draft` labels, and messages carrying `X-ECF-Install`, are
skipped and counted in the manifest (`skipped_own`). `--include-own` keeps them.

**Gmail mode** comes from `capabilities().gmail` after login (OD-438), never from the host [R42]. For a one-off
gmail.com address the host prompt defaults to `imap.gmail.com`.

## Limits (recorded in OD-462)

- **`--total`:** default 500, maximum 5,000. Phase B's loader has its own limit, not `ruletest.MAX_CASES` [R29].
  - **Power statement** [R8]: n=500 at 20% discordance gives a paired SE of about 2 points. The final N for the
    decision-model comparison is set from measured discordance (systemone OD-465).
- **`--max-bytes`:** default 256 MiB, maximum 512 MiB, of message bytes [R20].
- **Gmail budget share** [R24]:
  - At preflight the cap is `min(max-bytes, floor(left()/2))`, shown to the operator.
  - `left()` is re-read before each chunk. The run stops when the next message would exceed what remains.
  - The preflight warns that live checks of the address may stop on Gmail's budget until a given time.
  - Off Gmail, `left()` is None and only the byte cap applies.
- **`--chunk`:** default 10, range 1-100. Each body is still its own `BODY.PEEK[]` FETCH.
- **`--sleep`:** default 10 s, range 0-600 everywhere [R41].
  - The sleep is politeness and a pause point for `stop`. The byte caps are what keep the account safe.
- **Preflight** prints the candidate count, the batch count (`ceil(total/chunk)`), the total bytes (from the lean
  meta pass), the byte cap and an estimated duration. At the defaults (500 messages) that is 50 batches × 10 s,
  about 8 min plus fetch time.

## Selection [R12, R36, R40]

Messages are selected first, then a lean meta pass runs on the candidates only:
- `random`: `UID SEARCH ALL`, then `secrets.SystemRandom().sample` of about 1.2 N UIDs.
- `most-recent` / `oldest`: the highest or lowest about 2 N UIDs as a window, ordered by INTERNALDATE within it.
  The order differs from true INTERNALDATE order only for APPENDed or imported mail, which is a stated limit.
- The lean meta fetches `RFC822.SIZE INTERNALDATE`, plus `X-GM-LABELS X-GM-MSGID` on Gmail. It has no ENVELOPE.
- Candidates over `fetch.LARGE_BYTES`, own mail and refused roles are dropped and counted. The first N survivors
  are fetched. The corpus therefore never covers the large-message path (stated in §16.1).
- The folder is opened read-only (EXAMINE). `BODY.PEEK` sets no `\Seen`.

## Security model

- **Step-up for fetch:** new purpose `corpus_fetch` (Touch ID; the `manual_export.py` pattern).
  - Bound to `{source email, resolved folder, host, port, out path, total, max-bytes, order}` [R18]. It never
    contains the password or a hash of it.
  - The dialog names the count, the source address, the host and the output path [R16].
  - The CLI collects every input before the first step-up action: credentials, the passphrase twice, and the
    preflight confirmation. The retry inside the 2-minute window is then immediate [R33].
- **Step-up for replay:** new purpose `corpus_replay`, bound to `{corpus_id, file sha256, host, port, user}`,
  with a Security Notice [R16].
- **`label` and `run --corpus`:** `require_terminal()`, as the synthetic `eval label` does [R16].
- **Authorized administrator:**
  - In v1 the OS user is the sole admin (OD-007, OD-013).
  - Routes are `@allow(Caller.CLI)`. MCP and agent callers are refused.
  - `ecf claude` cannot run `ecf corpus`: Bash is denied there (`claude_wrapper.py`) [R35].
  - The M2 roadmap notes record that corpus commands will need the admin role.
  - Residual: any other same-user process holding the CLI token is covered by the §12.2 limit.
- **Credentials:**
  - With `--address`, the service reads the Keychain item through the `checks.py` closure.
  - One-off: the CLI collects the password with `prompts.hidden()` and sends it over the socket. The service
    builds `imap_factory(host, email, lambda: pw)` and never stores it.
  - Never argv, env or disk.
  - A typed email that matches a watched address (after `lower()` and Gmail folding) is refused with
    "use --address <id>" [R23].
- **Passphrase** [R17]:
  - By default the service generates the six-word passphrase (`passphrase.generate`).
  - `--own-passphrase` accepts a typed one only under a stronger rule: at least 5 words of 3 or more chars and at
    least 24 chars.
  - The real-service test measures decrypt time, and the work factor goes in SPEC.
  - Encryption is `_age.encrypt_passphrase`, so the file is portable between installs.
- **Output path** [R32]:
  - Checked like `manual_export.check_path`: absolute, suffix `.ecfcorpus`, parent exists, not in the data folder,
    target doesn't exist, not a symlink.
  - Refused inside a git work tree: walk up for `.git`, where a directory **or a file** counts (worktrees).
  - Refused in `~/Library/Mobile Documents`. Other known sync folders get a warning.
  - Written with `export_bundle.write_atomic` (0600, O_EXCL).
  - `.gitignore` gains `*.ecfcorpus` and `.*.ecfcorpus.partial`.
- **Memory** [R20]:
  - The tar.gz is streamed (`tarfile` mode `w|gz` into a buffer) as messages arrive, and each raw message is
    dropped once it is added.
  - The peak is about the gz plus the ciphertext. It is measured with `footprint` at 512 MiB, and the figure goes
    in §12.2.
  - Buffers are deleted right after use.
  - The job catches exceptions, stores only the type name and clears tracebacks.
  - Each message's handling sits under the same crash quarantine as fetch.
  - Assumptions are stated in §12.2 and checked by `doctor`: encrypted swap (`sysctl vm.swapusage`) and no core
    dumps.
- **Notice and audit** [R31]:
  - A Security Notice (Slack and desktop) names the path.
  - The audit event `corpus.fetched` stores counts, bytes, order, source address, `corpus_id` and the file sha256,
    **not the path** (as `export.completed`). Logs follow OD-165.
- **Excerpts shown while labelling** [R21]:
  - Shown through `text.plain` and `triggers.redact_injection`, about 1,500 chars by default, with a "more" key.
  - The CLI tells the operator to label in Terminal, not in a Claude session.

## Corpus format

- **Clear header** [R39, R14]: format version, `corpus_id` (uuid), created_at, ecf version, source domain, folder
  role, order, count, bytes, skipped counts (large, own, role), `complete` (bool) and the stop reason, and
  `source_preset`.
- **Encrypted payload:** a streamed `tar.gz` of `NNNNN.eml` files (raw bytes, every header) plus `manifest.jsonl`,
  age-encrypted with the passphrase.
- **Manifest, one row per message** [R1, R25, R28]:
  - Identity: index, uid, uidvalidity, folder, internaldate, size, sha256 (integrity only), `content_hash` (§6.3;
    the label key), Message-ID.
  - Gmail: `X-GM-MSGID` and labels.
  - **Facts at fetch time** (metadata only), computed by the service with live DNS: `senderauth` result and
    aligned domain, DKIM outcome, presence of the provider's `Authentication-Results` header.
  - With `--address`: a snapshot of `sender_seen_before` and `sender_confirmed` from the install's `senders`
    table. One-off sources have no sender history, and the manifest says so.
- **Source profile, in the encrypted payload:** email, provider, sensitivity, org_domains, org_addresses.
- **Labels file** [R28]: `<corpus file>.labels.jsonl` (0600) beside the corpus, keyed by `content_hash`, with
  `corpus_id` as a hint. It holds labels only, no content. It survives `ecf destroy`, and `corpus info` reports
  its count.

## Phase R: adversarial Fable review of draft 1 (done 2026-10-06)

Three parallel read-only Fable reviewers looked at three areas: security and privacy; IMAP, Gmail and limits;
eval validity and consistency.
- They found 44 merged findings: 2 critical, 13 high, 15 medium, 14 low.
- The operator decided every finding. The record is in `state-archive/corpus/corpus-review-findings.md`.
- This draft carries the accepted fixes.

## Phase R2: adversarial Fable review of draft 2 (before any code or SPEC commit)

Operator request 2026-10-06:
- **Reviewers:** parallel read-only `Agent` calls with `model: "fable"`, covering the same three areas. Each also:
  - checks that every accepted R1-R44 fix is in this draft and is correct;
  - attacks the run separation in Phase B: any other reader of `eval_runs` or `EVAL_ROOT`, and any path where a
    corpus result could still reach the gate, fallback or `claude_eval`;
  - checks consistency with the systemone plan.
- **Output:** findings numbered from R45, under a "Phase R2" heading in the findings file.
- **Gate:** every critical and high finding must be addressed before going on, either fixed in draft 3 (the
  operator confirms) or rejected by the operator with a recorded reason. Medium and low findings go to the
  operator as in Phase R.
- If draft 3 changes the design materially, the reviewers check it again under the same gate.
- Only then Phase 0, and then code, each started when the operator names it.

## Phase 0: decision and SPEC (first commit, after operator OK)

- **OD-461:** the §12.4 exception: encrypted corpus files, any install, step-up, Security Notice.
  - Excerpts of replayed corpus mail exist only in a deleted `ecf-server dev` data folder [R4].
  - Corpus content never goes to Anthropic in v1 [R3].
- **OD-462:** limits, defaults and selection (above), the email-keyed budget table [R23], and the power statement
  [R8].
- **OD-463:** replay goes only to loopback Dovecot, from `ecf-server dev`. The target must be an IP literal, the
  whole mail home is on tmpfs, and the container runs with `--rm` [R4, R19].
- **ADR 0022** "Real-mail test corpus": context, decision, consequences, the privacy destination analysis.
- **SPEC edits:**
  - §12.4: the exception text. A corpus is a new place content is stored; the destinations are unchanged.
  - §12.2: memory figures, the encrypted-swap assumption, the ssh-forward residual on replay [R19, R20].
  - §17.3 and §16.6: replay targets local Dovecot from a dev service only [R29].
  - §16.1: the real-mail set (blind labels, keyed by content_hash, held beside the corpus). §16.1 also says:
    - labels and results hold labels, hashes and model fields, no message text [R34];
    - it can't reproduce sender history for one-off sources or messages over 16 MiB [R1, R36].
  - §14.3: the corpus counts against the Gmail budget.
  - §9.6: the step-up list (`corpus_fetch`, `corpus_replay`).
  - §10.2: the command list.
  - §23.4: the OD rows.
  - No corpus-specific §1.5 criterion. The systemone OD is the criterion [R9, R10].
- **Rule:** `.claude/rules/eval-synthetic.md` and `GENERATE-FAKE-TESTING-EMAILS.md` say corpus content never
  feeds cards [R29].
- **Systemone plan:** drop its OD-466 (amend OD-462 instead), and change its "after Phases 0, 1, A and B" to
  "0, 1 and B" [R10, R30]. This needs the operator's OK, in the same commit or the next.
- **CHANGELOG**, one line per change [R30]:
  - `ecf corpus fetch/status/stop/info`;
  - the §12.4 exception;
  - Gmail budget sharing;
  - `ecf eval label/run --corpus`;
  - `ecf corpus replay` (dev).

## Phase 1: `corpus fetch` (service job)

- **New `ecf_server/corpus.py`:**
  - A background job modelled on `models.start_install`: a `Progress` with a lock, `ConflictError` when one is
    already running, an injectable `spawn`, a `stop` Event, and a `clock` for sleeps.
  - Steps: login, `capabilities()`, folder resolve, select, lean meta, then batches.
  - Each message is fetched, its facts are computed (live DNS), it is added to the streamed tar, and its budget is
    recorded **at once** (as `fetch.py:429`) [R14].
  - Then encrypt, write, notice, audit.
- **Read-only corpus reader** over `lib.Conn` [R11]:
  - It EXAMINEs the chosen folder once and records UIDVALIDITY.
  - `ImapSource` stays INBOX-only, and `MailSource` gains no folder parameter.
- **Interruptions** [R14, R25]:
  - On `MailUnavailableError`, reconnect with backoff (5 tries over 5-60 s), re-EXAMINE, compare UIDVALIDITY and
    resume at the next unfetched UID.
  - A UIDVALIDITY change, `stop`, or an error that can't be recovered writes a **partial corpus** (`complete:
    false`, with the reason) when at least one message was fetched.
  - A service restart discards the run (daemon thread).
  - The CLI says long runs need AC power and sleep prevented (the OD-230 pattern).
- **`download_budget.py`** [R23, R24]:
  - The address ID is used with `--address`.
  - One-off sources use a new table `corpus_downloads (email_norm, hour, bytes)` (migration, no FK).
    `download_budget.used` adds it when an address's normalised email matches, and prunes it like `downloads`.
  - The half-of-remaining rule lives in `corpus.py`.
- **Routes** (`api.py`): `POST /v1/corpus/fetch`, `GET /v1/corpus`, `POST /v1/corpus/stop`, all
  `@allow(Caller.CLI)`. Progress is polled the way `cli_models.py` does (`POLL_S = 2`).
- **New `ecf/cli_corpus.py`:** a `make_corpus_app(paths)` Typer group, registered in `ecf/cli.py` next to
  `models`. It runs the preflight summary and confirmation, then step-up.
- **`corpus info`** reads the clear header and the labels file count only.

## Phase B: blind labelling and eval runner (gates `v1.0.0` via systemone)

**`ecf eval label --corpus FILE`** [R5, R6, R21; systemone requirements]:
- The service decrypts. The CLI shows each excerpt through `text.plain` and `redact_injection`, in random order.
- No result file is ever loaded (tested).
- It is an authoring UI: one prompt per schema field with the closed vocabulary from `schema_v1.yaml`.
  - `s` skips and `u` marks unsure. Both are excluded from scoring and counted.
  - The session is resumable from the labels file.
  - Pre-fill comes only from deterministic manifest facts and keyword hits, never a model's answer.
  - The author is `operator` [R44].
- The expected `rule` and `safety` are derived from the labels and the manifest facts by `ecf rules test`, not set
  by hand.
- Freeze: once labelling is done, the labels file's hash becomes part of the set version. A later change, such as
  systemone adjudication, makes a new version and every arm is re-scored.
- **Operator time:** at 1-2 min per message, 500 messages is about 8-17 h. The estimate is refined after the
  20-message test and entered in both plans.
- **Composition:** if a fetch falls short of the minimum per-class counts in systemone's OD-465, a second fetch
  from another folder tops it up. The manifest keeps the folder, so results split by folder.

**Facts in the run** [R1]:
- A corpus case source yields the decrypted message plus a `Scratch` variant built from the manifest. It
  uses:
  - the source profile;
  - the recorded auth/DKIM outcome instead of `_no_dns`;
  - the sender-history snapshot when present.
- `rule` is scored only when the manifest facts exist. Otherwise only the classifier fields and safety are scored.
- Corpus cases use `id` = the index `NNNNN`, `author` = `operator` and `profile` = `corpus` [R44].

**`ecf eval run --corpus FILE --classifier … --actor …`:**
- Preset A only. `--claude` is refused [R3].
- **Run separation** [R2]:
  - A corpus run writes **only its result file**, to `<data_dir>/evals/corpus/<corpus_id>/<run_id>.json` (0600).
  - It inserts no `eval_runs` row and does not call `_remember_root`.
  - It writes the `eval.completed` audit row with the set noted.
  - `set_version` is `corpus:<corpus_id>:<labels hash, 12 hex>`.
  - `results.compare` already refuses different set versions (`results.py:71`), so corpus and synthetic runs
    can't be mixed.
  - The gate, fallback, `claude_eval` and the status list never see corpus runs. There is no migration and no
    reader change.
  - Code: split `_save` into the file half and the DB/audit half. `start` skips `_remember_root` for a corpus.
- `ecf eval compare` takes result paths and scores confirmed labels only. This is the harness for the
  Gemma-vs-decision-model comparison.

## Phase A: replay on `ecf-server dev` (outside the gate; after B)

Phase A checks mechanics only [R7]. Accuracy comes only from Phase B's blind labels.
- **Target** [R4, R19, R27]:
  - Loopback Dovecot, with the whole mail home (mail, index, control) on tmpfs and the container run with `--rm`.
  - CONTRIBUTING gives explicit sizes, for example `colima start --memory 4`, a 1 GB tmpfs.
  - Replay checks the target's `APPENDLIMIT` against the largest message.
- **`ecf corpus replay`**:
  - Allowed only from an `ecf-server dev` service. Refused on prod and test roles.
  - The host must be an IP literal with `ipaddress.ip_address(host).is_loopback`. Names and DNS are refused.
  - TLS keeps `CERT_REQUIRED` and `check_hostname`.
  - The CA file is a per-request parameter used only for that connection, never stored and never given to
    `mail_factory`. An ssh `-L` forward residual is stated in §12.2.
  - The replay step-up and Security Notice apply.
- **Fidelity** [R13, R15]:
  - Each message's INTERNALDATE is kept: an optional timezone-aware `when` is added to `Conn.append`,
    `MailSource.append`, `ImapSource.append`, both fakes and the contract test.
  - Message-IDs are kept by default. Each replay uses a fresh dev service, so stable_id duplicates can't arise.
  - `--fresh-ids` is for load tests only and is documented as breaking DKIM.
- **Shadow processing:** `ecf address add` the Dovecot account on the dev service. It uses the fake chat (no
  Slack). Check that every replayed message is fetched, classified and actioned without errors, and compare the
  auth distribution with the manifest.
  - Unverified: how `ecf-server dev` trusts the container's certificate for `address add` [R19]. Check this in
    Phase 1. If there is no mechanism, add a dev-role-only CA option.
- **Cleanup:** delete the dev data folder and the container after the run.

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
  - `src/ecf_server/api.py`
  - `src/ecf_server/evalrun.py` (`_save` split, corpus case source)
  - `src/ecf_server/ruletest.py` (`Scratch` from a manifest)
  - `src/ecf/eval/*` (label UI, `compare` confirmed-only)
  - `src/ecf/eval/cards.py` (`operator` author, `corpus` profile)
  - `src/ecf/cli.py`
  - `.gitignore`
  - `SPEC.md`
  - `CHANGELOG.md`
  - `CONTRIBUTING.md`
  - `.claude/rules/eval-synthetic.md`
- **Reused:**
  - `manual_export.check_path`
  - `export_bundle.write_atomic`
  - `_age.encrypt_passphrase`/`decrypt_passphrase`
  - `passphrase.generate`/`check`
  - `stepup.purpose`/`consume`
  - `ecf/stepup.with_step_up`
  - `prompts.hidden`
  - `addresses.login_and_probe`
  - `models.start_install`
  - `download_budget`
  - `senderauth`
  - `text.plain`
  - `triggers.redact_injection`
  - `fetch.LARGE_BYTES`
  - `replay.fresh_message_id`
  - `results.compare`

## Verification

- **Unit tests** (`uv run pytest tests/test_corpus*.py`, plus ruff, pyright and lint-imports):
  - Selection: the orders, over-sampling, own-mail and role skips, and the lean meta items.
  - Caps: count, byte cap, and the half budget re-read per chunk.
  - Chunk and sleep with a fake clock.
  - Budget recorded per message, for both the address ID and the email-keyed form.
  - Retry and resume after a drop, and a partial corpus on stop and on a UIDVALIDITY change.
  - Step-up refused without a nonce, and bound to its target (host included). The input order puts every prompt
    before the step-up.
  - Path checks: data folder, git dir **and** `.git` file, symlink, existing file, Mobile Documents.
  - The one-off password is never stored. A watched email typed as one-off is refused.
  - Generated passphrase by default. `--own-passphrase` enforces the stronger rule.
  - Encrypt/decrypt round trip, matching manifest hashes, and a tampered manifest excluding the case.
  - `corpus info` needs no passphrase and prints no subject or sender.
  - MCP/agent caller refused.
  - Run separation [R2, R43]:
    - `gate.synthetic` returns the same `Check` before and after a corpus run;
    - no `eval_runs` row is written and `EVAL_ROOT` is unchanged;
    - `compare` of a corpus result against a synthetic result is refused.
  - Labels: `label --corpus` loads no result file and needs a terminal. Unconfirmed, unsure or mismatched labels
    aren't counted. `--corpus --claude` is refused.
  - Replay: non-literal and non-loopback hosts are refused (`localhost`, `127.1`, `::ffff:127.0.0.1`,
    `0.0.0.0`). Replay is refused outside the dev role, and `when` reaches the fakes.
- **IMAP tests** (`-m imap`, Dovecot), all also on Linux CI:
  - fetch from a seeded container, by folder role;
  - replay into a second mailbox with INTERNALDATE kept;
  - byte-identical messages.
- **macOS:** the scripted MacStepper test for both purposes (no Touch ID dialog; as `tests/test_stepper.py`) [R43].
  Run the full suite once before asking to commit (`uv run pytest -n auto -rs`, 0 skipped).
- **Real-service test** (the mechanics check; needs the operator's go-ahead at the time; code throwaway in the
  scratchpad):
  - fetch 20 messages from the `ecf-test-gmail` Keychain account (most-recent, then random), with a forced stop
    and a partial file;
  - check the budget rows;
  - measure decrypt time and the `footprint` peak;
  - label the 20 blind, and time the labelling;
  - `ecf eval run --corpus` (preset A);
  - replay on `ecf-server dev` into tmpfs Dovecot.
  Results go in SPEC §21.1. The 500+ fetch from an operator mailbox belongs to the systemone plan's runs, with its
  own go-ahead and §21.1 row.
- **After each push:** `gh run watch <id> --exit-status`.
