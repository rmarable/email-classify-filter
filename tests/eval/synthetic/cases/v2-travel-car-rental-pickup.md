---
id: v2-travel-car-rental-pickup
title: Car rental sends pickup details
threat: none (control)
control: rental pickup details with nothing to do are travel, low priority
why: tests a third travel service type
failure_looks_like: labelled appointment or as needing action
author: claude
profile: freemail
from: "Pinegate Car Rental <noreply@pinegate-rental.example>"
subject: "Pickup details for your rental on 2026-10-29"
date: 2026-10-26T12:00:00Z
expected:
  labels: {category: travel, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: travel_finance
---
Hello Pat,

Your compact car is ready for pickup at the Port Halden airport desk on Thursday
2026-10-29 from 10:00.

Please bring your driving licence and the card used to book. The desk is on the
arrivals level next to baggage claim.

Pinegate Car Rental
