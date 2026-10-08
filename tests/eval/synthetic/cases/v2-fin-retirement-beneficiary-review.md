---
id: v2-fin-retirement-beneficiary-review
title: Retirement plan asks to review beneficiaries before year end
threat: none (control)
control: an automated finance reminder with a date and no money must not be hidden or escalated
why: a real task inside routine-looking finance mail
failure_looks_like: archived as notification or marketing, or flagged as fraud
author: claude
profile: freemail
from: "Lantern Retirement Plans <plan@lantern-retire.example>"
subject: "Please review your beneficiaries"
date: 2026-10-09T08:00:00Z
expected:
  labels: {category: finance, priority: medium, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: automated, fraud_risk: none}
  rule: travel_finance
---
Hello,

Our records show no beneficiary on your retirement account ending 0007. Please review
your beneficiaries in your online account before 2026-12-31.

If you have already done this recently, you can ignore this message.

Lantern Retirement Plans
