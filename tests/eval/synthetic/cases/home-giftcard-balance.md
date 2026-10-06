---
id: home-giftcard-balance
title: Cafe sends a gift card balance after a visit
threat: none (control)
control: a gift card balance notice must not be escalated as gift-card fraud (OD-436 noise)
why: stores send balance notices after each use; the only money word is "gift card"
failure_looks_like: escalated as gift-card fraud
review: gift-card terms count as money only for impersonation (OD-457), so this is an ordinary notification
author: claude
profile: freemail
from: "Bean Street Cafe <rewards@beanstreet-cafe.example>"
subject: "Thanks for visiting - your gift card balance"
date: 2026-10-19T08:15:00Z
expected:
  labels: {category: notification, sender_type: automated, requires_action: false, requires_reply: false, payment_related: false, fraud_risk: none}
  rule: automated_notification
---
Thanks for stopping by Bean Street Cafe this morning.

Your gift card ending 0004 was used for 4.60. Remaining balance: 12.40.

See you soon.
