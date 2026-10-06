# email-classify-filter (ecf)

ecf watches business mailboxes (for example `billing@` or `accounts-payable@`) over IMAP and
classifies each message. It flags likely invoice and payment fraud and regulator mail with
deterministic rules, and asks you to approve actions in Slack before anything risky happens. v1 runs
on one Mac (Linux isn't supported yet; it is roadmap milestone M5) with a local model or
Claude, and needs no cloud infrastructure.

## Status

Milestones V1.0 to V1.6 are done, including personal Gmail accounts (V1.6). `v1.0.0` is being
prepared; SPEC §1.5 lists what it needs, and the first release candidate will be `v1.0.0-rc1`.
**Not released yet:** milestone tags (`ms-…`) record internal progress, not releases.

## What it does

- Reads each watched mailbox every 10 minutes in business hours and every 30 otherwise.
- Checks DKIM and DMARC itself and computes facts about the sender (first time, lookalike domain,
  changed bank details, a Reply-To that differs). Fraud and regulator rules run before any model.
- Classifies each email (category, priority, fraud risk, payment-related) and proposes an action:
  label, flag, archive, a draft reply, a template reply or an internal forward.
- Posts to Slack: escalations for possible fraud and regulator mail, approvals, digests, and a
  pinned "Needs you" list.
- Does on its own only what you've allowed per address, and only after an address has passed its
  go-live gate (stages `shadow` → `assist` → `live`).

It never:

- pays anything: approvals on payment emails say "This acts on the email only. ecf never pays
  anything.";
- hides fraud or regulator email, or removes their labels;
- sends mail you didn't approve: sending is off per address until you turn it on, every send needs
  your approval and a step-up (Touch ID or your password) at the computer, and a draft only ever
  lands in your Drafts folder;
- lets a model approve anything, or hide mail on a model's word alone.

## How it works

One background service on your computer (a LaunchAgent on macOS, a systemd user unit on Linux)
does all the work: fetching mail, the checks, rules and policy, Slack, step-up and the actions. It
keeps its state in SQLite and its secrets (app passwords, Slack tokens) in the macOS Keychain or,
on Linux, Secret Service or `systemd-creds`. Full messages are held in memory only, never written
to disk. The `ecf` command and the Claude Code plugin talk to it over a private socket and hold no
rules of their own.

Each address uses one of three presets:

| Preset | Classifier | Actor | Runs |
|---|---|---|---|
| A | Gemma 4 12B on Ollama | Gemma | on this computer, every check |
| B | Gemma | Claude | Claude only when you type `/ecf-review` in `ecf claude` |
| C | Claude | Claude | the same |

Preset A needs about 9 GB of free memory while the model is loaded and is about three times
slower on battery (SPEC §7.5, §21.2).

## Install

Requirements: macOS (Linux isn't supported until milestone M5); Python 3.12.6 or newer
and [uv](https://docs.astral.sh/uv/); full-disk encryption and a screen lock; a Slack workspace
where you can install an app; an IMAP mailbox with app passwords (Purelymail is tested; Microsoft
365 isn't supported, as it needs OAuth); Ollama for presets A and B; Claude Code and a separate
Claude login for presets B and C.

Personal Gmail accounts (gmail.com) are supported over IMAP with an app password; Google
Workspace accounts aren't yet. [`docs/gmail-setup.md`](docs/gmail-setup.md) covers Google's side.

From `v1.0.0`, each release is published only as a GitHub Release on this repository (wheel,
sdist, `SHA256SUMS`, `release-manifest.json`); there is no PyPI package. The repository is private,
so downloading needs access to it. Download the release's files and install the wheel:

```sh
gh release download v1.0.0 --repo rmarable/email-classify-filter
shasum -a 256 email_classify_filter-*.whl   # compare with the wheel's line in SHA256SUMS
uv tool install ./email_classify_filter-*.whl
```

`uv tool install` also accepts a URL to the wheel, if your download can authenticate to the
repository. From `v1.0.0`, `ecf upgrade` checks what it downloads against the release's
`SHA256SUMS`.

Nothing is released yet. Until `v1.0.0`, build the wheel from a checkout and install that:

```sh
uv build
uv tool install dist/email_classify_filter-*.whl
```

## First run

```sh
ecf init
```

`init` prints a "have ready" list, then walks through: the background service, disk-encryption and
secret-store checks, whether this install is `prod` or `test`, the Slack app, your first mailbox
(its app password, your org domains, a probe of the mailbox, the preset), alert email (optional),
backups (the backup key, shown once for your password manager, and a folder off this disk), the
local model, and Claude for presets B and C. `ecf init --resume` picks up where you stopped;
`ecf init status` shows each step. Every address starts in `shadow` with sending off.

Then:

```sh
ecf doctor          # everything ecf depends on, with fixes
ecf status          # each address, the backlog, the service
```

## Day to day

- In Slack you approve or reject, answer ecf's questions, undo and pause.
- `ecf inbox` lists everything waiting on you; `ecf item show <id>` explains one email.
- `ecf approve --pending` lists approvals from Slack that wait for step-up at the computer;
  `ecf approve <id>` confirms one.
- `ecf pause <address>` stops actions at once; fraud and regulator flagging keeps running.
- `ecf claude`, then `/ecf-review`, runs Claude on the queue for presets B and C.

The operator guide (`docs/operator-guide.md`) covers daily use; the admin guide
(`docs/admin-guide.md`) covers setup, configuration, backups, upgrades and recovery. Every command
has `--help`, which marks the ones that are admin-only, destructive or need step-up.

## When you're away

ecf runs only while the computer is on and awake. While it's off or asleep, nothing is checked:
mail waits at your provider and nothing is lost, but fraud checks don't run either. If the
computer sleeps or the service stops without a clean stop, a message scheduled in Slack posts
"ecf hasn't checked in since …" (in business hours, unless you set `deadman_offhours`). On the
first check back, ecf catches up (on AC power on laptops) and the digest opens with "Caught up: N
messages since …". After a week away, approvals of sends have expired (4 days) and need approving
again; other approvals last 14 days. A computer left on keeps working around the clock.

## Privacy

Email content goes only to your mail provider, this computer, Slack (subjects, senders,
classifications, the model's reason, your answers, and short excerpts when you ask), and Anthropic
while you run `/ecf-review` (presets B and C). Alert email carries no subject, sender or text.
Backups are encrypted to your backup key. ecf logs no message content. The full statement is SPEC
§12.4.

## Security

ecf keeps everything on one computer, so whoever controls your user account controls ecf: use
full-disk encryption and a screen lock. What ecf protects against, and what it doesn't, is in
SPEC §12; [`SECURITY.md`](SECURITY.md) summarizes it. We recommend:

- **Slack:** two-factor sign-in, private channels, restricted app installs, and a periodic review
  of who is in ecf's channels.
- **Mail:** a separate app password per address, with two-factor sign-in on the account; your own
  domain's SPF, DKIM and DMARC at `reject` or `quarantine`; rotate app passwords.
- **Computers:** full-disk encryption, a screen lock, current patches.
- **Claude:** Team or Enterprise for business mail; on Pro or Max, turn model-improvement sharing
  off and keep extra usage off or capped.
- **Process:** confirm any payment or bank change by phone or another channel you already trust;
  review the audit log (`ecf logs`); keep the backup key in a password manager.

## Where things are

- [`SPEC.md`](SPEC.md): the v1 specification (authoritative).
- [`docs/adr/`](docs/adr/): decision records.
- [`CHANGELOG.md`](CHANGELOG.md): changes, newest first.
- [`docs/gmail-setup.md`](docs/gmail-setup.md): setting up a personal Gmail account for ecf.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): how to work on ecf.
- [`docs/CURRENT-DESIGN-PLAN.md`](docs/CURRENT-DESIGN-PLAN.md): the latest design plan, including
  the roadmap (AWS mode, teams, remote access, always-on).
- [`docs/history/`](docs/history/): earlier design documents, kept for history.

## License

Apache License 2.0 with the Commons Clause restriction; see [`LICENSE`](LICENSE). This makes ecf
source-available, not OSI open source: you may use and modify it, but not sell it or sell hosting
or support for it. Fees for your own time, such as installation or training, are allowed.
