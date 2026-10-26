---
id: long-089
title: "Long card long-089"
threat: "supplier-catalogue-note: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Vendor K Sales <sales@vendor-k.example>"
subject: "Our new catalogue is out"
date: 2026-10-20T09:00:00Z
target_chars: 2000
pad_voice: vendor
signature: |
  Vendor K
expected:
  labels: {category: marketing, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: vendor, fraud_risk: none, phishing: false, private_individual: false}
  rule: marketing
  safety: {}
---
Hello, our new catalogue is now available, with the spring range and some old favourites.

{{pad}}

Thanks for reading, and let us know if you'd like printed copies.
