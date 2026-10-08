---
id: v2-appt-salon-confirmed
title: Hair salon confirms a booking
threat: none (control)
control: a routine booking confirmation with nothing to do is low priority, not fraud
why: the most common appointment mail; it should be labelled correctly and left quiet
failure_looks_like: labelled as fraud or as needing action
review: deadline_mentioned false assumes an appointment time alone is not a deadline
author: claude
profile: freemail
from: "Willow and Thread Salon <bookings@willowthread-salon.example>"
subject: "Booking confirmed: Saturday 2026-10-24, 11:00"
date: 2026-10-09T10:20:00Z
expected:
  labels: {category: appointment, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: appointment_school_family
---
Hi Pat,

Your booking is confirmed.

Service: cut and blow-dry with Remy
When: Saturday 2026-10-24 at 11:00

We look forward to seeing you.

Willow and Thread Salon
