# Using ecf day to day

This guide is for whoever looks after the mailbox: reading ecf's alerts, approving what it
suggests, and keeping the queue moving. Setting ecf up, changing its configuration and fixing
problems are in the [admin guide](admin-guide.md). Right now one person does both jobs.

**Before you rely on ecf, read the [disclaimer](../DISCLAIMER.md).** ecf makes fraud harder to miss
but can't catch everything, comes with no warranty, and leaves you responsible for what you approve
and for the mailboxes you connect.

Every command has `--help`. Commands that need step-up (see below) say so there.

## A few words you'll see

- **Item:** one email that ecf is tracking.
- **Card:** a Slack message about one item, with buttons.
- **Draft:** a reply ecf's model writes and, once you approve it, saves in your Drafts folder. ecf
  never sends it; you read it, edit it if you like, and send it yourself.
- **Step-up:** a second check at the computer itself (Touch ID or your Mac password) before ecf
  does something risky. A Slack click alone isn't enough for those.
- **Stage:** how much ecf may do on its own for a mailbox. Every mailbox starts in `shadow`
  (watch only), can move to `assist` (labels and flags only) and then to `live` (handles routine
  mail itself). Your reviews are what earn a mailbox the next stage.
- **Sensitivity:** `standard`, or `high` for finance mailboxes. `high` means stricter checks and a
  10-minute pause before any email goes out.
- **The actor:** the part of ecf (Claude) that suggests what to do with an email after it's been
  classified.

## Slack is where you work

ecf creates private Slack channels and invites you:

- **One channel per mailbox** (`ecf-<install>-<mailbox>`): cards for emails that need you,
  digests and review posts.
- **One summary channel** (`ecf-<install>-summary`): the pinned "Needs you" list, the daily
  summary, alerts and notices.

Only clicks from your own Slack account count. Buttons only work while the computer running ecf is
awake; "Needs you" shows when it last connected.

### "Needs you"

A pinned message listing what's waiting on you: the top 20 items, oldest first, each saying what
it's waiting for. It also shows paused mailboxes, and has Pause all and Resume all buttons. It
always matches `ecf inbox`.

### Item cards

A card shows the sender, the subject, why ecf flagged the email, what it found when checking the
sender, and any warning signs: a first-time sender, a reply address that goes somewhere else, an
email not actually addressed to this mailbox, an email too big to scan fully, or bulk mail. It says
what ecf did and how it classified the email (for example "Classified: invoice, high priority, by
Claude Opus 5.5", or "corrected by you").

Cards never include the email's text. **Show excerpt** shows you, and only you, the first 200
characters. Cards about payments or fraud say "This acts on the email only. ecf never pays
anything."

### Escalations

Possible fraud and regulator mail get their own card and mention you, so your phone and desktop
notify you. These are never bundled together. If a lot of other escalations arrive at once, the
extras beyond the hourly limit (`escalations_per_hour`) are grouped into one "N more escalations"
thread per hour.

### Digests

At most once an hour per mailbox, during business hours, and only when new mail came in. A digest
lists the milder warning signs (weak fraud signals, payment emails from senders ecf hasn't
verified) and what ecf did about each, with Undo and Pause buttons. After a gap of more than 2
hours it opens with "Caught up: N messages since <time>". `ecf digest <address>` posts one
straight away.

**Approve all N reversible** approves exactly the list shown. It never includes sends, drafts,
fraud, regulator, unverified-sender or not-fully-scanned items, or anything on a `high` mailbox.
You approve each draft on its own card, which shows the whole draft, or with `ecf approve <id>`
at your computer. If a draft is ever too long for Slack, its card says so and you approve it at
your computer. A digest also lists drafts that are still waiting for you to read when a busy
spell held their cards back.

### Review posts

Once per business hour per mailbox, up to 20 emails with what Claude made of each and what ecf
would do. See [Reviewing](#reviewing-how-a-mailbox-earns-trust).

### The daily summary

Posted at the start of business hours: open, stale and expired items; emails that couldn't be
fully scanned; sends held back; the last backup; the backlog; newer models or ecf releases; and
anyone in ecf's channels other than you and ecf itself.

### Alerts

Alerts start with `[ecf-alert]`, and `[ecf-alert] Resolved:` when the problem clears. The ones
you'll see most:

| Alert | What to do |
|---|---|
| `Operator Input Needed: <what> (<address>)` | It says what's needed: approvals waiting at the computer, items going stale, sending is off, the send limit was reached, Claude review is overdue, a send goes out in 10 minutes, a possible second copy of ecf, or a Gmail download limit (new mail just waits; nothing to do). |
| `Possible Fraud Attempt`, `Regulatory Mail Notice` | Open the card. Check by phone or another channel you trust before acting on the email. |
| `Mail Provider Unreachable`, `Mailbox Login Rejected` | See [Recovery](admin-guide.md#recovery) in the admin guide. |
| `Slack Delivery Failed: a post wasn't sent` | The notice says what didn't reach Slack. If it names an email, decide it at your computer with the command it gives (`ecf approve <id>`, `ecf reject <id>`, `ecf answer <id>`, or `ecf cancel <id>` to stop a send that's counting down); `ecf slack repost` puts approval cards back in Slack. If it names a digest, `ecf digest <address>` posts a new one. Otherwise there's nothing to do unless it repeats. |
| `System Error`, other `Slack Delivery Failed` alerts | Run `ecf doctor`, then see the admin guide. |
| `Security Notice` | A security setting changed. If it wasn't you, see the admin guide. |

### "ecf hasn't checked in"

If the computer sleeps, shuts down or ecf stops unexpectedly, Slack posts "ecf hasn't checked in
since <time> (<computer>)". By default this happens only during business hours (the
`deadman_offhours` setting changes that).

## Approving and rejecting

Buttons say exactly what they do ("Approve: archive email"). Whoever decides first wins.
Approvals expire after 4 days for sends and 14 days for everything else; an expired approval does
nothing.

- **Reversible actions** (mark as read, archive, move, mark as junk, save a draft) take one click
  in Slack (a draft on its own card, as above).
- **Risky actions need you at the computer:** every send, anything that can't be undone, and
  hiding a fraud or regulator email. When you click one in Slack, the card says "Queued for your
  computer (N waiting)" and keeps only Reject, and your desktop shows a notification with the
  command to run:

  ```sh
  ecf approve --pending     # list what's waiting and approve it (each send or draft separately)
  ecf approve <id>          # one item: shows exactly what it does (a draft in full), then asks
  ecf approve --scan <id>   # one backfill or reprocess run's waiting items (sends, drafts apart)
  ecf reject <id>
  ```

  The Touch ID or password dialog names the action, sender, subject and mailbox, plus a
  4-character code. The command prints the same code. Check they match before you approve.
- **Sends on a `high` mailbox** then wait 10 minutes (of the computer being awake). Slack
  announces it with a **Cancel** button, and so does alert email if you use it. `ecf cancel <id>`
  does the same.

## Answering ecf's questions

When Claude isn't sure about an email, ecf asks you on a card headed "ecf has a question (round
N of 2)". The question comes from the AI, so it can be wrong.

- **Answer** opens a form (the computer must be awake). `ecf answer <id> [text]` answers from the
  terminal.
- On payment or fraud emails, an answer also needs step-up: a Slack answer waits until you confirm
  it with `ecf answer <id>` at the computer.
- If two rounds of questions don't settle it, the item moves to "Needs you" as "two rounds of
  questions didn't settle it". An answer you never confirm expires after 14 days.

## Undo and Dismiss

- **Undo** (on digests) removes the labels and flag ecf added. It never works on fraud, lookalike,
  regulator or quarantined emails, and never removes the `suspicious` or `regulatory` labels. It
  runs at the mailbox's next check and tells you how it went. If the email changed or was moved
  out of the inbox in the meantime, ecf reports that rather than forcing it. Each button names
  the sender's address and the subject, for example "Undo: billing@vendor-a.example, Invoice
  4471"; when the address is too long for a button, it shows the email's short ID instead. The
  digest lists the same emails, with their IDs, under "You can undo" near its top.
- An undone draft is deleted only if it's still unchanged in Drafts. If you edited, sent or deleted
  it, ecf leaves it alone and says "draft not deleted".
- **Dismiss** closes an item that has no payment, fraud or regulator warning.
- `ecf item undo <id>` is Undo from the terminal, for any item a digest would offer it on.

## Mail pretending to be someone you work with

If your admin has listed the people you work with (`org_addresses`), ecf checks every email from
anyone else for signs it's pretending to be one of them. The card, or `ecf item show`, says what
it noticed:

| ecf says | Meaning |
|---|---|
| `display name matches Pat Lee, one of your org addresses, but the sender is x@gmail.com` | The sender is using a colleague's name. |
| `display name shows pat.lee@gmail.com, an internal address, but the sender is x@gmail.com` | The name shown is a colleague's address, but the email came from somewhere else. |
| `patlee1@gmail.com looks like patlee@gmail.com, one of your org addresses` | A one-letter typo of a colleague's Gmail address, or their name at a different provider. |

If the email is about money, it's labelled `suspicious`, flagged and escalated with its own card.
Otherwise it's labelled `suspicious`, flagged, and listed in the digest.

**What to do:** don't reply, pay, click links or change anything because of the email. Contact the
person a way you already trust, by phone or by writing to their known address, and ask. If it
really was them on a new account, your admin can add that address to the list. Someone outside
who just happens to share a colleague's name gets flagged too; close those with
`ecf item resolve <id> --reason <text>` (step-up for payment or fraud items).

A separate check covers mail that claims to come from a listed address itself. If it fails ecf's
authenticity check, it's treated as a possible fraud attempt, including a forged copy of your own
Gmail address. Notes you send to yourself on Gmail are the exception.

## Finding and closing items

```sh
ecf inbox [--address <a>] [--stale]     # everything waiting on you, oldest first
ecf item show <id> [--full]             # everything ecf knows about one email, with the start
                                        # of its excerpt (--full: all of it)
ecf item resolve <id>… --reason <text>  # close without acting (also --ids, --older-than N)
ecf item requeue <id>                   # retry a failed or stuck action, or send back to Claude
```

You only need the first 8 characters of an item ID. Closing a payment or fraud item needs
step-up, and closing several at once shows you the list first. Items open for 30 days are marked
stale.

Retrying a send (`item requeue`) only happens once ecf is sure the first attempt didn't go out. It
needs a fresh approval and step-up, and on a `high` mailbox the 10-minute pause again. The admin
guide covers items stuck in `failed_unknown`.

## Pausing

```sh
ecf pause <address>|--all
ecf resume <address>|--all
```

Pausing is instant and needs no step-up; the Pause and Resume buttons do the same. A paused
mailbox still gets its fraud and regulator checks. Approved actions, delayed sends and Claude's
work wait until you resume.

## Reviewing: how a mailbox earns trust

Before a mailbox can go `live`, ecf needs proof it gets that mailbox right, and your reviews are
that proof. Review posts list each email, what Claude said about it (marked as AI output) and what
ecf would do.

- **Fix** opens a form where you correct whatever is wrong. You can change the category,
  priority, sender type, fraud risk, whether it's about a payment, the five safety questions
  (bank-detail change, claims to be from a regulator, phishing, claims to be a colleague, from a
  private individual), the suggested action, the reply and any deadline. A Fix records only what
  you change, and never removes a warning ecf's fixed rules already raised.
- **Correct** appears on emails that need checking one by one: payment, fraud, regulator,
  bank-detail, phishing, claimed-colleague and not-fully-scanned emails, and every email on a
  `high` mailbox.
- **All others correct** marks the rest of the post as right.

`ecf stage status` shows progress ("N/target reviewed, A% accurate, K to go"). A `standard`
mailbox needs 100 reviews with at least 85% correct; a `high` one needs 200 with at least 90%.
Both need zero missed fraud. A review counts as correct when your Fix didn't change anything.
Moving a mailbox to the next stage is an admin task.

## Senders

The **Confirm category** button on digests only shows you the command, because confirming a
sender matters: a confirmed sender counts as known when ecf checks for bank-detail changes. You
confirm at the computer, with step-up:

```sh
ecf sender show <sender> [--address <a>]
ecf sender confirm <sender> --category <c> [--address <a>]
```

`sender set-reply-to` and `sender set-verified` are in the admin guide.

## Drafts, replies and forwards

ecf can suggest three kinds of outgoing email:

- **Draft reply:** saved in your Drafts folder and never sent; you send it yourself if you want
  to. The card shows the whole text under "Draft written by ecf's model (it can be wrong; it is
  saved, never sent)". Links in it are shown but not clickable.
- **Template reply** and **internal forward:** these really send. Each one needs sending turned on
  for the mailbox, your approval and step-up, every time. A forward attaches the original email
  unchanged and can only go to an address on your forward allow-list.

While sending is off, ecf holds suggested sends back and flags the email. It reminds you that
sending is off 7 days after a mailbox goes live, then weekly for five reminders, then once a month
in the summary channel.

```sh
ecf outbound report <address>            # what it would have sent, your reviews, sends, reminders
ecf outbound snooze <address> [--days N] # quiet the reminders for 1-90 days (default 7)
ecf outbound dismiss <address>           # no more reminders
ecf outbound disable <address>           # turn sending off now; approved and delayed sends stop
```

Turning sending on is in the admin guide.

**Send limit:** by default 25 emails an hour and 250 a day per mailbox. If a mailbox hits the
limit, all its sends are held (nothing is lost) and ecf alerts you. Once you've checked what
happened, `ecf outbound resume <address>` (step-up) lets them go. This never turns sending back on
if you'd turned it off.

## Running Claude

Claude only reads your everyday mail when you ask it to. Until then, ecf's fraud and regulator
checks still run on every email as it arrives, but nothing else is classified. Plan on a review
at least once a working day:

```sh
ecf claude          # opens Claude Code with ecf's own settings and login
/ecf-review         # type this inside that session
```

Claude works through the queue and its suggestions arrive in Slack like any others. Claude never
approves anything. If emails have waited more than 24 hours (`claude_review_reminder_hours`), ecf
alerts you, and after 48 hours it sends a louder alert that mentions you.

## Notifications and alert email

- **Desktop notifications** never include email text, just mailbox names, counts and commands.
  On macOS they may appear as coming from Script Editor (not yet confirmed).
- **Alert email**, if your admin set it up, carries the same text and never a subject, sender or
  excerpt. At most 10 an hour of one kind and 30 in total, then one hourly summary.
  `ecf alerts show` lists where each kind of alert goes; `ecf alerts test` sends a test everywhere.

## Checking on ecf

```sh
ecf status                           # the service, each mailbox, the backlog, sending
ecf logs [--address <a>] [--since 2h] [--until 1h] [--event check.] [-f]   # the audit log (never email content)
ecf stats [--since 7d] [--address <a>]   # Claude usage, speed, Slack timings
ecf stats --stages [--since 7d]      # how long mail spends at each step
ecf doctor                           # checks everything ecf depends on, with fixes
```

## Coming back after time away

ecf only works while the computer is on and awake. When you're back:

1. Wake the computer, plugged in if it's a laptop. ecf catches up on its own (`ecf status` shows
   how much is left).
2. Read "Needs you" or run `ecf inbox`. Escalations first.
3. Approvals for sends older than 4 days have expired. Approve them again if you still want them.
4. Run `ecf approve --pending` for anything waiting for step-up.
5. Run `ecf claude`, then `/ecf-review`.
