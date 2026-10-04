# ADR 0011: ecf checks DKIM and DMARC itself

- **Status:** accepted (OD-017, OD-046, OD-051, 2026-09-26; OD-047 to OD-050, 2026-09-27;
  OD-187, 2026-09-28; OD-192, 2026-09-29); implemented in V1.1
- **Context source:** SPEC §7.2, §7.3, §12.1, §12.2, §18, §23.5 (G1-32); design plan
  (`docs/history/design-plan-2026-09-27.md`) §5 and "Decision summary";
  `ecf_server/senderauth.py`

## Context

The fraud rules turn on who really sent an email: a payment request "from" a known vendor or an
internal colleague is only safe to treat that way when the sender is authenticated. Providers
stamp an `Authentication-Results` header, but no hosted provider documents stripping a forged
one that arrives from outside (G1-32), and Microsoft's header carries no authserv-id. An attacker
can write any header they like.

## Decision

- **ecf's own DKIM/DMARC check is the only source of `auth_result`** (OD-046): at fetch the
  service verifies every DKIM signature on the raw message with `dkimpy` and `dnspython`, finds the
  From domain's DMARC policy per RFC 9989 with its DNS tree walk (no Public Suffix List), and
  applies `p=`/`sp=`/`np=`/`t=` and alignment itself (§7.3).
- **Provider Authentication-Results are never used** (OD-017, OD-051):
  `trust_provider_authentication_results` is fixed `false` everywhere, including dev.
- **Strict about what counts** (OD-047, OD-187): a signature with `l=` is unverified; it must
  cover From, Subject, Date, To and any Reply-To; unsigned MIME headers lower `auth_result` to
  `none` for payment and fraud rules only; more than one From header is `fail` and a fraud trigger.
- **`fail` is narrow** (OD-192): more than one From, or every aligned signature broken under an
  enforcing policy. A missing signature is `none`, since the sender may pass by SPF, which ecf
  can't check after delivery.
- **DNS** is ordinary DNS, cached in SQLite with TTLs capped at the check interval; DoH is opt-in
  (OD-050). A DNS failure or an exhausted per-check budget gives `none`, never `pass` (§7.3).

## Alternatives considered

- **Trusting provider Authentication-Results:** rejected (OD-051); no provider documents
  stripping forged ones, so a header from outside could claim `pass`.
- **A stamp/strip probe** to prove a provider strips forged headers: dropped in the design plan;
  it needs a forged message arriving from outside. Revisit if a provider documents stripping.
- **The Public Suffix List** for organizational domains: not needed; RFC 9989's tree walk replaces
  it (`publicsuffixlist` is not used, §17.5).
- **SPF:** not possible after delivery (a design judgement, §7.3); ARC for forwarded and list mail
  is a "Later" item.

## Consequences

- SPF-only senders, forwarded and list mail that breaks DKIM, and mail verified days late (computer
  off, keys rotated) end at `none`; on payment mail `none` lands in the unverified-sender digest
  (rule 1a), never as verified (§7.3, OD-065).
- On a hostile network DNS can be forged; SPEC owns that stated limit and SECURITY.md summarizes
  it (OD-049, §12.2).
- `dkimpy`'s last release was 2024-07-04 (checked 2026-09-27): a maintenance risk to watch.
- `doctor` checks DNS reachability, and a System Error fires when 0 of ≥ 20 signed messages pass
  in a day (§7.3).
