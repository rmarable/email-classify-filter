---
id: long-021
title: "Long card long-021"
threat: "carrier-tracking: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Northgate Post Tracking <tracking@northgate-post.example>"
subject: "Your shipment is on its way"
date: 2026-10-20T09:00:00Z
target_chars: 1925
pad_voice: vendor
signature: |
  Northgate Post
expected:
  labels: {category: shipping, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none, phishing: false, private_individual: false}
  rule: shipping
  safety: {}
---
Your shipment left our depot this morning and is on its way to you.

{{pad}}

You don't need to do anything; we'll deliver it as planned.
