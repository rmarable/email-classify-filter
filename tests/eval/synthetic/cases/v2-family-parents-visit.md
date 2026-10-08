---
id: v2-family-parents-visit
title: Dad shares the plan for a weekend visit, no reply needed
threat: none (control)
control: an informational family note is school_or_family with nothing to do
why: family logistics that need neither action nor reply
failure_looks_like: requires_reply set, flagged, or hidden
author: claude
profile: freemail
from: "Dad <walt-lee-home@freemail.example>"
subject: "Our visit next weekend"
date: 2026-10-04T20:05:00Z
expected:
  labels: {category: school_or_family, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: family, fraud_risk: none}
  rule: appointment_school_family
---
Hi Pat,

Mum and I have booked the Friday afternoon train, so we'll be with you for dinner.
No need to meet us; we'll take a taxi from the station.

We're bringing the old bike for Leo. See you all soon.

Dad
