---
id: ctrl-exchange-withdrawal-receipt
title: Crypto exchange receipt for a withdrawal the owner made
threat: none (negative for the crypto_payment signal)
control: a receipt names a wallet and a sent amount but asks for nothing
why: people who hold crypto get these; a wallet address alone is not a demand
failure_looks_like: escalated as crypto fraud
profile: freemail
from: "Coin Exchange <receipts@coin-exchange.example>"
subject: "Withdrawal complete"
date: 2026-11-20T18:45:00Z
author: claude
expected:
  labels: {category: finance, sender_type: vendor, payment_related: true, fraud_risk: none}
  facts: {auth_result: pass}
  rule: travel_finance
---
Your withdrawal of 0.0500 BTC to wallet TEST-WALLET-0002 is complete. Network fee: 0.0001 BTC.

If you didn't make this withdrawal, contact support from the app. This receipt needs no reply.
