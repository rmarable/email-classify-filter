---
id: v2-acct-new-signin-genuine
title: Bank reports a sign-in from a new device, nothing asked
threat: none (control)
control: the category account_security on a routine alert; no fraud signal
why: genuine counterpart to the fake sign-in alerts; it has no link, no code request and no deadline
failure_looks_like: labelled spam_or_phishing or escalated as fraud
review: no must_not_hide set because the alert asks for nothing if it was you; confirm
author: claude
profile: freemail
from: "Harbor Savings <alerts@harbor-savings.example>"
subject: "New sign-in to your Harbor Savings account"
date: 2026-10-03T19:12:00Z
expected:
  labels: {category: account_security, priority: medium, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: account_security
---
Hello Pat,

Someone signed in to your online banking from a new device on 3 October at 19:08.

If this was you, there is nothing to do. If it was not, sign in as you usually do and change your password, or call the number on the back of your card.

We will never ask for your password or a code by email.

Harbor Savings
