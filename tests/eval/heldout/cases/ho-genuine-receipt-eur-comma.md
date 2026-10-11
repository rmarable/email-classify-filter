---
id: ho-genuine-receipt-eur-comma
title: Known European vendor's receipt with the amount written 1.234,56 € (genuine)
threat: none (look-alike of a fake invoice; tests European number format)
control: a receipt for a payment already made, from a known vendor, in European amount format; must not be read as an invoice or a fraud
why: "a regular EU supplier confirms receipt of Acme's payment, writing the amount as \"1.234,56 €\". Nothing is due. Auth pass: the vendor's own domain, which publishes DMARC. Acme has corresponded with this vendor before. Held-out card; written without the plan, keyword lists or signals."
failure_looks_like: labelled an invoice or payment request, amount misread, or escalated as fraud
author: claude
from: "Vendor D Buchhaltung <buchhaltung@vendor-d.example>"
to: [billing@acme.example]
subject: "Zahlungseingang / Payment received - RE-2026-0931"
date: 2026-10-03T07:55:00Z
expected:
  facts: {auth_result: pass, sender_seen_before: true}
  labels: {category: payment_confirmation, priority: low, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: vendor, fraud_risk: none}
---
Sehr geehrte Damen und Herren, dear Acme team,

we confirm receipt of your payment. Thank you.

Invoice: RE-2026-0931 (3 Sep 2026)
Amount received: 1.234,56 €
Value date: 1 Oct 2026
Open balance on this invoice: 0,00 €

Your account with us is fully paid. You don't need to do anything.

Mit freundlichen Grüßen / Kind regards
Accounting, Vendor D
Tel. 555-0171

## html
<html><body style="font-family:Verdana,sans-serif;font-size:13px">
<p>Sehr geehrte Damen und Herren, dear Acme team,</p>
<p>we confirm receipt of your payment. Thank you.</p>
<table cellpadding="3">
<tr><td>Invoice</td><td>RE-2026-0931 (3 Sep 2026)</td></tr>
<tr><td>Amount received</td><td><b>1.234,56 €</b></td></tr>
<tr><td>Value date</td><td>1 Oct 2026</td></tr>
<tr><td>Open balance on this invoice</td><td>0,00 €</td></tr>
</table>
<p>Your account with us is fully paid. You don't need to do anything.</p>
<p>Mit freundlichen Grüßen / Kind regards<br>Accounting, Vendor D<br>Tel. 555-0171</p>
</body></html>
