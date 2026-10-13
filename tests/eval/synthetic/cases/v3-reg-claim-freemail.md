---
id: v3-reg-claim-freemail
title: "Claimed tax authority writing from a personal address"
threat: "regulator impersonation"
control: "fraud_guard (fraud_risk high: a tax authority does not write from a personal address and demand payment)"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "labelled regulatory only, or routine"
author: claude
from: "Tax Office Enforcement <tax-office-enforcement@freemail.example>"
subject: "Final notice: unpaid tax penalty"
date: 2026-10-07T08:15:00Z
expected:
  labels: {category: spam_or_phishing, sender_type: unknown, priority: low, requires_action: true, requires_reply: true, payment_related: true, deadline_mentioned: true, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
This is the tax authority. Your business owes a penalty of USD 2,300.00. Pay today by gift cards or wire to avoid legal action. Reply with your payment confirmation.
