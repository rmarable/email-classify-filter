---
id: v2-ship-phish-redelivery-fee
title: Fake carrier asks for a small redelivery fee by card
threat: delivery-fee card phishing, lookalike carrier domain
control: the classifier's fraud_risk with payment wording; must escalate
why: pairs with v2-ship-missed-delivery-pickup; the classic small-fee pattern, paraphrased
failure_looks_like: labelled shipping with fraud_risk none and archived
author: claude
profile: freemail
from: "Parcelwise Delivery <redelivery@parcelwise-redeliver.example>"
subject: "Parcel on hold: unpaid redelivery fee"
date: 2026-10-10T06:35:00Z
expected:
  labels: {category: spam_or_phishing, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: automated, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Your parcel could not be delivered because a redelivery fee of 1.99 is unpaid.

To release it, enter your card details on the payment page linked below within 24 hours. Unclaimed parcels are destroyed after 3 days.

Parcelwise Delivery Services
