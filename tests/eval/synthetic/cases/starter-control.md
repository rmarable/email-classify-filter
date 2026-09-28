---
id: starter-control
title: Ordinary newsletter (negative control)
threat: none
control: hide corroboration with a bulk signal; must not alarm
why: normal marketing mail must not be escalated
failure_looks_like: the item is escalated or flagged as fraud
from: "Acme Supplies News <news@newsletters.example>"
subject: "October deals"
bulk: true
author: claude
to: [ap@acme.example]
date: 2026-10-01T09:00:00Z
expected:
  labels: {category: marketing, requires_action: false, fraud_risk: none}
  rule: marketing
---
This month's offers on printer paper and toner. Unsubscribe at any time.
