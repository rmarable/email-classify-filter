---
id: payconf-refund-issued
title: Vendor confirms a refund to ACME
threat: none (control)
control: payment mail stays visible
why: refunds arriving are payment confirmations too
failure_looks_like: escalated as fraud
from: "Vendor B Accounts <accounts@vendor-b.example>"
subject: "Refund issued for order 6610"
date: 2026-10-29T10:00:00Z
author: claude
expected:
  labels: {category: payment_confirmation, payment_related: true, fraud_risk: none}
  rule: unverified_payment_sender
  safety: {must_not_hide: true}
---
Hello,

We issued a refund of 214.00 for the damaged items in order 6610. It should appear on your card
within 5 business days.

Vendor B Accounts
