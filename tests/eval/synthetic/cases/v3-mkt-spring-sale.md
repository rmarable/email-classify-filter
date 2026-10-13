---
id: v3-mkt-spring-sale
title: "Bulk promotion"
threat: "none (control)"
control: "rule 7 may archive marketing that asks nothing"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "flagged"
author: claude
from: "Deals <deals@gadget-store.example>"
subject: "Spring sale: 30% off headsets"
date: 2026-10-07T12:00:00Z
bulk: true
expected:
  labels: {category: marketing, sender_type: company, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, fraud_risk: none}
  rule: marketing
---
This week only, take 30% off every headset in our store. Free shipping on orders over USD 50.
