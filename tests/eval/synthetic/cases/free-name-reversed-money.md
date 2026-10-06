---
id: free-name-reversed-money
title: A listed friend's name, written surname first, asks for an emergency transfer
threat: impersonation of someone in org_addresses on a personal account ("stranded abroad")
control: fraud trigger 7, impersonation by listed name (every word, any order; OD-433) with a payment keyword; fraud trigger 1 (bank details from an unconfirmed sender)
why: the name match must not depend on word order or punctuation
failure_looks_like: the item is labelled personal or hidden, no escalation
author: claude
profile: freemail
from: "\"Rivera, Sam\" <sam-rivera-abroad@freemail.example>"
subject: "Stuck abroad, need help today"
date: 2026-10-03T06:40:00Z
expected:
  labels: {category: spam_or_phishing, payment_related: true, fraud_risk: high, sender_type: unknown}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Pat, I'm so sorry to ask. My wallet and phone were stolen at the station and I can't get
home until Monday. Could you send a payment of 650 by bank transfer to the account below?
It's a friend's account here, I'll pay you back as soon as I'm home.

Account: published example IBAN GB82 WEST 1234 5698 7654 32

I'm writing from a borrowed laptop, so this address is the only way to reach me for now.
Sam
