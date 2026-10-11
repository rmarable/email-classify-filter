---
id: ho-genuine-receipt-usd-prefix
title: Known vendor's receipt with the amount written USD 1,200 (genuine)
threat: none (look-alike of a fake invoice; tests currency-code-first format)
control: a short receipt from a known vendor for a payment already made; must not be read as a payment request
why: "a terse receipt from a regular services vendor, with the amount as \"USD 1,200\" and no cents. Nothing is due. Auth pass: the vendor's own domain, which publishes DMARC. Acme has corresponded with this vendor before. Held-out card; written without the plan, keyword lists or signals."
failure_looks_like: labelled an invoice, or escalated as fraud
author: claude
from: "Vendor F Billing <billing@vendor-f.example>"
to: [ap@acme.example]
subject: "Receipt #R-20417"
date: 2026-10-09T13:20:00Z
expected:
  facts: {auth_result: pass, sender_seen_before: true}
  labels: {category: payment_confirmation, priority: low, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: vendor, fraud_risk: none}
---
Receipt #R-20417

Received from: Acme
Amount: USD 1,200
For: October window cleaning, 4 visits (invoice F-3318)
Method: ACH, received 9 Oct 2026

Balance due: USD 0

Thanks, as always.
Vendor F Billing
