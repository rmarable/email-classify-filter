---
id: v2-ship-phish-customs-giftcard
title: Fake customs notice demands duty paid in gift cards
threat: customs-fee scam with gift-card payment
control: the gift-card payment keyword and the classifier's fraud_risk; must escalate
why: pairs with v2-ship-customs-duty-genuine; the unusual payment method is the giveaway
failure_looks_like: labelled shipping and kept as a normal duty request
author: claude
profile: freemail
from: "Customs Clearance Office <clearance-desk@parcel-customs.example>"
subject: "Parcel held at customs: payment required"
date: 2026-10-21T13:25:00Z
expected:
  labels: {category: spam_or_phishing, priority: high, requires_action: false, requires_reply: true, payment_related: true, deadline_mentioned: true, sender_type: unknown, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Dear recipient,

A parcel addressed to you is held at customs. A clearance fee of 95.00 must be paid before release.

Buy two store gift cards and reply to this email with the card numbers and PINs by Friday 23 October, or the parcel will be destroyed.

Customs Clearance Office
