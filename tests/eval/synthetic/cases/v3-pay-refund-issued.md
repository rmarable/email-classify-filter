---
id: v3-pay-refund-issued
title: "Refund notice from a known vendor"
threat: "none (control)"
control: "rule 5 labels a refund from a known, authenticated sender"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "escalated as fraud"
author: claude
from: "Support <support@office-supplies.example>"
subject: "Your refund has been issued"
date: 2026-10-04T11:00:00Z
expected:
  labels: {category: remittance, sender_type: vendor, priority: low, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, fraud_risk: none}
  facts: {auth_result: pass, sender_seen_before: true, bulk_corroborates: false}
  rule: payment_confirmation_remittance
---
We have refunded USD 58.20 for the returned toner cartridges (order 55190) to the card you paid with. It can take five business days to appear.
