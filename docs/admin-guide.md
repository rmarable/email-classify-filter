# ecf admin guide

Setting up and running ecf: install, configuration, stages, models, Slack, alerts, backups,
upgrades, removal and recovery. Day-to-day use (Slack cards, approvals, `ecf inbox`) is in the
[operator guide](operator-guide.md). `SPEC.md` is authoritative; each section points to it.

In v1 one person is admin and approver. Commands marked "(step-up)" in `--help` ask for Touch ID
or your password at this computer (SPEC §9.6). `--install <name>` picks another install on the
same computer (default `default`). Linux behaviour is unverified until V1.6 (OD-408).

## Install

```sh
uv tool install email-classify-filter          # the supported method, from v1.0.0
uv build && uv tool install dist/email_classify_filter-*.whl   # until then, from a checkout
```

`ecf upgrade` works only on a `uv tool` install (OD-380). Requirements are in the README.

## First setup: `ecf init`

`ecf init` prints a "have ready" list, then runs each step the service doesn't already report
done (SPEC §13.1):

1. starts the background service (installs its unit);
2. checks disk encryption and the secret store;
3. sets the install role, `prod` or `test`, once;
4. Slack (`ecf slack install`, below);
5. the first address (`ecf address add`, below), including your org domains;
6. alert email (optional, default no);
7. backups: a backup key, a folder, a first backup (default yes);
8. the local model (presets A and B), then Claude's login for `ecf claude` (presets B and C);
9. confirms the unit is installed and running.

- `ecf init --resume` continues without the checklist; a step you declined isn't asked again.
- `ecf init status` shows each step.
- `ecf init --restore <bundle>` sets up a new computer from a backup of this install (see
  Recovery).

Then run `ecf doctor`. Every address starts in `shadow` with outbound off.

## The service

| Command | What it does |
|---|---|
| `ecf service install` | install the unit (launchd LaunchAgent, or systemd user unit) and start it |
| `ecf service start` / `restart` | start it; both also clear a tripped crash-loop breaker |
| `ecf service stop` | stop it until the next login; prefer `ecf pause` to keep fraud checks running |
| `ecf service status` | whether the unit is installed and running |
| `ecf service uninstall` | stop it and remove the unit; data is kept |
| `ecf service regrant` | macOS: re-allow Keychain reads after Python changed (see Recovery) |
| `ecf watch` | run the service in this terminal; the unit is stopped first and started afterwards |

The service runs only while the computer is on and awake (SPEC §4.1). On a computer left on, set
`ecf settings set resident true` to keep the local model loaded (§5.2).

Linux (unverified): the unit runs in your login session; `loginctl enable-linger` lets it run
without one (SPEC §11.14).

## Addresses

```sh
ecf address add ap@acme.example --imap-host imap.acme.example \
    --sensitivity high --preset A [--id ap] [--smtp-host … --smtp-port 465|587]
```

- `add` logs in over IMAP (and SMTP, sending nothing) before storing the app password, then probes
  the mailbox. It needs a real terminal (hidden prompt). Org domains are asked with the first
  address only; later changes go through `ecf config apply`.
- **Presets** (SPEC §4.2): A all-local, B local classifier + Claude actor, C all-Claude.
- **Sensitivity** `standard` or `high` (finance mailboxes: stricter gate, extra checks, a
  10-minute delay on sends). `ecf sensitivity set <address> high` is instant; lowering needs a
  reason and step-up, and sends a Security Notice (§9.1).
- **SMTP host:** default is the IMAP host with `imap.` → `smtp.`, port 465.
  `ecf address set <address> --smtp-host <host> [--smtp-port 587]` needs step-up and sends a
  Security Notice, since the app password goes there (OD-324).
- `ecf address list`; `ecf address retry <address>` checks at the next minute even while rejected
  logins retry hourly.
- `ecf address remove <address>` resolves its open items (step-up when any is a payment or fraud
  item), archives its Slack channel and deletes its app password; ecf's labels stay on messages.
  It's refused while the address sends alert email.

## Configuration

**Security-relevant config** (org domains, forward allow-list, move folders, templates, action
policy, rules, `export_schedule`) changes only through a YAML file (SPEC §9.7, file shape there):

```sh
ecf rules test rules.yaml        # run proposed rules on the synthetic set; shows what changes
ecf config apply config.yaml     # validated, diff shown, step-up, announced in Slack
```

A change to a template or the forward allow-list makes sends approved before it fail at execution
(OD-317).

**Settings:** `ecf settings show` lists every key you can change, with its value;
`ecf settings set <key> <value> [--address <address>]`. Keys owned by another command
(`slack_member_id`, `alerts.routes`, `export_dir`, send limits, `outbound`, stage) name that
command when you try. Install-wide and per-address keys are in SPEC §14.1-§14.2.

**Retention:** `ecf retention show`; `ecf retention set <days>` (1-3650, step-up; lowering it sends
a Security Notice). The audit log and items with fraud or regulator signals are never pruned
(SPEC §6.5).

## Stages and the go-live gate

`ecf stage status` shows each address's stage, days in it, review progress and held items.

| Stage | ecf does | To get there |
|---|---|---|
| `shadow` | decides and posts; changes nothing | the default; going back is instant |
| `assist` | labels, flags, escalates; other actions are held | `ecf stage set <a> assist` (step-up) |
| `live` | full policy | `ecf stage set <a> live` (step-up, the go-live gate) |

The gate (SPEC §9.3): `standard` needs 100 reviewed items at ≥ 85% category accuracy, `high`
200 at ≥ 90%, and both need 0 fraud-guard misses, 0 unsafe payment or fraud proposals and the
injection set at 0 on the synthetic eval for the address's model pair. `stage set live` names a missing eval
result. `--override --reason <text>` waives the review count or accuracy, never the safety gates.
`--held run|resolve-older|resolve-all` decides what happens to held emails (default `run`: up to 7
days old run, older stay held).

**Eval:** `ecf eval run` runs the synthetic set through the local model (about 40 minutes on a
MacBook Air on AC; run it on AC power); `ecf eval run --fraud-only` runs only the safety cases;
`ecf eval run --claude --preset B|C` registers a run that you start with `/ecf-eval` in
`ecf claude`. `ecf eval status|stop|compare`. Results are keyed by the pinned models.

**Pinned models:** a release that changes a pinned model (Ollama digest or Claude model ID) drops
the addresses using it from `live` to `assist` until their gate passes again (SPEC §9.3, OD-381).

## Local model

- `ecf models install` pulls the pinned Gemma model, checks its digest and copies it to ecf's own
  name; it starts ecf's Ollama login item when nothing serves Ollama.
- `ecf models status` shows the pin, Ollama's readiness and any install in progress.
- `ecf models serve install|uninstall|status` manages ecf's Ollama login item (fixed settings:
  loopback only, one request at a time, cloud off). Don't use `brew services`.
- macOS: install Ollama with Homebrew and pin it: `brew install ollama && brew pin ollama mlx-c`.
  To upgrade it on purpose, unpin, upgrade, re-pin, run `ecf models serve install`, and rerun
  the eval (CONTRIBUTING has the exact commands).
- `ecf models api-key` stores the optional Anthropic Models API key for the weekly model watch
  (SPEC §7.6); `ecf models watch` runs the watch at the next tick.

## Slack

- `ecf slack install`: asks for a Slack configuration token (hidden), creates ecf's app, then the
  bot and app-level tokens after you click "Install to Workspace", then a Confirm button DMed to
  your member ID (SPEC §10.1). ecf creates `ecf-<install>-summary` and one channel per address.
- `ecf slack status`: the app, workspace, member ID, connection and channels.
- `ecf slack set-member <id>` (step-up): accept clicks from another member ID; the old one works
  until the new one clicks Confirm.
- `ecf slack set-tokens` (step-up): replace both tokens (same workspace and app).
- `ecf slack reauthorize`: new configuration token, update the app's permissions, re-edit every
  card.

If the workspace doesn't let apps create channels, `ecf slack status` says which private channel
to create and add ecf to.

## Alerts

- `ecf alerts show`; `ecf alerts set [mail|system|operator|slack] --to slack|email|slack,email`
  (step-up); `ecf alerts test` sends a test on every route.
- `ecf alerts email set --from <watched address> --to <destination>` (step-up): alert email through
  one of your mailboxes to an address ecf doesn't watch; a Security Notice and a test email first,
  and email joins the default routes. `ecf alerts email off` (step-up) stops it.
- Alert email carries no subject, sender or text; caps and fallback to Slack are in SPEC §13.3.
- `ecf settings set notifications off` turns desktop notifications off.

## Outbound

Off per address by default (ADR 0007, SPEC §8.4, §9.8).

- `ecf outbound report <address>`: what it would have sent, your reviews, what a `high` address
  still needs, send limits, recent sends and reminders.
- `ecf outbound enable <address>` (step-up, Security Notice). A `high` address first needs 20
  reviewed suppressed sends, 95% marked correct; there's no override.
- `ecf outbound disable <address>`: instant; cancels sends in their 10-minute delay and voids
  approved ones.
- **Send limits:** 25 an hour, 250 a day by default.
  `ecf address set <address> --max-sends-per-hour <n> --max-sends-per-day <n>` (step-up, Security
  Notice). Reaching a limit holds the address's sends until `ecf outbound resume <address>`
  (step-up); resume never turns outbound back on.
- `ecf outbound snooze <address> [--days N]` / `dismiss <address>` for the reminders.

## Backups, export, import and restore

SPEC §11.9 has the formats and rules.

**Set up** (`ecf init` does both):

```sh
ecf export keys rotate       # new key, shown once; type `saved`, then its fingerprint (step-up)
ecf export dir set <folder>  # where backups go, ideally off this disk (step-up, fingerprint)
```

Keep the `ECF1-…` key in a password manager. ecf keeps only the halves that sign and encrypt, so
it can't read its own backups. iCloud Drive, `~/Library/CloudStorage/*` and external disks count
as off this disk; `ecf doctor` warns when the folder is on the same disk.

**Running:** a daily backup by default (`export_schedule` in `ecf config apply`: `daily`, `weekly`
or `off`), newest 14 kept (`ecf settings set export_keep <n>`). `ecf export status` shows the last
backup and failures; `ecf export now` makes one at once; `ecf export keys show` shows the key's
fingerprint. Two failures in a row raise a System Error.

**Manual export:** `ecf export --to <file or folder>` (step-up) writes a bundle protected by a
passphrase (ecf offers six words), readable without the backup key; use it to move data.

**Import** another install's data: `ecf import <bundle> --dry-run` first, then
`ecf import <bundle>` (step-up); `--replace` replaces this install's addresses and emails (asks
for the install name). Addresses arrive paused, at most `assist`, outbound off. Secrets, Slack,
the backup key and alert email are set up again.

**Restore** this install: `ecf restore <bundle>` (step-up) with the backup key (and the
passphrase for a manual export). It previews, warns when the bundle is older than your newest
backup, asks you to confirm ecf is stopped everywhere else, and keeps a copy of the data it
replaces for 7 days. Addresses come back paused at their previous stage; `ecf resume <address>`
works once a mail check has passed since the restore.

## Upgrade and rollback

Until a release index exists, `ecf upgrade` with no arguments refuses (OD-374). Test installs
upgrade from a wheel (`prod` refuses `--wheel`):

```sh
ecf upgrade --wheel <file> --check   # all checks, nothing stopped
ecf upgrade --wheel <file>
```

It refuses while items are executing, a lease is held, an `ecf claude` session is open or
`ecf watch` runs. It snapshots the database and keeps the running wheel, installs the new one,
migrates, re-grants Keychain access on macOS and starts the service. A failed install, migration
or start restores the old version by itself.

`ecf upgrade --to <version>` goes back using that upgrade's snapshot (the newest 2 are kept).
Before the new version's first good timer pass, the whole snapshot returns; after it, send
history, sender records and gate history are kept, mail since the upgrade is read again, open
approvals need deciding again, running actions become `failed_unknown`, and every address is
paused at `assist` at most (SPEC §11.10, OD-331).

## Removing an install

`ecf destroy [--config-token]` (step-up): refuses while anything runs or an upgrade hasn't
settled; offers an export when none was made in 24 hours; asks you to type the install name. It
archives the Slack channels, deletes the Slack app (with `--config-token`; otherwise turns its bot
off), deletes every secret, removes the unit, signs `ecf claude` out and deletes the data folder.
Backups and mailboxes are untouched. It ends with what's left for you: app passwords to revoke at
your provider, the Slack app if kept, Ollama if no other install uses it. Running it again finishes
an interrupted destroy (SPEC §11.11).

## Health: `ecf doctor`

`ecf doctor` checks the service, secret store, disk encryption, Slack, each address's IMAP and
SMTP, the local model and Ollama's settings, Claude (presets B and C), alert email and backups,
and prints a fix for each failure (SPEC §13.2). `ecf status` shows addresses, backlog and the
service; `ecf logs [--address] [--event] [--since] [-f]` shows the audit log (no message content).

## Recovery

**Items needing a person** (SPEC §13.5): `ecf inbox` lists them. `ecf item show <id>` explains
one. For `failed`, `failed_unknown` and stuck `executing`: `ecf item requeue <id>` runs the action
again (a send only once it surely failed: refused by the server, or missing from Sent on two later
checks; it needs a new approval and step-up, OD-322), or `ecf item resolve <id> --reason <text>`
closes it. `needs_human` goes back through a new proposal or `ecf item resolve`. An email marked
"the local model gave up" goes back to the model with `ecf item requeue <id>`.
Bulk: `ecf item resolve --older-than <days> [--address <a>] --reason <text>`.

**Revoked Slack token:** `ecf slack set-tokens`, then `ecf slack reauthorize`.

**Mailbox Login Rejected** (new or revoked app password): `ecf address set <address>
--app-password`; checks resume and a `Resolved:` notice follows (OD-119).

**Secret store locked, or "Python changed"** (macOS): after Python under ecf changes, the service
can't read its Keychain items and waits with "secret store needs you". Run `ecf service regrant`:
it stops the service, reads each item with the Keychain dialog on (enter your login password,
choose Always Allow), records the new interpreter only when every read succeeded, and starts the
service. A locked Linux keyring (after a reboot, before you log in) is unverified until V1.6.

**Crash loop:** after 5 crashes in 10 minutes the service posts why and stays stopped until
`ecf service start` (SPEC §11.1). A message that crashes the service twice is quarantined
(`content_unscanned` and escalated) so the rest keep flowing (§5.1). SPEC has no further
procedure; check `ecf logs` and `ecf doctor` before starting it again.

**Mailbox reset** (the provider changed the mailbox's UIDVALIDITY): ecf recovers by itself. It
re-reads INBOX mail from just before the last message it had, matches messages it already has,
re-points open items to their new UIDs and audits `mailbox.reset` (SPEC §6.4). Items it can't
find again close as `resolved_by_mailbox`. No command is needed.

**Size and scan limits:** messages over `max_message_bytes` (16 MB `standard`, 64 MB `high`) get
headers and the first `max_scan_bytes_per_part` (10 MB) of each text part only. Change them per
address with `ecf settings set max_message_bytes <size> --address <a>` and
`ecf settings set max_scan_bytes_per_part <size> --address <a>` (in MB, 1 to 64; SPEC §5.1).
64 MB is the largest size whose memory use was measured (OD-220).

**Two installs on one mailbox** (SPEC §13.6): another install's labels, or another copy's
`X-ECF-Install` on your own mail, pause the address with "possible second install". Stop the other
copy, then `ecf resume <address>`.

**Lost backup key:** SPEC defines no way to recover it. Scheduled backups made with it can't be
read; a manual export can still be brought in with its passphrase through `ecf import` (not
`ecf restore`, which needs the key). Run `ecf export keys rotate` for a new key (the
dialog names the old and new fingerprints), then `ecf export now` so a readable backup exists.

**Stolen laptop** (SPEC §12.1): full-disk encryption and a screen lock are what protect it. Then,
from another computer: revoke each mailbox's app password at your provider; revoke ecf's Slack
tokens (or remove its app) in Slack; sign out ecf's Claude login (presets B and C) in your Claude
account. SPEC names these steps but not each provider's screens. On a new computer:
`ecf init --restore <bundle>` with your backup key; it lists what's left (Slack tokens,
`ecf address set <a> --app-password` with the new passwords, `ecf check`, `ecf resume`, `ecf
doctor`). The restore raises the install's generation, so mail sent by the old computer is told
apart (OD-318).
