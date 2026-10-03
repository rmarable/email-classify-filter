# ADR 0010: IMAP only, with app passwords the service holds

- **Status:** accepted (design plan "Decision summary", Mail, reviewed by the operator
  2026-09-26/27; OD-086, 2026-09-27; OD-191, 2026-09-28; OD-196, 2026-09-29; OD-324, 2026-10-02);
  implemented in V1.1 (IMAP, the probe), SMTP in V1.5
- **Context source:** SPEC §1.1, §3.2, §3.3, §5.1, §10.2, §11.6, §18, §23.5 (G1-32 to
  G1-37); design plan (`docs/history/design-plan-2026-09-27.md`) "Decision summary";
  `ecf_server/mail/imap.py`, `mail/smtp.py`, `probe.py`

## Context

The mailboxes ecf watches (`billing@`, `accounts-payable@`, `info@`) sit at many providers. ecf
needs the original message byte for byte (DKIM covers the whole body, ADR 0011), its own labels on
the mail (IMAP keywords), and moves, drafts and sends. The credential that grants this access
reads and sends all of a business's mail at that address, so where it lives matters as much as
which protocol uses it. v1 has no web server, so no OAuth redirect (ADR 0002).

## Decision

- **IMAP only in v1, logged in with per-address app passwords** (design plan "Decision summary";
  §1.1): Purelymail first, then any IMAP provider that offers app passwords. Sends use the same
  address's SMTP server (V1.5, OD-324). TLS uses `ssl.create_default_context()`, TLS 1.2 or
  later, no STARTTLS downgrade (§12.3).
- **Only the service holds the credentials** (§3.2): the broker inside `ecf_server` reads each
  app password from the OS secret store at connect. The CLI sees it only while you type it into a
  hidden prompt and passes it over the socket; the service logs in before storing it, so a wrong
  one leaves nothing behind (OD-191). Models never see it. `ecf address set --app-password`
  re-enters or rotates it (OD-086).
- **A probe per address** records what the server can do (capabilities, folder roles, keyword
  support, size limits) and warns about what's missing (§18). Runtime detection, not a provider
  table, decides behaviour; ecf caps its own size limit at a smaller provider limit (OD-196).
- **Microsoft 365 and Outlook.com are not supported in v1**: their IMAP requires OAuth2 and app
  passwords no longer work (documented, G1-33).

## Alternatives considered

From the design plan and SPEC §1.2:

- **Microsoft 365 via Graph:** on the roadmap (M3), not v1.
- **The Gmail API and Google Workspace XOAUTH2:** "Later" items.
- **Providers without IMAP** (Atomic Mail): not usable until they offer IMAP and app passwords or
  an equivalent (SPEC §1.2, checked 2026-09-28).

## Consequences

- Any provider with IMAP and app passwords works without code changes, within what the probe finds
  (keywords, `\Drafts`, `\Sent`, UIDPLUS).
- Microsoft 365 mailboxes can't be watched in v1.
- An app password grants full mailbox access at some providers (Purelymail: "full access to your
  email", G1-35), so the README recommends one per address with 2FA, rotated (§12.6).
- A rejected password raises `Mailbox Login Rejected` after 3 failures, then retries hourly to
  avoid a lockout (§13.3).
- Gmail app passwords need 2-Step Verification and may be unavailable on Workspace or with
  Advanced Protection (documented, G1-34).
