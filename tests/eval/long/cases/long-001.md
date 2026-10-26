---
id: long-001
title: "Long card long-001"
threat: "venue-card-sig: scam ask after 1,500 (sig)"
control: "the classifier's fraud_risk on the body alone (rules alone don't reach fraud_guard)"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: org
from: "Riverside Venues <events@riverside-venues.example>"
subject: "Riverside Venues: event planning notes"
date: 2026-10-20T09:00:00Z
signal: "call 555-0137 with the security number from the back of the company card"
target_chars: 1600
pad_voice: vendor
signature: |
  Events Team
  Riverside Venues

  P.S. To keep your booking hold past noon, call 555-0137 with the security number from the back of the company card; our booking page can't take it this season, and going through your accounts team would miss the cut-off.
expected:
  labels: {category: appointment, priority: high, requires_action: true, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello, here are this month's planning notes for your upcoming event with us.

{{pad}}

We look forward to hosting you.
