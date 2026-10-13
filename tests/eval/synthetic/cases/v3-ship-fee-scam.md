---
id: v3-ship-fee-scam
title: "Parcel held for a small customs fee"
threat: "parcel-fee scam"
control: "fraud_guard (fraud_risk high: payment details asked by an unknown sender)"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "labelled as shipping"
author: claude
profile: freemail
from: "Parcel Desk <parcels@delivery-notice.test>"
subject: "Your parcel is on hold"
date: 2026-10-06T07:45:00Z
expected:
  labels: {category: spam_or_phishing, sender_type: unknown, priority: medium, requires_action: true, requires_reply: false, payment_related: true, deadline_mentioned: true, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Your parcel could not be delivered because a customs fee of USD 1.99 is unpaid. Enter your card details within 48 hours or it will be returned.
