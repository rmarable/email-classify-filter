# ecf: an email bodyguard for your business inbox

Scammers love business email. A fake invoice that looks like it's from a real supplier. A message
saying "we've changed banks, please pay our new account". An urgent request from your "CEO" who's
actually someone in another country. One mistake can cost thousands.

ecf (short for email-classify-filter) is a program that reads your business email for you, spots
those scams, and warns you before anyone pays. It also sorts the everyday mail (receipts,
newsletters, customer questions) so the inbox stays manageable.

**Before you rely on ecf, read the [disclaimer](DISCLAIMER.md).** ecf makes fraud harder to miss but
can't catch everything, comes with no warranty, and leaves you responsible for what you approve and
for the mailboxes you connect.

## Who it's for

Anyone who looks after a shared business mailbox, such as `billing@`, `invoices@` or
`accounts@`, and worries about payment fraud. You don't need to be technical to use ecf day to
day, but setting it up takes some comfort with the Terminal on a Mac.

ecf works just as well on your personal email. It supports any mailbox you can reach over IMAP
(the standard way mail programs read email) with an app password: Purelymail, personal Gmail,
iCloud Mail and other providers that offer both. When you add a mailbox, ecf checks that your
provider has what it needs and tells you if anything is missing. So far only Purelymail and
personal Gmail have been tested from start to finish. iCloud Mail offers both (Apple calls it an
"app-specific password") but hasn't been tested with ecf yet; with iCloud and other providers,
that check when you add the mailbox is what tells you ecf can work with it.

## What happens when an email arrives

Say a message lands in `billing@yourcompany.com` that claims to be from your usual supplier,
asking you to send next month's payment to a new bank account.

1. **ecf checks it straight away.** Every 10 minutes during business hours, ecf looks for new
   mail. It checks things a person can't easily see: whether the email really came from the
   supplier's server, whether the sender's address is one letter off from the real one, and
   whether replies would go somewhere else.
2. **It warns you in Slack.** A bank-detail change from a suspicious sender is exactly what ecf
   looks for, so it posts an alert to your Slack channel saying what the email is and why it
   looks wrong.
3. **It never hides it.** Suspected fraud always stays in the inbox, clearly flagged. ecf never
   files it away or deletes it.

Ordinary mail goes differently. When you're ready, you open Claude (an AI assistant from
Anthropic) and type `/ecf-review`. Claude reads the waiting emails and suggests what to do with
each one: "this is a receipt, file it", "this customer needs a reply, here's a draft", "this
newsletter can be archived". The suggestions appear in Slack, and you tap Approve or Reject.

## What ecf will never do

- **Pay anything.** It works only with email. It can't move money.
- **Hide a possible scam.** Anything that looks like fraud, or like a letter from a regulator,
  stays where you can see it.
- **Send an email without you.** Sending is off until you switch it on, and even then every
  outgoing email needs your approval plus your fingerprint or password on the Mac.
- **Let the AI decide alone.** Claude only suggests. The fraud checks are fixed rules that no AI
  can overrule, and nothing important happens without a person saying yes.

## Learning your mailbox first

When you add a mailbox, ecf starts out only watching: it shows you what it *would* do, and does
nothing. Once its suggestions for that mailbox have proven reliable, you can let it handle routine
mail on its own, like filing receipts. Fraud alerts always come to you either way.

## What you need

- **A Mac**, with disk encryption (FileVault) and a screen lock turned on. ecf runs on your
  computer; there's no website or cloud service to sign up for.
- **Your email**, with an "app password". This is a separate password your email provider
  gives you for programs like ecf, so ecf never needs your real one. Purelymail, personal Gmail
  ([Gmail instructions](https://github.com/rmarable/email-classify-filter/blob/main/docs/gmail-setup.md)) and other IMAP providers that offer app passwords are
  supported. iCloud Mail offers app passwords too but hasn't been tested yet. Microsoft 365 and
  Google Workspace don't work yet.
- **Slack**, where ecf sends alerts and you approve its suggestions. You need permission to add an
  app to your workspace.
- **Claude Code** and a Claude account, for sorting everyday mail.
- **Nothing else to install first.** The install script below sets up the rest. If you'd rather
  install by hand, you need [uv](https://docs.astral.sh/uv/), a tool that installs Python
  programs, and Python 3.12 (3.12.6 or later) or 3.13.

ecf works only while your Mac is on and awake. Nothing is lost while it's asleep (your email just
waits), but nothing is checked either. If ecf stops checking during business hours, Slack tells
you.

## Getting started

Download `install-ecf-macos.sh` from the [latest release](https://github.com/rmarable/email-classify-filter/releases/latest)
(under "Assets"). Open it in a text editor and read it first if you like; it's a plain script.
Then open Terminal and run it:

```sh
cd ~/Downloads
sh install-ecf-macos.sh
```

The script:

1. checks that FileVault is on, and warns you if the screen lock isn't;
2. installs whatever is missing: uv, gh (GitHub's command-line tool, which ecf uses to download
   upgrades) and Claude Code, each from its maker's own download, checked before use;
3. downloads ecf, checks it against the checksums published with it, and installs it.

It needs no administrator password (unless you use Homebrew, below) and asks before changing your
Terminal settings. Add
`--dry-run` to see what it would do without changing anything, or `--homebrew` to install uv, gh
and Claude Code with [Homebrew](https://brew.sh) instead.

At the end it offers to sign you in to GitHub (needed later for upgrades) and to start the setup,
`ecf init`. To start the setup yourself later, run:

```sh
ecf init
```

To install by hand instead, see the [admin guide](https://github.com/rmarable/email-classify-filter/blob/main/docs/admin-guide.md#install).

The setup tells you what to have ready, then walks you through connecting Slack, adding your first
mailbox, setting up backups and signing in to Claude. You can stop and come back later with
`ecf init --resume`. If anything isn't working, `ecf doctor` tells you what's wrong and how to
fix it. To remove ecf later, see [Removing ecf](https://github.com/rmarable/email-classify-filter/blob/main/docs/admin-guide.md#removing-ecf).

## Everyday use

- **Most of the time you're in Slack:** read the alerts, approve or reject suggestions, answer
  ecf's questions, and undo anything you change your mind about.
- **To sort the waiting mail**, run `ecf claude` and type `/ecf-review`.
- **To see what's waiting on you**, run `ecf inbox`. To see why ecf treated an email the way it
  did, run `ecf item show <id>`.
- **To stop everything at once**, run `ecf pause <address>`. Fraud alerts keep working.

## Guides

The guides live here on GitHub (they aren't included in the download):

- **[Daily use guide](https://github.com/rmarable/email-classify-filter/blob/main/docs/operator-guide.md):** Slack cards and alerts, approving and
  rejecting, answering ecf's questions, reviewing, pausing, and coming back after time away.
- **[Admin guide](https://github.com/rmarable/email-classify-filter/blob/main/docs/admin-guide.md):** setup, mailboxes, rules and settings, letting ecf do
  more on its own, sending email, backups, upgrades, and fixing problems.
- **[Gmail setup](https://github.com/rmarable/email-classify-filter/blob/main/docs/gmail-setup.md):** getting a personal Gmail account ready for ecf.

## Your privacy

Your email goes to four places only: your email provider, your own Mac, Slack (the subject,
sender and ecf's verdict, plus a short excerpt if you ask for one), and Anthropic, Claude's maker,
while you're running `/ecf-review`. ecf doesn't keep copies of your messages, doesn't log what they
say, and encrypts its backups with a key only you have. The full privacy statement is in
[SPEC.md](https://github.com/rmarable/email-classify-filter/blob/main/SPEC.md) (section 12.4).

If you use ecf for business mail, use a Claude Team or Enterprise plan. On a personal Pro or Max
plan, turn off the setting that lets Anthropic use your chats to improve its models.

## Important

ecf makes fraud much harder to miss, but it can't catch everything. **Always confirm a payment
request or a change of bank details by phone**, using a number you already have, not one from the
email. ecf comes with no warranty; see [DISCLAIMER.md](DISCLAIMER.md).

## Status

`v3.0.5` (2026-10-16) is the current version: `ecf upgrade` run from a copy of ecf that wasn't
installed the usual way no longer deletes a file another install needs to undo an upgrade. It runs
on macOS, for one person. Support for Linux, for teams, for an always-on version that doesn't need
your Mac, for connecting to Gmail directly through Google's own interface, and then for Google
Workspace (company and school) accounts are planned. What changed in each version is in
[CHANGELOG.md](CHANGELOG.md).

## For developers

[CONTRIBUTING.md](CONTRIBUTING.md) explains how to build and test ecf. [SPEC.md](SPEC.md) is the
full technical design, [SECURITY.md](SECURITY.md) covers security and how to report a problem, and
[docs/adr/](docs/adr/) records the decisions behind the design.

## License

You can use and change ecf for free, but you can't sell it or sell hosting or support for it
(Apache 2.0 with the Commons Clause). See [LICENSE](LICENSE).
