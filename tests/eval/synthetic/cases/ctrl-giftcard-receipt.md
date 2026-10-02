---
id: ctrl-giftcard-receipt
title: Retailer receipt for gift cards ACME bought
threat: none (control)
control: gift-card words in a receipt must not escalate
why: the gift-card fraud cases need a legitimate counterpart
failure_looks_like: escalated as gift-card fraud
from: "Store Receipts <receipts@office-store.example>"
subject: "Your receipt - order 30018"
date: 2026-10-20T15:00:00Z
bulk: true
author: claude
expected:
  labels: {category: payment_confirmation, sender_type: automated, fraud_risk: none}
  rule: unverified_payment_sender
---
Thank you for your order 30018.

3 x gift card 50.00 (team prizes)
Total 150.00, paid by company card ending 0001.
