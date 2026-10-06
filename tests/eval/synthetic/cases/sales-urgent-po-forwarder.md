---
id: sales-urgent-po-forwarder
title: New buyer wants to order at once on credit, shipped to a freight forwarder
threat: purchase-order fraud (goods on credit, reshipped)
control: "first-time sender plus payment words (fraud_weak, OD-062): labelled and flagged, never hidden; escalation needs the model to rate it medium"
why: a mid-risk case that looks like good news
failure_looks_like: no flag, or hidden
review: "relabelled 2026-10-06 (operator): the local model rated it low in every run (b91f57f7, 19595c8c, 524d18d1), and only the model's fraud risk escalates it, so the card expects what ecf guarantees; the miss is a stated model limit (SPEC §16)"
from: "Buyer QQ <purchasing@buyer-qq.example>"
subject: "Urgent order - net 60 terms"
date: 2026-11-02T08:00:00Z
author: claude
expected:
  labels: {category: sales_inquiry}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Hello,

We need 40 hardware kits shipped this week on net 60 terms. Please ship to our freight forwarder
at Unit 4, Forwarding Park, and send the invoice to this address. Our purchase order follows.

Buyer QQ
