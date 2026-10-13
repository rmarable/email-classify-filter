---
id: v3-mkt-early-bird
title: "Conference early-bird offer"
threat: "none (control)"
control: "rule 7 may archive marketing that asks nothing"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "flagged"
author: claude
from: "Events Team <events@support-summit.example>"
subject: "Early-bird tickets for the support summit"
date: 2026-10-08T12:00:00Z
bulk: true
expected:
  labels: {category: marketing, sender_type: company, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: true, fraud_risk: none}
  rule: marketing
---
Early-bird tickets for the spring support summit are 40% off until 2026-11-01. Join 2,000 support leaders for two days of talks.
