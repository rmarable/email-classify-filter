---
id: free-gift-card-unlisted
title: Someone claiming to be a manager, not a listed name, asks for gift cards
threat: gift-card scam from an unknown "manager" on a personal account
control: payment keyword "gift cards" (OD-436), a first-time sender with a payment keyword (fraud_weak trigger); rule 1 (staff from outside about money, OD-262)
why: without a listed name only the keyword and the classifier see it; the keyword must at least flag it
failure_looks_like: the item is labelled routine with no flag, or hidden
review: the escalation rests on the expected labels (sender_type staff, fraud_risk high); the triggers alone give fraud_weak
author: claude
profile: freemail
from: "Casey Quill <casey-quill-manager@freemail.example>"
subject: "Need this done today"
date: 2026-10-05T08:45:00Z
expected:
  labels: {category: spam_or_phishing, payment_related: true, fraud_risk: high, sender_type: team, deadline_mentioned: true, requires_reply: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello Pat, it's Casey from the regional office, I'm your new manager from Monday.

I need four gift cards of 200 each for staff awards today. Buy them on your way in and send
the card numbers to me by email. You'll be reimbursed with your next pay.

Casey
