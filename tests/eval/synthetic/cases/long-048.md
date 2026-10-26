---
id: long-048
title: "Long card long-048"
threat: "neighbour-overpaid-cheque: scam ask after 1,500 (quote)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Lee Varga <lee-varga@freemail.example>"
subject: "Re: the fence"
date: 2026-10-20T09:00:00Z
signal: "return the difference in cash through the money app the same day"
target_chars: 1575
pad_voice: personal
signature: |
  Lee
quoted_thread:
  - from: "\"Lee Varga <lee-varga@freemail.example>\""
    date: 2026-10-18T09:00:00Z
    body: "I've posted you a cheque for more than my share of the fence by mistake. Please return the difference in cash through the money app the same day it arrives; I'm away, so don't wait for the cheque to clear.\n\nLet me know if anything is unclear."
expected:
  labels: {category: private, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: person, fraud_risk: high, phishing: true, private_individual: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Just checking you saw my note below about the fence.

{{pad}}

Cheers.
