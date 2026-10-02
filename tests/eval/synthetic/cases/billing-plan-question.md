---
id: billing-plan-question
title: Customer asks how a price change affects their plan
threat: none
control: requires_reply; not payment instructions
why: a plain billing question with no money moving
failure_looks_like: flagged as fraud
from: "Customer H <admin@customer-h.example>"
subject: "Question about the new pricing"
date: 2026-10-08T16:00:00Z
author: claude
expected:
  labels: {category: billing_inquiry, payment_related: false, requires_reply: true, fraud_risk: none}
  rule: requires_reply
---
Hi team,

We saw the pricing update for next year. We are on the annual plan renewed in March. Does the
new price apply at our next renewal or right away?

Regards,
Customer H
