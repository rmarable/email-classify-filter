---
id: mid-polite-overdue-reminder
title: Supplier sends a polite second reminder for an overdue invoice
threat: low (a real reminder; urgency without pressure)
control: rule 1b flags it
why: overdue reminders are routine and should not escalate on wording alone
failure_looks_like: escalated as fraud
from: "Vendor R Credit Control <credit@vendor-r.example>"
subject: "Second reminder - INV-R-3301"
date: 2026-11-13T10:30:00Z
author: claude
expected:
  labels: {category: invoice, payment_related: true, fraud_risk: low}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Dear customer,

Our records show invoice INV-R-3301 (760.00) is now 15 days overdue. If you have already paid,
please ignore this reminder. Otherwise we would be grateful for payment this week.

Vendor R Credit Control
