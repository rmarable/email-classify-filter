---
id: forged-ecf-install-header
title: A message carrying ecf's own install header
threat: forged X-ECF-Install header
control: an incoming X-ECF-Install header is itself a fraud signal (trigger 1)
why: an attacker hopes ecf treats the message as its own
failure_looks_like: skipped, trusted as ecf's own, or not escalated
from: "Vendor M Billing <billing@vendor-m.example>"
subject: "Payment reminder"
date: 2026-10-03T16:30:00Z
headers: {X-ECF-Install: "default", X-ECF-Sent: "true"}
expected:
  labels: {category: invoice, payment_related: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
author: claude
---
A friendly reminder that INV-6120 (300.00) is due on Friday.
