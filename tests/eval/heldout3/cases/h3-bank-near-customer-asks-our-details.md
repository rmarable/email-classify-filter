---
id: h3-bank-near-customer-asks-our-details
title: "Customer asks us to confirm the account it pays us into before its first transfer from a new system"
threat: "none (near miss: our payee account discussed, nothing changed)"
control: "bank_detail_change must stay false when a customer asks to verify the account they already pay us into"
why: "A customer migrating to a new finance system wants to re-verify the account it already pays. Money is coming to us and no account is changed. Written without any listed bank, change or payment phrase, so no trigger fires and the model decides. Held-out batch 3 card."
review: "Judgement call: category billing_inquiry; remittance is defensible (money is coming to us), and with it the rule would be payment_confirmation_remittance."
failure_looks_like: "bank_detail_change true, fraud risk given, or escalated as fraud"
author: claude
from: "Ines Barlow <ap-team@northwind-retail.example>"
subject: "Confirming where we pay you - new AP system"
date: 2026-10-12T11:47:00Z
expected:
  labels: {category: billing_inquiry, priority: medium, requires_action: true, requires_reply: true, payment_related: true, deadline_mentioned: false, sender_type: customer, fraud_risk: none, bank_detail_change: false, regulator_claim: false, phishing: false, claims_internal: false, private_individual: false}
  facts: {auth_result: pass, sender_seen_before: true}
  rule: requires_reply
---
Hello ACME accounts,

We are moving our accounts payable to a new finance system this month. As part of the move, our controls require us to re-verify with every supplier the account we pay them into before the first transfer from the new system.

We hold your payee account as ending 4417, in the name of ACME Corp. Nothing on our side suggests this has changed; we just need written confirmation.

Could you reply confirming that the account ending 4417 is still correct? Please don't send the full number by email. If anything is different, we will arrange a call with your finance team instead.

Our next transfer to you, for bills 2281 and 2290, will go out once this is confirmed.

Thanks,
Ines Barlow
Accounts Payable, Northwind Retail
