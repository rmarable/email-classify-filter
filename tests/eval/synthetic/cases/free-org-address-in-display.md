---
id: free-org-address-in-display
title: An org address shown as the display name, sent from a personal address
threat: executive impersonation by writing the internal address into the display name
control: fraud trigger 7 (display name shows another domain; impersonation by an internal address in the display name, OD-433)
why: many mail apps show only the display name, so AP sees the CEO's real address
failure_looks_like: the item is treated as internal mail and not escalated
author: claude
from: "\"dana-chief@acme.example\" <exec-desk-dc@freemail.example>"
subject: "Staff directory"
date: 2026-10-05T10:05:00Z
expected:
  labels: {category: spam_or_phishing, payment_related: false, fraud_risk: high, sender_type: staff, requires_reply: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Please send me the current staff directory with home addresses and mobile numbers. I need
it for the offsite planning. Reply to this message.

Dana
