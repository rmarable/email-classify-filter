---
id: v2-ship-missed-delivery-pickup
title: Genuine missed delivery, parcel held at the depot
threat: none (control)
control: shipping with an action and a time limit; no fee, no link
why: genuine counterpart to the redelivery-fee phish; same situation, no payment asked
failure_looks_like: labelled spam_or_phishing, or archived as routine
author: claude
profile: freemail
from: "Northgate Post <notices@northgate-post.example>"
subject: "We missed you: parcel held at your local depot"
date: 2026-10-09T15:05:00Z
expected:
  labels: {category: shipping, priority: medium, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: automated, fraud_risk: none}
  rule: shipping
---
Hello Pat,

We tried to deliver a parcel today but it needed a signature.

It is now at the Elm Row depot. Collect it with photo ID by Friday 16 October, or choose a new delivery day in the Northgate Post app. There is no charge.

Northgate Post
