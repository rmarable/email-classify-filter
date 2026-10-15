---
id: h3-act-none-sales-tax-return-accepted
title: "Accountant's client portal says the quarterly sales-tax return was filed and accepted"
threat: none (benign finance notice that needs nothing)
control: travel_finance rule; requires_action false; regulator_claim false (the portal reports a filing, no agency writes to us)
why: "The accountant's portal confirms a filing it made for us went through. It asks for nothing, names no amount and points to the usual portal. Held-out batch 3 card."
review: "Judgement call: category finance (a tax filing on an account we hold); notification is defensible, and with it the rule would be otherwise."
failure_looks_like: "requires_action true, regulator_claim true, or escalated"
author: claude
from: "Beck & Ward Client Portal <portal@beckward-cpa.example>"
subject: "Filed and accepted: sales and use tax return, Q3 2026"
date: 2026-10-20T16:40:00Z
expected:
  labels: {category: finance, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: vendor, fraud_risk: none, bank_detail_change: false, regulator_claim: false, phishing: false, claims_internal: false, private_individual: false}
  facts: {auth_result: pass, sender_seen_before: true}
  rule: travel_finance
---
Hello ACME Corp,

Your quarterly return has been filed and accepted by the state.

Return: sales and use tax, July to September 2026
Filed by: Beck & Ward Accountants, 19 October 2026
Status: accepted, 20 October 2026
State confirmation reference: SUT-Q3-88214

A copy of the filed return and the state's acceptance receipt are in the Documents section of your client portal, under Tax filings > 2026.

There is nothing you need to do. Your next return, for October to December, is on our calendar and we will send the usual figures request in early January.

Beck & Ward Client Portal
This is an automated message from your accountant's portal. Questions go to your usual contact at Beck & Ward on 555-0151.
