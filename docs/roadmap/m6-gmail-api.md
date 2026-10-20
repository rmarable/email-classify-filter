# M6 Gmail API (personal Google accounts)

**Status: draft 2, 2026-10-16. Not started.** Phase R ran on draft 1 (four agents, findings P1-P29
below); the operator accepted every recommendation (2026-10-16). Started ahead of M1-M5 (operator
decision 2026-10-16, OD-527, amends OD-004). Released as a minor version, numbered when it ships (OD-531, OD-533). Replaces the outline
`planning-docs/M6-GMAIL-API.md` (OD-426, OD-525). Google Workspace accounts are M7, a separate
milestone, not planned here; M6 is built so M7 can reuse its adapter and sign-in without a rewrite
(P5).

Real-service tests need the operator's go-ahead each time; their code stays in the session
scratchpad. Code references are as of `e7f6d33` (v3.0.4).

## Scope

Personal Google accounts (gmail.com, googlemail.com) reached through the Gmail API with OAuth, as
an alternative to IMAP with an app password (V1.6, OD-425), chosen per address. IMAP stays
supported, for Gmail and for every other provider.

## Decisions (operator, 2026-10-16; SPEC OD-527 to OD-531)

- **Order (OD-527):** M6 starts before M1-M5; amends OD-004, as OD-421 did for M5.
- **Own Google Cloud project (OD-528):** each operator creates a Google Cloud project and a
  Desktop OAuth client; ecf ships no shared client. Personal use (fewer than 100 users) needs no
  OAuth verification, so no security assessment. Accepted with it: Google's unverified-app
  screen at sign-in, a cap of 100 users per project, and the project must be published "In
  production" (a project in "Testing" gets refresh tokens that expire after 7 days).
- **Access per address (OD-529):** `addresses.access` (`imap` | `gmail_api`), default `imap`,
  chosen at `ecf address add --access api`. One install may mix IMAP and API addresses.
- **Moving and signing in again (OD-530, amended by P15/P16):** `ecf address set <ref> --access
  api|imap` with a *different* access signs in the new way, revokes and deletes the old
  credential, and starts a new cursor epoch. ecf's normal recovery then runs: open items are
  found again by Message-ID, closed items and sender history stay (they belong to the address).
  The same command with the *current* access just signs in again (the fix after a password
  change) and keeps the epoch.
- **Version (OD-531, OD-533):** the next minor release under OD-480, numbered when it ships; checked path (it has a migration).
  The export `data_format` stays 4 (P8).
- **Scopes (P23):** `gmail.modify` only. It permits sending, so ecf's outbound gate (§8.4, §9.8)
  is the control, not the token; the outline's "no send scope while outbound is off" is dropped,
  as is a read-only scope for shadow (shadow writes nothing to the mailbox already,
  `mailbox_actions.py:109`). Step 1 checks whether `gmail.modify.restricted` excludes sending; if
  it does, it is reconsidered.
- **The superseding ADR:** ADR 0032, proposed with this plan, accepted at the M6 tag.
- **Unchanged (P29):** the gate key (pins), `prompt_digest`, `actor_prompt_digest`,
  `classifier_schema` and the frozen synthetic eval; `evalrun.py` never touches the mail port. The
  facts parity test (P5) stands in for an eval rerun.

## Design

### Mailbox kind

One helper, `gmail_mailbox(conn, address_id)`, is true for IMAP Gmail mode (`probe.is_gmail`,
X-GM-EXT-1, OD-438) or `access = 'gmail_api'` (P5). It replaces `probe.is_gmail` where the
behaviour is Gmail's rather than IMAP's: the Archive/All Mail role map
(`mailbox_actions.py:102,146`), "Gmail keeps its own Sent copy" (`send.py:211`), reading labels
for `self_sent` (`fetch.py:248-249`), and the probe's known facts (`probe.py:91`). Gmail-by-domain
(`internal.is_gmail`) keeps only what depends on the address itself: folding (OD-432), the 100 a
day send default (`addresses.py:249`), lookalike and typo facts. IMAP-only stays on the probe flag:
the daily download budget (`fetch.py:143,245,399`; OD-440), corpus, the doctor's All Mail and
inbox-limit rows. `AddressConfig.gmail` is split into `labels` and `download_budget`. M7's
Workspace addresses are `gmail_api` and get the Gmail behaviour without a domain check.

### Adapter: the API source behind the existing UID-shaped port

`MailSource` (`src/ecf_server/mail/__init__.py:141-233`), `cursors`, `items.uid`/`locator`,
`processing` and backfill are keyed on (uidvalidity, uid). `GmailApiSource` implements
`MailSource` and maps Gmail message ids to local UIDs, so `fetch.py`, `checks.py`, `backfill.py` and
`restore.py` keep their logic; the changes they need are named below.

**Adapter state** (P2, P8, P11): `gmail_state (address_id PK, epoch, history_id, next_uid,
synced_at, label_ids JSON)` and `gmail_ids (address_id, epoch, uid, gmail_id, PRIMARY KEY
(address_id, epoch, uid), UNIQUE (address_id, epoch, gmail_id))`. Both are excluded from export
(`export_bundle.py:66-70`). The epoch is never a counter: a 63-bit random value, so a restored
cursor's uidvalidity can't recur. Mapping inserts and the history-id advance happen in one
transaction under the check's fencing token. Rows of older epochs are deleted when recovery ends
(`recovering_until` back to 0).

**Epoch start** (P1): when the state is missing (new address, after import or restore) or history
returns 404, the adapter takes `historyId` from `users.getProfile` first, then lists INBOX
(`messages.list labelIds=INBOX`, ids only) and assigns UIDs oldest first by `internalDate`, so UID
order is arrival order and `uidnext` is real. History then appends. `inbox()` returns the new epoch;
`fetch_page` sees a uidvalidity change and runs `_start_recovery` (`fetch.py:713`). The cost
(listing pages, and whether ordering needs a metadata read per message) is measured at step 1.

**Gaps** (P11): `_start_recovery` with no items starts at `uidnext - 1` (`fetch.py:728`), which on
the API would skip the mail since the last good history read. For API addresses the recovery
window starts at `min(synced_at, recovery_since) - 1 day`, and falls back to the last finished
check when there are no items.

| Port method | Gmail API |
|---|---|
| `capabilities()` | `custom_keywords`, `move`, `uidplus` true; a new `gmail_ids` capability true (the contract test changes, P19); IMAP-only fields false |
| `folders()` | INBOX, a synthetic Archive folder with role `\Archive`, Sent, Drafts, Trash, Spam, and the user's own labels as folders by name (for `move_folders` and `label_folder`) |
| `inbox()` | `uidvalidity` = the epoch; `uidnext` = `next_uid` |
| `uids_after(last)` | read `history.list` (all pages, capped at 50; over the cap = resync), map new INBOX arrivals and re-arrivals (`labelAdded` INBOX gets a new UID, as IMAP Gmail does), then `SELECT uid FROM gmail_ids WHERE ... AND uid > last` |
| `uids_since(when, before)` | `messages.list q="in:inbox after:<epoch s> before:<epoch s>"`, all pages, mapped |
| `find_message_id(id)` | `messages.list q="rfc822msgid:…"`, mapped |
| `existing(uids)` | from the history read in the same check (removals) and the epoch list; no per-id call |
| `meta(uids)` / `flags` / `gmail_labels` | one cached `messages.get format=metadata&metadataHeaders=Message-ID,Date` per id per check (batched, P12), serving all three; a message not in INBOX counts as gone (P3) |
| `fetch(uid)` | `messages.get format=raw`, streamed and decoded chunk-wise; memory only (§12.4; P13) |
| `structure` / `fetch_part` | `format=full` for the part tree (`partId` as section), `format=metadata` for HEADER, `messages.attachments.get` for a part cut to the limit locally; never the whole raw for an oversized message (P13) |
| `add_keyword` / `remove_keyword` | user labels `ecf/<install>/<label>` by id (cached `labels.list`; 409 means re-list); every `ecf/*/*` label is read back in keyword form, so other installs and the restore guard still work (P20) |
| `set_flagged` / `set_seen` | STARRED / UNREAD |
| `move(uid, Archive)` / `move_back` / `copy_back` | remove INBOX / add INBOX (P4) |
| `move(uid, label)` / `copy` | add the label (and remove INBOX for a move) |
| `gmail_msgid(uid)` / `gmail_find` | `int(gmail id, 16)` (step 1 checks it equals X-GM-MSGID) / the live UID in the current epoch |
| `append(Drafts, raw)` | `drafts.create`; any other folder is refused (P6) |
| `delete_in(Drafts, uid)` | `drafts.delete` (after `_delete_draft`'s content-hash check); any other folder is refused (P10) |
| `timings()` | the same phases plus `units` (quota spent) |

- **Sizes** (P13): routing to the large/deferred path uses `sizeEstimate × 1.1`; the stored size
  is `len(raw)` once fetched. Step 1 measures the estimate's error and memory at 1, 17 and 50 MB.
- **Restore guard** (P20): for API addresses the 7-day restored-labels window (OD-372) compares
  `internalDate` with the restore time instead of the cursor's uidvalidity.
- **Probe** (P9): `probe()` takes the mailbox kind. For API addresses: `getProfile` and
  `labels.list`; `saves_sent` 1, `permanent_keywords` 1, `max_message_bytes` from the known Gmail
  facts, source "Gmail API"; no SMTP check, no Archive warning; `host` = `gmail.googleapis.com`, so
  health's Mail Provider Unreachable (`health.py:105-131`), the check join (`checks.py:114-117`)
  and destroy (`destroy.py:374-378`) keep working. A test asserts `address_config()` is the same
  for an IMAP-Gmail and an API-Gmail address except the download budget.

### Sender (P6)

- "Can send" is by mailbox kind: an SMTP host, or `access = 'gmail_api'`. `send_actions.refusal`
  (`send_actions.py:56-57`), `alerts.email_set` (`alerts.py:222`), `ops_doctor.smtp`
  (`ops_doctor.py:120`), `initsetup.smtp_addresses` (`initsetup.py:122`) and `service.make_sender`
  (`service.py:126-140`) use it. Live and outbound-on stay required for actions.
- `GmailApiSender.send(raw, mail_from, rcpt)`: `messages.send` (raw). It parses To/Cc from the
  built bytes (`email.utils.getaddresses`) and refuses unless the addr-specs equal `rcpt`; any Bcc
  or group syntax is refused (ecf writes only From and To, `outbound_msg.py:92`). Failures are
  classed as `SmtpSender`'s (`mail/smtp.py:6-13`): before the request body is sent, retryable;
  4xx, failed; timeout or 5xx after, `SendOutcomeUnknownError`, never retried (`settle` looks in
  SENT by Message-ID).
- The adapter's only mutating calls: `messages.modify`, `drafts.create`, `drafts.delete`,
  `labels.create`, and (sender only) `messages.send`. A test checks it.

### Sign-in and credentials (P7, P14, P15, P21, P22)

- **Secret names:** `gmail-client` (client id and secret as one JSON value) and
  `gmail-token/<address_id>`, which can't collide with an address id (`ecf/ids.py:18`). Added to
  `_NAME` (`secretstore/__init__.py:15-18`), `regrant.names` and `FIXED_NAMES`
  (`regrant.py:39-45`), destroy's `_secrets` (`destroy.py:330-341`), SPEC §11.6 and the
  `systemd-creds` naming note.
- `ecf gmail client set` reads the id and secret with a hidden prompt; step-up and a Security
  Notice, like `--smtp-host` (`api.py:2329-2331`). Replacing a client while API addresses exist is
  refused (their tokens belong to the old client).
- **Flow:** `POST /v1/gmail/signin` (step-up checked here) starts a flow and returns `{url,
  flow_id}`; the CLI opens the browser and polls `GET /v1/gmail/signin/{flow_id}` (each call within
  the 10 s socket timeout, `client.py:15`) until done, failed or expired (5 minutes). One pending
  flow per service.
- **Listener:** its own daemon thread, never a request worker; bound to 127.0.0.1 on a random
  port before the URL is returned; redirect `http://127.0.0.1:<port>/`; PKCE S256; `state`
  compared in constant time; only the exact path counts (`/favicon.ico` and others ignored); an
  `error=` reply fails the flow; the page shows no code or state, sends `Cache-Control: no-store`
  and says the browser can be closed; no access log. `code`, `state` and the URL join the log
  redaction list (`ecf/log.py:88`).
- **Exchange:** `login_hint=<address>`, `access_type=offline`, `prompt=consent` (so a refresh token
  comes back every time). The service checks `users.getProfile` against the address with Gmail
  folding (`internal.fold`, `internal.py:55-59`); on a mismatch it revokes the new token, stores
  nothing and says which account signed in. A rotated refresh token is stored when Google sends
  one.
- **Signing in again** keeps the epoch (P15); replacing a token revokes the old one first.
- **Revocation** (P22): remove and destroy revoke at Google first, delete locally even if Google
  can't be reached, and then leave a residue line pointing to the third-party connections page
  (myaccount.google.com/linkedapps; support.google.com/accounts/answer/13533235, fetched
  2026-10-16). `destroy.run` gets a revoke step before secrets.
- **Dead tokens:** `invalid_grant` on refresh maps to `MailLoginRejectedError`, so the existing
  alert (`[ecf-alert] Mailbox Login Rejected`, after three, `health.py:9`) fires, worded for
  sign-in, with `ecf address set <address> --access api` as the fix.
- **Access tokens** are cached per address on `ServiceState` (refreshed when under 5 minutes
  remain), shared by the source and the sender; a 401 refreshes once and retries.
- **CLI and init** (P21): `ecf address add --access api` uses the flow instead of the app-password
  prompt (`cli.py:670`); `address add` help (`cli.py:591-595`); `address list` shows the access
  instead of "(None)" (`cli.py:767`); the import, init and doctor hints that say `--app-password`
  (`cli_import.py:71,118`, `cli_init.py:202`, `doctor.py:324`) name the right fix per access;
  `ecf init`'s wizard (`cli_init.py:289-292`) offers both. `MailFactory` takes a small `MailTarget`
  (access, email, host, address_id) built from the request or the row (`addresses.py:57,166,195,340`,
  `checks.py:111,168`, `service.py:113,475,579`, `api.py:177,537,2158`). `address set --access`
  needs step-up.

### Requests, quota and errors (P12)

- One `httpx.Client` per source (connection reuse), `trust_env=False`, no redirects, timeouts as
  §15.4's IMAP row (connect 15 s, read 60 s); closed in `close()`.
- Remaining per-id reads go through Gmail's batch endpoint (up to 100 calls per request,
  unverified; step 1).
- A token bucket per Google account, about 5,000 units a minute, shared by source and sender;
  units recorded in the check's timings.
- Retries inside the adapter for 429, rate-limit 403 and 5xx, honouring `Retry-After`, full
  jitter, at most 3 tries, never past the check deadline or a lost lease. `messages.get` 404 means
  gone (`None`). Errors are wrapped with fixed text, so no URL, Message-ID or label name reaches the
  log; httpx stays at WARNING (`ecf/log.py:93-94`).
- Recovery relocation (`fetch.py:730-742`) is bounded by the check deadline and continues next
  check; items with pending actions go first.
- SPEC §15.4 gets a Gmail API row.

### Export and versions (P8)

`gmail_state` and `gmail_ids` are excluded from export. After an import or restore, the first check
of an API address finds no state and starts a new epoch (recovery as above). `addresses.access` is
exported; an older ecf refuses a newer bundle by schema version (`bundle_reader.py:253-259`) before
reading rows, and a newer ecf importing an older bundle gets the column default. So `data_format`
stays 4 (`export_bundle.py:44-53`) and the release is a minor one. `downgrade.py` and SPEC §11.10
note that an address moved to the API after the upgrade has no app password in the older version.

### Limits, dependencies, scope

- **Sends:** the 100 a day default stays for Gmail addresses on both paths.
- **Library:** httpx (already a dependency, `pyproject.toml:12`). No Google client library. An
  import-linter contract keeps the adapter in `ecf_server.mail`.
- **Destinations (§12.4):** Google's API and OAuth endpoints are "the mail provider"; polling only.
- **Out of scope:** `ecf corpus fetch` and `ecf/replay.py` stay IMAP-only and say so for API
  addresses; Workspace (M7); Linux (M5).
- **Migration:** the next free number at build time: `addresses.access` (backfill `imap`),
  `gmail_state`, `gmail_ids`; `release.json` schema bump.

## Steps

0. **Decisions + Google project.** OD rows committed with this doc. The operator creates a Google
   Cloud project and Desktop client by the draft guide (`docs/gmail-setup.md`, branch
   `m6-gmail-api`) and times it; the guide's time estimate comes from that.
1. **Real-service spike (gate).** Throwaway code, one test Gmail account. Results in
   `state-archive/m6/spike/` and a SPEC §21.1 row.
   a. **Same input to ecf** (P17): about 30 DKIM-signed messages fetched by IMAP `BODY.PEEK[]` and
      API `format=raw` within minutes of each other: 10 natural from at least 3 domains, 20
      constructed (simple/simple, relaxed/relaxed, an `l=` tag, two signatures, 8-bit body,
      quoted-printable with trailing spaces, an attachment over 10 MB, RFC 2047 and raw UTF-8
      headers, a bare-LF body, lines over 998 characters, a message to self). Per message compare
      `senderauth.evaluate` (result, reason, `mime_headers_signed`), `message.parse` (content
      hash, `headers_ambiguous`, defects) and `facts.compute`; class byte differences as
      line-ending-only or material. Pass: all equal and no material difference (line-ending-only
      differences get a normalisation decision, not a re-plan). The claim recorded: no systematic
      difference; a rare one under 10% (95%). Also `internalDate` vs INTERNALDATE, and
      `sizeEstimate` vs `len(raw)`, and memory at 1, 17 and 50 MB.
   b. **Measured items** (P17), each with its answer recorded: history.list paging, the 404 and
      `labelAdded` after an Undo; `rfc822msgid:` and `after:` search latency; `after:`/`before:`
      with epoch seconds; listing order and the cost of the epoch-start ordering; labels.create
      409 and name rules; historyId monotonic; batch endpoint limit; `messages.get` units by
      format; the project's quota in the console; access-token lifetime and a refresh mid-run;
      the error after a revoke and after a password change; `getProfile` for a googlemail.com
      login; a SENT copy after `messages.send`; `drafts.create` keeps ecf's Message-ID;
      `int(id, 16)` equals X-GM-MSGID; the consent screen's wording for `gmail.modify`; whether
      `gmail.modify.restricted` excludes sending; whether IMAP keywords and API labels see each
      other (two installs on one mailbox).
2. **Schema.** Migration, `access`, `gmail_state`, `gmail_ids`, secret names, `release.json`.
3. **Sign-in.** Flow endpoints, listener, `ecf gmail client set`, `ecf address add --access api`,
   revoke, `MailTarget`; tests against a fake OAuth server.
4. **Source, read path.** Epoch start, history, `uids_after`/`since`, `existing`, metadata cache,
   raw streaming, `structure`/`fetch_part`, sizes, quota and retries, missing state means a new
   epoch. `GmailApiFake`, each behaviour citing its step 1 entry. A `source` fixture runs the
   fetch, checks, backfill, restore and importer tests over `FakeMailSource`, `GmailFakeSource`
   and `GmailApiFake`, with the IMAP-only ones listed (P19). Exit: the parity test (P5) and the
   count of tests passing on all three.
5. **Mailbox kind and probe** (P5, P9): `gmail_mailbox`, `AddressConfig` split, probe by kind.
6. **Source, write path.** Labels, flags, archive and Undo, `move_folders`/`label_folder`, drafts
   and draft Undo, the restore guard (P20). Fake tests through `refusal()`, `_move`, `_undo_move`.
7. **Sender.** `GmailApiSender`, "can send" by kind, send settle (P6).
8. **Health, doctor, remove, destroy, CLI and init** for API addresses (P21, P22).
9. **`ecf address set --access`**: move and sign-in-again (OD-530, P15, P16), both directions,
   with tests for open items, closed items and sender history.
10. **Docs.** `docs/gmail-setup.md` (drafted on `m6-gmail-api`) brought in line with what was
    built; README updated with M6's capabilities (operator request 2026-10-16: signing in with
    Google, no app password, mixing both kinds of address) and its Status line; admin guide;
    SPEC §1.1, §3.3, §5.1, §11.6, §11.10, §15.4, §18; ADR 0032 accepted; ADR 0010 status; CHANGELOG.
11. **Real-service end-to-end test (gate for the tag).** Pre-registered pass bar (P18):
    - at least 20 real DKIM-signed messages from at least 3 domains, ecf's `auth_result` agreeing
      with Gmail's Authentication-Results on all;
    - archive then Undo restores INBOX and ecf's labels, and the content-hash check passes;
    - a forced resync gives one `mailbox.reset` audit with `open_items_relocated` equal to the
      open items;
    - IMAP to API and back ends with the P16 state (open items found, closed items and sender
      history kept);
    - one run over 60 minutes (an access-token refresh);
    - a real revoke at Google raises Mailbox Login Rejected;
    - peak units a minute read from the Cloud console;
    - `MailSourceContract` run against the real adapter (throwaway harness);
    - stated whether live was reached by override.
    Wheel hash, times and figures in a SPEC §21.1 row.
12. **Closing.** README checked against the build; full macOS suite, 0 skipped; request the tag
    `ms-m6-gmail-api`; release the next minor version by the checked path (`ecf-release` skill).

## Plan review findings

Phase R on draft 1 (2026-10-16): four read-only agents (safety, measurement, cost and design,
performance), about 90 findings merged into P1-P29; key claims spot-checked against the code
(P1, P3, P4, P6-P10, P12, P14); the operator accepted every recommendation (2026-10-16). Full text:
`state-archive/m6-plan/review-findings.md` (gitignored).

- P1 UIDs assigned on discovery aren't arrival-ordered; epoch start lists INBOX oldest first.
- P2 `gmail_ids` keyed by epoch; re-added mail gets a new UID; fenced, one transaction.
- P3 `meta()` needs Message-ID (`actions.py:81` refuses otherwise) and INBOX filtering.
- P4 Archive needs a `\Archive` folder; Undo needs `int(id, 16)`.
- P5 One `gmail_mailbox` predicate; `self_sent` kept; budget split; parity test; M7 reuse.
- P6 "Can send" by kind (`send_actions.py:56`); sender checks and failure classes; mutating calls
  listed.
- P7 Secret names that can't collide with an address id.
- P8 Non-repeating epoch; adapter state excluded from export; `data_format` stays 4.
- P9 Probe by kind; host `gmail.googleapis.com`.
- P10 Draft Undo needs `delete_in(Drafts)`.
- P11 History 404 on a quiet address skipped mail; `synced_at`.
- P12 Batching, quota bucket, retries, token cache, bounded relocation, §15.4 row.
- P13 Oversized mail without the whole raw; streaming; `sizeEstimate`.
- P14 Sign-in flow endpoints and listener hardening; `login_hint`, `prompt=consent`, fold check.
- P15 Signing in again keeps history.
- P16 A move keeps closed items and sender history; open items relocate.
- P17 Spike compares ecf's own outputs; strata; measured-items list.
- P18 Pre-registered end-to-end pass bar.
- P19 Tests over all three fakes; contract test amended.
- P20 Other installs' labels and the restore guard on API addresses.
- P21 Hidden CLI and init scope; `MailTarget`; client replacement; step-up.
- P22 Revocation order and failure.
- P23 SPEC §1.1, §1.2, §3.3, §11.6 consistent now; scope-by-stage dropped.
- P24 Build steps reordered and split.
- P25 Facts and guide claims cited or marked unverified.
- P26 `uids_since` with epoch seconds; paging.
- P27 Label id cache; 409.
- P28 Verify and Undo refetches counted in the unit budget.
- P29 Gate key and eval unchanged, stated.

## Facts (fetched 2026-10-16; re-check at step 1)

Re-checked from the outline (2026-10-04), still true:

- Scopes: `https://mail.google.com/`, `gmail.readonly`, `gmail.compose`, `gmail.insert`,
  `gmail.modify`, `gmail.metadata` restricted; `gmail.send` sensitive; `gmail.labels`
  non-sensitive (developers.google.com/workspace/gmail/api/auth/scopes).
- "If you store or transmit restricted scope data on servers, then you need to complete a
  security assessment"; personal use ("if you are the only user of your app or if your app is
  used by only a few users") is an exception to verification
  (developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification;
  support.google.com/cloud/answer/13464323).
- Testing: "Authorizations by a test user will expire seven days from the time of consent";
  unverified production apps show an unverified-app warning and are capped at "100 new users in
  total"; a project is "In production after selecting the Publish app button"
  (support.google.com/cloud/answer/15549945).
- Refresh tokens end when "the user changed passwords and the refresh token contains Gmail
  scopes" and after six months unused; "a limit of 100 refresh tokens per Google Account per
  OAuth 2.0 client ID" (developers.google.com/identity/protocols/oauth2).
- Desktop apps: loopback redirect recommended; PKCE supported (S256); the client secret can't be
  kept confidential (developers.google.com/identity/protocols/oauth2/native-app).
- `format=raw`: "The entire email message in an RFC 2822 formatted and base64url encoded string";
  a `historyId` "is typically valid for at least a week, but in some rare circumstances may be
  valid for only a few hours"; on a 404, "perform a full sync"
  (developers.google.com/workspace/gmail/api/reference/rest/v1).
- Send limit 500 a day (support.google.com/mail/answer/22839).
- Console steps (project, Gmail API, Google Auth platform Branding/Audience/Clients, Desktop app)
  as in developers.google.com/workspace/guides/create-project and
  developers.google.com/workspace/gmail/api/quickstart/python; removing an app's access at
  myaccount.google.com/linkedapps, "See details", "Remove access" (support.google.com/accounts/answer/13533235).

New since 2026-10-04:

- Quota is per minute: 1,200,000 units per project and 6,000 per user per project, for projects
  created on or after 2026-05-01; charges past quota planned later in 2026. messages.get 20 units
  (one read said 20-40; step 1), messages.send 100, history.list 2, messages.modify 5
  (developers.google.com/workspace/gmail/api/reference/quota).
- Accepted scopes, per each method's reference page
  (developers.google.com/workspace/gmail/api/reference/rest/v1, fetched 2026-10-16):
  `messages.modify` accepts `mail.google.com`, `gmail.modify` and a new
  `gmail.modify.restricted`; `drafts.create` accepts `gmail.compose` or `gmail.modify`;
  `messages.send` accepts `gmail.send`, `gmail.compose` or `gmail.modify`; `labels.create` accepts
  `gmail.labels` or `gmail.modify`.

Unverified (step 1 unless noted):

- Whether a local-only app is exempt from the security assessment: Google's current pages list
  no such exemption; a 2020 FAQ quoted second-hand said it was. Moot under OD-528.
- Whether `format=raw` gives ecf the same input as IMAP (step 1a).
- Whether API sends count toward the 500 a day the same way as Gmail web sends.
- The unverified-app screen's wording and its "Advanced" link; the consent wording for
  `gmail.modify`; whether a payment card is ever asked for (step 0).
- Listing order, batch limits, `int(id, 16)`, `prompt=consent` behaviour (step 1b).
