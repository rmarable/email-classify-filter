---
id: v2-acct-phish-mailbox-expiry
title: Fake mail provider says the password expires today
threat: credential phishing posing as the mailbox provider
control: the classifier's fraud_risk on a lookalike provider domain
why: pairs with v2-acct-unrecognised-device-review; same provider name, but it asks for the current password
failure_looks_like: labelled account_security and kept as routine
author: claude
profile: freemail
from: "Freemail Account Support <support@freemail-accounts.example>"
subject: "Your password expires today"
date: 2026-10-13T07:05:00Z
expected:
  labels: {category: spam_or_phishing, priority: high, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: automated, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Dear user,

Your mailbox password expires today. To keep your current password and avoid losing your messages, confirm it on the form linked in this email before midnight.

Accounts not confirmed today will be suspended.

Freemail Account Support
