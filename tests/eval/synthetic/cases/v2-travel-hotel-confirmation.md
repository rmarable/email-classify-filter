---
id: v2-travel-hotel-confirmation
title: Hotel confirms a stay with free cancellation until a date
threat: none (control)
control: a hotel confirmation that states a cancellation limit has deadline_mentioned true but no action
why: the legitimate counterpart to the hotel hold-fee phishing card
failure_looks_like: flagged as fraud, or labelled as needing action
author: claude
profile: freemail
from: "Harborline Stays <stays@harborline.example>"
subject: "Stay confirmed: Harborline Port Halden, 2026-10-29 to 2026-11-02"
date: 2026-10-03T10:15:00Z
expected:
  labels: {category: travel, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: company, fraud_risk: none}
  rule: travel_finance
---
Dear Pat Lee,

Your stay at Harborline Port Halden is confirmed: 4 nights, check-in Thursday
2026-10-29 from 15:00, check-out Monday 2026-11-02 by 11:00. Room: queen, harbour side.

You can cancel free of charge until 2026-10-26 at 18:00 in your Harborline account.

We look forward to welcoming you.

Harborline Stays
