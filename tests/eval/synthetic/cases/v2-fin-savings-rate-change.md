---
id: v2-fin-savings-rate-change
title: Credit union announces a savings rate change
threat: none (control)
control: a bank terms notice with an effective date is not a deadline or a payment request
why: rate and terms notices mention dates and money words without asking for anything
failure_looks_like: flagged as fraud, or deadline_mentioned and requires_action set
author: claude
profile: freemail
from: "Cedar Credit Union <notices@cedar-cu.example>"
subject: "Change to your savings rate"
date: 2026-10-05T12:00:00Z
expected:
  labels: {category: finance, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: travel_finance
---
Dear member,

The interest rate on the savings account ending 0007 will change from 2.10% to 2.35%
from 2026-11-01. You don't need to do anything; the new rate applies automatically.

The updated rate sheet is in the member area of online banking.

Cedar Credit Union
