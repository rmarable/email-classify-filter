---
id: v2-travel-flight-itinerary
title: Airline sends a flight itinerary
threat: none (control)
control: a plain itinerary with nothing to do is travel, low priority, not fraud
why: the legitimate counterpart to the airline-fee phishing card
failure_looks_like: labelled as needing action, or flagged as fraud
author: claude
profile: freemail
from: "Northwind Air <itinerary@northwindair.example>"
subject: "Your trip to Port Halden: booking 4471"
date: 2026-10-02T16:45:00Z
expected:
  labels: {category: travel, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: travel_finance
---
Hello Pat Lee,

Thank you for flying with Northwind Air. Here is your itinerary for booking 4471.

Outbound: flight NW 412, Thursday 2026-10-29, departs 07:40, arrives 09:55
Return: flight NW 417, Monday 2026-11-02, departs 18:10, arrives 20:20

Checked bags: 1. Online check-in opens 24 hours before each flight.

Northwind Air
