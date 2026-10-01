---
id: remit-short-pay-question
title: Remittance that short-pays an invoice and asks a question
threat: none
control: rule 1b (first-time sender with a payment keyword); payment mail is never hidden
why: a remittance that needs an answer must reach a person
failure_looks_like: archived or marked read
from: "Customer F Accounts <ap@customer-f.example>"
subject: "Remittance for ACME-1120 (partial)"
date: 2026-10-06T15:45:00Z
author: claude
expected:
  labels: {category: remittance, payment_related: true, requires_reply: true, fraud_risk: none}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Hi,

We paid 900.00 of ACME-1120 today. The remaining 100.00 is for the damaged item we reported
last week. Can you confirm a credit note for that amount?

Thanks,
Customer F Accounts
