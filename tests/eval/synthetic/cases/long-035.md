---
id: long-035
title: "Long card long-035"
threat: "overseas-flight: scam ask after 1,500 (text)"
control: "the classifier's fraud_risk on the body alone"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits after 1,500; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Morgan Ashby <morgan-ashby@freemail.example>"
subject: "Thinking of you"
date: 2026-10-20T09:00:00Z
signal: "cover my flight home at travelfare-booking.example"
target_chars: 1650
pad_voice: personal
signature: |
  Morgan
expected:
  labels: {category: private, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: person, fraud_risk: high, phishing: true, private_individual: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello my dear, I hope your week has been kind to you.

{{pad}}

My wages from the site are stuck until the contract ends, and I can't leave without a ticket. Could you cover my flight home at travelfare-booking.example? I will repay you as soon as I land, and then at last we can meet.

All my love.
