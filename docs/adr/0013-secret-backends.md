# ADR 0013: Secret backends

- **Status:** accepted (OD-096, 2026-09-26; OD-097, OD-163, OD-172, 2026-09-27; OD-348,
  2026-10-03); implemented in V1.0 (adapters), the re-grant command in V1.5; Linux unverified until
  M5 (ADR 0020)
- **Context source:** SPEC §11.6, §12.2, §21.1, §21.2; ADR 0001; design plan
  (`docs/history/design-plan-2026-09-27.md`) §10a; `ecf_server/regrant.py`, `ecf/service_unit.py`

## Context

ecf holds credentials that open real mailboxes and a Slack workspace: one IMAP app password per
address, the Slack bot and app-level tokens, an optional model API key, and the backup signing
seed. v1 has no cloud secret store (no AWS), so they live on the computer. ADR 0001 records what the
macOS Keychain gate test found and the macOS decision that followed; this ADR covers the backends
across both platforms and who may write them.

## Decision

- **The OS secret store only** (design plan §10a, CLAUDE.md): never the repo, environment
  variables or a secrets file. No passphrase-file fallback (§11.6).
- **The service is the only writer** (§11.6): the CLI sends an app password or token over the 0600
  Unix socket and the service stores it, so one binary owns the items' access rules.
- **macOS:** the Keychain via `keyring`, with prompts off in the service and a recorded interpreter
  hash (ADR 0001, OD-163). After the Python under ecf changes, `ecf service regrant` stops the
  service, runs `ecf-server regrant` in the foreground with the unit's interpreter and prompts on,
  reads every secret, and records the new hash only when every read succeeded (OD-348). `ecf
  upgrade` runs the same re-grant (§11.10).
- **Linux, chosen automatically and shown by `doctor`** (OD-096): (1) Secret Service (GNOME
  Keyring, KWallet, KeePassXC) via `keyring` when a D-Bus session and an unlocked keyring exist;
  (2) otherwise `systemd-creds --user` (TPM2 if present, else the host key; systemd 256+), loaded
  at service start through `LoadCredentialEncrypted=`; (3) otherwise refuse to start and explain.
  Desktops are supported; headless Linux is best-effort (OD-097).
- **Names** (§11.6): service `email-classify-filter/<install>`, accounts `mailbox/<address_id>`,
  `slack/bot`, `slack/app`, `models-api-key`, `export-signing-seed`; `/` maps to `.` under
  `systemd-creds` (OD-172).

## Alternatives considered

- **A private interpreter copy or user-presence Keychain items:** rejected by the V1.0 gate test
  (ADR 0001).
- **A passphrase-file fallback:** rejected (§11.6; no reason recorded there).
- **A root system unit for Linux systems older than systemd 256:** not in v1; decided in M4
  (§11.6).

## Consequences

- Any process running ecf's Python interpreter can read its Keychain items without a prompt; this
  is a stated limit (§12.2, ADR 0001).
- A Python update under ecf needs one foreground re-grant; until then the service waits with
  "secret store needs you" instead of hanging (§11.6).
- On Linux desktops after a reboot, the service can't read Secret Service until you log in
  (unverified, M5); the `systemd-creds` behavior is unverified too (M5).
- Backups never contain secrets (§11.9): after a restore or import, app passwords and Slack tokens
  are entered again.
- M1 moves the same accounts to AWS Secrets Manager (design plan, `ecf migrate`).
