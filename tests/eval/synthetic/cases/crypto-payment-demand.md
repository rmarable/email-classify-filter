---
id: crypto-payment-demand
title: Demand to settle an invoice in cryptocurrency
threat: payment in an unusual, irreversible channel
control: fraud guard (fraud risk from the request)
why: legitimate vendors don't switch to crypto wallets by email
failure_looks_like: labelled as an ordinary invoice
from: "Vendor H Collections <collections@vendor-h.example>"
subject: "Final notice: settle INV-5150 today"
date: 2026-10-03T08:30:00Z
expected:
  labels: {category: invoice, payment_related: true, fraud_risk: high, priority: urgent}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
author: claude
---
Your account is past due. Settle INV-5150 (2,150.00) within 4 hours by sending the equivalent in
cryptocurrency to wallet TEST-WALLET-0001, or legal action begins today. Bank transfers are no
longer accepted.
