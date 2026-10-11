---
id: h2-crypto-genuine-withdrawal-confirm
title: Exchange confirms a BTC withdrawal to the customer's own saved wallet
threat: none (benign control); crypto wording, a named destination wallet and a security reminder in genuine mail
control: crypto payment signal and fraud triggers (negative control); classifier fraud_risk on genuine exchange mail
why: "A real exchange's withdrawal notice names the destination wallet and the amount, and ends with the usual 'we will never ask for your recovery phrase' reminder, so crypto words appear without any request to move money. Authentication set to pass with sender_seen_before true: the mail comes from the exchange's own notification domain, which publishes DMARC, and the account holder has had mail from it before. Held-out batch 2 card; written without the plan, keyword lists or signals."
failure_looks_like: the notice is flagged as a crypto payment scam or escalated, or the reminder text is read as a credential request
review: "Category judgement call: finance (an account we hold, no payment asked) versus account_security (it carries a 'not you? lock your account' warning). Expected rule not set; compute with ecf rules test."
author: claude
profile: freemail
from: "Harbourline Exchange <no-reply@notify.harbourline.example>"
to: [pat-lee@freemail.example]
subject: "Withdrawal sent: 0.0425 BTC"
date: 2026-10-09T21:16:00Z
expected:
  labels: {category: finance, priority: high, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: vendor, fraud_risk: none}
  facts: {auth_result: pass, sender_seen_before: true}
---
Hi Pat,

Your withdrawal has been processed and broadcast to the Bitcoin network.

Asset:          Bitcoin (BTC)
Amount:         0.0425 BTC
Network fee:    0.00006 BTC
Destination:    TEST-WALLET-0412 (saved in your address book as "cold storage")
Requested:      9 Oct 2026, 21:14 UTC, Firefox on macOS, approx. location Leeds, UK
Reference:      WD-20261009-77310

Confirmation usually takes 30 to 60 minutes but can be slower when the network is busy. You can follow it under Wallet > History in the app, or at https://www.harbourline.example/account/history

Didn't make this withdrawal?
Lock your account now from the app (Settings > Security > Lock account) or call our 24/7 security line on +1 555-0147. Locking stops any further withdrawals until you talk to us.

A reminder about staying safe
- Harbourline staff will never ask for your password, two-step codes or recovery phrase, by email, phone, chat or social media.
- We will never ask you to move your funds to a "safe" or "verification" wallet.
- Only withdraw to addresses you control or have checked yourself.

Thanks for trading with us,
Harbourline Exchange

This is a mandatory account notification and can't be turned off. Harbourline Exchange Ltd, 1 Example Quay, Example City.
