---
id: v2-appt-vet-vaccines
title: Vet clinic says a pet is due for vaccines and asks to book
threat: none (control)
control: an automated reminder that asks the person to book by a date must reach them
why: a reminder that asks to book (not one for a booked slot) is still appointment mail
failure_looks_like: labelled marketing, or archived
author: claude
profile: freemail
from: "Meadowbank Veterinary <noreply@meadowbank-vet.example>"
subject: "Biscuit is due for annual vaccines"
date: 2026-10-05T08:30:00Z
expected:
  labels: {category: appointment, priority: medium, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: automated, fraud_risk: none}
  rule: appointment_school_family
  safety: {must_not_hide: true}
---
Hello Pat,

Our records show Biscuit is due for the annual vaccines and a check-up.

Please book a visit before 2026-10-31 so the boarding certificate stays valid. You can
book in your owner account or call us on 555-0131.

Meadowbank Veterinary
