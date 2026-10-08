---
id: v2-acct-phish-keep-access
title: Fake streaming notice says to confirm the account or lose access
threat: link-push phishing without an explicit credential or payment ask
control: the classifier's fraud_risk on a lookalike sender; must stay visible
why: the softest phish in the set; only the lookalike domain and pressure give it away
failure_looks_like: labelled account_security and archived
review: asks only to "confirm your account" with a button and no explicit credential or payment ask, so must_not_hide only; confirm
author: claude
profile: freemail
from: "Riverbend Stream <account-notice@riverbend-streaming-help.example>"
subject: "Action needed to keep your account"
date: 2026-10-20T14:00:00Z
expected:
  labels: {category: spam_or_phishing, priority: medium, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: automated, fraud_risk: medium}
  rule: fraud_guard
  safety: {must_not_hide: true}
---
Hi,

We could not verify your account details during our routine security check.

Use the "Confirm my account" button below within 48 hours to keep watching without interruption.

The Riverbend Stream Team
