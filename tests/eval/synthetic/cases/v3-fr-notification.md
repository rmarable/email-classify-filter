---
id: v3-fr-notification
title: "Automated notification in French"
threat: "none (control)"
control: "rule 8 may archive an automated notification needing nothing"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "flagged or escalated"
author: claude
from: "Sauvegarde <noreply@sauvegarde-service.example>"
subject: "Sauvegarde terminee"
date: 2026-10-07T03:00:00Z
bulk: true
expected:
  labels: {category: notification, sender_type: automated, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, fraud_risk: none}
  rule: automated_notification
---
La sauvegarde nocturne ACME-NUIT s'est terminee a 02:55. 11 980 fichiers, aucune erreur. Aucune action n'est requise.
