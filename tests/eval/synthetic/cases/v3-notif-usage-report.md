---
id: v3-notif-usage-report
title: "Automated monthly usage report"
threat: "none (control)"
control: "rule 8 may archive a report that asks for nothing"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "flagged or escalated"
author: claude
from: "Usage Reports <reports@cloud-host.example>"
subject: "Your September usage summary"
date: 2026-10-01T06:00:00Z
bulk: true
expected:
  labels: {category: notification, sender_type: automated, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, fraud_risk: none}
  rule: automated_notification
---
Your account used 412 compute hours and 1.2 TB of storage in September. This is a summary only; your plan covers it.
