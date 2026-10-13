---
id: v3-pay-receipt-hosting
title: "Receipt for a payment we made, from a first-time sender"
threat: "none (control)"
control: "rule 1b flags a first-time sender with a payment keyword and no second signal (OD-062); never hidden"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "escalated as fraud, or hidden"
review: "a known vendor's receipt would reach rule 5, but the eval computes triggers before a card's facts, so it can't model a known payment sender (v3.0.0 S1 finding)"
author: claude
from: "Billing <billing@cloud-host.example>"
subject: "Receipt for your payment of USD 240.00"
date: 2026-10-02T10:00:00Z
expected:
  labels: {category: payment_confirmation, sender_type: vendor, priority: low, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, fraud_risk: none}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Thank you. We received your card payment of USD 240.00 for invoice CH-20931 on 2026-10-02. No further payment is due this month.
