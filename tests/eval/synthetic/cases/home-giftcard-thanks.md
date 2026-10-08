---
id: home-giftcard-thanks
title: A niece thanks the account holder for a birthday gift card
threat: none (control)
control: a family thank-you that mentions a gift card must not be treated as a gift-card request (OD-436 noise)
why: gift cards are common presents; the scam asks for codes, a thank-you does not
failure_looks_like: escalated as gift-card fraud, or hidden
review: gift-card terms count as money only for impersonation (OD-457), so this is ordinary personal mail
author: claude
profile: freemail
from: "Tess <tess-k@freemail.example>"
subject: "Thank you!!"
date: 2026-10-20T16:00:00Z
expected:
  labels: {category: private, sender_type: family, requires_action: false, requires_reply: false, payment_related: false, fraud_risk: none}
  rule: otherwise
  safety: {must_not_hide: true}
---
Hi Aunt Pat,

Thank you so much for the birthday card and the bookshop gift card! I already picked out
two books for the holidays. Mum says hi.

Love,
Tess
