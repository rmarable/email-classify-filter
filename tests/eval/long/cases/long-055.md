---
id: long-055
title: "Long card long-055"
threat: "schoolfriend-villa-scheme: scam ask after 1,500 (text)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Kim Arden <kim-arden@freemail.example>"
subject: "Long time no see"
date: 2026-10-20T09:00:00Z
signal: "put in what you can at villa-yield-club.example before it closes on Sunday"
target_chars: 1775
pad_voice: personal
signature: |
  Kim
expected:
  labels: {category: private, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: friend, fraud_risk: high, phishing: true, private_individual: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello! Remember me from school? I found your address and had to write.

{{pad}}

I've joined a property scheme abroad that pays a tenth every month. You should put in what you can at villa-yield-club.example before it closes on Sunday; they only take people who are recommended.

Hope to hear from you.
