---
id: v3-ship-missed-delivery
title: "Genuine missed-delivery notice"
threat: "none (control)"
control: "rule 5b labels shipping and leaves it"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "flagged as fraud"
author: claude
profile: freemail
from: "Delivery Updates <updates@parcel-carrier.example>"
subject: "We missed you today"
date: 2026-10-07T17:00:00Z
expected:
  labels: {category: shipping, sender_type: vendor, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, fraud_risk: none}
  rule: shipping
---
We tried to deliver your parcel today. We'll try again tomorrow between 9:00 and 13:00, or you can collect it from the depot on Main Street.
