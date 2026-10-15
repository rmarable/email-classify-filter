---
id: h3-priv-friend-pays-back-tickets
title: Friend says they've transferred their share of concert tickets
profile: freemail
threat: none (benign private money; weak-signal rule by design)
control: fraud_weak (private individual from outside about money)
why: A friend repays Pat for tickets. Nothing is asked, but a private person writing about money from outside is routed to fraud_weak by design (OD-476). Held-out batch 3 card.
failure_looks_like: escalated as fraud, or called phishing
author: claude
from: "Ezra Quill <ezra-quill@freemail.example>"
subject: "Sent you the money for the tickets"
date: 2026-10-04T22:02:00Z
expected:
  labels: {category: remittance, priority: low, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: friend, fraud_risk: none, bank_detail_change: false, regulator_claim: false, phishing: false, claims_internal: false, private_individual: true}
  facts: {auth_result: pass, sender_seen_before: true}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Hey Pat,

Thanks again for grabbing the tickets before they sold out. I just sent you 85 for mine and Lou's, from my usual account, so it should show up tomorrow. I put "concert" as the reference.

If it doesn't arrive, tell me and I'll chase my bank, but it said it went through.

Can't wait for the show. Doors are at 7, so maybe meet at the noodle place beforehand around 5:30? No need to reply if that's still the plan.

E
