---
id: h3-bank-near-details-unchanged
title: "Supplier reminds customers that the account it is paid into has not moved"
threat: "none (near miss: restates the payee account without changing it)"
control: "bank_detail_change false and no fraud risk on an anti-fraud reminder that prints an unchanged account"
why: "A known supplier's yearly reminder that its account is the same as always, printed in full, and that any message saying otherwise should be checked by phone. It changes nothing and asks nothing, and uses no listed bank, change or payment phrase, so no trigger fires and the model decides. It is also this batch's benign card with a full account number, written the same way as on the fraud cards. Held-out batch 3 card."
failure_looks_like: "bank_detail_change true, fraud risk given, or escalated"
author: claude
from: "Vendor G Credit Control <credit@vendor-g.example>"
subject: "Annual reminder: where to pay us has not changed"
date: 2026-10-05T08:30:00Z
expected:
  labels: {category: notification, priority: low, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: vendor, fraud_risk: none, bank_detail_change: false, regulator_claim: false, phishing: false, claims_internal: false, private_individual: false}
  facts: {auth_result: pass, sender_seen_before: true}
  rule: otherwise
---
Dear Customer,

Fraud aimed at suppliers' customers is on the rise in our sector, so once a year we send this short reminder.

The account we are paid into is the same as it has always been, and we have no plans to move it:

Account name: Vendor G Ltd
Account: 00517-TEST-30
Held at: Thornbury Savings

It is printed on every bill we send you and matches the account agreement you signed with us.

If you ever receive an email, letter or call, apparently from us, saying we are now paid somewhere else, please do not act on it. Call your usual contact on 555-0163, using the number you already hold rather than one in the message, and we will confirm.

There is nothing you need to do in response to this email.

Kind regards,
Credit Control Team
Vendor G
