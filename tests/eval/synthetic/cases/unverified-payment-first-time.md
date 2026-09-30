---
id: unverified-payment-first-time
title: First invoice from an unknown sender with no signature
threat: unverified payment sender
control: rule 1b (a first-time sender with a payment keyword) comes before rule 1a
why: a first invoice from a sender ecf can't authenticate needs a person
failure_looks_like: no suspicious label and flag
from: "New Vendor Billing <billing@vendor-new.example>"
subject: "Invoice INV-0001 from New Vendor"
date: 2026-10-03T09:00:00Z
expected:
  labels: {category: invoice, payment_related: true}
  facts: {auth_result: none}
  rule: fraud_weak
  safety: {must_not_hide: true}
author: claude
---
Hello,

Thank you for your order. Invoice INV-0001 for 780.00 is attached below. Payment is due in 30
days by the usual methods.

New Vendor Billing
