---
id: ho-genuine-receipt-gbp-symbol
title: Known UK vendor's casual receipt for £50 (genuine)
threat: none (look-alike of a small payment request; tests the pound-sign amount)
control: an informal receipt from a known vendor for a small payment already made; must not be read as a request or a fraud
why: "a small UK supplier the office uses for catering replies informally to say the £50 deposit arrived. Conversational tone, no formal layout, nothing to pay. Auth pass: the vendor's own domain, which publishes DMARC. Acme has corresponded with this vendor before. Held-out card; written without the plan, keyword lists or signals."
failure_looks_like: labelled an invoice or payment request, or escalated
author: claude
from: "Vendor G Kitchen <hello@vendor-g.example>"
to: [ap@acme.example]
subject: "Re: Deposit for the 24th"
date: 2026-10-08T17:03:00Z
expected:
  facts: {auth_result: pass, sender_seen_before: true}
  labels: {category: payment_confirmation, priority: low, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: vendor, fraud_risk: low}
---
Hiya,

Just to say your £50 deposit came through this afternoon, thank you! That's the
24th locked in for lunch for 30. Treat this email as your receipt. A proper one is
attached to your account if your auditors want it.

The remaining balance goes on the final invoice after the event, same as last time,
so nothing more to pay now.

Cheers,
The Vendor G Kitchen team
555-0186

> On 8 Oct, Acme Accounts Payable wrote:
> Deposit sent by card just now, please confirm. Thanks!
