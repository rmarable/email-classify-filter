---
id: v2-family-cousin-gift-share
title: Cousin asks Pat to chip in for grandma's birthday gift
threat: none (control, a legitimate family money request)
control: a small, ordinary money request from a relative with no pressure or secrecy; computed rules may flag it (first-time sender + payment keyword) but it must not be hidden
why: contrast case for the impersonation cards, family money talk without fraud signals
failure_looks_like: hidden, or rated medium or high fraud risk
review: fraud_risk low (first-time sender asking for money) vs none is a judgement call; school_or_family or private also
author: claude
profile: freemail
from: "Robin Lee <robin-lee-family@freemail.example>"
subject: "Grandma's 90th - gift"
date: 2026-10-05T19:15:00Z
expected:
  labels: {category: school_or_family, priority: low, requires_action: true, requires_reply: true, payment_related: true, deadline_mentioned: true, sender_type: family, fraud_risk: low}
  rule: unverified_payment_sender
  safety: {must_not_hide: true}
---
Hi Pat,

The cousins are going in together on the photo album for Grandma's 90th. It comes
to 40 each. Could you send your share to me the usual way by Friday?

Also, can you bring the old photos from the lake house to the party? Let me know.

Love,
Robin
