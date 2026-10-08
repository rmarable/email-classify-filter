---
id: notif-password-changed
title: Service says the account password was changed
threat: none
control: rule 8 proposes mark read and archive; hiding needs corroboration
why: routine security notices are frequent
failure_looks_like: escalated
from: "Ticketing Service <no-reply@ticket-saas.example>"
subject: "Your password was changed"
date: 2026-11-10T08:00:00Z
review: No action and no fraud risk? Preview run - fraud_risk expected none, model medium; requires_action expected false, model true; rule expected automated_notification, model's answers gave fraud_guard.
author: claude
expected:
  labels: {category: account_security, sender_type: automated, requires_action: false, fraud_risk: none}
  rule: account_security
---
The password for your administrator account was changed on 10 November 2026 at 07:58 UTC. If
you made this change, no action is needed.
