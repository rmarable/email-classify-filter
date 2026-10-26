---
id: long-045
title: "Long card long-045"
threat: "supplier-holiday-hours: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Vendor G Customer Care <care@vendor-g.example>"
subject: "Holiday opening hours"
date: 2026-10-20T09:00:00Z
target_chars: 1850
pad_voice: vendor
signature: |
  Customer Care
  Vendor G
expected:
  labels: {category: notification, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: vendor, fraud_risk: none, phishing: false, private_individual: false}
  rule: otherwise
  safety: {}
---
Hello, here are our opening hours over the holiday period.

{{pad}}

Orders placed during the break will be sent on the first working day after it.
