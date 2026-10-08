---
id: v2-family-friend-lunchbox
title: Friend says she'll drop off a forgotten lunchbox
threat: none (control)
control: a short friend note with nothing to do must not be flagged or hidden
why: low-priority family logistics from a friend
failure_looks_like: requires_reply set, flagged, or hidden
author: claude
profile: freemail
from: "Theo Marsh <theo-marsh@freemail.example>"
subject: "Leo's lunchbox"
date: 2026-10-06T16:50:00Z
expected:
  labels: {category: school_or_family, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: friend, fraud_risk: none}
  rule: appointment_school_family
---
Hi Pat,

Leo left his lunchbox in our car after the playdate. I'll leave it on your porch on
my way past tomorrow morning.

No need to reply.
Theo
