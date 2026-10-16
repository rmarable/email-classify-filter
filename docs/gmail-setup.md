# Connecting Gmail to ecf

ecf can watch a personal Gmail account (an address ending in `@gmail.com` or `@googlemail.com`).
It connects through the Gmail API, Google's own way for programs to read and sort Gmail: you sign
in once in your browser and tell Google that ecf may manage your mail. This guide walks you
through setting that up.

**Before you rely on ecf, read the [disclaimer](../DISCLAIMER.md).** ecf makes fraud harder to miss
but can't catch everything, comes with no warranty, and leaves you responsible for what you approve
and for the mailboxes you connect.

ecf also supports Gmail over IMAP with an app password. If you'd rather use that, see
[Using an app password instead](#using-an-app-password-instead) at the end. One copy of ecf can
have addresses connected either way.

Work and school accounts (Google Workspace) don't work yet.

## What you need

- **A personal Google account**, signed in to in your browser.
- **ecf installed and set up** on your Mac (the [admin guide](admin-guide.md) covers that).

## Why there's a Google Cloud step

Google only lets a program sign in to Gmail if the program is registered with Google. ecf doesn't
register one copy for everyone; instead **you register your own**, for free, in Google Cloud
(Google's site for developers). You don't need to be a developer or set up billing. You do it
once, and it covers every Gmail address you connect to the same copy of ecf.

Two words you'll meet:

- **Project:** a container in Google Cloud for your registration. Yours holds only ecf.
- **OAuth client:** the registration itself. It gives you a **client ID** and a **client
  secret**, two codes that tell Google "this is my copy of ecf". They don't open your mailbox on
  their own; you still sign in.

## Step 1: create a project

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and sign in with the Google
   account you'll connect.
2. Open the menu, choose **IAM & Admin**, then **Create a Project**.
3. Give it a name you'll recognise, such as `ecf`, and click **Create**.

## Step 2: turn on the Gmail API

Go to the [Gmail API page](https://console.cloud.google.com/apis/library/gmail.googleapis.com),
check your new project is selected at the top, and click **Enable**.

## Step 3: set up the sign-in screen

This is the screen Google shows when you sign in, naming the app that asks for access.

1. Open the menu, then **Google Auth platform**, then **Branding**. If it says "Google Auth
   platform not configured yet", click **Get Started**.
2. **App name:** `ecf`. **User support email:** your own address. Click **Next**.
3. **Audience:** choose **External**. (Internal is only for company accounts.) Click **Next**.
4. **Contact information:** your own address. Click **Next**.
5. Read and agree to Google's user data policy, then click **Continue** and **Create**.
6. Open **Audience** and click **Publish app**, so the status reads **In production**.

The last step matters: while the app is in "Testing", Google ends your sign-in after 7 days and
ecf would lose access every week. Publishing it doesn't make it public or send it to Google for
review. It's still only yours.

## Step 4: create the OAuth client

1. In **Google Auth platform**, open **Clients** and click **Create Client**.
2. **Application type:** **Desktop app**. **Name:** something like `ecf on my MacBook`.
   Leave the other options as they are.
3. Click **Create**. Keep the page with the **client ID** and **client secret** open for the next
   step.

## Step 5: give ecf the client ID and secret

On the Mac running ecf:

```sh
ecf gmail client set
```

It asks for the client ID, then the client secret. What you type or paste stays hidden, and ecf
keeps them in your Mac's Keychain. You only do this once for each copy of ecf you run (most people
run one). Don't save the secret in a file, a note or a chat message.

## Step 6: add the address and sign in

```sh
ecf address add pat.lee@gmail.com --access api
```

ecf opens your browser at Google's sign-in page:

1. Choose the account you're adding. ecf refuses the sign-in if you pick a different one.
2. Google warns **"Google hasn't verified this app"**. That's expected: the app is the one you
   registered a few minutes ago, and Google only checks apps used by many people. Click
   **Advanced**, then the link to continue to `ecf`.
3. Google lists what ecf asks to be allowed to do with your Gmail. Read it, then click
   **Continue**.

The browser then says you can close it, and the Terminal carries on with the usual questions for a
new address.

**Why ecf asks to send even if you never turn sending on:** Google offers no permission that
allows labelling and archiving without also allowing sending. ecf itself sends nothing from the
address until you turn sending on for it, and every email it sends that way needs your approval.
The one exception is ecf's own alert emails, if you choose this address to send them from (the
admin guide's alert email settings).

## Moving an address that uses an app password

If ecf already watches the address with an app password, do steps 1 to 5, then:

```sh
ecf address set pat.lee@gmail.com --access api
```

You sign in as in step 6. ecf then forgets the app password and reads the inbox afresh. It keeps
what it has learned about the address: emails it was still working on carry over, and emails it
had finished with stay in its history. Then remove the old app password at
[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), since ecf can't do
that for you.

## Watching someone else's account

You can watch another person's Gmail account only if **they** sign in with Google themselves at
step 6, and agree to what ecf does with their mail:

- the subject, sender and ecf's verdict on each email go to your Slack;
- the content of their emails goes to Anthropic (Claude's maker) whenever you run `/ecf-review`.

Ask them to read this guide first. ecf has no way of checking that they agreed, so that's up to
you. If they later withdraw their agreement, they cancel ecf's access (below).

One more rule: ecf's real-mail test collection (`ecf corpus fetch`) may only be built from a
mailbox you own, never from someone else's account you watch. It works only with an app password.

## What ecf does and doesn't do in Gmail

- It only reads your **inbox**. It never looks at what Gmail has put in Spam.
- It never deletes your mail. Archiving takes an email out of your inbox and leaves it in All
  Mail. (Gmail's own Archive button archives the whole conversation; ecf archives just that
  email.) The only thing it deletes is a draft it wrote itself, when you undo it.
- **Its labels** are ordinary Gmail labels in a group called `ecf`, with one folder inside named
  after your copy of ecf, so you can see in Gmail how ecf sorted each email. Leave them alone: ecf
  uses them to recognise its own work.
- It sends at most 100 emails a day from a Gmail account, to stay well under Google's own limit
  of 500 a day. (Google says that if you go over, sending usually works again within 1 to 24
  hours.)

## Revoking ecf's access

Go to Google's [third-party connections page](https://myaccount.google.com/linkedapps), choose
`ecf`, then **See details**, **Remove access** and **Confirm**. ecf can't get into the account
after that. Removing the address from ecf (`ecf address remove`) also asks Google to cancel ecf's
access.

Do it when:

- you stop using ecf on that computer;
- the computer is **lost or stolen**. Do it straight away from another device, then follow the
  stolen-laptop steps in the [admin guide](admin-guide.md#recovery);
- you're watching someone else's account and they no longer agree.

If you don't need the Google Cloud project any more, you can delete it in Google Cloud too.

## After a password change

Changing your Google password ends ecf's sign-in. After three failed logins, ecf sends the alert
`[ecf-alert] Mailbox Login Rejected` and tries again every hour.

To fix it, run this on the computer running ecf and sign in again (step 6):

```sh
ecf address set <address> --access api
```

Nothing is lost; ecf carries on where it stopped, and Slack shows a "Resolved" notice.

Google also ends a sign-in that hasn't been used for six months. That won't happen while ecf
checks the address, but it can if you pause an address for that long; sign in again the same way.

## Using an app password instead

An app password is a separate 16-character password Google gives you for one program. ecf logs in
over IMAP with it.

1. Turn on 2-Step Verification at
   [myaccount.google.com/signinoptions/two-step-verification](https://myaccount.google.com/signinoptions/two-step-verification).
2. Create an app password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
   named something like `ecf on my MacBook`. Google shows it once.
3. In Gmail on the web, open the gear icon, then **See all settings**, and check two settings:
   - **Forwarding and POP/IMAP → Folder size limits:** **Do not limit the number of messages in
     an IMAP folder**. With a limit, ecf only sees part of your inbox.
   - **Labels → All Mail:** **Show in IMAP** ticked. ecf archives by moving mail to All Mail.
4. Add the address and type the app password when asked (it stays hidden):

   ```sh
   ecf address add pat.lee@gmail.com
   ```

With an app password, ecf's labels are invisible tags rather than Gmail labels, and Google limits
how much mail can be downloaded in a day: ecf stops before 2,500 MB in 24 hours and tells you.
To revoke it, remove it at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
A Google password change cancels it: create a new one, then run
`ecf address set <address> --app-password`. To move the address back from signing in with Google,
run `ecf address set <address> --access imap`.

---

What this guide says about Google comes from Google's help pages.

- Checked on 2026-10-16:
  [creating a project](https://developers.google.com/workspace/guides/create-project),
  [the Gmail API set-up steps](https://developers.google.com/workspace/gmail/api/quickstart/python),
  [publishing status and the 7-day limit](https://support.google.com/cloud/answer/15549945),
  [personal use](https://support.google.com/cloud/answer/13464323),
  [when a sign-in ends](https://developers.google.com/identity/protocols/oauth2) and
  [third-party connections](https://support.google.com/accounts/answer/13533235).
- Checked on 2026-10-05:
  [app passwords](https://support.google.com/accounts/answer/185833),
  [2-Step Verification](https://support.google.com/accounts/answer/185839),
  [IMAP settings](https://support.google.com/mail/answer/7126229),
  [sending limits](https://support.google.com/mail/answer/22839) and
  [Show in IMAP](https://support.google.com/a/users/answer/11339703).

Not yet confirmed by Google's pages:

- the exact wording of the unverified-app screen and its **Advanced** link, and of the list of
  permissions Google shows;
- whether mail sent by ecf counts toward the 500 a day the same way as mail sent in Gmail;
- Google's exact download limit for personal accounts.
