---
id: long-087
title: "Long card long-087"
threat: "customer-order-question: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Ravi Kumar <ravi-kumar@hotel-co.example>"
subject: "Question about our standing order"
date: 2026-10-20T09:00:00Z
target_chars: 1850
pad_voice: vendor
signature: |
  Ravi Kumar
  Facilities, Hotel Co
expected:
  labels: {category: customer_request, priority: medium, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: false, sender_type: customer, fraud_risk: none, phishing: false, private_individual: false}
  rule: requires_reply
  safety: {}
---
Hello, we'd like to check something about our standing order before the winter season.

{{pad}}

Could you confirm whether the larger storage units can be added to it from next month?
