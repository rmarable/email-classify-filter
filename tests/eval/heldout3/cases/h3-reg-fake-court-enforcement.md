---
id: h3-reg-fake-court-enforcement
title: "Fake court enforcement office demands a fine by transfer within 24 hours"
threat: "government impersonation extortion (fake fine, threat of bailiffs)"
control: "fraud_guard (fraud_risk high); regulator_claim must still be true"
why: "Low-credibility claim of acting for a court: free-mail reply address, a transfer to a named individual's account, a 24-hour deadline and a threat of seizure. The fraud rule should win; the regulator item still records the claim. Scam: requires_reply false; requires_action false, since nobody should act on it and ecf's escalation brings it to a person. Held-out batch 3 card."
failure_looks_like: "labelled regulatory only and treated as genuine, or hidden as junk without escalation"
author: claude
from: "County Court Enforcement Office <enforcement@court-notices.example>"
reply_to: "enforcement-desk@freemail.example"
subject: "FINAL ENFORCEMENT NOTICE - unpaid penalty, seizure scheduled"
date: 2026-10-11T06:55:00Z
expected:
  labels: {category: invoice, priority: urgent, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: true, sender_type: regulator, fraud_risk: high, bank_detail_change: false, regulator_claim: true, phishing: true, claims_internal: false, private_individual: false}
  facts: {auth_result: pass, sender_seen_before: false, reply_to_mismatch: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
FINAL NOTICE BEFORE ENFORCEMENT

Case: CE-2026-TEST-0718
Debtor: ACME Corp

A penalty of 2,480.00 imposed on your business for late filing remains unpaid. All previous letters have been ignored.

An enforcement agent has been instructed to attend your business premises within 24 hours to remove goods to the value of the debt plus costs. This can only be stopped by immediate payment.

To settle, transfer 2,480.00 today to the enforcement officer's client account:
Payee: Officer M Grant
Account: 00731-TEST-29

Reply to this email with proof of payment and the case number. Do not contact the court directly as this will delay the cancellation of the visit.

Enforcement Section
County Court
