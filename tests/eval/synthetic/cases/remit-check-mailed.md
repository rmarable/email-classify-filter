---
id: remit-check-mailed
title: Customer says a check for two invoices is in the mail
threat: none (ordinary remittance)
control: first-time sender with a payment keyword is flagged, not escalated (rule 1b)
why: plain remittance advice from a new customer contact
failure_looks_like: escalated as fraud, or hidden
from: "Customer AB Accounts Payable <ap@customer-ab.example>"
subject: "Remittance - check mailed for INV-7710 and INV-7714"
date: 2026-11-03T09:00:00Z
review: Remittance or payment_confirmation? Preview run - category expected remittance, model payment_confirmation.
author: claude
expected:
  labels: {category: remittance, payment_related: true, requires_reply: false, fraud_risk: none}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Hello,

A check for 2,340.00 covering invoices INV-7710 and INV-7714 was mailed yesterday. It should
arrive within five business days.

Customer AB Accounts Payable
