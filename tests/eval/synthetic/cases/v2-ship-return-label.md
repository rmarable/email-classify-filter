---
id: v2-ship-return-label
title: Store accepts a return and explains the refund
threat: none (control)
control: shipping (return update) with a deadline and a refund amount
why: a return update carries money wording (refund) and a drop-off deadline without being a bill or fraud
failure_looks_like: labelled invoice or payment_confirmation, or fraud risk raised
review: payment_related true because a refund will be paid; category shipping (return update) rather than payment_confirmation; confirm
author: claude
profile: freemail
from: "Lantern Outfitters Orders <orders@lantern-outfitters.example>"
subject: "Your return is approved"
date: 2026-10-14T10:00:00Z
expected:
  labels: {category: shipping, priority: medium, requires_action: true, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: company, fraud_risk: none}
  rule: fraud_weak
---
Hi Pat,

Your return for the rain jacket (order LO-20417) is approved.

Print the return label from your orders page as you usually do and drop the parcel at any Parcelwise point by 30 October.

Once it reaches us we will refund 42.50 to your original payment card within 5 days.

Lantern Outfitters
