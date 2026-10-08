---
id: v2-acct-password-reset-requested
title: Store sends the password reset Pat asked for
threat: none (control)
control: account_security with a short time limit; no fraud signal
why: a real reset mail has urgency wording ("expires in 30 minutes") that must not read as fraud pressure
failure_looks_like: labelled spam_or_phishing or fraud_risk raised because of the time limit
author: claude
profile: freemail
from: "Lantern Outfitters <no-reply@lantern-outfitters.example>"
subject: "Reset your password"
date: 2026-10-06T08:41:00Z
expected:
  labels: {category: account_security, priority: medium, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: automated, fraud_risk: none}
  rule: account_security
---
We received a request to reset the password for your Lantern Outfitters account.

Use the reset button in this email within 30 minutes to choose a new password.

If you did not ask for this, ignore this message and your password stays the same.

Lantern Outfitters
