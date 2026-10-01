---
id: sales-city-procurement
title: A city procurement office asks for a quote
threat: none
control: reply needed continues to the actor
why: public-sector buyers write differently
failure_looks_like: classified as regulatory
from: "City Procurement <procurement@city-rr.example>"
subject: "Request for quote - help desk software"
date: 2026-11-02T13:00:00Z
author: claude
expected:
  labels: {category: sales_inquiry, requires_reply: true, fraud_risk: none}
  rule: requires_reply
---
Good afternoon,

The city is collecting quotes for help desk software for 25 staff. Please send a quote with
annual pricing and your standard terms by 20 November.

City Procurement
