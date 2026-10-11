---
id: h2-crypto-scam-btc-invoice-suspension
title: Fake hosting renewal invoice demands BTC payment to avoid service suspension
threat: fake invoice / crypto payment demand under a suspension threat
control: fraud triggers (crypto payment to a wallet, urgency, first-time sender); classifier category ordering for deceptive invoices
why: "A fake 'final notice' for domain and hosting renewal, claiming the card on file failed and the only way to stop suspension today is to pay in bitcoin to a wallet and reply with the transaction ID. Authentication set to pass with sender_seen_before false: the attacker owns the look-official renewals domain and signs mail from it correctly, and it has never written to the mailbox before. Held-out batch 2 card; written without the plan, keyword lists or signals."
failure_looks_like: the email is treated as an ordinary invoice and routed for payment, or given fraud_risk below high
review: "Category: spam_or_phishing under the 2026-10-09 order (deceptive invoice), though it reads as an invoice; the guide's older 'a fake invoice is invoice' line conflicts. sender_type vendor (it poses as our host). Expected rule not set; compute with ecf rules test."
author: claude
from: "HostDesk Billing <billing@hostdesk-renewals.example>"
to: [ap@acme.example]
subject: "FINAL NOTICE - invoice HD-88213 unpaid, acme.example suspension scheduled"
date: 2026-10-02T07:48:00Z
expected:
  labels: {category: spam_or_phishing, priority: high, requires_action: true, requires_reply: true, payment_related: true, deadline_mentioned: true, sender_type: vendor, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
  facts: {auth_result: pass, sender_seen_before: false}
---
Dear Customer,

Our records show invoice HD-88213 for the annual renewal of your services is still unpaid. Two attempts to charge the card on file were declined.

Services affected:
  - Domain registration: acme.example
  - Business hosting plan (3 sites)
  - Mailboxes (12)

Amount due: USD 389.00

Your services are scheduled for suspension within 24 hours of this notice. Once suspended, your website and email will stop working and the domain may be released after the grace period. Reactivation carries a USD 150 fee.

Because card payment has failed twice, our billing system has switched this invoice to cryptocurrency settlement. To avoid suspension, please settle in BTC:

  Amount:   0.0061 BTC
  Wallet:   TEST-WALLET-0958
  Memo:     HD-88213

After sending, reply to this email with the transaction ID so our team can cancel the suspension manually. Payments without a transaction ID can take up to 72 hours to match, which is after the suspension date.

If you have already paid, please ignore this notice.

Regards,
Billing Department
HostDesk Domain & Hosting Services
+1 555-0118
