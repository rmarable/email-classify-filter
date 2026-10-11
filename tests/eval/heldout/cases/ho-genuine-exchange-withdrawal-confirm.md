---
id: ho-genuine-exchange-withdrawal-confirm
title: Crypto exchange confirms a withdrawal the treasury account holder made (genuine)
threat: none (look-alike of a crypto payment demand)
control: a confirmation of the holder's own completed withdrawal must not be read as a demand to pay in crypto
why: "a completed-withdrawal notice from the exchange where Acme keeps a small treasury account. It confirms an action the holder took, to a wallet saved on the account's address book, and asks for nothing except a lock if it wasn't them. Wallet is a TEST-WALLET placeholder. Auth pass: the exchange's own domain, which publishes DMARC. The account holder has heard from it before. Held-out card; written without the plan, keyword lists or signals."
failure_looks_like: escalated as a crypto scam, or labelled spam or phishing
author: claude
from: "LedgerLine Exchange <no-reply@ledgerline.example>"
to: [treasury@acme.example]
subject: "Withdrawal completed: 0.2500 BTC"
date: 2026-10-08T19:47:00Z
expected:
  facts: {auth_result: pass, sender_seen_before: true}
  labels: {category: notification, priority: high, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, sender_type: vendor, fraud_risk: low}
---
Withdrawal completed

Your withdrawal from LedgerLine has been sent and has 3 network confirmations.

  Asset:           Bitcoin (BTC)
  Amount:          0.2500 BTC
  Network fee:     0.00004 BTC
  To address:      TEST-WALLET-0003 (saved as "Acme cold storage")
  Requested:       8 Oct 2026, 19:31 UTC, from a recognised device
  Approved by:     second admin on the account
  Reference:       WD-20261008-7714

Nothing else for you to do.

Not you? Lock your account straight away from the app, or at
https://www.ledgerline.example/lock, then contact support. We'll never ask for your
password, your 2FA code or your recovery phrase, and we'll never ask you to send
crypto to us.

LedgerLine Exchange
