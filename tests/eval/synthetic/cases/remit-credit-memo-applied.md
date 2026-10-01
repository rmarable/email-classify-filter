---
id: remit-credit-memo-applied
title: Remittance that applies a credit memo and pays the net amount
threat: none
control: rule 1b flags it
why: net payments look like short pays but are agreed
failure_looks_like: escalated
from: "Customer AG Payables <ap@customer-ag.example>"
subject: "Payment for INV-7850 less credit memo CM-310"
date: 2026-11-06T09:15:00Z
author: claude
expected:
  labels: {category: remittance, payment_related: true, requires_reply: false}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Hello,

Our payment today covers invoice INV-7850 (4,000.00) less credit memo CM-310 (250.00), so the
amount sent is 3,750.00.

Customer AG Payables
