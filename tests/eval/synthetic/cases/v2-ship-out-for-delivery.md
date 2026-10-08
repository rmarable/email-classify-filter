---
id: v2-ship-out-for-delivery
title: Carrier says the parcel is out for delivery today
threat: none (control)
control: shipping, informational
why: genuine counterpart to the fee and address-confirmation phish
failure_looks_like: labelled spam_or_phishing
author: claude
profile: freemail
from: "Parcelwise Tracking <tracking@parcelwise.example>"
subject: "Out for delivery today"
date: 2026-10-08T07:30:00Z
expected:
  labels: {category: shipping, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: shipping
---
Your parcel PW 4408 1173 26 is out for delivery.

Estimated window: 10 am to 2 pm. No signature is needed; if nobody is home the driver will leave it in a safe place.

Parcelwise
