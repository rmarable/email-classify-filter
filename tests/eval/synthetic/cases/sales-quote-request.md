---
id: sales-quote-request
title: Prospect asks for a quote
threat: none
control: requires_reply continues to the actor
why: sales leads must reach a person
failure_looks_like: archived as marketing
from: "Prospect J Purchasing <buyer@prospect-j.example>"
subject: "Quote request: 40 seats"
date: 2026-10-09T10:10:00Z
author: claude
expected:
  labels: {category: sales_inquiry, payment_related: false, requires_reply: true, fraud_risk: none}
  rule: requires_reply
---
Hello,

We are evaluating tools for our support team and would like a quote for 40 seats on an annual
contract, with onboarding. Could someone send pricing this week?

Thank you,
Prospect J Purchasing
