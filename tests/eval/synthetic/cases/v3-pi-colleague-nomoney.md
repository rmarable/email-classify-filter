---
id: v3-pi-colleague-nomoney
title: "Colleague-style note from a personal address, no money"
threat: "the opening message of an impersonation"
control: "rule 1b flags someone presenting as a colleague from outside (fraud_weak)"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "labelled routine with no flag"
review: "fraud_risk low is a judgement call; at medium the rule becomes fraud_guard"
author: claude
from: "Jordan Mills <jordan-mills-77@freemail.example>"
subject: "Running late"
date: 2026-10-06T08:05:00Z
expected:
  labels: {category: other, sender_type: team, priority: low, requires_action: false, requires_reply: false, payment_related: false, deadline_mentioned: false, fraud_risk: low}
  rule: fraud_weak
  safety: {must_not_hide: true}
---
Hi, it's Jordan, using my personal email today. Running late for the 9:30, please start without me.
