---
id: long-025
title: "Long card long-025"
threat: "partnership-pitch: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Greenfield Studio <hello@greenfield-studio.example>"
subject: "An idea for working together"
date: 2026-10-20T09:00:00Z
target_chars: 2000
pad_voice: vendor
signature: |
  Greenfield Studio
expected:
  labels: {category: partnership, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: company, fraud_risk: none, phishing: false, private_individual: false}
  rule: otherwise
  safety: {}
---
Hello, I run a small design studio and have been following your products.

{{pad}}

Would you be open to a short call next month to talk about a joint range?
