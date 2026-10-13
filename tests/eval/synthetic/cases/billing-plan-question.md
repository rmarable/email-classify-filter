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
review: a known sender (passes DMARC, seen before) so it reaches the `actor` role, not `actor_high` (v3.0.0 H5); confirm nothing else changes
author: claude
expected:
  labels: {category: billing_inquiry, payment_related: false, requires_reply: true, fraud_risk: none}
  rule: requires_reply
  facts: {auth_result: pass, sender_seen_before: true, bulk_corroborates: false}
---
Hi team,

We saw the pricing update for next year. We are on the annual plan renewed in March. Does the
new price apply at our next renewal or right away?

Regards,
Customer H
