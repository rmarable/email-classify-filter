---
id: v2-ship-shipped-tracking
title: Carrier says an order has shipped
threat: none (control)
control: the category shipping; no fraud signal
why: baseline genuine shipping notice under schema v2
failure_looks_like: labelled spam_or_phishing or notification, or escalated
author: claude
profile: freemail
from: "Parcelwise Tracking <tracking@parcelwise.example>"
subject: "Your parcel has shipped"
date: 2026-10-05T16:20:00Z
expected:
  labels: {category: shipping, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: shipping
---
Good news, Pat. Your order from Lantern Outfitters is on its way.

Tracking number PW 4408 1173 26
Expected delivery: Thursday 8 October.

You can follow it in the Parcelwise app.
