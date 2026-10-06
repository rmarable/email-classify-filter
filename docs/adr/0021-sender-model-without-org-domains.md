# ADR 0021: sender model without org domains

- **Status:** accepted (V1.6 step 1, 2026-10-05; OD-430 to OD-436, OD-441, OD-446 to OD-452);
  built in V1.6 step 1b
- **Context source:** SPEC §7.2, §8.5, §8.6, §9.7, §12.1, §12.2

## Context

Through V1.5, "internal" meant a DMARC pass from a domain in `org_domains`, and public mailbox
domains were refused there because anyone can get an address at one. V1.6 adds personal Google
accounts (OD-425): an install may watch only gmail.com addresses, so it has no org domain, and
the people the user works with may use gmail.com too. Impersonating a colleague or a boss from a
new gmail.com address is a common fraud, and trigger 7's "org address or staff name" clause was
never built.

## Decision

- The internal set is `org_domains` (by domain) plus a new config section, `org_addresses`
  (exact addresses, each with an optional name), install-wide. A monitored address is internal
  only when listed. A public domain is never internal by domain.
- An exact gmail.com address with an aligned DKIM pass counts as that account: in the V1.6 step 0
  test, Gmail rewrote a `From:` naming another gmail.com address to the authenticated account.
  Addresses at other providers may be listed, with the stated limit that this is unverified there.
- Gmail address folding (dots, `+tag`, googlemail.com) identifies the same account for trigger 6
  and impersonation, never for `sender_origin`.
- Impersonation is a fact, `impersonates_internal`: a listed name in the display name, an
  internal address in the display name, a one-edit Gmail typo, or the same local part at another
  provider. It isn't a fraud trigger. Rule 1 escalates it when the mail is about money, and
  rule 1b flags it otherwise. It counts as a fraud signal for routing, hides and sends.
- Trigger 6 extends to listed addresses. Gmail delivers an account's mail to itself unsigned, so
  mail from the watched address that Gmail labels `\Sent` (`self_sent`) is exempt from trigger 6
  and rule 1a. A forged copy of your own address has no such label.
- Names come only from `org_addresses`; nothing is learned.

## Alternatives considered

- **Monitored addresses internal automatically:** rejected. A compromised watched account,
  perhaps someone else's (OD-427), would become internal install-wide, and `address add` needs no
  step-up.
- **Allow gmail.com in `org_domains`:** rejected; every Gmail user would be internal.
- **Fold Gmail addresses for `sender_origin`:** rejected; an exact match keeps "internal" narrow,
  and folded variants still fire trigger 6.
- **Impersonation as its own fraud trigger:** rejected; it would escalate every same-name email,
  including harmless ones. Rules 1 and 1b already split by money (OD-262).
- **Learn names from history:** rejected for V1.6; it widens what a sender can influence.
- **Trust DMARC only for notes to self:** not possible on Gmail (unsigned); without `self_sent`,
  every note to self from a listed address would escalate.
- **Exempt mailing-list mail from trigger 6:** rejected; a forger can add `List-Id`.

## Consequences

- A Gmail-only install with no `org_addresses` has no internal set: impersonation isn't detected,
  and `ecf doctor` says so.
- Config gains a section (`config.py` sections and risk order), and exports a higher
  `DATA_FORMAT` (OD-442).
- Eval cards need profiles to test this (OD-443).
- An outside person who shares a listed name is flagged; there is no per-sender exception in
  V1.6.
- Fetch on Gmail reads `X-GM-LABELS` for each message.
- Whether mail sent from Gmail's web or app carries `\Sent` in INBOX is unverified until the V1.6
  closing run.
