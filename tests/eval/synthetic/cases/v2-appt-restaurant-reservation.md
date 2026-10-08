---
id: v2-appt-restaurant-reservation
title: Restaurant confirms a table reservation
threat: none (control)
control: a reservation confirmation from a business is appointment, low priority, no fraud
why: reservations are frequent personal mail and must not read as marketing or fraud
failure_looks_like: labelled marketing, or flagged as fraud
author: claude
profile: freemail
from: "Copper Kettle Bistro <reservations@copperkettle.example>"
subject: "Your table for 4 on Friday 2026-10-16"
date: 2026-10-11T17:05:00Z
expected:
  labels: {category: appointment, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: company, fraud_risk: none}
  rule: appointment_school_family
---
Dear Pat Lee,

Thank you for your reservation at the Copper Kettle Bistro.

Party of 4, Friday 2026-10-16 at 19:30, garden room.

If your plans change, just let us know. We hold tables for 15 minutes.

The Copper Kettle team
