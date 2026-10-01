---
id: notif-maintenance-window
title: Software provider announces a maintenance window
threat: none
control: rule 8
why: maintenance notices have dates but need no action
failure_looks_like: flagged for a deadline
from: "Status Updates <status@email-host.example>"
subject: "Scheduled maintenance on 20 November"
date: 2026-11-12T15:00:00Z
author: claude
expected:
  labels: {category: notification, sender_type: automated, requires_action: false}
  rule: automated_notification
---
Scheduled maintenance: 20 November 2026, 01:00-03:00 UTC. Mail may be delayed by up to 15
minutes during this window. No action is required.
