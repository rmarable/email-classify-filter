---
id: v2-acct-phish-account-locked
title: Fake bank says the account is locked and asks to sign in through its form
threat: credential phishing, lookalike bank domain
control: the classifier's fraud_risk plus first-time lookalike sender; must escalate
why: pairs with v2-acct-new-signin-genuine; same bank name, lookalike domain, deadline pressure, credential request
failure_looks_like: labelled account_security with fraud_risk none, or archived
review: requires_action false (nothing legitimate to do); confirm
author: claude
profile: freemail
from: "Harbor Savings Security <secure-team@harbor-savings-verify.example>"
subject: "Account locked: verify within 24 hours"
date: 2026-10-04T05:50:00Z
expected:
  labels: {category: spam_or_phishing, priority: high, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: automated, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Dear customer,

We noticed unusual activity and have locked your online banking. To restore access, use the secure button below and enter your username, password and card PIN.

If you do not verify within 24 hours your account will be closed permanently.

Harbor Savings Security Department
