---
id: sales-student-discount
title: Individual asks about a student discount
threat: none
control: reply needed continues to the actor; low priority
why: the low end of sales mail
failure_looks_like: flagged as urgent
from: "Student OO <student@university-oo.example>"
subject: "Student pricing?"
date: 2026-11-01T20:00:00Z
review: a known sender (passes DMARC, seen before) so it reaches the `actor` role, not `actor_high` (v3.0.0 H5); confirm nothing else changes
author: claude
expected:
  labels: {category: sales_inquiry, priority: low, payment_related: false, fraud_risk: none}
  rule: requires_reply
  facts: {auth_result: pass, sender_seen_before: true, bulk_corroborates: false}
---
Hello, do you have a student discount for a single seat? I'm using it for a class project.
