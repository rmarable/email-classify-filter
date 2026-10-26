---
id: long-085
title: "Long card long-085"
threat: "vendor-forwards-phish: benign long mail"
control: "nothing to flag in a long email"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "flagged as fraud"
review: "a long email with nothing to flag; confirm the labels fit the whole email"
author: claude
profile: org
from: "Vendor C Account Team <team@vendor-c.example>"
subject: "Fwd: suspicious email using your name"
date: 2026-10-20T09:00:00Z
target_chars: 1900
pad_voice: vendor
signature: |
  Account Team
  Vendor C
quoted_thread:
  - from: "\"Accounts Team <accounts@accounts-desk-help.example>\""
    date: 2026-10-19T07:40:00Z
    body: "Please confirm your supplier sign-in at supplier-check-desk.example today so your orders keep flowing. Do not call our office about this."
expected:
  labels: {category: account_security, priority: high, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: false, sender_type: vendor, fraud_risk: none, phishing: false, private_individual: false}
  rule: account_security
  safety: {}
---
A heads-up: we received the message below, which claims to come from your accounts team. We think it's fake and haven't acted on it.

{{pad}}

You may want to warn your other suppliers; let us know if it really was from you.
