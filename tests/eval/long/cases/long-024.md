---
id: long-024
title: "Long card long-024"
threat: "friend-holiday-photos: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Sasha Kerr <sasha-kerr@freemail.example>"
subject: "Back from the coast"
date: 2026-10-20T09:00:00Z
target_chars: 1900
pad_voice: personal
signature: |
  Sasha
expected:
  labels: {category: private, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: friend, fraud_risk: none, phishing: false, private_individual: true}
  rule: otherwise
  safety: {}
---
We're back from the coast, and I promised you a full report.

{{pad}}

I'll bring the photos when we see you next.
