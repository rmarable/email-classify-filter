---
id: billing-invoice-copy
title: Customer asks for a copy of an invoice
threat: none
control: rule 1b
why: the simplest billing request
failure_looks_like: classified as invoice
from: "Customer AM Admin <admin@customer-am.example>"
subject: "Copy of October invoice please"
date: 2026-11-11T14:00:00Z
author: claude
expected:
  labels: {category: billing_inquiry, requires_reply: true, fraud_risk: none}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Hi,

We can't find the October invoice in our records. Could you send a copy?

Customer AM Admin
