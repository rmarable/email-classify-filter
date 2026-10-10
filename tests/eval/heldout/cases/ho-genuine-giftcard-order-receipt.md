---
id: ho-genuine-giftcard-order-receipt
title: Receipt for an e-gift card order Acme placed for staff (genuine)
threat: none (look-alike of a gift-card purchase scam)
control: a vendor receipt for gift cards the company bought must not be treated as a request to buy gift cards
why: "a real order receipt from a corporate gift-card vendor for the staff appreciation event. The money is already paid, nothing is asked of the reader, and the codes go straight to the named recipients. First order from this vendor, so sender_seen_before is left out. Auth pass: the vendor's own domain, which publishes DMARC. Held-out card; written without the plan, keyword lists or signals."
failure_looks_like: escalated as a gift-card fraud, or labelled as spam or phishing instead of a payment confirmation
author: claude
from: "GiftHub for Business <orders@gifthub.example>"
to: [ap@acme.example]
subject: "Order GHB-58821 confirmed - 40 eGift cards"
date: 2026-10-02T16:25:00Z
expected:
  facts: {auth_result: pass}
  labels: {category: payment_confirmation, priority: low, requires_action: false, requires_reply: false, payment_related: true, deadline_mentioned: false, sender_type: vendor, fraud_risk: low}
---
GiftHub for Business - Order confirmation

Thanks for your order. Here are the details for your records.

Order number: GHB-58821
Placed by: ap@acme.example
Company: Acme (account ACME-0042)
Order date: 2 Oct 2026

Item                                  Qty   Unit      Total
Coffee Corner eGift card, $25          40   $25.00    $1,000.00
Personalised message                   40   $0.00     $0.00
Corporate service fee                   1   $0.00     $0.00
                                                     ---------
Order total                                           $1,000.00

Paid by: company card ending 4417
Delivery: by email to the 40 recipients in your uploaded list
Scheduled send: 10 Oct 2026, 09:00 your time

Card codes go straight to each recipient. They don't appear in this receipt and
we'll never send them to the purchaser.

A tax invoice (PDF) is in your account under Orders. Questions about this order:
reply here or call 555-0163.

GiftHub for Business

## html
<html><body style="font-family:Arial,sans-serif;color:#222">
<h2 style="color:#5a2d82">Order confirmation</h2>
<p>Thanks for your order. Here are the details for your records.</p>
<table cellpadding="4" style="border-collapse:collapse">
<tr><td><b>Order number</b></td><td>GHB-58821</td></tr>
<tr><td><b>Placed by</b></td><td>ap@acme.example</td></tr>
<tr><td><b>Company</b></td><td>Acme (account ACME-0042)</td></tr>
<tr><td><b>Order date</b></td><td>2 Oct 2026</td></tr>
</table>
<table cellpadding="4" border="1" style="border-collapse:collapse;margin-top:12px">
<tr><th>Item</th><th>Qty</th><th>Unit</th><th>Total</th></tr>
<tr><td>Coffee Corner eGift card, $25</td><td>40</td><td>$25.00</td><td>$1,000.00</td></tr>
<tr><td>Personalised message</td><td>40</td><td>$0.00</td><td>$0.00</td></tr>
<tr><td>Corporate service fee</td><td>1</td><td>$0.00</td><td>$0.00</td></tr>
<tr><td colspan="3"><b>Order total</b></td><td><b>$1,000.00</b></td></tr>
</table>
<p>Paid by: company card ending 4417<br>
Delivery: by email to the 40 recipients in your uploaded list<br>
Scheduled send: 10 Oct 2026, 09:00 your time</p>
<p>Card codes go straight to each recipient. They don't appear in this receipt and we'll never send them to the purchaser.</p>
<p>A tax invoice (PDF) is in your account under <a href="https://business.gifthub.example/orders/GHB-58821">Orders</a>. Questions: reply here or call 555-0163.</p>
</body></html>
