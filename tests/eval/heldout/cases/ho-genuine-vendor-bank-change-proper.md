---
id: ho-genuine-vendor-bank-change-proper
title: Known vendor announces a bank move in advance and asks for confirmation by phone (genuine)
threat: none (look-alike of a vendor bank-change fraud)
control: a genuine change notice from the vendor's own domain; whatever the label, the process it describes (confirm by phone on the number on file) is the safe one
why: "an established supplier writes from its usual domain six weeks ahead of the switch. The email carries no account numbers, says the old account stays open until the cut-over, and tells AP to verify by calling the number already on file, not one in the email. It is still a bank-change notice, so an operator may want it surfaced. Auth pass: the vendor's genuine domain, which publishes DMARC. The vendor is a regular sender. Held-out card; written without the plan, keyword lists or signals."
failure_looks_like: archived or hidden without anyone seeing it, or the details are applied without a phone check
author: claude
from: "Vendor C Accounts Receivable <ar@vendor-c.example>"
to: [ap@acme.example]
subject: "Advance notice: Vendor C is changing banks on 16 November 2026"
date: 2026-10-05T11:15:00Z
expected:
  facts: {auth_result: pass, sender_seen_before: true}
  labels: {category: vendor_change_request, priority: low, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: vendor, fraud_risk: none}
---
Dear Acme Accounts Payable,

We're writing to all customers with advance notice that Vendor C is moving its
business banking to a new provider. The change takes effect on Monday 16 November
2026.

What this means for you:

1. Keep paying our current account for everything due before 16 November. It will
   stay open until the end of January, so any late payments will still reach us.
2. On purpose, we are not putting the new account details in this email. Our
   finance team will send you a signed letter by post on company letterhead during
   the week of 26 October.
3. Before you change anything in your system, please call us to confirm the
   details. Use the phone number you already have on file for Vendor C, not one
   from any email or letter, including this one. If you don't have it, it's the
   number printed on every invoice we've sent you.

If you get an email that says our bank details have changed and it doesn't match
this notice (a different date, details in the email body, pressure to pay
urgently), please don't act on it. Forward it to us and give us a call.

We're sorry for the admin. Thanks for being a customer for so many years.

Kind regards,

Accounts Receivable
Vendor C
