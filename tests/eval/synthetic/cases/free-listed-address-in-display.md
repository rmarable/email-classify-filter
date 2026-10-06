---
id: free-listed-address-in-display
title: A listed address shown as the display name, sent from another address, asks for money
threat: impersonation by writing a listed address into the display name
control: fraud trigger 7, impersonation (an internal address in the display name; OD-433) with a payment keyword
why: many mail apps show only the display name, so the reader sees the friend's real address
failure_looks_like: the item is treated as mail from the listed friend and not escalated
author: claude
profile: freemail
from: "\"sam-rivera@freemail.example\" <sr-cabin-share@freemail.example>"
subject: "Your share of the cabin"
date: 2026-10-04T10:15:00Z
expected:
  labels: {category: spam_or_phishing, payment_related: true, fraud_risk: high, sender_type: unknown, deadline_mentioned: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hi Pat,

The owner wants the rest of the cabin rental by Friday. Your share is 220. Please send the
payment by bank transfer to the account I sent last week, not the old one.

Thanks,
Sam
