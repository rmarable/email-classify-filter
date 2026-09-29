# Changelog

One entry per tag, newest first, kept up to date as changes are committed (rule: `CLAUDE.md`,
Changelog). Milestone tags (`ms-…`) record internal progress and are not releases (ADR 0003).

## ms-v1.1.1-fixes (2026-09-29)

Fixes from an adversarial review of V1.1 (four reviewers; SPEC §5.1, §6.3, §6.4, §7.3, §8.5).

- Security: a signed message with a bare carriage return in its headers could show an unsigned Subject or Reply-To and still count as authenticated; it now counts as unverified and fires the ambiguous-header fraud trigger.
- Security: every message is now read and checked in a separate short-lived process with a time limit, so no email can stall checking for every address; a quadratic case in the HTML reader was also fixed (OD-204).
- Security: at most 8 DKIM signatures are checked per message, DNS lookups for one message stay within the check's DNS budget, and a DNS error on the way to a sender's DMARC policy now gives "unverified" instead of falling back to a parent domain's policy.
- Security: a re-send with the same Message-ID and body but a different sender, display name, Reply-To or Subject is now checked as a new message and flagged "Message-ID reused", instead of being filed as a repeat delivery.
- Security: text attachments and text bodies in other formats are now scanned for fraud and regulator keywords; bank details laid out in an HTML table are now recognized; lookalike domains written in punycode are caught.
- Fewer false alarms: a vendor's own parent domain, subdomains and sibling subdomains no longer count as lookalikes; brand names like "Booking.com" and product names like "Node.js" in a display name no longer fire (OD-205); bare "bank", "banking" and "wire" no longer count as bank details (OD-202); "not addressed to this mailbox" counts as a fraud signal only on authenticated mail (OD-201); your organization's help desk on Zendesk, Freshdesk, Atlassian or ServiceNow isn't a lookalike (OD-203).
- Fixed: a network error while reading a message counted as a crash, so two of them quarantined an ordinary email unread and escalated it as fraud.
- Fixed: after a mailbox reset, mail that had waited days before being read, and deferred large mail, could be skipped; recovery now looks back from when the mail arrived. Items whose message was deleted before a reset now close.
- Fixed: an unexpected error in a check skipped all record-keeping and made `ecf check` say the service wasn't running; it's now recorded as `internal_error`. A missing app password now shows as `secret_unavailable` instead of raising "Mail Provider Unreachable".
- Fixed: `ecf check --until-empty` could stop partway with a database threading error; after a crash, an address could wait 42 minutes for its next check; alerts of a removed address stayed open.
- The provider size cap now uses only tested provider limits, not the server's upload limit (`APPENDLIMIT`), which isn't a receiving limit (OD-200).
- Malformed headers and DNS answers with a TTL of 0 are handled without errors or stale caching.

## ms-v1.1-mail-checks (2026-09-29)

- Decisions at the tag: the Mail Provider Unreachable alert treats the network as up when the mail provider's host name resolves (OD-198); four V1.1 measurement items are carried to later milestones (OD-199, SPEC §21.2).
- The list of shared platforms (senders that never count as "seen before") now uses each vendor's researched sending domains: added Adobe Sign, Dropbox Sign (HelloSign), PandaDoc, Zoho Invoice and Wave; removed `quickbooks.com`, which isn't a sending domain (Intuit sends from `intuit.com`) (OD-197).
- Shadow run on the test mailbox done (33 messages, ended early by the operator): results in SPEC §21.1; V1.1 measurement items closed or carried forward in §21.2.
- Fixed: a check that lost its lease partway through a message counted that as one of the message's two allowed crash attempts, so repeated interruptions could quarantine an ordinary message. Only real failures on the message count now (found in the shadow run).
- Fixed: `ecf check` run while a scheduled check of the same address was in progress could take over that check's lease, stopping it (nothing was written twice). A second check of the same address now reports "busy" (found in the shadow run).
- An address's message size limit is now capped at the mail provider's own limit when the probe finds a smaller one (for Purelymail, 48.8 MB instead of 64 MB on `high` addresses); sizes are printed in MB (OD-196).
- Security and memory: ecf no longer lets the IMAP library build debug text from every command and fetched message. That text held the app password and each message in full; it was never written to the log at ecf's log level, but it cost about 3 times each message's size in memory, which the service never gave back (OD-195).
- Messages over 16 MB are now read and checked in a separate short-lived process, so the memory they need (about 750 MB at peak for a 64 MB message) is returned as soon as the check finishes (OD-195).
- Mail-health alerts: a desktop notification when a mailbox can't be reached for 15 minutes while your network is up (`Mail Provider Unreachable`), or rejects the app password 3 times (`Mailbox Login Rejected`; checks then slow to hourly), and again when it recovers. `ecf status` lists open alerts. New command `ecf address retry <address>` checks again now; `ecf address set --app-password` clears the rejection count. `ecf doctor` now reports each address's last check, open alerts, your organization's domains and DNS reachability (OD-190).
- Security: a crafted email can no longer crash parsing on Python 3.12.3 (Ubuntu 24.04's system Python), whose email header parser fails on some malformed headers; ecf now falls back to Python's older, tolerant parser for that message.
- When a mailbox resets its message numbering (UIDVALIDITY), ecf now recovers on its own: it re-reads recent mail, recognizes messages it already has, and re-points them instead of treating them as new. Items whose message you move, archive or delete in your mail client are closed automatically (`resolved_by_mailbox`).
- The audit log is now also written to daily JSON-lines files in the data folder (one per address, plus one for install-wide events), and the new command `ecf logs` shows it, with filters for address, event, time and `--follow`. It records what ecf did and decided, never message content.
- The service now checks each address on its own: every 10 minutes in business hours (Mon-Fri 08:00-17:00 New York by default) and every 30 minutes otherwise, every 30 seconds while a backlog remains (catch-up, with a cap and a cooldown, on AC power only on laptops), and straight away after the computer wakes.
- New command `ecf check [address] [--until-empty]`: fetches new mail, checks each sender's DKIM and DMARC, runs the fraud and regulator checks and records what the pre-check would do (every address is in shadow in V1.1, so nothing in the mailbox changes). The first check of an address starts from now; older mail isn't fetched. `ecf status` now shows each address's last check, backlog and last error.
- Fraud and regulator triggers, run on every check: bank-detail changes, first-time payment senders with a second signal, lookalike domains (including lookalike letters, via Unicode's confusables data), DMARC fail on payment mail, reused Message-IDs, your own domain unauthenticated, misleading display names, multiple or ambiguous From headers (OD-194), regulator keywords, and unverified payment senders.
- Sender authentication, on every message: ecf checks DKIM and DMARC itself (RFC 9989). `fail` means more than one From header, or every signature aligned with the sender broken under a quarantine or reject policy; unsigned mail stays `none` (OD-192).
- Adding an address (or changing its app password) now probes the mailbox: folders, keyword support and size limit, with a note for anything missing. `ecf address list` shows the number of notes.
- New commands `ecf address add`, `list`, `set --app-password` and `remove`. Adding checks the app password by logging in before the service stores it in the OS secret store; new mailboxes start in shadow with outbound off. The first address sets your organization's domains (public mail domains such as gmail.com are refused); later changes go through `ecf config apply` (V1.2). Removing an address with open items waits for step-up in V1.2 (OD-191).
- Commands that ask for a secret refuse at once, with an explanation, when there is no real terminal to hide your typing.
- New dependencies for mail and sender authentication: `imapclient`, `dkimpy` (with ed25519 support via PyNaCl) and `dnspython`; all pass the license check.
- Lookalike-character detection will use Unicode's confusables data, shipped under the Unicode License v3 (OD-188).
- V1.1 builds label and flag writes but uses them only in tests; real addresses stay in shadow (OD-189). V1.1 alerts are desktop notifications and `ecf doctor` only (OD-190).
- Sender authentication: a DKIM signature that leaves Content-Type or MIME-Version unsigned still passes for ordinary mail, but counts as unverified for payment and fraud rules (OD-187). Purelymail signs neither header, so the full rule made all internal mail look like fraud.
- Real-service test (mail and sender authentication) passed on Purelymail; results in SPEC §21.1 and the provider table.
- Test containers (Dovecot, later Postfix) run on Colima on macOS and Docker Engine on Linux (OD-186).
- SPEC provider table: Purelymail accepts a subdomain as a mail domain; user names can't contain symbols when symbolic subaddressing is on.
- SPEC roadmap: Atomic Mail added as a Later item, to revisit when it ships IMAP/SMTP.

## ms-v1.0.1-fixes (2026-09-27)

Fixes from the adversarial review of V1.0. Nothing here processes mail yet.

### Fixed

- Security: tracebacks in the service log no longer include local variables (a crash could write
  the CLI token into the log); a non-ASCII token now gets 401, not a 500; tokens are hidden from
  `repr`; the service runs with umask 077 (rotated logs stay 0600) and the launchd plist sets
  `Umask`.
- Job queue: a job whose claim keeps expiring now dead-letters; per-address order holds while a
  job waits in backoff (OD-183).
- State machine: stage guards on `proposed` (nothing executes in shadow); a third clarification
  round goes to a person instead of getting stuck; approval without step-up only for reversible
  actions (OD-182).
- SQLite refuses status writes outside `transition()` and item inserts outside `create_item()`
  (OD-181); a failed COMMIT no longer wedges a connection; migrations re-check inside the
  transaction.
- Crash breaker survives a malformed state file and keeps only the 10-minute window.
- `ecf claude`: transcripts are purged before revoking the token and at start; the purge keeps
  only login and config; loosening flags are refused and the environment is allow-listed (OD-184).
- Rules and schema: strict value types, clear errors for a missing label field or unknown level,
  `version: true` and non-name enum values refused; `ecf-server --install` is validated; systemd
  unit paths are quoted.
- Hygiene scan catches domains outside a fixed TLD list, defanged and non-ASCII names, more phone,
  number and token shapes (OD-185).
- Tests: no leaked service processes (the run fails if one survives); assertions that couldn't
  fail now can; doctor branches, license-gate logic and orphaned `.eml` files are tested; license
  overrides are pinned to the reviewed version.
- Docs: SPEC matches the build (Linux CI plus the macOS merge gate, OD-180; Keychain re-grant in
  V1.5; stale hashes and wording).

## ms-v1.0-foundations (2026-09-27)

Foundations for v1 single-user local mode. Nothing here processes mail yet.

- Packaging: one distribution, `email-classify-filter`, with the client (`ecf`) and the service
  (`ecf_server`); commands `ecf`, `ecf-server`, `ecf-mcp` (a stub until V1.4).
- CI on Linux (ruff, strict pyright, import-linter, tests, license allow-list, build); macOS tests
  run locally before merging (merge gate).
- IDs, the error table (HTTP status, exit code, Slack text), and no-content logging.
- The item state machine as data, with `transition()` as the only status writer.
- SQLite state (21 tables, STRICT, WAL, synchronous=FULL) and the per-address FIFO job queue.
- Schema v1 compiler, rules engine with the starter rules, reply templates.
- Secret stores: Keychain (prompts off in the service), Secret Service and `systemd-creds`
  (Linux, unverified until V1.6); interpreter tracking for re-grants.
- The local service: instance lock, 0600 Unix socket, timer tick, watchdog, crash-loop breaker;
  launchd and systemd units; `ecf service install|uninstall|start|stop|restart|status`.
- `ecf status` and `ecf doctor`.
- `ecf claude` wrapper skeleton and session profile tokens.
- `ecf-server dev`: a throwaway dev service with a fake clock and fake chat.
- Synthetic eval tooling: case cards, a byte-identical `.eml` builder, fake PDF invoices, the
  hygiene scan, metrics, and `ecf eval new-case|build|show|compare`; 8 starter cards.
- Documents: SPEC, CLAUDE.md, CONTRIBUTING, GENERATE-FAKE-TESTING-EMAILS, ADRs 0001-0004;
  the changelog rule.
- Real-service test: the V1.0 Keychain gate (ADR 0001).
