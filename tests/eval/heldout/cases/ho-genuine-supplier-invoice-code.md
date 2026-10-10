---
id: ho-genuine-supplier-invoice-code
title: Supplier asks for the payment reference code from its invoice to match a payment (genuine)
threat: none (look-alike of a request to "send us the code")
control: a request for a document reference code (not a sign-in code or credential) from a known supplier must not be treated as credential harvesting
why: "a known supplier's credit controller received a payment with no reference and asks AP for the short payment reference printed on the invoice so the cash can be allocated. The \"code\" is an invoice reference, not an access code. Auth pass: the supplier's own domain, which publishes DMARC. A regular sender. Held-out card; written without the plan, keyword lists or signals."
failure_looks_like: escalated as a code or credential request
author: claude
from: "Vendor E Credit Control <creditcontrol@vendor-e.example>"
to: [ap@acme.example]
subject: "RE: Payment received 2 Oct - which invoice?"
date: 2026-10-06T09:12:00Z
expected:
  facts: {auth_result: pass, sender_seen_before: true}
  labels: {category: billing_inquiry, priority: high, requires_action: true, requires_reply: true, payment_related: true, deadline_mentioned: false, sender_type: vendor, fraud_risk: low}
---
Hi there,

Thanks for the payment of $3,480.00 that landed on 2 October. Unfortunately it came
through with no reference, and you have two open invoices with us for exactly that
amount (one for September's consumables, one for the replacement filters), so I
can't tell which to clear.

Could you send us the code from your invoice? It's the 8-character payment reference
in the box under the total, starting "VE-" (for example VE-7K2Q9M). The PO number
from your side would also do.

Once I have it I'll allocate the payment and send you an updated statement.

Many thanks,
Credit Control
Vendor E
555-0149

> On 2 Oct 2026, Acme Accounts Payable wrote:
> Hi, payment sent today by ACH. Regards, AP
