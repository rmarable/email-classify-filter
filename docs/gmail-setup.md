# Getting a Gmail account ready for ecf

ecf can watch a personal Gmail account (an address ending in `@gmail.com` or `@googlemail.com`).
Before you add it to ecf, a few things need setting up on Google's side. This guide walks through
them. It takes about five minutes.

Once you're done, add the account to ecf as the [admin guide](admin-guide.md#gmail) describes.

## What you need

- **A personal Google account.** Work and school accounts (Google Workspace) won't work yet.
  Google doesn't let them create the kind of password ecf needs.
- **2-Step Verification** turned on for the account (step 1).
- **An app password** for ecf (step 2).

## What's an app password?

ecf needs to log in to your mailbox to read and sort your mail, and later, if you turn it on, to
send email for you. You don't give ecf your real Google password for that. Instead, Google lets
you create an **app password**: a separate 16-character code that only works for reading and
sending mail, and that you can cancel at any time without changing your real password.

Treat it like your real password: anyone who has it can read and send your email. ecf stores it
in your Mac's Keychain and nowhere else.

Google says app passwords aren't recommended in most cases, because most apps can sign in to
Google directly. ecf can't do that yet (support is planned), so for now it needs one.

**Some accounts can't create app passwords:**

- work, school or other organization accounts;
- accounts using Google's Advanced Protection;
- accounts where 2-Step Verification only uses security keys.

**Changing your Google password cancels every app password.** If you do, ecf stops being able to
log in until you give it a new one. See [After a password change](#after-a-password-change).

## Step 1: turn on 2-Step Verification

Go to [myaccount.google.com/signinoptions/two-step-verification](https://myaccount.google.com/signinoptions/two-step-verification)
and follow the steps. (Or, in your Google Account, open **Security & sign-in** and choose **Turn
on 2-Step Verification**.)

## Step 2: create an app password

1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) and sign
   in.
2. Follow the steps. When it asks for a name, pick one you'll recognise later, such as
   `ecf on my MacBook`.
3. Google shows you the 16-character code once. Type it into `ecf address add` when it asks (what
   you type stays hidden). Don't save it in a file, a note or a chat message.

Make a separate app password for each computer that runs ecf, so you can cancel one without
affecting the others.

## Step 3: check two Gmail settings

In Gmail on the web, click the gear icon, then **See all settings**.

- **Forwarding and POP/IMAP → Folder size limits:** make sure it says **Do not limit the number
  of messages in an IMAP folder**. With a limit, ecf only sees part of your inbox and would treat
  the rest as already dealt with. `ecf doctor` warns you if this looks wrong.
- **Labels → All Mail:** make sure **Show in IMAP** is ticked. ecf archives an email by moving it
  to All Mail, so without this, archiving fails and `ecf doctor` warns you.

You don't need to turn IMAP on; Gmail always has it on.

## Watching someone else's account

You can watch another person's Gmail account only if **they** create the app password and agree
to what ecf does with their mail:

- the subject, sender and ecf's verdict on each email go to your Slack;
- the content of their emails goes to Anthropic (Claude's maker) whenever you run `/ecf-review`.

Ask them to read this guide first. ecf has no way of checking that they agreed, so that's up to
you. If they later withdraw their agreement, cancel the app password (below).

One more rule: ecf's real-mail test collection (`ecf corpus fetch`) may only be built from a
mailbox you own, never from someone else's account you watch.

## What ecf does and doesn't do in Gmail

- It only reads your **inbox**. It never looks at what Gmail has put in Spam.
- It never deletes anything, and never moves anything out of All Mail or Trash.
- Its labels are invisible tags. You won't see them in Gmail's label list.
- It sends at most 100 emails a day from a Gmail account, to stay well under Google's own limit
  of 500 a day. (Google says that if you go over, sending usually works again within 1 to 24
  hours.)
- Google limits how much mail can be downloaded in a day, so ecf stops before it reaches 2,500 MB
  in 24 hours and tells you. New mail just waits; nothing is skipped.

## Revoking ecf's access

Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), find the
app password you made for ecf, and click **Remove**. ecf can't get into the account after that.
Changing your Google password does the same for every app password at once.

Cancel it when:

- you stop using ecf on that computer. Removing the mailbox from ecf deletes ecf's copy of the
  password, but not Google's.
- the computer is **lost or stolen**. Do it straight away from another device, then follow the
  stolen-laptop steps in the [admin guide](admin-guide.md#recovery).
- you're watching someone else's account and they no longer agree.

## After a password change

Your old app password stopped working when you changed your Google password. After three failed
logins, ecf sends the alert `[ecf-alert] Mailbox Login Rejected` and tries again every hour.

To fix it, create a new app password (step 2), then run this on the computer running ecf:

```sh
ecf address set <address> --app-password
```

ecf starts checking again, and Slack shows a "Resolved" notice.

---

What this guide says about Google comes from Google's help pages, checked on 2026-10-05:
[app passwords](https://support.google.com/accounts/answer/185833),
[2-Step Verification](https://support.google.com/accounts/answer/185839),
[IMAP settings](https://support.google.com/mail/answer/7126229),
[sending limits](https://support.google.com/mail/answer/22839) and
[Show in IMAP](https://support.google.com/a/users/answer/11339703). That last page is written for
Workspace accounts; on ecf's own personal test account, All Mail was shown over IMAP as expected.
Two details aren't confirmed by Google's pages: whether mail sent by ecf counts toward the 500 a
day the same way as mail sent in Gmail, and Google's exact download limit for personal accounts.
