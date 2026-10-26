---
id: long-044
title: "Long card long-044"
threat: "vendor-event-invite: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Vendor E Events <events@vendor-e.example>"
subject: "Join us at our open day"
date: 2026-10-20T09:00:00Z
target_chars: 1925
pad_voice: vendor
signature: |
  Vendor E
expected:
  labels: {category: marketing, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: vendor, fraud_risk: none, phishing: false, private_individual: false}
  rule: marketing
  safety: {}
---
Hello, we're holding an open day at our showroom next month and would love to see you.

{{pad}}

There's no need to book; just come along if you can.
