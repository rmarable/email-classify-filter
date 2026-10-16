# M7 Google Workspace accounts

Status: outline, not started. Split from M6 (Gmail API) by operator decision 2026-10-16 (OD-525,
amends OD-426). At M7's start this becomes `docs/roadmap/m7-google-workspace.md` and gets its own
design review.

## Why separate

Workspace accounts bring work that personal accounts don't: an organization's admin, domain-wide
controls, different sending limits and a likely security review. Keeping them out of M6 lets the
Gmail API ship for personal accounts first (operator decision 2026-10-16).

## Scope

Google Workspace (company and school) accounts. Google says app passwords aren't available for
them (support.google.com/accounts/answer/185833, fetched 2026-10-04), so V1.6's IMAP route can't
reach them.

- Reuses M6's Gmail API adapter, API sender and OAuth desktop sign-in (operator decision
  2026-10-16). Not IMAP with XOAUTH2: that would be a second OAuth path to maintain.
- Adds what Workspace needs: admin controls (whether the organization's admin must allow ecf's
  client, and how), Workspace sending limits, and anything the Workspace security review requires.
- The sender model: a Workspace address is at the organization's own domain, so `org_domains`
  applies as for any non-public domain (SPEC §7.2, OD-441); check this holds with the API adapter.

## Dependencies and order

Depends on M6. Comes after M6.

## Google facts recorded at planning

Re-check at M7's start.

- Workspace turned off less secure apps on 2025-03-14, while its transition page still suggests
  app passwords for devices (knowledge.workspace.google.com), which conflicts with the accounts page
  above (SPEC §18).
- Workspace sending limits: 2,000 messages a day on paid accounts and 500 on trials, applied over a
  rolling 24 hours, blocking for up to 24 hours (knowledge.workspace.google.com, Gmail sending
  limits in Google Workspace, fetched 2026-10-05).
- The scope classes, the security assessment, the 100-user cap and refresh-token expiry in
  `M6-GMAIL-API.md` apply here too.

## To settle at M7's start

1. Workspace admin controls: what an admin has to allow, and whether ecf can work where the admin
   hasn't.
2. Whether the security assessment applies (it may differ from M6's answer for personal accounts).
3. The per-address `max_sends_per_day` default for a Workspace address (personal Gmail starts at
   100, SPEC §18).
4. User-facing docs: a Workspace setup guide, or a Workspace section in `docs/gmail-setup.md`.
   When M7 is built, README is updated with its capabilities (operator request 2026-10-16),
   in the user-facing docs style (CLAUDE.md).

## Tag

`ms-m7-google-workspace` (OD-525).
