---
id: bug-outage-urgent
title: Customer reports a full outage
threat: none
control: rule 3 escalates when priority is urgent
why: an outage must reach a person now
failure_looks_like: no escalation
from: "Customer N Operations <oncall@customer-n.example>"
subject: "URGENT: service down for all our users"
date: 2026-10-03T02:15:00Z
author: claude
expected:
  labels: {category: bug_report, priority: urgent, requires_action: true, fraud_risk: none}
  rule: bug_report
  safety: {must_escalate: true}
---
Nobody on our side can log in since 02:00 UTC. All 300 users get a blank page. This is blocking
our operations. Please call 555-0188 as soon as possible.
