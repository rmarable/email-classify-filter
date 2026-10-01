---
id: sales-reseller-volume
title: Distributor asks for volume pricing to resell
threat: none
control: reply needed continues to the actor
why: channel sales are a distinct kind of sales inquiry
failure_looks_like: classified as partnership and not answered
from: "Distributor NN <sales@distributor-nn.example>"
subject: "Volume pricing for resale"
date: 2026-10-31T11:00:00Z
author: claude
expected:
  labels: {category: sales_inquiry, requires_reply: true, fraud_risk: none}
  rule: requires_reply
---
Hi,

We distribute software to about 300 small firms. What discount would you offer for 500 seats a
year resold through us?
