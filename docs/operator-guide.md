# Operator guide

Day-to-day use of ecf. Setup, configuration, stages, backups, upgrades and recovery are in the
[admin guide](admin-guide.md). `SPEC.md` is authoritative; this guide points into it. Every
command has `--help`, which marks the ones that need step-up.

In v1 one person is both operator and admin.

## What you see in Slack

ecf posts in private channels it creates and invites you to (SPEC §10.1):

- **`ecf-<install>-<address_id>`**, one per watched address: cards for emails that need you,
  digests, review posts.
- **`ecf-<install>-summary`**: the pinned "Needs you" message, the daily summary, alerts, notices.

Every click is accepted only from your member ID. Clicks reach ecf only while the computer is
awake; "Needs you" says "Buttons work only while <computer> is awake. Last connected <time>."
A button that opens a form needs the computer awake (SPEC §10.1, Sleep).

**"Needs you"** (pinned in the summary channel) lists what `ecf inbox` lists: the top 20, stale
first, each labelled by what it waits for, plus paused addresses, and Pause all / Resume all.

**Item cards** show the sender, subject, why ecf flagged it, the sender check, flags (first-time
sender, Reply-To mismatch, not addressed to this mailbox, not fully scanned, bulk), what was done,
and `ecf item show <id>`. Payment and fraud items say "This acts on the email only. ecf never pays
anything." No email body is posted; **Show excerpt** shows the first 200 characters of the stored
excerpt to you only (SPEC §9.2).

**Escalations** (possible fraud, regulator mail) get their own card and mention you, so you're
notified on desktop and phone. Fraud, quarantine and regulator escalations are never rolled up;
others beyond `escalations_per_hour` roll into one "N more escalations" thread per hour (SPEC
§9.2).

**Digests** post at most hourly per address, in business hours only, and only when mail came in.
They list weak fraud signals and unverified payment senders with what was done, with Undo and
Pause buttons. After a gap of more than 2 hours a digest opens with "Caught up: N messages since
<time>". `ecf digest <address>` posts one now. A digest's **Approve all N reversible** approves
the fixed set it lists; it never includes sends, fraud-guard, unverified-sender, regulator,
not-fully-scanned or `high`-address items (SPEC §9.5).

**Review posts** (one per address channel per business hour, at most 20 emails) list what the
model said about each email. See [Reviewing](#reviewing-shadow-and-assist).

**The daily summary** posts at the start of business hours: open, stale and expired items,
not-fully-scanned counts, held-back sends, the last backup, backlog and hours on battery, newer
models and releases, and anyone in ecf's channels besides you and the bot (SPEC §10.1).

**Alerts** read `[ecf-alert] <Title>`, and `[ecf-alert] Resolved: <Title>` when they clear. The
titles and what raises each are in SPEC §13.3; the ones you'll see most:

| Title | What to do |
|---|---|
| `Operator Input Needed: <condition> (<address>)` | the condition names it: approvals queued for step-up, stale items, outbound off, send limit reached, Claude review waiting, a send scheduled in 10 minutes, a possible second install |
| `Possible Fraud Attempt`, `Regulatory Mail Notice` | open the escalation card; check out of band before acting |
| `Mail Provider Unreachable`, `Mailbox Login Rejected` | see the admin guide (recovery) |
| `System Error`, `Slack Delivery Failed` | run `ecf doctor`; see the admin guide |
| `Security Notice` | a security-relevant change was made; if it wasn't you, see the admin guide |

**The dead-man's message**: "ecf hasn't checked in since <time> (<computer>)" posts if the computer
sleeps, dies or the service stops without a clean stop; by default only in business hours
(`deadman_offhours`, SPEC §10.1).

## Approving and rejecting

Approve and Reject buttons name the action ("Approve: archive email"). The first decision wins.
Approvals expire after 4 days for sends and 14 days for everything else; on expiry nothing runs
(SPEC §6.5).

- **Reversible actions** (mark read, archive, move, junk, save a draft) are one click in Slack.
- **Step-up actions** wait for you at the computer: every send, every irreversible action, and
  hiding a fraud, quarantine or regulator email. Clicked in Slack, the card says "Queued for your
  computer (N waiting)", keeps only Reject, and a desktop notification names the command:

  ```sh
  ecf approve --pending     # lists them (sends separately), asks, one step-up for the non-sends
  ecf approve <id>          # one item: shows what it does (a draft in full), asks; every send needs its own
  ecf reject <id>
  ```

  Step-up is Touch ID or your password (Linux: your password via PAM, unverified until V1.6). The
  dialog names the action, sender, subject and address, plus a 4-character code that the CLI
  prints too; check they match (SPEC §9.6).
- **Sends on `high` addresses** then wait 10 minutes of awake time, announced in Slack with
  **Cancel** (and emailed when alert email is on). `ecf cancel <id>` does the same.

## Answering ecf's questions

When the model isn't sure it asks, on a card headed "ecf has a question (round N of 2)". The
question is model output and can be wrong (SPEC §9.9).

- **Answer** opens a form (computer awake); `ecf answer <id> [text]` answers from the CLI.
- On payment or fraud items an answer needs step-up: from Slack it waits, and `ecf answer <id>` at
  the computer shows it and confirms it.
- A third question, or an answer expiring in round 2, moves the item to "Needs you: two rounds of
  questions didn't settle it". An unconfirmed answer expires after 14 days.

## Undo and Dismiss

- **Undo** (digests) removes labels and a flag ecf added. It never runs on fraud, weak-fraud,
  lookalike, quarantine or regulator items, and never removes `suspicious` or `regulatory`. It runs
  at the address's next check and tells you the result; a message that changed or left INBOX is
  reported, not forced (SPEC §10.1).
- An undone draft is deleted only when it's unchanged in Drafts; one you edited, sent or deleted is
  left alone ("draft not deleted").
- **Dismiss** closes an item with no payment, fraud or regulator signal (`resolved_manual`).

## The inbox and single items

```sh
ecf inbox [--address <a>] [--stale]     # everything waiting on you, stale first
ecf item show <id>                      # what ecf keeps about one email, incl. its excerpt
ecf item resolve <id>… --reason <text>  # close without acting (also --ids, --older-than N)
ecf item requeue <id>                   # a failed or stuck action again, or back to the model
```

Item IDs work from 8 characters. Resolving payment or fraud items needs step-up; a bulk resolve
shows the list first. Items open 30 days are marked stale. `item requeue` of a send runs only once
the send is known not to have gone out, needs a new approval and step-up, and on a `high` address
the 10-minute delay again (SPEC §8.4). The admin guide covers `failed_unknown` and stuck items.

## Pausing

```sh
ecf pause <address>|--all
ecf resume <address>|--all
```

Instant, no step-up, audited; also the Pause and Resume buttons. A paused address keeps its
pre-check and fraud and regulator flagging; approved actions, delayed sends and model checks wait
until you resume (SPEC §10.1, step 8a).

## Reviewing (shadow and assist)

Before an address goes `live`, your reviews are its evidence (SPEC §9.2, §9.3). Review posts list
each email with what the model said (labelled as model output) and what ecf would do.

- **Fix** opens a form for the five fields you correct: category, priority, fraud risk, payment,
  sender type. A Fix never removes a trigger that already fired.
- **Correct** appears on lines that need their own review: payment, fraud, regulator and
  not-fully-scanned items, and every item on a `high` address.
- **All others correct** marks the rest of that post.

`ecf stage status` shows progress ("N/target reviewed, A% accurate, K to go"). A `standard` address
needs 100 reviewed at 85% category accuracy, a `high` one 200 at 90%, and no fraud-guard misses.
Moving stages is an admin task (admin guide).

## Senders

The **Confirm category** button (on digests) only replies with the command; you confirm at the
computer, with step-up, because a confirmed sender counts as known for the bank-detail trigger
(SPEC §9.6):

```sh
ecf sender show <sender> [--address <a>]
ecf sender confirm <sender> --category <c> [--address <a>]
```

`sender set-reply-to` and `sender set-verified` are in the admin guide.

## Drafts, replies and forwards

ecf can propose three outbound actions (ADR 0007, SPEC §8.4):

- **Draft reply**: saved in your Drafts folder, never sent; you send it yourself. The card shows the
  full text under "Draft written by ecf's model (it can be wrong; it is saved, never sent)", with
  links shown but not clickable.
- **Template reply** and **internal forward**: real sends. They need outbound on for the address,
  your approval and step-up each time. A forward attaches the original email unchanged and goes only
  to an entry on your forward allow-list.

While outbound is off, a proposed send is held back as suppressed and the item is flagged.
Reminders (`Operator Input Needed: outbound off (<address>)`) come 7 days after an address first
goes live, then weekly, 5 in all, then a monthly line in the summary channel.

```sh
ecf outbound report <address>            # what it would have sent, your reviews, sends, reminders
ecf outbound snooze <address> [--days N] # 1-90 days, default 7
ecf outbound dismiss <address>           # no more reminders
ecf outbound disable <address>           # off at once; approved and delayed sends are stopped
```

Turning outbound on (`ecf outbound enable`) is in the admin guide.

**Send limit**: by default 25 sends an hour and 250 a day per address. Reaching it holds every send
of the address (nothing is lost) and raises `Operator Input Needed: send limit reached
(<address>)` until you check what happened and run `ecf outbound resume <address>` (step-up). It
doesn't turn outbound back on if you turned it off.

## Claude review (presets B and C)

Addresses on presets B and C wait for you to run Claude:

```sh
ecf claude          # opens Claude Code with ecf's own config and login
/ecf-review         # typed inside that session
```

The session works through the queue; its proposals arrive in Slack like any others, and Claude
never approves. When items have waited more than 24 hours (`claude_review_reminder_hours`) ecf
raises `Operator Input Needed: Claude review waiting (<address>)` (SPEC §10.3, §13.3).

## Notifications and alert email

- **Desktop notifications** carry no email text (address IDs, counts, commands). On macOS they show
  as Script Editor (unverified); Linux desktops use `notify-send` (unverified until V1.6); headless
  Linux has none.
- **Alert email** (if set up; admin guide) carries the same text, never a subject, sender or
  excerpt. At most 10 an hour of one kind and 30 in all, then an hourly summary. `ecf alerts show`
  lists where each class goes; `ecf alerts test` sends a test on every route.

## Checking on ecf

```sh
ecf status                           # service, each address, backlog and estimate, outbound
ecf logs [--address <a>] [--since 2h] [--event check.] [-f]   # audit log, never message content
ecf stats [--since 7d] [--address <a>] [--preset A|B|C]       # model tokens, speed, plan usage
ecf doctor                           # everything ecf depends on, with fixes
```

## After time away

ecf works only while the computer is on and awake (README, "When you're away"). When you're back:

1. Wake the computer on AC power; ecf catches up on its own (`ecf status` shows the backlog).
2. Read "Needs you" or `ecf inbox`; escalations first.
3. Approvals of sends older than 4 days have expired; approve again if still wanted.
4. Run `ecf approve --pending` for anything queued for step-up.
5. Presets B and C: `ecf claude`, then `/ecf-review`.
