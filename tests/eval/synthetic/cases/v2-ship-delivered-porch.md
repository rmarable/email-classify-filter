---
id: v2-ship-delivered-porch
title: Carrier confirms the parcel was delivered
threat: none (control)
control: shipping, informational
why: the last step in a genuine delivery sequence
failure_looks_like: labelled spam_or_phishing or escalated
author: claude
profile: freemail
from: "Parcelwise Tracking <tracking@parcelwise.example>"
subject: "Delivered"
date: 2026-10-08T11:47:00Z
expected:
  labels: {category: shipping, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: shipping
---
Your parcel PW 4408 1173 26 was delivered at 11:45 and left at the front door.

If you can't find it, report a problem in the Parcelwise app within 7 days.

Parcelwise
