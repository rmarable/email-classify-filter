# M6 Gmail API (personal Google accounts)

Status: superseded 2026-10-16 by `docs/roadmap/m6-gmail-api.md` (OD-527 to OD-531); kept as
history. Was: outline, not started. Split from Google Workspace accounts (now M7, `M7-GOOGLE-WORKSPACE.md`)
by operator decision 2026-10-16 (OD-525, amends OD-426). Source: SPEC §1.2 "M6 Gmail API" as of
`c544d53`. At M6's start this becomes `docs/roadmap/m6-gmail-api.md` and gets its own design review.

## Scope

Personal Google accounts (gmail.com, googlemail.com) through the Gmail API, instead of IMAP with an
app password (V1.6, OD-425). Google Workspace accounts are out of scope (M7).

- A Gmail API adapter behind the mail port (`MailSource`, which is shaped around IMAP UIDs today).
- An API sender.
- The OAuth desktop flow: a loopback redirect, no web server.
- Tokens in the OS secret store.
- Narrower scopes by stage: no send scope while outbound is off.
- Native labels (instead of IMAP keywords).
- History-based sync.
- Polling, not Pub/Sub push (Pub/Sub would add a destination, SPEC §12.4).

M7 builds on this adapter and sign-in, so both are designed to take a Workspace account later
without a rewrite.

## Dependencies and order

No technical dependency. By the roadmap order (OD-004) it comes after M5.

## Google facts recorded at planning

Fetched 2026-10-04, from SPEC §1.2; re-check at M6's start.

- Every Gmail scope ecf needs (`gmail.readonly`, `gmail.modify`, `gmail.compose`,
  `gmail.metadata`, `https://mail.google.com/`) is restricted; `gmail.send` is sensitive;
  `gmail.labels` is non-sensitive.
- A security assessment applies "if you store restricted scope data on servers (or transmit)"
  (developers.google.com/gmail/api/auth/scopes).
- A project in "Testing" gets authorizations and refresh tokens that expire after 7 days; one
  "In production" and unverified shows an unverified-app screen and is capped at 100 new users over
  its lifetime (support.google.com/cloud/answer/15549945).
- Personal use under 100 users needs no verification (support.google.com/cloud/answer/13464323).
- Refresh tokens with Gmail scopes end when the password changes and after 6 months unused
  (developers.google.com/identity/protocols/oauth2).
- Personal Gmail sending limit: 500 emails a day, or 500 recipients in one email
  (support.google.com/mail/answer/22839, fetched 2026-10-05; SPEC §18).

## To settle at M6's start

1. Each operator's own Google Cloud project, or one shared, verified client.
2. Whether the security assessment applies to a local-only app.
3. The ADR superseding ADR 0010 (IMAP only).
4. Migration of an existing V1.6 Gmail address (app password, IMAP) to the API: in place, or
   remove and re-add (unverified which keeps item history; check at the start).
5. Whether an install may keep some Gmail addresses on IMAP and move others to the API.

## Tag

`ms-m6-gmail-api` (OD-426).
