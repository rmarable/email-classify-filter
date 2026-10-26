---
id: long-059
title: "Long card long-059"
threat: "friend-recipe: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Priya Nair <priya-nair@freemail.example>"
subject: "That recipe"
date: 2026-10-20T09:00:00Z
target_chars: 1900
pad_voice: personal
signature: |
  Priya
expected:
  labels: {category: private, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: friend, fraud_risk: none, phishing: false, private_individual: true}
  rule: otherwise
  safety: {}
---
Here's the recipe I promised, plus all my news.

{{pad}}

Let me know how it turns out.
