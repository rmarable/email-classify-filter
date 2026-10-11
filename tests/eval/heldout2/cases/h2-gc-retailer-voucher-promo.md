---
id: h2-gc-retailer-voucher-promo
title: Genuine office-supply retailer promo with a voucher code
threat: "none (benign look-alike: voucher and bonus-card wording in marketing)"
control: false-positive control for voucher wording in bulk marketing
why: >-
  An office-supply store Acme buys from sends a seasonal promotion with a discount voucher and
  a "bonus e-gift card with large orders" offer. Authentication set to pass with
  sender_seen_before true: genuine bulk mail from the retailer's own domain.
  Held-out batch 2 card; written without the plan, keyword lists or signals.
failure_looks_like: ordinary marketing is escalated as a voucher or gift-card scam
review: >-
  no expected rule given; compute it with ecf rules test. sender_type vendor assumes Acme buys
  from this store; company if not.
author: claude
from: "Vendor F Office Supply <deals@news.vendor-f.example>"
to: [ap@acme.example]
subject: "Stock up for Q4: 15% off + a $25 bonus e-gift card on orders over $300"
date: 2026-10-01T12:30:00Z
bulk: true
expected:
  labels: {category: marketing, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, sender_type: vendor, fraud_risk: none}
  facts: {auth_result: pass, sender_seen_before: true}
---
VENDOR F OFFICE SUPPLY - Business Rewards

Q4 is coming. Get your supply room ready.

15% OFF printer paper, toner and breakroom essentials
Use voucher code STOCKUP15 at checkout. Valid on business accounts until October 31.

PLUS: spend $300 or more in one order and we'll add a $25 bonus e-gift card to your account, usable on your next purchase. It appears automatically under My Account > Rewards within 3 days of shipping. No code needed.

Top picks this week
  - Multipurpose copy paper, 10-ream case - from $42.99
  - High-yield toner, assorted models - save up to 20%
  - Coffee pods, 96 count - $38.49

Free next-day delivery on orders over $75 for business account holders.

Terms: one voucher per order; cannot be combined with contract pricing. Bonus e-gift card has no cash value and expires 90 days after issue.

You are receiving this because ap@acme.example is registered for Vendor F business offers. Unsubscribe or manage preferences in your account settings.
Vendor F Office Supply, 12 Supply Park Drive
