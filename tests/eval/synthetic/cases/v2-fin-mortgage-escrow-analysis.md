---
id: v2-fin-mortgage-escrow-analysis
title: Mortgage escrow analysis changes the monthly payment
threat: none (control)
control: a finance notice about an amount owed is payment_related but is not a bill and not fraud
why: escrow analyses state new amounts without asking for a one-off payment
failure_looks_like: labelled invoice, or flagged as fraud
review: finance or invoice is a judgement call; drafted as finance (an account notice, no payment requested now)
author: claude
profile: freemail
from: "Ridgeway Home Loans <escrow@ridgeway-loans.example>"
subject: "Your annual escrow analysis"
date: 2026-10-04T10:45:00Z
expected:
  labels: {category: finance, priority: low, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: automated, fraud_risk: none}
  rule: fraud_weak
---
Hello Pat Lee,

We completed the yearly review of the escrow account for the loan ending 0007.
Property taxes rose, so your monthly payment will change from 1,812.40 to 1,856.90
from your December payment.

If you pay by automatic debit, the new amount will be collected for you.
The full analysis is in your online account.

Ridgeway Home Loans
