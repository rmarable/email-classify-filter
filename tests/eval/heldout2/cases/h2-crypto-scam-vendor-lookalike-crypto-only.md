---
id: h2-crypto-scam-vendor-lookalike-crypto-only
title: Lookalike of a known vendor says it now takes only crypto and gives a wallet for open invoices
threat: vendor impersonation / payment method change to a crypto wallet
control: fraud trigger for payment-detail change (bank to crypto), lookalike domain, first-time sender; category precedence for vendor_change_request
why: "An impostor on a domain resembling Vendor B's writes in a friendly accounts-team voice: the bank is closing their account, so from now on they accept only USDT, and the two open invoices should go to a wallet. It changes how we pay a known vendor. Authentication set to pass with sender_seen_before false: the lookalike domain belongs to the attacker and authenticates correctly, and that exact domain has never written before, unlike the real vendor-b.example. Held-out batch 2 card; written without the plan, keyword lists or signals."
failure_looks_like: the request is filed as an ordinary vendor notification or invoice, the change goes unflagged, or it is not escalated
review: "Category vendor_change_request (switching from bank transfer to crypto for a known payee), which takes precedence over invoice and spam_or_phishing. Expected rule not set; compute with ecf rules test."
author: claude
from: "Vendor B Accounts <accounts@vendor-b-billing.example>"
to: [ap@acme.example]
subject: "Change to how we accept payment - open invoices INV-B-2207 and INV-B-2231"
date: 2026-10-05T10:12:00Z
expected:
  labels: {category: vendor_change_request, priority: high, requires_action: true, requires_reply: true, payment_related: true, deadline_mentioned: true, sender_type: vendor, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
  facts: {auth_result: pass, sender_seen_before: false}
---
Hi ACME accounts team,

Hope you're well. A quick but important update from us.

Our bank has told us it is closing a number of business accounts as part of a "risk review", ours included. We are moving banks, but in the meantime, and for the foreseeable future, we have decided to accept payment in USDT (Tether) only. Honestly it has been quicker and cheaper for us with overseas suppliers anyway.

That means the bank details you have on file for us will no longer be monitored, and any transfer sent there may bounce or be delayed by weeks.

You currently have two invoices open with us:

  INV-B-2207   due 10 Oct   USD 4,180.00
  INV-B-2231   due 17 Oct   USD 2,960.00
  Total                     USD 7,140.00

Please send the total as 7,140 USDT (TRC-20 network) to our company wallet:

  TEST-WALLET-0587

If your finance system can't handle crypto, let me know and I can point you to a broker we've used who makes it quite painless. Once sent, just reply with the transaction reference and I'll mark both invoices paid on our side the same day.

Sorry for the hassle, and thanks for being flexible. Not our choice!

Kind regards,
Accounts Receivable
Vendor B
+1 555-0129
