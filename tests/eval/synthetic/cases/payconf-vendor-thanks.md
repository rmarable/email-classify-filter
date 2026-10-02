---
id: payconf-vendor-thanks
title: A vendor confirms our payment arrived
threat: none (control)
control: rule 1b for a first-time sender with a payment keyword; no escalation
why: balances the payment cases with an outgoing-payment confirmation
failure_looks_like: escalated, or hidden
from: "Vendor D Accounts <accounts@vendor-d.example>"
subject: "Re: INV-2210 - payment received, thank you"
date: 2026-10-05T14:20:00Z
author: claude
expected:
  labels: {category: payment_confirmation, payment_related: true, fraud_risk: none}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Hi,

Just confirming we received your payment of 3,600.00 for INV-2210 this morning. Nothing else is
needed. Thanks for the quick turnaround.

Vendor D Accounts (555-0161)
