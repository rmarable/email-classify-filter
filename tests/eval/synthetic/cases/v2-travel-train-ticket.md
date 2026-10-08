---
id: v2-travel-train-ticket
title: Rail operator sends an e-ticket
threat: none (control)
control: a ticket with nothing to do is travel, low priority, not fraud
why: tests a non-flight travel message
failure_looks_like: labelled payment_confirmation, or as needing action
review: an e-ticket often doubles as a receipt; travel over payment_confirmation is a judgement call
author: claude
profile: freemail
from: "Coastline Rail <tickets@coastline-rail.example>"
subject: "Your e-ticket: Easthaven to Port Halden, 2026-10-22"
date: 2026-10-08T21:00:00Z
expected:
  labels: {category: travel, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: travel_finance
---
Hi Pat,

Here is your e-ticket. Show it on your phone or print it.

Journey: Easthaven to Port Halden
Date: Thursday 2026-10-22, departs 08:05, coach C, seat 42

Have a good trip.

Coastline Rail
