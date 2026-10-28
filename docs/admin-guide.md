# Setting up and running ecf

This guide is for whoever installs and looks after ecf: setup, mailboxes, configuration, backups,
upgrades and fixing problems. Everyday use (Slack cards, approvals, `ecf inbox`) is in the
[daily use guide](operator-guide.md). The complete technical design is in [SPEC.md](../SPEC.md).

**Before you rely on ecf, read the [disclaimer](../DISCLAIMER.md).** ecf makes fraud harder to miss
but can't catch everything, comes with no warranty, and leaves you responsible for what you approve
and for the mailboxes you connect.

Right now ecf is for one person, who is both the admin and the approver. It runs on macOS only;
Linux support is planned.

## Before you start

- **Step-up** is a second check at the computer itself: Touch ID or your Mac password. Commands
  that change something security-related ask for it, and `--help` marks them "(step-up)". A Slack
  click is never enough for these, so that someone with your Slack account alone can't do them.
- **Security Notice:** whenever a security-related setting changes, ecf posts a Security Notice in
  Slack (and alert email, if set up). If you see one you didn't cause, treat it as a warning.
- **Several installs:** `--install <name>` picks a separate copy of ecf on the same computer, for
  example a test one. Without it you get the install called `default`.

## Install

The easy way is the install script, `install-ecf-macos.sh`, from the
[latest release](https://github.com/rmarable/email-classify-filter/releases/latest); the
[README](../README.md#getting-started) walks through it. Its options:

| Option | What it does |
|---|---|
| `--dry-run` | Show each step without downloading or changing anything. |
| `--homebrew` | Install uv, gh and Claude Code with Homebrew (it must be installed already). |
| `--version vX.Y.Z` | Install that release instead of the latest. |
| `--python 3.13` | Use Python 3.13 instead of 3.12. ecf's tests run on 3.13 on Linux, not on macOS. |
| `--yes` | Take the default answer to every question. |
| `--allow-no-filevault` | Go on even though FileVault is off. Not recommended. |

The script keeps the downloaded ecf file in ecf's own folder (`~/Library/Application
Support/ecf/_installer`), because `ecf upgrade` needs it to go back if an upgrade fails. It also
writes down which tools it installed, so the uninstall script can offer to remove them.

**By hand:** install [uv](https://docs.astral.sh/uv/) and
[gh](https://cli.github.com), then sign in with `gh auth login`. Download and check the release:

```sh
gh release download --repo rmarable/email-classify-filter --pattern '*'
shasum -a 256 email_classify_filter-*.whl   # this number should match the one in SHA256SUMS
uv tool install --python 3.12 ./email_classify_filter-*.whl
```

Keep the `.whl` file where it is afterwards, for the same reason as above.

There's no PyPI package. `ecf upgrade` only works on an install made with `uv tool`. To build from
a checkout of the code instead: `uv build && uv tool install dist/email_classify_filter-*.whl`.

## First setup: `ecf init`

`ecf init` lists what to have ready, then goes through these steps, skipping any that are already
done:

1. Starts ecf's background service.
2. Checks that disk encryption is on and the Keychain works.
3. Asks whether this install is `prod` (real mail) or `test`. You only choose once.
4. Connects Slack (see [Slack](#slack)).
5. Adds your first mailbox (see [Mailboxes](#mailboxes)), including your organization's email
   domains (not asked for a Gmail address).
6. Alert email (optional, off by default).
7. Backups: creates a backup key, picks a folder and makes the first backup.
8. Signs in to Claude for `ecf claude`.
9. Confirms the service is installed and running.

If you stop partway, `ecf init --resume` continues; a step you said no to isn't asked again.
`ecf init status` shows each step. `ecf init --restore <bundle>` sets up a new computer from a
backup (see [Recovery](#recovery)).

Then run `ecf doctor`. Every mailbox starts in `shadow` (watch only) with sending off.

## The background service

ecf does all its work in one background service. On macOS it's a LaunchAgent that starts when you
log in. It only runs while the computer is on and awake.

| Command | What it does |
|---|---|
| `ecf service install` | Install the service and start it. |
| `ecf service start` / `restart` | Start it. Either one also clears the crash-loop stop (see [Recovery](#recovery)). |
| `ecf service stop` | Stop it until you next log in. Usually `ecf pause` is better, because it keeps fraud checks running. |
| `ecf service status` | Whether the service is installed and running. |
| `ecf service uninstall` | Stop it and remove it. Your data is kept. |
| `ecf service regrant` | Let ecf read the Keychain again after Python changed (see [Recovery](#recovery)). |
| `ecf watch` | Run the service in this Terminal window instead, for watching what it does. |

ecf's fraud and regulator checks run on every email as it arrives. Claude only classifies
everyday mail when someone runs `/ecf-review` in `ecf claude`.

## Secret store

ecf keeps every password and token (mailbox app passwords, Slack tokens, API keys) in the macOS
Keychain, and nowhere else: never in files, settings or environment variables. Only the background
service reads and writes them. If the Keychain is locked or stops letting ecf in, see
[Recovery](#recovery).

## Mailboxes

```sh
ecf address add ap@acme.example --imap-host imap.acme.example \
    --sensitivity high [--id ap] [--smtp-host … --smtp-port 465|587]
```

- `add` asks for the mailbox's app password (typed hidden, so run it in a real Terminal window).
  Before saving it, ecf logs in to check it works, and checks it can reach the outgoing mail
  server too (without sending anything). Then it looks over the mailbox.
- Your organization's domains are asked for with the first mailbox only. Change them later with
  `ecf config apply`.
- One install can watch work and personal mailboxes together.
- `--id` sets a short name for the mailbox to use in commands (for example `ap`).

**Sensitivity:** `standard`, or `high` for finance mailboxes. `high` means a stricter go-live
test, extra checks, and a 10-minute pause before any email is sent. `ecf sensitivity set <address>
high` takes effect at once. Lowering it needs a reason and step-up, and sends a Security Notice.

**Outgoing mail server:** by default ecf uses the incoming server's name with `imap.` changed to
`smtp.`, on port 465. `ecf address set <address> --smtp-host <host> [--smtp-port 587]` changes it.
Because the app password is sent there, this needs step-up and sends a Security Notice.

**Other commands:**

- `ecf address list` lists your mailboxes.
- `ecf address retry <address>` checks at the next minute, even if login failures have slowed ecf
  to hourly retries.
- `ecf address remove <address>` closes the mailbox's open items (step-up if any involve payments or
  fraud), archives its Slack channel and deletes its app password. ecf's labels stay on your
  messages. You can't remove a mailbox that sends ecf's alert email.

**Senders:** two commands tell ecf about a particular sender (add `--address` if you have more
than one mailbox):

- `ecf sender set-reply-to <sender> <domain>` says which reply address domain to expect, so ecf
  stops flagging that sender's mail for replies going somewhere else. `--clear` undoes it.
- `ecf sender set-verified <sender>` marks a sender as checked by a person. That only turns off
  the "payment from an unverified sender" flag for their mail; all fraud checks stay on. `--off`
  undoes it.

Both need step-up; undoing them doesn't. `ecf sender show` and `ecf sender confirm` are in the
daily use guide.

## Older mail: backfill and reprocess

ecf reads a mailbox from its first check onwards. To look at mail that arrived before that, use
`ecf backfill`:

```sh
ecf backfill ap --since 2026-09-01 [--until 2026-09-30T18:00]   # only records; changes nothing
ecf backfill ap --since 30d --act        # counts, estimates the cost, asks; step-up over 500 emails
ecf backfill ap --stop                   # stop; what was read is kept
ecf reprocess ap --since 90d [--rules-only] [--force] [--dry-run]   # look again after a change
ecf reprocess --status | ecf reprocess ap --stop
```

- **Times** can be a date, a date and time (`2026-09-29T08:00`, your local time unless you add `Z`
  or an offset like `-04:00`), a time today (`08:00`), or an age (`90s`, `30m`, `2h`, `3d`, `2w`,
  `6mo`, `1y`, or combined like `1h30m`).
- **Old mail doesn't flood Slack.** Each run posts one summary in the summary channel, with fraud
  and regulator finds as replies underneath. Anything it would hide needs your approval, all at
  once with `ecf approve --scan <id>`. New mail still comes first.
- **Cost:** when Claude will classify the mail, ecf shows an estimate first, based on your own
  past reviews if it has enough of them.
- `reprocess` skips mail you've already reviewed and mail that raised a fraud or regulator alert,
  and never moves mail ecf already moved. `ecf item undo <id>` puts one back.

## Gmail

Personal Gmail accounts (`@gmail.com`, `@googlemail.com`) work with an app password. Google
Workspace (company) accounts don't yet. Do Google's side first, using
[Gmail setup](gmail-setup.md): turning on 2-Step Verification, creating the app password, two
Gmail settings, and what to do when the account belongs to someone else.

```sh
ecf address add pat.lee@gmail.com --sensitivity high
```

- You don't need `--imap-host`; ecf uses Gmail's servers.
- It doesn't ask for organization domains, since `gmail.com` is never "internal".
- It starts with a limit of 100 sends a day (Google allows 500). `add` prints the command to change
  it: `ecf address set <address> --max-sends-per-day <n>` (step-up).
- If you haven't listed the people you work with yet, it suggests doing so (below).

**The people you work with (`org_addresses`):** with personal accounts there's no company domain to
recognise colleagues by, so you list them, in the config file applied with `ecf config apply`.
Each entry is an exact address at any provider, with an optional name of at least two words:

```yaml
org_addresses:
  - {address: pat.lee@gmail.com, name: Pat Lee}
  - {address: dana@acme.example, name: Dana Ruiz}
```

- Mail from a listed address that passes ecf's authenticity check counts as internal. Mail claiming
  to be from a listed address that fails the check raises a fraud alert, unless Gmail shows it's a
  note you sent yourself.
- Mail pretending to be a listed person (their name or address shown as the sender's name, a
  one-letter typo of their Gmail address, or the same address at another provider) is labelled
  `suspicious` and flagged, and escalated if it's about money. The daily use guide shows
  [what you see](operator-guide.md#mail-pretending-to-be-someone-you-work-with). ecf doesn't learn
  names: people you haven't listed aren't covered.
- At most 50 entries, and a one-word name is refused. Gmail ignores dots and `+tags`, so
  `patlee@gmail.com` and `pat.lee+x@gmail.com` are the same account and can't both be listed.
- Only Gmail is known to stop one account sending as another. An address at another provider is
  only as trustworthy as that provider.

**Forwarding to a personal account:** a forward can only go to an address in your organization's
domains or listed exactly in `org_addresses`, and never to a mailbox ecf watches. When you add one
at a public provider, the step-up dialog warns "<address> is a personal account your organization
doesn't control". You can't remove a domain or address that a forward still needs. Don't forward
someone else's mail to a personal address without their agreement; ecf can't check that for you.

**What `ecf doctor` may say about Gmail:**

| Row | Warning | Fix |
|---|---|---|
| `gmail <id>` | All Mail isn't shown over IMAP: archive fails and the email stays in the inbox | In Gmail settings, Labels, tick "Show in IMAP" for All Mail. Then run `ecf address set <id> --app-password` so ecf checks again. |
| `gmail <id>` | IMAP shows N of M inbox messages | In Gmail settings, Forwarding and POP/IMAP, set Folder size limits to "Do not limit". |
| `org domains` | no org domains or org addresses: mail pretending to be people you work with isn't detected | List your colleagues in `org_addresses`, or your domains in `org_domains`. |

`ecf address add` prints the same warnings when it first checks the mailbox.

**Download limit:** Gmail limits how much can be downloaded in a day. ecf stops before it reaches
2,500 MB in 24 hours and tells you (`Operator Input Needed: Gmail download limit reached`). New
mail just waits, nothing is skipped, and it carries on by itself.

**"Mailbox Login Rejected" on Gmail:** changing your Google password cancels every app password
([what to do](gmail-setup.md#after-a-password-change)). Create a new one, then run
`ecf address set <address> --app-password`.

## Configuration

**Security settings** (organization domains and addresses, the forward allow-list, folders ecf may
move mail to, reply templates, what ecf may do on its own, and your rules) can only be changed
through a YAML config file. That way every change is reviewed in one place:

```sh
ecf rules test rules.yaml        # try new rules on ecf's test emails and show what changes
ecf config apply config.yaml     # checks the file, shows the changes, asks for step-up, posts in Slack
```

The file's full format is in SPEC.md (§9.7).

### Rules

Rules decide what happens to an email once it's classified: label it, flag it, file it, and so
on. They run in three layers, in this order:

1. **ecf's fixed rules**, always first and never changeable: fraud, regulator mail, weak fraud
   signals, payments from unverified senders, and phishing.
2. **Your rules**, in the order they appear in your file.
3. **ecf's default rules**, in their own order: `bug_report`, `invoice`,
   `payment_confirmation_remittance`, `account_security`, `shipping`, `appointment_school_family`,
   `travel_finance`, `marketing`, `automated_notification`, `requires_reply` and `otherwise`.

You don't need to repeat ecf's rules in your file. Some ways to shape the defaults:

- **Replace a default:** give your rule the same id as the default. Yours then runs in the
  default's place. (Give it a different id to keep both.)
- **Turn defaults off:** add `default_rules_off: [marketing, ...]` next to `rules:`.
- **Replace all the defaults:** make your file's last rule one with no `when`. It matches
  everything, so the defaults never run.

`ecf config apply` refuses a file that would be confusing, and says why: a rule with no `when`
anywhere but last, a rule reusing a fixed rule's id, an off list naming a rule that doesn't exist,
and so on. Its dry run says which layout applies, which defaults you replace or turn off, and
which of your rules can hide mail.

Things to know when writing conditions:

- A rule can't test the values removed in v3.0.0 (`category` `vendor_change_request`, `regulatory`
  and `spam_or_phishing`; `sender_type` `team`). Use the yes/no safety fields instead:
  `bank_detail_change`, `regulator_claim`, `phishing` or `claims_internal: true`.
- If an email's classification doesn't have a value a condition tests, the condition is treated as
  unknown and never matches, even inside `not`.
- A catch-all at the end is optional; ecf adds `otherwise` for you.

**Rules files from before v3.0.0:** a file applied before v3.0.0 that doesn't end in a rule with no
`when` now runs *before* the defaults instead of replacing them. `ecf config apply` and
`ecf doctor` point this out until you apply rules again. To keep the old behaviour, end the file
with `- {id: otherwise, actor: continue}`.

### Your own classification fields

ecf can be built to accept extra fields and categories of your own, but **this is turned off in
v3.0.0**: it didn't pass its accuracy testing. `ecf config apply` refuses a `schema` section with
"your fields are off in this release". SPEC.md (§7.1) describes how it will work.

### Settings

`ecf settings show` lists every setting you can change, with its current value.
`ecf settings set <key> <value> [--address <address>]` changes one. A few settings belong to their
own commands (Slack member, alert routes, backup folder, send limits, sending, stage); trying to
set them this way tells you which command to use.

A change to a reply template or the forward allow-list cancels sends that were approved before it.

### Retention

`ecf retention show`; `ecf retention set <days>` (1 to 3650, step-up). Lowering it sends a Security
Notice. The audit log and anything with a fraud or regulator warning are never deleted.

## Stages: letting ecf do more

`ecf stage status` shows each mailbox's stage, how long it's been there, review progress and
anything held.

| Stage | What ecf does | How to get there |
|---|---|---|
| `shadow` | Decides and tells you, but changes nothing. | Where every mailbox starts. Going back is instant. |
| `assist` | Labels, flags and escalates; holds anything else for you. | `ecf stage set <a> assist` (step-up) |
| `live` | Does everything its policy allows. | `ecf stage set <a> live` (step-up, plus the go-live test) |

**The go-live test** checks that ecf has proven itself on that mailbox:

- **Reviews:** a `standard` mailbox needs 100 reviewed emails with at least 85% correct; a `high`
  one needs 200 with at least 90%. A review counts as correct when the Fix didn't change anything.
- **Safety:** no missed fraud, no unsafe suggestions for payment or fraud emails, and no injection
  attacks followed, on ecf's set of test emails. You get this by running an eval (below).

`stage set live` tells you if an eval result is missing. `--override --reason <text>` can waive the
number of reviews or the accuracy, but never the safety checks. `--held run|resolve-older|resolve-all`
decides what happens to emails being held (default `run`: those up to 7 days old run, older ones
stay held).

**Evals:** `ecf eval run --claude --preset C [--fraud-only]` sets up a test run on ecf's synthetic
test emails. You start it by typing `/ecf-eval` in `ecf claude`; it uses your Claude plan.
`ecf eval status`, `stop` and `compare` follow it up. `ecf eval run --classifier-backend null` runs
ecf's rules alone, as a quick wiring check that never counts for going live.
To test ecf on mail another company's model wrote, see
[Testing ecf on mail another model wrote](#testing-ecf-on-mail-another-model-wrote).

**When ecf's Claude models change:** a release that changes which Claude model ecf uses drops every
mailbox from `live` to `assist` until it passes the go-live test again. v3.0.0 did this when it
moved every role to Claude Opus 5.5. A release that changes the instructions ecf gives Claude for
classifying mail or proposing actions does the same: every `live` mailbox goes back to `assist`
until it passes the test again.

## Models

- `ecf models status` shows which Claude models ecf uses, and any override, and the Jev version
  used for testing.
- `ecf models api-key set|clear --provider anthropic` stores or removes an optional Anthropic API
  key, used only to check weekly for new or retiring Claude models. ecf tests the key once before
  saving it, and posts a Security Notice whenever it's added, changed or removed.
  `ecf models watch` runs that weekly check at the next minute.

ecf can also run its test emails through TypeSafe's Jev model, to compare it with Claude
(`ecf eval run --classifier-backend jev`). This is for testing only: none of your mail goes to
TypeSafe, and mailboxes can't use Jev. Jev reads the same first 4,000 characters of each test
email that Claude does. To use it, store a TypeSafe key with
`ecf models api-key set --provider typesafe`. While a key is stored, the weekly check also asks
TypeSafe which Jev version is its latest, sending no email content, and `ecf models status` says
when it's a different version than the one ecf uses. ecf never switches versions by itself.

## Testing ecf on mail another model wrote

ecf's own test emails were written by Claude, and ecf classifies mail with Claude, so its test
scores show Claude reading Claude's mail. For a second opinion you can build a **generated test
set**: another model writes a few hundred fake emails, the emails are labelled (what type each is,
whether it's phishing, whether it asks for money, and so on), and ecf then runs on the set as it
runs on its own test emails. A generated set is for your information only: it never counts toward
the go-live test.

Nothing from your mailboxes or your ecf data goes to these models. The model that writes the
emails (the **writer**) gets ecf's published list of email types (scam types from the FBI's crime
reports, ordinary mail from Gmail's inbox categories), a made-up company to write to, and rules
that keep the emails fake (no real companies, addresses or account numbers). A model that labels
them (a **grader**) gets each fake email and the meaning of each label. Never put real mail in a
generated set.

### Two ways to label a set

- **You label every email; one grader checks you.** OpenAI's model writes the set, Mistral's
  grades it, and you label every email yourself without seeing the grader's answers. The report
  then shows how often the grader agreed with you. This is the most trustworthy, and the most
  work.
- **Two graders label it; you decide where they disagree.** The two graders are called
  **judges**, and they must come from two different companies, neither of them the writer's
  company or Claude's. With the models ecf accepts today that means Claude writes the set and
  Mistral's and OpenAI's models judge it. Claude is then scored on mail Claude wrote, which is
  what this test is meant to avoid, so you have to ask for it (`--claude-generator`) and the
  report says so beside every figure. You answer only the disagreements that matter: replayed
  on ecf's first 300-email set, that comes to about 150 of the 300 emails.

ecf refuses models from some labs, and accepts only the models on its list for each job; the
error message names the ones it would take.

### What you need

- **For OpenAI's model** (`gpt-6.1-sol` by default, as writer or judge): OpenAI's Codex
  command-line tool and a ChatGPT plan. ecf keeps a separate Codex sign-in for itself, so install
  Codex and sign it in once for ecf (replace `default` with your install's name if you use
  `--install`):

  ```sh
  CODEX_HOME="$HOME/Library/Application Support/ecf/default/codex-home" \
    codex login --device-auth -c cli_auth_credentials_store="keyring"
  ```

  The device sign-in needs device code sign-in turned on in ChatGPT's settings; the last part keeps
  the sign-in in your Keychain rather than in a file. A ChatGPT plan has usage limits, so a run may
  stop part-way: run the same command again with `--resume` later. In ChatGPT's settings, turn off
  "Improve the model for everyone", and don't rate these runs with a thumbs up or down.
- **For Mistral's model** (`mistral-large-4-0`, as grader or judge): a paid Mistral API account.
  Store its key with `ecf models api-key set --provider mistral` (it's kept in your Keychain), and
  set a spending limit in Mistral's console as well. In the console's Privacy settings, opt out of
  having your data used for training. ecf stops a grading run before it would pass
  `--max-cost` (US$15 by default); grading 300 emails cost about US$1 in October 2026.
- **For Claude as the writer:** ecf's Claude sign-in (`ecf claude --login`, on your own Claude
  plan). With Claude writing, 300 emails took about 25 minutes.

### Example: you label every email

The set lives in a new, empty folder; here `~/ecf-sets/first-300`. Each command prints the next
one.

```sh
ecf eval generate --root ~/ecf-sets/first-300                        # OpenAI writes 300 emails
ecf eval grade --root ~/ecf-sets/first-300                           # Mistral grades them
ecf eval label --root ~/ecf-sets/first-300 --review all --add        # you label each email
ecf eval expect --root ~/ecf-sets/first-300                          # what ecf's rules should do
ecf eval label --root ~/ecf-sets/first-300                           # you confirm each result
```

- **generate** writes the emails in batches and checks each one as it's made: anything that looks
  like a real address, phone number, account number or brand is dropped and another is written.
  If the model refuses to write one, that's recorded and not retried. `--count` sets how many to
  keep (300 by default).
- **label --review** shows you each email as your mail program would, in random order, and asks
  each question from the email alone.
- **expect** works out, from your labels, which of ecf's rules should handle each email and
  whether hiding it would be unsafe; you then confirm each.

### Example: two judges

```sh
ecf eval generate --root ~/ecf-sets/first-300 --generator claude:claude-sonnet-5-5 --claude-generator
ecf eval grade --root ~/ecf-sets/first-300 --grader mistral:mistral-large-4-0
ecf eval grade --root ~/ecf-sets/first-300 --grader codex:gpt-6.1-sol
ecf eval agree --root ~/ecf-sets/first-300     # labels both judges gave are kept
ecf eval expect --root ~/ecf-sets/first-300    # what ecf's rules should do with each email
ecf eval settle --root ~/ecf-sets/first-300    # you decide where the judges disagreed
ecf eval expect --root ~/ecf-sets/first-300    # again, after settling
```

- **agree** keeps every label the two judges answered the same way. Where they differ, the email
  waits for you. Some differences matter only if they change what ecf should do (the email's type,
  or whether it mentions a deadline); `expect` checks those and sends you only the ones that do.
  The safety questions (phishing, fraud risk, changed bank details and the like) are always yours
  when the judges disagree.
- **settle** shows you each waiting email as your mail program would, one at a time and in random
  order, with only the questions the judges disagreed on. You answer before you see what the
  judges said. If the second `expect` sends more emails back, settle and expect once more.

### Running ecf on the set and reading the report

```sh
ecf eval run --root ~/ecf-sets/first-300 --claude   # then type /ecf-eval in `ecf claude`
```

Run it twice. Two runs of the same models show how much the scores move by chance alone;
`ecf eval compare <result 1> <result 2>` puts them side by side, and `ecf eval status` lists the
result files. Then:

```sh
ecf eval report --root ~/ecf-sets/first-300 <result files> ~/ecf-sets/first-300/grades/*.json
```

The report starts with each grader's accuracy, measured only against labels you gave. With two
judges it then scores ecf three ways: against the labels the judges agreed on, against the ones
you settled, and against both. Read the second closely: those are the emails the judges found
hard, and your answers there are the only ones no model set. A question the judges often disagreed
on is shown but not scored, except the safety questions, which are always scored. How a set is
built and checked is described in [SPEC.md](../SPEC.md) section 16.

## Slack

- `ecf slack install` asks for a Slack configuration token (typed hidden) and creates ecf's Slack
  app. After you click "Install to Workspace" it asks for the two tokens Slack shows you, then
  sends a Confirm button to your Slack account. ecf then creates its summary channel and one
  channel per mailbox.
- `ecf slack status` shows the app, workspace, your Slack account, the connection and the channels.
- `ecf slack set-member <id>` (step-up) moves approvals to a different Slack account. The old one
  keeps working until the new one clicks Confirm.
- `ecf slack set-tokens` (step-up) replaces both tokens.
- `ecf slack reauthorize` uses a new configuration token to update the app's permissions, then
  puts ecf's cards back in Slack (as `ecf slack repost` does).
- `ecf slack repost` puts ecf's cards back in Slack: it updates the cards already there, and cards
  of emails waiting for your approval that aren't in Slack come back over the next minutes.

If your workspace doesn't let apps create channels, `ecf slack status` says which private channel
to create and add ecf to.

## Alerts

- `ecf alerts show` lists where each kind of alert goes.
- `ecf alerts set [mail|system|operator|slack] --to slack|email|slack,email` (step-up) changes it.
- `ecf alerts test` sends a test alert everywhere.
- `ecf alerts email set --from <watched address> --to <destination>` (step-up) turns on alert email:
  ecf sends it from one of your mailboxes to an address it doesn't watch. It sends a Security
  Notice and a test email first. `ecf alerts email off` (step-up) turns it off.
- Alert email never contains a subject, sender or email text.
- `ecf settings set notifications off` turns desktop notifications off.

## Sending email

Sending is off for every mailbox until you turn it on.

- `ecf outbound report <address>` shows what ecf would have sent, your reviews of those, what a
  `high` mailbox still needs before it can send, the send limits, recent sends and reminders.
- `ecf outbound enable <address>` (step-up, Security Notice) turns sending on. A `high` mailbox
  must first have 20 held-back sends reviewed, with 95% marked correct. There's no override.
- `ecf outbound disable <address>` turns it off at once, cancelling sends in their 10-minute pause
  and any already approved.
- **Send limits** are 25 an hour and 250 a day by default.
  `ecf address set <address> --max-sends-per-hour <n> --max-sends-per-day <n>` (step-up, Security
  Notice) changes them. Hitting a limit holds that mailbox's sends until `ecf outbound resume
  <address>` (step-up). Resuming never turns sending back on.
- `ecf outbound snooze <address> [--days N]` and `ecf outbound dismiss <address>` quiet the
  "sending is off" reminders.

## Backups, moving and restoring

### Setting up backups

`ecf init` does this for you. To do it by hand:

```sh
ecf export keys rotate       # creates a new backup key, shown once (step-up)
ecf export dir set <folder>  # where backups go, ideally not on this computer's disk (step-up)
```

When the key is shown, you type `saved` and then its fingerprint, to show you've kept it. **Keep the
`ECF1-…` key in a password manager.** ecf itself can't read its own backups, so without the key
nobody can. iCloud Drive, other cloud folders in `~/Library/CloudStorage` and external disks count
as off this disk; `ecf doctor` warns if the folder is on the same disk.

### How backups run

A backup is made daily by default (`export_schedule` in the config file: `daily`, `weekly` or
`off`), and the newest 14 are kept (`ecf settings set export_keep <n>`).

- `ecf export status` shows the last backup and any failures.
- `ecf export now` makes one straight away.
- `ecf export keys show` shows the key's fingerprint.
- Two failures in a row raise a System Error.

### Moving data to another install

`ecf export --to <file or folder>` (step-up) writes a bundle protected by a passphrase (ecf suggests
one made of six words). It doesn't need the backup key, so use it to move data.

To bring it in: `ecf import <bundle> --dry-run` first, then `ecf import <bundle>` (step-up).
`--replace` replaces this install's mailboxes and emails (it asks you to type the install's name).
Imported mailboxes arrive paused, at most at `assist`, with sending off. Passwords, Slack, the
backup key and alert email have to be set up again.

### Restoring this install

`ecf restore <bundle>` (step-up) needs the backup key (and the passphrase, for a manual export).
It shows what it will do, warns if the bundle is older than your newest backup, asks you to
confirm ecf isn't running anywhere else, and keeps a copy of the data it replaces for 7 days.
Mailboxes come back paused at their old stage. `ecf resume <address>` works once a mail check has
run since the restore.

## Upgrading and going back

`ecf upgrade` installs the newest release from GitHub, using your signed-in `gh` command.
`ecf upgrade --to vX.Y.Z` installs a particular version; naming an older version goes back to it.
ecf refuses a download unless its checksums match the release's published ones.

On a `test` install you can also upgrade from a wheel file (a `prod` install refuses):

```sh
ecf upgrade --wheel <file> --check   # run every check, change nothing
ecf upgrade --wheel <file>
```

An upgrade waits if anything is running: an action in progress, an open `ecf claude` session, or
`ecf watch`. It saves a snapshot of the database, keeps the old version, installs the new one,
updates the database, re-allows Keychain access and starts the service. If any of that fails, it
puts the old version back by itself.

**Going back:** `ecf upgrade --to <older version>` uses that upgrade's snapshot (the newest 2 are
kept). If you go back before the new version has run properly once, everything returns as it was.
After that, send history, sender records and go-live history are kept; mail since the upgrade is
read again; open approvals need deciding again; actions that were running are marked as unknown;
and every mailbox is paused, at most at `assist`.

### Upgrading to v3.0.0

- **Classification changes.** Five yes/no safety questions now sit beside the categories:
  bank-detail change, claims to be from a regulator, phishing, claims to be a colleague, and from a
  private individual. They replace four old values (`category` `vendor_change_request`,
  `regulatory`, `spam_or_phishing`, and `sender_type` `team`). A fake email now gets the category
  of whatever it's pretending to be. Your stored mail is updated automatically.
- **The go-live test starts again** for every mailbox, and live mailboxes drop to `assist`.
- **Your rules file:** copies of the old starter rules in an applied file are cleaned up once,
  automatically (and logged). A copy you changed stops that: ecf shows a System Error and uses its
  default rules until you remove it. The junk rule is now called `phishing` (it was
  `spam_or_phishing`); if you have your own rule with either name, rename it before upgrading.
  Mail labelled as phishing before the upgrade keeps the old label
  (`$ecf_<install>_spam_or_phishing`); new mail gets `$ecf_<install>_phishing`. Undo works on
  both.
- **Claude only.** v3.0.0 removes the local AI model (Gemma on Ollama) and the presets that used
  it. See [Converted mailboxes](#v300-converted-addresses).
- **Every Claude role now uses Claude Opus 5.5**, which costs more per email than before.
- **Backups:** v3.0.0 reads backups of format 3 and 4. Export an older backup again on v2.2.x
  before bringing it in.

### v3.0.0: converted addresses

Mailboxes that used the local model are switched to Claude and **paused** during the upgrade. The
upgrade output, a Security Notice and `ecf doctor` name each one and how many emails are waiting.
While paused, fraud and regulator checks still run on new mail, but nothing goes to Anthropic.

- **Ask first.** From now on that mailbox's mail goes to Anthropic whenever someone runs
  `/ecf-review`. If the mailbox belongs to someone else (for example a Gmail account you watch with
  its owner's agreement), ask them before you resume it.
- **Resume** with `ecf resume <address>`, which asks you to confirm the owner agreed. Waiting mail
  is classified at the next `/ecf-review`. The mailbox restarts at `assist`.
- **Or remove it** with `ecf address remove <address>`.
- **What's different:** mail is only classified when someone runs `/ecf-review`, so plan on one at
  least every working day. ecf reminds you after 24 hours and alerts you more loudly after 48.
- **Ollama:** once the upgrade has settled, ecf removes its own Ollama login item and log folder.
  Ollama itself and its model copies are yours to remove (`ollama rm` for each `ecf/gemma4-12b:*`).
- **Going back** to v2.2.x restores the old setup from the snapshot, with Ollama still in place.

### Older upgrades

What changed in each earlier version, including v2.0.0's new categories, is in
[CHANGELOG.md](../CHANGELOG.md).

## Removing ecf

`ecf destroy [--config-token]` (step-up) removes an install completely. It refuses while anything
is running or an upgrade hasn't settled, offers a backup if none was made in the last 24 hours, and
asks you to type the install's name. Then it archives the Slack channels, deletes the Slack app
(with `--config-token`; otherwise it turns the app off), deletes every stored password and token,
removes the service, signs `ecf claude` out and deletes ecf's data. Your backups and mailboxes
aren't touched.

At the end it lists what's left for you: app passwords to cancel at your mail provider, the Slack
app if it was kept, and Ollama if you used it with ecf before v3.0.0. If it's interrupted, run it
again to finish.

**Removing everything:** `uninstall-ecf-macos.sh`, from the same release page as the install
script, runs `ecf destroy` for each install on this Mac (it asks before each one, and whether you
have a Slack configuration token), then removes ecf itself and lists what's left. Last, it offers
to remove the tools the install script put there (uv, gh, Claude Code); the answer is no unless
you say yes, since other programs may use them. Add `--dry-run` to see each step first.

## Health checks

`ecf doctor` checks everything ecf depends on (the service, the Keychain, disk encryption, Slack,
each mailbox's incoming and outgoing servers, Claude, alert email and backups) and prints a fix
for anything wrong. Also useful:

- `ecf status`: mailboxes, backlog and the service.
- `ecf logs [--address] [--event] [--since] [--until] [-f]`: the audit log (never email content).
- `ecf stats --stages`: how long mail spends at each step. `ecf stats`: Claude usage and Slack
  timings.

## Recovery

**Emails that need a person:** `ecf inbox` lists them and `ecf item show <id>` explains one. For
an action that failed, may or may not have happened (`failed_unknown`), or seems stuck:

- `ecf item requeue <id>` tries again. For a send, only once ecf is sure it didn't go out (the
  server refused it, or it's missing from Sent on two later checks); it needs a new approval and
  step-up.
- `ecf item resolve <id> --reason <text>` closes it without acting.
- To close many old items at once: `ecf item resolve --older-than <days> [--address <a>] --reason
  <text>`.

**Slack token revoked:** `ecf slack set-tokens`; then `ecf slack repost` if cards are missing.
(`ecf slack reauthorize` is only for when Slack says the app is missing a permission.)

**"Mailbox Login Rejected"** (the app password changed or was cancelled): create a new one at your
provider, then `ecf address set <address> --app-password`. Checks resume and a "Resolved" notice
follows.

**Keychain locked, or "Python changed":** after the Python that ecf uses is updated, macOS may stop
letting the service read its Keychain items, and it waits with "secret store needs you". Run
`ecf service regrant`. It stops the service, reads each item with the Keychain dialog showing
(enter your login password and choose Always Allow), and starts the service again.

**Crash loop:** if the service crashes 5 times in 10 minutes, it posts why and stays stopped until
`ecf service start`. Check `ecf logs` and `ecf doctor` before starting it. An email that crashes
the service twice is set aside as quarantined and escalated, so the rest keep flowing.

**Mailbox reset:** sometimes a mail provider renumbers a mailbox. ecf notices, finds its messages
again, and carries on by itself. Items it can't find again are closed. Nothing for you to do.

**Very large emails:** emails over 16 MB (64 MB on a `high` mailbox) are only partly scanned: the
headers, and the first 10 MB of each text part. You can change both per mailbox, in MB from 1 to
64: `ecf settings set max_message_bytes <size> --address <a>` and `ecf settings set
max_scan_bytes_per_part <size> --address <a>`.

**Two copies of ecf on one mailbox:** if ecf sees another install's labels, or its own mail marked
by another copy, it pauses the mailbox with "possible second install". Stop the other copy, then
`ecf resume <address>`.

**Lost backup key:** there's no way to recover it, and scheduled backups made with it can't be
read. A manual export can still be brought in with its passphrase, using `ecf import` (not
`ecf restore`, which needs the key). Run `ecf export keys rotate` for a new key, then
`ecf export now` so you have a readable backup again.

**Stolen laptop:** disk encryption and a screen lock are what protect it. Then, from another
computer:

1. Cancel each mailbox's app password at your mail provider (Gmail:
   [how](gmail-setup.md#revoking-ecfs-access)).
2. Cancel ecf's Slack tokens, or remove its app, in Slack.
3. Sign ecf's Claude login out in your Claude account.

On a new computer, run `ecf init --restore <bundle>` with your backup key. It lists what's left to
do: new Slack tokens, `ecf address set <a> --app-password` with the new app passwords, `ecf check`,
`ecf resume` and `ecf doctor`. ecf can tell mail sent by the old computer apart from the new one.
