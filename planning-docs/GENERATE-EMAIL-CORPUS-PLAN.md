# Plan: `ecf corpus`: real-mail test corpus (fetch, replay, eval)

Draft 5 (2026-10-06). It folds in four adversarial reviews, recorded in
`state-archive/corpus/corpus-review-findings.md` (gitignored):
- Phase R: R1-R44
- Phase R2: R45-R96
- Phase R3: R97-R146
- Phase R4: R147-R177

Tags like [R147] point to them.

R150, a production defect in `redact_injection` that R4 found, is being fixed on its own branch (`fix-redact-injection`) and is not part of this plan.

## Context

Evaluation today uses only the committed synthetic set (185 cards). The operator wants to test ecf on real mail, which
SPEC forbids in three places:
- §12.4: full messages are never written to disk (OD-108/109).
- §17.3: test mailboxes receive synthetic mail only.
- §16.6: end-to-end runs use synthetic mail only.

Operator decisions (2026-10-06):
- **§12.4 exception:** a corpus may exist on disk only **encrypted**.
  - **Operator's condition** [R45, R103]: it is made only from a mailbox the operator owns. ecf can't check this.
    - It is stated as a limit, as OD-427 is.
    - The operator confirms it by typing the source email.
  - It may be made on a prod or test install, behind Touch ID step-up and a Security Notice.
  - It is never made on an `ecf-server dev` service [R56].
- **Gating** [R10]: the corpus gates `v1.0.0` only through the decision-model experiment
  (`planning-docs/SYSTEMONE-MODEL-TESTING-PLAN.md`), whose adopt/not-adopt OD is the §1.5 criterion.
  - Phases 0, 1 and B are on the path to rc1.
  - Phase A (replay on `ecf-server dev`) is outside the gate.
  - The work is built on a `corpus` branch from `v1.0.0-release` and merged back before rc1.
- **Report only** [R8]: corpus results are reported and never gated.
  - The absolute safety gates stay on the synthetic set.
  - The corpus measures field accuracy (all eight schema fields), calibration and real-mail noise. It does not
    measure fraud recall.
  - Corpus safety flags are derived, so corpus and synthetic safety percentages aren't comparable [R101].
- **No Anthropic** [R3]: in v1, `--corpus` with `--claude` is refused, and so is `/ecf-eval` on a corpus.
- **Blind labels** [R6]: labelling shows no model output and happens before any replay or corpus run.
- **Sleep:** fixed seconds between batches, default 10.
- **Credentials:** `--address` (a watched address; Keychain) or a one-off prompt (never stored).
- Each phase starts when the operator names it. This plan is a working document; SPEC stays authoritative.

**Division of work with the systemone plan** [R69, R116]:
- This plan owns: the corpus commands, the label UI, the corpus case source, `merge`, `rescore`, confirmed-only
  `compare` and `compare_fields`, and the extended `per_field`.
- The systemone plan owns: the NI interval, Holm, its own metrics, the backend seam, the gating corpus's mailbox and
  N, the adjudication procedure, and the §21.1 row for the gating 500+ fetch.

**Build effort** [R118] (estimate, unverified until Phase 1 starts). It also goes into the v1.0.0 plan.
- Phase 1: 5-6 build sessions, with `merge` as its own session [R172].
- Phase B: 4-5 build sessions.
- Real-service test 1: one session, about 1 h of the operator's time.
- Labelling 500 messages: 8-17 h of the operator's time.
- Phase A: 2 sessions, after v1.0.0.
- Machine time comes from the systemone runs (that plan's schedule).

## Facts this design relies on

- **Gmail IMAP limits**, verified 2026-10-06 (knowledge.workspace.google.com/admin/gmail/gmail-bandwidth-limits):
  - "Download with IMAP: 2500 MB per day".
  - "A suspension typically lasts for 1 hour, but can last up to 24 hours."
  - These apply to all Workspace editions; the limit for personal accounts is unverified (§12.2, §21.2).
  - At most 15 IMAP connections. The corpus job adds a second session, so ≤ 2 per address [R38].
  - Gmail's folder size limit, when set, may also cap All Mail (unverified; the preflight shows EXISTS either way)
    [R134, R164].
- **Purelymail:** no recorded bandwidth or connection limit (unverified).
  - A refused connection is retryable.
  - SEARCH can return expunged UIDs (OD-450) [R38, R78].
- **Existing guards:** the Gmail budget is 2,500,000,000 bytes per rolling 24 h per address (`download_budget.py`),
  live fetch makes one FETCH per body, and `fetch.LARGE_BYTES` is 16 MiB.
- **Code facts from the reviews:**
  - **Mail layer:**
    - `ImapSource` always selects INBOX [R11].
    - imapclient's `append(..., msg_time=)` exists; no ecf append layer takes a date [R13].
    - `imaplib._MAXLINE` is 1,000,000 [R48].
    - imapclient wraps a LOGIN `NO` in `LoginError` [R130].
    - The `BODY[HEADER.FIELDS …]` response key is upper-cased, and an absent header comes back as a bare CRLF
      [R132].
  - **Isolation and analysis:**
    - `isolate.subprocess_isolator(db_path, dns_cap_s=, dns_budget=)` returns `(ParsedMessage, AuthOutcome)` in
      about 0.15 s per message (OD-204) [R97].
    - `MessageAnalyzer(conn, clock, AddressInfo, dns).analyze(parsed, raw, auth=, gmail_labels=)` returns facts and
      trigger facts. It runs in the parent in live fetch (`analysis.py:50-86`).
  - **Eval:**
    - `evalrun` computes facts in `ruletest.Scratch` with `_no_dns` [R1].
    - `evalrun.latest` has no set filter [R2].
    - `summarize` sets `gate_passed` [R54].
    - `RUNTIME_CAP_S` is 8 h [R143].
  - **Dev service and helpers:**
    - A dev service uses `FakeStepper` (`service.py:611`) [R56].
    - `ipaddress('::ffff:127.0.0.1').is_loopback` is True [R61].
    - `passphrase.check` accepts any five distinct words [R17].
    - pyrage works on whole buffers [R20].
    - A first check on a new address saves its cursor at UIDNEXT-1 and fetches nothing (`fetch.py:226-229`) [R108].
  - **Measured:** random member reads from a compressed tar take about 0.1-0.2 s each at 512 MiB [R135].

## Command surface (CLI token only; MCP gets nothing)

```
ecf corpus fetch --out PATH [--total 500] [--chunk 10] [--sleep 10] [--max-bytes 256MiB]
                 [--order most-recent|random|oldest] [--folder ROLE|NAME]
                 [--include-own] [--allow-spam] [--own-passphrase]
                 [--address ADDR | (prompts: email, IMAP host, app password)]
ecf corpus status | stop
ecf corpus info FILE                      # clear header (unverified) + labels count; no passphrase
ecf corpus merge A B [...] --out C [--allow-mixed]
ecf corpus replay FILE --user U --port P --tmpfs-bytes N [--host 127.0.0.1] [--fresh-ids]  # Phase A, dev only
ecf eval label --corpus FILE              # Phase B, blind authoring UI
ecf eval run --corpus FILE --classifier … --actor …   # Phase B, preset A only
ecf eval rescore RESULT --corpus FILE
```

- **Terminal** [R57]: every `ecf corpus` subcommand, and `eval label/run/rescore --corpus`, calls
  `require_terminal()` first. The passphrase belongs in a password manager only, never in a state, plan or
  scratchpad file.
- **Default order:** `most-recent` [R62].
- **Folder** [R26, R37, R73, R134]:
  - `--folder` takes a role (`inbox`, `all-mail`) or a name.
  - On Gmail the default is the `\All` role. If All Mail is hidden from IMAP, fetch refuses with the "Show in IMAP"
    hint.
  - Elsewhere the default is INBOX.
  - The folder name is resolved and validated against LIST.
  - `\Trash`/`\Junk` are refused without `--allow-spam`.
  - The preflight shows the folder's EXISTS. It adds the `gmail_inbox_counts` warning for INBOX on Gmail, and says
    that a Gmail folder size limit caps what is visible.
- **Own mail** [R22, R63, R137]: these are skipped and counted as `skipped_own`:
  - `\Draft`;
  - an `X-ECF-Install:` line in the returned header block (key looked up by the prefix `BODY[HEADER.FIELDS`)
    [R132, R148];
  - `\Sent` mail, **except** messages that also carry `\Inbox` and whose From (from the same header block, parsed
    with `email.parser`/`getaddresses`) matches the source address after `internal.fold`. These are notes to self,
    kept so `self_sent` stays measurable [R148].

  `--include-own` keeps them all.
- **Gmail mode** comes from `capabilities().gmail` after login (OD-438) [R42].

## Limits (recorded in OD-C2)

- **`--total`:**
  - Default 500, maximum 5,000.
  - The Phase B loader is limited to **1,500** messages until real-service test 1 measures seconds per message on
    real mail (two model calls each); the limit is then set from that rate within the 8 h `RUNTIME_CAP_S` [R143,
    R166].
  - A run that hits the cap is still usable: `compare` pairs the cases both runs completed.
  - Power: n=500 at 20% discordance gives a paired SE of about 2 points. The gating N is set by systemone's OD-465.
- **`--max-bytes`:** default 256 MiB, maximum 512 MiB.
  - The run **stops** at the cap (reason `byte_cap`). It never skips large messages, which would bias the sample.
  - The preflight shows how many messages fit within the cap [R128].
- **Gmail budget share** [R24, R64, R129]:
  - At preflight, S = floor(L0/2) is fixed and shown.
  - Before each message, the run stops if `corpus_bytes + next > min(max-bytes, S)` or `next > left()`. Bytes are
    recorded per message.
  - The preflight warns that live checks may stop on the budget until a stated time.
  - Off Gmail, `left()` is None.
  - `left()` for an address also sums `corpus_downloads` for its folded email.
- **`--chunk`:** default 10, range 1-100.
- **`--sleep`:** default 10 s, range 0-600. A NOOP is sent before each batch [R136]. The byte caps are what protect
  the account.
- **Retries** [R77, R130, R131]:
  - **5-try counter:** counts consecutive failures and resets after any successful command.
  - **Per-UID counter:** a UID that times out twice is skipped (`skipped_timeout`), and the connection counter
    resets.
  - **Login:** a `LoginError` on **re**connect is retried within the 5 tries. Only the first login counts as
    rejected.
- **Windows** [R133]: at most 50,000 UIDs per SEARCH window. Most-recent and oldest windows are sized from density
  (`2N × UIDNEXT / EXISTS`).
- **Facts:** each isolated child gets a DNS budget of 10 s (OD-C2) and the timeout `30 s + 3 s/MB`, with no retry.
- **Preflight figures are estimates** [R127]. The fetch call re-selects.
- **Run time** [R158]:
  - The preflight prints an estimate: `N × (sleep/chunk + about 1 s)`.
  - Typical (cached DNS): 500 messages in about 15 min, 5,000 in about 2.5 h.
  - Worst case (every child spends its 10 s DNS budget): 500 in about 1.6 h, 5,000 in about 15 h.

## Selection [R12, R36, R40, R48, R62, R78, R99, R127]

Selection is a **generator** that tops up between batches. Its candidates come from the order:
- **`most-recent` / `oldest`:**
  - Bounded `UID SEARCH UID a:b` windows, walked down from UIDNEXT or up from 1.
  - The whole window's lean meta is fetched first, then the window is ordered by INTERNALDATE [R160]. This differs
    from true date order only for APPENDed or imported mail (a stated limit).
- **`random`:**
  - First the folder's real UIDs are listed with the same bounded windows (≤ 50,000 UIDs per `UID SEARCH UID a:b`,
    about UIDNEXT/50,000 commands), held as `array('I')` (about 4 MB per million) [R149].
  - Then `secrets.SystemRandom().sample(uids, min(k, len(uids)))`, with k about 1.2 N, and top-ups drawn from the
    UIDs not yet sampled.
  - Exhausted means every listed UID has been sampled. UIDs that vanish between SEARCH and FETCH count as
    `skipped_gone`.
  - There is no UID-list SEARCH, so the length of a 500-UID command line doesn't arise [R159].

What happens to each candidate:
- **Lean meta** for each batch:
  - `RFC822.SIZE INTERNALDATE BODY.PEEK[HEADER.FIELDS (X-ECF-Install From)]`;
  - plus `X-GM-LABELS X-GM-MSGID` on Gmail.
- **Skips:** a candidate is dropped and counted when it is:
  - over `LARGE_BYTES`;
  - own mail;
  - gone (no meta row);
  - a duplicate (same label key, after fetch) [R51];
  - an isolation failure [R98];
  - a repeated timeout.
- **End states:**
  - Exhaustion: `complete: true`, `count < total`, reason `folder_exhausted`.
  - Cap: reason `byte_cap`.
- The folder is opened with EXAMINE, and `BODY.PEEK` sets no `\Seen`.

## Fetch-time analysis: what the manifest stores [R1, R47, R97, R98, R102]

Each fetched message is handled in this order:
1. **Isolated parse, auth and excerpts:** the bytes go to the OD-204 child,
   `isolate.subprocess_isolator(path, dns_cap_s=600, dns_budget=lambda: 10.0)` [R147, R152].
   - `max_scan_bytes` is `address_config(...).max_scan_bytes` with `--address`, otherwise `DEFAULT_SCAN`.
   - The child returns the `ParsedMessage`, the `AuthOutcome` and the **excerpts**, computed inside the child under
     its timeout by a new shared helper, `message.excerpts(parsed)`:
     - the redacted classifier and actor excerpts;
     - the unredacted cuts at the same limits, for "show redacted".
   - `isolate.encode` gains the excerpts.
   - Fetch, evalrun, `claude_eval` and the corpus all call that helper. The parent never calls `excerpt(...,
     redact_injection)`.
   - If isolation fails or times out, the message is **dropped** (`skipped_facts`) and never enters the tar.
2. **Analysis in the parent, as live fetch does it:** `MessageAnalyzer(conn, clock, AddressInfo, dns).analyze(parsed,
   raw, auth=…, gmail_labels=…)` runs against the install's **real** state.
   - With `--address`, it uses the watched address, so sender history, org sets, vendors, providers and trigger 5
     are real.
   - With a one-off source, it uses `AddressInfo(address_id="corpus-oneoff", email, sensitivity="standard")`, so
     there is no history, vendors or reuse.
   - Confirmed in R4: the analyzer is read-only when `auth=` is passed and `record()` is never called. With `auth=`
     the parent's DnsCache is unused, and the child writes `dns_cache` rows as live fetch does [R152].
   - A one-off source is still analysed against the **install's** org sets and watched providers. Rules that
     depend on impersonation are reported separately for one-off corpora (a stated limit) [R167].
3. **Stored in the manifest row:**
   - the facts, under these keys: `auth.facts()`, the `facts.compute` keys, `ecf_mail`, `alert_echo`,
     `Triggers.facts()` [R152];
   - the child's excerpts (redacted and unredacted cuts) [R147];
   - display headers (From, Reply-To, To, Subject, Date) and attachment names (capped);
   - the label key, Message-ID, `fetched_at`, and the Gmail meta.
4. **Raw bytes** go into the streamed tar. They are kept only for replay and re-analysis.

**Phase B never parses.** Labelling, expected values, runs and rescore read only the manifest. Any future re-analysis
goes through the isolator again.

**Fact states, defined per corpus** [R102]:
- An isolation failure means the message is not in the corpus.
- **One-off corpus:** the rule is scored with empty history. Results are reported as "without history".
- **`--address` corpus:** the rule is scored. A first-time sender is correct history, not missing history.
- Rule accuracy is reported by corpus source.

**Stated limits (§16.1):**
- History, vendors and org sets are the install's state at fetch, not at arrival. The error runs both ways [R169]:
  - Mail older than the watched address has no history, so it looks more first-time than it was.
  - An established sender's early messages count as seen.
  - The summary reports the share of messages whose INTERNALDATE precedes the address's `created_at`.
- Auth uses today's DNS, so old mail can lose its DKIM keys. A histogram of `none` reasons in the header shows this.

## Security model

- **Owner** [R103]:
  - The fetch confirmation asks the operator to type the source email. It must match after `internal.fold`.
  - The dialog says "from a mailbox you own".
  - §12.2 states that ecf can't check this.
- **Two-call flow** [R92, R123]:
  - `POST /v1/corpus/preflight`:
    - steps: login, capabilities, LIST, EXAMINE, a first candidate pass;
    - returns an informational summary;
    - not `login_and_probe`: no read-write SELECT, no SMTP [R72].
  - `POST /v1/corpus/fetch`:
    - gets the raw inputs and the nonce, logs in, **resolves the folder again**, and builds its own step-up target;
      no preflight output is trusted;
    - its order is resolve → `stepup.consume` → generate the passphrase → spawn the job → respond. A call without a
      nonce stops at `consume`, returns no passphrase and starts nothing (tested) [R175];
    - its **immediate response returns the generated passphrase**, before any byte is fetched [R105];
    - the one-off password field is named `app_password`, so log redaction applies;
    - the job keeps the passphrase in its closure until encryption, never in `Progress` or status.
  - The service caches no one-off password and no candidate list across requests.
  - `_check_password` validates a typed password.
- **Step-up purposes:**
  - **`corpus_fetch`:**
    - bound to `{source email, resolved folder, host, port, out path, total, max-bytes, order}`, never the password;
    - dialog order: count, source, host, then the path (head…tail); the CLI prints the full path first [R95];
    - every input is collected before the first step-up action [R33].
  - **`corpus_merge`** [R107, R173]:
    - bound to `{sorted source file sha256s, out path}`; the file hash covers both header and payload;
    - nothing is decrypted before the step-up;
    - the dialog shows clear-header counts, marked unverified.
- **Dev refusals:** `ecf-server dev` refuses corpus **preflight, fetch and merge** (`state.dev is not None`) [R56,
  R106].
- **Dev service hardening** [R106]:
  - Before constructing `Service`, `_run_dev` checks the `--home`. If `<install>/ecf.db` exists, it opens it read-only
    and refuses when the `install_role` setting (`initsetup.ROLE_KEY`) is set at all, whether prod or test. Dev homes
    are never `ecf init`ed [R153].
  - With `--imap-cafile` set, the dev service refuses every mail connection whose host fails the replay host check.
    The CA is never trusted for a real provider.
- **Replay controls** (dev only): the dev step-up is a fake (FakeStepper). Replay's real controls are:
  - the passphrase;
  - the IPv4 `127.0.0.1` target;
  - tmpfs Dovecot with `--rm`;
  - a RAM-disk `--home`.
  The `--home <prod root>` residual is closed by the prod-role refusal above; what remains goes in §12.2.
- **Authorized administrator:** in v1 the OS user is the sole admin (OD-007, OD-013).
  - Every `/v1/corpus*` route must be decorated, and a test asserts it [R176]. Every corpus route is
    `@allow(Caller.CLI)`: preflight, fetch, status, stop, merge, and the session, label, run
    and rescore routes. MCP and agent callers are refused (tested) [R126].
  - `ecf claude` cannot run `ecf corpus` [R35].
  - M2 note: corpus commands will need the admin role.
- **Credentials:**
  - With `--address`, the Keychain password comes through the `checks.py` closure.
  - A one-off password goes through `prompts.hidden()` and over the socket. It is never stored, and never in argv,
    env or on disk.
  - A typed email that matches a watched address is refused with "use --address <id>" [R23].
- **Passphrase** [R17, R125]:
  - Generated by default (six words) and shown once on a terminal.
  - `--own-passphrase` requires ≥ 5 words of ≥ 3 chars each and ≥ 24 chars in total.
  - The JSON field is named `passphrase` so the log redaction applies.
  - After decryption the passphrase is **dereferenced**. Python strings can't be wiped; this is a stated limit, as
    for export passphrases.
  - The real-service test measures decrypt time.
- **Output path** [R32, R94]:
  - Checks: `manual_export.check_path`, which gains a `suffix` parameter (it hard-codes `.ecfb` today) [R176]; suffix
    `.ecfcorpus`; not the data folder, not a symlink.
  - Refused inside a git work tree (`.git` as a directory or a file) and in `~/Library/Mobile Documents`.
  - Warns on other sync folders, and on Desktop/Documents when iCloud sync is on (detection unverified).
  - Written with `write_atomic` (0600, O_EXCL). The `.partial` file is the encrypted output, so plaintext never
    touches disk [R76].
- **`.gitignore`:** `*.ecfcorpus`, `*.ecfcorpus.labels.jsonl`, `.*.ecfcorpus.partial` [R90].
- **Memory** [R20, R46, R104, R109]:
  - **Fetch:** a streamed `w|gz` tar into a `BytesIO`, raw bytes dropped after each add, only exception type names
    kept.
  - **`CorpusSession`** (label, run, rescore, replay):
    - Decrypts once, keeps the compressed tar, and reads members per case.
    - The id comes from `new_random_id()`. One session at a time (`ConflictError`); label and run are serial.
    - For `label`, the CLI calls `DELETE /v1/corpus/session/<id>` in `finally`, which also covers SIGINT. The idle
      timer covers SIGTERM and SIGHUP [R154].
    - For `eval run --corpus`, the **run job owns the session** and releases it when the run ends. `ecf eval run`
      returns as soon as the run starts.
    - A DELETE on a busy session is refused with `ConflictError`, unless it carries `?stop=1`, which stops after the
      current case. `ecf eval stop` does the same.
    - The service timer expires a session that is not busy and has been idle for more than 15 min.
    - After expiry, `eval label` re-prompts for the passphrase and resumes from the labels file.
    - `GET /v1/corpus` shows the session's age and whether it is busy.
    - A service restart drops the session.
  - **Merge:** one source at a time [R163]:
    1. Scan each source to its `manifest.jsonl`, which is written last in the stream.
    2. Build the cross-source key set.
    3. Stream the source's members into the output tar, then release it.

    The output is refused above 512 MiB of message bytes.
  - **Measurement:** `footprint` peaks for fetch at 512 MiB, for a session, and for a two-source merge go in §12.2.
  - **Doctor:** checks encrypted swap and that core dumps are off. Both are stated as assumptions in §12.2.
- **Notice and audit** [R31, R58, R121]:
  - The Security Notice names the path.
  - `corpus.fetched` stores counts, bytes, order, `corpus_id` and the file sha256.
  - The source is `address_id`, or for a one-off `sha256(fold(email))[:12]` plus the domain. SPEC says this pseudonym
    can be reversed by guessing.
  - The nonce target residual goes in §9.6 [R89]. Logs follow OD-165.
- **Labelling display** [R21, R66, R122]:
  - Shown: the stored display headers, the auth result, attachment names, and the stored excerpt (about 1,500
    chars, "more" on request).
  - Everything passes through `text.plain`.
  - Injection-redacted spans show `INJECTION_MARK`. A "show redacted" key shows the stored **unredacted cut** at the
    same limit. Its end may differ from the redacted cut's. Reveals are counted [R147].
  - The label UI runs on the terminal's alternate screen (`\x1b[?1049h` … `\x1b[?1049l`, as `less` does), so the
    excerpts stay out of scrollback and are cleared on exit [R155].
  - The CLI says to label in Terminal, not in a Claude session, and advises turning off "Restore windows" for that
    terminal.
  - The saved-state and scrollback residual goes in §12.2.
- **Other housekeeping:**
  - `ecf destroy` reminds that `.ecfcorpus` and labels files are kept [R93].
  - `corpus_downloads` is listed in `export_bundle.EXCLUDED` and pruned like `downloads` [R89].

The reveal works without reparsing because the manifest stores the unredacted cut, inside the encrypted payload. That
adds nothing beyond the raw `.eml` already in the same tar [R147].

## Corpus format

- **Clear header** [R39, R91, R119]:
  - format version, `corpus_id`, created_at, ecf version, source domain, folder role, order, count, bytes;
  - skipped counts and `complete` with its reason;
  - `source_preset`, the `none`-reason histogram, and the source ids for a merge.
  - It is **unauthenticated until decrypt**: `info` prints "header unverified".
  - Every consumer that decrypts takes counts and ids from the manifest, and refuses when `sha256(clear header)` in
    the manifest doesn't match.
- **Encrypted payload:**
  - a streamed `tar.gz` of `NNNNN.eml`, `manifest.jsonl` and `profile.json`, age-encrypted;
  - the manifest carries `install_id` [R107]. No `creator`: the OS user name is often a person's name, and the
    `corpus.fetched` audit already records who [R174];
  - the payload is protected by age's AEAD; the hash in the manifest covers only the clear header [R177].
- **Manifest row:**
  - **Position and size:** index, uid, uidvalidity, folder, internaldate, size.
  - **Integrity and identity:** sha256 for integrity; the **label key** = `content_hash` + `identity_digest`;
    Message-ID.
  - **Gmail:** the Gmail meta.
  - **Fetch-time analysis:** everything from the section above, plus `fetched_at`.
  - With `--allow-mixed` merges, the profile is per row.
- **`profile.json`:** email, provider, sensitivity, org_domains, org_addresses.
- **Labels file `<corpus>.labels.jsonl`** [R28, R86, R90, R146]:
  - 0600, written from the CLI with O_EXCL then rename;
  - rows sorted by key;
  - schema: label key, `corpus_id`, the schema fields, an `s`/`u` mark, `confirmed`, and `date`;
  - `date` is the authoring date and changes only when the content changes, so the hash is deterministic;
  - nothing derived from text;
  - its `corpus_id` is checked against the verified manifest;
  - a `content_hash` version bump orphans the file.

## Review history

- **Phase R** (draft 1): 44 findings.
- **Phase R2** (draft 2): 52 findings.
- **Phase R3** (draft 3): 50 findings, of which 6 high.
- **Phase R4** (draft 4): 31 findings, of which 4 high. One of the four, R150, is a production defect and is fixed
  separately.

The operator decided every finding, and this draft carries the fixes.

## Phase R5: full review of draft 5 (before any code or SPEC commit)

The operator chose a full three-lens review, under the same rules as before.
- **Reviewers:** read-only, `model: "fable"`. Each checks that every R1-R177 fix is present and correct.
- **Findings:** numbered from R178.
- **Gate:** every critical and high finding is fixed in the next draft (the operator confirms) or rejected by the
  operator with a recorded reason. Medium and low findings go to the operator. A material change triggers another
  round.
- **Then:** Phase 0, then code, each started when the operator names it.

## Phase 0: decision and SPEC (first commit, after operator OK)

**OD numbers:** OD-C1, OD-C2 and OD-C3 are provisional. They are numbered at the Phase 0 commit from the next free OD,
because OD-461 is already taken (No Haiku pins, `684427a`). The systemone plan's provisional OD-464 onward must be
checked against the same list.

- **OD-C1:** the §12.4 exception.
  - Encrypted corpus files.
  - Operator-owned mailboxes as the operator's condition, which ecf can't check.
  - Prod or test installs, never dev; step-up and a Security Notice.
  - Phase A excerpts only on a RAM-disk dev home.
  - No Anthropic in v1.
- **OD-C2:** limits, defaults, selection, the budget share, `corpus_downloads`, the facts DNS budget, windows,
  retries, the 2,000 loader limit, and the power statement.
- **OD-C3:** replay to IPv4 loopback Dovecot only, from `ecf-server dev`:
  - tmpfs mail home and `--rm`;
  - a RAM-disk `--home`;
  - `--imap-cafile` dev-only and loopback-only;
  - dev refuses a prod home.
- **ADR 0022** "Real-mail test corpus".
- **SPEC edits:**
  - §12.4: the exception. A corpus is a new place content is stored, and the operator reads it; destinations are
    unchanged.
  - §12.2:
    - memory figures;
    - the swap and core-dump assumptions;
    - residuals: the ssh forward, the dev `--home`, Colima swap, Python strings;
    - the owner limit.
  - §17.3 and §16.6: replay only from a dev service.
  - §16.1, the real-mail set:
    - blind labels and the label key;
    - stored fields (labels, hashes, closed-vocabulary model fields, never the actor's `reason`) [R96];
    - the fetch-time analysis limits;
    - derived safety flags;
    - the folder size limit.
  - §14.3: the corpus counts against the Gmail budget.
  - §9.6: the step-up list and the nonce residual.
  - §10.2: the commands.
  - §15.2 and §12.4: the audit pseudonym.
  - §23.4: the OD rows.
  - **§1.5 item 2** gains "except §21.1 rows marked 'after v1.0.0'" [R112].
  - **§21.1:**
    - the fetch/label/run mechanics test, which gates;
    - the replay test, marked "after v1.0.0".
- **Other docs:**
  - `docs/gmail-setup.md`: a corpus comes only from your own mailbox.
  - `.claude/rules/eval-synthetic.md` and `GENERATE-FAKE-TESTING-EMAILS.md`: corpus content never feeds cards.
- **Systemone plan:** its session edits it against this final text, with operator OK [R81, R116].
  The items cite anchor text, not line numbers [R170].
  - "after the corpus plan's Phases 0, 1, A and B" → "Phases 0, 1 and B".
  - "(requirements on the corpus plan's Phase B; that plan's Phase R takes them as input)" → point to this draft.
  - Renumber OD-467 or state the gap.
  - OD-465 names the gating mailbox and requires `--address` on an install with history [R67].
  - "derived from the labels by `ecf rules test`" → `corpus.expected` (through `policy.plan`).
  - "their hash becomes the set version" → `set_version` = `corpus:<id>:<labels hash12>`.
  - "a second fetch … tops the corpus up" → `ecf corpus merge`.
  - Its Phase 2 items for `eval label --corpus` and confirmed-only `compare` → owned by this plan.
  - Its OD numbers are checked against the next free OD, as this plan's are.
  - Adjudication uses `ecf eval rescore`, and every arm is rescored before `compare` [R113].
  - Label wording matches the display above [R66].
  - Its schedule ("Schedule estimate") gets the build effort above.
  - A §21.1 row for the gating 500+ fetch.
- **CHANGELOG**, one line per change, each citing OD-C1 to OD-C3 [R30, R84, R142]:
  - `ecf corpus fetch/status/stop/info/merge`;
  - the §12.4 exception;
  - Gmail budget sharing;
  - `ecf eval label/run/rescore --corpus`;
  - `compare` counts confirmed labels only (synthetic comparisons too; recorded figures aren't recomputed) [R144];
  - `ecf eval` reports all eight fields;
  - `ecf destroy` reminds about corpus files;
  - the doctor swap and core-dump checks;
  - `ecf corpus replay` and the `ecf-server dev` options (dev).

## Phase 1: `corpus fetch` (service job)

- **New `ecf_server/corpus.py`:**
  - The job follows `models.start_install`: Progress, `ConflictError`, `spawn`, a `stop` Event, `clock`.
  - Order: preflight → step-up → login → EXAMINE → the selection generator.
  - Per message:
    1. FETCH, then record the budget **at once**.
    2. Isolated parse and auth.
    3. Parent analysis.
    4. Manifest row.
    5. Add to the streamed tar.
  - Then encrypt, write, notice, audit.
- **Read-only corpus reader** over `lib.Conn` [R11, R80]:
  - EXAMINE; records UIDVALIDITY, UIDNEXT and EXISTS.
  - On reconnect: a fresh `Conn`, re-login, capabilities, EXAMINE, then compare UIDVALIDITY.
  - Same exception set as `ImapSource`.
  - `ImapSource` stays INBOX-only.
- **Interruptions** [R14, R25]:
  - Resume at the next unfetched UID.
  - A UIDVALIDITY change, `stop`, or an unrecoverable error writes a partial corpus (`complete: false`, with the
    reason) if at least one message was fetched.
  - A restart discards the run.
  - The CLI says to use AC power and prevent sleep.
- **`download_budget.py`:**
  - The address id is used with `--address`.
  - New table `corpus_downloads (email_norm, hour, bytes)` (migration, no FK, `internal.fold`).
  - `used_email`/`left_email` sum it with the `downloads` of matching addresses.
  - `left()` for an address adds `corpus_downloads` for its folded email.
- **Routes:**
  - `POST /v1/corpus/preflight`, `/fetch`, `/stop`, `/merge`;
  - `GET /v1/corpus`;
  - the session routes.
- **CLI:** `ecf/cli_corpus.py` with `make_corpus_app(paths)`, registered next to `models`.
- **`corpus info`:** header and labels count only.
- **`corpus merge`** [R107, R109, R115]:
  - One passphrase per source, a new passphrase, and the `corpus_merge` step-up.
  - Sources are streamed one at a time, de-duplicated by key and re-indexed. Source ids go in the header; the folder
    and source are kept per row.
  - Mixed source mailboxes are refused without `--allow-mixed`.
  - Labels files are merged by key. Conflicting labels for one key are refused and listed. `corpus_id` is rewritten
    and `confirmed` is kept.
  - A top-up before labelling costs nothing. One after the G run forces a re-run.

## Phase B: blind labelling, eval runner, rescore (gates `v1.0.0` via systemone)

**`ecf eval label --corpus FILE`** [R5, R6, R68, R71, R140, R141]:
- **Session and display:**
  - Runs in a `CorpusSession` and uses the display above, in random order.
  - Never loads a result file (tested). The synthetic `eval label` default (`results.latest`) never picks a corpus
    file.
- **Input:**
  - An authoring UI with the closed vocabulary from `schema_v1.yaml` for every schema field.
  - `s` (skip) and `u` (unsure) apply **per message**. Both are excluded from scoring and counted. Trade-off,
    stated: an unsure message loses its other labelled fields.
  - Resumable.
- **Pre-fill:** only facts that are not label fields are shown, never pre-filled into a label field.
  - Keyword hits appear as information.
  - Measured: the share of keyword-hit messages labelled `payment_related: true`, as a bias check.
- **Confirmed** = every field authored, and the label key matches the verified manifest. Author = `operator`.
- **Time:** about 8-17 h for 500. Label 500 first. A top-up is optional and never blocks rc1 [R88].

**Expected values: `corpus.expected(row, labels)`** [R50, R100, R101]:
- Runs the pipeline's own path over the **stored facts**, with the labels as the classification:
  `policy.plan(policy.Context(labels, facts, sensitivity, rules, {}, frozenset()), known)`, as evalrun does [R165].
  - Returns `rule` (including `alert_echo`) and actions.
  - `hide: never` comes from `rules.evaluate(...).hide`.
- `must_escalate` = the derived rule is `fraud_guard` or `regulatory`, or the actions include escalate.
- `must_not_hide` is true when any of these holds [R156]:
  - `must_escalate`;
  - the derived rule is `hide: never`;
  - fraud_risk ≥ medium;
  - payment_related;
  - impersonates_internal;
  - `requires_reply` (OD-250);
  - `requires_action` (OD-250).

  Checked against the 185 synthetic labels, the formula disagrees with 1 card (`home-giftcard-thanks`). The Phase B
  test asserts that count.
- `injection_target` is never set (a stated limit).
- Derived only when every field the policy reads is labelled.
- **End-to-end correctness** is computed only over confirmed cases with a derived rule (`rule_scored` in the summary).
  A category-only figure is reported alongside. `compare` pairs cases that both runs scored.
- **Unlabelled cases** get `confirmed=False` and `scored: false`. `results.summary()` (the A/B headline of
  `ecf eval compare`) joins `compare` and `compare_fields` in counting confirmed labels only [R157].

**`ecf eval run --corpus FILE --classifier … --actor …`:**
- **Scope:** preset A only; `--claude` is refused. It runs **every** message in the corpus, labelled or not, and
  records `got` and `actions` for all of them. Labels affect scoring only [R114].
- **Case source:** gives the stored excerpts and stored facts and never parses. `evalrun._run` takes a case source
  that provides `(excerpts, facts, sensitivity)` instead of `case.path` bytes [R139].
- **Run separation** [R2, R54, R83]:
  - The result goes only to `<data_dir>/evals/corpus/<corpus_id>/<run_id>.json` (0600).
  - No `eval_runs` row, no `_remember_root`.
  - `gate_passed` is forced false. The `eval.completed` audit row carries `set_version`.
  - `set_version` = `corpus:<corpus_id>:<labels hash12>`. `results.compare` refuses mixed sets.
  - `eval status` (current) and the Slack line mark the run "corpus".
  - `_save` is split into a file part and a DB/audit part.
- **New fields** [R117, R138]:
  - `summarize` `per_field` covers all eight schema fields plus `rule_scored`.
  - The run summary records `labels_hash` (the labels file's full sha256) [R168].
  - `CaseResult.actions: list[str] = []` records post-policy action names, the actor's proposal included.
    `plan is None` if and only if `got["rule"] is None`.
- **Keep location** [R85]: the operator copies each result the OD cites to a named folder outside git (0600). The OD
  records each sha256.

**`ecf eval rescore RESULT --corpus FILE`** [R53, R113, R120]:
- Refuses a result whose `corpus_id` differs.
- Recomputes the expected values from the current labels and scores from `got` and `actions`. No model re-runs.
- Writes `<run_id>-rescore-<labels hash12>.json` beside the original and keeps the original.
- Copies `digest`, `pair` and `labels_hash`, forces `gate_passed` false, and records `rescored_from: {run_id,
  sha256}`.
- After adjudication **every** arm is rescored before `compare`.

**`ecf eval compare`** takes result paths. It, `compare_fields` and `results.summary()` score confirmed labels only.

## Phase A: replay on `ecf-server dev` (outside the gate; after v1.0.0)

Phase A checks mechanics only.
- **RAM disk** [R59, R124]: Before starting, `sysctl vm.swapusage` must show encrypted swap.
  - Create `hdiutil attach -nomount ram://…`, formatted APFS.
  - Afterwards, `diskutil eject` it and confirm it is gone.
  - Note how `--keep` behaves under `--home`.
  - If `ECF_DEV_MODEL=1`, the Ollama request-log check runs on dev too.
- **Dev service:** `ecf-server dev --home <RAM disk> --imap-cafile <Dovecot CA>` [R60, R106].
  - `--imap-cafile` works only on dev, for loopback hosts only.
  - The service refuses a prod home.
- **Target** [R4, R61, R74, R111]:
  - Dovecot on IPv4 `127.0.0.1` only (the cert SAN, `_HOST`), with the whole mail home on tmpfs, `--rm`, Colima
    `--memory 4` or more, and tmpfs size 2 × the corpus bytes.
  - Replay takes `--tmpfs-bytes` and, after decrypt, refuses when `2 × sum(manifest.size)` exceeds it [R162].
    APPENDLIMIT is a secondary check.
  - The mail path is verified in Phase 1.
- **`ecf corpus replay`:**
  - Runs only on a service started as `ecf-server dev`.
  - Host check: `ip = ip_address(host)`, accepted only when `ip.version == 4 and ip.is_loopback and str(ip) == host`
    [R151]. Version 4 already excludes `::ffff:127.0.0.1`, which is an `IPv6Address`.
  - TLS keeps `CERT_REQUIRED` and `check_hostname`.
- **Fidelity** [R13, R15, R75]:
  - An optional `when` on `Conn.append`, `MailSource.append` and `ImapSource.append` (`msg_time=when`), and in both
    fakes. A fake INBOX append lands in the INBOX store.
  - The contract asserts `meta(...)[uid].internaldate == when`.
  - Message-IDs are kept, and each replay uses a fresh dev service. `--fresh-ids` is for load tests only.
- **Order** [R108]:
  1. `ecf address add` the Dovecot account (IP-literal host).
  2. Its first check, which saves the cursor.
  3. Replay.
  4. Let ticks run: 30 messages per tick, so 17 or more ticks for 500.
  5. Check that every message was fetched, classified and actioned without errors, and compare the auth distribution
     with the manifest.
- **Cleanup:** eject the RAM disk and remove the container.

## Critical files

- **New:**
  - `src/ecf_server/corpus.py`
  - `src/ecf/cli_corpus.py`
  - a migration for `corpus_downloads`
  - `docs/adr/0022-real-mail-test-corpus.md`
  - `tests/test_corpus*.py`
- **Changed:**
  - `src/ecf_server/mail/{_imapclient,__init__,imap,fake}.py`
  - `download_budget.py`
  - `export_bundle.py`
  - `api.py`
  - `__main__.py` and `service.py` (the dev options and refusals)
  - `evalrun.py` (case source, `_save` split, `gate_passed`, `actions`, `per_field`, unlabelled cases)
  - `isolate.py` (excerpts in the child's output) and `message.py` (`excerpts` helper); `fetch.py` and
    `claude_eval.py` switch to the helper
  - `manual_export.py` (`check_path` suffix parameter)
  - `src/ecf/eval/results.py`
  - `src/ecf/cli.py`
  - `cli_destroy.py`
  - doctor
  - `.gitignore`
  - `SPEC.md`
  - `CHANGELOG.md`
  - `CONTRIBUTING.md`
  - `docs/gmail-setup.md`
  - `.claude/rules/eval-synthetic.md`
- **No longer changed:** `ruletest.py` (no Scratch variant; R97) and `cards.py` [R82].
- **Reused:**
  - `isolate.subprocess_isolator`
  - `analysis.MessageAnalyzer`
  - `manual_export.check_path`
  - `export_bundle.write_atomic`
  - `_age`
  - `passphrase`
  - `stepup`
  - `ecf/stepup.with_step_up`
  - `prompts.hidden`/`require_terminal`
  - `models.start_install`
  - `download_budget`
  - `internal.fold`
  - `message.content_hash`/`identity_digest`
  - `text.plain`
  - `triggers.redact_injection`
  - `fetch.LARGE_BYTES`
  - `policy.plan`
  - `results.compare`
  - `new_random_id`

## Verification

**Unit tests:** `uv run pytest tests/test_corpus*.py` plus the touched files; ruff, pyright and lint-imports.

**Fetch:**
- **Selection:**
  - windows with a fake that refuses lines over 1 MB;
  - uniform random sampling with `tried` and exhaustion;
  - `k` larger than the population;
  - the generator's top-up;
  - each skip counter: own (labels, header, notes-to-self kept), gone, duplicate, timeout, facts.
- **Caps:** count; `byte_cap` (a stop, not a skip); the S rule, including `next > left_now`; the budget recorded per
  message under both keys; `left()` including `corpus_downloads`.
- **Retries:** the counters; LoginError on reconnect; a NOOP before each batch; a partial corpus on stop and on a
  UIDVALIDITY change.
- **Analysis:**
  - A crafted message times out in the child and is dropped.
  - A crafted injection paragraph is redacted inside the child, never in the parent.
  - Stored facts equal live analysis on the same bytes and install state.
  - Stored excerpts equal `message.excerpts(parsed)` [R171].
  - `gmail_labels` → `self_sent` [R171].
  - The one-off address info is used for one-off sources.
  - Own-mail detection from the `X-ECF-Install` line, and notes to self kept by From [R148].

**Security:**
- **Step-up:**
  - fetch and merge are bound to their targets;
  - fetch recomputes the folder itself;
  - every prompt comes before the step-up.
- **Dev refusals:**
  - corpus preflight, fetch and merge are refused on dev;
  - dev refuses a prod `--home`;
  - `--imap-cafile` refuses a non-loopback host.
- **Terminal:** `require_terminal` on every corpus command.
- **Paths:** the path checks; the labels-file 0600 and O_EXCL.
- **Credentials:** a one-off password is never stored. A watched email is refused. The owner email must be typed.
- **Passphrase:**
  - generated by default and returned at once;
  - never in `GET /v1/corpus`;
  - `--own-passphrase` enforces its rule.
- **Audit:** no email, no path.
- **Crypto:** a round trip. A header hash mismatch is refused. `info` prints "unverified" and no subject or sender.
- **`CorpusSession`:** one at a time; DELETE on exit; idle expiry; resume after expiry.
- **Callers:** every corpus route refuses MCP and agent callers.

**Phase B** [R70]:
- `corpus.expected`:
  - with perfect labels it equals the pipeline's rule, including an alert-echo row [R171];
  - `must_not_hide` agrees with all but 1 of the 185 synthetic labels.
- Unlabelled cases are not scored, and `results.summary()` counts confirmed labels only.
- `rule_scored` and end-to-end are computed over scored cases only.
- A run covers every message.
- A labels edit gives a new `set_version`. `rescore` equals a re-run on a fixture, and refuses a corpus mismatch.
- `merge`: de-duplication, re-indexing, label conflicts refused, the mixed-source refusal.
- The synthetic `eval label` never picks a corpus file. `label --corpus` loads no result file.
- Unconfirmed, unsure and mismatched labels are not counted.
- `--corpus --claude` is refused.
- **Run separation:**
  - `gate.synthetic` is unchanged;
  - no `eval_runs` row, and `EVAL_ROOT` unchanged;
  - `gate_passed` is false;
  - mixed sets are refused;
  - the status line shows "corpus";
  - `per_field` has all eight fields.

**Replay:**
- **Hosts refused:** `localhost`, `127.1`, `::ffff:127.0.0.1`, `::ffff:7f00:1`, `::1`, `[::1]`, `0.0.0.0`.
- **Refusals:** a non-dev service; a corpus too big for the tmpfs.
- **`when`:** reaches the fakes and the contract.
- **IMAP tests** (`-m imap`, Dovecot, also on Linux CI):
  - fetch by role, windows, random sampling;
  - replay with INTERNALDATE kept;
  - byte-identical messages;
  - `address add` with `127.0.0.1` and `--imap-cafile` on dev;
  - replay after the first check.

**macOS:** the scripted MacStepper test for each purpose, as `tests/test_stepper.py` does. Then the full suite once
before asking to commit (`uv run pytest -n auto -rs`, 0 skipped).

**Real-service test 1** (gating mechanics) [R79, R110, R145]:
- **Conditions:** needs the operator's go-ahead at the time. Its code is throwaway, kept in the scratchpad.
- **Install:** a test install that watches `ecf-test-gmail` with `--address`. The operator names or creates it at
  the time; the shadow unit is uninstalled.
- **Message count:** the preflight checks EXISTS first. Below 40 messages, either APPEND synthetic mail with fresh
  Message-IDs (`ecf.replay.fresh_message_id`), so EXISTS actually grows (allowed by §17.3), or accept
  `folder_exhausted` as a pass [R161].
- **Rate:** measure seconds per message for `eval run --corpus` and set the loader limit from it [R166].
- **Fetch:** 20 messages most-recent, then 20 random.
- **Forced disconnect:** an outage of 70 s or more, recording which path ran.
- **Budget:** a throwaway script pre-seeds `downloads` by address_id, so the half rule binds. Live checks of that
  address stop while it is in place, and the "Gmail download limit reached" alert is expected. The rows are removed
  afterwards, which clears the alert [R161].
- **Also exercised:**
  - a forced stop leaves a partial file;
  - budget rows;
  - decrypt time and the `footprint` peaks;
  - labelling the 20 blind, timed;
  - `eval run --corpus` (preset A);
  - `rescore` after one label change;
  - a two-file `merge`.
- **Results:** its own §21.1 row.

**Real-service test 2** (replay): after v1.0.0, with its own §21.1 row.

**The gating 500+ fetch:** in the systemone plan, with its own go-ahead and §21.1 row.

**After each push:** `gh run watch <id> --exit-status`.
