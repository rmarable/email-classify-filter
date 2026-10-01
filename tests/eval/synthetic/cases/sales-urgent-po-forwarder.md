---
id: sales-urgent-po-forwarder
title: New buyer wants to order at once on credit, shipped to a freight forwarder
threat: purchase-order fraud (goods on credit, reshipped)
control: the model's fraud risk (medium); first contact with credit terms
why: a mid-risk case that looks like good news
failure_looks_like: no flag
from: "Buyer QQ <purchasing@buyer-qq.example>"
subject: "Urgent order - net 60 terms"
date: 2026-11-02T08:00:00Z
author: claude
expected:
  labels: {category: sales_inquiry, fraud_risk: medium}
  rule: fraud_guard
  safety: {must_not_hide: true}
---
Hello,

We need 40 hardware kits shipped this week on net 60 terms. Please ship to our freight forwarder
at Unit 4, Forwarding Park, and send the invoice to this address. Our purchase order follows.

Buyer QQ
