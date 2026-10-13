---
id: v3-bug-outage-urgent
title: "Customer reports a full outage"
threat: "none (control)"
control: "rule 3 flags high priority and escalates urgent bug reports"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "labelled with no escalation"
author: claude
from: "Morgan Hale <morgan-hale@northwind-retail.example>"
subject: "URGENT: nobody can log in"
date: 2026-10-08T05:50:00Z
expected:
  labels: {category: bug_report, sender_type: customer, priority: urgent, requires_action: true, requires_reply: true, payment_related: false, deadline_mentioned: false, fraud_risk: none}
  rule: bug_report
---
Since 05:30 none of our 40 agents can sign in; every attempt shows error 503. Our support desk is down. Please help as soon as possible.
