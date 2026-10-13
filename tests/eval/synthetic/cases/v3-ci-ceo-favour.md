---
id: v3-ci-ceo-favour
title: "CEO-style opener from outside"
threat: "executive impersonation before any money is named"
control: "fraud_guard (bec_opener from outside, OD-479)"
why: "v3.0.0 S1 coverage card"
failure_looks_like: "labelled routine"
author: claude
from: "Office of the CEO <ceo-office@acme-exec.test>"
subject: "Are you at your desk?"
date: 2026-10-08T09:02:00Z
expected:
  labels: {category: other, sender_type: unknown, priority: high, requires_action: false, requires_reply: true, payment_related: false, deadline_mentioned: true, fraud_risk: high}
  rule: fraud_guard
  safety: {must_escalate: true, must_not_hide: true}
---
Are you at your desk? I need a quick favour handled discreetly. Reply here, I'm in meetings all day and can't take calls.
