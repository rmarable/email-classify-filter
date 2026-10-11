---
id: h2-gc-corporate-egift-order-codes
title: Genuine corporate e-gift order confirmation delivering codes
threat: "none (benign look-alike: gift-card codes in a real order confirmation)"
control: false-positive control for gift-card wording
why: >-
  Acme's corporate gifting supplier confirms a bulk e-gift order placed by Acme and delivers
  the codes. Authentication set to pass with sender_seen_before true: it is genuine mail from
  the supplier's own domain, which Acme has bought from before.
  Held-out batch 2 card; written without the plan, keyword lists or signals.
failure_looks_like: a routine order confirmation is escalated as a gift-card scam
review: >-
  no expected rule given; it depends on how the gift-card terms score for a known, authenticated
  sender. Compute it with ecf rules test.
author: claude
from: "Vendor D Corporate Gifting <orders@vendor-d.example>"
to: [ap@acme.example]
subject: "Order GD-208815 delivered: 10 e-gift codes for ACME"
date: 2026-10-02T15:10:00Z
expected:
  labels: {category: payment_confirmation, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: vendor, fraud_risk: none}
  facts: {auth_result: pass, sender_seen_before: true}
---
Hello ACME Accounts Payable,

Thank you for your order. Your digital gift codes are ready.

Order: GD-208815
Placed by: ap@acme.example on Oct 2, 2026
PO reference: PO-6620 (client appreciation, Q4)
Paid: corporate account on file, net 30 (invoice to follow separately)

Item: Vendor D Dining & Experiences e-gift, $75 value
Quantity: 10
Subtotal: $750.00
Service fee: $0.00
Total: $750.00

Codes (each redeemable once, no expiry):
  1. VDX7-QKTM-4R2P  PIN 4417
  2. VDX7-HWNA-9C3L  PIN 2093
  3. VDX7-ZPEB-6T8Y  PIN 7731
  4. VDX7-MLRG-2D5K  PIN 0586
  5. VDX7-CFJU-8N1W  PIN 3342
  6. VDX7-TBQS-5H7A  PIN 6128
  7. VDX7-YNDV-3G9E  PIN 9054
  8. VDX7-KRWC-7P4M  PIN 1867
  9. VDX7-EAHZ-1L6U  PIN 5203
 10. VDX7-GSXF-4B2R  PIN 8710

Recipients redeem at checkout on the Vendor D site or in partner restaurants by showing the code. Please store these codes as you would cash; we cannot replace codes that have been redeemed by someone else.

Questions about this order? Reply to this email or call your account manager at 555-0127.

Vendor D Corporate Gifting
Business accounts team
