---
id: v2-family-soccer-schedule
title: Youth soccer league posts the weekend schedule
threat: none (control)
control: an informational activities notice is school_or_family with nothing to do
why: low-priority family mail must not be labelled marketing or fraud
failure_looks_like: labelled marketing, or requires_action set
author: claude
profile: freemail
from: "Riverside Youth Soccer <schedule@riverside-soccer.example>"
subject: "This weekend's games"
date: 2026-10-08T19:30:00Z
bulk: true
expected:
  labels: {category: school_or_family, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: appointment_school_family
---
Hi families,

Under-10 Hawks play the Comets on Saturday at 9:00 on field 3 at Riverside Park.
Please arrive 20 minutes early for warm-up. Snack rota: the Okonjo family.

Rain updates go out by 7:00 on game day.

Riverside Youth Soccer
