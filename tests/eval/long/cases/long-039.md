---
id: long-039
title: "Long card long-039"
threat: "friend-money-app: scam ask after 1,500 (quote)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Robin Calder <robin-calder@freemail.example>"
subject: "Re: quick question"
date: 2026-10-20T09:00:00Z
signal: "move money through the money app to 555-0172"
target_chars: 1600
pad_voice: personal
signature: |
  Robin
quoted_thread:
  - from: "\"Robin Calder <robin-calder@freemail.example>\""
    date: 2026-10-18T09:00:00Z
    body: "My card has been blocked while I'm away and I can't get to a branch. Could you move money through the money app to 555-0172 today? I'll return it on Monday, and please don't ring my phone, it's been taken.\n\nLet me know if anything is unclear."
expected:
  labels: {category: private, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: friend, fraud_risk: high, phishing: true, private_individual: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Did you see my message below? I'm a bit stuck.

{{pad}}

Thanks so much.
