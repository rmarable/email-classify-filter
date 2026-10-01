---
id: billing-wrong-tax-rate
title: Customer says the wrong sales-tax rate was charged
threat: none
control: payment keyword from a first-time sender, rule 1b; a reply is needed
why: billing disputes need a person
failure_looks_like: hidden, or classified as regulatory
from: "Customer AL Finance <finance@customer-al.example>"
subject: "Tax on invoice INV-7905"
date: 2026-11-10T11:00:00Z
author: claude
expected:
  labels: {category: billing_inquiry, payment_related: true, requires_reply: true, fraud_risk: none}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Hello,

Invoice INV-7905 charges 9.5% sales tax, but we are in a county with 7.25%. Could you check and
send a corrected invoice?

Customer AL Finance
