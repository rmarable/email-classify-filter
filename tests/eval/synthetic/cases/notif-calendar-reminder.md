---
id: notif-calendar-reminder
title: Automated calendar reminder
threat: none (control)
control: rule 8
why: balances the set
failure_looks_like: flagged
from: "Calendar <reminders@calendar-service.example>"
subject: "Reminder: Quarterly review tomorrow at 10:00"
date: 2026-10-12T17:00:00Z
author: claude
expected:
  labels: {category: notification, sender_type: automated, fraud_risk: none}
  rule: automated_notification
---
Reminder: Quarterly review, tomorrow 10:00-11:00, Room 2.
