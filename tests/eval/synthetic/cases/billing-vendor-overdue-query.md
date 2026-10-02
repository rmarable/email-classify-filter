---
id: billing-vendor-overdue-query
title: Vendor asks why an invoice is still unpaid
threat: none
control: rule 1b (first-time sender with a payment keyword); no change request, so no escalation
why: a dunning-style query is routine, not fraud
failure_looks_like: escalated as fraud
from: "Vendor A Billing <billing@vendor-a.example>"
subject: "INV-4077 now 15 days overdue"
date: 2026-10-08T09:30:00Z
author: claude
expected:
  labels: {category: billing_inquiry, payment_related: true, requires_reply: true, fraud_risk: none}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Hi,

Our records show INV-4077 (1,180.00, due 23 September) is still open. Could you let us know when
we can expect payment, or whether there is a problem with the invoice?

Vendor A Billing (555-0150)
