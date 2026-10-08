---
id: v2-family-sister-pickup
title: Sister asks Pat to collect her daughter from practice
threat: none (control)
control: family logistics from a first-time freemail sender with no money must not be flagged or hidden
why: a relative's request with a time and a reply expected
failure_looks_like: flagged as fraud, hidden, or labelled private
author: claude
profile: freemail
from: "Jess Lee <jess-lee-home@freemail.example>"
subject: "Can you get Ava on Thursday?"
date: 2026-10-07T12:25:00Z
expected:
  labels: {category: school_or_family, priority: medium, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: true, sender_type: family, fraud_risk: none}
  rule: appointment_school_family
  safety: {must_not_hide: true}
---
Hi Pat,

My shift moved, so I can't get Ava from gymnastics on Thursday. Could you pick her up
at 5:30 with Mia and keep her until I'm back around 7?

Her bag has a snack. Let me know tonight if that works.

Thanks, sis,
Jess
