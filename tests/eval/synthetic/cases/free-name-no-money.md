---
id: free-name-no-money
title: A listed friend's name on another address, nothing about money
threat: impersonation of someone in org_addresses without a request for money (often the first message of a longer scam)
control: rule 1b, fraud_weak (impersonates_internal without money; OD-436)
why: a listed name on an unlisted address is worth a flag even when nothing is asked yet
failure_looks_like: the item is labelled routine with no flag, or hidden
review: fraud_risk low is a judgement call; at medium the rule becomes fraud_guard
author: claude
profile: freemail
from: "Sam Rivera <sam-rivera-photos@freemail.example>"
subject: "Photos from Saturday"
date: 2026-10-03T19:05:00Z
expected:
  labels: {category: other, priority: low, requires_action: false, requires_reply: false, payment_related: false, fraud_risk: low, sender_type: unknown}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Hi Pat,

Sending these from my other account because the photos are on this laptop. The ones from
the lake came out well, the ones from the trail less so. More to follow when I find the
cable for the camera.

Sam
