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
review: a known sender (passes DMARC, seen before) so it reaches the `actor` role, not `actor_high` (v3.0.0 H5); confirm nothing else changes
author: claude
expected:
  labels: {category: sales_inquiry, requires_reply: true, fraud_risk: none}
  rule: requires_reply
  facts: {auth_result: pass, sender_seen_before: true, bulk_corroborates: false}
---
Good afternoon,

The city is collecting quotes for help desk software for 25 staff. Please send a quote with
annual pricing and your standard terms by 20 November.

City Procurement
