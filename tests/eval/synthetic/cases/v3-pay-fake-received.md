---
id: v3-pay-fake-received
title: "Fake 'payment received' asking to confirm account details"
threat: "credential phishing dressed as a payment confirmation"
control: "fraud_guard on fraud_risk high (credentials asked)"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "labelled as a payment confirmation"
author: claude
from: "Payments Team <payments@pay-notice.test>"
subject: "Payment received - confirm your account to release funds"
date: 2026-10-05T09:40:00Z
expected:
  labels: {category: spam_or_phishing, sender_type: vendor, priority: high, requires_action: true, requires_reply: true, payment_related: true, deadline_mentioned: true, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
We received a payment of USD 4,980.00 for your account. To release the funds, confirm your account by entering your online banking username and password on our secure page within 24 hours.
