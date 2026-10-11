---
id: h2-code-customer-promo-not-working
title: Customer says a promo code is rejected at checkout
threat: none (benign control; customer mail that mentions a discount code)
control: credential-request reading must not treat a discount code as a sign-in code
why: A customer writes casually from a personal mailbox. The autumn discount code is rejected at checkout and they want it fixed before ordering. Authentication set to pass, because a large public mail provider publishes DMARC and this is the customer's real account. sender_seen_before is false because this is a first contact. Held-out batch 2 card; written without the plan, keyword lists or signals.
failure_looks_like: the email is labelled spam_or_phishing or account_security, or flagged with any fraud risk
review: category is sales_inquiry (they are trying to buy) unless the operator reads a rejected code as a billing_inquiry
author: claude
from: "Jordan Whitfield <jordan-w@freemail.example>"
to: [sales@acme.example]
subject: "discount code not working??"
date: 2026-10-08T19:47:00Z
expected:
  labels: {category: sales_inquiry, priority: medium, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: false, sender_type: customer, fraud_risk: none}
  facts: {auth_result: pass, sender_seen_before: false}
---
hi,

got your autumn email with the code AUTUMN15 for 15% off but checkout keeps saying "code not valid or expired". tried on my phone and my laptop, same thing both times. your email says it's good until the 31st so not sure what's going on

I'm trying to order 2 of the replacement filter cartridges (the FC-200 ones) plus the wall bracket, comes to about 186 before the discount. can you fix the code, or tell me if you can apply it if I order by phone instead?

would like to get the order in this week if possible

thanks
Jordan
