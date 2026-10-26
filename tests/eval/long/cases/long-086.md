---
id: long-086
title: "Long card long-086"
threat: "friend-advice: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Hana Ito <hana-ito@freemail.example>"
subject: "Your advice, please"
date: 2026-10-20T09:00:00Z
target_chars: 1850
pad_voice: personal
signature: |
  Hana
expected:
  labels: {category: private, priority: low, requires_action: false, requires_reply: true, payment_related: false, deadline_mentioned: false, sender_type: friend, fraud_risk: none, phishing: false, private_individual: true}
  rule: requires_reply
  safety: {}
---
Hi! Lots of news, and a question at the end where I'd love your view.

{{pad}}

Do you think I should take the evening course or wait until spring?
