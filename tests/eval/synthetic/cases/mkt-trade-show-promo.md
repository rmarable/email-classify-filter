---
id: mkt-trade-show-promo
title: Trade-show organiser promotes early-bird tickets
threat: none
control: rule 7
why: event promotion with a date and a price
failure_looks_like: payment_related true or flagged
from: "Expo Organisers <tickets@support-expo.example>"
subject: "Early-bird tickets end Friday"
date: 2026-11-11T07:00:00Z
bulk: true
author: claude
expected:
  labels: {category: marketing, payment_related: false, requires_action: false}
  rule: marketing
---
Early-bird tickets for the spring support expo end this Friday. Save 30% on full passes and
meet 120 exhibitors.

Expo Organisers
