---
id: h3-priv-stranger-found-wallet
title: Stranger found Pat's wallet and wants to return it
profile: freemail
threat: none (benign mail from someone unknown)
control: private_individual true, sender_type person, no fraud
why: An unknown person writes kindly about returning a lost wallet; they ask for no money, details or codes, only a time to meet in a public place. Held-out batch 3 card.
failure_looks_like: given medium or high fraud risk, or archived
author: claude
from: "Hollis Brandt <hollis-brandt@freemail.example>"
subject: "Found your wallet at the cafe on Birch Street"
date: 2026-10-11T12:20:00Z
expected:
  labels: {category: private, priority: medium, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: false, sender_type: person, fraud_risk: none, bank_detail_change: false, regulator_claim: false, phishing: false, claims_internal: false, private_individual: true}
  facts: {auth_result: pass, sender_seen_before: false}
  rule: requires_reply
---
Hello,

I hope this is the right person. I found a brown leather wallet under a table at the cafe on Birch Street this morning. Your library card was inside with this email address written on the back, which is how I'm reaching you.

I haven't gone through it beyond that, but I could see cards and some cash, so I didn't want to leave it with the counter staff when it was so busy.

I'm around the cafe most weekday mornings. If you tell me a day and time that works, I'm happy to meet you there and hand it over. Or if you'd rather, I can drop it at the police station on Grove Road, just say.

Best wishes,
Hollis
