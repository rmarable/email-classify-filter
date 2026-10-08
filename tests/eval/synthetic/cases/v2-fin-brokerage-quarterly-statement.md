---
id: v2-fin-brokerage-quarterly-statement
title: Quarterly brokerage statement notice
threat: none (control)
control: a routine investment statement notice must be labelled finance, not notification or marketing
why: tests the new finance category on the most common personal finance mail
failure_looks_like: labelled notification or marketing, or flagged as fraud
author: claude
profile: freemail
from: "Bayline Brokerage <statements@bayline-invest.example>"
subject: "Your third-quarter statement"
date: 2026-10-06T05:30:00Z
expected:
  labels: {category: finance, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: travel_finance
---
Hello,

Your statement for the quarter ending 2026-09-30 is ready for the individual account
ending 0007. It shows your holdings, activity and any fees for the period.

Sign in as you usually do to read it. Bayline will never ask for your password by email.

Bayline Brokerage
