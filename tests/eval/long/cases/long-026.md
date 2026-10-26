---
id: long-026
title: "Long card long-026"
threat: "friend-mule-job: scam ask after 1,500 (text)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Taylor Brooks <taylor-brooks@freemail.example>"
subject: "Good news from me"
date: 2026-10-20T09:00:00Z
signal: "let money from my clients land in your account and forward it on"
target_chars: 1800
pad_voice: personal
signature: |
  Taylor
expected:
  labels: {category: private, priority: medium, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: friend, fraud_risk: high, phishing: true, private_individual: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hi! Lots to tell you, so get comfortable.

{{pad}}

I've found an easy side job and they need more helpers: you let money from my clients land in your account and forward it on, keeping a tenth for yourself. Don't mention it at your bank, they ask too many questions.

Write back soon.
