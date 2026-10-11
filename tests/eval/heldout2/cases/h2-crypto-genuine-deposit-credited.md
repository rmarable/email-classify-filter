---
id: h2-crypto-genuine-deposit-credited
title: Exchange tells a business account that a USDC deposit was credited
threat: none (benign control); stablecoin deposit notice naming the sending wallet
control: crypto payment signal and fraud triggers (negative control); classifier category and fraud_risk on a terse balance notice
why: "A business that keeps a stablecoin balance at an exchange gets short, number-heavy deposit notices naming the sending wallet and the new balance. It asks for nothing. Authentication set to pass with sender_seen_before true: genuine mail from the exchange's own alerts domain, which publishes DMARC, to a mailbox that has had these alerts before. Held-out batch 2 card; written without the plan, keyword lists or signals."
failure_looks_like: the notice is escalated or labelled spam_or_phishing because it names a wallet and an amount
review: "Category judgement call: finance (a balance on an account we hold) versus remittance (money arriving). Our own funds moving in, so finance. Expected rule not set; compute with ecf rules test."
author: claude
from: "Copperleaf Markets <alerts@copperleaf.example>"
to: [ap@acme.example]
subject: "Deposit credited - 12,500.00 USDC"
date: 2026-10-06T14:02:00Z
expected:
  labels: {category: remittance, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: vendor, fraud_risk: none}
  facts: {auth_result: pass, sender_seen_before: true}
---
Copperleaf Markets - deposit credited

Account:        ACME Example Inc. (business, ID ending 4410)
Asset:          USD Coin (USDC), Ethereum network
Amount:         12,500.00 USDC
From address:   TEST-WALLET-0261
Confirmations:  64 of 64
Credited:       6 Oct 2026, 14:01 UTC
Reference:      DEP-CL-558102

New USDC balance: 48,730.15 USDC (available)

The funds are available for trading and withdrawal now. As set by your account administrator, withdrawals to addresses not yet on your allowlist are held for 24 hours.

See all activity: https://app.copperleaf.example/activity

If you don't recognise this deposit, let your account administrator know. Deposits can't be reversed by Copperleaf.

--
Copperleaf Markets. You get these alerts because deposit notifications are on for this account. Admins can change alert settings under Team > Notifications.
