# ADR 0032: Gmail API for personal Gmail, chosen per address

- **Status:** proposed (operator decisions 2026-10-16, OD-527 to OD-531); accepted when M6 is
  tagged (`ms-m6-gmail-api`). Supersedes the "Later" alternative of ADR 0010 (the Gmail API);
  ADR 0010 stays in force for IMAP addresses.
- **Context source:** `docs/roadmap/m6-gmail-api.md` (draft 2, after Phase R P1-P29); SPEC §1.2, §3.2, §3.3, §12.4,
  §18; `planning-docs/M6-GMAIL-API.md` (outline, superseded)

## Context

V1.6 reaches personal Gmail over IMAP with an app password (OD-425). App passwords need 2-Step
Verification, are unavailable to some accounts, are cancelled by every password change, and
Google discourages them. ADR 0010 left the Gmail API as a "Later" item because v1 has no web
server for an OAuth redirect; Google's desktop-app flow uses a loopback redirect, which the local
service can serve for the length of one sign-in. Restricted Gmail scopes need OAuth verification
and, for apps that store or transmit the data on servers, a security assessment, unless the app
is for personal use (fewer than 100 users).

## Decision

- **Per address** (OD-529): `addresses.access` is `imap` or `gmail_api`. Personal Gmail
  addresses can use either; one install may mix them. An address moves between the two with
  `ecf address set --access`: open items are found again, closed items and sender history stay
  (OD-530); the same command with the current access signs in again.
- **Own Google Cloud project** (OD-528): each operator creates the project and a Desktop OAuth
  client; ecf ships no shared client. Personal use needs no verification.
- **The service holds the tokens**, as it holds app passwords: the loopback flow runs in the
  service, the refresh token goes to the OS secret store, access tokens stay in memory, the CLI
  never sees either.
- **One mail port**: the API adapter implements `MailSource` and maps Gmail message ids to local
  UIDs (assigned oldest first in a non-repeating epoch, state excluded from export), so the
  fetch, cursor and recovery code is shared with IMAP.
- **One "Gmail mailbox" predicate** (IMAP Gmail mode or the API) for Gmail's behaviour (archive,
  Sent copies, `self_sent`), so M7's Workspace addresses need no domain check.
- **Scope `gmail.modify`.** It also permits sending, so ecf's outbound gate, not the token,
  keeps an address from sending while outbound is off; no read-only scope for shadow.
- **Polling only**; Google's API and OAuth endpoints count as the mail provider (§12.4).

## Alternatives considered

- **A shared, verified ecf OAuth client:** simpler setup, but restricted-scope verification and
  likely a yearly security assessment (ecf sends mail content to Anthropic and Slack); not
  confirmed with Google either way. Rejected for M6 (OD-528).
- **IMAP with XOAUTH2 instead of the REST API:** keeps the IMAP code but adds a second OAuth path
  and keeps IMAP's download limits; the API gives history-based sync and native labels. Rejected
  (M7 outline, OD-525).
- **A transport-neutral rewrite of the mail port:** cleaner, but changes the fetch, cursor,
  restore and export code that every address depends on. Rejected for M6; revisit if a third
  transport arrives.
- **Pub/Sub push:** adds a destination (§12.4). Rejected.

## Consequences

- Each operator does a one-time Google Cloud setup (its length timed at step 0) and sees Google's
  unverified-app screen at sign-in.
- A Google password change still ends the sign-in (refresh tokens with Gmail scopes end then),
  as it ends app passwords; the alert and the fix (`ecf address set --access api`, which keeps the
  address's history) match.
- Google's per-minute API quota replaces IMAP's daily download budget for API addresses.
- M7 (Google Workspace) can build on the same adapter and sign-in.
