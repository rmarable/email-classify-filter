---
id: v2-fin-dividend-reinvested
title: Brokerage reports a dividend paid and reinvested
threat: none (control)
control: money received into an investment account is payment_related but must not escalate
why: separates a finance notice that mentions money from a payment request
failure_looks_like: escalated as fraud, or labelled invoice
review: finance or payment_confirmation is a judgement call; drafted as finance (a brokerage account notice)
author: claude
profile: freemail
from: "Bayline Brokerage <notices@bayline-invest.example>"
subject: "Dividend reinvested in your account"
date: 2026-10-07T07:00:00Z
expected:
  labels: {category: finance, priority: low, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: unverified_payment_sender
---
Hello Pat Lee,

A dividend of 42.18 from the Evergreen Total Market Fund was paid to your account ending
0007 on 2026-10-06 and reinvested in the same fund, as your settings request.

No action is needed. The details will appear on your next statement.

Bayline Brokerage
