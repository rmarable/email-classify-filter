---
id: h2-crypto-genuine-processor-stablecoin-paid
title: Payment processor tells the business a customer paid an invoice in USDC
threat: none (benign control); a customer paying us in stablecoin, settled to our bank in dollars
control: crypto payment signal and fraud triggers (negative control); classifier money-direction category
why: "A merchant that accepts stablecoin gets the processor's 'you've been paid' notice, which names the customer's sending wallet, the network and the fiat payout. Money is coming in and nothing is asked. Authentication set to pass with sender_seen_before true: genuine mail from the processor's own domain, which publishes DMARC, and the business gets these notices regularly. Held-out batch 2 card; written without the plan, keyword lists or signals."
failure_looks_like: the notice is escalated as a crypto payment request, or labelled invoice or payment_confirmation as if we were paying
review: "Category judgement call: remittance (money coming to us via the processor) versus payment_confirmation. Expected rule not set; compute with ecf rules test."
author: claude
from: "Tillway Payments <receipts@tillway.example>"
to: [ap@acme.example]
subject: "You've been paid: INV-4471 from Brightwater Studio (USDC)"
date: 2026-10-08T16:45:00Z
expected:
  labels: {category: remittance, priority: low, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: vendor, fraud_risk: none}
  facts: {auth_result: pass, sender_seen_before: true}
---
Good news, ACME Example: Brightwater Studio has paid invoice INV-4471.

Payment details
  Invoice:            INV-4471 (issued 24 Sep 2026)
  Customer:           Brightwater Studio <accounts@brightwater-studio.example>
  Paid with:          USDC on Solana
  Customer wallet:    TEST-WALLET-0349
  Amount paid:        2,340.00 USDC
  Received:           8 Oct 2026, 16:43 UTC
  Payment ID:         TW-PAY-2026-30917

Settlement
  Converted at:       1 USDC = 1.0000 USD
  Processing fee:     -11.70 USD (0.5%)
  Net to you:         2,328.30 USD
  Payout:             to your bank account ending 0193, expected 13 Oct 2026

INV-4471 is now marked paid in your dashboard. Brightwater Studio has been sent its own receipt; you don't need to do anything.

View payment: https://dashboard.tillway.example/payments/TW-PAY-2026-30917

Questions about this payment? Reply to this email or reach merchant support on +1 555-0163.

Tillway Payments
