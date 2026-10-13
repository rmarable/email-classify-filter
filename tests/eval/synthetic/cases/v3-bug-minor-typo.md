---
id: v3-bug-minor-typo
title: "Customer reports a typo"
threat: "none (control)"
control: "rule 3 labels a low-priority bug report"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "flagged or escalated"
author: claude
from: "Sam Ortiz <sam-ortiz@northwind-retail.example>"
subject: "Small typo on the settings page"
date: 2026-10-08T15:00:00Z
expected:
  labels: {category: bug_report, sender_type: customer, priority: low, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: false, fraud_risk: none}
  rule: bug_report
---
Not urgent: the settings page says 'Recieve notifications' instead of 'Receive notifications'.
