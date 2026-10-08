---
id: v2-appt-car-service-booked
title: Garage confirms a car service booking
threat: none (control)
control: a booking confirmation with no request is low priority and not fraud
why: tests a non-medical, non-social appointment from a business
failure_looks_like: labelled as needing action, or as an invoice
author: claude
profile: freemail
from: "Fernbrook Auto Care <service@fernbrook-auto.example>"
subject: "Service booked for Wednesday 2026-10-21"
date: 2026-10-14T15:50:00Z
expected:
  labels: {category: appointment, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: appointment_school_family
---
Hi Pat,

Your car is booked in for its yearly service on Wednesday 2026-10-21. Drop-off is from
07:30 and we will text you when it is ready.

The courtesy shuttle runs every hour if you need a ride.

Fernbrook Auto Care
