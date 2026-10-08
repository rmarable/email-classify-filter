---
id: v2-acct-two-step-enabled
title: Shopping site confirms two-step verification was turned on
threat: none (control)
control: account_security, informational
why: varies the genuine security set past sign-ins and resets
failure_looks_like: labelled spam_or_phishing
author: claude
profile: freemail
from: "Maple Market Account Team <security@maple-market.example>"
subject: "Two-step verification is on"
date: 2026-10-11T15:30:00Z
expected:
  labels: {category: account_security, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: account_security
---
Hello Pat,

Two-step verification is now on for your Maple Market account. From now on we will send a code to your phone ending 0142 when you sign in on a new device.

You can change this in your account settings at any time.

Maple Market
