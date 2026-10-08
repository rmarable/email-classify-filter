---
id: v2-fin-insurance-policy-renewed
title: Insurer sends renewed home policy documents, no payment due
threat: none (control)
control: an insurance policy notice with no amount due is finance, not invoice
why: renewal mail is often mistaken for a bill; this one says nothing is owed
failure_looks_like: labelled invoice or payment_related, or archived as marketing
author: claude
profile: freemail
from: "Northfield Mutual Insurance <policy@northfield-mutual.example>"
subject: "Your home policy has renewed"
date: 2026-10-03T09:20:00Z
expected:
  labels: {category: finance, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: fraud_weak
---
Hello Pat,

Your home insurance policy ending 0007 has renewed for another year. Your cover and
deductible are unchanged.

The new declarations page is in your online account. Please keep it with your records.
Nothing is due now; your payment plan continues as before.

Northfield Mutual Insurance
