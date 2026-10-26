---
id: long-009
title: "Long card long-009"
threat: "hardware-remote: scam ask after 1,500 (quote)"
control: "the classifier's fraud_risk on the body alone (rules alone don't reach fraud_guard)"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: org
from: "Corelink Hardware Support <support@corelink-hardware.example>"
subject: "Re: Corelink support visit"
date: 2026-10-20T09:00:00Z
signal: "download the helper from the link below and read out the number it shows"
target_chars: 1575
pad_voice: vendor
signature: |
  Corelink Hardware Support
quoted_thread:
  - from: "\"Corelink Hardware Support <support@corelink-hardware.example>\""
    date: 2026-10-18T09:00:00Z
    body: "Our technician needs to connect to your server remotely today. Please download the helper from the link below and read out the number it shows when he calls on 555-0153. There's no need to tell your IT team; this is routine on our side.\n\nLet me know if anything is unclear."
expected:
  labels: {category: action_alert, priority: high, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Following up on my note below, as we haven't heard back from your team.

{{pad}}

Thanks for your patience.
