# Setting up a Gmail account for ecf

What to do on Google's side before `ecf address add` watches a personal Gmail address
(`@gmail.com` or `@googlemail.com`). ecf's side is in the [admin guide](admin-guide.md#gmail).
`SPEC.md` is authoritative (§1.1, §12.2, §18). Facts about Google cite Google's own help pages,
with the date they were checked.

## What you need

- A **personal** Google account. Work, school and organization accounts (Google Workspace) can't
  have app passwords ([Google](https://support.google.com/accounts/answer/185833), checked
  2026-10-05); ecf reaches them only with the Gmail API, roadmap milestone M6 (SPEC §1.2).
- **2-Step Verification** turned on for that account.
- An **app password** made for ecf.

## App passwords

An app password is "a 16-digit passcode that gives a less secure app or device permission to
access your Google Account" ([Google](https://support.google.com/accounts/answer/185833), checked
2026-10-05). ecf uses it to read and act on the mailbox over IMAP and, once you turn sending on,
to send over SMTP (both tested, SPEC §18). Anyone who has it can read and send your mail, so
treat it like the account password.
Google adds that app passwords "aren't recommended and are unnecessary in most cases"
([Google](https://support.google.com/mail/answer/7126229), checked 2026-10-05); ecf needs one
because v1 uses IMAP, not the Gmail API.

**Accounts that can't have one** ([Google](https://support.google.com/accounts/answer/185833),
checked 2026-10-05):

- work, school or other organization accounts;
- accounts with Advanced Protection;
- accounts whose 2-Step Verification is set up only for security keys.

**Changing the Google password revokes them:** "we revoke your app passwords when you change your
Google Account password" ([Google](https://support.google.com/accounts/answer/185833), checked
2026-10-05). After a password change ecf's logins fail; see
[After a password change](#after-a-password-change).

## Step 1: turn on 2-Step Verification

Open [myaccount.google.com/signinoptions/two-step-verification](https://myaccount.google.com/signinoptions/two-step-verification),
or in your Google Account choose **Security & sign-in**, then under "How you sign in to Google"
**Turn on 2-Step Verification**, and follow the steps
([Google](https://support.google.com/accounts/answer/185839), checked 2026-10-05).

## Step 2: create an app password

1. Open [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) and sign in
   ([Google](https://support.google.com/accounts/answer/185833), checked 2026-10-05).
2. Follow the page's steps. If it asks for a name, use one you'll recognise later, such as
   `ecf on <computer>`.
3. Type the 16-digit passcode Google shows into `ecf address add` when it asks (the prompt is
   hidden). Don't save it in a file, a note or a chat; ecf keeps it in the macOS Keychain.

Make one app password per computer, so you can revoke one without the others.

## Step 3: check two Gmail settings

In Gmail on the web, open **Settings** (the gear), then **See all settings**.

- **Forwarding and POP/IMAP, Folder size limits:** leave it at "Do not limit the number of
  messages in an IMAP folder". The setting "limit[s] IMAP folders to contain no more than this
  many messages" ([Google](https://support.google.com/mail/answer/7126229), checked 2026-10-05).
  With a limit, ecf sees only part of your inbox and would treat the rest as handled elsewhere.
  `ecf doctor` warns when it finds fewer messages over IMAP than Gmail's own search does.
- **Labels, All Mail:** keep **Show in IMAP** ticked. ecf archives by moving mail to All Mail, so
  without it archive actions are refused and `ecf doctor` warns. Google describes the **Show in
  IMAP** box on a page written for Workspace users
  ([Google](https://support.google.com/a/users/answer/11339703), checked 2026-10-05); that personal
  accounts show the same box is unverified on Google's pages. All Mail was shown over IMAP on ecf's
  personal test account (SPEC §18, 2026-10-05).

IMAP itself needs no switch: "IMAP access is always turned on in Gmail"
([Google](https://support.google.com/mail/answer/7126229), checked 2026-10-05).

## Watching someone else's account

You may watch another person's Google account only when **its owner** creates the app password and
agrees to ecf processing their mail as SPEC §12.4 describes: subjects, senders and ecf's
classifications go to your Slack, and on presets B and C email content goes to Anthropic while you
run `/ecf-review` (SPEC §12.4, OD-427). Ask them to read this guide first. ecf can't check that
they agreed; that is your responsibility (SPEC §12.2).

A real-mail test corpus (`ecf corpus fetch`, not built yet) may be made only from a mailbox you own,
never from someone else's account you watch (SPEC §16.7, OD-466).

## What ecf does and doesn't do on Gmail

- It reads your **inbox only**. Mail Gmail puts in Spam is never checked (SPEC §12.2).
- It never deletes anything in All Mail or Trash, and never moves anything out of them (OD-438).
- Its labels are hidden IMAP keywords; they don't appear as Gmail labels (OD-445).
- It starts with a limit of 100 sends a day per Gmail address. Gmail's own limit is 500 emails a
  day; past it, "you should be able to send emails again within 1 to 24 hours"
  ([Google](https://support.google.com/mail/answer/22839), checked 2026-10-05). Whether mail sent
  over SMTP counts toward that limit the same way is unverified.
- It stops downloading for an address before it passes 2,500 MB in 24 hours; new mail waits and
  is read later, nothing is skipped, and ecf raises `Operator Input Needed: Gmail download limit
  reached`. Google's download limit for personal accounts is unverified (SPEC §14.3).

## Revoking ecf's access

Open [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), find the app
password you made for ecf and click **Remove**. "Once you revoke the App Password, the app can't
access your Google Account again"
([Google](https://support.google.com/accounts/answer/185833), checked 2026-10-05). Changing the
Google Account password also revokes every app password (same page).

Revoke it when:

- you stop using ecf on that computer (`ecf address remove` and `ecf destroy` delete ecf's copy,
  not Google's);
- the computer is **lost or stolen**: do it from another device straight away, then follow the
  stolen-laptop steps in the [admin guide](admin-guide.md#recovery);
- you no longer have the owner's agreement to watch their account.

## After a password change

Google has revoked the old app password, so after 3 rejected logins ecf raises
`[ecf-alert] Mailbox Login Rejected` and retries hourly. Create a new app password (step 2), then
on the computer running ecf:

```sh
ecf address set <address> --app-password
```

Checks resume and a `Resolved:` notice follows.
