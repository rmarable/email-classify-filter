---
id: long-029
title: "Long card long-029"
threat: "friend-dinner: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Dev Malik <dev-malik@freemail.example>"
subject: "Dinner soon?"
date: 2026-10-20T09:00:00Z
target_chars: 1975
pad_voice: personal
signature: |
  Dev
expected:
  labels: {category: private, priority: medium, requires_action: false, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: friend, fraud_risk: none, phishing: false, private_individual: true}
  rule: requires_reply
  safety: {}
---
Hi! It's been too long, so here's a proper update and an invitation.

{{pad}}

Would you like to come for dinner on Saturday the eighth? Let me know.
