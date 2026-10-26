---
id: long-020
title: "Long card long-020"
threat: "relative-reship: scam ask early (control)"
control: "the classifier's fraud_risk on the body alone (rules alone don't reach fraud_guard)"
why: "window tuning step 4 (plan draft 8)"
failure_looks_like: "fraud_risk none, not escalated"
review: "a long email whose scam ask sits early; confirm the labels fit the whole email"
author: claude
profile: freemail
from: "Alex Reyes <alex-reyes@freemail.example>"
subject: "News from our side"
date: 2026-10-20T09:00:00Z
signal: "parcels will arrive addressed to you under a business name"
target_chars: 1775
pad_voice: personal
signature: |
  Alex
expected:
  labels: {category: private, priority: high, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: friend, fraud_risk: high, phishing: true, private_individual: true}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Hello, it's been far too long, so here is all our news in one go.

Could you do me a favour? Some parcels will arrive addressed to you under a business name. Please don't open them; just repost them to the overseas address I'll give you, and I'll cover your costs plus a thank-you for each.

{{pad}}

Take care of yourselves.
