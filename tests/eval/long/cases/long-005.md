---
id: long-005
title: "Long card long-005"
threat: "renewal-signin: scam ask early (control)"
control: "the classifier's fraud_risk on the body alone (rules alone don't reach fraud_guard)"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits early; confirm the labels fit the whole email"
author: claude
profile: org
from: "Ledgerline Accounts Software <accounts@ledgerline-software.example>"
subject: "Ledgerline: your autumn account update"
date: 2026-10-20T09:00:00Z
signal: "sign in at billing-desk-renewal.example with your current details"
target_chars: 1800
pad_voice: vendor
signature: |
  Customer Team
  Ledgerline
expected:
  labels: {category: account_security, priority: high, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: high, phishing: true, private_individual: false}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello from the Ledgerline customer team. Here is your autumn account update.

Before Thursday, sign in at billing-desk-renewal.example with your current details so your licence carries on; the renewal has moved to a partner site. There is no need to involve your account manager, who isn't handling renewals this year.

{{pad}}

Thanks for choosing Ledgerline.
