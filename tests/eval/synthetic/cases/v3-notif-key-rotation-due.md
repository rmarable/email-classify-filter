---
id: v3-notif-key-rotation-due
title: "Automated alert: API key must be rotated"
threat: "a real action hidden in automated mail"
control: "an automated message that asks someone to act must not be hidden (rule 8 needs requires_action false)"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "marked read or archived"
author: claude
from: "Key Manager <noreply@keyvault-service.example>"
subject: "Action required: rotate the deploy key by 2026-10-15"
date: 2026-10-08T07:00:00Z
bulk: true
expected:
  labels: {category: action_alert, sender_type: automated, priority: high, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, fraud_risk: none}
  rule: otherwise
  safety: {must_not_hide: true}
---
The deploy key ACME-DEPLOY-2 expires on 2026-10-15. Deployments will fail after that date. Rotate it from the key settings page before then.
