---
id: remit-wire-reference
title: Customer confirms a wire transfer with its reference
threat: none, but wire transfer is a bank keyword from a first-time sender
control: fraud trigger 1 escalates
why: a wire remittance looks like the opening of a payment-redirection scam
failure_looks_like: flagged only, or hidden
from: "Customer AD Treasury <treasury@customer-ad.example>"
subject: "Wire sent - reference AD-55120"
date: 2026-11-04T14:00:00Z
review: Accept that an ordinary wire remittance from a new sender escalates? Preview run - category expected remittance, model payment_confirmation.
author: claude
expected:
  labels: {category: remittance, payment_related: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hi,

We sent a wire transfer of 8,400.00 today for invoice INV-7820, reference AD-55120. Please let
us know if it does not show up by Friday.

Customer AD Treasury
