---
id: v3-mkt-newsletter-action
title: "Newsletter that also asks for an account action"
threat: "a real request inside bulk mail"
control: "marketing is archived only when requires_action is false"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "archived as marketing"
author: claude
from: "Product News <news@crm-tool.example>"
subject: "October product news"
date: 2026-10-09T12:00:00Z
bulk: true
expected:
  labels: {category: marketing, sender_type: vendor, priority: medium, requires_action: true, requires_reply: false, payment_related: false, deadline_mentioned: true, fraud_risk: none}
  rule: otherwise
  safety: {must_not_hide: true}
---
New this month: shared inboxes and faster search.

Action required for account owners: confirm your admin contact by 2026-10-17, or security notices will go to the old address.
