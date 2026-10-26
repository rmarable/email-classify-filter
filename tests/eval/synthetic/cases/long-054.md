---
id: long-054
title: "Long card long-054"
threat: "telecom-porting-number: scam ask after 1,500 (quote)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: org
from: "Clearline Business Phones <accounts@clearline-phones.example>"
subject: "Re: your business lines"
date: 2026-10-20T09:00:00Z
signal: "read us the six-digit number our text sends to your phone"
target_chars: 1575
pad_voice: vendor
signature: |
  Clearline Business Phones
quoted_thread:
  - from: "\"Clearline Business Phones <accounts@clearline-phones.example>\""
    date: 2026-10-18T09:00:00Z
    body: "To stop your company numbers being moved to another provider today, call us on 555-0186 and read us the six-digit number our text sends to your phone. Please don't contact your usual account desk; they can't stop the move.\n\nLet me know if anything is unclear."
expected:
  labels: {category: account_security, priority: high, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Following up on my message below about your company lines.

{{pad}}

Thanks for acting quickly.
