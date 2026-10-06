# Plan: `ecf corpus`: real-mail test corpus (fetch, replay, eval)

## Context

Evaluation today uses only the committed synthetic set (185 cards). The operator wants to test ecf on
real mail, which SPEC forbids: §12.4 says full messages are never written to disk (OD-108/109), and §17.3 says
test mailboxes receive synthetic mail only. Operator decisions in this session (2026-10-06):

- §12.4 gains an exception. A corpus may exist on disk only **encrypted**, and **any install** (prod
  included) may create one, behind step-up and a Security Notice.
- **Eval path: A and B, both now** (operator decision 2026-10-06; was "A now, B later"). A is replay into local Dovecot plus shadow review. B is
  `ecf eval run --corpus` with stored labels.
- **Sleep:** fixed seconds between batches, default 10.
- **Credentials:** either `--address` (an address ecf already watches; Keychain) or a one-off prompt
  (never stored).

First step after approval: save this plan as `planning-docs/GENERATE-EMAIL-CORPUS-PLAN.md`. This creates the
directory. The plan is a working document; SPEC stays authoritative.

**Gates `v1.0.0`** (operator decision 2026-10-06). Phases 0, 1, A and B are all release criteria. The plan built on `v1.0.0-release` in a `corpus` branch, merged back before rc1.
Phase 0 adds it to SPEC §1.5 (release criteria) and to the v1.0.0 plan
(`~/.claude/plans/what-s-left-in-6b-mellow-quiche.md`, as a wave before rc1). The gating real-service test
is the one under Verification. Each phase starts when the operator names it.

## Facts this design relies on

- Gmail IMAP limits, verified 2026-10-06 (knowledge.workspace.google.com/admin/gmail/gmail-bandwidth-limits):
  - "Download with IMAP: 2500 MB per day".
  - "A suspension typically lasts for 1 hour, but can last up to 24 hours."
  - These apply to "all Google Workspace editions". The limit for personal accounts is unverified (SPEC §12.2, §21.2).
  - At most 15 simultaneous IMAP connections (§18). ecf uses 1.
- Purelymail: no bandwidth or connection limit is recorded. Unverified.
- Existing guards: ecf's Gmail budget is 2,500,000,000 bytes per rolling 24 h per address
  (`ecf_server/download_budget.py`, table `downloads`). Fetch downloads full bodies one message per FETCH,
  never batched (SPEC line 265). A message over 16 MB is deferred.

## Command surface (CLI token only; MCP gets nothing)

```
ecf corpus fetch --out PATH [--total 100] [--chunk 10] [--sleep 10]
                 [--order most-recent|random|oldest] [--folder INBOX]
                 [--address ADDR | (prompts: email, IMAP host [imap.gmail.com for gmail.com], app password)]
ecf corpus status | stop
ecf corpus info FILE               # passphrase prompt; prints the manifest summary, never content
ecf corpus replay FILE --user U [--host 127.0.0.1] [--port 31993] [--fresh-ids]   # phase A
ecf eval label --corpus FILE / ecf eval run --corpus FILE ...                     # phase B
```

**Limits** (to record as an operator decision in SPEC):
- **`--total`:** default 100, maximum 5,000.
  - The run also stops at **512 MiB** of message bytes, the most held in memory before encryption.
  - On Gmail it may use at most **half of the address's remaining download budget**, so live fetch keeps
    working and the account stays well under 2,500 MB/day. With one-off credentials the budget is recorded
    against the email address in the `downloads` table. The budget table is keyed by address ID, so the plan
    adds an email-keyed row form for this case.
- **`--chunk`:** default 10, range 1-100. A chunk is the number of messages fetched before a sleep. Each body is
  still its own `BODY.PEEK[]` FETCH (line 265).
- **`--sleep`:** default 10 s, range 1-600 on Gmail, 0-600 elsewhere. Before starting, the CLI prints the batch count
  (`ceil(total/chunk)`), the total bytes (from `RFC822.SIZE` in a meta pass) and an estimated duration.
- **Recommended maximum:** 5,000 messages. At the defaults that is 500 batches × 10 s, about 1.4 h plus fetch
  time. The byte cap and the budget share bind before the count does for mail with large attachments.

**Selection:** one meta pass over the folder's UIDs (`ImapSource.meta`, 500 UIDs per command), then:
- `most-recent`: the highest N by INTERNALDATE.
- `oldest`: the lowest N by INTERNALDATE.
- `random`: `secrets.SystemRandom().sample`, N of them.

Messages over 16 MB are skipped and counted. The folder is opened read-only (EXAMINE), and `BODY.PEEK` sets no
`\Seen` flag.

## Security model

- **Step-up:** new purpose `corpus_fetch`, bound to `{source email, folder, out path, total, order}`. It uses
  macOS LocalAuthentication (Touch ID) and follows the pattern of `manual_export.py` (`@stepup.purpose`,
  `stepup.consume`). The CLI uses `ecf/stepup.py: with_step_up`.
- **Authorized administrator:** in v1 the OS user is the sole admin (OD-007, OD-013). Three checks cover it:
  the CLI token (0600 file, `@allow(Caller.CLI)`), the step-up bound to `stepup.person()`, and an MCP/agent
  caller being refused. The plan records in the M2 roadmap notes that `corpus fetch` will require the admin
  role there.
- **Credentials:**
  - With `--address`, the service reads the address's Keychain item through the `checks.py` password closure.
  - Otherwise the CLI collects the password with `ecf/prompts.py: hidden()` and sends it over the socket. The
    service builds `imap_factory(host, email, lambda: pw)`, the same way `addresses.login_and_probe` does,
    and never stores it.
  - Never argv, env or disk (CLAUDE.md).
- **Encryption:**
  - The passphrase is prompted twice and checked by `ecf_server/passphrase.check`.
  - `_age.encrypt_passphrase` (age scrypt) makes the file portable between installs, so a prod fetch can be
    replayed on a test or dev install.
  - The file is written with `export_bundle.write_atomic` (0600, O_EXCL, never overwrites).
- **Output path:** checked like `manual_export.check_path`: absolute, suffix `.ecfcorpus`, folder exists, not
  inside the data folder, target doesn't exist. It is also refused inside a git work tree (walk up for
  `.git`). `.gitignore` gains `*.ecfcorpus`.
- **Notice and audit:** a Security Notice (Slack and desktop) as in `manual_export`. The audit event is
  `corpus.fetched` with counts, bytes, order, source address and path, never content. Logs follow the OD-165
  redaction rules.

## Corpus format

- The plaintext is assembled only in the service's memory: a `tar.gz` of `NNNNN.eml` files (raw bytes, every
  header) plus `manifest.jsonl`.
- Manifest fields per message: index, uid, uidvalidity, folder, internaldate, size, sha256, Gmail labels when
  Gmail.
- The tar.gz is then age-encrypted with the passphrase. A small clear header holds the format version,
  `corpus_id` (uuid), created_at, source domain only, count and the ecf version.

## Phase R: adversarial Fable review (before any code or SPEC commit)

Reviews come right after the plan is saved, before Phase 0 is committed and before any code is written
(operator request 2026-10-06).

**Reviewers:** three parallel `Agent` calls, each with `model: "fable"`, read-only. Each gets this plan, SPEC
§9.6/§12/§14.3/§16/§17.3/§18 and the files under "Critical files". Each is told to attack the design, not to
summarize it:
1. **Security and privacy.**
   - Ways the corpus or the password could reach disk unencrypted: swap, temp files, tarfile internals, logs,
     tracebacks, Slack and audit text, crash dumps.
   - Step-up binding gaps.
   - MCP or agent reach.
   - A path or git-tree check bypass (symlinks, races).
   - The passphrase strength and scrypt work factor.
   - Replay to a non-loopback host, including through DNS or IPv6 tricks.
   - Whether "any install" weakens prod.
   - Injection content in replayed mail.
2. **IMAP, Gmail and limits.**
   - Selection correctness: INTERNALDATE vs UID, UIDVALIDITY changes mid-run, Gmail All Mail and labels,
     folder names.
   - Budget accounting for one-off sources.
   - The half-budget rule.
   - Suspension risk at the maximum values.
   - The 512 MiB memory cap on the 24 GB Air.
   - Stop and resume behavior.
   - Purelymail unknowns.
   - Replay fidelity (INTERNALDATE, flags, Message-ID, stable_id duplicates).
3. **Eval validity and consistency.**
   - Phase B labelling and scoring.
   - Leakage between the corpus and the synthetic set.
   - Gate isolation (OD-261).
   - Label storage keyed by sha256.
   - The preset B/C Anthropic path.
   - Conflicts with existing SPEC text and ODs.
   - The CHANGELOG, ADR and §1.5 wording.
   - The test plan's gaps, including the macOS gate with 0 skipped.

**Output:** findings numbered R1…Rn, each with severity, evidence (file:line or SPEC §), and a proposed fix,
written to `state-archive/corpus/corpus-review-findings.md` (gitignored).
- The operator accepts, revises or rejects each finding.
- Accepted fixes go into plan draft 2 (`planning-docs/GENERATE-EMAIL-CORPUS-PLAN.md` updated, the operator
  confirms) and into the Phase 0 OD and ADR text.
- No code before the operator approves draft 2.

## Phase 0: decision and SPEC (first commit, after operator OK)

- **OD-461:** the §12.4 exception: encrypted corpus files, any install, step-up, Security Notice.
- **OD-462:** limits and defaults (above).
- **OD-463:** replay to loopback Dovecot only, on a tmpfs mail volume.
- **ADR 0022** "Real-mail test corpus" (context, decision, consequences, the privacy destination analysis).
- **SPEC edits:**
  - §12.4: exception text. The corpus is a new place content is stored, the destinations are unchanged.
  - §17.3: replay targets local Dovecot only.
  - §16.1: the real-mail set (corpus labels, phase B).
  - §14.3: the corpus counts against the Gmail budget.
  - §9.6: the step-up list.
  - §10.2: the command list.
  - §23.4: the OD rows.
  - §1.5: release criterion "`ecf corpus fetch`, `replay`, `eval label --corpus` and `eval run --corpus` built,
    real-service test passed".
- **CHANGELOG** line under the next heading.

## Phase 1: `corpus fetch` (service job)

- **New `ecf_server/corpus.py`:**
  - A background job modelled on `models.start_install`: a module `Progress` with a lock, `ConflictError` if
    one is already running, an injectable `spawn`, and a `stop` Event.
  - Meta pass, select, batches with `clock`-based sleep (testable), budget `left`/`record`, then build the
    tar.gz in memory, encrypt, write, notice, audit.
  - Plaintext bytes are dropped as soon as encryption finishes.
- **`ImapSource`** (`mail/imap.py`):
  - Add `folder` support for read-only select of a named folder (default INBOX).
  - Reuse `meta()` and `fetch()`.
  - Add the same to `MailSource` and `GmailFakeSource`/`FakeMailSource` (`mail/fake.py`).
- **`download_budget.py`:** an email-keyed form for one-off sources, or the address ID when `--address` is used.
  The half-of-remaining rule lives in `corpus.py`.
- **Routes in `api.py`:** `POST /v1/corpus/fetch`, `GET /v1/corpus`, `POST /v1/corpus/stop`, all
  `@allow(Caller.CLI)`. Progress is polled the way `cli_models.py` does (`POLL_S = 2`).
- **New `ecf/cli_corpus.py`:** a `make_corpus_app(paths)` Typer group, registered in `ecf/cli.py` next to
  `models`. It shows the preflight summary and a confirm before step-up.

## Phase A: `corpus replay` + shadow review

- **`ecf corpus info FILE`:** service-side decrypt and manifest summary.
- **`ecf corpus replay FILE`:**
  - The CLI sends the path, the passphrase and a one-off target password over the socket.
  - The service decrypts in memory and APPENDs each message to the target INBOX, reusing
    `ecf/replay.py: fresh_message_id` logic when `--fresh-ids` is set.
  - The target host must be loopback (127.0.0.1/::1), otherwise the command is refused.
  - Each message's INTERNALDATE is kept from the manifest.
- **CONTRIBUTING:** how to run the dev Dovecot container with a **tmpfs** mail volume (Colima), then
  `ecf address add` it on a test or dev install in shadow mode.
- **Evaluation:** you Correct or Fix items as in the V1.3 shadow run. `ecf stats` and the gate report per model
  (category accuracy, fraud-guard misses, unsafe proposals). Labels are stored as `stable_id` plus labels,
  with no content.

## Phase B: eval runner on a corpus (built after A, same branch)

- **`ecf eval label --corpus FILE`:**
  - Decrypts in the service and shows ecf's stored-length excerpt (≤ 4,000 chars) per message.
  - Labels use the same fields as the synthetic `labels.jsonl` and require operator confirmation (OD-229).
  - Labels are saved to `<data_dir>/evals/corpus/<corpus_id>/labels.jsonl` (0600), keyed by message sha256,
    with no content. They are never committed and never touched by the synthetic hygiene scan.
- **`ecf eval run --corpus FILE --classifier … --actor …`:**
  - `evalrun` gets a case source that yields decrypted messages plus labels, in place of built cards.
  - Scoring and the report are unchanged.
  - Results are marked `set: corpus:<corpus_id>`, so they never count toward the go-live gate's synthetic
    result (OD-261) unless a later decision says so.
  - `ecf eval compare` works across runs of the same corpus. This is the harness for the Gemma vs decision-model
    comparison.
- **Presets B and C:** a corpus run sends real mail to Anthropic, an existing allowed destination. The CLI
  states this and asks for confirmation. That is a candidate OD.

## Critical files

- **New:**
  - `src/ecf_server/corpus.py`
  - `src/ecf/cli_corpus.py`
  - `docs/adr/0022-real-mail-test-corpus.md`
  - `planning-docs/GENERATE-EMAIL-CORPUS-PLAN.md`
  - `tests/test_corpus*.py`
- **Changed:**
  - `src/ecf_server/mail/{__init__,imap,fake}.py`
  - `src/ecf_server/download_budget.py`
  - `src/ecf_server/api.py`
  - `src/ecf/cli.py`
  - `src/ecf_server/evalrun.py` (B)
  - `src/ecf/eval/*` (B)
  - `.gitignore`
  - `SPEC.md`
  - `CHANGELOG.md`
  - `CONTRIBUTING.md`
- **Reused:**
  - `manual_export.check_path` and the export pattern
  - `export_bundle.write_atomic`
  - `_age.encrypt_passphrase`/`decrypt_passphrase`
  - `passphrase.check`
  - `stepup.purpose`/`consume`
  - `ecf/stepup.with_step_up`
  - `prompts.hidden`
  - `addresses.login_and_probe` pattern
  - `models.start_install` job pattern
  - `download_budget`
  - `replay.fresh_message_id`

## Verification

- **Unit tests** (`uv run pytest tests/test_corpus*.py`, plus ruff, pyright and lint-imports):
  - Selection orders, caps (count, 512 MiB, half budget), chunk and sleep with a fake clock.
  - Step-up refused without a nonce and bound to its target.
  - Path checks (data folder, git tree, existing file).
  - One-off password never written (memory secret store stays empty).
  - Encrypt/decrypt round trip, manifest sha256 matches.
  - MCP/agent caller refused.
  - Gmail fake budget recorded.
- **IMAP tests** (`-m imap`, Dovecot): fetch from a seeded container, then replay into a second mailbox, then
  byte-identical message bodies (headers identical unless `--fresh-ids`).
- **macOS test:** the Touch ID step-up path with `MacStepper` follows the existing `ecf-test-*` conventions.
- **Full suite** once before asking to commit (`uv run pytest -n auto -rs`, 0 skipped).
- **Real-service test** (needs the operator's go-ahead at the time; code throwaway in the scratchpad): fetch 20
  messages from the `ecf-test-gmail` Keychain account (most-recent, then random), check the budget rows, replay
  into tmpfs Dovecot, run a shadow review, then label the 20 with `ecf eval label --corpus` and
  run `ecf eval run --corpus` (preset A). Results go in SPEC §21.1.
- **After each push:** `gh run watch <id> --exit-status`.
