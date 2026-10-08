---
id: v2-fin-brokerage-tax-form
title: Brokerage says the annual tax form is ready
threat: none (control)
control: an automated tax-form notice with no money request and no link must not escalate or be hidden
why: brokerage tax forms are routine finance mail on a personal account; "tax" alone is not fraud
failure_looks_like: escalated as fraud, labelled regulatory, or archived as marketing
author: claude
profile: freemail
from: "Bayline Brokerage <statements@bayline-invest.example>"
subject: "Your consolidated tax form is available"
date: 2026-10-02T06:15:00Z
expected:
  labels: {category: finance, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: travel_finance
---
Hello Pat Lee,

The consolidated tax form for your brokerage account ending 0007 is now available.
Sign in to your account as you usually do to view or download it.

If we receive corrected information from a fund company, we will send a revised form.

Bayline Brokerage
