---
id: home-shop-giftcard-receipt
title: Bookshop receipt that includes a gift card bought as a present
threat: none (control)
control: gift-card words in an ordinary personal receipt must not escalate (OD-436 noise)
why: buying a gift card as a present is common; the gift-card keywords must not treat it as a scam
failure_looks_like: escalated as gift-card fraud, or hidden
review: gift-card terms count as money only for impersonation (OD-457); the receipt is payment-related from a first-time unsigned sender, so rule 1a flags it
author: claude
profile: freemail
from: "Corner Books Orders <orders@corner-books.example>"
subject: "Your receipt - order 4471"
date: 2026-10-08T12:20:00Z
bulk: true
expected:
  labels: {category: payment_confirmation, sender_type: automated, requires_action: false, requires_reply: false, payment_related: true, fraud_risk: none}
  rule: unverified_payment_sender
  safety: {must_not_hide: true}
---
Thanks for shopping at Corner Books.

Order 4471, picked up in store on 2026-10-08.

1 x Field Guide to Garden Birds 18.50
1 x gift card 25.00 (wrapped, with a card for a birthday)

Total 43.50, paid by card ending 0002.

Keep this receipt for returns within 30 days.
