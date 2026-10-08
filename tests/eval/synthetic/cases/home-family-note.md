---
id: home-family-note
title: A parent asks about Sunday dinner
threat: none (control)
control: personal mail from a first-time freemail sender with no money must not be flagged or hidden
why: most mail on a personal account is notes like this; no fraud signal should fire
failure_looks_like: labelled suspicious, flagged as fraud, or archived
author: claude
profile: freemail
from: "Mom <robin-home@freemail.example>"
subject: "Sunday dinner?"
date: 2026-10-06T18:10:00Z
expected:
  labels: {category: private, sender_type: family, requires_reply: true, payment_related: false, fraud_risk: none}
  rule: requires_reply
  safety: {must_not_hide: true}
---
Hi love,

Are you still coming on Sunday? Dad is making the lasagne. Could you bring a salad or
dessert, whichever is easier? Let me know by Friday so I know how much to make.

Love,
Mom
