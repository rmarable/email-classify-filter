---
id: long-047
title: "Long card long-047"
threat: "customer-thanks: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Sam Patel <sam-patel@cafe-co.example>"
subject: "Thank you"
date: 2026-10-20T09:00:00Z
target_chars: 1825
pad_voice: vendor
signature: |
  Sam Patel
  Cafe Co
expected:
  labels: {category: notification, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: customer, fraud_risk: none, phishing: false, private_individual: false}
  rule: otherwise
  safety: {}
---
Just a note to say thank you for the delivery last week.

{{pad}}

Everything arrived in good order, and the team is very pleased.
