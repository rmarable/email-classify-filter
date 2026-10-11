---
id: ho-scam-known-vendor-iban-no-change-words
title: Known vendor's real mailbox sends an invoice with a new IBAN and no 'changed account' wording
threat: business email compromise (compromised vendor mailbox) / silent payee redirection
control: "fraud guard: payment details that differ from the vendor's usual ones, from an authenticated known sender, without change wording"
why: "Vendor A's genuine mailbox, apparently compromised, sends a normal-looking invoice. The pay-to block gives a foreign IBAN (published example DE89 3704 0044 0532 0130 00). The email never says the account has changed. auth_result pass, because the mail really comes from the vendor's signed domain. sender_seen_before true, because the org already deals with Vendor A. Held-out card; written without the plan, keyword lists or signals."
failure_looks_like: the item is treated as a routine invoice from a trusted vendor and goes to the normal invoice path without escalation
author: claude
from: "Vendor A Billing <billing@vendor-a.example>"
to: [ap@acme.example]
subject: "Invoice INV-7716 - Q4 maintenance"
date: 2026-10-07T09:12:00Z
attachments:
  - name: INV-7716.pdf
    generate: invoice_pdf
    vendor: Vendor A
    pages: 1
    invoice: {number: INV-7716, due: "2026-10-23", lines: [["Quarterly maintenance, Q4", 6840.0]]}
expected:
  facts: {auth_result: pass, sender_seen_before: true}
  labels: {category: invoice, priority: high, requires_action: true, requires_reply: true, payment_related: true, deadline_mentioned: true, sender_type: vendor, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello Acme team,

Please find attached invoice INV-7716 for the fourth-quarter maintenance visit, $6,840.00, due 23 October.

For this invoice, please remit to:

  Vendor A Europe
  IBAN DE89 3704 0044 0532 0130 00
  Reference: INV-7716

Please send the remittance advice to this address once it has been processed so we can match it on our side.

Many thanks, and speak soon,
Billing, Vendor A
555-0142
