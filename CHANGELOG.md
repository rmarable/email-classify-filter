# Changelog

One entry per tag, newest first, kept up to date as changes are committed (rule: `CLAUDE.md`,
Changelog). Milestone tags (`ms-…`) record internal progress and are not releases (ADR 0003).

## ms-v1.1-mail-checks (not yet tagged)

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
