---
id: long-056
title: "Long card long-056"
threat: "neighbour-fence-date: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Owen Clarke <owen-clarke@freemail.example>"
subject: "The fence"
date: 2026-10-20T09:00:00Z
target_chars: 1925
pad_voice: personal
signature: |
  Owen
expected:
  labels: {category: private, priority: medium, requires_action: false, requires_reply: true, payment_related: false, deadline_mentioned: false, sender_type: person, fraud_risk: none, phishing: false, private_individual: true}
  rule: requires_reply
  safety: {}
---
Hello, I hope you're well. I wanted to follow up about the fence between our gardens.

{{pad}}

Could we agree a weekend for the work? Any Saturday suits us.
