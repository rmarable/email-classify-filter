---
id: remit-portal-html-table
title: Payables portal sends remittance advice as an HTML table
threat: none
control: the classifier reads the visible HTML text; rule 1b flags it
why: much remittance arrives as HTML from AP portals
failure_looks_like: classified as notification and hidden
from: "Payables Hub <no-reply@payables-hub.example>"
subject: "Remittance advice from Customer AF"
date: 2026-11-05T16:00:00Z
author: claude
expected:
  labels: {category: remittance, payment_related: true, fraud_risk: none}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Remittance advice from Customer AF. Payment of 3,050.00 for invoices INV-7840 and INV-7843.


## html
<p>Remittance advice from <b>Customer AF</b></p>
<table><tr><th>Invoice</th><th>Amount</th></tr>
<tr><td>INV-7840</td><td>2,000.00</td></tr>
<tr><td>INV-7843</td><td>1,050.00</td></tr>
<tr><td>Total</td><td>3,050.00</td></tr></table>
<p>Payment date 5 November 2026.</p>
