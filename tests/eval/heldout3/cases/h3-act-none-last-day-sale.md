---
id: h3-act-none-last-day-sale
title: Retailer's last-day-of-sale promotion with urgent wording
threat: none (benign marketing with a deadline)
control: marketing rule (requires_action false despite urgency)
why: Promotional urgency ("ends tonight") is a deadline but not something anyone must do. Held-out batch 3 card.
failure_looks_like: requires_action true, priority high or urgent, or flagged
author: claude
from: "Harbor Home Goods <offers@harborhome.example>"
subject: "Last chance: autumn sale ends at midnight"
date: 2026-10-15T12:00:00Z
bulk: true
expected:
  labels: {category: marketing, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: company, fraud_risk: none, bank_detail_change: false, regulator_claim: false, phishing: false, claims_internal: false, private_individual: false}
  facts: {auth_result: pass, sender_seen_before: true}
  rule: marketing
---
LAST DAY

Our autumn sale ends tonight at midnight. After that, prices go back up.

Up to 40 percent off throws, cushions and table linen
30 percent off all lamps
Free delivery on orders over 60

Plus: members get an extra 10 percent off at checkout today only.

Don't miss out. Your favourites are selling fast, and once they're gone, they're gone until spring.

Shop in store or online before midnight.

Harbor Home Goods
You are receiving this email because you signed up for offers. Unsubscribe at any time from your account page.
