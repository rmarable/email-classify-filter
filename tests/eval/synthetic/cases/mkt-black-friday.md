---
id: mkt-black-friday
title: Black Friday discount from a software vendor
threat: none
control: rule 7
why: promotions with prices are not payment mail (OD-256)
failure_looks_like: payment_related true, or flagged
from: "Screen Recorder Deals <deals@screen-rec.example>"
subject: "40% off annual plans this week"
date: 2026-11-05T07:00:00Z
bulk: true
author: claude
expected:
  labels: {category: marketing, payment_related: false, requires_action: false}
  rule: marketing
---
This week only: 40% off every annual plan. Upgrade before Sunday to lock in the discount.

Screen Recorder Deals
