---
id: long-090
title: "Long card long-090"
threat: "vendor-new-contact: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Vendor J Customer Team <team@vendor-j.example>"
subject: "Introducing your new contact"
date: 2026-10-20T09:00:00Z
target_chars: 1850
pad_voice: vendor
signature: |
  Customer Team
  Vendor J
expected:
  labels: {category: notification, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: vendor, fraud_risk: none, phishing: false, private_individual: false}
  rule: otherwise
  safety: {}
---
Hello, from next month your account will be looked after by a new member of our team.

{{pad}}

Your existing orders and contacts stay as they are; nothing is needed from you.
